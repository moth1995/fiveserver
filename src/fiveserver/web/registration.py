"""Legacy registration and account-modification HTTP interface."""


from html import escape
from ipaddress import IPv4Address, IPv4Network
import logging
from pathlib import Path
from urllib.parse import parse_qs
from xml.sax.saxutils import escape as xml_escape

from Crypto.Cipher import Blowfish
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ..database import MySQLDatabase
from ..protocol.codecs import CIPHER_KEY


XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<?xml-stylesheet type="text/xsl" href="/xsl/style.xsl"?>\n'
LOGGER = logging.getLogger(__name__)


def parse_banned_networks(specs: list[str]) -> tuple[IPv4Network, ...]:
    """Parse original IP/network shorthand and CIDR entries into IPv4 networks."""
    networks = []
    for spec in specs:
        address, _, mask = spec.partition("/")
        parts = [part for part in address.rstrip(".").split(".") if part]
        if not 1 <= len(parts) <= 4:
            continue
        try:
            full_address = ".".join(parts + ["0"] * (4 - len(parts)))
            prefix = int(mask) if mask else len(parts) * 8
            networks.append(IPv4Network(f"{full_address}/{prefix}", strict=False))
        except ValueError:
            continue
    return tuple(networks)


class RegistrationRepository:
    """User lookup and account mutation used by the registration form."""
    def __init__(self, database: MySQLDatabase):
        self.database = database

    async def find(self, column: str, value: str) -> tuple[object, ...] | None:
        """Find a non-deleted user by username, nonce, or hash."""
        if column not in ("username", "reset_nonce", "hash"):
            raise ValueError("Invalid account lookup")
        rows = await self.database.read(
            f"SELECT id,username,serial,hash,reset_nonce,updated_on FROM users "
            f"WHERE deleted = 0 AND {column} = %s",
            value,
        )
        return rows[0] if rows else None

    async def store(self, user_id: int | None, username: str, serial: str, user_hash: str, nonce: str | None) -> None:
        """Insert or update an account while preserving its ID."""
        await self.database.write(
            "INSERT INTO users (id,username,serial,hash,reset_nonce) VALUES (%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE deleted=0, username=%s, serial=%s, hash=%s, reset_nonce=%s",
            user_id, username, serial, user_hash, nonce,
            username, serial, user_hash, nonce,
        )


def _asset(web_dir: Path, name: str) -> str:
    path = web_dir / name
    if not path.exists():
        path = web_dir / name.replace(".html", "-sample.html")
    return path.read_text(encoding="utf-8")


def _result(web_dir: Path, fmt: str | None, message: str, status: int = 200) -> Response:
    if fmt == "html":
        body = _asset(web_dir, "result.html") % {"result": escape(message)}
        return Response(body, status_code=status, media_type="text/html")
    return Response(XML_HEADER + f'<result text="{xml_escape(message)}" />', status_code=status, media_type="text/xml")


def create_registration_app(repository: RegistrationRepository, web_dir: Path, banned_ips: tuple[IPv4Network, ...] = ()) -> FastAPI:
    """Build the legacy registration and account-modification FastAPI app."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/{path:path}")
    async def get_page(path: str) -> Response:
        if path == "xsl/style.xsl":
            return Response((web_dir / "style.xsl").read_text(encoding="utf-8"), media_type="text/xml")
        if path == "md5.js":
            return Response((web_dir / "md5.js").read_text(encoding="utf-8"), media_type="text/javascript")
        username = nonce = serial = ""
        if path.startswith("modifyUser/"):
            user = await repository.find("reset_nonce", path.rsplit("/", 1)[-1])
            if user:
                _, username_value, serial_value, _, nonce_value, _ = user
                username, serial, nonce = str(username_value), str(serial_value), str(nonce_value)
        form = _asset(web_dir, "form.html") % {
            "username": escape(str(username), quote=True),
            "nonce": escape(str(nonce), quote=True),
            "serial": escape(str(serial), quote=True),
        }
        return Response(form, media_type="text/html")

    @app.post("/{path:path}")
    async def register(path: str, request: Request) -> Response:
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        try:
            serial = form["serial"][0]
            username = form["user"][0]
            digest = bytes.fromhex(form["hash"][0])
            nonce = form["nonce"][0]
        except (KeyError, IndexError, UnicodeError, ValueError):
            return _result(web_dir, form.get("format", [None])[0], "ERROR: Invalid registration form", 400)
        fmt = form.get("format", [None])[0]
        message, status = "SUCCESS: Registration complete", 200
        if len(digest) != 16:
            message, status = "ERROR: Invalid registration form", 400
        else:
            blocked = False
            if request.client:
                try:
                    blocked = any(IPv4Address(request.client.host) in network for network in banned_ips)
                except ValueError:
                    pass
            if blocked:
                message, status = "ERROR: Cannot register: your IP is banned", 403
            else:
                user_hash = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).encrypt(digest).hex()
                try:
                    if not nonce:
                        if await repository.find("username", username):
                            message, status = "ERROR: Cannot register: username taken", 409
                        elif await repository.find("hash", user_hash):
                            message, status = "ERROR: Unable to register: server errror", 500
                        else:
                            await repository.store(None, username, serial, user_hash, None)
                    else:
                        existing = await repository.find("reset_nonce", nonce)
                        if existing is None:
                            message, status = "ERROR: Cannot modify user: invalid nonce", 404
                        else:
                            await repository.store(int(str(existing[0])), username, serial, user_hash, None)
                except Exception:
                    LOGGER.exception("Registration failed")
                    message, status = "ERROR: Unable to register: server errror", 500
        return _result(web_dir, fmt, message, status)

    return app
