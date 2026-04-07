Implement the admin blueprint in flask-web/blueprints/admin.py.

Read first:
- lib/fiveserver/admin.py    (all 20 endpoints — port the logic, not the XML output)
- flask-web/db.py            (query functions)
- flask-web/config.py        (AppConfig, save())
- etc/conf/admin.yaml        (AdminUser, AdminPassword, FiveserverLogFile)

Create branch `web-flask/step-06-admin-blueprint` from `web-flask` before making any changes.

## What to implement

`flask-web/blueprints/admin.py` — all admin endpoints behind BasicAuth.

```python
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
```

### Authentication

```python
def _require_auth() -> Response | None:
    """Check Authorization: Basic header. Return 401 response if missing/wrong, None if OK."""
```

Register as `@admin_bp.before_request`. Returns a `WWW-Authenticate: Basic realm="fiveserver"` 401 if credentials are absent or wrong. Credentials come from `current_app.config['ADMIN_USER']` and `current_app.config['ADMIN_PASSWORD']`.

### Endpoints (all return `render_template(...)`)

| Method | Route | Template | Notes |
|--------|-------|----------|-------|
| GET | `/` or `/home` | `admin/home.html` | Summary: user count, online count (from Go API), lobby list |
| GET | `/users` | `admin/users.html` | Paginated, `?offset=0&limit=50`, optional `?q=username` search |
| GET | `/users/<int:user_id>` | `admin/user_detail.html` | User + profiles |
| GET | `/profiles` | `admin/users.html` reused | Profiles list |
| GET | `/profiles/<profile_id>` | `admin/profile_detail.html` | Full stats |
| GET | `/stats` | `admin/home.html` reused | Same as home for now |
| GET | `/log` | `admin/log.html` | Read last N lines of log file, `?lines=30` |
| GET/POST | `/userlock` | `admin/confirm.html` | GET: form; POST: generate nonce, store, return recovery URL |
| GET/POST | `/userkill` | `admin/confirm.html` | GET: form; POST: soft delete user |
| GET/POST | `/maxusers` | `admin/settings.html` | GET: show; POST: update config + save() |
| GET/POST | `/debug` | `admin/settings.html` | Toggle debug flag in config + save() |
| GET/POST | `/settings` | `admin/settings.html` | Toggle StorePlayerSettings flag |
| GET/POST | `/roster` | `admin/settings.html` | Toggle roster hash enforcement flags |
| GET | `/banned` | `admin/banned.html` | List banned IPs/networks |
| GET/POST | `/ban-add` | `admin/banned.html` | POST: add IP/network to banned list + save() |
| GET/POST | `/ban-remove` | `admin/banned.html` | POST: remove from banned list + save() |
| GET/POST | `/server-ip` | `admin/confirm.html` | Show current ServerIP |
| GET | `/ps` | `admin/confirm.html` | Process info: PID, memory via psutil or /proc |

### Online users (Go internal API)

```python
def _get_online_users() -> list[dict[str, Any]]:
    """Call http://127.0.0.1:8199/internal/online-users with 1s timeout.
    Return empty list on any error."""
```

Use `urllib.request.urlopen` (stdlib only, no requests package).

### userlock nonce generation

Port from Twisted: `nonce = str(random.randint(1000,9999)) + str(random.random()) + ...`
Store in `users.reset_nonce`. Return the recovery URL: `https://<host>/modifyUser/<nonce>`.

## Register in create_app()

```python
from flask_web.blueprints.admin import admin_bp
app.register_blueprint(admin_bp)
app.config['ADMIN_USER'] = cfg['AdminUser']
app.config['ADMIN_PASSWORD'] = cfg['AdminPassword']
```

## Type annotations

`from __future__ import annotations` at top. All functions fully annotated.

## Unit tests

In `flask-web/tests/test_admin.py`, `unittest.TestCase`:
1. `test_no_auth_returns_401` — GET /admin/ without auth header → 401
2. `test_wrong_password_returns_401` — GET /admin/ with bad credentials → 401
3. `test_home_with_auth` — GET /admin/ with correct credentials → 200
4. `test_users_list` — GET /admin/users with auth → 200, mock DB returns user list
5. `test_userlock_post` — POST /admin/userlock with username → 200, verify nonce stored

## Verification

`python -m unittest flask-web/tests/test_admin.py` — all tests pass.

After verification, merge `web-flask/step-06-admin-blueprint` → `web-flask`.
