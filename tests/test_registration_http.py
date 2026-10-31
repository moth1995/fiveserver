import asyncio
from ipaddress import ip_network
from pathlib import Path
from urllib.parse import urlencode

from Crypto.Cipher import Blowfish
from starlette.requests import Request

from fiveserver.protocol.codecs import CIPHER_KEY
from fiveserver.web.registration import create_registration_app


class Repository:
    def __init__(self):
        self.rows = {}

    async def find(self, column, value):
        index = {"username": 1, "reset_nonce": 4, "hash": 3}[column]
        for row in self.rows.values():
            if str(row[index]) == value:
                return row
        return None

    async def store(self, user_id, username, serial, user_hash, nonce):
        user_id = user_id or (max(self.rows, default=0) + 1)
        self.rows[user_id] = (user_id, username, serial, user_hash, nonce, None)


def request(path, data=b"", client=("127.0.0.1", 1234)):
    async def receive():
        return {"type": "http.request", "body": data, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": path, "headers": [],
         "query_string": b"", "client": client},
        receive,
    )


def endpoints(app):
    get = next(route.endpoint for route in app.routes
               if getattr(route, "path", None) == "/{path:path}" and "GET" in route.methods)
    post = next(route.endpoint for route in app.routes
                if getattr(route, "path", None) == "/{path:path}" and "POST" in route.methods)
    return get, post


def test_registration_gets_and_posts_match_legacy_pages():
    async def scenario():
        repository = Repository()
        app = create_registration_app(repository, Path("web"))
        get, post = endpoints(app)

        root = await get("")
        assert root.status_code == 200 and b'name="user"' in root.body
        assert (await get("md5.js")).media_type == "text/javascript"
        assert (await get("xsl/style.xsl")).media_type == "text/xml"

        repository.rows[7] = (7, "old-user", "serial", "hash", "123456", None)
        modify = await get("modifyUser/123456")
        assert b'value="old-user"' in modify.body

        digest = bytes(range(16))
        form = urlencode({"serial": "serial", "user": "new-user", "hash": digest.hex(),
                          "nonce": "", "format": "html"}).encode()
        created = await post("register", request("/register", form))
        assert created.status_code == 200 and b"SUCCESS: Registration complete" in created.body
        assert len(repository.rows) == 2
        stored = next(row for row in repository.rows.values() if row[1] == "new-user")
        assert stored[3] == Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).encrypt(digest).hex()

        duplicate = await post("register", request("/register", form))
        assert duplicate.status_code == 409 and b"username taken" in duplicate.body

        bad = await post("register", request("/register", b"serial=x&user=y&hash=00&nonce="))
        assert bad.status_code == 400 and b"Invalid registration form" in bad.body

        repository.rows[7] = (7, "old-user", "serial", "hash", "123456", None)
        modify_form = urlencode({"serial": "serial", "user": "renamed", "hash": digest.hex(),
                                 "nonce": "123456"}).encode()
        updated = await post("register", request("/register", modify_form))
        assert updated.status_code == 200
        assert repository.rows[7][1] == "renamed"

        banned = create_registration_app(Repository(), Path("web"), (ip_network("127.0.0.0/8"),))
        _, banned_post = endpoints(banned)
        blocked = await banned_post("register", request("/register", form))
        assert blocked.status_code == 403 and b"IP is banned" in blocked.body

    asyncio.run(scenario())


