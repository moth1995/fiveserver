import asyncio
import base64
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

import asyncmy
import pytest
import yaml

from fiveserver import server as server_module
from fiveserver.logging_setup import close_logging
from fiveserver.protocol.dispatcher import GameId
from fiveserver.protocol.wire import encode_packet, read_packet
from tls_helpers import generate_pair


ROOT = Path(__file__).resolve().parents[1]


def _env():
    host = os.getenv("FIVESERVER_TEST_DB_HOST")
    if not host:
        pytest.skip("Set FIVESERVER_TEST_DB_HOST to run startup integration tests")
    return {
        "host": host,
        "port": int(os.getenv("FIVESERVER_TEST_DB_PORT", "3306")),
        "user": os.getenv("FIVESERVER_TEST_DB_USER", "root"),
        "password": os.getenv("FIVESERVER_TEST_DB_PASSWORD", ""),
    }


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _free_pair():
    while True:
        first = _free_port()
        if first >= 65535:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", first + 1))
            except OSError:
                continue
            return first


def _get(port, path, headers=None, context=None):
    scheme = "https" if context else "http"
    request = urllib.request.Request(f"{scheme}://127.0.0.1:{port}{path}", headers=headers or {})
    return urllib.request.urlopen(request, timeout=5, context=context).read()


async def _create_database(env, name, schema):
    connection = await asyncmy.connect(**env)
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8")
        await connection.select_db(name)
        async with connection.cursor() as cursor:
            for statement in (ROOT / schema).read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    await cursor.execute(statement)
    finally:
        connection.close()


async def _drop_database(env, name):
    connection = await asyncmy.connect(**env)
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        connection.close()


async def _packet(port, packet_id, payload=b""):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(encode_packet(packet_id, payload, 1))
        await writer.drain()
        return await asyncio.wait_for(read_packet(reader), timeout=5)
    finally:
        writer.close()
        await writer.wait_closed()


LEGACY_RECORD = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4} \[[^\]]+\] ")


def _assert_console_logging(output):
    assert "time=" not in output and "level=" not in output
    assert "INFO:     " not in output and "DEBUG:    " not in output
    lines = [line for line in output.splitlines() if line.strip()]
    records = [line for line in lines if LEGACY_RECORD.match(line)]
    assert records, output
    assert len(records) == len(lines), output  # No unformatted or duplicate envelope.
    assert sum("Started server process" in line for line in records) == 3
    assert sum("Uvicorn running on" in line for line in records) == 3
    assert sum("[uvicorn.registration] Uvicorn running on http://" in line for line in records) == 1
    assert sum("[uvicorn.admin] Uvicorn running on https://" in line for line in records) == 1
    assert sum("[uvicorn.statistics] Uvicorn running on http://127.0.0.1" in line for line in records) == 1
    assert any('"GET / HTTP/1.1" 200 OK' in line and "[uvicorn.registration.access]" in line for line in records)
    assert any('"GET / HTTP/1.1" 200 OK' in line and "[uvicorn.admin.access]" in line for line in records)
    assert any("Fiveserver 0.5.0 ready" in line for line in records)
    assert any("[NewsProtocol,1," in line and "Connection established" in line for line in records)
    assert any("[LoginService" in line and "Connection established" in line for line in records)
    assert not any("Fatal server error" in line for line in records)


