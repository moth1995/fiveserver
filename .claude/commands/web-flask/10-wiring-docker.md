Wire the Flask app entry point and create the Dockerfile.

Read first:
- tac/fiveserver.tac          (reference for how Twisted wired things)
- fiveserver-go/Dockerfile    (reference for Dockerfile style)
- flask-web/app.py            (create_app() factory)
- etc/conf/fiveserver.yaml    (port config keys)
- etc/conf/admin.yaml         (AdminPort, KeysDirectory)

Create branch `web-flask/step-10-wiring-docker` from `web-flask` before making any changes.

## flask-web/run.py

Start two Werkzeug servers from a single process:

1. **HTTP server** on port 80 (configurable via env `HTTP_PORT`):
   - A minimal Flask app that redirects every request to `https://`
   - Uses `301 Moved Permanently`

2. **HTTPS server** on port 443 (configurable via env `HTTPS_PORT`):
   - The full app from `create_app()`
   - `ssl_context=(cert_path, key_path)` where paths come from `etc/conf/admin.yaml` `KeysDirectory`

Use `werkzeug.serving.make_server` + `threading.Thread` for both servers:

```python
from __future__ import annotations
import threading
import os
from werkzeug.serving import make_server
from flask import Flask, redirect
from flask_web.app import create_app

def run() -> None:
    http_port: int = int(os.environ.get('HTTP_PORT', '80'))
    https_port: int = int(os.environ.get('HTTPS_PORT', '443'))
    # ...
```

Config/cert paths must be resolved relative to the repo root (not flask-web/).
Use `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` as the repo root.

## flask-web/app.py (complete create_app)

Finalize `create_app()` to:
1. Load config via `load_config('etc/conf/fiveserver.yaml', 'etc/conf/admin.yaml')`
2. Set `app.secret_key` from config or a generated default
3. Store in `app.config`: `ADMIN_USER`, `ADMIN_PASSWORD`, `BANNED_LIST`, `FS_CONFIG`
4. Register all three blueprints: `register_bp`, `admin_bp`, `stats_bp`
5. Register `db.teardown_db` via `app.teardown_appcontext`

## flask-web/Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install deps first (layer cache)
COPY flask-web/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy app and config
COPY flask-web/ ./flask-web/
COPY etc/ ./etc/
COPY web/ ./web/

EXPOSE 80 443

ENV HTTP_PORT=80
ENV HTTPS_PORT=443

CMD ["python", "flask-web/run.py"]
```

Note: The Dockerfile is in the repo root context (`flask-web/Dockerfile`). Build with:
`docker build -f flask-web/Dockerfile -t fiveserver-web .`

## Environment variable overrides

Both `run.py` and `create_app()` should check these env vars (override YAML):
- `HTTP_PORT` — HTTP redirect port (default: 80)
- `HTTPS_PORT` — HTTPS port (default: 443)
- `DB_HOST` — override DB host
- `DB_PORT` — override DB port
- `DB_NAME` — override DB name
- `DB_USER` — override DB user
- `DB_PASSWORD` — override DB password
- `ADMIN_USER` — override admin username
- `ADMIN_PASSWORD` — override admin password

## Verification

1. `python -m unittest discover flask-web/tests/` — all tests pass
2. `python flask-web/run.py` — app starts (ignore SSL errors for self-signed cert)
3. `curl -k https://localhost/` — registration form HTML returned
4. `curl http://localhost/ -v` — 301 redirect to https://
5. `docker build -f flask-web/Dockerfile -t fiveserver-web . && docker run -p 80:80 -p 443:443 fiveserver-web` — container starts

After verification, merge `web-flask/step-10-wiring-docker` → `web-flask`.
