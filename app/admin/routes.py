import csv
import io
import logging
from datetime import datetime, date

from flask import render_template, redirect, url_for, flash, request, abort, Response, current_app, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.admin import admin_bp
from app.admin.forms import AddSpotForm, PricingForm, BulkToggleSpotsForm, AnnouncementForm, SessionNoteForm
from app.auth.utils import admin_required
from app.extensions import db, cache
from app.models import ParkingSpot, ParkingSession, PricingConfig, User, Announcement, ActivityLog
from app.utils import calculate_fee, log_activity

logger = logging.getLogger(__name__)

SESSIONS_PER_PAGE = 20


@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    total = ParkingSpot.query.count()
    available = ParkingSpot.query.filter_by(status="available").count()
    occupied = ParkingSpot.query.filter_by(status="occupied").count()
    inactive = ParkingSpot.query.filter_by(status="inactive").count()

    today_revenue = db.session.query(
        func.sum(ParkingSession.fee)
    ).filter(
        ParkingSession.paid == True,
        func.date(ParkingSession.exit_time) == date.today()
    ).scalar() or 0

    total_revenue = db.session.query(
        func.sum(ParkingSession.fee)
    ).filter(ParkingSession.paid == True).scalar() or 0

    total_sessions = ParkingSession.query.filter_by(paid=True).count()

    return render_template(
        "admin/dashboard.html",
        total=total, available=available, occupied=occupied, inactive=inactive,
        today_revenue=today_revenue, total_revenue=total_revenue,
        total_sessions=total_sessions,
    )


@admin_bp.route("/spots", methods=["GET", "POST"])
@login_required
@admin_required
def spots():
    form = AddSpotForm()
    if form.validate_on_submit():
        if ParkingSpot.query.filter_by(spot_number=form.spot_number.data).first():
            flash("Spot number already exists.", "danger")
        else:
            try:
                db.session.add(ParkingSpot(spot_number=form.spot_number.data, status="available"))
                db.session.commit()
                flash(f"Spot {form.spot_number.data} added.", "success")
            except Exception:
                db.session.rollback()
                logger.exception("Failed to add spot %s", form.spot_number.data)
                flash("Failed to add spot. Please try again.", "danger")
        return redirect(url_for("admin.spots"))

    all_spots = ParkingSpot.query.order_by(ParkingSpot.spot_number).all()
    from app.admin.forms import AddSpotsBulkForm
    bulk_form = AddSpotsBulkForm()
    bulk_toggle_form = BulkToggleSpotsForm()
    return render_template(
        "admin/spots.html",
        form=form, bulk_form=bulk_form, bulk_toggle_form=bulk_toggle_form, spots=all_spots,
    )


