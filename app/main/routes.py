import logging
from datetime import datetime, timezone
from decimal import Decimal

import razorpay
from flask import render_template, redirect, url_for, flash, request, current_app, jsonify, abort
from flask_login import login_required, current_user

from app.main import main_bp
from app.main.forms import CheckInForm, ExitForm, AddVehicleForm
from app.extensions import db
from app.models import ParkingSession, PricingConfig, Announcement, Vehicle
from app.services import get_active_session, get_first_available_spot
from app.utils import calculate_fee, log_activity

logger = logging.getLogger(__name__)


def _razorpay_client():
    return razorpay.Client(auth=(
        current_app.config["RAZORPAY_KEY_ID"],
        current_app.config["RAZORPAY_KEY_SECRET"],
    ))


@main_bp.route("/health")
def health():
    """Health-check endpoint for load balancers and uptime monitors. No auth required."""
    try:
        db.session.execute(db.text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    status = "ok" if db_status == "ok" else "degraded"
    return jsonify({"status": status, "db": db_status}), (200 if status == "ok" else 503)


@main_bp.route("/")
@login_required
def dashboard():
    active_session = get_active_session(current_user.id)
    form = CheckInForm()
    duration_minutes = None
    spots_available = True
    pricing = PricingConfig.get_or_create()
    if active_session:
        delta = datetime.now(timezone.utc).replace(tzinfo=None) - active_session.entry_time
        duration_minutes = int(delta.total_seconds() // 60)
    else:
        spots_available = get_first_available_spot() is not None
    history = (
        ParkingSession.query
        .filter_by(user_id=current_user.id, paid=True)
        .order_by(ParkingSession.exit_time.desc())
        .limit(5).all()
    )
    entry_time_iso = (active_session.entry_time.isoformat() + "Z") if active_session else None
    notice = Announcement.get_active()  # H4: dismissible banner
    user_vehicles = Vehicle.query.filter_by(user_id=current_user.id).all()  # H7: saved vehicles
    return render_template(
        "main/dashboard.html",
        session=active_session,
        form=form,
        duration_minutes=duration_minutes,
        history=history,
        spots_available=spots_available,
        hourly_rate=float(pricing.hourly_rate),
        entry_time_iso=entry_time_iso,
        notice=notice,
        user_vehicles=user_vehicles,
    )


@main_bp.route("/checkin", methods=["POST"])
@login_required
def checkin():
    existing = get_active_session(current_user.id)
    if existing:
        if existing.exit_time is not None:
            flash("You have an incomplete payment. Please complete it before checking in again.", "warning")
        else:
            flash("You already have an active parking session.", "warning")
        return redirect(url_for("main.dashboard"))

    form = CheckInForm()
    if not form.validate_on_submit():
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")
        return redirect(url_for("main.dashboard"))

    spot = get_first_available_spot()
    if spot is None:
        flash("No spots available at this time.", "warning")
        return redirect(url_for("main.dashboard"))

    try:
        spot.status = "occupied"
        session = ParkingSession(
            user_id=current_user.id,
            spot_id=spot.id,
            vehicle_number=form.vehicle_number.data.strip().upper(),
        )
        db.session.add(session)
        log_activity(current_user.id, "checkin", f"spot={spot.spot_number} vehicle={session.vehicle_number}")
        db.session.commit()
        logger.info("Check-in: user=%s spot=%s vehicle=%s", current_user.id, spot.spot_number, session.vehicle_number)
        flash(f"Checked in to spot {spot.spot_number}.", "success")
    except Exception:
        db.session.rollback()
        logger.exception("Check-in failed for user=%s", current_user.id)
        flash("Check-in failed, please try again.", "danger")

    return redirect(url_for("main.dashboard"))


@main_bp.route("/exit", methods=["POST"])
@login_required
def exit():
    active_session = get_active_session(current_user.id)
    if active_session is None:
        flash("No active parking session.", "warning")
        return redirect(url_for("main.dashboard"))

    pricing = PricingConfig.get_or_create()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    fee = calculate_fee(
        active_session.entry_time, now, pricing.hourly_rate,
        grace_minutes=getattr(pricing, "grace_minutes", 0) or 0,  # G3: apply grace period
    )
    amount_paise = int(fee * 100)  # Razorpay expects paise (integer)

    try:
        client = _razorpay_client()
        order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"session_{active_session.id}",
        })
        logger.info("Razorpay order created: order_id=%s session=%s fee=%s",
                    order["id"], active_session.id, fee)
    except Exception:
        logger.exception("Razorpay create_order failed for session=%s", active_session.id)
        flash("Payment initiation failed, please try again.", "danger")
        return redirect(url_for("main.dashboard"))

    # Persist fee and exit_time now so payment_callback reads stored values (not recomputed)
    active_session.fee = fee
    active_session.exit_time = now
    active_session.razorpay_order_id = order["id"]
    db.session.commit()

    duration_hours = (now - active_session.entry_time).total_seconds() / 3600
    return render_template(
        "main/payment.html",
        session=active_session,
        fee=fee,
        duration_hours=duration_hours,
        hourly_rate=pricing.hourly_rate,
        order_id=order["id"],
        razorpay_key_id=current_app.config["RAZORPAY_KEY_ID"],
        form=ExitForm(),
    )


