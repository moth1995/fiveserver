"""Legacy XML administration and local statistics endpoints."""


import asyncio
import base64
import binascii
import hashlib
import hmac
import os
import secrets
import sys
import xml.etree.ElementTree as ET
from html import escape
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, quote

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import Response

try:
    import resource
except ImportError:  # Unix-only module; Windows has no resource.
    resource = None  # type: ignore[assignment]

from ..database import MySQLDatabase, UserRepository
from ..handlers.matches import MATCH_STATE_TEXT
from ..handlers.news import VERSION
from ..logging_setup import set_debug
from ..network import resolve_server_ip
from .registration import XML_HEADER, parse_banned_networks


DEFAULT_PAGE_LIMIT = 30
MAX_LOG_LINES = 5000
MIN_LOG_LINES = 10
MAX_LOG_TAIL_BYTES = 4 * 1024 * 1024
MAX_ADMIN_USERS = 1000
DEFAULT_ADMIN_USER = "fives"
DEFAULT_ADMIN_PASSWORD = "fives"
BASIC_PREFIX = "Basic "


class ServerView(Protocol):
    """Minimal live server view required by administrative routes."""
    settings: dict[str, Any]
    server_ip: str
    sessions: dict[str, object]
    lobbies: list[Any]
    banned_networks: tuple[Any, ...]
    started_at: datetime


class AdminRepository:
    """Database operations used by the legacy administration endpoints."""
    def __init__(self, database: MySQLDatabase, profiles: UserRepository):
        self.database = database
        self.profiles = profiles

    async def users(self, offset: int, limit: int) -> tuple[int, list[tuple[Any, ...]]]:
        """Return a page of non-deleted users ordered by username."""
        total = int((await self.database.read("SELECT count(id) FROM users WHERE deleted = 0"))[0][0])
        rows = await self.database.read(
            "SELECT id,username,serial,hash,reset_nonce,updated_on FROM users WHERE deleted = 0 "
            "ORDER BY username LIMIT %s OFFSET %s", limit, offset,
        )
        return total, rows

    async def profiles_page(self, offset: int, limit: int) -> tuple[int, list[tuple[Any, ...]]]:
        """Return a page of non-deleted profiles ordered by name."""
        total = int((await self.database.read("SELECT count(id) FROM profiles WHERE deleted = 0"))[0][0])
        rows = await self.database.read(
            "SELECT id,name FROM profiles WHERE deleted = 0 ORDER BY name LIMIT %s OFFSET %s", limit, offset,
        )
        return total, rows

    async def profile(self, identifier: str) -> Any:
        """Resolve a profile by numeric ID or unique name."""
        if identifier.isdecimal():
            return await self.profiles.profile_by_id(int(identifier))
        rows = await self.database.read("SELECT id FROM profiles WHERE deleted = 0 AND name = %s", identifier)
        return await self.profiles.profile_by_id(int(rows[0][0])) if rows else None

    async def user_by_name(self, username: str) -> tuple[Any, ...] | None:
        """Find a non-deleted user by username."""
        rows = await self.database.read(
            "SELECT id,username,serial,hash,reset_nonce,updated_on FROM users "
            "WHERE deleted = 0 AND username = %s", username,
        )
        return rows[0] if rows else None

    async def lock_user(self, user_id: int) -> str:
        # Matches the original four groups of decimal digits without persisting other user changes.
        """Generate and persist a reset nonce for the account."""
        nonce = "".join(str(secrets.randbelow(9001) + 1000) for _ in range(4))
        await self.database.write("UPDATE users SET reset_nonce = %s WHERE id = %s", nonce, user_id)
        return nonce

    async def delete_user(self, user_id: int) -> None:
        """Soft-delete an account."""
        await self.database.write("UPDATE users SET deleted = 1 WHERE id = %s", user_id)


def _xml(element: ET.Element, status: int = 200) -> Response:
    return Response(XML_HEADER + ET.tostring(element, encoding="unicode"), status_code=status, media_type="text/xml")


def _error(message: str, status: int) -> Response:
    return _xml(ET.Element("error", text=message), status)


