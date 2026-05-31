#!/usr/bin/env bash
# Idempotent provisioning script — safe to re-run on re-deploy.
# Run as root on a fresh Ubuntu 22.04 EC2 instance.
set -euo pipefail

APP_DIR="/opt/park-easy"
REPO_URL="${REPO_URL:-}"   # set via env or edit below: REPO_URL="https://github.com/you/park-easy-project.git"
SERVICE_NAME="parking"

echo "=== Step 1: System packages ==="
apt-get update -qq
apt-get install -y nginx python3-venv python3-pip git make

echo "=== Step 2: Clone or update repo ==="
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull
else
    [ -z "$REPO_URL" ] && { echo "ERROR: REPO_URL is not set"; exit 1; }
    git clone "$REPO_URL" "$APP_DIR"
fi

echo "=== Step 3: Python environment ==="
cd "$APP_DIR"
make setup  # creates venv, pip install -r requirements.txt, copies .env.example → .env if missing

echo "=== Step 4: Verify .env exists ==="
[ ! -f "$APP_DIR/.env" ] && {
    echo "ERROR: $APP_DIR/.env not found."
    echo "Upload it first:  scp .env ubuntu@<ip>:$APP_DIR/.env"
    exit 1
}

echo "=== Step 5: Database migrations ==="
make db   # flask db upgrade — idempotent

echo "=== Step 6: Seed admin (skipped if already exists) ==="
make seed  # reads ADMIN_EMAIL / ADMIN_PASSWORD from .env; idempotent

echo "=== Step 7: systemd service ==="
cp "$APP_DIR/deploy/parking.service" /etc/systemd/system/parking.service
systemctl daemon-reload
systemctl enable parking
systemctl restart parking

echo "=== Step 8: Nginx ==="
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/parking
ln -sf /etc/nginx/sites-available/parking /etc/nginx/sites-enabled/parking
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo "=== Step 9: Firewall ==="
ufw allow 'Nginx Full'
ufw allow OpenSSH
ufw --force enable

echo ""
echo "=== Done! ==="
echo "App is running at http://$(curl -s ifconfig.me)"
echo "Check service: systemctl status $SERVICE_NAME"
