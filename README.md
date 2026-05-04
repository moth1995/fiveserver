# Fiveserver

Reverse-engineered online multiplayer server for **PES5 / WE9 / WE9LE** and **PES6 / WE2007** (via "sixserver"). Speaks a custom binary TCP protocol over XOR-encrypted streams, backed by MySQL.

Copyright (C) 2011–2021 juce and reddwarf — BSD-style license.

---

## Features

- Full network play for PES5/WE9/WE9LE, including 2-vs-2 for PES6
- Persistent accounts: up to 3 profiles per user, match history, win streaks, points/ranking
- Division-restricted lobbies (A / 3B / 3A / 2 / 1), open lobbies, no-stats lobbies
- Disconnect-as-loss penalty (configurable)
- Live config reload without restart
- Admin HTTP API: broadcast chat, kick players, view/reload config, online user list
- Flask web UI: user/profile browser, ban management, settings editor, stats dashboard
- User registration and password recovery via web form

---

## Architecture

```
Game clients
    │
    ├─ Port 16001/18001/19501 → NewsProtocol       (lobby list, server time, greeting)
    ├─ Port 20102/20103/20104 → LoginService       (auth, profiles, match history)
    ├─ Port 20101             → NetworkMenuService (menus, chat, challenge)
    └─ Port 20100             → MainService        (team selection, match logic)

Web clients
    ├─ HTTP :80 / HTTPS :443  → Flask app          (registration, admin UI, stats)
    └─ HTTP AdminPort         → Go admin API       (broadcast, kick, config)
```

### Protocol

All TCP traffic is XOR-encrypted with the rolling 4-byte key `\xa6\x77\x95\x7c`. Packet layout:

```
[2 bytes]  packet ID       big-endian uint16
[2 bytes]  data length     big-endian uint16
[4 bytes]  packet counter  big-endian uint32
[16 bytes] MD5 checksum    over header + data
[N bytes]  data
```

Heartbeat packet `0x0005` is echoed back by the base handler.

---

## Repository Layout

```
fiveserver/
├── fiveserver-go/          Go socket server
│   ├── cmd/fiveserver/     Entry point
│   └── internal/
│       ├── admin/          HTTP admin API
│       ├── config/         YAML loader + live reload
│       ├── crypto/         XOR stream cipher + Blowfish
│       ├── db/             MySQL query layer
│       ├── model/          Structs (user, lobby, profile, match)
│       ├── protocol/       Packet handlers (news, login, menu, main)
│       └── server/         Generic TCP listener
├── flask-web/              Python Flask web layer
│   ├── blueprints/         register, admin, stats
│   ├── templates/          Jinja2 HTML templates
│   └── static/             CSS + md5.js
├── etc/conf/               YAML config files
│   ├── fiveserver.yaml     PES5/WE9/WE9LE
│   ├── sixserver.yaml      PES6/WE2007
│   ├── admin.yaml          Admin UI + Go admin API (PES5)
│   └── admin6.yaml         Admin UI (PES6)
├── sql/
│   ├── schema.sql          PES5 schema
│   ├── schema6.sql         PES6 schema
│   └── metrics.sql         match_rosters table (optional)
├── scripts/
│   ├── install.sh          Linux/macOS quick-start
│   └── install.ps1         Windows quick-start
├── docker-compose.yml
└── .env.example
```

---

## Prerequisites

### From source

- **Go 1.21+**
- **Python 3.12+** and pip
- **MySQL 5.7+** or MariaDB

### Docker Compose

- Docker Engine 24+ with Compose v2 (`docker compose version`)

---

## Quick Start — From Source

The install scripts build the Go binary, create a Python venv, and set up the database interactively.

**Linux / macOS:**

```bash
bash scripts/install.sh
```

**Windows (PowerShell):**

```powershell
.\scripts\install.ps1
```

---

## Quick Start — Docker Compose

```bash
cp .env.example .env
# Edit .env — set MYSQL_ROOT_PASSWORD and DB_PASSWORD at minimum
docker compose up --build -d
```

