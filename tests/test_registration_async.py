import asyncio
from pathlib import Path
from urllib.parse import urlencode

from starlette.requests import Request
import uvicorn

from fiveserver.web.registration import create_registration_app, parse_banned_networks


class FakeRegistrationRepository:
    def __init__(self):
        self.rows = {}
        self.saved = []

    async def find(self, column, value):
        return self.rows.get((column, value))

    async def store(self, *args):
        self.saved.append(args)


def make_request(form):
    data = urlencode(form).encode()
    async def receive():
        return {"type": "http.request", "body": data, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/register", "headers": [], "client": ("192.0.2.2", 1234)}, receive)


def test_registration_preserves_form_and_xml_responses():
    async def scenario():
        repo = FakeRegistrationRepository()
        app = create_registration_app(repo, Path("web"), parse_banned_networks(["192.0.2.0/24"]))
        get_page = next(route.endpoint for route in app.routes if getattr(route, "methods", None) == {"GET"})
        register = next(route.endpoint for route in app.routes if getattr(route, "methods", None) == {"POST"})
        assert b'name="serial"' in (await get_page("")).body
        form = {"serial": "A" * 20, "user": "player", "hash": "00" * 16, "nonce": ""}
        assert (await register("register", make_request(form))).status_code == 403
        app = create_registration_app(repo, Path("web"))
        register = next(route.endpoint for route in app.routes if getattr(route, "methods", None) == {"POST"})
        result = await register("register", make_request(form))
        assert result.status_code == 200
        assert b"SUCCESS: Registration complete" in result.body
        assert repo.saved[0][1:3] == ("player", "A" * 20)
        repo.rows[("username", "player")] = (1,)
        assert (await register("register", make_request(form))).status_code == 409
    asyncio.run(scenario())


def test_registration_runs_on_http_listener():
    async def scenario():
        app = create_registration_app(FakeRegistrationRepository(), Path("web"))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="error"))
        task = asyncio.create_task(server.serve())
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started
            port = server.servers[0].sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            assert b"200 OK" in response
            assert b"FiveServer Registration Form" in response
            writer.close()
            await writer.wait_closed()
        finally:
            server.should_exit = True
            await task

    asyncio.run(scenario())


