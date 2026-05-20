import os
from datetime import timedelta

# Ensure instance directory exists
instance_dir = os.path.join(os.path.dirname(__file__), "instance")
if not os.path.exists(instance_dir):
    os.makedirs(instance_dir)


class Config:
    """Base configuration — extended by DevelopmentConfig and ProductionConfig."""
    # Principle: OCP — base config defines common settings; subclasses extend
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CACHE_TYPE = "SimpleCache"
    CACHE_DEFAULT_TIMEOUT = 60
    OVERSTAY_HOURS = 8  # G7: sessions active longer than this many hours are highlighted in admin
    LOGIN_MAX_ATTEMPTS = 5  # G10: max failed logins before account lockout
    LOGIN_LOCKOUT_MINUTES = 15  # G10: lockout duration
    LOGIN_ATTEMPT_WINDOW_MINUTES = 10  # G10: window for counting failed attempts


class DevelopmentConfig(Config):
    """Development configuration — SQLite, debug mode, easy testing."""
    DEBUG = True
    TESTING = False
    SQLALCHEMY_ECHO = False  # Set True manually to log SQL queries during debugging
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("SQLALCHEMY_DATABASE_URI")
        or f"sqlite:///{instance_dir}/parking.db"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False}  # Allow SQLite with threads
    }
    SESSION_COOKIE_SECURE = False  # Allow HTTP in dev
    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "test-key-id")
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "test-key-secret")


class ProductionConfig(Config):
    """Production configuration — PostgreSQL-ready, env var overrides."""
    DEBUG = False
    TESTING = False
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("SQLALCHEMY_DATABASE_URI")
        or f"sqlite:///{instance_dir}/parking.db"
    )
    SQLALCHEMY_ENGINE_OPTIONS = (
        {"connect_args": {"check_same_thread": False}}  # SQLite + Waitress thread safety
        if not os.environ.get("SQLALCHEMY_DATABASE_URI") or
           os.environ.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite")
        else {}
    )
    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
    # Only enforce Secure cookies when HTTPS is actually configured.
    # Set SESSION_COOKIE_SECURE=true in .env when TLS is enabled (e.g. with Certbot).
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"


class TestingConfig(Config):
    """Testing configuration — in-memory SQLite for pytest."""
    DEBUG = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    RAZORPAY_KEY_ID = "test-key-id"
    RAZORPAY_KEY_SECRET = "test-key-secret"
    RATELIMIT_ENABLED = False
    CACHE_TYPE = "NullCache"  # disables caching entirely in tests


def get_config():
    """Return config class based on FLASK_ENV."""
    env = os.environ.get("FLASK_ENV", "development")
    if env == "production":
        return ProductionConfig
    elif env == "testing":
        return TestingConfig
    else:
        return DevelopmentConfig