```
docker compose logs -f   # follow logs
docker compose down      # stop all services
```

---

## Database Setup (manual)

```sql
-- PES5 / WE9 / WE9LE
CREATE DATABASE fiveserver;
CREATE USER 'fiveserver'@'%' IDENTIFIED BY 'your_password';
GRANT SELECT, INSERT, UPDATE ON fiveserver.* TO 'fiveserver'@'%';
USE fiveserver;
SOURCE sql/schema.sql;
-- optional match-roster metrics:
SOURCE sql/metrics.sql;

-- PES6 / WE2007
CREATE DATABASE sixserver;
CREATE USER 'sixserver'@'%' IDENTIFIED BY 'your_password';
GRANT SELECT, INSERT, UPDATE ON sixserver.* TO 'sixserver'@'%';
USE sixserver;
SOURCE sql/schema6.sql;
```

> **Important:** Change all passwords. The default credentials in the config files are for local development only.

---

## Running Manually

### Go socket server

```bash
cd fiveserver-go
go build -o fiveserver ./cmd/fiveserver
./fiveserver -config ../etc/conf/fiveserver.yaml -admin-config ../etc/conf/admin.yaml
```

### Flask web layer

```bash
cd flask-web
pip install -r requirements.txt
python run.py
```

By default Flask runs on plain HTTP (port 80). To enable TLS set `useSecure: true` in `etc/conf/fiveserver.yaml` and place `servercert.pem` / `serverkey.pem` in `etc/keys/`. See the TLS section below.

---

## TLS

1. Place your certificate and key under `etc/keys/`:
   ```
   etc/keys/servercert.pem
   etc/keys/serverkey.pem
   ```
2. Set `useSecure: true` in `etc/conf/fiveserver.yaml`:
   ```yaml
   WebInterface:
     useSecure: true
     port: 80        # HTTP → HTTPS redirect
     securePort: 443 # HTTPS listener
   ```
3. Optionally override the paths with env vars `CERT_FILE` and `KEY_FILE`.

Without cert/key the server logs a warning and falls back to plain HTTP (development only).

---

## Configuration

### `etc/conf/fiveserver.yaml`

| Key | Description |
|---|---|
| `ServerIP` | `auto` to detect public IP, or an explicit address |
| `DB.name` / `DB.readServers` | MySQL database name and host |
| `DB.user` / `DB.password` | MySQL credentials — **change these** |
| `Lobbies` | List of lobby definitions (name, type, division) |
| `Debug` | Verbose packet logging |
| `Disconnects.CountAsLoss` | Penalize disconnects as losses |
| `WebInterface.useSecure` / `port` / `securePort` | HTTP/HTTPS settings |

### `etc/conf/admin.yaml`

| Key | Description |
|---|---|
| `AdminPort` | Port for the Go admin HTTP API (bind to 127.0.0.1) |
| `AdminUser` / `AdminPassword` | Flask admin UI credentials |
| `WebserverLogFile` | Flask log output path |
| `FiveserverLogFile` | Go server log output path |

Config is reloaded live without restart via `POST /admin/reload-config`.

---

## Admin API (Go server)

> **Security note:** The admin API has no authentication. Bind `AdminPort` to `127.0.0.1` or firewall it — `GET /admin/config` returns DB credentials in plaintext.

| Method | Path | Description |
|---|---|---|
| `POST` | `/admin/broadcast` | Send a chat message to all players |
| `POST` | `/admin/kick` | Kick a player by session ID |
| `GET`  | `/admin/config` | Return current config (includes DB credentials) |
| `POST` | `/admin/reload-config` | Reload config from disk |
| `GET`  | `/admin/users` | List online users |
| `GET`  | `/admin/lobbies` | Lobby + room stats |

---

## Known Limitations

- The Go server only supports the PES5 DB schema. PES6 (`sixserver`) uses a different `matches` layout and is not yet supported by the Go DB layer.
- `friends` and `blocked` tables exist in the schema but are not used by any application code.
- `go.mod` declares `go 1.25.0` which does not exist; builds fine with Go 1.21+.
