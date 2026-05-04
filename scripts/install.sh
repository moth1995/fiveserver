#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# --- Checks ---
fail() { echo "Error: $*" >&2; exit 1; }

command -v go   &>/dev/null || fail "Go is not installed. See https://go.dev/dl/"
command -v python3 &>/dev/null || fail "Python 3 is not installed."
command -v mysql   &>/dev/null || fail "MySQL client (mysql) is not in PATH."

GO_VER=$(go version | awk '{print $3}' | sed 's/go//')
REQUIRED="1.21"
if [ "$(printf '%s\n' "$REQUIRED" "$GO_VER" | sort -V | head -1)" != "$REQUIRED" ]; then
    fail "Go $REQUIRED+ required (found $GO_VER)."
fi

# --- Build Go server ---
echo "Building fiveserver-go..."
cd "$REPO_ROOT/fiveserver-go"
go build -o fiveserver ./cmd/fiveserver
echo "  Binary: fiveserver-go/fiveserver"
cd "$REPO_ROOT"

# --- Python venv + dependencies ---
echo "Installing Flask dependencies..."
python3 -m venv flask-web/.venv
flask-web/.venv/bin/pip install --quiet --upgrade pip
flask-web/.venv/bin/pip install --quiet -r flask-web/requirements.txt
echo "  Venv: flask-web/.venv"

# --- Database ---
echo ""
echo "Database setup"
echo "--------------"
read -rp "MySQL host     [127.0.0.1]: " DB_HOST;  DB_HOST="${DB_HOST:-127.0.0.1}"
read -rp "MySQL port     [3306]:      " DB_PORT;  DB_PORT="${DB_PORT:-3306}"
read -rp "MySQL root user [root]:     " DB_ROOT;  DB_ROOT="${DB_ROOT:-root}"
read -rsp "MySQL root password: "                  DB_ROOT_PASS; echo ""
read -rp "App DB name    [fiveserver]: " DB_NAME;  DB_NAME="${DB_NAME:-fiveserver}"
read -rp "App DB user    [fiveserver]: " DB_USER;  DB_USER="${DB_USER:-fiveserver}"
read -rsp "App DB password: "                       DB_PASS;  echo ""

MYSQL_OPTS="-h$DB_HOST -P$DB_PORT -u$DB_ROOT -p$DB_ROOT_PASS"

echo "Creating database and user..."
mysql $MYSQL_OPTS <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\`;
CREATE USER IF NOT EXISTS '$DB_USER'@'%' IDENTIFIED BY '$DB_PASS';
GRANT SELECT, INSERT, UPDATE ON \`$DB_NAME\`.* TO '$DB_USER'@'%';
FLUSH PRIVILEGES;
SQL

echo "Applying schema..."
mysql $MYSQL_OPTS "$DB_NAME" < sql/schema.sql

read -rp "Apply metrics schema (match_rosters)? [y/N]: " DO_METRICS
if [[ "${DO_METRICS,,}" == "y" ]]; then
    mysql $MYSQL_OPTS "$DB_NAME" < sql/metrics.sql
    echo "  metrics.sql applied."
fi

# --- Config reminder ---
echo ""
echo "Update etc/conf/fiveserver.yaml with your DB credentials:"
echo "  DB:"
echo "    name: $DB_NAME"
echo "    user: $DB_USER"
echo "    password: <your password>"
echo "  readServers: [$DB_HOST]"
echo ""
echo "Done. To start:"
echo "  fiveserver-go/fiveserver -config etc/conf/fiveserver.yaml -admin-config etc/conf/admin.yaml"
echo "  flask-web/.venv/bin/python flask-web/run.py"
