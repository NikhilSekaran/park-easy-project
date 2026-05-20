"""Tests for database models."""
import pytest
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from app.models import User, ParkingSpot, ParkingSession, PricingConfig
from app.extensions import db


class TestUserModel:
    def test_user_creation(self, app):
        """Test creating a user."""
        with app.app_context():
            user = User(email="test@example.com", role="user")
            user.set_password("password123")
            db.session.add(user)
            db.session.commit()

            fetched = User.query.filter_by(email="test@example.com").first()
            assert fetched is not None
            assert fetched.email == "test@example.com"
            assert fetched.role == "user"
            assert user.check_password("password123")

    def test_password_hashing(self, app):
        """Test that passwords are hashed, not stored as plaintext."""
        with app.app_context():
            user = User(email="hash@test.com", role="user")
            user.set_password("secret")

            assert user.password_hash != "secret"
            assert user.check_password("secret")
            assert not user.check_password("wrong")

    def test_is_admin(self, app):
        """Test admin role check."""
        with app.app_context():
            admin = User(email="admin@test.com", role="admin")
            user = User(email="user@test.com", role="user")

            assert admin.is_admin()
            assert not user.is_admin()

    def test_user_repr(self, app):
        """Test user string representation."""
        with app.app_context():
            user = User(email="repr@test.com")
            assert "repr@test.com" in repr(user)


class TestParkingSpotModel:
    def test_spot_creation(self, app):
        """Test creating a parking spot."""
        with app.app_context():
            spot = ParkingSpot(spot_number=42, status="available")
            db.session.add(spot)
            db.session.commit()

            fetched = ParkingSpot.query.filter_by(spot_number=42).first()
            assert fetched is not None
            assert fetched.spot_number == 42
            assert fetched.status == "available"

    def test_spot_unique_number(self, app):
        """Test spot_number uniqueness constraint."""
        with app.app_context():
            # Use a spot number not in conftest fixtures
            spot1 = ParkingSpot(spot_number=999, status="available")
            spot2 = ParkingSpot(spot_number=999, status="available")

            db.session.add(spot1)
            db.session.commit()

            db.session.add(spot2)
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_spot_repr(self, app):
        """Test spot string representation."""
        with app.app_context():
            spot = ParkingSpot(spot_number=10, status="occupied")
            assert "10" in repr(spot)
            assert "occupied" in repr(spot)


class TestParkingSessionModel:
    def test_session_creation(self, app):
        """Test creating a parking session."""
        with app.app_context():
            user = User.query.filter_by(email="user@test.com").first()
            spot = ParkingSpot.query.first()

            session = ParkingSession(
                user_id=user.id,
                spot_id=spot.id,
                vehicle_number="ABC123",
                entry_time=datetime.utcnow(),
                paid=False,
            )
            db.session.add(session)
            db.session.commit()

            fetched = ParkingSession.query.first()
            assert fetched.vehicle_number == "ABC123"
            assert fetched.paid is False
            assert fetched.user_id == user.id
            assert fetched.spot_id == spot.id

    def test_session_relationships(self, app):
        """Test session relationships with user and spot."""
        with app.app_context():
            user = User.query.filter_by(email="user@test.com").first()
            spot = ParkingSpot.query.first()

            session = ParkingSession(
                user_id=user.id,
                spot_id=spot.id,
                vehicle_number="XYZ789",
            )
            db.session.add(session)
            db.session.commit()

            fetched = ParkingSession.query.first()
            assert fetched.user == user
            assert fetched.spot == spot

    def test_session_fee_numeric(self, app):
        """Test that fee is a Numeric field (supports decimal precision)."""
        with app.app_context():
            user = User.query.filter_by(email="user@test.com").first()
            spot = ParkingSpot.query.first()

            session = ParkingSession(
                user_id=user.id,
                spot_id=spot.id,
                vehicle_number="DEC123",
                fee=99.99,
                paid=True,
            )
            db.session.add(session)
            db.session.commit()

            fetched = ParkingSession.query.filter_by(vehicle_number="DEC123").first()
            assert float(fetched.fee) == 99.99


class TestPricingConfigModel:
    def test_pricing_creation(self, app):
        """Test creating pricing config."""
        with app.app_context():
            config = PricingConfig(hourly_rate=75.50)
            db.session.add(config)
            db.session.commit()

            # Fetch the most recently created (highest ID)
            fetched = PricingConfig.query.order_by(PricingConfig.id.desc()).first()
            assert float(fetched.hourly_rate) == 75.50

    def test_pricing_get_current(self, app):
        """Test get_current class method."""
        with app.app_context():
            config = PricingConfig.get_current()
            assert config is not None
            assert float(config.hourly_rate) == 50.0  # From conftest seed

    def test_pricing_numeric_precision(self, app):
        """Test that hourly_rate supports decimal precision."""
        with app.app_context():
            config = PricingConfig(hourly_rate=123.45)
            db.session.add(config)
            db.session.commit()

            fetched = PricingConfig.query.order_by(PricingConfig.id.desc()).first()
            assert float(fetched.hourly_rate) == 123.45