def _tail_log(path: Path, count: int) -> str:
    """Read only trailing chunks, so a huge log is never loaded into memory."""
    chunks: list[bytes] = []
    lines = total = 0
    with path.open("rb") as stream:
        position = stream.seek(0, 2)
        while position and lines <= count and total < MAX_LOG_TAIL_BYTES:
            size = min(position, 64 * 1024, MAX_LOG_TAIL_BYTES - total)
            position -= size
            stream.seek(position)
            chunk = stream.read(size)
            chunks.append(chunk)
            lines += chunk.count(b"\n")
            total += len(chunk)
    text = b"".join(reversed(chunks)).decode("utf-8", "replace")
    return "".join(text.splitlines(keepends=True)[-count:])


def _page(text: str) -> Response:
    return Response(text, media_type="text/html")


def _append_lobby_users(item: ET.Element, lobby: Any) -> None:
    for peer in lobby.players.values():
        if peer.selected_profile is None:
            continue
        attrs = {"profile": peer.selected_profile.name}
        peer_ip = getattr(peer, "peer_ip", None)
        if peer_ip is not None:
            attrs["ip"] = peer_ip
        ET.SubElement(item, "user", attrs)


def _match_element(room: Any) -> ET.Element:
    match = room.pes5_match or room.match6
    attrs = {
        "roomName": room.name.decode("utf-8", "replace"),
        "matchTime": str(room.match_time),
        "score": f"{match.score_home}:{match.score_away}",
    }
    if room.pes5_match is not None:
        attrs["homeTeamId"] = str(match.home_team_id)
        attrs["awayTeamId"] = str(match.away_team_id)
        if match.home_profile is not None:
            attrs["homeProfile"] = match.home_profile.name
        if match.away_profile is not None:
            attrs["awayProfile"] = match.away_profile.name
    elif room.team_selection is not None:
        attrs["homeTeamId"] = str(room.team_selection.home_team_id)
        attrs["awayTeamId"] = str(room.team_selection.away_team_id)
        attrs["clock"] = str(match.clock)
        attrs["state"] = MATCH_STATE_TEXT.get(match.state, "Unknown")
    node = ET.Element("match", attrs)
    if room.match6 is not None and room.team_selection is not None:
        selection = room.team_selection
        for side, captain, others in (
            ("homeTeam", selection.home_captain, selection.home_more),
            ("awayTeam", selection.away_captain, selection.away_more),
        ):
            team = ET.SubElement(node, side)
            for profile in ((captain,) if captain else ()) + tuple(others):
                ET.SubElement(team, "profile", name=profile.name)
    return node


async def _form(request: Request) -> dict[str, str]:
    values = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {key: value[0] for key, value in values.items() if value}


