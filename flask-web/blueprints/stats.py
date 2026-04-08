"""Stats blueprint: metrics dashboard. Same BasicAuth as admin. Read-only."""
from __future__ import annotations

import base64
import calendar
from datetime import date, datetime, timedelta
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
# Mock data — replace with real DB queries once sql/metrics.sql is applied
# ---------------------------------------------------------------------------

def _mock_matches_per_day(year: int, month: int) -> list[dict[str, Any]]:
    days_in_month = calendar.monthrange(year, month)[1]
    import random
    rng = random.Random(year * 100 + month)
    return [
        {'day': d, 'count': rng.randint(0, 48)}
        for d in range(1, days_in_month + 1)
    ]


def _mock_top_teams() -> list[dict[str, Any]]:
    return [
        {'team_id': 5,  'name': 'Brazil',    'count': 312},
        {'team_id': 1,  'name': 'Argentina', 'count': 287},
        {'team_id': 12, 'name': 'Italy',     'count': 241},
        {'team_id': 7,  'name': 'England',   'count': 198},
        {'team_id': 3,  'name': 'France',    'count': 175},
        {'team_id': 9,  'name': 'Germany',   'count': 154},
        {'team_id': 21, 'name': 'Portugal',  'count': 132},
        {'team_id': 14, 'name': 'Spain',     'count': 119},
        {'team_id': 6,  'name': 'Netherlands','count': 98},
        {'team_id': 18, 'name': 'Croatia',   'count': 87},
    ]


def _mock_top_rosters() -> list[dict[str, Any]]:
    return [
        {'hash': 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4', 'count': 204},
        {'hash': 'deadbeefdeadbeefdeadbeefdeadbeef', 'count': 177},
        {'hash': 'cafebabecafebabecafebabecafebabe', 'count': 143},
        {'hash': '0102030405060708090a0b0c0d0e0f10', 'count': 98},
        {'hash': 'fffefdfcfbfaf9f8f7f6f5f4f3f2f1f0', 'count': 71},
    ]


def _mock_summary(year: int, month: int, date_from: date, date_to: date) -> dict[str, Any]:
    import random
    rng = random.Random(year * 100 + month)
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
    summary = _mock_summary(year, month, date_from, date_to)

    # Build month options for the selector (last 12 months)
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