@admin_bp.route("/spots/<int:spot_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_spot(spot_id):
    spot = db.session.get(ParkingSpot, spot_id)
    if spot is None:
        abort(404)
    if spot.status == "occupied":
        flash("Cannot deactivate an occupied spot.", "danger")
    elif spot.status == "available":
        try:
            spot.status = "inactive"
            log_activity(current_user.id, "spot_toggle", f"spot={spot.spot_number} → inactive")
            db.session.commit()
            flash(f"Spot {spot.spot_number} marked inactive.", "info")
        except Exception:
            db.session.rollback()
            logger.exception("Failed to toggle spot %s", spot.spot_number)
            flash("Failed to update spot status. Please try again.", "danger")
    elif spot.status == "inactive":
        try:
            spot.status = "available"
            log_activity(current_user.id, "spot_toggle", f"spot={spot.spot_number} → available")
            db.session.commit()
            flash(f"Spot {spot.spot_number} marked available.", "success")
        except Exception:
            db.session.rollback()
            logger.exception("Failed to toggle spot %s", spot.spot_number)
            flash("Failed to update spot status. Please try again.", "danger")
    return redirect(url_for("admin.spots"))


@admin_bp.route("/sessions")
@login_required
@admin_required
def sessions():
    # G6: URL-based filters — vehicle number, date range, payment status
    vehicle = request.args.get("vehicle", "").strip()
    from_date = request.args.get("from", "")
    to_date = request.args.get("to", "")
    paid = request.args.get("paid", "")

    query = (
        ParkingSession.query
        .options(joinedload(ParkingSession.user), joinedload(ParkingSession.spot))
    )
    if vehicle:
        query = query.filter(ParkingSession.vehicle_number.ilike(f"%{vehicle}%"))
    if from_date:
        try:
            query = query.filter(
                ParkingSession.entry_time >= datetime.strptime(from_date, "%Y-%m-%d")
            )
        except ValueError:
            pass
    if to_date:
        try:
            query = query.filter(
                ParkingSession.entry_time <= datetime.strptime(to_date, "%Y-%m-%d")
            )
        except ValueError:
            pass
    if paid in ("0", "1"):
        query = query.filter(ParkingSession.paid == (paid == "1"))

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(ParkingSession.entry_time.desc()).paginate(
        page=page, per_page=SESSIONS_PER_PAGE, error_out=False
    )

    # G7: Overstay threshold from config — highlight rows active beyond this
    overstay_hours = current_app.config.get("OVERSTAY_HOURS", 8)
    now = datetime.utcnow()

    note_form = SessionNoteForm()
    return render_template(
        "admin/sessions.html",
        pagination=pagination,
        vehicle=vehicle, from_date=from_date, to_date=to_date, paid=paid,
        overstay_hours=overstay_hours,
        now=now,
        note_form=note_form,
    )


@admin_bp.route("/pricing", methods=["GET", "POST"])
@login_required
@admin_required
def pricing():
    config = PricingConfig.get_current()
    form = PricingForm(obj=config)
    if form.validate_on_submit():
        if config is None:
            config = PricingConfig()
            db.session.add(config)
        try:
            config.hourly_rate = form.hourly_rate.data
            config.grace_minutes = form.grace_minutes.data  # G3: persist grace period
            config.updated_at = datetime.utcnow()
            db.session.commit()
            cache.delete("pricing_config")
            flash(f"Hourly rate updated to ₹{form.hourly_rate.data}.", "success")
            return redirect(url_for("admin.pricing"))
        except Exception:
            db.session.rollback()
            logger.exception("Failed to update pricing config")
            flash("Failed to update pricing. Please try again.", "danger")
    return render_template("admin/pricing.html", form=form, config=config)


@admin_bp.route("/revenue")
@login_required
@admin_required
def revenue():
    daily = db.session.query(
        func.date(ParkingSession.exit_time).label("day"),
        func.count(ParkingSession.id).label("sessions"),
        func.sum(ParkingSession.fee).label("revenue"),
    ).filter(
        ParkingSession.paid == True
    ).group_by("day").order_by(db.desc("day")).limit(30).all()
    return render_template("admin/revenue.html", daily=daily)


@admin_bp.route("/sessions/export")
@login_required
@admin_required
def export_sessions():
    sessions = (
        ParkingSession.query
        .options(joinedload(ParkingSession.user), joinedload(ParkingSession.spot))
        .order_by(ParkingSession.entry_time.desc())
        .all()
    )

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["ID", "User Email", "Vehicle", "Spot", "Entry Time", "Exit Time", "Fee", "Paid"])
        for s in sessions:
            writer.writerow([
                s.id,
                s.user.email,
                s.vehicle_number,
                s.spot.spot_number,
                s.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                s.exit_time.strftime("%Y-%m-%d %H:%M:%S") if s.exit_time else "",
                str(s.fee) if s.fee else "",
                "Yes" if s.paid else "No",
            ])
        return buf.getvalue()

    return Response(
        generate(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sessions.csv"},
    )


@admin_bp.route("/spots/bulk", methods=["POST"])
@login_required
@admin_required
def add_spots_bulk():
    from app.admin.forms import AddSpotsBulkForm
    form = AddSpotsBulkForm()
    if form.validate_on_submit():
        start = form.start_number.data
        count = form.count.data
        added = 0
        skipped = 0
        for n in range(start, start + count):
            if ParkingSpot.query.filter_by(spot_number=n).first():
                skipped += 1
            else:
                db.session.add(ParkingSpot(spot_number=n, status="available"))
                added += 1
        db.session.commit()
        msg = f"Added {added} spot(s)."
        if skipped:
            msg += f" {skipped} skipped (already exist)."
        flash(msg, "success" if added else "warning")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")
    return redirect(url_for("admin.spots"))


@admin_bp.route("/spots/bulk-toggle", methods=["POST"])
@login_required
@admin_required
def bulk_toggle_spots():
    form = BulkToggleSpotsForm()
    if not form.validate_on_submit():
        flash("Invalid request (CSRF check failed).", "danger")
        return redirect(url_for("admin.spots"))

    action = request.form.get("action")
    if action not in ("activate", "deactivate"):
        flash("Invalid action.", "danger")
        return redirect(url_for("admin.spots"))

    spot_ids = request.form.getlist("spot_ids")
    if not spot_ids:
        flash("No spots selected.", "warning")
        return redirect(url_for("admin.spots"))

    updated = 0
    skipped = 0
    for sid in spot_ids:
        try:
            sid_int = int(sid)
        except ValueError:
            continue
        spot = db.session.get(ParkingSpot, sid_int)
        if spot is None or spot.status == "occupied":
            skipped += 1
            continue
        spot.status = "available" if action == "activate" else "inactive"
        updated += 1

    if updated:
        try:
            db.session.commit()
            verb = "activated" if action == "activate" else "deactivated"
            msg = f"{updated} spot(s) {verb}."
            if skipped:
                msg += f" {skipped} skipped (occupied or not found)."
            flash(msg, "success")
        except Exception:
            db.session.rollback()
            logger.exception("Failed to bulk toggle spots")
            flash("Failed to update spots. Please try again.", "danger")
    else:
        msg = "No spots were updated."
        if skipped:
            msg += f" {skipped} skipped (occupied or not found)."
        flash(msg, "warning")

    return redirect(url_for("admin.spots"))


@admin_bp.route("/users")
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=all_users)


