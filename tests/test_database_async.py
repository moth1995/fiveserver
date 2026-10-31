import asyncio
from datetime import datetime

from fiveserver.database import MySQLDatabase, Profile, UserRepository
from fiveserver.database import connection as connection_module


class FakeDatabase:
    def __init__(self):
        self.queries = []
        self.writes = []

    async def read(self, sql, *args):
        self.queries.append((sql, args))
        if "FROM users" in sql:
            return [(5, "player", "A" * 20, "abcd", None, datetime(2026, 1, 1))]
        if "FROM profiles" in sql and "user_id" in sql:
            return [(11, 5, 0, "Profile", 0, 0, 0, 250, 0, datetime(2026, 1, 1), 90)]
        if "SELECT id FROM profiles" in sql:
            return [(11,)]
        if "count(id)" in sql:
            return [(7,)]
        return []

    async def write(self, sql, *args):
        self.writes.append((sql, args))
        return 1


def test_original_user_profile_queries_and_write_fields():
    async def scenario():
        db = FakeDatabase()
        repo = UserRepository(db, pes6=False)
        user = await repo.by_hash("abcd")
        assert user is not None
        assert len(user.profiles) == 3
        assert user.profiles[0].name == "Profile"
        assert user.profiles[1].id == 0
        assert "ORDER BY updated_on ASC" in db.queries[1][0]
        assert await repo.games_played(11) == 7
        assert db.queries[-1][1] == (11, 11)
        profile = Profile(1, 5, 0, "New")
        await repo.save_profile(profile)
        assert profile.id == 11
        assert "ON DUPLICATE KEY UPDATE" in db.writes[0][0]
        assert db.writes[0][1][:4] == (0, 5, 1, "New")
    asyncio.run(scenario())


def test_original_keepalive_query_runs_once_per_unique_pool(caplog):
    class Cursor:
        def __init__(self, executed):
            self.executed = executed

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql):
            self.executed.append(sql)

    class Connection:
        def __init__(self, executed):
            self.executed = executed

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def cursor(self):
            return Cursor(self.executed)

    class Pool:
        def __init__(self):
            self.executed = []
            self.acquired = 0

        def acquire(self):
            self.acquired += 1
            return Connection(self.executed)

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def scenario():
        caplog.set_level("DEBUG", logger="fiveserver.database.connection")
        pool = Pool()
        database = MySQLDatabase([pool], [pool], keepalive_query="SELECT (1)", keepalive_interval=15)
        await database.keepalive_once()
        assert pool.executed == ["SELECT (1)"]
        assert pool.acquired == 1  # Shared read/write pools are queried once.
        database.start_keepalive()
        database.start_keepalive()
        assert database._keepalive_task is not None
        await database.close()  # Cancels the task before pool shutdown.

    asyncio.run(scenario())
    assert "DEBUG: KeepAliveManager:: keep-alive query: SELECT (1)" in caplog.text


def test_read_and_write_pools_use_their_configured_hosts(monkeypatch):
    class Pool:
        def __init__(self, host):
            self.host = host

        def close(self):
            pass

        async def wait_closed(self):
            pass

    calls = []

    async def create_pool(**kwargs):
        calls.append(kwargs["host"])
        return Pool(kwargs["host"])

    monkeypatch.setattr(connection_module.asyncmy, "create_pool", create_pool)

    async def scenario():
        config = {
            "user": "u", "password": "p", "name": "db", "port": 3306,
            "readServers": ["reader"], "writeServers": ["writer"], "sharePool": True,
        }
        database = await MySQLDatabase.connect(config)
        assert [pool.host for pool in database.read_pools] == ["reader"]
        assert [pool.host for pool in database.write_pools] == ["writer"]
        await database.close()

    asyncio.run(scenario())
    assert calls == ["reader", "writer"]