async def _start_family(env, family, web_name, schema):
    temp = Path(tempfile.mkdtemp(prefix=f"fiveserver-{family}-"))
    dbname = f"fiveserver_smoke_{uuid.uuid4().hex[:10]}"
    process = None
    try:
        shutil.copytree(ROOT / web_name, temp / web_name)
        generate_pair(temp / "etc/keys")
        (temp / "etc/conf").mkdir(parents=True)
        (temp / "etc/data").mkdir(parents=True)
        (temp / "log").mkdir()
        (temp / "etc/data/banned.yaml").write_text("Banned: []\n", encoding="utf-8")
        await _create_database(env, dbname, schema)

        game_port, menu_port, login_port, main_port = (_free_port() for _ in range(4))
        web_port = _free_port()
        admin_port = _free_pair()
        admin = {
            "AdminPort": admin_port, "AdminUser": "admin", "AdminPassword": "secret",
            "KeysDirectory": "./etc/keys", "XslFile": f"./{web_name}/style.xsl",
            "FiveserverWebPort": web_port, "FiveserverLogFile": "./log/fiveserver.log",
        }
        game = "pes5" if family == "five" else "pes6"
        config = {
            "ServerIP": "127.0.0.1", "ListenOn": "127.0.0.1",
            "Lobbies": ["England", {"name": "Training", "type": "noStats"}],
            "GamePorts": {game: game_port},
            "NetworkServer": {
                "mainService": main_port, "networkMenuService": menu_port,
                "loginService": {game: login_port},
            },
            "WebInterface": {"port": web_port},
            "DB": {"name": dbname, **env, "readServers": [env["host"]],
                   "writeServers": [env["host"]], "sharePool": True,
                   "ConnectionPool": {"minConnections": 1, "maxConnections": 2,
                                      "keepAliveInterval": 60}},
            "BannedList": "./etc/data/banned.yaml",
            "Chat": {"bannedWords": [], "warningMessage": "removed"},
            "Roster": {"enforceHash": False, "compareHash": True},
            "ComputeRanksInterval": {"days": 1, "seconds": 0},
            "StoreSettings": True, "ShowStats": True,
            "Disconnects": {"CountAsLoss": {"Enabled": False,
                                             "Score": {"player": 0, "opponent": 3}}},
            "ServerName": "Smoke", "MaxUsers": 100, "Greeting": {"text": "Hello"},
        }
        config_name = "fiveserver.yaml" if family == "five" else "sixserver.yaml"
        admin_name = "admin.yaml" if family == "five" else "admin6.yaml"
        (temp / "etc/conf" / config_name).write_text(yaml.safe_dump(config), encoding="utf-8")
        (temp / "etc/conf" / admin_name).write_text(yaml.safe_dump(admin), encoding="utf-8")

        child_env = os.environ.copy()
        child_env["PYTHONPATH"] = str(ROOT / "src")
        child_env["FIVESERVER_PUBLIC_IP"] = "127.0.0.1"
        process = subprocess.Popen(
            [sys.executable, "-m", "fiveserver", family, "--config",
             str(temp / "etc/conf" / config_name)],
            cwd=temp, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

        deadline = time.time() + 20
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError("server exited during startup:\n" + process.stdout.read())
            try:
                _get(web_port, "/")
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("HTTP server did not start")

        assert b"Registration Form" in _get(web_port, "/")
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        authorization = "Basic " + base64.b64encode(b"admin:secret").decode()
        assert b"<adminService" in _get(admin_port, "/", {"Authorization": authorization}, context)
        assert b"<statsService" in _get(admin_port + 1, "/")
        log_path = temp / "log/fiveserver.log"
        assert log_path.exists()
        assert "Fiveserver 0.5.0 ready" in log_path.read_text(encoding="utf-8")
        assert b"Fiveserver 0.5.0 ready" in _get(
            admin_port, "/log?n=100", {"Authorization": authorization}, context,
        )

        assert (await _packet(game_port, 0x2006)).header.packet_id == 0x2007
        hello = await _packet(login_port, 0x3001)
        assert hello.header.packet_id == 0x3002 and hello.payload == bytes(16)
        log_content = log_path.read_text(encoding="utf-8")
        assert "[uvicorn.admin.access]" in log_content and '"GET / HTTP/1.1" 200 OK' in log_content
        assert "[NewsProtocol,1," in log_content and "Connection established" in log_content
        assert log_content.count("Started server process") == 3
        assert "time=" not in log_content and "level=" not in log_content
        process.terminate()
        output = process.communicate(timeout=15)[0] or ""
        _assert_console_logging(output)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        await _drop_database(env, dbname)
        shutil.rmtree(temp, ignore_errors=True)


def test_both_service_families_start_with_database_http_and_listeners():
    env = _env()

    async def scenario():
        await _start_family(env, "five", "web", "sql/schema.sql")
        await _start_family(env, "six", "web6", "sql/schema6.sql")

    asyncio.run(scenario())


def test_run_entrypoint_emits_graceful_shutdown_without_duplicate_errors(monkeypatch, tmp_path, capsys):
    env = _env()
    dbname = f"fiveserver_shutdown_{uuid.uuid4().hex[:10]}"

    async def scenario():
        shutil.copytree(ROOT / "web", tmp_path / "web")
        generate_pair(tmp_path / "etc/keys")
        (tmp_path / "etc/conf").mkdir(parents=True)
        (tmp_path / "etc/data").mkdir(parents=True)
        (tmp_path / "etc/data/banned.yaml").write_text("Banned: []\n", encoding="utf-8")
        (tmp_path / "log").mkdir()
        await _create_database(env, dbname, "sql/schema.sql")
        game_port, menu_port, login_port, main_port = (_free_port() for _ in range(4))
        web_port = _free_port()
        admin_port = _free_pair()
        (tmp_path / "etc/conf/admin.yaml").write_text(yaml.safe_dump({
            "AdminPort": admin_port, "AdminUser": "admin", "AdminPassword": "secret",
            "KeysDirectory": "./etc/keys", "XslFile": "./web/style.xsl",
            "FiveserverWebPort": web_port, "FiveserverLogFile": "./log/fiveserver.log",
        }), encoding="utf-8")
        (tmp_path / "etc/conf/fiveserver.yaml").write_text(yaml.safe_dump({
            "ServerIP": "127.0.0.1", "ListenOn": "127.0.0.1", "Lobbies": ["England"],
            "GamePorts": {"pes5": game_port},
            "NetworkServer": {"mainService": main_port, "networkMenuService": menu_port,
                              "loginService": {"pes5": login_port}},
            "WebInterface": {"port": web_port},
            "DB": {"name": dbname, **env, "readServers": [env["host"]],
                   "writeServers": [env["host"]], "sharePool": True,
                   "ConnectionPool": {"minConnections": 1, "maxConnections": 2,
                                      "keepAliveInterval": 60}},
            "BannedList": "./etc/data/banned.yaml",
            "Chat": {"bannedWords": [], "warningMessage": "removed"},
            "Roster": {"enforceHash": False, "compareHash": True},
            "ComputeRanksInterval": {"days": 1, "seconds": 0},
            "StoreSettings": True, "ShowStats": True,
            "Disconnects": {"CountAsLoss": {"Enabled": False,
                                             "Score": {"player": 0, "opponent": 3}}},
            "ServerName": "Smoke", "MaxUsers": 100, "Greeting": {"text": "Hello"},
        }), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        async def stop_web(*_servers, **_kwargs):
            return None

        monkeypatch.setattr(server_module, "_serve", stop_web)

        try:
            await server_module.run(tmp_path / "etc/conf/fiveserver.yaml", (GameId.PES5,))
        finally:
            await _drop_database(env, dbname)

    try:
        asyncio.run(scenario())
    finally:
        close_logging()
    output = capsys.readouterr().err
    records = [line for line in output.splitlines() if line.strip()]
    assert records and all(LEGACY_RECORD.match(line) for line in records), output
    assert any("Fiveserver 0.5.0 ready" in line for line in records)
    assert any("Fiveserver shutting down" in line for line in records)
    assert not any("Fatal server error" in line for line in records)
    assert "time=" not in output and "level=" not in output


