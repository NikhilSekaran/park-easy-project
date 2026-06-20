<powershell>
# =============================================================================
# ParkEasy -- EC2 Windows User Data Script
# Runs automatically on first boot of a new Windows Server EC2 instance.
#
# Prerequisites (one-time setup from your local machine):
#
#   1. Create .env file with your secrets, then store it in SSM:
#        aws ssm put-parameter --name "/parkeasy/env" --type "SecureString" `
#          --value (Get-Content .env -Raw) --overwrite
#
#   2. Create IAM Role:
#        IAM Console -> Roles -> Create Role -> EC2
#        Attach policy: AmazonSSMReadOnlyAccess
#        Also attach inline policy for KMS decrypt (if using SecureString):
#          { "Effect": "Allow", "Action": "ssm:GetParameter", "Resource": "arn:aws:ssm:*:*:parameter/parkeasy/*" }
#        Name: EC2-ParkEasy-SSM-Role
#
#   3. When launching EC2:
#        - Attach IAM Instance Profile: EC2-ParkEasy-SSM-Role
#        - Paste this entire script into Advanced Details -> User Data
#
# Monitor bootstrap progress:
#        Get-Content C:\userdata_bootstrap.log -Wait -Tail 30
# =============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"   # Don't abort on non-fatal errors (e.g. SSM unavailable)

# ---------------------------------------------------------------------------
# CONFIGURATION -- edit before use
# ---------------------------------------------------------------------------
$AppDir      = "C:\park-easy"
$RepoUrl     = "https://github.com/NikhilSekaran/park-easy-project.git"
$GitHubToken = ""    # Leave blank for public repos. Set token for private repos.
                     # Or store in SSM as /parkeasy/github_token and set IAM role.
$SsmEnvParam   = "/parkeasy/env"           # SSM parameter with full .env content (optional)
$SsmTokenParam = ""                        # SSM parameter for GitHub token; leave blank if public
$ServiceName   = "parking"
$LogFile       = "C:\userdata_bootstrap.log"

# ---------------------------------------------------------------------------
# INLINE .env -- PRIMARY: SSM Parameter Store (recommended, works with auto-scaling)
# Set $InlineEnv = "" to use SSM (requires IAM role with AmazonSSMReadOnlyAccess).
# Set $InlineEnv to a heredoc string only for quick one-off local testing.
# ---------------------------------------------------------------------------
$InlineEnv = ""   # SSM is the primary path -- do not put secrets here

# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------
function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Output $line
    Add-Content -Path $LogFile -Value $line
}

Write-Log "=== ParkEasy bootstrap started ==="

# ---------------------------------------------------------------------------
# Step 1 -- Execution policy
# ---------------------------------------------------------------------------
Write-Log "Step 1: Execution policy"
Set-ExecutionPolicy RemoteSigned -Scope LocalMachine -Force

# ---------------------------------------------------------------------------
# Step 2 -- Chocolatey
# ---------------------------------------------------------------------------
Write-Log "Step 2: Chocolatey"
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    [System.Net.ServicePointManager]::SecurityProtocol = 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
} else {
    Write-Log "Chocolatey already installed"
}

# ---------------------------------------------------------------------------
# Step 3 -- System packages
# ---------------------------------------------------------------------------
Write-Log "Step 3: System packages"
foreach ($pkg in @("python", "git", "nssm")) {
    # 'choco list --local-only' is deprecated in newer Chocolatey; use 'choco list' instead
    $installed = choco list $pkg --limit-output --local-only 2>$null | Select-String "^$pkg\|"
    if (-not $installed) {
        choco install $pkg -y --no-progress
    } else {
        Write-Log "$pkg already installed"
    }
}

# Refresh PATH
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User")

# ---------------------------------------------------------------------------
# Step 4 -- Clone repository
# ---------------------------------------------------------------------------
Write-Log "Step 4: Clone repository"

