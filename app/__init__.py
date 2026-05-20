import logging
import os
from datetime import timedelta
from logging.handlers import RotatingFileHandler

from flask import Flask, render_template
from config import get_config
from app.extensions import init_extensions, db, login_manager


# Pattern: Factory — create_app constructs and wires the app without exposing internals
def create_app(config_class=None):
    """Application factory — creates and configures a Flask app instance."""
    if config_class is None:
        config_class = get_config()

    app = Flask(__name__)
    app.config.from_object(config_class)

    # Configure structured logging
    log_level = logging.DEBUG if app.config.get("DEBUG") else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    if not app.config.get("DEBUG") and not app.config.get("TESTING"):
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "parkeasy.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        logging.getLogger("app").addHandler(file_handler)

    logging.basicConfig(level=log_level, handlers=[console_handler])
    app.logger.setLevel(log_level)

    # Initialize extensions
    init_extensions(app)

    @app.template_filter("to_ist")
    def to_ist(dt):
        """Convert a naive UTC datetime to IST (UTC+5:30) for display."""
        if dt is None:
            return "—"
        ist = dt + timedelta(hours=5, minutes=30)
        return ist.strftime("%d %b %Y, %H:%M IST")

    # Register user loader for Flask-Login
    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    # Import and register CLI commands
    from app.cli import register_cli_commands
    register_cli_commands(app)

    # Register blueprints
    from app.auth import auth_bp
    from app.main import main_bp
    from app.admin import admin_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)

    # Register error handlers
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    return app
