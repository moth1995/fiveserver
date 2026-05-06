"""Stats blueprint: metrics dashboard. Shares admin session auth."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from db import (
    get_db,
    stats_matches_per_day,
    stats_top_teams,
    stats_top_rosters,
    stats_summary,
)

stats_bp = Blueprint("stats", __name__, url_prefix="/stats")

# ---------------------------------------------------------------------------
# Authentication — shared with admin (same session key)
# ---------------------------------------------------------------------------


@stats_bp.before_request
def check_auth():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin.login", next=request.path))
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _parse_filters() -> tuple[int, int, date, date]:
    """Return (year, month, date_from, date_to) from request args."""
    today = date.today()
    month_str: str = request.args.get("month", today.strftime("%Y-%m"))
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
    except (ValueError, IndexError):
        year, month = today.year, today.month

    days_in_month = calendar.monthrange(year, month)[1]
    default_from = date(year, month, 1)
    default_to = date(year, month, days_in_month)

    try:
        date_from = date.fromisoformat(request.args.get("from", ""))
    except ValueError:
        date_from = default_from
    try:
        date_to = date.fromisoformat(request.args.get("to", ""))
    except ValueError:
        date_to = default_to

    return year, month, date_from, date_to


@stats_bp.route("/")
@stats_bp.route("/home")
def home() -> str:
    year, month, date_from, date_to = _parse_filters()
    month_label = date(year, month, 1).strftime("%B %Y")

    conn = get_db()
    matches_per_day: list[dict[str, Any]] = stats_matches_per_day(conn, year, month)
    max_daily: int = max((d["count"] for d in matches_per_day), default=1) or 1
    top_teams: list[dict[str, Any]] = stats_top_teams(conn, date_from, date_to)
    top_rosters: list[dict[str, Any]] = stats_top_rosters(conn, date_from, date_to)
    summary: dict[str, Any] = stats_summary(conn, date_from, date_to)

    month_options: list[dict[str, str]] = []
    cursor = date.today().replace(day=1)
    for _ in range(12):
        month_options.append(
            {
                "value": cursor.strftime("%Y-%m"),
                "label": cursor.strftime("%B %Y"),
            }
        )
        cursor = (cursor - timedelta(days=1)).replace(day=1)

    return render_template(
        "stats/home.html",
        month_label=month_label,
        selected_month=date(year, month, 1).strftime("%Y-%m"),
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        month_options=month_options,
        summary=summary,
        matches_per_day=matches_per_day,
        max_daily=max_daily,
        top_teams=top_teams,
        top_rosters=top_rosters,
    )
