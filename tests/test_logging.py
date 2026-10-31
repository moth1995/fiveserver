"""Central legacy-format logging, packet dumps, Uvicorn routing, and admin logs."""

import asyncio
from datetime import datetime, timezone
import logging
import re

import pytest

from fiveserver.database import Profile
from fiveserver.logging_setup import (
    ContextFilter, HistoricalFormatter, SizeRotatingLogFileHandler, configure_logging,
    connection_context, flush_logging, format_hex_dump, log_incoming_packet,
    log_outgoing_packet, logging_context, set_debug,
)
from fiveserver.protocol.dispatcher import GameId, ServiceRole
from fiveserver.protocol.wire import encode_packet, read_packet
from fiveserver.server import FiveServer
from fiveserver.web.admin import _tail_log, create_admin_app


class Context:
    game = GameId.PES5
    role = ServiceRole.MAIN
    session_id = 7
    peer_ip = "192.0.2.10"
    peer_address = "192.0.2.10:5555"
    settings = {"Debug": True}
    selected_profile = Profile(0, 1, 2, "Player")

    def __init__(self, **settings):
        self.settings = {"Debug": True, **settings}


def record_at(when, *, name="fiveserver.test", level=logging.INFO, message="message", args=(), exc_info=None):
    record = logging.LogRecord(name, level, "", 0, message, args, exc_info)
    record.created = when
    record.legacy_context = "-"
    return record


def test_timestamp_is_iso_without_fraction_and_includes_numeric_offset():
    when = datetime(2026, 10, 30, 15, 17, 21, 999999, tzinfo=timezone.utc).timestamp()
    rendered = HistoricalFormatter().format(record_at(when))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+0000 \[-\] message", rendered)
    assert "time=" not in rendered and "level=" not in rendered and rendered.count(".") == 0


@pytest.mark.parametrize("offset,expected", [
    ("+05:30", "2026-01-01T05:30:00+0530"),
    ("-03:00", "2025-12-31T21:00:00-0300"),
    ("+0000", "2026-01-01T00:00:00+0000"),
    ("UTC", "2026-01-01T00:00:00+0000"),
])
def test_configured_timezone_offsets_are_applied(offset, expected):
    when = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp()
    rendered = HistoricalFormatter(timezone_=offset).format(record_at(when))
    assert rendered.startswith(expected + " [-] message")


def test_connection_context_uses_real_protocol_identity_number_and_ip():
    assert connection_context(Context()) == "MainService,7,192.0.2.10"
    news = Context()
    news.role, news.session_id = ServiceRole.NEWS, 12
    assert connection_context(news) == "NewsProtocol,12,192.0.2.10"
    login6 = Context()
    login6.role, login6.game = ServiceRole.LOGIN, GameId.PES6
    assert connection_context(login6) == "LoginServicePES6,7,192.0.2.10"
    absent = object()
    assert connection_context(absent) == "-"


def test_normal_debug_warning_and_exception_messages_keep_original_conventions():
    formatter = HistoricalFormatter()
    assert formatter.format(record_at(0, message="plain")) .endswith("[-] plain")
    assert formatter.format(record_at(0, level=logging.DEBUG, message="detail")).endswith("[-] DEBUG: detail")
    assert formatter.format(record_at(0, level=logging.WARNING, message="problem")).endswith("[-] WARN: problem")
    assert formatter.format(record_at(0, level=logging.WARNING, message="WARN: already labelled")).endswith("[-] WARN: already labelled")
    try:
        raise ValueError("synthetic failure")
    except ValueError as error:
        exc_info = (type(error), error, error.__traceback__)
    rendered = formatter.format(record_at(0, level=logging.ERROR, message="Request failed", exc_info=exc_info))
    assert rendered.startswith("1970-01-01T00:00:00+0000 [-] ERROR: Request failed\n\tTraceback")
    assert "\n\tTraceback (most recent call last):" in rendered
    assert "\n\tValueError: synthetic failure" in rendered


def test_multiline_messages_are_indented_but_packet_rows_are_unchanged():
    record = record_at(0, level=logging.DEBUG, message="[RECV {Player}]: Packet: id=0x3003, length=0x4, count=12\naa bb  ..")
    rendered = HistoricalFormatter().format(record)
    assert rendered == (
        "1970-01-01T00:00:00+0000 [-] [RECV {Player}]: Packet: id=0x3003, length=0x4, count=12"
        "\n\taa bb  .."
    )


def test_uvicorn_and_access_records_use_central_context_and_keep_client_details():
    formatter = HistoricalFormatter()
    error = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, "Uvicorn running on http://0.0.0.0:8180", (), None)
    error.legacy_context = "uvicorn.admin"
    rendered = formatter.format(error)
    assert "[uvicorn.admin]" in rendered and "Uvicorn running on http://0.0.0.0:8180" in rendered
    access = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:59294", "POST", "/register", "1.1", 200), None,
    )
    access.legacy_context = "uvicorn.registration.access"
    rendered = formatter.format(access)
    assert '[uvicorn.registration.access]' in rendered
    assert '127.0.0.1:59294 - "POST /register HTTP/1.1" 200 OK' in rendered
    assert not rendered.startswith("INFO:")


