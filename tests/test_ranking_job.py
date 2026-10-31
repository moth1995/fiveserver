import asyncio

from fiveserver.database import RankingRepository
from fiveserver.handlers.lobbies import configured_lobbies
from fiveserver.scheduling import Scheduler, _rank_interval

from test_match_storage import Connection, Database


class RankingCursor:
    def __init__(self):
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, sql, args=()):
        self.statements.append((sql, args))

    async def fetchall(self):
        return [(1, 500), (2, 500), (3, 400)]


def test_rank_transaction_preserves_ties_and_order():
    async def scenario():
        cursor = RankingCursor()
        connection = Connection(cursor)
        await RankingRepository(Database(connection)).compute_ranks()
        assert connection.events == ["begin", "commit"]
        updates = [args for sql, args in cursor.statements if sql.startswith("UPDATE profiles")]
        assert updates == [(1, 1), (1, 2), (3, 3)]

    asyncio.run(scenario())


def test_rank_interval_uses_existing_yaml_fields():
    assert _rank_interval({"ComputeRanksInterval": {"days": 1, "seconds": 0}}) == 86400
    assert _rank_interval({"ComputeRanksInterval": {"days": 0, "seconds": 30}}) == 30
    assert _rank_interval({"ComputeRanksInterval": {"days": 0, "seconds": 0}}) == 86400


def test_scheduler_writes_day_message_and_cancels_tasks():
    class Repository:
        async def compute_ranks(self):
            raise AssertionError("Initial rank delay should not have elapsed")

    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        scheduler = Scheduler({}, lobbies, Repository())
        scheduler.start()
        await asyncio.sleep(0.01)
        assert lobbies[0].chat_history[0].text.startswith(b"Date: ")
        await scheduler.close()
        assert scheduler.tasks == []

    asyncio.run(scenario())


