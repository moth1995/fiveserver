"""Async MySQL pool management."""

import asyncio
from typing import Any, Self
import logging

import asyncmy  # type: ignore[import-untyped]

LOGGER = logging.getLogger(__name__)
KEEPALIVE_QUERY = "SELECT (1)"
KEEPALIVE_INTERVAL = 60
MIN_KEEPALIVE_INTERVAL = 15


class MySQLDatabase:
    """Manage asyncmy read and write pools for the original schema."""
    def __init__(self, read_pools: list[Any], write_pools: list[Any], *,
                 keepalive_query: str = KEEPALIVE_QUERY, keepalive_interval: int = KEEPALIVE_INTERVAL):
        self.read_pools = read_pools
        self.write_pools = write_pools
        self.keepalive_query = keepalive_query
        self.keepalive_interval = int(keepalive_interval)
        if self.keepalive_interval < MIN_KEEPALIVE_INTERVAL:
            raise ValueError(f"keepAliveInterval must be >= {MIN_KEEPALIVE_INTERVAL} seconds")
        self._keepalive_task: asyncio.Task[None] | None = None

    @classmethod
    async def connect(cls, config: dict[str, Any]) -> Self:
        """Create pools from the YAML DB section, using the configured read and write hosts."""
        options: dict[str, Any] = {
            "user": config["user"],
            "password": config["password"],
            "db": config["name"],
            "port": int(config.get("port", 3306)),
            "charset": "utf8",
            "autocommit": True,
        }
        pool_config: dict[str, Any] = config.get("ConnectionPool", {})
        options["minsize"] = int(pool_config.get("minConnections", 3))
        options["maxsize"] = int(pool_config.get("maxConnections", 5))
        keepalive_query = str(pool_config.get("keepAliveQuery", KEEPALIVE_QUERY))
        keepalive_interval = int(pool_config.get("keepAliveInterval", KEEPALIVE_INTERVAL))
        reads: list[Any] = []
        writes: list[Any] = []
        try:
            for host in config["readServers"]:
                reads.append(await asyncmy.create_pool(host=str(host), **options))
            if config.get("sharePool") and config["readServers"] == config["writeServers"]:
                writes = reads
            else:
                # The original DatabaseConfig.getWritePool accidentally used
                # readServers even when separate write hosts were configured.
                # The migrated server intentionally honors writeServers.
                for host in config["writeServers"]:
                    writes.append(await asyncmy.create_pool(host=str(host), **options))
            return cls(reads, writes, keepalive_query=keepalive_query,
                       keepalive_interval=keepalive_interval)
        except BaseException:
            for pool in reads + writes:
                pool.close()
                await pool.wait_closed()
            raise

    def start_keepalive(self) -> None:
        """Start the original periodic ``SELECT (1)`` pool-health task once."""
        if self._keepalive_task is None:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def keepalive_once(self) -> None:
        """Run one keep-alive query against every unique read/write pool."""
        LOGGER.debug("DEBUG: KeepAliveManager:: keep-alive query: %s", self.keepalive_query)
        for pool in {id(pool): pool for pool in self.read_pools + self.write_pools}.values():
            async with pool.acquire() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(self.keepalive_query)

    async def _keepalive_loop(self) -> None:
        while True:
            await asyncio.sleep(self.keepalive_interval)
            try:
                await self.keepalive_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("ERROR: keep-alive query failed")

    async def close(self) -> None:
        """Stop pool health checks and close every read and write pool."""
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None
        for pool in {id(pool): pool for pool in self.read_pools + self.write_pools}.values():
            pool.close()
            await pool.wait_closed()

    async def read(self, sql: str, *args: object) -> list[tuple[Any, ...]]:
        # The original WeightedPool.getPoolItem always chooses its first entry.
        """Execute a read query on the first configured read pool."""
        pool = self.read_pools[0]
        LOGGER.debug("dbRead: %s", sql)  # Never log bound credentials or query parameters.
        try:
            async with pool.acquire() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(sql, args)
                    return list(await cursor.fetchall())
        except Exception:
            LOGGER.exception("ERROR: error in DB retrieval")
            raise

    async def write(self, sql: str, *args: object) -> int:
        """Execute a write statement on the first configured write pool."""
        pool = self.write_pools[0]
        LOGGER.debug("dbWrite: %s", sql)
        try:
            async with pool.acquire() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(sql, args)
                    return cursor.rowcount
        except Exception:
            LOGGER.exception("ERROR: error in DB write")
            raise
