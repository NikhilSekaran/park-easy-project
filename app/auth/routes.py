from datetime import datetime, timedelta
from urllib.parse import urlparse, urljoin

from flask import render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.auth import auth_bp
from app.auth.forms import RegistrationForm, LoginForm
from app.extensions import db, limiter
from app.models import User
from app.utils import log_activity


def _dashboard_url():
    """Return the dashboard URL if main blueprint is registered, else root."""
    try:
        return url_for("main.dashboard")
    except Exception:
        return "/"


def _is_safe_url(target):
    """Return True only if target is a relative URL on the same host."""
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return test.scheme in ("http", "https") and ref.netloc == test.netloc


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(_dashboard_url())

    form = RegistrationForm()
    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data.lower()).first():
            flash("Email already registered.", "danger")
            return render_template("auth/register.html", form=form)

        user = User(email=form.email.data.lower(), role="user")
        user.set_password(form.password.data)
        try:
            db.session.add(user)
            db.session.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("auth.login"))
        except Exception:
            db.session.rollback()
            flash("Registration failed. Please try again.", "danger")
            return render_template("auth/register.html", form=form)

    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(_dashboard_url())

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()

        # G10: check account lockout before attempting password verification
        if user and user.locked_until and user.locked_until > datetime.utcnow():
            remaining = int((user.locked_until - datetime.utcnow()).total_seconds() // 60) + 1
            flash(f"Account locked. Try again in {remaining} minute(s).", "danger")
            return render_template("auth/login.html", form=form)

        if user and user.check_password(form.password.data):
            # Successful login — reset lockout counters
            user.failed_logins = 0
            user.locked_until = None
            user.last_failed_at = None
            log_activity(user.id, "login")
            db.session.commit()
            login_user(user, remember=form.remember.data)  # G2: remember=True sets 30-day cookie
            next_page = request.args.get("next")
            if not _is_safe_url(next_page):
                next_page = None
            return redirect(next_page or _dashboard_url())
        else:
            # G10: track failed attempt and lock if threshold reached
            if user:
                window = current_app.config.get("LOGIN_ATTEMPT_WINDOW_MINUTES", 10)
                max_attempts = current_app.config.get("LOGIN_MAX_ATTEMPTS", 5)
                lockout_min = current_app.config.get("LOGIN_LOCKOUT_MINUTES", 15)
                now = datetime.utcnow()
                if user.last_failed_at and (now - user.last_failed_at) > timedelta(minutes=window):
                    user.failed_logins = 0  # reset counter outside window
                user.failed_logins += 1
                user.last_failed_at = now
                if user.failed_logins >= max_attempts:
                    user.locked_until = now + timedelta(minutes=lockout_min)
                    db.session.commit()
                    flash(f"Too many failed attempts. Account locked for {lockout_min} minutes.", "danger")
                    return render_template("auth/login.html", form=form)
                db.session.commit()
            flash("Invalid email or password.", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    from app.auth.forms import ChangePasswordForm
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("Current password is incorrect.", "danger")
            return render_template("auth/change_password.html", form=form)
        current_user.set_password(form.new_password.data)
        db.session.commit()
        flash("Password changed successfully.", "success")
        return redirect(_dashboard_url())
    return render_template("auth/change_password.html", form=form)
