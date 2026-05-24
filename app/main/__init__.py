from flask import Blueprint

# Principle: SRP — main_bp owns only user-facing flows (dashboard, check-in, exit, payment)
main_bp = Blueprint("main", __name__)

from app.main import routes  # noqa: E402, F401