@main_bp.route("/payment/callback", methods=["POST"])
@login_required
def payment_callback():
    active_session = get_active_session(current_user.id)
    if active_session is None:
        # Already paid or no session — redirect silently
        return redirect(url_for("main.dashboard"))

    payment_id = request.form.get("razorpay_payment_id", "")
    order_id = request.form.get("razorpay_order_id", "")
    signature = request.form.get("razorpay_signature", "")

    try:
        client = _razorpay_client()
        client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
    except razorpay.errors.SignatureVerificationError:
        logger.warning("Signature verification failed: order=%s payment=%s session=%s",
                       order_id, payment_id, active_session.id)
        flash("Payment verification failed — please contact support.", "danger")
        return redirect(url_for("main.dashboard"))
    except Exception:
        logger.exception("Unexpected error during signature verification session=%s", active_session.id)
        flash("Payment verification failed — please contact support.", "danger")
        return redirect(url_for("main.dashboard"))

    try:
        # Use fee and exit_time already stored by exit() — do not recompute
        fee = active_session.fee
        now = active_session.exit_time

        active_session.paid = True
        if active_session.spot:
            active_session.spot.status = "available"
        else:
            logger.warning("Spot missing for session=%s — spot status not reset", active_session.id)
        log_activity(current_user.id, "payment", f"session={active_session.id} fee={fee}")
        db.session.commit()
        logger.info("Payment success: session=%s fee=%s order=%s payment=%s",
                    active_session.id, fee, order_id, payment_id)
        receipt_url = url_for("main.receipt", session_id=active_session.id)
        flash(
            f'Payment successful. <a href="{receipt_url}">Download Receipt</a>',
            "success",
        )
    except Exception:
        db.session.rollback()
        logger.exception("Failed to close session=%s after payment", active_session.id)
        flash("Payment recorded but session close failed — please contact support.", "danger")

    return redirect(url_for("main.dashboard"))


@main_bp.route("/retry-payment", methods=["POST"])
@login_required
def retry_payment():
    """G4: Re-create a Razorpay order for a session that exited but was never paid."""
    form = ExitForm()
    if not form.validate_on_submit():
        abort(400)
    session = get_active_session(current_user.id)
    if not session or session.paid or session.exit_time is None:
        flash("No pending payment found.", "warning")
        return redirect(url_for("main.dashboard"))
    pricing = PricingConfig.get_or_create()
    fee = session.fee or calculate_fee(session.entry_time, session.exit_time, pricing.hourly_rate)
    try:
        client = _razorpay_client()
        order = client.order.create({
            "amount": int(fee * 100),
            "currency": "INR",
            "receipt": f"retry_{session.id}",
        })
        session.razorpay_order_id = order["id"]
        db.session.commit()
    except Exception:
        logger.exception("Razorpay order creation failed on retry for session=%s", session.id)
        flash("Payment gateway error. Please try again.", "danger")
        return redirect(url_for("main.dashboard"))
    duration_hours = (session.exit_time - session.entry_time).total_seconds() / 3600
    return render_template(
        "main/payment.html",
        session=session,
        fee=fee,
        duration_hours=duration_hours,
        hourly_rate=pricing.hourly_rate,
        order_id=order["id"],
        razorpay_key_id=current_app.config["RAZORPAY_KEY_ID"],
        form=ExitForm(),
    )


@main_bp.route("/profile")
@login_required
def profile():
    """G5: Read-only user profile showing account details and parking stats."""
    paid_sessions = ParkingSession.query.filter_by(user_id=current_user.id, paid=True).all()
    total_spent = sum((s.fee for s in paid_sessions if s.fee), Decimal("0.00"))
    return render_template(
        "main/profile.html",
        total_sessions=len(paid_sessions),
        total_spent=total_spent,
    )


@main_bp.route("/receipt/<int:session_id>")
@login_required
def receipt(session_id):
    """H2: Print-friendly payment receipt for a completed session."""
    parking_session = db.session.get(ParkingSession, session_id)
    if not parking_session or parking_session.user_id != current_user.id or not parking_session.paid:
        abort(404)
    return render_template("main/receipt.html", session=parking_session)


@main_bp.route("/vehicles", methods=["GET", "POST"])
@login_required
def vehicles():
    """H7: List saved vehicles and add new ones."""
    form = AddVehicleForm()
    if form.validate_on_submit():
        existing = Vehicle.query.filter_by(
            user_id=current_user.id,
            vehicle_number=form.vehicle_number.data.upper(),
        ).first()
        if existing:
            flash("Vehicle already saved.", "warning")
        else:
            db.session.add(Vehicle(
                user_id=current_user.id,
                vehicle_number=form.vehicle_number.data.upper(),
                label=form.label.data or None,
            ))
            db.session.commit()
            flash("Vehicle saved.", "success")
        return redirect(url_for("main.vehicles"))
    user_vehicles = Vehicle.query.filter_by(user_id=current_user.id).all()
    return render_template("main/vehicles.html", form=form, vehicles=user_vehicles)


@main_bp.route("/vehicles/<int:vid>/delete", methods=["POST"])
@login_required
def delete_vehicle(vid):
    """H7: Remove a saved vehicle owned by the current user."""
    v = db.session.get(Vehicle, vid)
    if not v or v.user_id != current_user.id:
        abort(404)
    db.session.delete(v)
    db.session.commit()
    flash("Vehicle removed.", "info")
    return redirect(url_for("main.vehicles"))
