"""Stats blueprint: metrics dashboard. Same BasicAuth as admin. Read-only."""
from __future__ import annotations

import base64
import calendar
from datetime import date, timedelta
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    make_response,
    render_template,
    request,
)

stats_bp = Blueprint('stats', __name__, url_prefix='/stats')

# ---------------------------------------------------------------------------
# Authentication (mirrors admin)
# ---------------------------------------------------------------------------


def _require_auth() -> Response | None:
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
# Placeholder data — replace with real DB queries after sql/metrics.sql applied
# ---------------------------------------------------------------------------

def _mock_matches_per_day(year: int, month: int) -> list[dict[str, Any]]:
    import random
    days_in_month = calendar.monthrange(year, month)[1]
    rng = random.Random(year * 100 + month)
    return [{'day': d, 'count': rng.randint(0, 48)} for d in range(1, days_in_month + 1)]


def _mock_top_teams() -> list[dict[str, Any]]:
    return [
        {'team_id': 5,  'count': 312},
        {'team_id': 1,  'count': 287},
        {'team_id': 12, 'count': 241},
        {'team_id': 7,  'count': 198},
        {'team_id': 3,  'count': 175},
        {'team_id': 9,  'count': 154},
        {'team_id': 21, 'count': 132},
        {'team_id': 14, 'count': 119},
        {'team_id': 6,  'count': 98},
        {'team_id': 18, 'count': 87},
    ]


def _mock_top_rosters() -> list[dict[str, Any]]:
    return [
        {'hash': 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4', 'count': 204},
        {'hash': 'deadbeefdeadbeefdeadbeefdeadbeef', 'count': 177},
        {'hash': 'cafebabecafebabecafebabecafebabe', 'count': 143},
    ]


def _mock_summary(date_from: date, date_to: date) -> dict[str, Any]:
    import random
    rng = random.Random(date_from.toordinal())
    days = max(1, (date_to - date_from).days + 1)
    return {
        'total_matches': rng.randint(800, 1400),
        'active_users': rng.randint(40, 120) * days // 30,
        'new_users': rng.randint(10, 60),
        'avg_goals_per_match': round(rng.uniform(1.8, 3.2), 1),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _parse_filters() -> tuple[int, int, date, date]:
    """Return (year, month, date_from, date_to) from request args."""
    today = date.today()
    month_str: str = request.args.get('month', today.strftime('%Y-%m'))
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
    except (ValueError, IndexError):
        year, month = today.year, today.month

    days_in_month = calendar.monthrange(year, month)[1]
    default_from = date(year, month, 1)
    default_to = date(year, month, days_in_month)

    try:
        date_from = date.fromisoformat(request.args.get('from', ''))
    except ValueError:
        date_from = default_from
    try:
        date_to = date.fromisoformat(request.args.get('to', ''))
    except ValueError:
        date_to = default_to

    return year, month, date_from, date_to


@stats_bp.route('/')
@stats_bp.route('/home')
def home() -> str:
    year, month, date_from, date_to = _parse_filters()
    month_label = date(year, month, 1).strftime('%B %Y')

    matches_per_day = _mock_matches_per_day(year, month)
    max_daily = max((d['count'] for d in matches_per_day), default=1) or 1
    top_teams = _mock_top_teams()
    top_rosters = _mock_top_rosters()
    summary = _mock_summary(date_from, date_to)

    month_options: list[dict[str, str]] = []
    cursor = date.today().replace(day=1)
    for _ in range(12):
        month_options.append({
            'value': cursor.strftime('%Y-%m'),
            'label': cursor.strftime('%B %Y'),
        })
        cursor = (cursor - timedelta(days=1)).replace(day=1)

    return render_template(
        'stats/home.html',
        month_label=month_label,
        selected_month=date(year, month, 1).strftime('%Y-%m'),
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        month_options=month_options,
        summary=summary,
        matches_per_day=matches_per_day,
        max_daily=max_daily,
        top_teams=top_teams,
        top_rosters=top_rosters,
    )
