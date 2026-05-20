.PHONY: setup db seed run start test prod help

help:
	@echo "Available targets:"
	@echo "  make setup   - Create venv and install dependencies"
	@echo "  make db      - Run database migrations"
	@echo "  make seed    - Seed admin user and pricing config"
	@echo "  make run     - Run development server"
	@echo "  make start   - One-shot: setup + db + seed + run"
	@echo "  make test    - Run pytest with coverage"
	@echo "  make prod    - Run production server with gunicorn"

setup:
	python3 -m venv venv
	. venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt
	[ ! -f .env ] && cp .env.example .env || true
	@echo "Setup complete. Run 'source venv/bin/activate' to activate the venv."

db:
	venv/bin/flask db upgrade

seed:
	venv/bin/flask seed-admin
	venv/bin/flask seed-pricing
	venv/bin/flask seed-spots 10

run:
	venv/bin/flask run

start: setup db seed run

test:
	venv/bin/pytest --cov=app

prod:
	venv/bin/gunicorn -w 1 -b 0.0.0.0:8000 "app:create_app()"
