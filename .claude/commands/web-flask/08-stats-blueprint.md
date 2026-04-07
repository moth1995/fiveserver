Implement the stats blueprint in flask-web/blueprints/stats.py.

Read first:
- flask-web/blueprints/admin.py    (reuse the BasicAuth helper)
- flask-web/db.py                  (query functions to reuse)

Create branch `web-flask/step-08-stats-blueprint` from `web-flask` before making any changes.

## What to implement

`flask-web/blueprints/stats.py` — read-only stats under `/stats`, same BasicAuth as admin.

```python
stats_bp = Blueprint('stats', __name__, url_prefix='/stats')
```

### Authentication

Import and reuse `_require_auth` from `flask_web.blueprints.admin`. Register as `@stats_bp.before_request`.

### Endpoints (all read-only, no DB writes)

| Method | Route | Template | Notes |
|--------|-------|----------|-------|
| GET | `/` or `/home` | `stats/home.html` | Lobby occupancy + online user list from Go API |
| GET | `/users` | `stats/users.html` | Read-only user list, same query as admin, no action buttons |
| GET | `/users/online` | `stats/home.html` | Online users only (from Go internal API) |
| GET | `/profiles` | `stats/users.html` | Read-only profile list |
| GET | `/profiles/<profile_id>` | `admin/profile_detail.html` | Reuse admin template, pass `readonly=True` |

### Online users

Call `_get_online_users()` from `flask_web.blueprints.admin` — import and reuse, do not duplicate.

## Register in create_app()

```python
from flask_web.blueprints.stats import stats_bp
app.register_blueprint(stats_bp)
```

## Type annotations

`from __future__ import annotations` at top. All functions fully annotated.

## Unit tests

In `flask-web/tests/test_stats.py`, `unittest.TestCase`:
1. `test_no_auth_returns_401` — GET /stats/ without auth → 401
2. `test_stats_home_with_auth` — GET /stats/ with auth → 200
3. `test_users_readonly` — GET /stats/users with auth → 200, response contains no "Delete" or "Lock" buttons

## Verification

`python -m unittest flask-web/tests/test_stats.py` — all tests pass.

After verification, merge `web-flask/step-08-stats-blueprint` → `web-flask`.
