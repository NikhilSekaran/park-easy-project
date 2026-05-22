from functools import wraps
from flask import abort
from flask_login import current_user


# Pattern: Decorator — admin_required wraps route handlers to enforce role without changing route logic
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function
