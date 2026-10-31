"""Central standard-library logging with the original FiveServer presentation."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone, tzinfo
import glob
from http import HTTPStatus
import logging
import logging.handlers
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from Crypto.Cipher import Blowfish

from .protocol.codecs import AUTH_REQUEST_LENGTHS, CIPHER_KEY
from .protocol.dispatcher import SIX_GAMES, ServiceRole

LOGGER = logging.getLogger("fiveserver.logging")
PACKET_LOGGER = logging.getLogger("fiveserver.packet")
HEX_BYTES_PER_LINE = 8
HEX_FIELD_WIDTH = HEX_BYTES_PER_LINE * 3 - 1
SENSITIVE_PACKET_IDS = frozenset({0x3003, 0x3088})
HANDLER_MARKER = "_fiveserver_handler"
LOG_CONTEXT: ContextVar[Any] = ContextVar("fiveserver_log_context", default=None)
_DEBUG = False
ASCII_REPLACEMENT = re.compile(rb'''[^\w .,!?'"/<>-]''')
SEVERITY_PREFIX = re.compile(r"^(?:DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|CRITICAL|SERVER ERROR):")
DEFAULT_LOG_MAX_BYTES = 1_000_000
DEFAULT_LOG_BACKUP_COUNT: int | None = None


def _env_log_int(name: str, default: int) -> int:
    """Read a non-negative integer log setting from the environment."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value


def _env_log_backup_count() -> int | None:
    """Read the optional rotated-file limit; empty means unlimited."""
    raw = os.getenv("FIVESERVER_LOG_BACKUP_COUNT")
    if raw is None or raw == "":
        return DEFAULT_LOG_BACKUP_COUNT
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("FIVESERVER_LOG_BACKUP_COUNT must be an integer") from exc
    if value < 0:
        raise ValueError("FIVESERVER_LOG_BACKUP_COUNT must be zero or greater")
    return value


class SizeRotatingLogFileHandler(logging.FileHandler):
    """Rotate like Twisted's LogFile: size first, then .1/.2 backups."""

    def __init__(self, filename: Path, *, max_bytes: int = DEFAULT_LOG_MAX_BYTES,
                 backup_count: int | None = DEFAULT_LOG_BACKUP_COUNT, encoding: str = "utf-8"):
        super().__init__(str(filename), mode="a", encoding=encoding)
        self.max_bytes = max_bytes
        self.backup_count = backup_count

    def emit(self, record: logging.LogRecord) -> None:
        """Rotate before writing once the active file reaches the byte limit."""
        try:
            if self.max_bytes > 0:
                if self.stream is None:
                    self.stream = self._open()
                if self.stream.tell() >= self.max_bytes:
                    self._rotate()
        except (OSError, ValueError):
            pass
        super().emit(record)

    def _backups(self) -> list[tuple[int, str]]:
        """Return numbered backup files in descending rotation order."""
        backup_files = []
        for name in glob.glob(f"{self.baseFilename}.*"):
            suffix = name.rsplit(".", 1)[-1]
            if suffix.isdigit() and int(suffix) > 0:
                backup_files.append((int(suffix), name))
        return sorted(backup_files, reverse=True)

    def _rotate(self) -> None:
        """Shift backups, rename the active file to .1, and reopen for append."""
        if self.stream is not None:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        backups = self._backups()
        try:
            for index, name in backups:
                if self.backup_count is not None and index >= self.backup_count:
                    os.remove(name)
                else:
                    os.replace(name, f"{self.baseFilename}.{index + 1}")
            if os.path.exists(self.baseFilename):
                os.replace(self.baseFilename, f"{self.baseFilename}.1")
        except OSError:
            pass  # Match Twisted: keep logging to the existing file on failure.
        self.stream = self._open()


@contextmanager
def logging_context(context: Any) -> Iterator[None]:
    """Propagate a connection or component context, restoring it on every exit."""
    token = LOG_CONTEXT.set(context)
    try:
        yield
    finally:
        LOG_CONTEXT.reset(token)


def connection_context(connection: Any) -> str:
    """Name the real service, server-assigned connection number and remote IP."""
    role = getattr(connection, "role", None)
    names = {ServiceRole.NEWS: "NewsProtocol", ServiceRole.LOGIN: "LoginService",
             ServiceRole.NETWORK_MENU: "NetworkMenuService", ServiceRole.MAIN: "MainService"}
    name = names.get(role) if isinstance(role, ServiceRole) else None
    if role == ServiceRole.LOGIN and getattr(connection, "game", None) in SIX_GAMES:
        name = "LoginServicePES6"
    identity = getattr(connection, "session_id", None)
    remote = getattr(connection, "peer_ip", None)
    if remote is None:
        address = getattr(connection, "peer_address", None)
        remote = str(address).rsplit(":", 1)[0].strip("[]") if address else None
    if name is None or identity is None or remote is None:
        return "-"
    return f"{name},{identity},{remote}"


