"""Shared pytest fixtures for all tests."""
import pytest
from app import create_app
from config import TestingConfig
from app.extensions import db
from app.models import User, ParkingSpot, PricingConfig


@pytest.fixture
def app():
    """Create and configure a test app."""
    app = create_app(TestingConfig)

    with app.app_context():
        db.create_all()

        # Seed test data
        pricing = PricingConfig(hourly_rate=50.0)
        db.session.add(pricing)
        db.session.commit()

        admin = User(email="admin@test.com", role="admin")
        admin.set_password("admin123")
        db.session.add(admin)

        user = User(email="user@test.com", role="user")
        user.set_password("user123")
        db.session.add(user)
        db.session.commit()

        for i in range(1, 4):
            spot = ParkingSpot(spot_number=i, status="available")
            db.session.add(spot)
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Test client for making requests."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """CLI runner for testing CLI commands."""
    return app.test_cli_runner()