def test_context_filter_reads_task_local_context_and_never_leaks_between_tasks():
    formatter = HistoricalFormatter()

    async def worker(session, delay):
        connection = Context()
        connection.session_id = session
        with logging_context(connection):
            await asyncio.sleep(delay)
            record = logging.LogRecord("fiveserver.test", logging.INFO, "", 0, f"client {session}", (), None)
            assert ContextFilter().filter(record)
            return formatter.format(record)

    async def scenario():
        return await asyncio.gather(worker(1, 0.02), worker(2, 0.01), worker(3, 0))

    rendered = asyncio.run(scenario())
    assert [line.split("]")[0].rsplit(",", 2)[1] for line in rendered] == ["1", "2", "3"]
    assert all(connection_context(Context()) == "MainService,7,192.0.2.10" for _ in rendered)


def test_exception_traceback_reaches_file_and_console_equivalently(tmp_path, capsys):
    log_file = tmp_path / "errors.log"
    try:
        configure_logging(log_file, debug=True, announce_debug=False)
        stream = logging.getLogger("fiveserver.test").handlers
        assert not stream  # No per-module handler is installed.
        try:
            raise ValueError("synthetic failure")
        except ValueError:
            logging.getLogger("fiveserver.test").exception("Request failed")
        flush_logging()
        content = log_file.read_text(encoding="utf-8")
        assert "ERROR: Request failed" in content
        assert "\n\tTraceback" in content and "synthetic failure" in content
        assert "time=" not in content and "level=" not in content
    finally:
        configure_logging(None, debug=False, announce_debug=False)


def test_console_and_file_are_identical_and_handlers_do_not_duplicate(tmp_path, capsys):
    log_file = tmp_path / "server.log"
    try:
        logger = logging.getLogger("fiveserver.test")
        configure_logging(log_file, debug=True, announce_debug=False)
        assert sum(getattr(h, "_fiveserver_handler", False) for h in logging.getLogger().handlers) == 2
        logger.info("first entry")
        flush_logging()
        configure_logging(log_file, debug=True, announce_debug=False)
        assert sum(getattr(h, "_fiveserver_handler", False) for h in logging.getLogger().handlers) == 2
        logger.info("second entry")
        logger.debug("debug entry")
        flush_logging()
        written = log_file.read_text(encoding="utf-8").splitlines()
        console = capsys.readouterr().err.splitlines()
        assert written == console
        assert sum("first entry" in line for line in written) == 1  # Append, never truncate.
        assert sum("second entry" in line for line in written) == 1  # No duplicate handlers.
        assert any("DEBUG: debug entry" in line for line in written)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            assert logging.getLogger(name).handlers == []
            assert logging.getLogger(name).propagate is True
    finally:
        configure_logging(None, debug=False, announce_debug=False)


def test_runtime_debug_changes_emit_notice_and_gate_debug_records(caplog):
    try:
        configure_logging(None, debug=False, announce_debug=False)
        caplog.set_level(logging.DEBUG)
        set_debug(True)
        logging.getLogger("fiveserver.test").debug("detail")
        assert any("SYSTEM: Debug is ON" in r.getMessage() for r in caplog.records)
        set_debug(False)
        assert any("SYSTEM: Debug is OFF" in r.getMessage() for r in caplog.records)
        caplog.clear()
        logging.getLogger("fiveserver.test").debug("hidden detail")
        assert not any("hidden detail" in r.getMessage() for r in caplog.records)
    finally:
        configure_logging(None, debug=False, announce_debug=False)


def test_upstream_packet_formatter_layout_padding_and_zero_length():
    assert format_hex_dump(b"") == ""
    assert format_hex_dump(b"\0\0\0\0") == "00 00 00 00              ...."
    assert format_hex_dump(b"\x69\xfa\xca\x3c") == "69 fa ca 3c              i..<"
    assert format_hex_dump(b"ABCDEFGHI") == "41 42 43 44 45 46 47 48  ABCDEFGH\n49                       I"
    # Upstream whitelist keeps word characters and .,!?'"/<>- and replaces the rest.
    assert format_hex_dump(b"A@?/") == "41 40 3f 2f              A.?/"
    assert format_hex_dump(b"\x01\x02", max_bytes=1) == "01                       .\n... 1 bytes omitted"


def test_packet_logging_has_exact_header_dump_counter_and_context(caplog):
    caplog.set_level(logging.DEBUG, logger="fiveserver.packet")
    log_incoming_packet(Context(), 0x2006, b"", 12)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert text == "[RECV {Player}]: Packet: id=0x2006, length=0x0, count=12"
    caplog.clear()
    log_outgoing_packet(Context(), 0x2007, b"\x69\xfa\xca\x3c", 13)
    records = caplog.records
    assert records[0].getMessage() == "[SEND {Player}]: Packet: id=0x2007, length=0x4, count=13\n69 fa ca 3c              i..<"
    assert connection_context(records[0].connection) == "MainService,7,192.0.2.10"


