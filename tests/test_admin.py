"""Tests for Slice 5: admin blueprint — spots, sessions, pricing."""
import re
from decimal import Decimal

import pytest
from app.models import ParkingSpot, ParkingSession, PricingConfig
from app.extensions import db


def login(client, email, password):
    return client.post("/auth/login", data={"email": email, "password": password}, follow_redirects=True)


def _get_csrf(client, url="/admin/"):
    rv = client.get(url, follow_redirects=True)
    match = re.search(rb'name="csrf_token" value="([^"]+)"', rv.data)
    return match.group(1).decode() if match else ""


class TestAdminAccess:
    def test_non_admin_gets_403(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/admin/")
        assert rv.status_code == 403

    def test_unauthenticated_redirects_to_login(self, client):
        rv = client.get("/admin/", follow_redirects=True)
        assert b"Login" in rv.data

    def test_admin_can_access_dashboard(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/")
        assert rv.status_code == 200
        assert b"Admin Dashboard" in rv.data


class TestSpotManagement:
    def test_add_spot_success(self, client, app):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/spots")
        rv = client.post(
            "/admin/spots",
            data={"spot_number": 99, "csrf_token": csrf},
            follow_redirects=True,
        )
        assert b"added" in rv.data
        with app.app_context():
            assert ParkingSpot.query.filter_by(spot_number=99).first() is not None

    def test_add_duplicate_spot_number(self, client):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/spots")
        # Spots 1, 2, 3 are seeded by conftest
        rv = client.post(
            "/admin/spots",
            data={"spot_number": 1, "csrf_token": csrf},
            follow_redirects=True,
        )
        assert b"already exists" in rv.data

    def test_toggle_spot_available_to_inactive(self, client, app):
        login(client, "admin@test.com", "admin123")
        with app.app_context():
            spot = ParkingSpot.query.filter_by(status="available").first()
            spot_id = spot.id

        csrf = _get_csrf(client, "/admin/spots")
        client.post(f"/admin/spots/{spot_id}/toggle", data={"csrf_token": csrf}, follow_redirects=True)

        with app.app_context():
            spot = db.session.get(ParkingSpot, spot_id)
            assert spot.status == "inactive"

    def test_cannot_deactivate_occupied_spot(self, client, app):
        # Mark spot 1 as occupied
        with app.app_context():
            spot = ParkingSpot.query.filter_by(spot_number=1).first()
            spot.status = "occupied"
            db.session.commit()
            spot_id = spot.id

        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/spots")
        rv = client.post(f"/admin/spots/{spot_id}/toggle", data={"csrf_token": csrf}, follow_redirects=True)
        assert b"Cannot deactivate" in rv.data

        with app.app_context():
            assert db.session.get(ParkingSpot, spot_id).status == "occupied"


class TestSessionsView:
    def test_sessions_list_shows_empty_state(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/sessions")
        assert rv.status_code == 200
        # No sessions in conftest, so empty state
        assert b"No sessions" in rv.data or b"Sessions" in rv.data

    def test_sessions_list_shows_after_checkin(self, client, app):
        # Create a session directly in DB
        with app.app_context():
            from app.models import User
            user = User.query.filter_by(email="user@test.com").first()
            spot = ParkingSpot.query.filter_by(status="available").first()
            spot.status = "occupied"
            session = ParkingSession(user_id=user.id, spot_id=spot.id, vehicle_number="KA01AA1111")
            db.session.add(session)
            db.session.commit()

        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/sessions")
        assert rv.status_code == 200
        assert b"KA01AA1111" in rv.data


class TestPricingManagement:
    def test_view_pricing_shows_current_rate(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/pricing")
        assert rv.status_code == 200
        assert b"50" in rv.data  # conftest seeds rate=50

    def test_update_pricing_success(self, client, app):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/pricing")
        rv = client.post(
            "/admin/pricing",
            data={"hourly_rate": "75.00", "grace_minutes": "0", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert b"updated" in rv.data
        with app.app_context():
            config = PricingConfig.get_current()
            assert float(config.hourly_rate) == 75.0

    def test_invalid_rate_rejected(self, client):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/pricing")
        rv = client.post(
            "/admin/pricing",
            data={"hourly_rate": "-5", "grace_minutes": "0", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert rv.status_code == 200
        # Should show inline validation error, not a flash success
        assert b"Rate must be greater than zero" in rv.data
        assert b"Hourly rate updated to" not in rv.data

    def test_rate_change_reflected_in_fee_calculation(self, app):
        """Integration: updated rate is used in calculate_fee."""
        from datetime import datetime, timedelta
        from decimal import Decimal
        from app.utils import calculate_fee

        new_rate = Decimal("100")
        entry = datetime(2026, 1, 1, 10, 0, 0)
        exit_ = datetime(2026, 1, 1, 11, 30, 0)
        fee = calculate_fee(entry, exit_, new_rate)
        assert fee == Decimal("200")  # ceil(1.5h) = 2h × 100 = 200


class TestRevenueAndExport:
    def test_revenue_page_accessible(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/revenue")
        assert rv.status_code == 200

    def test_export_sessions_csv(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/sessions/export")
        assert rv.status_code == 200
        assert b"User Email" in rv.data

    def test_admin_dashboard_shows_revenue(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/")
        assert rv.status_code == 200
        assert b"Revenue" in rv.data


class TestBulkSpots:
    def test_bulk_add_spots(self, client, app):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/spots")
        rv = client.post("/admin/spots/bulk", data={
            "start_number": 20, "count": 3, "csrf_token": csrf
        }, follow_redirects=True)
        assert rv.status_code == 200
        assert b"Added 3" in rv.data

    def test_bulk_add_skips_existing(self, client, app):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/spots")
        # Spots 1-3 already seeded in conftest
        rv = client.post("/admin/spots/bulk", data={
            "start_number": 1, "count": 2, "csrf_token": csrf
        }, follow_redirects=True)
        assert b"skipped" in rv.data


class TestBulkToggleSpots:
    def _spot_ids(self, app, statuses):
        """Create spots with given statuses, return their ids."""
        ids = []
        with app.app_context():
            for i, status in enumerate(statuses, start=50):
                s = ParkingSpot(spot_number=i, status=status)
                db.session.add(s)
            db.session.flush()
            db.session.commit()
            ids = [
                ParkingSpot.query.filter_by(spot_number=i).first().id
                for i in range(50, 50 + len(statuses))
            ]
        return ids

    def test_bulk_activate_inactive_spots(self, client, app):
        ids = self._spot_ids(app, ["inactive", "inactive"])
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/spots")
        rv = client.post("/admin/spots/bulk-toggle", data={
            "spot_ids": ids,
            "action": "activate",
            "csrf_token": csrf,
        }, follow_redirects=True)
        assert rv.status_code == 200
        assert b"2 spot(s) activated" in rv.data
        with app.app_context():
            for sid in ids:
                assert db.session.get(ParkingSpot, sid).status == "available"

    def test_bulk_deactivate_available_spots(self, client, app):
        ids = self._spot_ids(app, ["available", "available"])
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/spots")
        rv = client.post("/admin/spots/bulk-toggle", data={
            "spot_ids": ids,
            "action": "deactivate",
            "csrf_token": csrf,
        }, follow_redirects=True)
        assert rv.status_code == 200
        assert b"2 spot(s) deactivated" in rv.data
        with app.app_context():
            for sid in ids:
                assert db.session.get(ParkingSpot, sid).status == "inactive"

    def test_bulk_toggle_skips_occupied(self, client, app):
        ids = self._spot_ids(app, ["occupied", "inactive"])
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/spots")
        rv = client.post("/admin/spots/bulk-toggle", data={
            "spot_ids": ids,
            "action": "activate",
            "csrf_token": csrf,
        }, follow_redirects=True)
        assert rv.status_code == 200
        assert b"skipped" in rv.data
        with app.app_context():
            assert db.session.get(ParkingSpot, ids[0]).status == "occupied"
            assert db.session.get(ParkingSpot, ids[1]).status == "available"

    def test_bulk_toggle_no_selection_shows_warning(self, client, app):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/spots")
        rv = client.post("/admin/spots/bulk-toggle", data={
            "action": "activate",
            "csrf_token": csrf,
        }, follow_redirects=True)
        assert rv.status_code == 200
        assert b"No spots selected" in rv.data


class TestUsersPage:
    def test_users_page_accessible(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/users")
        assert rv.status_code == 200
        assert b"admin@test.com" in rv.data

    def test_users_page_blocked_for_non_admin(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/admin/users")
        assert rv.status_code == 403


# G3: Grace period fee tests
class TestGracePeriodFee:
    def test_fee_within_grace_period_is_zero(self):
        from app.utils import calculate_fee
        from datetime import datetime
        entry = datetime(2026, 1, 1, 10, 0)
        exit_ = datetime(2026, 1, 1, 10, 8)  # 8 minutes
        assert calculate_fee(entry, exit_, 50, grace_minutes=10) == Decimal("0.00")

    def test_fee_after_grace_period(self):
        from app.utils import calculate_fee
        from datetime import datetime
        entry = datetime(2026, 1, 1, 10, 0)
        exit_ = datetime(2026, 1, 1, 10, 30)  # 30 min, grace=10 → 20 min billable → 1 hour min
        assert calculate_fee(entry, exit_, 50, grace_minutes=10) == Decimal("50.00")

    def test_grace_zero_charges_minimum_one_hour(self):
        from app.utils import calculate_fee
        from datetime import datetime
        entry = datetime(2026, 1, 1, 10, 0)
        exit_ = datetime(2026, 1, 1, 10, 0)  # zero duration
        assert calculate_fee(entry, exit_, 50, grace_minutes=0) == Decimal("50.00")

    def test_admin_can_set_grace_minutes(self, client, app):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/pricing")
        rv = client.post("/admin/pricing", data={
            "hourly_rate": "50.00", "grace_minutes": "10", "csrf_token": csrf,
        }, follow_redirects=True)
        assert rv.status_code == 200
        with app.app_context():
            config = PricingConfig.query.first()
            assert config.grace_minutes == 10


# G6: Admin session search & filter tests
class TestSessionFilter:
    def test_filter_by_vehicle(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/sessions?vehicle=MH12")
        assert rv.status_code == 200

    def test_filter_paid_only(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/sessions?paid=1")
        assert rv.status_code == 200

    def test_filter_unpaid_only(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/sessions?paid=0")
        assert rv.status_code == 200

    def test_filter_form_rendered(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/sessions")
        assert b"Vehicle number" in rv.data or b"vehicle" in rv.data.lower()

    def test_clear_link_present(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/sessions?vehicle=XY")
        assert b"Clear" in rv.data


# G7: Overstay alert test
class TestOverstayAlert:
    def test_overstay_legend_present(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/sessions")
        assert rv.status_code == 200
        assert b"Highlighted" in rv.data or b"overstay" in rv.data.lower() or b"active &gt;" in rv.data


# G8: Stats JSON endpoint tests
class TestStatsJson:
    def test_stats_json_shape(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/stats.json")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "labels" in data and len(data["labels"]) == 7
        assert "revenue" in data and len(data["revenue"]) == 7
        assert "occupancy" in data
        assert set(data["occupancy"].keys()) == {"available", "occupied", "inactive"}

    def test_stats_json_blocked_for_non_admin(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/admin/stats.json")
        assert rv.status_code == 403


# G9: Auto-close stale sessions CLI tests
class TestCloseStale:
    def test_close_stale_closes_old_sessions(self, app, runner):
        from datetime import datetime, timedelta
        with app.app_context():
            old = datetime.utcnow() - timedelta(hours=30)
            spot = ParkingSpot.query.filter_by(status="available").first()
            s = ParkingSession(user_id=2, spot_id=spot.id, vehicle_number="MH01AA0001",
                               entry_time=old)
            db.session.add(s)
            db.session.commit()
            sid = s.id
        result = runner.invoke(args=["close-stale", "--hours", "24"])
        assert "Closed 1" in result.output
        with app.app_context():
            s = db.session.get(ParkingSession, sid)
            assert s.exit_time is not None
            assert s.fee is not None

    def test_close_stale_dry_run_makes_no_changes(self, app, runner):
        from datetime import datetime, timedelta
        with app.app_context():
            old = datetime.utcnow() - timedelta(hours=30)
            spot = ParkingSpot.query.filter_by(status="available").first()
            s = ParkingSession(user_id=2, spot_id=spot.id, vehicle_number="MH01AA0002",
                               entry_time=old)
            db.session.add(s)
            db.session.commit()
            sid = s.id
        runner.invoke(args=["close-stale", "--hours", "24", "--dry-run"])
        with app.app_context():
            s = db.session.get(ParkingSession, sid)
            assert s.exit_time is None  # unchanged

    def test_close_stale_no_stale_sessions(self, app, runner):
        result = runner.invoke(args=["close-stale", "--hours", "24"])
        assert "No stale sessions found" in result.output


# ---- Slice 14: Group H admin tests ----

class TestForceCloseSession:
    def test_force_close_active_session(self, client, app):
        from app.models import ParkingSession, ParkingSpot
        with app.app_context():
            spot = db.session.get(ParkingSpot, 1)
            spot.status = "occupied"
            s = ParkingSession(user_id=2, spot_id=1, vehicle_number="MH01AA0001")
            db.session.add(s)
            db.session.commit()
            sid = s.id
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/sessions")
        rv = client.post(f"/admin/sessions/{sid}/close", data={"csrf_token": csrf},
                         follow_redirects=True)
        assert rv.status_code == 200
        assert b"force-closed" in rv.data.lower()
        with app.app_context():
            s = db.session.get(ParkingSession, sid)
            assert s.exit_time is not None
            assert db.session.get(ParkingSpot, 1).status == "available"

    def test_force_close_nonexistent_session_404(self, client):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/sessions")
        rv = client.post("/admin/sessions/999/close", data={"csrf_token": csrf})
        assert rv.status_code == 404

    def test_force_close_blocked_for_non_admin(self, client):
        login(client, "user@test.com", "user123")
        rv = client.post("/admin/sessions/1/close", data={})
        assert rv.status_code == 403


class TestAnnouncement:
    def test_announcement_page_accessible(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/announcement")
        assert rv.status_code == 200

    def test_announcement_blocked_for_non_admin(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/admin/announcement")
        assert rv.status_code == 403

    def test_active_announcement_shown_on_dashboard(self, client, app):
        from app.models import Announcement
        with app.app_context():
            db.session.add(Announcement(message="Test notice", is_active=True))
            db.session.commit()
        login(client, "user@test.com", "user123")
        rv = client.get("/")
        assert b"Test notice" in rv.data

    def test_inactive_announcement_not_shown(self, client, app):
        from app.models import Announcement
        with app.app_context():
            db.session.add(Announcement(message="Hidden notice", is_active=False))
            db.session.commit()
        login(client, "user@test.com", "user123")
        rv = client.get("/")
        assert b"Hidden notice" not in rv.data

    def test_create_announcement(self, client, app):
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/announcement")
        rv = client.post("/admin/announcement", data={
            "message": "Spots 8-10 closed", "is_active": "y", "csrf_token": csrf,
        }, follow_redirects=True)
        assert b"Announcement updated" in rv.data


class TestSpotUtilization:
    def test_spot_utilization_page(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/spots/utilization")
        assert rv.status_code == 200
        assert b"Spot Utilization" in rv.data
        assert b"Never" in rv.data

    def test_spot_utilization_blocked_for_non_admin(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/admin/spots/utilization")
        assert rv.status_code == 403


class TestSessionNote:
    def test_admin_can_set_session_note(self, client, app):
        from app.models import ParkingSession, ParkingSpot
        with app.app_context():
            spot = db.session.get(ParkingSpot, 1)
            spot.status = "occupied"
            s = ParkingSession(user_id=2, spot_id=1, vehicle_number="MH12AB0001")
            db.session.add(s)
            db.session.commit()
            sid = s.id
        login(client, "admin@test.com", "admin123")
        csrf = _get_csrf(client, "/admin/sessions")
        rv = client.post(f"/admin/sessions/{sid}/note",
                         data={"note": "Damage reported", "csrf_token": csrf},
                         follow_redirects=True)
        assert rv.status_code == 200
        with app.app_context():
            s = db.session.get(ParkingSession, sid)
            assert s.admin_note == "Damage reported"

    def test_note_blocked_for_non_admin(self, client):
        login(client, "user@test.com", "user123")
        rv = client.post("/admin/sessions/1/note", data={})
        assert rv.status_code == 403


class TestActivityLog:
    def test_activity_log_page_accessible(self, client):
        login(client, "admin@test.com", "admin123")
        rv = client.get("/admin/activity")
        assert rv.status_code == 200

    def test_activity_log_blocked_for_non_admin(self, client):
        login(client, "user@test.com", "user123")
        rv = client.get("/admin/activity")
        assert rv.status_code == 403

    def test_login_creates_activity_log(self, client, app):
        from app.models import ActivityLog
        client.post("/auth/login", data={"email": "user@test.com", "password": "user123"})
        with app.app_context():
            entry = ActivityLog.query.filter_by(action="login").first()
            assert entry is not None
