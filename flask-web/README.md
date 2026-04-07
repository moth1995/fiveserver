# fiveserver-web

Flask-based web layer for fiveserver (PES5 / WE9 / WE9LE).

Provides:
- `/` — player registration form
- `/admin/*` — admin panel (HTTP Basic Auth)
- `/stats/*` — read-only server stats (same credentials as admin)

---

## Requirements

- Python 3.11+
- MySQL (same instance used by the Go game server)
- Existing `etc/conf/fiveserver.yaml` and `etc/conf/admin.yaml` (already present in this repo)
- TLS certificate + key at `etc/keys/servercert.pem` and `etc/keys/serverkey.pem` (required for HTTPS)

---

## Without Docker

### 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r flask-web/requirements.txt
```

### 3. Configure

Edit `etc/conf/fiveserver.yaml` to set your DB credentials and server IP.

Edit `etc/conf/admin.yaml` to set the admin username and password.

### 4. Run

From the **repo root** (so that `etc/` paths resolve correctly):

```bash
python flask-web/run.py
```

This starts:
- HTTP on port **80** — redirects all traffic to HTTPS
- HTTPS on port **443** — full application with TLS

#### Environment variable overrides

| Variable | Default | Description |
|---|---|---|
| `HTTP_PORT` | `80` | Plain-HTTP redirect port |
| `HTTPS_PORT` | `443` | HTTPS application port |
| `CERT_FILE` | `etc/keys/servercert.pem` | Path to TLS certificate |
| `KEY_FILE` | `etc/keys/serverkey.pem` | Path to TLS private key |
| `ADMIN_USER` | value in `admin.yaml` | Admin panel username |
| `ADMIN_PASSWORD` | value in `admin.yaml` | Admin panel password |
| `FLASK_SECRET` | random on startup | Flask session secret key |
| `DB_HOST` | value in `fiveserver.yaml` | MySQL host override |
| `DB_USER` | value in `fiveserver.yaml` | MySQL user override |
| `DB_PASSWORD` | value in `fiveserver.yaml` | MySQL password override |

Example with overrides:

```bash
HTTP_PORT=8080 HTTPS_PORT=8443 ADMIN_PASSWORD=changeme python flask-web/run.py
```

---

## With Docker

### 1. Build the image

Run from the **repo root** (the Dockerfile copies both `flask-web/` and `etc/`):

```bash
docker build -f flask-web/Dockerfile -t fiveserver-web .
```

### 2. Run the container

```bash
docker run -d \
  --name fiveserver-web \
  -p 80:80 \
  -p 443:443 \
  -v "$(pwd)/etc:/app/../etc" \
  fiveserver-web
```

The `-v` mount lets you keep your config and TLS keys outside the image and update them without rebuilding.

#### Passing environment variables

```bash
docker run -d \
  --name fiveserver-web \
  -p 80:80 \
  -p 443:443 \
  -v "$(pwd)/etc:/app/../etc" \
  -e ADMIN_USER=admin \
  -e ADMIN_PASSWORD=changeme \
  -e DB_HOST=192.168.1.10 \
  fiveserver-web
```

### 3. View logs

```bash
docker logs -f fiveserver-web
```

### 4. Stop / remove

```bash
docker stop fiveserver-web && docker rm fiveserver-web
```

---

## Running tests

From the repo root:

```bash
python -m unittest discover -s flask-web/tests
```