@admin_bp.route("/sessions/<int:session_id>/close", methods=["POST"])
@login_required
@admin_required
def force_close_session(session_id):
    """H3: Admin force-closes an active session, calculating fee and freeing the spot."""
    form = SessionNoteForm()  # CSRF check via empty-ish form
    if not form.validate_on_submit():
        abort(400)
    session = db.session.get(ParkingSession, session_id)
    if not session:
        abort(404)
    if session.exit_time is not None:
        flash("Session already closed.", "warning")
        return redirect(url_for("admin.sessions"))
    now = datetime.utcnow()
    pricing = PricingConfig.get_or_create()
    session.exit_time = now
    session.fee = calculate_fee(session.entry_time, now, pricing.hourly_rate)
    if session.spot:
        session.spot.status = "available"
    log_activity(current_user.id, "force_close", f"session={session.id}")
    db.session.commit()
    flash(f"Session #{session_id} force-closed. Fee ₹{session.fee} (unpaid).", "warning")
    return redirect(url_for("admin.sessions"))


@admin_bp.route("/announcement", methods=["GET", "POST"])
@login_required
@admin_required
def announcement():
    """H4: Manage the site-wide announcement banner shown on the user dashboard."""
    form = AnnouncementForm()
    current_ann = Announcement.query.order_by(Announcement.updated_at.desc()).first()
    if form.validate_on_submit():
        if current_ann:
            current_ann.message = form.message.data
            current_ann.is_active = form.is_active.data
        else:
            db.session.add(Announcement(message=form.message.data, is_active=form.is_active.data))
        db.session.commit()
        flash("Announcement updated.", "success")
        return redirect(url_for("admin.announcement"))
    if current_ann:
        form.message.data = current_ann.message
        form.is_active.data = current_ann.is_active
    return render_template("admin/announcement.html", form=form, current=current_ann)


@admin_bp.route("/spots/utilization")
@login_required
@admin_required
def spot_utilization():
    """H5: Per-spot summary of total sessions, revenue, and last use date."""
    from sqlalchemy import case
    rows = (
        db.session.query(
            ParkingSpot,
            db.func.count(ParkingSession.id).label("total_sessions"),
            db.func.sum(
                case((ParkingSession.paid == True, ParkingSession.fee), else_=0)
            ).label("total_revenue"),
            db.func.max(ParkingSession.exit_time).label("last_used"),
        )
        .outerjoin(ParkingSession, ParkingSpot.id == ParkingSession.spot_id)
        .group_by(ParkingSpot.id)
        .order_by(ParkingSpot.spot_number)
        .all()
    )
    return render_template("admin/spot_utilization.html", rows=rows)


@admin_bp.route("/sessions/<int:session_id>/note", methods=["POST"])
@login_required
@admin_required
def set_session_note(session_id):
    """H9: Save an admin annotation on a session."""
    form = SessionNoteForm()
    session = db.session.get(ParkingSession, session_id)
    if not session:
        abort(404)
    if form.validate_on_submit():
        session.admin_note = form.note.data.strip() or None
        db.session.commit()
        flash("Note saved.", "success")
    return redirect(url_for("admin.sessions"))


@admin_bp.route("/activity")
@login_required
@admin_required
def activity_log():
    """H10: Paginated audit log of key actions."""
    page = request.args.get("page", 1, type=int)
    action_filter = request.args.get("action", "")
    q = ActivityLog.query.options(db.joinedload(ActivityLog.user))
    if action_filter:
        q = q.filter(ActivityLog.action == action_filter)
    pagination = q.order_by(ActivityLog.created_at.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    distinct_actions = [r[0] for r in db.session.query(ActivityLog.action).distinct().all()]
    return render_template(
        "admin/activity_log.html",
        pagination=pagination,
        entries=pagination.items,
        distinct_actions=distinct_actions,
        action_filter=action_filter,
    )


@admin_bp.route("/stats.json")
@login_required
@admin_required
def stats_json():
    """G8: JSON endpoint for Chart.js — last 7 days revenue + current spot occupancy."""
    from datetime import timedelta
    today = datetime.utcnow().date()
    labels, revenue = [], []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_start = datetime(day.year, day.month, day.day)
        day_end = day_start + timedelta(days=1)
        total = db.session.query(func.sum(ParkingSession.fee)).filter(
            ParkingSession.paid == True,
            ParkingSession.exit_time >= day_start,
            ParkingSession.exit_time < day_end,
        ).scalar() or 0
        labels.append(day.strftime("%d %b"))
        revenue.append(float(total))
    occupancy = {
        "available": ParkingSpot.query.filter_by(status="available").count(),
        "occupied": ParkingSpot.query.filter_by(status="occupied").count(),
        "inactive": ParkingSpot.query.filter_by(status="inactive").count(),
    }
    return jsonify({"labels": labels, "revenue": revenue, "occupancy": occupancy})
