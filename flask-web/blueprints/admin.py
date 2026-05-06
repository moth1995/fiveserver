"""Admin blueprint: protected panel for server management."""

from __future__ import annotations

import json
import logging
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

from db import (
    browse_profiles,
    browse_users,
    delete_user,
    find_user_by_id,
    find_user_by_username,
    get_db,
    get_profile_by_id,
    get_profile_by_name,
    get_profile_stats,
    get_profiles_for_user,
    lock_user,
    unlock_user,
)
from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

_OPEN_ENDPOINTS = {"admin.login", "admin.logout"}

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _is_logged_in() -> bool:
    return session.get("admin_logged_in") is True


@admin_bp.before_request
def check_auth():
    if request.endpoint in _OPEN_ENDPOINTS:
        return None
    if not _is_logged_in():
        return redirect(url_for("admin.login", next=request.path))
    return None


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if _is_logged_in():
        return redirect(url_for("admin.home"))
    error: str | None = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if (username == current_app.config["ADMIN_USER"] and
                password == current_app.config["ADMIN_PASSWORD"]):
            session["admin_logged_in"] = True
            next_url = request.args.get("next") or url_for("admin.home")
            return redirect(next_url)
        error = "Invalid username or password."
    return render_template("admin/login.html", error=error)


@admin_bp.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin.login"))


# ---------------------------------------------------------------------------
# Go internal API helper
# ---------------------------------------------------------------------------


def _go_api_base() -> str:
    host = os.environ.get('GO_API_HOST', 'fiveserver')
    port = current_app.config['FS_CONFIG'].get('AdminPort', 8181)
    return f'http://{host}:{port}'