def create_admin_app(
    view: ServerView, repository: AdminRepository, config: dict[str, Any],
    *, public: bool = False,
) -> FastAPI:
    """Build the legacy XML admin app or its unauthenticated statistics variant."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    username = str(config["AdminUser"])
    password = str(os.getenv("FIVESERVER_ADMIN_PASSWORD") or config["AdminPassword"])
    stylesheet = Path(str(config["XslFile"]))
    xsl = stylesheet.read_bytes()
    etag = hashlib.md5(xsl).hexdigest()  # Existing XSL endpoint uses MD5 ETags.

    @app.middleware("http")
    async def basic_auth(request: Request, call_next: Any) -> Response:
        if public:
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith(BASIC_PREFIX):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="fiveserver"'})
        try:
            supplied = base64.b64decode(authorization[len(BASIC_PREFIX):], validate=True).decode("utf-8")
            supplied_user, supplied_password = supplied.split(":", 1)
        except (ValueError, UnicodeError, binascii.Error):
            return Response("Not authorized", status_code=403, media_type="text/plain")
        if not (hmac.compare_digest(supplied_user, username)
                and hmac.compare_digest(supplied_password, password)):
            return Response("Not authorized", status_code=403, media_type="text/plain")
        return await call_next(request)

    @app.get("/xsl/style.xsl")
    @app.head("/xsl/style.xsl")
    async def get_xsl(request: Request) -> Response:
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(xsl if request.method == "GET" else b"", media_type="text/xml", headers={"ETag": etag})

    @app.get("/")
    @app.get("/home")
    async def home() -> Response:
        root = ET.Element("statsService" if public else "adminService", version="1.0")
        ET.SubElement(root, "server", version=VERSION, ip=view.server_ip)
        def link(tag: str, href: str, **attrs: str) -> None:
            ET.SubElement(root, tag, {**attrs, "href": href})

        if public:
            link("users", "/users")
            link("profiles", "/profiles")
            link("onlineUsers", "/users/online")
            link("stats", "/stats")
            link("processInfo", "/ps")
            return _xml(root)
        link("log", "/log")
        link("biglog", "/log?n=5000")
        link("users", "/users")
        link("profiles", "/profiles")
        link("onlineUsers", "/users/online")
        link("stats", "/stats")
        link("userlock", "/userlock")
        link("userkill", "/userkill")
        link("maxusers", "/maxusers", value=str(view.settings.get("MaxUsers", MAX_ADMIN_USERS)))
        link("debug", "/debug", enabled=str(view.settings.get("Debug", False)))
        link("storeSettings", "/settings", enabled=str(view.settings.get("StoreSettings", True)))
        link("roster", "/roster")
        link("banned", "/banned")
        link("server-ip", "/server-ip")
        link("processInfo", "/ps")
        return _xml(root)

    @app.get("/users")
    async def users(request: Request, offset: int = 0, limit: int = DEFAULT_PAGE_LIMIT) -> Response:
        offset, limit = max(0, offset), max(1, limit)
        total, rows = await repository.users(offset, limit)
        root = ET.Element("users", href="/home", total=str(total))
        for _, user_name, _, _, nonce, _ in rows:
            attrs = {"username": str(user_name)}
            if nonce is not None:
                attrs.update(locked="yes", href=f"http://{request.url.hostname}:{config['FiveserverWebPort']}/modifyUser/{nonce}")
            ET.SubElement(root, "user", attrs)
        ET.SubElement(root, "next", href=f"/users?offset={offset + limit}&limit={limit}")
        return _xml(root)

    @app.get("/users/online")
    async def online() -> Response:
        root = ET.Element("users", count=str(len(view.sessions)), href="/home")
        for user_hash, connection in sorted(view.sessions.items()):
            user = getattr(connection, "user", None)
            profile = getattr(connection, "selected_profile", None)
            attrs = {"username": user.username} if user is not None else {"key": user_hash}
            if profile is not None:
                attrs["profile"] = profile.name
            lobby_id = getattr(connection, "lobby_id", None)
            if lobby_id is not None:
                attrs["lobby"] = view.lobbies[lobby_id].name
            peer_ip = getattr(connection, "peer_ip", None)
            if peer_ip is not None:
                attrs["ip"] = peer_ip
            ET.SubElement(root, "user", attrs)
        return _xml(root)

    @app.get("/profiles")
    async def profiles(offset: int = 0, limit: int = DEFAULT_PAGE_LIMIT) -> Response:
        offset, limit = max(0, offset), max(1, limit)
        total, rows = await repository.profiles_page(offset, limit)
        root = ET.Element("profiles", href="/home", total=str(total))
        for profile_id, name in rows:
            ET.SubElement(root, "profile", name=str(name), href=f"/profiles/{profile_id}")
        ET.SubElement(root, "next", href=f"/profiles?offset={offset + limit}&limit={limit}")
        return _xml(root)

    @app.get("/profiles/{identifier}")
    async def profile(identifier: str) -> Response:
        item = await repository.profile(identifier)
        if item is None:
            return _error("profile not found", 404)
        stats = await repository.profiles.profile_stats(item.id)
        root = ET.Element("profile", href="/profiles", name=item.name, id=str(item.id))
        fields = {
            "rank": item.rank, "favPlayer": item.fav_player,
            "favPlayerId": item.fav_player & 0xFFFF,
            "favPlayerTeamId": (item.fav_player >> 16) & 0xFFFF,
            "favTeam": item.fav_team, "division": next(
                (index for index, threshold in enumerate((250, 450, 600, 750)) if item.points < threshold), 4),
            "points": item.points, "disconnects": item.disconnects, "playTime": item.play_time,
            "games": stats.wins + stats.losses + stats.draws, "wins": stats.wins,
            "losses": stats.losses, "draws": stats.draws, "goalsScored": stats.goals_scored,
            "goalsAllowed": stats.goals_allowed, "winningStreakCurrent": stats.streak_current,
            "winningStreakBest": stats.streak_best,
        }
        for name, value in fields.items():
            ET.SubElement(root, name).text = str(value)
        games = stats.wins + stats.losses + stats.draws
        ET.SubElement(root, "winningPct").text = f"{100.0 * stats.wins / games if games else 0.0:0.1f}%"
        ET.SubElement(root, "goalsScoredAverage").text = f"{stats.goals_scored / games if games else 0.0:0.2f}"
        ET.SubElement(root, "goalsAllowedAverage").text = f"{stats.goals_allowed / games if games else 0.0:0.2f}"
        return _xml(root)

    @app.get("/stats")
    async def stats() -> Response:
        root = ET.Element("stats", playerCount=str(len(view.sessions)), href="/home")
        lobbies = ET.SubElement(root, "lobbies", count=str(len(view.lobbies)))
        for lobby in view.lobbies:
            rooms = [room for room in lobby.rooms.values() if room.pes5_match or room.match6]
            item = ET.SubElement(lobbies, "lobby", name=lobby.name,
                                 type=lobby.type_name, showMatches=str(lobby.show_matches),
                                 checkRosterHash=str(lobby.check_roster_hash),
                                 playerCount=str(len(lobby.players)),
                                 roomCount=str(len(lobby.rooms)),
                                 matchesInProgress=str(len(rooms)))
            _append_lobby_users(item, lobby)
            if rooms and lobby.show_matches:
                matches = ET.SubElement(item, "matches")
                for room in rooms:
                    matches.append(_match_element(room))
        return _xml(root)

    @app.get("/ps")
    async def process_info() -> Response:
        root = ET.Element("processInfo", href="/home", pid=str(os.getpid()))
        elapsed = max((datetime.now() - view.started_at).total_seconds(), 1.0)
        times = os.times()
        cpu = 100.0 * (times.user + times.system) / elapsed
        mem = 0.0
        if resource is not None:
            try:
                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # type: ignore[attr-defined]
                mem = rss / 1024.0 if sys.platform == "darwin" else rss
            except AttributeError:
                pass
        ET.SubElement(root, "uptime", since=str(view.started_at), up=str(datetime.now() - view.started_at))
        ET.SubElement(root, "stats", cpu=f"{cpu:0.1f}%", mem=f"{mem / 1024.0:0.1f}M")
        ET.SubElement(root, "info", cmdline=" ".join(sys.argv))
        return _xml(root)

    if public:
        return app

    @app.get("/userlock")
    async def userlock_form() -> Response:
        return _page("<html><body><form action='/userlock' method='POST'><input name='username'/><input name='lock' type='submit'/></form></body></html>")

    @app.post("/userlock")
    async def userlock(request: Request) -> Response:
        form = await _form(request)
        username_value = form.get("username")
        if username_value is None:
            return _error("username parameter missing", 400)
        user = await repository.user_by_name(username_value)
        if user is None:
            return _error("unknown username", 404)
        nonce = await repository.lock_user(int(user[0]))
        root = ET.Element("userLocked", username=username_value, href="/home")
        ET.SubElement(root, "unlock", href=f"http://{request.url.hostname}:{config['FiveserverWebPort']}/modifyUser/{nonce}")
        return _xml(root)

    @app.get("/userkill")
    async def userkill_form() -> Response:
        return _page("<html><body><form action='/userkill' method='POST'><input name='username'/><input name='kill' type='submit'/></form></body></html>")

    @app.post("/userkill")
    async def userkill(request: Request) -> Response:
        name = (await _form(request)).get("username")
        if name is None:
            return _error("username parameter missing", 400)
        user = await repository.user_by_name(name)
        if user is None:
            return _error("unknown username", 404)
        await repository.delete_user(int(user[0]))
        return _xml(ET.Element("userDeleted", username=name, href="/home"))

    @app.get("/log")
    @app.get("/biglog")
    async def logs(n: int = 30) -> Response:
        """Return a bounded tail of the configured server log."""
        log_path = Path(str(config["FiveserverLogFile"]))
        if not log_path.exists():
            return _error("no log file available", 200)
        count = max(MIN_LOG_LINES, min(MAX_LOG_LINES, n))

        try:
            lines = await asyncio.to_thread(_tail_log, log_path, count)
        except FileNotFoundError:  # Removed or rotated between the check and open.
            return _error("no log file available", 200)
        return Response(f"Last {count} lines of the log:\r\n===========================================\r\n"
                        + lines, media_type="text/plain")

    @app.get("/debug")
    @app.get("/maxusers")
    @app.get("/settings")
    @app.get("/roster")
    async def setting_form(request: Request) -> Response:
        path = request.url.path.strip("/")
        key = {"debug": "Debug", "maxusers": "MaxUsers", "settings": "StoreSettings", "roster": "Roster"}[path]
        value = escape(str(view.settings.get(key, "")))
        field = {"debug": "debug", "maxusers": "maxusers", "settings": "store", "roster": "enforceHash"}[path]
        return _page(f"<html><body><form action='/{path}' method='POST'>{value}<input name='{field}'/><input name='set' type='submit'/></form></body></html>")

    @app.post("/debug")
    @app.post("/maxusers")
    @app.post("/settings")
    @app.post("/roster")
    async def setting_update(request: Request) -> Response:
        path = request.url.path.strip("/")
        form = await _form(request)
        if path == "maxusers":
            try:
                value = int(form.get("maxusers", ""))
            except ValueError:
                value = view.settings.get("MaxUsers", MAX_ADMIN_USERS)
            if 0 <= value <= MAX_ADMIN_USERS:
                view.settings["MaxUsers"] = value
            return _xml(ET.Element("maxUsers", value=str(view.settings["MaxUsers"]), href="/home"))
        if path == "roster":
            if "enforceHash" not in form or "compareHash" not in form:
                return _xml(ET.Element("error", text="missing or incorrect parameters", href="/home"))
            view.settings["Roster"] = {key: form[key].lower() in ("1", "true")
                                       for key in ("enforceHash", "compareHash")}
            return _xml(ET.Element("result", text="roster settings changed", href="/home"))
        key, field, tag = ("Debug", "debug", "debug") if path == "debug" else ("StoreSettings", "store", "storeSettings")
        if field in form:
            supplied = form[field].lower()
            if supplied in ("0", "false", "no", "1", "true", "yes"):
                view.settings[key] = supplied in ("1", "true", "yes")
        if key == "Debug":
            set_debug(bool(view.settings.get("Debug", False)))
        return _xml(ET.Element(tag, enabled=str(view.settings.get(key, False)), href="/home"))

    @app.get("/banned")
    async def banned() -> Response:
        root = ET.Element("banned", href="/home")
        entries = ET.SubElement(root, "list")
        banned_path = Path(str(view.settings["BannedList"]))
        stored = await asyncio.to_thread(banned_path.read_text, encoding="utf-8")
        specs = yaml.safe_load(stored).get("Banned", [])
        for entry in sorted(specs):
            ET.SubElement(entries, "entry", spec=entry, href="/ban-remove?entry=" + quote(entry))
        ET.SubElement(root, "add", href="/ban-add")
        return _xml(root)

    @app.get("/ban-add")
    @app.get("/ban-remove")
    async def ban_form(request: Request, entry: str = "") -> Response:
        action = request.url.path
        return _page(f"<html><body><form action='{action}' method='POST'><input name='entry' value='{escape(entry, quote=True)}'/><input type='submit'/></form></body></html>")

    @app.post("/ban-add")
    @app.post("/ban-remove")
    async def ban_change(request: Request) -> Response:
        entry = (await _form(request)).get("entry", "")
        if not parse_banned_networks([entry]):
            return _error("invalid network", 400)
        banned_path = Path(str(view.settings["BannedList"]))

        def update_banned() -> list[str]:
            data = yaml.safe_load(banned_path.read_text(encoding="utf-8"))
            specs: list[str] = list(data.get("Banned", []))
            if request.url.path.endswith("ban-add") and entry not in specs:
                specs.append(entry)
            elif request.url.path.endswith("ban-remove") and entry in specs:
                specs.remove(entry)
            data["Banned"] = specs
            temporary = banned_path.with_suffix(banned_path.suffix + ".tmp")
            temporary.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            os.replace(temporary, banned_path)
            return specs

        specs = await asyncio.to_thread(update_banned)
        view.banned_networks = parse_banned_networks(specs)
        return _xml(ET.Element("actionAccepted", href="/banned"))

    @app.get("/server-ip")
    async def server_ip_form() -> Response:
        return _page(f"<html><body><h3>Current server IP is: {escape(view.server_ip)}</h3><form action='/server-ip' method='POST'><input name='requery' type='submit'/></form></body></html>")

    @app.post("/server-ip")
    async def server_ip_update() -> Response:
        view.server_ip = await resolve_server_ip(view.settings)
        return _xml(ET.Element("serverIP-requery", started="true", href="/home"))

    return app
