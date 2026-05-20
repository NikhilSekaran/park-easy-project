#!/usr/bin/env python
"""Entry point for the Flask application."""
import os
from app import create_app
from app.extensions import db

if __name__ == "__main__":
    app = create_app()

    with app.app_context():
        # Create tables if they don't exist (for initial setup; migrations handle schema)
        db.create_all()

    app.run(debug=True)
