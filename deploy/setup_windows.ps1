#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Idempotent provisioning script for ParkEasy on Windows Server / EC2 Windows.
    Safe to re-run on every re-deploy.

.PARAMETER RepoUrl
    Git URL of the repository.  Only required on the very first run.
    Example: https://github.com/you/park-easy-project.git

.PARAMETER AppDir
    Installation directory.  Defaults to C:\park-easy

.EXAMPLE
    .\setup_windows.ps1 -RepoUrl "https://github.com/you/park-easy-project.git"
    .\setup_windows.ps1   # subsequent re-deploys (no RepoUrl needed)
#>
param(
    [string]$RepoUrl = "",
    [string]$AppDir  = (Split-Path -Parent $PSScriptRoot),
    [string]$ServiceName = "parking"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) {
    Write-Host "`n=== $msg ===" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# Step 1 - Chocolatey
# ---------------------------------------------------------------------------
Write-Step "Step 1: Chocolatey package manager"
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Chocolatey..."
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    # Refresh PATH so 'choco' is available immediately
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
} else {
    Write-Host "Chocolatey already installed - skipping."
}

# ---------------------------------------------------------------------------
# Step 2 - System packages
# ---------------------------------------------------------------------------
Write-Step "Step 2: System packages (Python, Git, Nginx, NSSM)"
$packages = @("python", "git", "nginx", "nssm")
foreach ($pkg in $packages) {
    $installed = choco list --local-only $pkg 2>$null | Select-String "^$pkg "
    if ($installed) {
        Write-Host "$pkg already installed - skipping."
    } else {
        Write-Host "Installing $pkg..."
        choco install $pkg -y --no-progress
    }
}
# Refresh PATH after installs
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User")

# ---------------------------------------------------------------------------
# Step 3 - Clone or update repo
# ---------------------------------------------------------------------------
Write-Step "Step 3: Clone or update repository"
if (Test-Path (Join-Path $AppDir ".git")) {
    Write-Host "Repo exists - pulling latest..."
    git -C $AppDir pull
} else {
    if ([string]::IsNullOrWhiteSpace($RepoUrl)) {
        Write-Error "ERROR: -RepoUrl is required for the first run."
        exit 1
    }
    Write-Host "Cloning $RepoUrl ? $AppDir ..."
    git clone $RepoUrl $AppDir
}

# ---------------------------------------------------------------------------
# Step 4 - Python virtual environment & dependencies
# ---------------------------------------------------------------------------
Write-Step "Step 4: Python virtual environment"
$venvPython  = Join-Path $AppDir "venv\Scripts\python.exe"
$venvPip     = Join-Path $AppDir "venv\Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating venv..."
    python -m venv "$AppDir\venv"
}

Write-Host "Upgrading pip and installing dependencies..."
& $venvPip install --upgrade pip --quiet
& $venvPip install -r "$AppDir\requirements.txt" --quiet

# ---------------------------------------------------------------------------
# Step 5 - Verify .env
# ---------------------------------------------------------------------------
Write-Step "Step 5: Verify .env"
$envFile = Join-Path $AppDir ".env"
if (-not (Test-Path $envFile)) {
    # Copy example if it exists, then abort so the user can fill in secrets.
    $example = Join-Path $AppDir ".env.example"
    if (Test-Path $example) { Copy-Item $example $envFile }
    Write-Error @"
ERROR: $envFile not found.
Create it (copy from .env.example) and populate all secrets before re-running.
"@
    exit 1
}
Write-Host ".env found."

# ---------------------------------------------------------------------------
# Step 6 - Database migrations
# ---------------------------------------------------------------------------
Write-Step "Step 6: Database migrations"
$env:FLASK_APP = "run.py"
& $venvPython -m flask --app run:app db upgrade

# ---------------------------------------------------------------------------
# Step 7 - Seed admin & pricing (idempotent)
# ---------------------------------------------------------------------------
Write-Step "Step 7: Seed admin user and pricing config"

