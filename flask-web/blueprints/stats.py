"""Stats blueprint: read-only server statistics, same BasicAuth as admin."""
from __future__ import annotations

import base64
import json
import urllib.request
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    make_response,
    render_template,
    request,
)

from db import browse_users, get_db

stats_bp = Blueprint('stats', __name__, url_prefix='/stats')

# ---------------------------------------------------------------------------
# Authentication (same check as admin — shared helper)
# ---------------------------------------------------------------------------


def _require_auth() -> Response | None:
    """Check Authorization: Basic header. Returns 401 response if missing/wrong."""
    auth_header: str | None = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Basic '):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            username, _, password = decoded.partition(':')
            if (username == current_app.config['ADMIN_USER'] and
                    password == current_app.config['ADMIN_PASSWORD']):
                return None
        except Exception:
            pass
    resp: Response = make_response('Unauthorized', 401)
    resp.headers['WWW-Authenticate'] = 'Basic realm="fiveserver"'
    return resp


@stats_bp.before_request
def check_auth() -> Response | None:
    return _require_auth()


# ---------------------------------------------------------------------------
# Go internal API helper
# ---------------------------------------------------------------------------


def _get_online_users() -> list[dict[str, Any]]:
    """Call Go server's internal API. Returns [] on any error."""
    try:
        with urllib.request.urlopen(
                'http://127.0.0.1:8199/internal/online-users', timeout=1) as resp:
            data: dict[str, Any] = json.loads(resp.read())
            return data.get('users', [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@stats_bp.route('/')
@stats_bp.route('/home')
def home() -> str:
    online_users: list[dict[str, Any]] = _get_online_users()
    cfg = current_app.config['FS_CONFIG']
    lobbies: list[Any] = cfg.get('Lobbies', [])
    return render_template(
        'stats/home.html',
        online_users=online_users,
        lobbies=lobbies,
    )


@stats_bp.route('/users')
def users() -> str:
    conn = get_db()
    query: str = request.args.get('q', '').strip()
    offset: int = int(request.args.get('offset', 0))
    limit: int = int(request.args.get('limit', 50))
    total, user_list = browse_users(conn, offset=offset, limit=limit, search=query)
    return render_template(
        'stats/users.html',
        users=user_list,
        total=total,
        offset=offset,
        limit=limit,
        query=query,
    )