def _record_context(record: logging.LogRecord) -> str:
    context = getattr(record, "connection", None)
    if context is None:
        context = LOG_CONTEXT.get()
    if record.name.startswith("uvicorn"):
        component = context if isinstance(context, str) and context.startswith("uvicorn") else "uvicorn"
        return component + (".access" if record.name == "uvicorn.access" else "")
    if context is None:
        return "-"
    return context if isinstance(context, str) else connection_context(context)


class ContextFilter(logging.Filter):
    """Capture task context before a record reaches console/file formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "legacy_context"):
            record.legacy_context = _record_context(record)
        return True


def log_timezone(value: str | tzinfo) -> tzinfo:
    """Resolve UTC, a fixed numeric offset, or an installed IANA timezone."""
    if isinstance(value, tzinfo):
        return value
    if value == "UTC":
        return timezone.utc
    if re.fullmatch(r"[+-]\d{2}:?\d{2}", value):
        digits = value[1:].replace(":", "")
        hours, minutes = int(digits[:2]), int(digits[2:])
        if hours > 23 or minutes > 59:
            raise ValueError(f"Invalid log timezone offset: {value}")
        return timezone(timedelta(hours=hours, minutes=minutes) * (-1 if value[0] == "-" else 1))
    return ZoneInfo(value)


def _event_message(record: logging.LogRecord) -> str:
    message = record.getMessage()
    if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
        status = int(str(record.args[-1]))
        try:
            message += " " + HTTPStatus(status).phrase
        except ValueError:
            pass  # Nonstandard status codes still retain the actual numeric code.
    if SEVERITY_PREFIX.match(message) or message.startswith(("[RECV ", "[SEND ", "[BLOWFISH]")):
        return message
    if record.levelno == logging.DEBUG:
        return "DEBUG: " + message
    if record.levelno >= logging.WARNING:
        return ("WARN: " if record.levelno == logging.WARNING else "ERROR: ") + message
    return message


class HistoricalFormatter(logging.Formatter):
    """Render ``YYYY-MM-DDTHH:MM:SS+0000 [context] message`` with UTC default.

    Continuation lines are tab-indented as in Twisted's text observer. Severity
    is not an envelope: ordinary messages have no INFO prefix. PacketFormatter
    text stays intact and legacy severity labels are never duplicated.
    """

    def __init__(self, *, timezone_: str | tzinfo = timezone.utc):
        super().__init__()
        self.timezone = log_timezone(timezone_)

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, self.timezone).strftime("%Y-%m-%dT%H:%M:%S%z")
        context = getattr(record, "legacy_context", None)
        if context is None:
            context = _record_context(record)
        message = _event_message(record)
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            message += "\n" + self.formatStack(record.stack_info)
        return f"{stamp} [{context}] " + message.replace("\n", "\n\t")


def format_hex_dump(payload: bytes, *, max_bytes: int | None = None, include_offset: bool = False) -> str:
    """Match upstream PacketFormatter's eight-byte rows and ASCII whitelist."""
    shown = payload if max_bytes is None else payload[:max_bytes]
    rows = []
    for start in range(0, len(shown), HEX_BYTES_PER_LINE):
        chunk = shown[start:start + HEX_BYTES_PER_LINE]
        hex_part = " ".join(f"{value:02x}" for value in chunk).ljust(HEX_FIELD_WIDTH)
        ascii_part = ASCII_REPLACEMENT.sub(b".", chunk).decode("ascii")
        prefix = f"{start:08x}  " if include_offset else ""
        rows.append(f"{prefix}{hex_part}  {ascii_part}")
    if max_bytes is not None and len(payload) > len(shown):
        rows.append(f"... {len(payload) - len(shown)} bytes omitted")
    return "\n".join(rows)


def _dump_payload(context: Any, packet_id: int, payload: bytes) -> str | None:
    settings = getattr(context, "settings", {})
    if not settings.get("Debug") or not settings.get("LogPayloads", True):
        return None
    if packet_id in SENSITIVE_PACKET_IDS and not settings.get("LogSensitivePayloads", False):
        return None
    max_dump = settings.get("MaxPayloadDump")
    return format_hex_dump(payload, max_bytes=int(max_dump) if max_dump else None)


