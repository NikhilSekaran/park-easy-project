from datetime import datetime
from app.extensions import db, bcrypt
from flask_login import UserMixin


class User(UserMixin, db.Model):
    """User account — supports login, role-based access."""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")  # 'user' or 'admin'
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # G10: account lockout fields — track failed login attempts
    failed_logins = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_failed_at = db.Column(db.DateTime, nullable=True)

    sessions = db.relationship("ParkingSession", backref="user", lazy=True)

    def set_password(self, password):
        """Hash and store password using bcrypt."""
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password):
        """Verify password against stored hash."""
        return bcrypt.check_password_hash(self.password_hash, password)

    def is_admin(self):
        """Check if user has admin role."""
        return self.role == "admin"

    def __repr__(self):
        return f"<User {self.email}>"


class ParkingSpot(db.Model):
    """Parking spot — managed by admin, status transitions through check-in/exit."""
    __tablename__ = "parking_spots"

    id = db.Column(db.Integer, primary_key=True)
    spot_number = db.Column(db.Integer, unique=True, nullable=False, index=True)
    status = db.Column(
        db.String(20), nullable=False, default="available"
    )  # available, occupied, inactive

    sessions = db.relationship("ParkingSession", backref="spot", lazy=True)

    def __repr__(self):
        return f"<ParkingSpot {self.spot_number} ({self.status})>"


class ParkingSession(db.Model):
    """Parking session — user check-in/exit, fee calculation, payment tracking."""
    __tablename__ = "parking_sessions"
    __table_args__ = (
        db.Index("ix_active_session", "user_id", "paid", "exit_time"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    spot_id = db.Column(
        db.Integer, db.ForeignKey("parking_spots.id"), nullable=False, index=True
    )
    vehicle_number = db.Column(db.String(50), nullable=False)
    entry_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    exit_time = db.Column(db.DateTime)
    fee = db.Column(db.Numeric(10, 2))
    paid = db.Column(db.Boolean, nullable=False, default=False)
    razorpay_order_id = db.Column(db.String(100), nullable=True)
    admin_note = db.Column(db.String(200), nullable=True)  # H9: admin annotation per session

    def __repr__(self):
        return f"<ParkingSession user={self.user_id} spot={self.spot_id} vehicle={self.vehicle_number}>"


class PricingConfig(db.Model):
    """Pricing configuration — single-row table storing current hourly rate."""
    __tablename__ = "pricing_config"

    id = db.Column(db.Integer, primary_key=True)
    hourly_rate = db.Column(db.Numeric(10, 2), nullable=False, default=50)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    grace_minutes = db.Column(db.Integer, nullable=False, default=0)  # G3: free window before billing

    def __repr__(self):
        return f"<PricingConfig hourly_rate={self.hourly_rate}>"

    @classmethod
    def get_current(cls):
        """Get the single pricing config row. Result is cached for 60 seconds."""
        from app.extensions import cache
        cached = cache.get("pricing_config")
        if cached is not None:
            return cached
        result = cls.query.first()
        cache.set("pricing_config", result)
        return result

    @classmethod
    def get_or_create(cls, default_rate=50):
        """Get pricing config, auto-creating the row with default_rate if missing."""
        config = cls.query.first()
        if config is None:
            config = cls(hourly_rate=default_rate)
            from app.extensions import db
            db.session.add(config)
            db.session.commit()
        return config


class Announcement(db.Model):
    """H4: Admin-managed banner message shown on the user dashboard."""
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String(280), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get_active(cls):
        """Return the most-recently-updated active announcement, or None."""
        return cls.query.filter_by(is_active=True).order_by(cls.updated_at.desc()).first()


class Vehicle(db.Model):
    """H7: Saved vehicle numbers per user — reduces re-typing on check-in."""
    __tablename__ = "vehicles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    vehicle_number = db.Column(db.String(20), nullable=False)
    label = db.Column(db.String(50), nullable=True)
    __table_args__ = (db.UniqueConstraint("user_id", "vehicle_number"),)

    user = db.relationship("User", backref=db.backref("vehicles", lazy=True))


class ActivityLog(db.Model):
    """H10: Audit trail of key actions performed by users and admins."""
    __tablename__ = "activity_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(64), nullable=False, index=True)
    detail = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref=db.backref("activity", lazy=True))
