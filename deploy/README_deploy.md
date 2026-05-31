# Deployment Guide — ParkEasy on AWS EC2

## Prerequisites

- AWS account with EC2 access
- Razorpay account with **live-mode** API keys
- SSH key pair

---

## Step 1 — Launch EC2

1. Open the EC2 console → **Launch Instance**
2. **AMI**: Ubuntu Server 22.04 LTS (64-bit x86)
3. **Instance type**: t3.micro (free tier eligible)
4. **Key pair**: select or create one; save the `.pem` file
5. **Security Group** — allow inbound:
   | Port | Protocol | Source |
   |---|---|---|
   | 22 | SSH | Your IP only |
   | 80 | HTTP | 0.0.0.0/0 |
   | 443 | HTTPS | 0.0.0.0/0 |
6. Launch and note the **Public IPv4 address**

---

## Step 2 — Prepare `.env`

Create a `.env` file on your local machine (never commit it):

```dotenv
FLASK_ENV=production
SECRET_KEY=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
RAZORPAY_KEY_ID=<live key id from Razorpay dashboard>
RAZORPAY_KEY_SECRET=<live key secret>
SQLALCHEMY_DATABASE_URI=          # leave blank for SQLite; set postgresql:// for PostgreSQL
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=<strong password>
```

---

## Step 3 — Upload `.env` to EC2

```bash
scp -i <key.pem> .env ubuntu@<public-ip>:/opt/park-easy/.env
```

> The `.env` file must be in place **before** running `setup.sh`. It is never committed to git.

---

## Step 4 — Connect and Run Setup

```bash
ssh -i <key.pem> ubuntu@<public-ip>

# On the EC2 instance:
sudo mkdir -p /opt/park-easy
sudo chown ubuntu:ubuntu /opt/park-easy

# Clone the repo (first time):
export REPO_URL="https://github.com/you/park-easy-project.git"
cd /opt/park-easy/..
git clone $REPO_URL park-easy   # or: cd /opt/park-easy && git clone ...

# Run the setup script:
cd /opt/park-easy/deploy
chmod +x setup.sh
sudo REPO_URL=$REPO_URL ./setup.sh
```

`setup.sh` is **idempotent** — safe to re-run on every re-deploy. It will:
- Install Nginx, Python 3, git, make
- Clone or `git pull` the repo
- Create the Python virtualenv and install dependencies
- Run `flask db upgrade` (applies any new migrations)
- Seed the admin user (skips if already exists)
- Install and start the `parking` systemd service
- Configure and reload Nginx
- Enable the UFW firewall

---

## Step 5 — Verify

```bash
systemctl status parking   # should show: active (running)
curl http://localhost      # should return HTML login page
```

Open `http://<public-ip>` in a browser. Log in with the admin credentials from `.env`.

---

## Re-deploy (code update)

```bash
ssh -i <key.pem> ubuntu@<public-ip>
cd /opt/park-easy/deploy
sudo ./setup.sh    # git pull + migrate + restart service
```

---

## Migrating to PostgreSQL

The app is designed so the SQLite → PostgreSQL migration is a **2-line config change** — no model or query changes required.

### Step 1 — Add the PostgreSQL driver

```bash
pip install psycopg2-binary   # or add to requirements.txt and redeploy
```

Or add it permanently:
```
# requirements.txt
psycopg2-binary==2.9.10
```

### Step 2 — Provision a PostgreSQL database

**Option A — On the same EC2 (quickest for a demo):**
```bash
sudo apt-get install -y postgresql
sudo -u postgres psql -c "CREATE USER parking WITH PASSWORD 'yourpassword';"
sudo -u postgres psql -c "CREATE DATABASE parking OWNER parking;"
```

**Option B — AWS RDS (production-grade):**
1. Open RDS console → Create database → PostgreSQL
2. Instance: `db.t3.micro` (free tier eligible)
3. Note the endpoint hostname, username, and password

### Step 3 — Update `.env`

```dotenv
SQLALCHEMY_DATABASE_URI=postgresql://parking:yourpassword@localhost/parking
# For RDS:
# SQLALCHEMY_DATABASE_URI=postgresql://user:pass@rds-endpoint.amazonaws.com:5432/parking
```

### Step 4 — Run migrations

```bash
venv/bin/flask db upgrade   # Alembic works identically on PostgreSQL
```

### Step 5 — Increase Gunicorn workers

Edit `Makefile` — change `-w 1` to `-w 4` (safe with PostgreSQL, unsafe with SQLite):

```makefile
prod:
    venv/bin/gunicorn -w 4 -b 0.0.0.0:8000 "app:create_app()"
```

Then restart the service: `sudo systemctl restart parking`

> **Why `Numeric(10,2)` matters here**: all fee and rate fields in the models use `db.Numeric(10,2)` instead of `Float`. PostgreSQL is strict about numeric types — `Float` would cause precision loss or type errors. This was done in Slice 1 specifically to make this migration safe.

---

## Horizontal scaling (multiple EC2 instances + ALB)

Once on PostgreSQL, adding a second instance is straightforward:

1. **Launch a second EC2** and run `setup.sh` — same steps as the first instance
2. **Point both at the same PostgreSQL** — set the same `SQLALCHEMY_DATABASE_URI` in `.env` on both
3. **Same `SECRET_KEY` on all instances** — Flask session cookies are HMAC-signed; all instances must share the key so any instance can validate any session cookie
4. **Create an Application Load Balancer (ALB)** in AWS → add both EC2 instances as targets on port 8000
5. **No sticky sessions needed** — all state lives in the DB; any instance can serve any request

```
Internet → ALB → EC2-1 (Gunicorn -w 4)  ┐
                                          ├── PostgreSQL (RDS)
               → EC2-2 (Gunicorn -w 4)  ┘
```

---

## Optional: Add TLS (HTTPS)

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

Certbot auto-renews certificates and rewrites `nginx.conf` to redirect HTTP → HTTPS.
