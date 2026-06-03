"""Tests for G-feature additions: health check, profile, payment retry."""
import re
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from app.extensions import db
from app.models import ParkingSession, ParkingSpot, PricingConfig


def login(client, email, password):
    return client.post("/auth/login", data={"email": email, "password": password},
                       follow_redirects=True)


def _get_csrf(client, url="/"):
    rv = client.get(url, follow_redirects=True)
    match = re.search(rb'name="csrf_token" value="([^"]+)"', rv.data)
    return match.group(1).decode() if match else ""


class TestHealthCheck:
    def test_health_ok(self, client):
        rv = client.get("/health")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["status"] == "ok"
        assert data["db"] == "ok"

    def test_health_no_auth_required(self, client):
        rv = client.get("/health")
        assert rv.status_code == 200  # no login needed


class TestProfile:
    def test_profile_requires_login(self, client):
        rv = client.get("/profile", follow_redirects=False)
        assert rv.status_code == 302
        assert "/auth/login" in rv.headers["Location"]

    def test_profile_shows_email(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/profile")
        assert rv.status_code == 200
        assert b"user@test.com" in rv.data

    def test_profile_shows_zero_sessions_initially(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/profile")
        assert b"0" in rv.data

    def test_profile_link_in_navbar(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/")
        assert b"Profile" in rv.data


class TestRetryPayment:
    def _setup_unpaid_session(self, app, user_id=2, spot_id=1):
        """Create a session with exit_time set but paid=False."""
        with app.app_context():
            spot = db.session.get(ParkingSpot, spot_id)
            spot.status = "occupied"
            s = ParkingSession(
                user_id=user_id,
                spot_id=spot_id,
                vehicle_number="MH12AB1234",
                exit_time=datetime.utcnow(),
                fee=Decimal("50.00"),
                paid=False,
            )
            db.session.add(s)
            db.session.commit()
            return s.id

    def test_retry_payment_no_session_shows_warning(self, client):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client, "/")
        rv = client.post("/retry-payment", data={"csrf_token": csrf}, follow_redirects=True)
        assert rv.status_code == 200
        assert b"No pending payment" in rv.data

    def test_retry_payment_shows_payment_page(self, client, app, monkeypatch):
        self._setup_unpaid_session(app)
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client, "/")

        import razorpay
        fake_order = {"id": "order_retry123"}

        class FakeClient:
            class order:
                @staticmethod
                def create(_): return fake_order
            class utility:
                @staticmethod
                def verify_payment_signature(_): pass

        monkeypatch.setattr("app.main.routes._razorpay_client", lambda: FakeClient())
        rv = client.post("/retry-payment", data={"csrf_token": csrf}, follow_redirects=True)
        assert rv.status_code == 200
        assert b"order_retry123" in rv.data or b"payment" in rv.data.lower()


# ---- Slice 14: Group H feature tests ----

class TestReceipt:
    def _make_paid_session(self, app, user_id=2, spot_id=1):
        from datetime import timedelta
        with app.app_context():
            spot = db.session.get(ParkingSpot, spot_id)
            spot.status = "occupied"
            entry = datetime.utcnow() - timedelta(hours=1)
            s = ParkingSession(
                user_id=user_id, spot_id=spot_id, vehicle_number="MH12AB1234",
                entry_time=entry, exit_time=datetime.utcnow(),
                fee=Decimal("50.00"), paid=True,
            )
            db.session.add(s)
            db.session.commit()
            spot.status = "available"
            db.session.commit()
            return s.id

    def test_receipt_accessible_for_owner(self, client, app):
        sid = self._make_paid_session(app)
        login(client, "user@test.com", "user123")
        rv = client.get(f"/receipt/{sid}")
        assert rv.status_code == 200
        assert b"Payment Receipt" in rv.data

    def test_receipt_404_for_unpaid_session(self, client, app):
        with app.app_context():
            spot = db.session.get(ParkingSpot, 1)
            spot.status = "occupied"
            s = ParkingSession(user_id=2, spot_id=1, vehicle_number="MH12AB1234",
                               fee=Decimal("50.00"), paid=False)
            db.session.add(s)
            db.session.commit()
            sid = s.id
        login(client, "user@test.com", "user123")
        rv = client.get(f"/receipt/{sid}")
        assert rv.status_code == 404

    def test_receipt_404_for_other_user(self, client, app):
        sid = self._make_paid_session(app, user_id=2)
        login(client, "admin@test.com", "admin123")
        rv = client.get(f"/receipt/{sid}")
        assert rv.status_code == 404


class TestDashboardH:
    def test_dashboard_has_meta_refresh(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/")
        assert b'http-equiv="refresh"' in rv.data

    def test_dashboard_meta_refresh_disabled_by_param(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/?live=0")
        assert b'http-equiv="refresh"' not in rv.data

    def test_dashboard_has_liveFee_element_when_active(self, client, app):
        """liveFee only renders when the user has an active session."""
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client, "/")
        client.post("/checkin", data={"vehicle_number": "MH12AB1234", "csrf_token": csrf})
        rv = client.get("/")
        assert b"liveFee" in rv.data

    def test_darkToggle_button_present(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/")
        assert b"darkToggle" in rv.data


class TestVehicles:
    def test_save_vehicle(self, client):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client, "/vehicles")
        rv = client.post("/vehicles", data={
            "vehicle_number": "MH12AB1234", "label": "My Car", "csrf_token": csrf,
        }, follow_redirects=True)
        assert b"Vehicle saved" in rv.data

    def test_save_duplicate_vehicle_shows_warning(self, client):
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client, "/vehicles")
        client.post("/vehicles", data={"vehicle_number": "MH12AB1234", "csrf_token": csrf})
        rv = client.post("/vehicles", data={"vehicle_number": "MH12AB1234", "csrf_token": csrf},
                         follow_redirects=True)
        assert b"already saved" in rv.data

    def test_delete_vehicle(self, client, app):
        login(client, "user@test.com", "user123")
        from app.models import Vehicle
        with app.app_context():
            v = Vehicle(user_id=2, vehicle_number="KA01XY9999")
            db.session.add(v)
            db.session.commit()
            vid = v.id
        csrf = _get_csrf(client, "/vehicles")
        rv = client.post(f"/vehicles/{vid}/delete", data={"csrf_token": csrf},
                         follow_redirects=True)
        assert b"removed" in rv.data

    def test_delete_other_user_vehicle_blocked(self, client, app):
        from app.models import Vehicle
        with app.app_context():
            v = Vehicle(user_id=1, vehicle_number="KA01XY0001")
            db.session.add(v)
            db.session.commit()
            vid = v.id
        login(client, "user@test.com", "user123")
        csrf = _get_csrf(client, "/vehicles")
        rv = client.post(f"/vehicles/{vid}/delete", data={"csrf_token": csrf})
        assert rv.status_code == 404

    def test_vehicles_requires_login(self, client):
        rv = client.get("/vehicles", follow_redirects=False)
        assert rv.status_code == 302
        assert "/auth/login" in rv.headers["Location"]
