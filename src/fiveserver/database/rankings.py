"""Transactional rank recomputation."""

import logging

from .connection import MySQLDatabase


LOGGER = logging.getLogger(__name__)


class RankingRepository:
    """Recompute profile ranks transactionally from points and play time."""
    def __init__(self, database: MySQLDatabase):
        self.database = database

    async def compute_ranks(self) -> None:
        """Assign competition ranks in descending points order, preserving ties."""
        updated = 0
        pool = self.database.write_pools[0]
        async with pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    rank = count = 1
                    last_points: int | None = None
                    limit, offset = 50, 0
                    while True:
                        await cursor.execute(
                            "SELECT id, points FROM profiles ORDER BY points DESC, seconds_played DESC "
                            "LIMIT %s OFFSET %s", (limit, offset),
                        )
                        rows = await cursor.fetchall()
                        for profile_id, points in rows:
                            if last_points is not None and last_points > points:
                                rank = count
                            await cursor.execute("UPDATE profiles SET `rank`=%s WHERE id=%s", (rank, profile_id))
                            last_points = points
                            count += 1
                            updated += 1
                        if len(rows) < limit:
                            break
                        offset += limit
                await connection.commit()
                LOGGER.info("NOTICE: Ranks successfully computed for all profiles.")
                LOGGER.debug("Rank computation complete: profiles=%d", updated)
            except BaseException:
                LOGGER.exception("Rank computation failed")
                await connection.rollback()
                raise
