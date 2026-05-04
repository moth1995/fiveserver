#Requires -Version 5.1
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# --- Checks ---
function Fail($msg) { Write-Error $msg; exit 1 }

if (-not (Get-Command go    -ErrorAction SilentlyContinue)) { Fail "Go is not installed. See https://go.dev/dl/" }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "Python 3 is not installed." }
if (-not (Get-Command mysql  -ErrorAction SilentlyContinue)) { Fail "MySQL client (mysql) is not in PATH." }

$goVer = (go version) -replace '.*go(\d+\.\d+).*','$1'
if ([version]$goVer -lt [version]"1.21") { Fail "Go 1.21+ required (found $goVer)." }

# --- Build Go server ---
Write-Host "Building fiveserver-go..."
Set-Location "$RepoRoot\fiveserver-go"
go build -o fiveserver.exe ./cmd/fiveserver
Write-Host "  Binary: fiveserver-go\fiveserver.exe"
Set-Location $RepoRoot

# --- Python venv + dependencies ---
Write-Host "Installing Flask dependencies..."
python -m venv flask-web\.venv
& flask-web\.venv\Scripts\pip install --quiet --upgrade pip
& flask-web\.venv\Scripts\pip install --quiet -r flask-web\requirements.txt
Write-Host "  Venv: flask-web\.venv"

# --- Database ---
Write-Host ""
Write-Host "Database setup"
Write-Host "--------------"
$DB_HOST      = Read-Host "MySQL host      [127.0.0.1]"; if (-not $DB_HOST) { $DB_HOST = "127.0.0.1" }
$DB_PORT      = Read-Host "MySQL port      [3306]";      if (-not $DB_PORT) { $DB_PORT = "3306" }
$DB_ROOT      = Read-Host "MySQL root user [root]";      if (-not $DB_ROOT) { $DB_ROOT = "root" }
$DB_ROOT_PASS = Read-Host "MySQL root password" -AsSecureString
$DB_ROOT_PLAIN = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($DB_ROOT_PASS))
$DB_NAME      = Read-Host "App DB name     [fiveserver]"; if (-not $DB_NAME) { $DB_NAME = "fiveserver" }
$DB_USER      = Read-Host "App DB user     [fiveserver]"; if (-not $DB_USER) { $DB_USER = "fiveserver" }
$DB_PASS_SS   = Read-Host "App DB password" -AsSecureString
$DB_PASS      = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($DB_PASS_SS))

$mysqlArgs = "-h$DB_HOST", "-P$DB_PORT", "-u$DB_ROOT", "-p$DB_ROOT_PLAIN"

Write-Host "Creating database and user..."
$setupSql = @"
CREATE DATABASE IF NOT EXISTS ``$DB_NAME``;
CREATE USER IF NOT EXISTS '$DB_USER'@'%' IDENTIFIED BY '$DB_PASS';
GRANT SELECT, INSERT, UPDATE ON ``$DB_NAME``.* TO '$DB_USER'@'%';
FLUSH PRIVILEGES;
"@
$setupSql | mysql @mysqlArgs

Write-Host "Applying schema..."
Get-Content sql\schema.sql | mysql @mysqlArgs $DB_NAME

$doMetrics = Read-Host "Apply metrics schema (match_rosters)? [y/N]"
if ($doMetrics -eq 'y') {
    Get-Content sql\metrics.sql | mysql @mysqlArgs $DB_NAME
    Write-Host "  metrics.sql applied."
}

# --- Config reminder ---
Write-Host ""
Write-Host "Update etc\conf\fiveserver.yaml with your DB credentials:"
Write-Host "  DB:"
Write-Host "    name: $DB_NAME"
Write-Host "    user: $DB_USER"
Write-Host "    password: <your password>"
Write-Host "    readServers: [$DB_HOST]"
Write-Host ""
Write-Host "Done. To start:"
Write-Host "  fiveserver-go\fiveserver.exe -config etc\conf\fiveserver.yaml -admin-config etc\conf\admin.yaml"
Write-Host "  flask-web\.venv\Scripts\python flask-web\run.py"
