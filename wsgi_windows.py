"""
Windows production entry point.
Loads .env then starts Waitress (replaces Gunicorn, which is Unix-only).

Usage:
    venv\Scripts\python wsgi_windows.py
"""
import os
from dotenv import load_dotenv

# Load .env before creating the app so config.py picks up every variable.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from waitress import serve          # noqa: E402
from app import create_app          # noqa: E402

if __name__ == "__main__":
    host = os.getenv("WAITRESS_HOST", "0.0.0.0")
    port = int(os.getenv("WAITRESS_PORT", "8000"))
    threads = int(os.getenv("WAITRESS_THREADS", "4"))

    app = create_app()
    print(f"Starting Waitress on {host}:{port} with {threads} threads …")
    serve(app, host=host, port=port, threads=threads)