def _detail(direction: str, context: Any, packet_id: int, payload: bytes, counter: int) -> str:
    profile = getattr(context, "selected_profile", None)
    name = str(profile.name) if profile is not None else ""
    message = (f"[{direction} {{{name}}}]: Packet: id=0x{packet_id:04x}, "
               f"length=0x{len(payload):x}, count={counter}")
    dump = _dump_payload(context, packet_id, payload)
    return message + (f"\n{dump}" if dump else "")


def _log_packet(direction: str, context: Any, packet_id: int, payload: bytes, counter: int) -> None:
    if not getattr(context, "settings", {}).get("Debug") or not PACKET_LOGGER.isEnabledFor(logging.DEBUG):
        return
    PACKET_LOGGER.debug("%s", _detail(direction, context, packet_id, payload, counter), extra={"connection": context})


def log_incoming_packet(context: Any, packet_id: int, payload: bytes, counter: int) -> None:
    """Record the received frame before heartbeat handling or command dispatch."""
    _log_packet("RECV", context, packet_id, payload, counter)
    _log_auth_plaintext(context, packet_id, payload, counter)


def _log_auth_plaintext(context: Any, packet_id: int, payload: bytes, counter: int) -> None:
    settings = getattr(context, "settings", {})
    if packet_id != 0x3003 or not settings.get("LogSensitivePayloads", False):
        return
    if not settings.get("Debug") or not settings.get("LogPayloads", True) or not PACKET_LOGGER.isEnabledFor(logging.DEBUG):
        return

    if len(payload) not in AUTH_REQUEST_LENGTHS:
        return
    plaintext = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).decrypt(payload)
    dump = _dump_payload(context, packet_id, plaintext)
    PACKET_LOGGER.debug("[BLOWFISH]: Packet: id=0x%04x, length=0x%x, count=%d\n%s",
                        packet_id, len(payload), counter, dump, extra={"connection": context})


def log_outgoing_packet(context: Any, packet_id: int, payload: bytes, counter: int) -> None:
    """Record the actual outgoing counter with the recipient's connection context."""
    _log_packet("SEND", context, packet_id, payload, counter)


def get_debug() -> bool:
    """Return the runtime debug state used by the centralized configuration."""
    return _DEBUG


def set_debug(enabled: bool, *, announce: bool = True) -> None:
    """Change debug verbosity and emit the original SYSTEM announcement."""
    global _DEBUG
    _DEBUG = enabled
    level = logging.DEBUG if enabled else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        if getattr(handler, HANDLER_MARKER, False):
            handler.setLevel(level)
    if announce:
        LOGGER.info("SYSTEM: Debug is %s", "ON" if enabled else "OFF")


def configure_logging(log_file: Path | None, *, debug: bool, console: bool = True,
                      timezone_: str | tzinfo = timezone.utc, announce_debug: bool = True) -> None:
    """Install one append-mode console/file pipeline, including Uvicorn loggers."""
    formatter = HistoricalFormatter(timezone_=timezone_)
    close_logging()
    root = logging.getLogger()
    handlers: list[logging.Handler] = []
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(SizeRotatingLogFileHandler(
            log_file,
            max_bytes=_env_log_int("FIVESERVER_LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES),
            backup_count=_env_log_backup_count(),
        ))
    for handler in handlers:
        setattr(handler, HANDLER_MARKER, True)
        handler.setFormatter(formatter)
        handler.addFilter(ContextFilter())
        root.addHandler(handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.disabled = False
        logger.setLevel(logging.NOTSET)
    set_debug(debug, announce=announce_debug)


def _safe_flush(handler: logging.Handler) -> None:
    """Flush a console stream that may already be closed during interpreter exit."""
    try:
        handler.flush()
    except (OSError, ValueError):
        pass  # A closed stderr must not prevent file/shutdown logging.


def flush_logging() -> None:
    """Flush every root handler, including final shutdown/exception records."""
    for handler in logging.getLogger().handlers:
        _safe_flush(handler)


def close_logging() -> None:
    """Flush and close only handlers owned by FiveServer."""
    root = logging.getLogger()
    for handler in tuple(root.handlers):
        if getattr(handler, HANDLER_MARKER, False):
            _safe_flush(handler)
            root.removeHandler(handler)
            try:
                handler.close()
            except (OSError, ValueError):
                pass