if (Test-Path (Join-Path $AppDir ".git")) {
    Write-Log "Repo exists -- pulling latest"
    git -C $AppDir pull
} else {
    $cloneUrl = $RepoUrl   # default: public repo, no token needed
    if (-not [string]::IsNullOrWhiteSpace($GitHubToken)) {
        # Token hardcoded in script (private repo)
        $cloneUrl = $RepoUrl -replace "https://", "https://$GitHubToken@"
        Write-Log "Using hardcoded GitHub token"
    } elseif (-not [string]::IsNullOrWhiteSpace($SsmTokenParam)) {
        # Try fetching token from SSM (private repo + SSM setup)
        $fetchedToken = (aws ssm get-parameter --name $SsmTokenParam --with-decryption --query "Parameter.Value" --output text 2>$null)
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($fetchedToken)) {
            $cloneUrl = $RepoUrl -replace "https://", "https://$fetchedToken@"
            Write-Log "Using GitHub token from SSM"
        } else {
            Write-Log "SSM token not available -- cloning as public repo"
        }
    } else {
        Write-Log "Public repo -- cloning without token"
    }
    Write-Log "Cloning repo to $AppDir"
    git clone $cloneUrl $AppDir
}

# ---------------------------------------------------------------------------
# Step 5 -- Pull .env from SSM Parameter Store
# ---------------------------------------------------------------------------
Write-Log "Step 5: Write .env"
$envFile = Join-Path $AppDir ".env"
if (-not (Test-Path $envFile)) {
    if (-not [string]::IsNullOrWhiteSpace($InlineEnv)) {
        # Path A: inline .env defined in this script (public repo / quick test)
        [System.IO.File]::WriteAllText($envFile, $InlineEnv.Trim(), [System.Text.UTF8Encoding]::new($false))
        Write-Log ".env written from inline config"
    } elseif (-not [string]::IsNullOrWhiteSpace($SsmEnvParam)) {
        # Path B: fetch .env from SSM Parameter Store (requires IAM role with SSMReadOnly)
        $envContent = aws ssm get-parameter --name $SsmEnvParam --with-decryption --query "Parameter.Value" --output text 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($envContent)) {
            [System.IO.File]::WriteAllText($envFile, $envContent, [System.Text.UTF8Encoding]::new($false))
            Write-Log ".env written from SSM"
        } else {
            Write-Log "ERROR: .env not found and SSM fetch failed. Set InlineEnv or configure SSM."
            exit 1
        }
    } else {
        Write-Log "ERROR: .env not found. Set InlineEnv in this script or configure SsmEnvParam."
        exit 1
    }
} else {
    Write-Log ".env already exists -- skipping"
}

# ---------------------------------------------------------------------------
# Step 6 -- Python virtual environment and dependencies
# ---------------------------------------------------------------------------
Write-Log "Step 6: Python venv and dependencies"
$venvPython = "$AppDir\venv\Scripts\python.exe"
$venvPip    = "$AppDir\venv\Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    python -m venv "$AppDir\venv"
}
& $venvPip install --upgrade pip --quiet
& $venvPip install -r "$AppDir\requirements.txt" --quiet

# ---------------------------------------------------------------------------
# Step 7 -- Database migrations and seed
# ---------------------------------------------------------------------------
Write-Log "Step 7: DB migrations and seed"
$env:FLASK_APP = "run.py"

