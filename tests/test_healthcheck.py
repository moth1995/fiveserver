import asyncio
from types import SimpleNamespace

from fiveserver import healthcheck


def test_healthcheck_verifies_http_and_database(monkeypatch):
    events = []

    class Response:
        def close(self):
            events.append("http-closed")

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql):
            events.append(sql)

        async def fetchone(self):
            return (1,)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            events.append("db-closed")

    async def connect(**kwargs):
        events.append(kwargs)
        return Connection()

    monkeypatch.setattr(healthcheck.urllib.request, "urlopen",
                        lambda url, timeout: (events.append(("url", url, timeout)), Response())[1])
    monkeypatch.setattr(healthcheck.asyncmy, "connect", connect)
    monkeypatch.setenv("FIVESERVER_DB_HOST", "five-db")
    monkeypatch.setenv("FIVESERVER_DB_PASSWORD", "secret")

    asyncio.run(healthcheck._check(SimpleNamespace(
        url="http://127.0.0.1:8180/", database="fiveserver", user=None, timeout=4,
    )))

    assert ("url", "http://127.0.0.1:8180/", 2) in events
    assert {"host": "five-db", "port": 3306, "user": "fiveserver",
            "password": "secret", "db": "fiveserver"} in events
    assert "SELECT 1" in events
    assert events[-1] == "db-closed"