def test_sensitive_payload_redaction_payload_controls_and_debug_gate(caplog):
    caplog.set_level(logging.DEBUG, logger="fiveserver.packet")
    log_incoming_packet(Context(), 0x3003, b"secret", 5)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "id=0x3003" in text and "length=0x6" in text and "count=5" in text
    assert "73 65 63 72 65 74" not in text
    caplog.clear()
    log_incoming_packet(Context(LogSensitivePayloads=True), 0x3003, b"secret", 5)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "73 65 63 72 65 74" in text
    caplog.clear()
    log_incoming_packet(Context(LogPayloads=False), 0x2006, b"abcd", 1)
    assert "61 62 63 64" not in "\n".join(record.getMessage() for record in caplog.records)
    caplog.clear()
    log_incoming_packet(Context(MaxPayloadDump=1), 0x2006, b"abcd", 1)
    assert "... 3 bytes omitted" in "\n".join(record.getMessage() for record in caplog.records)
    caplog.clear()
    log_incoming_packet(Context(Debug=False), 0x2006, b"", 1)
    assert caplog.records == []


def test_admin_log_endpoint_reads_configured_file_without_full_load(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.write_bytes(b"line one\nline two\n")

    class View:
        settings = {"MaxUsers": 100, "Debug": False, "StoreSettings": True, "Roster": {}}
        server_ip = "127.0.0.1"
        sessions = {}
        lobbies = []
        banned_networks = ()
        started_at = datetime(2026, 1, 1)

    config = {
        "AdminUser": "admin", "AdminPassword": "secret", "XslFile": "web/style.xsl",
        "FiveserverWebPort": 8180, "FiveserverLogFile": str(log_file),
    }
    app = create_admin_app(View(), object(), config)
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", None) == "/log" and "GET" in route.methods)

    async def scenario():
        response = await endpoint(n=2)
        assert response.status_code == 200
        assert response.body.endswith(b"line one\nline two\n")

    asyncio.run(scenario())
    assert _tail_log(log_file, 10) == "line one\nline two\n"


def test_real_listener_writes_legacy_packet_lines_with_connection_context(tmp_path):
    log_file = tmp_path / "packets.log"

    async def scenario():
        configure_logging(log_file, debug=True, announce_debug=False)
        settings = {
            "ListenOn": "127.0.0.1", "Debug": True,
            "Lobbies": ["England"], "GamePorts": {"pes5": 0},
            "NetworkServer": {"networkMenuService": 0, "mainService": 0,
                              "loginService": {"pes5": 0}},
            "Roster": {"enforceHash": False}, "ShowStats": False,
        }
        server = FiveServer(settings, (GameId.PES5,), "127.0.0.1")
        await server.start()
        try:
            port = server.listeners[0].sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(encode_packet(0x2006, b"", 1))
            await writer.drain()
            response = await asyncio.wait_for(read_packet(reader), timeout=5)
            assert response.header.packet_id == 0x2007
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.05)
        finally:
            await server.close()
            flush_logging()
            configure_logging(None, debug=False, announce_debug=False)
        return log_file.read_text(encoding="utf-8")

    content = asyncio.run(scenario())
    assert re.search(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+0000 \[NewsProtocol,1,127\.0\.0\.1\] "
                     r"\[RECV \{\}\]: Packet: id=0x2006, length=0x0, count=1$", content, re.MULTILINE)
    assert re.search(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+0000 \[NewsProtocol,1,127\.0\.0\.1\] "
                     r"\[SEND \{\}\]: Packet: id=0x2007, length=0x4, count=1$", content, re.MULTILINE)
    assert "Connection lost: Connection was closed cleanly." in content
    assert "[tcp]" not in content and "time=" not in content and "level=" not in content

def test_log_rotation_defaults_match_twisted(monkeypatch, tmp_path):
    log_file = tmp_path / "server.log"
    monkeypatch.delenv("FIVESERVER_LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("FIVESERVER_LOG_BACKUP_COUNT", raising=False)
    try:
        configure_logging(log_file, debug=False, announce_debug=False)
        handler = next(handler for handler in logging.getLogger().handlers
                       if isinstance(handler, SizeRotatingLogFileHandler))
        assert handler.max_bytes == 1_000_000
        assert handler.backup_count is None
    finally:
        configure_logging(None, debug=False, announce_debug=False)


def test_log_rotation_uses_environment_and_keeps_limited_backups(monkeypatch, tmp_path):
    log_file = tmp_path / "server.log"
    monkeypatch.setenv("FIVESERVER_LOG_MAX_BYTES", "250")
    monkeypatch.setenv("FIVESERVER_LOG_BACKUP_COUNT", "2")
    try:
        configure_logging(log_file, debug=False, announce_debug=False)
        logger = logging.getLogger("fiveserver.test")
        for index in range(20):
            logger.info("rotation line %02d %s", index, "x" * 120)
        flush_logging()
    finally:
        configure_logging(None, debug=False, announce_debug=False)

    assert log_file.exists()
    assert (tmp_path / "server.log.1").exists()
    assert (tmp_path / "server.log.2").exists()
    assert not (tmp_path / "server.log.3").exists()
    assert log_file.stat().st_size < 500
