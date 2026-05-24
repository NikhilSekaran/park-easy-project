"""Tests for Slice 3: user check-in flow and dashboard."""
import re
import pytest
from app.models import ParkingSession, ParkingSpot
from app.extensions import db


def login(client, email, password):
    return client.post("/auth/login", data={"email": email, "password": password}, follow_redirects=True)


def _get_csrf(client):
    """Extract CSRF token from the dashboard page (must be called after login)."""
    rv = client.get("/", follow_redirects=True)
    match = re.search(rb'name="csrf_token" value="([^"]+)"', rv.data)
    return match.group(1).decode() if match else ""


class TestDashboard:
    def test_dashboard_redirects_unauthenticated(self, client):
        rv = client.get("/")
        assert rv.status_code == 302
        assert "/login" in rv.headers["Location"]

    def test_dashboard_shows_checkin_form_when_no_session(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/")
        assert rv.status_code == 200
        assert b"Check In" in rv.data

    def test_dashboard_shows_session_details_when_active(self, client):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        client.post("/checkin", data={"vehicle_number": "MH12AB1234", "csrf_token": csrf})
        rv = client.get("/")
        assert rv.status_code == 200
        assert b"You are parked" in rv.data
        assert b"MH12AB1234" in rv.data


class TestCheckIn:
    def test_checkin_success_assigns_spot_and_creates_session(self, client, app):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        rv = client.post(
            "/checkin",
            data={"vehicle_number": "KA01AB1234", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert rv.status_code == 200
        assert b"You are parked" in rv.data or b"Checked in" in rv.data

        with app.app_context():
            session = ParkingSession.query.filter_by(paid=False).first()
            assert session is not None
            assert session.vehicle_number == "KA01AB1234"
            spot = db.session.get(ParkingSpot, session.spot_id)
            assert spot.status == "occupied"

    def test_checkin_when_already_active_session(self, client):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        client.post("/checkin", data={"vehicle_number": "KA01AB1234", "csrf_token": csrf})
        csrf = _get_csrf(client)
        rv = client.post(
            "/checkin",
            data={"vehicle_number": "KA02CD5678", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert b"already have an active parking session" in rv.data

    def test_checkin_no_spots_available(self, client, app):
        with app.app_context():
            ParkingSpot.query.update({"status": "occupied"})
            db.session.commit()

        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        rv = client.post(
            "/checkin",
            data={"vehicle_number": "MH12AB0001", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert b"No spots available" in rv.data

    def test_checkin_vehicle_number_blank(self, client):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        rv = client.post(
            "/checkin",
            data={"vehicle_number": "", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert rv.status_code == 200
        assert b"Check In" in rv.data

    def test_checkin_blocked_when_payment_pending(self, client, app):
        """User with exit_time set but paid=False should not be allowed to check in."""
        from datetime import datetime
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        client.post("/checkin", data={"vehicle_number": "MH12AB1234", "csrf_token": csrf})
        with app.app_context():
            s = ParkingSession.query.filter_by(paid=False).first()
            s.exit_time = datetime.utcnow()
            db.session.commit()
        csrf = _get_csrf(client)
        rv = client.post(
            "/checkin",
            data={"vehicle_number": "MH01AA0002", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert b"incomplete payment" in rv.data.lower()

    def test_checkin_vehicle_number_too_long(self, client, app):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        rv = client.post(
            "/checkin",
            data={"vehicle_number": "A" * 21, "csrf_token": csrf},
            follow_redirects=True,
        )
        assert rv.status_code == 200
        # No session should have been created
        with app.app_context():
            count = ParkingSession.query.count()
        assert count == 0

    def test_checkin_rejects_invalid_vehicle_number(self, client):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        rv = client.post("/checkin", data={"vehicle_number": "INVALID", "csrf_token": csrf}, follow_redirects=True)
        assert rv.status_code == 200
        assert b"valid vehicle number" in rv.data

    def test_checkin_rejects_empty_vehicle_number(self, client):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        rv = client.post("/checkin", data={"vehicle_number": "", "csrf_token": csrf}, follow_redirects=True)
        assert rv.status_code == 200

    def test_dashboard_shows_full_warning_when_no_spots(self, client, app):
        with app.app_context():
            ParkingSpot.query.update({"status": "inactive"})
            db.session.commit()
        login(client, "user@test.com", "user123")
        rv = client.get("/")
        assert rv.status_code == 200
        assert b"Parking Full" in rv.data

    def test_dashboard_shows_ist_label(self, client, app):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client)
        client.post("/checkin", data={"vehicle_number": "MH12AB1234", "csrf_token": csrf})
        rv = client.get("/")
        assert b"IST" in rv.data
        assert b"UTC" not in rv.data
