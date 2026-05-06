"""Stats blueprint: metrics dashboard. Shares admin session auth."""

from __future__ import annotations

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
    stats_top_online_users,
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
# Preset definitions
# ---------------------------------------------------------------------------

_PRESETS: list[dict[str, str]] = [
    {"value": "today", "label": "Today"},
    {"value": "yesterday", "label": "Yesterday"},
    {"value": "last7", "label": "Last 7 days"},
    {"value": "last30", "label": "Last 30 days"},
    {"value": "this_month", "label": "This month"},
    {"value": "last_month", "label": "Last month"},
    {"value": "last3months", "label": "Last 3 months"},
    {"value": "last_year", "label": "Last year"},
    {"value": "custom", "label": "Custom range"},
]


def _preset_range(preset: str) -> tuple[date, date]:
    """Return (date_from, date_to) for a named preset."""
    today = date.today()
    if preset == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if preset == "last7":
        return today - timedelta(days=6), today
    if preset == "last30":
        return today - timedelta(days=29), today
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if preset == "last3months":
        return today - timedelta(days=89), today
    if preset == "last_year":
        return today - timedelta(days=364), today
    # default: today
    return today, today


# ---------------------------------------------------------------------------
# Filter parsing
# ---------------------------------------------------------------------------


def _parse_filters() -> tuple[str, date, date]:
    """Return (preset, date_from, date_to) from request args."""
    today = date.today()
    preset = request.args.get("preset", "today")
    valid_presets = {p["value"] for p in _PRESETS}
    if preset not in valid_presets:
        preset = "today"

    if preset == "custom":
        try:
            date_from = date.fromisoformat(request.args.get("from", ""))
        except ValueError:
            date_from = today
        try:
            date_to = date.fromisoformat(request.args.get("to", ""))
        except ValueError:
            date_to = today
        if date_from > date_to:
            date_from, date_to = date_to, date_from
    else:
        date_from, date_to = _preset_range(preset)

    return preset, date_from, date_to


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@stats_bp.route("/")
@stats_bp.route("/home")
def home() -> str:
    preset, date_from, date_to = _parse_filters()

    conn = get_db()
    matches_per_day: list[dict[str, Any]] = stats_matches_per_day(
        conn, date_from, date_to
    )
    max_daily: int = max((d["count"] for d in matches_per_day), default=1) or 1
    top_teams: list[dict[str, Any]] = stats_top_teams(conn, date_from, date_to)
    top_rosters: list[dict[str, Any]] = stats_top_rosters(conn, date_from, date_to)
    summary: dict[str, Any] = stats_summary(conn, date_from, date_to)
    top_online: list[dict[str, Any]] = stats_top_online_users(conn)

    # Enrich top_online with formatted hours
    for u in top_online:
        secs = u.get("total_online_seconds", 0) or 0
        u["hours"] = round(secs / 3600, 1)

    return render_template(
        "stats/home.html",
        preset=preset,
        presets=_PRESETS,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        summary=summary,
        matches_per_day=matches_per_day,
        max_daily=max_daily,
        top_teams=top_teams,
        top_rosters=top_rosters,
        top_online=top_online,
    )
