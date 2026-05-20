import os
from datetime import datetime, timedelta

import click
from flask import current_app
from app.extensions import db
from app.models import User, ParkingSpot, PricingConfig


def register_cli_commands(app):
    """Register custom Flask CLI commands."""

    @app.cli.command("seed-admin")
    def seed_admin():
        """Create an admin user if it doesn't already exist."""
        email = os.environ.get("ADMIN_EMAIL") or click.prompt("Admin email")
        password = os.environ.get("ADMIN_PASSWORD") or click.prompt(
            "Admin password", hide_input=True
        )

        if User.query.filter_by(email=email).first():
            click.echo("Admin already exists.")
            return

        admin = User(email=email, role="admin")
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        click.echo(f"Admin user created: {email}")

    @app.cli.command("seed-spots")
    @click.argument("count", type=int, default=10)
    def seed_spots(count):
        """Create parking spots if they don't already exist."""
        existing_count = ParkingSpot.query.count()
        spots_to_create = count - existing_count

        if spots_to_create <= 0:
            click.echo(f"Spots already exist (total: {existing_count}).")
            return

        for i in range(existing_count + 1, existing_count + spots_to_create + 1):
            spot = ParkingSpot(spot_number=i, status="available")
            db.session.add(spot)

        db.session.commit()
        click.echo(f"Created {spots_to_create} parking spots (total: {count}).")

    @app.cli.command("seed-pricing")
    @click.option("--rate", type=float, default=50.0, help="Hourly rate in currency units")
    def seed_pricing(rate):
        """Create the single PricingConfig row if it doesn't exist."""
        if PricingConfig.query.first():
            click.echo("Pricing config already exists.")
            return

        config = PricingConfig(hourly_rate=rate)
        db.session.add(config)
        db.session.commit()
        click.echo(f"Pricing config created: hourly_rate={rate}")

    @app.cli.command("close-stale")
    @click.option("--hours", default=24, type=int,
                  help="Close sessions open longer than this many hours (default: 24).")
    @click.option("--dry-run", is_flag=True, help="Print what would be closed without committing.")
    def close_stale(hours, dry_run):
        """G9: Close parking sessions open longer than HOURS hours."""
        from app.models import ParkingSession
        from app.utils import calculate_fee
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        stale = ParkingSession.query.filter(
            ParkingSession.exit_time == None,
            ParkingSession.entry_time <= cutoff,
        ).all()
        if not stale:
            click.echo("No stale sessions found.")
            return
        pricing = PricingConfig.get_or_create()
        now = datetime.utcnow()
        for s in stale:
            fee = calculate_fee(s.entry_time, now, pricing.hourly_rate)
            click.echo(f"  Session {s.id} — {s.vehicle_number} — {s.entry_time} — fee ₹{fee}")
            if not dry_run:
                s.exit_time = now
                s.fee = fee
                if s.spot:
                    s.spot.status = "available"
        if not dry_run:
            db.session.commit()
            click.echo(f"Closed {len(stale)} session(s).")
        else:
            click.echo(f"[dry-run] Would close {len(stale)} session(s). No changes made.")
