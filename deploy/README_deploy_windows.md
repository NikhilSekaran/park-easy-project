# Deployment Guide — ParkEasy on AWS EC2 Windows Server

## Why Windows is different from the Ubuntu setup

| Concern | Ubuntu | Windows |
|---|---|---|
| WSGI server | Gunicorn | **Waitress** (Gunicorn is Unix-only) |
| Process manager | systemd | **NSSM** (Non-Sucking Service Manager) |
| Reverse proxy | Nginx (apt) | **Nginx for Windows** (Chocolatey) |
| Setup script | `setup.sh` (bash) | `setup_windows.ps1` (PowerShell) |
| Firewall | `ufw` | Windows Firewall / `netsh` |
| Paths | `/opt/park-easy` | your app extraction path |

> **Note:** `setup_windows.ps1` requires PowerShell 5.1+ (the default on Windows Server 2025 — no upgrade needed). Run it as Administrator.

---

## Prerequisites

- AWS account with EC2 access
- Razorpay account with **live-mode** API keys
- RDP access (port 3389) or Systems Manager Session Manager

---

## Step 1 — Launch EC2 (Windows)

1. Open the EC2 console → **Launch Instance**
2. **AMI**: Windows Server 2022 Base (64-bit x86)
3. **Instance type**: t3.small or larger (Windows needs more RAM than Ubuntu)
4. **Key pair**: select or create one; save the `.pem` file
5. **Security Group** — allow inbound:

   | Port | Protocol | Source |
   |---|---|---|
   | 3389 | RDP | Your IP only |
   | 80 | HTTP | 0.0.0.0/0 |
   | 443 | HTTPS | 0.0.0.0/0 |

6. Launch and note the **Public IPv4 address**

---

## Step 2 — Connect via RDP

1. EC2 Console → select instance → **Connect → RDP Client**
2. Click **Get Password** and decrypt it with your `.pem` file
3. Open Remote Desktop and connect with:
   - Computer: `<public-ip>`
   - Username: `Administrator`
   - Password: (decrypted above)

---

## Step 3 — Prepare `.env`

Create a `.env` file locally (never commit it):

```dotenv
FLASK_ENV=production
SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
RAZORPAY_KEY_ID=<live key id>
RAZORPAY_KEY_SECRET=<live key secret>
SQLALCHEMY_DATABASE_URI=          # blank = SQLite; or mssql+pyodbc://... for SQL Server
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=<strong password>
WAITRESS_THREADS=4                # optional, defaults to 4
```

---

## Step 4 — Upload `.env` to the instance

**Option A — RDP copy-paste**: open Notepad on the instance, paste contents, save to `C:\park-easy\.env`.

**Option B — PowerShell Remoting** (from your local machine):
```powershell
$session = New-PSSession -ComputerName <public-ip> -Credential Administrator
Copy-Item .\.env -Destination "C:\park-easy\.env" -ToSession $session
```

> The `.env` must be present **before** running `setup_windows.ps1`.

---

## Step 5 — Clone repo and run setup

Open **PowerShell as Administrator** on the instance and run:

```powershell
# Allow running local scripts
Set-ExecutionPolicy RemoteSigned -Scope LocalMachine -Force

# First-time deploy — provide the repo URL
cd C:\
git clone https://github.com/you/park-easy-project.git park-easy
cd C:\park-easy\deploy
.\setup_windows.ps1 -RepoUrl "https://github.com/you/park-easy-project.git"
```

`setup_windows.ps1` is **idempotent** — safe to re-run on every re-deploy. It will:

1. Install Chocolatey (if missing)
2. Install Python, Git, Nginx, NSSM via Chocolatey
3. Clone **or** `git pull` the repo
4. Create the Python virtualenv and install all dependencies (incl. `waitress`)
5. Run `flask db upgrade` (applies migrations)
6. Seed admin / pricing / spots (skipped if already present)
7. Register and start the `parking` Windows Service via NSSM
8. Configure and (re)start Nginx as a Windows Service
9. Open ports 80 and 443 in Windows Firewall

---

## Step 6 — Verify

```powershell
nssm status parking          # should show: SERVICE_RUNNING
Invoke-WebRequest http://localhost   # should return HTML
```

Open `http://<public-ip>` in a browser and log in with the admin credentials from `.env`.

---

## Re-deploy (code update)

```powershell
cd C:\park-easy\deploy
.\setup_windows.ps1
```

No `-RepoUrl` needed on subsequent runs — the script detects the existing repo and does `git pull`.

---

## Useful commands

```powershell
# Service management
nssm status parking
nssm start  parking
nssm stop   parking
nssm restart parking

# Live log tail
Get-Content C:\park-easy\logs\service_stderr.log -Wait -Tail 50

# Nginx
nginx -t -p C:\tools\nginx          # test config
Restart-Service nginx

# Manual production start (for debugging, outside the service)
cd C:\park-easy
venv\Scripts\python wsgi_windows.py
```

---

## HTTPS / SSL (optional)

1. Point a domain at the EC2 Elastic IP.
2. Install Certbot for Windows: `choco install certbot -y`
3. Run: `certbot --nginx -d yourdomain.com`
4. Certbot will update the Nginx config automatically.

---

## SQLite vs SQL Server

The default config uses SQLite (`instance/parking.db`), which is fine for low traffic.  
For production-grade storage, set `SQLALCHEMY_DATABASE_URI` in `.env` to a SQL Server connection string:

```
SQLALCHEMY_DATABASE_URI=mssql+pyodbc://user:pass@localhost/parking?driver=ODBC+Driver+17+for+SQL+Server
```

Install the driver: `choco install sqlserver-odbcdriver -y`

---

## Key files added for Windows

| File | Purpose |
|---|---|
| `wsgi_windows.py` | Entry point — loads `.env`, starts Waitress |
| `deploy/setup_windows.ps1` | Idempotent PowerShell provisioning script |
| `deploy/nginx_windows.conf` | Nginx reverse-proxy config (Windows paths) |
