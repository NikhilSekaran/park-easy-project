<powershell>
# =============================================================================
# ParkEasy -- EC2 Windows User Data Script
# Runs automatically on first boot of a new Windows Server EC2 instance.
#
# Prerequisites (set up once before launching the instance):
#   1. Store the .env content in SSM Parameter Store:
#        aws ssm put-parameter --name "/parkeasy/env" --type "SecureString" --value (Get-Content .env -Raw) --overwrite
#   2. Attach an IAM role to the EC2 instance with AmazonSSMReadOnlyAccess
#   3. Set GITHUB_TOKEN below (or store in SSM as /parkeasy/github_token)
#
# Usage -- launch EC2 with this script as User Data:
#   $userData = Get-Content "deploy\userdata_windows.ps1" -Raw
#   $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($userData))
#   aws ec2 run-instances --image-id <ami-id> --instance-type t3.small \
#     --key-name <key-pair> --security-group-ids <sg-id> \
#     --iam-instance-profile Name=<profile-with-ssm-access> \
#     --user-data $b64
# =============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# CONFIGURATION -- edit before use
# ---------------------------------------------------------------------------
$AppDir     = "C:\park-easy"
$RepoUrl    = "https://github.com/yourusername/park-easy-project.git"   # replace
$GitHubToken = ""   # set token here OR leave blank to fetch from SSM /parkeasy/github_token
$SsmEnvParam = "/parkeasy/env"           # SSM parameter holding the full .env content
$SsmTokenParam = "/parkeasy/github_token"  # SSM parameter for GitHub token (optional)
$ServiceName = "parking"
$LogFile    = "C:\userdata_bootstrap.log"

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
    $installed = choco list --local-only $pkg 2>$null | Select-String "^$pkg "
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

# Fetch GitHub token from SSM if not hardcoded
if ([string]::IsNullOrWhiteSpace($GitHubToken)) {
    Write-Log "Fetching GitHub token from SSM $SsmTokenParam"
    $GitHubToken = (aws ssm get-parameter --name $SsmTokenParam --with-decryption --query "Parameter.Value" --output text 2>$null)
}

if (Test-Path (Join-Path $AppDir ".git")) {
    Write-Log "Repo exists -- pulling latest"
    git -C $AppDir pull
} else {
    $cloneUrl = $RepoUrl -replace "https://", "https://$GitHubToken@"
    Write-Log "Cloning repo to $AppDir"
    git clone $cloneUrl $AppDir
}

# ---------------------------------------------------------------------------
# Step 5 -- Pull .env from SSM Parameter Store
# ---------------------------------------------------------------------------
Write-Log "Step 5: Pull .env from SSM"
$envFile = Join-Path $AppDir ".env"
if (-not (Test-Path $envFile)) {
    $envContent = aws ssm get-parameter --name $SsmEnvParam --with-decryption --query "Parameter.Value" --output text
    [System.IO.File]::WriteAllText($envFile, $envContent, [System.Text.UTF8Encoding]::new($false))
    Write-Log ".env written from SSM"
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
$nginxInstalled = choco list --local-only nginx 2>$null | Select-String "^nginx "
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

# Copy parking.conf
New-Item -ItemType Directory -Force "$nginxRoot\conf\sites" | Out-Null
Copy-Item "$AppDir\deploy\nginx_windows.conf" "$nginxRoot\conf\sites\parking.conf" -Force

# Inject include line into nginx.conf if missing
$nginxConf = "$nginxRoot\conf\nginx.conf"
$content = Get-Content $nginxConf -Raw
if ($content -notmatch "include sites/") {
    $content = $content -replace "(http\s*\{)", "`$1`n    include sites/*.conf;"
    Set-Content $nginxConf $content -Encoding ASCII
    Write-Log "Added include sites/*.conf to nginx.conf"
}

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
