import asyncio

from fiveserver.database import MatchRecord, UserRepository
from fiveserver.handlers.matches import rating_points
from fiveserver.database import ProfileStats


class Cursor:
    def __init__(self, fail_on=None):
        self.statements = []
        self.fail_on = fail_on

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, sql, args=()):
        self.statements.append((sql, args))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("database write failed")

    async def fetchone(self):
        if "LAST_INSERT_ID" in self.statements[-1][0]:
            return (42,)
        return (1, 2)


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.events = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def cursor(self):
        return self._cursor

    async def begin(self):
        self.events.append("begin")

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return self.connection


class Database:
    def __init__(self, connection):
        self.write_pools = [Pool(connection)]


def test_pes6_match_and_streak_writes_share_one_transaction():
    async def scenario():
        cursor = Cursor()
        connection = Connection(cursor)
        repo = UserRepository(Database(connection), pes6=True)
        result = await repo.store_match(MatchRecord((1, 2), (3,), 2, 1, 11, 12))
        assert result == 42
        assert connection.events == ["begin", "commit"]
        assert sum("INSERT INTO matches_played" in sql for sql, _ in cursor.statements) == 3
        assert sum("INSERT INTO streaks" in sql for sql, _ in cursor.statements) == 3

    asyncio.run(scenario())


def test_match_write_failure_rolls_back():
    async def scenario():
        cursor = Cursor(fail_on="INSERT INTO streaks")
        connection = Connection(cursor)
        repo = UserRepository(Database(connection), pes6=False)
        try:
            await repo.store_match(MatchRecord((1,), (2,), 1, 1, 11, 12))
        except RuntimeError:
            pass
        else:
            assert False, "write should have failed"
        assert connection.events == ["begin", "rollback"]
        assert not any("INSERT INTO matches_played" in sql for sql, _ in cursor.statements)

    asyncio.run(scenario())


def test_rating_matches_original_formula():
    assert rating_points(ProfileStats()) == 0
    assert rating_points(ProfileStats(wins=5)) == 563
    assert rating_points(ProfileStats(losses=5)) == 123