def _go_post(path: str, payload: dict[str, Any]) -> tuple[Any, int]:
    """POST JSON to the Go admin API. Returns (parsed_body, status_code)."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f'{_go_api_base()}{path}',
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("Go API POST %s returned %d: %s", path, e.code, body)
        return {'error': body}, e.code
    except Exception as e:
        logger.error("Go API POST %s failed: %s", path, e)
        return {'error': str(e)}, 503


def _get_online_users() -> list[dict[str, Any]]:
    try:
        with urllib.request.urlopen(
                f'{_go_api_base()}/stats/users', timeout=1) as resp:
            data: list[dict[str, Any]] = json.loads(resp.read())
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("Go API /stats/users unavailable: %s", e)
        return []


def _get_lobby_stats() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(
                f'{_go_api_base()}/lobby-stats', timeout=1) as resp:
            data: dict[str, Any] = json.loads(resp.read())
            return {lb['name']: lb for lb in data.get('lobbies', [])}
    except Exception as e:
        logger.warning("Go API /lobby-stats unavailable: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@admin_bp.route("/", methods=["GET", "POST"])
@admin_bp.route("/home", methods=["GET", "POST"])
def home() -> str:
    conn = get_db()
    cfg = current_app.config["FS_CONFIG"]
    edit_mode: bool = request.args.get("edit") == "1"
    lobby_saved = False

    if request.method == "POST":
        action: str = request.form.get("action", "")
        lobbies_display = _parse_lobbies_from_form(request.form)

        if action == "lobby_add":
            lobbies_display.append({
                'name': '', 'type': 'open', 'show_matches': True, 'check_roster_hash': True,
            })
            edit_mode = True
        elif action.startswith("lobby_remove_"):
            idx = int(action.split("_")[-1])
            if 0 <= idx < len(lobbies_display):
                lobbies_display.pop(idx)
            edit_mode = True
        elif action == "lobby_save":
            cfg.Lobbies = _lobbies_to_yaml(lobbies_display)
            try:
                cfg.save()
                lobby_saved = True
            except Exception as e:
                logger.error("Failed to save lobby config: %s", e)
            # return to view mode after saving
            edit_mode = False
    else:
        lobbies_display = _normalize_lobbies(cfg.get("Lobbies", []))

    total, _ = browse_users(conn, offset=0, limit=1)
    online_users = _get_online_users()
    lobby_stats = _get_lobby_stats()  # keyed by lobby name

    # Count players per lobby name from online users (fallback when Go API unavailable)
    lobby_players: dict[str, int] = {}
    for u in online_users:
        lobby_name: str = u.get("lobby", "")
        lobby_players[lobby_name] = lobby_players.get(lobby_name, 0) + 1

    return render_template(
        "admin/home.html",
        user_count=total,
        online_users=online_users,
        lobbies=lobbies_display,
        lobby_players=lobby_players,
        lobby_stats=lobby_stats,
        lobby_saved=lobby_saved,
        edit_mode=edit_mode,
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@admin_bp.route("/users")
def users() -> str:
    offset: int = int(request.args.get("offset", 0))
    limit: int = int(request.args.get("limit", 50))
    search: str | None = request.args.get("q") or None
    conn = get_db()
    total, rows = browse_users(conn, offset=offset, limit=limit, search=search)
    return render_template(
        "admin/users.html",
        users=rows,
        offset=offset,
        limit=limit,
        total=total,
        query=search or "",
    )


@admin_bp.route("/users/<int:user_id>")
def user_detail(user_id: int) -> str:
    conn = get_db()
    user: dict[str, Any] | None = find_user_by_id(conn, user_id)
    if user is None:
        abort(404)
    profiles = get_profiles_for_user(conn, user_id)
    return render_template("admin/user_detail.html", user=user, profiles=profiles)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@admin_bp.route("/profiles")
def profiles() -> str:
    offset: int = int(request.args.get("offset", 0))
    limit: int = int(request.args.get("limit", 50))
    query: str = request.args.get("q", "").strip()
    conn = get_db()
    total, rows = browse_profiles(conn, offset=offset, limit=limit, query=query)
    return render_template(
        "admin/users.html",
        users=[],
        profiles=rows,
        offset=offset,
        limit=limit,
        total=total,
        query=query,
        profiles_mode=True,
    )


@admin_bp.route("/profiles/<profile_id>")
def profile_detail(profile_id: str) -> str:
    conn = get_db()
    profile: dict[str, Any] | None
    try:
        profile = get_profile_by_id(conn, int(profile_id))
    except ValueError:
        profile = get_profile_by_name(conn, profile_id)
    if profile is None:
        abort(404)
    stats = get_profile_stats(conn, profile["id"])
    return render_template("admin/profile_detail.html", profile=profile, stats=stats)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@admin_bp.route("/chat", methods=["GET", "POST"])
def chat() -> str:
    lobby_filter: str = request.args.get("lobby", "")
    error: str | None = None
    success: str | None = None

    if request.method == "POST":
        message: str = request.form.get("message", "").strip()
        lobby: str = request.form.get("lobby", "").strip()
        if not message:
            error = "Message cannot be empty."
        elif len(message.encode()) > 126:
            error = "Message too long (max 126 bytes)."
        else:
            payload: dict[str, Any] = {"message": message, "from": "admin"}
            if lobby:
                payload["lobby"] = lobby
            _, status = _go_post("/admin/chat", payload)
            if status == 200:
                success = f"Message sent to {'lobby ' + lobby if lobby else 'all lobbies'}."
            else:
                error = "Go server unavailable or lobby not found."

    # Fetch chat history
    url = f'{_go_api_base()}/admin/chat'
    if lobby_filter:
        url += f'?lobby={urllib.parse.quote(lobby_filter)}'
    lobbies_chat: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read())
            lobbies_chat = data.get('lobbies', [])
    except Exception as e:
        logger.warning("Go API /admin/chat unavailable: %s", e)

    return render_template(
        "admin/chat.html",
        lobbies_chat=lobbies_chat,
        lobby_filter=lobby_filter,
        error=error,
        success=success,
    )


# ---------------------------------------------------------------------------
# JSON polling endpoints (used by dashboard and chat page JS)
# ---------------------------------------------------------------------------


@admin_bp.route("/api/lobby-stats")
def api_lobby_stats():
    return jsonify({
        "users": _get_online_users(),
        "lobby_stats": _get_lobby_stats(),
    })


@admin_bp.route("/api/chat")
def api_chat():
    lobby_filter = request.args.get("lobby", "")
    url = f'{_go_api_base()}/admin/chat'
    if lobby_filter:
        url += f'?lobby={urllib.parse.quote(lobby_filter)}'
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return jsonify(json.loads(resp.read()))
    except Exception as e:
        logger.warning("Go API /admin/chat (api) unavailable: %s", e)
        return jsonify({"lobbies": []}), 503


# ---------------------------------------------------------------------------
# Log viewer
# ---------------------------------------------------------------------------


@admin_bp.route("/log")
def log() -> str:
    n_lines: int = int(request.args.get("lines", 30))
    n_lines = min(n_lines, 5000)
    cfg = current_app.config["FS_CONFIG"]
    log_file: str = cfg.get("FiveserverLogFile", "")
    lines: list[str] = []
    if log_file:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isabs(log_file):
            log_file = os.path.join(repo_root, log_file)
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            lines = [l.rstrip("\n") for l in all_lines[-n_lines:]]
        except OSError:
            lines = ["(log file not found or unreadable)"]
    return render_template("admin/log.html", lines=lines, line_count=n_lines)


# ---------------------------------------------------------------------------
# User management actions
# ---------------------------------------------------------------------------


@admin_bp.route("/userlock", methods=["GET", "POST"])
def userlock() -> str | tuple[str, int]:
    if request.method == "GET":
        return render_template(
            "admin/confirm.html",
            title="Lock User",
            message="",
            form_action="/admin/userlock",
            form_field="username",
            form_label="Username",
            button_label="Lock User",
            detail="",
        )
    username: str = request.form.get("username", "").strip()
    if not username:
        return (
            render_template(
                "admin/confirm.html",
                title="Lock User",
                message="ERROR: username required",
                detail="",
            ),
            400,
        )
    conn = get_db()
    user: dict[str, Any] | None = find_user_by_username(conn, username)
    if user is None:
        return (
            render_template(
                "admin/confirm.html",
                title="Lock User",
                message=f'ERROR: user "{username}" not found',
                detail="",
            ),
            404,
        )
    nonce = "".join(str(random.randint(1000, 9999)) for _ in range(4))
    lock_user(conn, user["id"], nonce)
    host = request.host
    recovery_url = f"https://{host}/modifyUser/{nonce}"
    return render_template(
        "admin/confirm.html",
        title="User Locked",
        message=f'User "{username}" has been locked.',
        detail=f"Recovery URL: {recovery_url}",
    )


@admin_bp.route("/userkill", methods=["GET", "POST"])
def userkill() -> str | tuple[str, int]:
    if request.method == "GET":
        return render_template(
            "admin/confirm.html",
            title="Delete User",
            message="",
            form_action="/admin/userkill",
            form_field="username",
            form_label="Username",
            button_label="Delete User",
            detail="",
        )
    username: str = request.form.get("username", "").strip()
    if not username:
        return (
            render_template(
                "admin/confirm.html",
                title="Delete User",
                message="ERROR: username required",
                detail="",
            ),
            400,
        )
    conn = get_db()
    user: dict[str, Any] | None = find_user_by_username(conn, username)
    if user is None:
        return (
            render_template(
                "admin/confirm.html",
                title="Delete User",
                message=f'ERROR: user "{username}" not found',
                detail="",
            ),
            404,
        )
    delete_user(conn, user["id"])
    return render_template(
        "admin/confirm.html",
        title="User Deleted",
        message=f'User "{username}" has been deleted.',
        detail="",
    )


@admin_bp.route("/userunlock", methods=["GET", "POST"])
def userunlock() -> str | tuple[str, int]:
    if request.method == "GET":
        return render_template(
            "admin/confirm.html",
            title="Unlock User",
            message="",
            form_action="/admin/userunlock",
            form_field="username",
            form_label="Username",
            button_label="Unlock User",
            detail="",
        )
    username: str = request.form.get("username", "").strip()
    if not username:
        return (
            render_template(
                "admin/confirm.html",
                title="Unlock User",
                message="ERROR: username required",
                detail="",
            ),
            400,
        )
    conn = get_db()
    user: dict[str, Any] | None = find_user_by_username(conn, username)
    if user is None:
        return (
            render_template(
                "admin/confirm.html",
                title="Unlock User",
                message=f'ERROR: user "{username}" not found',
                detail="",
            ),
            404,
        )
    unlock_user(conn, user["id"])
    return render_template(
        "admin/confirm.html",
        title="User Unlocked",
        message=f'User "{username}" has been unlocked.',
        detail="",
    )


# ---------------------------------------------------------------------------
# Server settings
# ---------------------------------------------------------------------------


# Division types stored as single-element lists in YAML: ['1'], ['2'], etc.
# 'open' and 'noStats' are plain strings. 'open' is the default (no type key needed).
_DIV_TYPES: set[str] = {'A', '3B', '3A', '2', '1'}


def _yaml_type_to_ui(type_val: Any) -> str:
    """Convert a YAML lobby type value to a single select option string."""
    if isinstance(type_val, list):
        # e.g. ['A'] → 'A', ['1'] → '1'
        return type_val[0] if type_val else 'open'
    if isinstance(type_val, str) and type_val:
        return type_val  # 'noStats' or 'open'
    return 'open'


def _ui_type_to_yaml(ui_type: str) -> Any:
    """Convert a select option string back to the YAML type value."""
    if ui_type in _DIV_TYPES:
        return [ui_type]
    if ui_type == 'noStats':
        return 'noStats'
    return 'open'  # caller will omit this from the dict


def _normalize_lobbies(raw: list[Any]) -> list[dict[str, Any]]:
    """Normalize YAML lobby entries (str or dict) to uniform dicts for the UI.

    Defaults match original FiveServerConfig:
      showMatches      → True when absent
      checkRosterHash  → True when absent
    """
    result: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, str):
            result.append({
                'name': entry,
                'type': 'open',
                'show_matches': True,
                'check_roster_hash': True,
            })
        elif isinstance(entry, dict):
            result.append({
                'name': entry.get('name', ''),
                'type': _yaml_type_to_ui(entry.get('type', 'open')),
                'show_matches': bool(entry.get('showMatches', True)),
                'check_roster_hash': bool(entry.get('checkRosterHash', True)),
            })
    return result


def _lobbies_to_yaml(lobbies: list[dict[str, Any]]) -> list[Any]:
    """Convert UI lobby dicts back to the mixed str/dict YAML format.

    A lobby becomes a plain string when type is open, showMatches is True,
    and checkRosterHash is True — matching the original compact notation.
    """
    result: list[Any] = []
    for lb in lobbies:
        name = lb['name'].strip()
        if not name:
            continue
        ui_type: str = lb.get('type', 'open')
        show: bool = lb['show_matches']
        roster: bool = lb['check_roster_hash']
        # Simple case — plain string, no extras needed
        if ui_type == 'open' and show and roster:
            result.append(name)
            continue
        d: dict[str, Any] = {'name': name}
        yaml_type = _ui_type_to_yaml(ui_type)
        if yaml_type != 'open':
            d['type'] = yaml_type
        if not show:
            d['showMatches'] = False
        if not roster:
            d['checkRosterHash'] = False
        result.append(d)
    return result


def _parse_lobbies_from_form(form: Any) -> list[dict[str, Any]]:
    """Reconstruct lobby list from indexed form fields."""
    count = int(form.get('lobby_count', 0))
    lobbies: list[dict[str, Any]] = []
    for i in range(count):
        lobbies.append({
            'name': form.get(f'lobby_name_{i}', '').strip(),
            'type': form.get(f'lobby_type_{i}', 'open'),
            'show_matches': f'lobby_show_{i}' in form,
            'check_roster_hash': f'lobby_roster_{i}' in form,
        })
    return lobbies


@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_bp.route("/debug", methods=["GET", "POST"])
@admin_bp.route("/maxusers", methods=["GET", "POST"])
@admin_bp.route("/roster", methods=["GET", "POST"])
def settings() -> str:
    cfg = current_app.config["FS_CONFIG"]
    saved = False

    if request.method == "POST":
        # General
        cfg.Debug = "debug" in request.form
        cfg.MaxUsers = int(request.form.get("max_users", 1000))
        cfg.StoreSettings = "store_settings" in request.form
        cfg.ShowStats = "show_stats" in request.form
        cfg.ServerName = request.form.get("server_name", "Fiveserver").strip()

        # Greeting
        cfg.Greeting = {"text": request.form.get("greeting", "")}

        # Roster
        cfg.Roster = {
            "enforceHash": "enforce_hash" in request.form,
            "compareHash": "compare_hash" in request.form,
        }

        # Disconnects
        cfg.Disconnects = {
            "CountAsLoss": {
                "Enabled": "dc_enabled" in request.form,
                "Score": {
                    "player": int(request.form.get("dc_score_player", 0)),
                    "opponent": int(request.form.get("dc_score_opponent", 3)),
                },
            }
        }

        # Compute ranks interval
        cfg.ComputeRanksInterval = {
            "days": int(request.form.get("ranks_days", 1)),
            "seconds": int(request.form.get("ranks_seconds", 0)),
        }

        # Chat — preserve warningMessage, only update bannedWords
        warning_msg: str = cfg.get("Chat", {}).get("warningMessage", "")
        banned_words: list[str] = [
            w.strip()
            for w in request.form.get("banned_words", "").splitlines()
            if w.strip()
        ]
        cfg.Chat = {"bannedWords": banned_words, "warningMessage": warning_msg}

        try:
            cfg.save()
            saved = True
        except Exception as e:
            logger.error("Failed to save settings: %s", e)

    roster: dict[str, Any] = cfg.get("Roster", {})
    disconnects: dict[str, Any] = cfg.get("Disconnects", {})
    dc_loss = disconnects.get("CountAsLoss", {})
    dc_score = dc_loss.get("Score", {})
    ranks: dict[str, Any] = cfg.get("ComputeRanksInterval", {})
    chat: dict[str, Any] = cfg.get("Chat", {})
    greeting: Any = cfg.get("Greeting", "")
    greeting_text: str = greeting.get("text", "") if isinstance(greeting, dict) else str(greeting)

    return render_template(
        "admin/settings.html",
        saved=saved,
        server_name=cfg.get("ServerName", "Fiveserver"),
        max_users=cfg.get("MaxUsers", 1000),
        debug=bool(cfg.get("Debug", False)),
        show_stats=bool(cfg.get("ShowStats", True)),
        store_settings=bool(cfg.get("StoreSettings", True)),
        greeting=greeting_text,
        enforce_hash=bool(roster.get("enforceHash", False)),
        compare_hash=bool(roster.get("compareHash", True)),
        dc_enabled=bool(dc_loss.get("Enabled", False)),
        dc_score_player=int(dc_score.get("player", 0)),
        dc_score_opponent=int(dc_score.get("opponent", 3)),
        ranks_days=int(ranks.get("days", 1)),
        ranks_seconds=int(ranks.get("seconds", 0)),
        banned_words="\n".join(chat.get("bannedWords", [])),
    )


# ---------------------------------------------------------------------------
# Ban management
# ---------------------------------------------------------------------------


@admin_bp.route("/banned")
def banned() -> str:
    cfg = current_app.config["FS_CONFIG"]
    banned_list: list[str] = []
    try:
        import os as _os

        import yaml as _yaml

        repo_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        banned_file: str = cfg.get("BannedList", "")
        if banned_file:
            if not _os.path.isabs(banned_file):
                banned_file = _os.path.join(repo_root, banned_file)
            if _os.path.exists(banned_file):
                with open(banned_file, encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                banned_list = data.get("Banned", [])
    except Exception as e:
        logger.error("Failed to load banned list: %s", e)
    return render_template("admin/banned.html", banned_list=banned_list)


def _load_banned_file() -> tuple[list[str], str]:
    """Return (banned_list, file_path)."""
    import yaml as _yaml

    cfg = current_app.config["FS_CONFIG"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    banned_file: str = cfg.get("BannedList", "")
    if banned_file and not os.path.isabs(banned_file):
        banned_file = os.path.join(repo_root, banned_file)
    banned_list: list[str] = []
    if banned_file and os.path.exists(banned_file):
        with open(banned_file, encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        banned_list = data.get("Banned", [])
    return banned_list, banned_file


def _save_banned_file(banned_list: list[str], banned_file: str) -> None:
    import yaml as _yaml

    with open(banned_file, "wt", encoding="utf-8") as f:
        f.write(_yaml.dump({"Banned": banned_list}))
    # Update in-memory fast list
    from config import make_fast_banned_list

    current_app.config["BANNED_LIST"] = make_fast_banned_list(banned_list)


@admin_bp.route("/ban-add", methods=["GET", "POST"])
def ban_add() -> str:
    if request.method == "POST":
        ip_spec: str = request.form.get("ip", "").strip()
        if ip_spec:
            banned_list, banned_file = _load_banned_file()
            if ip_spec not in banned_list:
                banned_list.append(ip_spec)
                if banned_file:
                    _save_banned_file(banned_list, banned_file)
    return redirect(url_for("admin.banned"))


@admin_bp.route("/ban-remove", methods=["GET", "POST"])
def ban_remove() -> str:
    if request.method == "POST":
        ip_spec: str = request.form.get("ip", "").strip()
        if ip_spec:
            banned_list, banned_file = _load_banned_file()
            if ip_spec in banned_list:
                banned_list.remove(ip_spec)
                if banned_file:
                    _save_banned_file(banned_list, banned_file)
    return redirect(url_for("admin.banned"))


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


@admin_bp.route("/server-ip")
def server_ip() -> str:
    cfg = current_app.config["FS_CONFIG"]
    ip: str = cfg.get("ServerIP", "unknown")
    return render_template(
        "admin/confirm.html",
        title="Server IP",
        message=f"Server IP: {ip}",
        detail="",
        form_action=None,
    )


@admin_bp.route("/ps")
def ps() -> str:
    import platform

    pid = os.getpid()
    try:
        import psutil  # type: ignore[import-untyped]

        proc = psutil.Process(pid)
        mem_mb = round(proc.memory_info().rss / 1024 / 1024, 1)
        cpu_pct = proc.cpu_percent(interval=0.1)
        detail = f"PID: {pid} | Memory: {mem_mb} MB | CPU: {cpu_pct}%"
    except ImportError:
        detail = f"PID: {pid} | (install psutil for detailed stats)"
    return render_template(
        "admin/confirm.html",
        title="Process Info",
        message=platform.node(),
        detail=detail,
        form_action=None,
    )