# Load ADMIN_EMAIL and ADMIN_PASSWORD from .env so seed-admin runs non-interactively
$envVars = Get-Content $envFile | Where-Object { $_ -match '^(ADMIN_EMAIL|ADMIN_PASSWORD)=' }
foreach ($line in $envVars) {
    $parts = $line -split '=', 2
    [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
}

& $venvPython -m flask --app run:app seed-admin
& $venvPython -m flask --app run:app seed-pricing
& $venvPython -m flask --app run:app seed-spots 10

# ---------------------------------------------------------------------------
# Step 8 - Windows Service via NSSM
# ---------------------------------------------------------------------------
Write-Step "Step 8: Windows Service ($ServiceName) via NSSM"
$nssmCmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
$nssmExe = if ($nssmCmd) { $nssmCmd.Source } else { "C:\ProgramData\chocolatey\bin\nssm.exe" }

$existing = & $nssmExe status $ServiceName 2>$null
if ($existing -match "SERVICE_") {
    Write-Host "Service already exists - stopping for update..."
    & $nssmExe stop $ServiceName confirm 2>$null
    & $nssmExe remove $ServiceName confirm
}

Write-Host "Registering service..."
& $nssmExe install $ServiceName $venvPython "$AppDir\wsgi_windows.py"
& $nssmExe set $ServiceName AppDirectory $AppDir
& $nssmExe set $ServiceName AppEnvironmentExtra "FLASK_ENV=production"
& $nssmExe set $ServiceName DisplayName "ParkEasy Flask Application"
& $nssmExe set $ServiceName Description  "ParkEasy parking management system (Waitress/Flask)"
& $nssmExe set $ServiceName Start SERVICE_AUTO_START
# Redirect stdout/stderr to log files
New-Item -ItemType Directory -Force "$AppDir\logs" | Out-Null
& $nssmExe set $ServiceName AppStdout "$AppDir\logs\service_stdout.log"
& $nssmExe set $ServiceName AppStderr "$AppDir\logs\service_stderr.log"
& $nssmExe set $ServiceName AppRotateFiles 1
& $nssmExe set $ServiceName AppRotateBytes 10485760   # 10 MB
& $nssmExe start $ServiceName

Write-Host "Service status:"
& $nssmExe status $ServiceName

# ---------------------------------------------------------------------------
# Step 9 - Nginx configuration
# ---------------------------------------------------------------------------
Write-Step "Step 9: Nginx reverse proxy"

# Auto-detect nginx root: Chocolatey installs to a versioned folder under C:\tools
$nginxRoot = (Get-ChildItem "C:\tools" -Directory -Filter "nginx*" -ErrorAction SilentlyContinue |
              Sort-Object Name -Descending | Select-Object -First 1).FullName
if (-not $nginxRoot) {
    Write-Error "ERROR: Could not find nginx installation under C:\tools. Check Chocolatey installed it correctly."
    exit 1
}
Write-Host "Nginx root: $nginxRoot"
$nginxExe  = Join-Path $nginxRoot "nginx.exe"
$sitesDir  = Join-Path $nginxRoot "conf\sites"
$siteConf  = Join-Path $sitesDir  "parking.conf"
$nginxConf = Join-Path $nginxRoot "conf\nginx.conf"

# Copy parking server block
New-Item -ItemType Directory -Force $sitesDir | Out-Null
Copy-Item "$AppDir\deploy\nginx_windows.conf" $siteConf -Force

# Replace nginx.conf entirely with a clean config that only includes our site.
# This avoids conflicts with the default "server_name localhost" block that
# Chocolatey ships and that would clash with our "server_name _" on port 80.
$cleanNginxConf = @"
worker_processes 1;
events { worker_connections 1024; }
http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 65;
    include sites/*.conf;
}
"@
Set-Content $nginxConf $cleanNginxConf -Encoding UTF8

# Validate config, then (re)start Nginx as a Windows service
& $nginxExe -t -p $nginxRoot
$nginxSvc = Get-Service -Name "nginx" -ErrorAction SilentlyContinue
if ($nginxSvc) {
    Restart-Service nginx
} else {
    # Register nginx as a service using NSSM
    & $nssmExe install nginx $nginxExe "-p `"$nginxRoot`""
    & $nssmExe set nginx AppDirectory $nginxRoot
    & $nssmExe set nginx Start SERVICE_AUTO_START
    Start-Service nginx
}
Write-Host "Nginx running."

# ---------------------------------------------------------------------------
# Step 10 - Windows Firewall
# ---------------------------------------------------------------------------
Write-Step "Step 10: Windows Firewall rules"
$rules = @(
    @{ Name="ParkEasy-HTTP";   Port=80;  Proto="TCP" },
    @{ Name="ParkEasy-HTTPS";  Port=443; Proto="TCP" }
)
foreach ($r in $rules) {
    $exists = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    if ($exists) {
        Write-Host "Firewall rule '$($r.Name)' already exists - skipping."
    } else {
        New-NetFirewallRule -DisplayName $r.Name -Direction Inbound `
            -Protocol $r.Proto -LocalPort $r.Port -Action Allow | Out-Null
        Write-Host "Added firewall rule: $($r.Name)"
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Green
Write-Host "ParkEasy is running at  http://<your-ec2-public-ip>"
Write-Host "Check service:  nssm status $ServiceName"
Write-Host "App logs:       $AppDir\logs\"
