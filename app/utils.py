"""Utility functions — business logic extracted from routes."""
import math
from decimal import Decimal


# Pattern: Strategy — calculate_fee is a standalone, swappable fee-calculation function
def calculate_fee(entry_time, exit_time, hourly_rate, grace_minutes=0):
    """Return fee as Decimal, rounding partial hours up to the next whole hour.

    Minimum charge: 1 hour (after grace period).
    G3: sessions within grace_minutes of entry cost ₹0.
    """
    total_minutes = (exit_time - entry_time).total_seconds() / 60
    if grace_minutes > 0 and total_minutes <= grace_minutes:
        return Decimal("0.00")
    billable_minutes = total_minutes - grace_minutes
    duration_hours = max(math.ceil(billable_minutes / 60), 1)
    return Decimal(duration_hours) * Decimal(str(hourly_rate))


def log_activity(user_id, action, detail=None):
    """H10: Append an ActivityLog entry. Caller must commit the session."""
    from app.models import ActivityLog
    from app.extensions import db
    db.session.add(ActivityLog(user_id=user_id, action=action, detail=detail))
