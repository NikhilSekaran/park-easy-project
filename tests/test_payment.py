"""Tests for Slice 4: fee calculation, exit flow, Razorpay payment."""
import re
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
import razorpay

from app.utils import calculate_fee
from app.models import ParkingSession, ParkingSpot, PricingConfig
from app.extensions import db


# ---------------------------------------------------------------------------
# calculate_fee unit tests (no DB, no Flask context needed)
# ---------------------------------------------------------------------------

class TestCalculateFee:
    BASE = datetime(2026, 1, 1, 12, 0, 0)

    def _entry(self, minutes_back):
        return self.BASE - timedelta(minutes=minutes_back)

    def test_exactly_one_hour(self):
        fee = calculate_fee(self._entry(60), self.BASE, Decimal("50"))
        assert fee == Decimal("50")

    def test_partial_hour_rounds_up(self):
        fee = calculate_fee(self._entry(61), self.BASE, Decimal("50"))
        assert fee == Decimal("100")  # ceil(61/60) = 2 hours

    def test_zero_duration_charges_minimum_one_hour(self):
        fee = calculate_fee(self.BASE, self.BASE, Decimal("50"))
        assert fee == Decimal("50")

    def test_three_hours_exact(self):
        fee = calculate_fee(self._entry(180), self.BASE, Decimal("30"))
        assert fee == Decimal("90")

    def test_two_and_a_half_hours_rounds_to_three(self):
        fee = calculate_fee(self._entry(150), self.BASE, Decimal("40"))
        assert fee == Decimal("120")  # ceil(150/60) = 3 hours


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def login(client, email, password):
    return client.post("/auth/login", data={"email": email, "password": password}, follow_redirects=True)


def _checkin(client, app):
    """Log in as user, check in, and return the active session id."""
    login(client, "user@test.com", "user123")
    # Get CSRF from dashboard
    rv = client.get("/", follow_redirects=True)
    match = re.search(rb'name="csrf_token" value="([^"]+)"', rv.data)
    csrf = match.group(1).decode() if match else ""
    client.post("/checkin", data={"vehicle_number": "MH12AB1234", "csrf_token": csrf})
    with app.app_context():
        session = ParkingSession.query.filter_by(paid=False).first()
        return session.id if session else None


def _get_exit_csrf(client):
    """Get CSRF from the exit (payment) page."""
    with patch("app.main.routes._razorpay_client") as mock_client:
        mock_order = MagicMock()
        mock_order.order.create.return_value = {"id": "order_test123"}
        mock_client.return_value = mock_order
        rv = client.post("/exit", follow_redirects=True)
    match = re.search(rb'name="csrf_token" value="([^"]+)"', rv.data)
    return match.group(1).decode() if match else ""


# ---------------------------------------------------------------------------
# Exit route tests
# ---------------------------------------------------------------------------

class TestExitRoute:
    def test_exit_no_active_session_redirects(self, client):
        login(client, "user@test.com", "user123")
        rv = client.post("/exit", follow_redirects=True)
        assert b"No active parking session" in rv.data

    def test_exit_creates_razorpay_order_and_renders_payment_page(self, client, app):
        _checkin(client, app)

        with patch("app.main.routes._razorpay_client") as mock_client:
            mock_rz = MagicMock()
            mock_rz.order.create.return_value = {"id": "order_abc123"}
            mock_client.return_value = mock_rz

            rv = client.post("/exit", follow_redirects=True)

        assert rv.status_code == 200
        assert b"Fee Breakdown" in rv.data
        assert b"order_abc123" in rv.data
        mock_rz.order.create.assert_called_once()

    def test_exit_razorpay_failure_flashes_error(self, client, app):
        _checkin(client, app)

        with patch("app.main.routes._razorpay_client") as mock_client:
            mock_rz = MagicMock()
            mock_rz.order.create.side_effect = Exception("Network error")
            mock_client.return_value = mock_rz

            rv = client.post("/exit", follow_redirects=True)

        assert b"Payment initiation failed" in rv.data


# ---------------------------------------------------------------------------
# Payment callback tests
# ---------------------------------------------------------------------------

class TestPaymentCallback:
    def _post_callback(self, client, payment_id, order_id, signature, csrf=""):
        return client.post(
            "/payment/callback",
            data={
                "razorpay_payment_id": payment_id,
                "razorpay_order_id": order_id,
                "razorpay_signature": signature,
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )

    def test_valid_signature_closes_session_and_frees_spot(self, client, app):
        _checkin(client, app)
        csrf = _get_exit_csrf(client)

        with patch("app.main.routes._razorpay_client") as mock_client:
            mock_rz = MagicMock()
            mock_rz.utility.verify_payment_signature.return_value = None  # no exception = success
            mock_client.return_value = mock_rz

            rv = self._post_callback(client, "pay_123", "order_123", "sig_123", csrf)

        assert rv.status_code == 200
        assert b"Payment successful" in rv.data

        with app.app_context():
            session = ParkingSession.query.filter_by(vehicle_number="MH12AB1234").first()
            assert session.paid is True
            assert session.exit_time is not None
            assert session.fee is not None
            spot = db.session.get(ParkingSpot, session.spot_id)
            assert spot.status == "available"

    def test_invalid_signature_does_not_close_session(self, client, app):
        _checkin(client, app)
        csrf = _get_exit_csrf(client)

        with patch("app.main.routes._razorpay_client") as mock_client:
            mock_rz = MagicMock()
            mock_rz.utility.verify_payment_signature.side_effect = (
                razorpay.errors.SignatureVerificationError("bad sig")
            )
            mock_client.return_value = mock_rz

            rv = self._post_callback(client, "pay_bad", "order_bad", "sig_bad", csrf)

        assert b"Payment verification failed" in rv.data

        with app.app_context():
            session = ParkingSession.query.filter_by(vehicle_number="MH12AB1234").first()
            assert session.paid is False

    def test_callback_with_no_active_session_redirects_silently(self, client):
        login(client, "user@test.com", "user123")
        rv = self._post_callback(client, "pay_x", "order_x", "sig_x")
        # Should redirect to dashboard with no error
        assert rv.status_code == 200
        assert b"Check In" in rv.data

    def test_exit_works_without_seeded_pricing(self, client, app):
        """Exit should not 500 if PricingConfig row was never seeded."""
        with app.app_context():
            from app.models import PricingConfig
            db.session.query(PricingConfig).delete()
            db.session.commit()
        _checkin(client, app)
        with patch("app.main.routes._razorpay_client") as mock_client:
            mock_rz = MagicMock()
            mock_rz.order.create.return_value = {"id": "order_test999"}
            mock_client.return_value = mock_rz
            rv = client.post("/exit", follow_redirects=True)
        assert rv.status_code == 200
        assert b"Internal Server Error" not in rv.data
        assert b"Fee Breakdown" in rv.data

    def test_fee_stored_at_exit_not_recalculated(self, client, app):
        """Fee set in exit() must be stored on session row before callback."""
        _checkin(client, app)
        with patch("app.main.routes._razorpay_client") as mock_client:
            mock_rz = MagicMock()
            mock_rz.order.create.return_value = {"id": "order_test123"}
            mock_client.return_value = mock_rz
            client.post("/exit", follow_redirects=True)
        with app.app_context():
            s = ParkingSession.query.filter_by(paid=False).first()
            assert s.fee is not None
            assert s.razorpay_order_id is not None
            assert s.exit_time is not None
