"""Service helpers — Repository-lite pattern: DB query helpers extracted from routes."""
# Pattern: Repository — routes call these helpers instead of writing SQLAlchemy queries inline
from app.models import ParkingSpot, ParkingSession


def get_active_session(user_id):
    """Return the open or awaiting-payment session for a user, or None."""
    return ParkingSession.query.filter_by(user_id=user_id, paid=False).first()


def get_first_available_spot():
    """Return the lowest-numbered available spot, or None if no spots are free."""
    return (
        ParkingSpot.query
        .filter_by(status="available")
        .order_by(ParkingSpot.spot_number)
        .first()
    )
