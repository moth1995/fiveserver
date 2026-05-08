"""Public-facing website blueprint.

Includes all public routes (home, rankings, profiles, matches, downloads, about)
plus the registration and password-recovery routes (ported from the old register.py).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import captcha as _captcha
from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

from config import is_banned
from crypto import blowfish_encrypt
from db import (
    count_leaderboard,
    count_public_matches,
    create_user,
    find_user_by_nonce,
    find_user_by_username,
    get_db,
    get_leaderboard,
    get_profile_with_stats,
    get_public_matches,
    record_user_registration,
    search_profiles,
    update_user,
)

public_bp = Blueprint("public", __name__)

# ---------------------------------------------------------------------------
# Division map — mirrors Go server: divMap{"A":0,"3B":1,"3A":2,"2":3,"1":4}
# ---------------------------------------------------------------------------

_DIVISION_MAP: dict[int, str] = {0: "A", 1: "3B", 2: "3A", 3: "2", 4: "1"}
_DIVISION_LABEL_TO_INT: dict[str, int] = {
    v.lower(): k for k, v in _DIVISION_MAP.items()
}


def _division(division_int: int) -> str:
    return _DIVISION_MAP.get(int(division_int), "A")


def _division_filter(div_param: str) -> int | None:
    """Convert URL div param (e.g. '3a', '1', 'a') to division int, or None if invalid."""
    return _DIVISION_LABEL_TO_INT.get(div_param.strip().lower())


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

_VALID_PAGE_SIZES: frozenset[int] = frozenset({10, 25, 50, 100})


def _page_size(req: Any) -> int:
    try:
        n = int(req.args.get("per_page", 25))
        return n if n in _VALID_PAGE_SIZES else 25
    except (TypeError, ValueError):
        return 25


def _sort_params(
    req: Any, default_col: str, default_dir: str = "desc"
) -> tuple[str, str]:
    col = req.args.get("sort", default_col)
    direction = req.args.get("dir", default_dir)
    return col, "asc" if direction == "asc" else "desc"


def _total_pages(total: int, per_page: int) -> int:
    return max(1, -(-total // per_page))


# ---------------------------------------------------------------------------
# Go admin API helper  (mirrors pattern in admin.py)
# ---------------------------------------------------------------------------


def _go_api_base() -> str:
    host = os.environ.get("GO_API_HOST", "127.0.0.1")
    port = current_app.config["FS_CONFIG"].get("AdminPort", 8181)
    return f"http://{host}:{port}"


def _go_get(path: str) -> tuple[Any, int]:
    """GET a path on the Go admin API. Returns (parsed_json, status_code)."""
    url = f"{_go_api_base()}{path}"
    user: str = current_app.config.get("ADMIN_USER", "")
    password: str = current_app.config.get("ADMIN_PASSWORD", "")
    headers: dict[str, str] = {}
    if user:
        import base64

        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=1) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        return {}, e.code
    except Exception:
        return {}, 503


# ---------------------------------------------------------------------------
# CAPTCHA helper (shared with ported registration routes)
# ---------------------------------------------------------------------------


def _captcha_template_vars() -> dict[str, str]:
    return _captcha.template_vars(
        current_app.config.get("CAPTCHA_PROVIDER", ""),
        current_app.config.get("CAPTCHA_SITE_KEY", ""),
    )


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------


@public_bp.route("/")
def home() -> str:
    conn = get_db()
    return render_template(
        "public/home.html",
        total_players=count_leaderboard(conn),
        total_matches=count_public_matches(conn),
    )


@public_bp.route("/api/live-matches")
def api_live_matches():
    """JSON: live matches from Go API + last 10 finished matches from DB."""
    live: list[dict] = []
    online: int | None = None
    data, status = _go_get("/lobby-stats")
    if status == 200:
        lobbies = data.get("lobbies", [])
        online = sum(lb.get("player_count", 0) for lb in lobbies)
        for lb in lobbies:
            for m in lb.get("matches", []):
                m["lobby"] = lb["name"]
                live.append(m)

    finished = get_public_matches(get_db(), 0, 10)
    for r in finished:
        played_on = r.get("played_on")
        if played_on is not None:
            r["played_on"] = played_on.strftime("%Y-%m-%d %H:%M")

    return jsonify({"live": live, "finished": finished, "online": online})


@public_bp.route("/rankings")
def rankings() -> str:
    per_page = _page_size(request)
    page = max(1, int(request.args.get("page", 1) or 1))
    sort, direction = _sort_params(request, "points", "desc")
    div_param = request.args.get("div", "").strip().lower()
    division_int = _division_filter(div_param)
    offset = (page - 1) * per_page

    conn = get_db()
    total = count_leaderboard(conn, division_int)
    rows = get_leaderboard(conn, offset, per_page, sort, direction, division_int)
    for r in rows:
        r["division_label"] = _division(r["division"])

    return render_template(
        "public/rankings.html",
        rows=rows,
        page=page,
        per_page=per_page,
        page_sizes=sorted(_VALID_PAGE_SIZES),
        total_pages=_total_pages(total, per_page),
        total=total,
        sort=sort,
        direction=direction,
        div_param=div_param,
    )


@public_bp.route("/profiles")
def profiles() -> str:
    name = request.args.get("q", "").strip()
    profile: dict | None = None
    recent_matches: list | None = None
    top_players: list | None = None
    conn = get_db()
    if name:
        profile = get_profile_with_stats(conn, name)
        if profile:
            profile["division_label"] = _division(profile["division"])
            recent_matches = get_public_matches(conn, 0, 10, profile["id"])
    else:
        top_players = get_leaderboard(conn, 0, 25, "games", "desc")
        for r in top_players:
            r["division_label"] = _division(r["division"])
    return render_template(
        "public/profiles.html",
        query=name,
        profile=profile,
        recent_matches=recent_matches,
        top_players=top_players,
    )


@public_bp.route("/api/profiles/search")
def api_profiles_search():
    term = request.args.get("q", "").strip()
    if len(term) < 3:
        return jsonify([])
    return jsonify([r["name"] for r in search_profiles(get_db(), term)])


@public_bp.route("/matches")
def matches() -> str:
    per_page = _page_size(request)
    page = max(1, int(request.args.get("page", 1) or 1))
    sort, direction = _sort_params(request, "id", "desc")
    profile_name = request.args.get("profile", "").strip() or None
    offset = (page - 1) * per_page

    conn = get_db()
    profile_id: int | None = None
    profile: dict | None = None
    if profile_name:
        profile = get_profile_with_stats(conn, profile_name)
        if not profile:
            abort(404)
        profile_id = profile["id"]

    total = count_public_matches(conn, profile_id)
    rows = get_public_matches(conn, offset, per_page, profile_id, sort, direction)

    return render_template(
        "public/matches.html",
        rows=rows,
        page=page,
        per_page=per_page,
        page_sizes=sorted(_VALID_PAGE_SIZES),
        total_pages=_total_pages(total, per_page),
        total=total,
        profile=profile,
        profile_name=profile_name,
        sort=sort,
        direction=direction,
    )


# ---------------------------------------------------------------------------
# Downloads — dynamic file explorer
# ---------------------------------------------------------------------------


def _get_sections() -> dict[str, tuple[str, str]]:
    root = current_app.config["REPO_ROOT"]
    return {
        "network": ("Network Databases", os.path.join(root, "downloads", "network")),
        "tools": ("Tools", os.path.join(root, "downloads", "tools")),
    }


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} TB"


def _list_dir(directory: str, section: str, subpath: str) -> list[dict[str, Any]]:
    entries = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return entries
    for name in names:
        if name.startswith(".") or name.lower() == "index.php":
            continue
        full = os.path.join(directory, name)
        is_dir = os.path.isdir(full)
        rel = os.path.join(subpath, name).replace("\\", "/").strip("/")
        stat_res = os.stat(full)
        entries.append(
            {
                "name": name,
                "is_dir": is_dir,
                "size": _human_size(stat_res.st_size) if not is_dir else None,
                "modified": datetime.fromtimestamp(stat_res.st_mtime),
                "url": url_for("public.downloads", section=section, subpath=rel),
            }
        )
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def _make_breadcrumbs(section: str, label: str, subpath: str) -> list[dict[str, str]]:
    crumbs = [
        {"label": "Downloads", "url": url_for("public.downloads")},
        {"label": label, "url": url_for("public.downloads", section=section)},
    ]
    parts = [p for p in subpath.replace("\\", "/").split("/") if p]
    for i, part in enumerate(parts):
        partial = "/".join(parts[: i + 1])
        crumbs.append(
            {
                "label": part,
                "url": url_for("public.downloads", section=section, subpath=partial),
            }
        )
    return crumbs


@public_bp.route("/downloads")
@public_bp.route("/downloads/<section>")
@public_bp.route("/downloads/<section>/<path:subpath>")
def downloads(section: str | None = None, subpath: str = "") -> Any:
    sections = _get_sections()
    if section is None:
        return render_template("public/downloads.html", sections=sections, section=None)

    if section not in sections:
        abort(404)

    label, base = sections[section]
    base_real = os.path.realpath(base)
    target = os.path.realpath(os.path.join(base, subpath))

    if not target.startswith(base_real + os.sep) and target != base_real:
        abort(403)

    if os.path.isfile(target):
        ext = os.path.splitext(target)[1].lower()
        as_attachment = ext in {".cfg", ".zip", ".rar", ".exe", ".7z"}
        mimetype = "application/octet-stream" if as_attachment else None
        return send_file(target, as_attachment=as_attachment, mimetype=mimetype)

    if not os.path.isdir(target):
        # Directory doesn't exist yet — show empty listing rather than 404
        entries: list[dict[str, Any]] = []
        breadcrumbs = _make_breadcrumbs(section, label, subpath)
        return render_template(
            "public/downloads.html",
            sections=sections,
            section=section,
            label=label,
            entries=entries,
            breadcrumbs=breadcrumbs,
            subpath=subpath,
        )

    entries = _list_dir(target, section, subpath)
    breadcrumbs = _make_breadcrumbs(section, label, subpath)
    return render_template(
        "public/downloads.html",
        sections=sections,
        section=section,
        label=label,
        entries=entries,
        breadcrumbs=breadcrumbs,
        subpath=subpath,
    )


# ---------------------------------------------------------------------------
# DB updates — static file server for game client
# ---------------------------------------------------------------------------


@public_bp.route("/updates/<path:filename>")
def db_updates(filename: str) -> Any:
    base = os.path.join(current_app.config["REPO_ROOT"], "db_updates")
    base_real = os.path.realpath(base)
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base_real + os.sep) and target != base_real:
        abort(403)
    if not os.path.isfile(target):
        abort(404)
    return send_file(target, as_attachment=False)


@public_bp.route("/db_updates/<path:filename>")
def db_updates_redirect(filename: str):
    """Backward-compat redirect — folder renamed but old URL still works."""
    return redirect(url_for("public.db_updates", filename=filename), 301)


# ---------------------------------------------------------------------------
# Legacy game ranking endpoints (pes5ec + we9lek_pc share one function)
# ---------------------------------------------------------------------------


def _generate_ranking_html(conn: Any) -> str:
    rows = get_leaderboard(conn, 0, 100)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"general,{now}"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i},0,{r['name']},{r['points']},0,0,0,0,0,0,0")
    return "\n".join(lines)


@public_bp.route("/pes5ec/ranking/we9getrank.html")
@public_bp.route("/we9lek_pc/ranking/we9getrank.html")
def we9_ranking():
    content = _generate_ranking_html(get_db())
    return current_app.response_class(content, mimetype="text/html")


# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------


@public_bp.route("/about")
def about() -> str:
    return render_template("public/about.html")


# ---------------------------------------------------------------------------
# Registration routes (ported from the old register.py)
# ---------------------------------------------------------------------------


@public_bp.route("/register", methods=["GET"])
def register_form() -> str:
    return render_template(
        "register/form.html",
        serial="",
        username="",
        nonce="",
        **_captcha_template_vars(),
    )


@public_bp.route("/md5.js")
def md5_js() -> Any:
    static_folder: str = current_app.static_folder or ""
    return send_from_directory(static_folder, "md5.js")


@public_bp.route("/modifyUser/<nonce>")
def modify_user(nonce: str) -> str:
    conn = get_db()
    user: dict[str, Any] | None = find_user_by_nonce(conn, nonce)
    if user is None:
        abort(404)
    return render_template(
        "register/form.html",
        serial=user["serial"],
        username=user["username"],
        nonce=nonce,
        **_captcha_template_vars(),
    )


@public_bp.route("/register", methods=["POST"])
def register() -> Any:
    remote_ip: str = request.remote_addr or "0.0.0.0"
    banned_list = current_app.config.get("BANNED_LIST", [])
    if is_banned(remote_ip, banned_list):
        abort(403)

    provider_name: str = current_app.config.get("CAPTCHA_PROVIDER", "")
    secret_key: str = current_app.config.get("CAPTCHA_SECRET_KEY", "")
    if provider_name and secret_key:
        provider = _captcha.get_provider(provider_name)
        token: str = request.form.get(provider.token_field, "") if provider else ""
        if not _captcha.verify(token, secret_key, provider_name):
            return (
                render_template(
                    "register/result.html",
                    message="ERROR: CAPTCHA verification failed",
                    success=False,
                ),
                400,
            )

    serial: str = request.form.get("serial", "")
    username: str = request.form.get("user", "")
    hex_hash: str = request.form.get("hash", "")
    nonce: str = request.form.get("nonce", "")

    if not re.fullmatch(r"[0-9a-f]{32}", hex_hash):
        return (
            render_template(
                "register/result.html",
                message="ERROR: invalid hash",
                success=False,
            ),
            400,
        )

    encrypted_hash: str = blowfish_encrypt(hex_hash)
    conn = get_db()

    if not nonce:
        existing = find_user_by_username(conn, username)
        if existing is not None:
            return (
                render_template(
                    "register/result.html",
                    message="ERROR: username is already taken",
                    success=False,
                ),
                409,
            )
        new_id: int = create_user(conn, username, serial, encrypted_hash)
        record_user_registration(conn, new_id)
        return render_template(
            "register/result.html",
            message="Registration complete",
            success=True,
        )

    user: dict[str, Any] | None = find_user_by_nonce(conn, nonce)
    if user is None:
        return (
            render_template(
                "register/result.html",
                message="ERROR: invalid or expired recovery link",
                success=False,
            ),
            404,
        )
    update_user(conn, user["id"], username, serial, encrypted_hash)
    return render_template(
        "register/result.html",
        message="Account updated successfully",
        success=True,
    )