# Load ADMIN_EMAIL and ADMIN_PASSWORD from .env so seed-admin runs non-interactively
$envVars = Get-Content (Join-Path $AppDir ".env") | Where-Object { $_ -match '^(ADMIN_EMAIL|ADMIN_PASSWORD)=' }
foreach ($line in $envVars) {
    $parts = $line -split '=', 2
    [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
}

& $venvPython -m flask --app run:app db upgrade
& $venvPython -m flask --app run:app seed-admin
& $venvPython -m flask --app run:app seed-pricing
& $venvPython -m flask --app run:app seed-spots 10

# ---------------------------------------------------------------------------
# Step 8 -- Register parking Windows Service via NSSM
# ---------------------------------------------------------------------------
Write-Log "Step 8: NSSM parking service"
$nssmCmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
$nssmExe = if ($nssmCmd) { $nssmCmd.Source } else { "C:\ProgramData\chocolatey\bin\nssm.exe" }

$existing = & $nssmExe status $ServiceName 2>$null
if ($existing -match "SERVICE_") {
    Write-Log "Service exists -- removing for fresh install"
    & $nssmExe stop $ServiceName confirm 2>$null
    & $nssmExe remove $ServiceName confirm
}

New-Item -ItemType Directory -Force "$AppDir\logs" | Out-Null
& $nssmExe install $ServiceName $venvPython "$AppDir\wsgi_windows.py"
& $nssmExe set $ServiceName AppDirectory      $AppDir
& $nssmExe set $ServiceName DisplayName       "ParkEasy Flask Application"
& $nssmExe set $ServiceName Start             SERVICE_AUTO_START
& $nssmExe set $ServiceName AppStdout         "$AppDir\logs\stdout.log"
& $nssmExe set $ServiceName AppStderr         "$AppDir\logs\stderr.log"
& $nssmExe set $ServiceName AppRotateFiles    1
& $nssmExe set $ServiceName AppRotateBytes    10485760
& $nssmExe start $ServiceName
Write-Log "Parking service status: $(& $nssmExe status $ServiceName)"

# ---------------------------------------------------------------------------
# Step 9 -- Nginx configuration
# ---------------------------------------------------------------------------
Write-Log "Step 9: Nginx"

# Install nginx via Chocolatey if not already present
$nginxInstalled = choco list nginx --limit-output --local-only 2>$null | Select-String "^nginx\|"
if (-not $nginxInstalled) {
    choco install nginx -y --no-progress
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
}

# Find nginx root dynamically (Chocolatey installs versioned folder under C:\tools)
$nginxExePath = (Get-ChildItem "C:\tools" -Filter "nginx.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
if (-not $nginxExePath) {
    Write-Log "ERROR: nginx.exe not found under C:\tools -- check Chocolatey install"
    exit 1
}
$nginxRoot = Split-Path $nginxExePath -Parent
Write-Log "Nginx root: $nginxRoot"

# Copy parking.conf and replace nginx.conf with a clean config
New-Item -ItemType Directory -Force "$nginxRoot\conf\sites" | Out-Null
Copy-Item "$AppDir\deploy\nginx_windows.conf" "$nginxRoot\conf\sites\parking.conf" -Force

# Replace nginx.conf entirely to avoid conflicts with the default server block
# Use WriteAllText with no-BOM UTF8 -- Set-Content -Encoding UTF8 adds a BOM which nginx rejects
$nginxConf = "$nginxRoot\conf\nginx.conf"
$nginxContent = "worker_processes 1;`nevents { worker_connections 1024; }`nhttp {`n    include       mime.types;`n    default_type  application/octet-stream;`n    sendfile      on;`n    keepalive_timeout 65;`n    include sites/*.conf;`n}`n"
[System.IO.File]::WriteAllText($nginxConf, $nginxContent, [System.Text.UTF8Encoding]::new($false))
Write-Log "nginx.conf replaced with clean config (no BOM)"

# Register nginx as a Windows Service via NSSM
$nginxSvcStatus = & $nssmExe status nginx 2>$null
if ($nginxSvcStatus -match "SERVICE_") {
    & $nssmExe stop nginx confirm 2>$null
    & $nssmExe remove nginx confirm
}
& $nssmExe install nginx $nginxExePath "-p `"$nginxRoot`""
& $nssmExe set nginx AppDirectory $nginxRoot
& $nssmExe set nginx Start SERVICE_AUTO_START
& $nssmExe start nginx
Write-Log "Nginx service status: $(& $nssmExe status nginx)"

# ---------------------------------------------------------------------------
# Step 10 -- Windows Firewall
# ---------------------------------------------------------------------------
Write-Log "Step 10: Firewall rules"
foreach ($rule in @(
    @{Name="ParkEasy-HTTP";  Port=80},
    @{Name="ParkEasy-HTTPS"; Port=443}
)) {
    $exists = netsh advfirewall firewall show rule name=$($rule.Name) 2>$null
    if ($exists -notmatch "Rule Name") {
        netsh advfirewall firewall add rule name=$($rule.Name) dir=in action=allow protocol=TCP localport=$($rule.Port)
        Write-Log "Firewall rule $($rule.Name) added"
    } else {
        Write-Log "Firewall rule $($rule.Name) already exists"
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Log "=== Bootstrap complete ==="
Write-Log "Parking service: $(& $nssmExe status $ServiceName)"
Write-Log "Nginx service:   $(& $nssmExe status nginx)"
Write-Log "Log file: $LogFile"
</powershell>
