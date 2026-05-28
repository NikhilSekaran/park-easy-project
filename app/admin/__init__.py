from flask import Blueprint

# Principle: SRP — admin_bp owns only admin-facing flows
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

from app.admin import routes  # noqa: E402, F401
