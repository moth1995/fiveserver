import asyncio
import base64
import ssl
from datetime import datetime
from pathlib import Path

import uvicorn
import yaml
from starlette.requests import Request

from fiveserver.web.admin import create_admin_app

from tls_helpers import generate_pair


class View:
    settings = {"MaxUsers": 100, "Debug": False, "StoreSettings": True, "Roster": {}}
    server_ip = "127.0.0.1"
    sessions = {}
    lobbies = []
    banned_networks = ()
    started_at = datetime(2026, 1, 1)


class Repository:
    async def users(self, offset, limit):
        return 1, [(1, "player", "serial", "hash", None, None)]

    async def profiles_page(self, offset, limit):
        return 0, []


async def get(port, path, authorization=None, ssl_context=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ssl_context)
    header = f"Authorization: {authorization}\r\n" if authorization else ""
    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n{header}Connection: close\r\n\r\n".encode())
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response


def test_admin_auth_and_public_xml_listener():
    async def scenario():
        config = {
            "AdminUser": "admin", "AdminPassword": "secret",
            "XslFile": str(Path("web/style.xsl")), "FiveserverWebPort": 8180,
            "FiveserverLogFile": "log/fiveserver.log",
        }
        admin = create_admin_app(View(), Repository(), config)
        stats = create_admin_app(View(), Repository(), config, public=True)
        servers = [uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="error"))
                   for app in (admin, stats)]
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        try:
            for _ in range(100):
                if all(server.started for server in servers):
                    break
                await asyncio.sleep(0.01)
            assert all(server.started for server in servers)
            admin_port, stats_port = [server.servers[0].sockets[0].getsockname()[1] for server in servers]
            assert b"401 Unauthorized" in await get(admin_port, "/")
            assert b"403 Forbidden" in await get(admin_port, "/", "Basic " + base64.b64encode(b"admin:wrong").decode())
            valid = "Basic " + base64.b64encode(b"admin:secret").decode()
            assert b"<adminService" in await get(admin_port, "/", valid)
            assert b"<users" in await get(admin_port, "/users", valid)
            assert b"<processInfo" in await get(admin_port, "/ps", valid)
            assert b"<statsService" in await get(stats_port, "/")
            assert b"404 Not Found" in await get(stats_port, "/userlock")
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks)

    asyncio.run(scenario())


def test_admin_ban_routes_update_original_yaml_format(tmp_path):
    async def scenario():
        banned_path = tmp_path / "banned.yaml"
        banned_path.write_text("Banned: []\n", encoding="utf-8")
        view = View()
        view.settings = {**View.settings, "BannedList": str(banned_path)}
        config = {
            "AdminUser": "admin", "AdminPassword": "secret",
            "XslFile": "web/style.xsl", "FiveserverWebPort": 8180,
            "FiveserverLogFile": "log/fiveserver.log",
        }
        app = create_admin_app(view, Repository(), config)

        def make_request(path, data):
            async def receive():
                return {"type": "http.request", "body": data, "more_body": False}

            return Request({"type": "http", "method": "POST", "path": path, "headers": []}, receive)

        add = next(route.endpoint for route in app.routes
                   if getattr(route, "path", None) == "/ban-add" and "POST" in route.methods)
        remove = next(route.endpoint for route in app.routes
                      if getattr(route, "path", None) == "/ban-remove" and "POST" in route.methods)
        assert (await add(make_request("/ban-add", b"entry=192.0.2.0%2F24"))).status_code == 200
        assert yaml.safe_load(banned_path.read_text(encoding="utf-8")) == {"Banned": ["192.0.2.0/24"]}
        assert str(view.banned_networks[0]) == "192.0.2.0/24"
        assert (await remove(make_request("/ban-remove", b"entry=192.0.2.0%2F24"))).status_code == 200
        assert yaml.safe_load(banned_path.read_text(encoding="utf-8")) == {"Banned": []}

    asyncio.run(scenario())


def test_admin_debug_endpoint_changes_runtime_logging(tmp_path):
    view = View()
    view.settings = dict(View.settings)
    config = {
        "AdminUser": "admin", "AdminPassword": "secret", "XslFile": "web/style.xsl",
        "FiveserverWebPort": 8180, "FiveserverLogFile": str(tmp_path / "server.log"),
    }
    app = create_admin_app(view, Repository(), config)
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", None) == "/debug" and "POST" in route.methods)

    def request(value):
        async def receive():
            return {"type": "http.request", "body": f"debug={value}".encode(), "more_body": False}

        return Request({"type": "http", "method": "POST", "path": "/debug", "headers": []}, receive)

    async def scenario():
        from fiveserver.logging_setup import get_debug

        try:
            enabled = await endpoint(request("true"))
            assert b'enabled="True"' in enabled.body
            assert view.settings["Debug"] is True and get_debug() is True
            disabled = await endpoint(request("false"))
            assert b'enabled="False"' in disabled.body
            assert view.settings["Debug"] is False and get_debug() is False
        finally:
            from fiveserver.logging_setup import set_debug

            set_debug(False, announce=False)

    asyncio.run(scenario())


def test_admin_https_uses_generated_certificate_files(tmp_path):
    keys = tmp_path / "etc/keys"
    generate_pair(keys)

    async def scenario():
        config = {"AdminUser": "admin", "AdminPassword": "secret", "XslFile": "web/style.xsl",
                  "FiveserverWebPort": 8180, "FiveserverLogFile": "log/fiveserver.log"}
        app = create_admin_app(View(), Repository(), config)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=0, lifespan="off", log_level="error",
            ssl_certfile=str(keys / "servercert.pem"), ssl_keyfile=str(keys / "serverkey.pem"),
        ))
        task = asyncio.create_task(server.serve())
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started
            port = server.servers[0].sockets[0].getsockname()[1]
            context = ssl.create_default_context(cafile=str(keys / "servercert.pem"))
            valid = "Basic " + base64.b64encode(b"admin:secret").decode()
            assert b"<adminService" in await get(port, "/", valid, context)
        finally:
            server.should_exit = True
            await task

    asyncio.run(scenario())


