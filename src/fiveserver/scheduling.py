"""Managed rank and day-change jobs from the original server schedule."""


import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from .handlers.chat import broadcast_system
from .database import RankingRepository
from .handlers.lobbies import Lobby


LOGGER = logging.getLogger(__name__)
RANK_INITIAL_DELAY_SECONDS = 5
DAY_CHANGE_GRACE_SECONDS = 1
DEFAULT_RANK_DAYS = 1


def _rank_interval(settings: dict[str, Any]) -> float:
    configured = settings.get("ComputeRanksInterval", {})
    days = configured.get("days")
    seconds = configured.get("seconds")
    if (days is None and seconds is None) or (not days and not seconds):
        days, seconds = DEFAULT_RANK_DAYS, 0
    return timedelta(days=int(days or 0), seconds=int(seconds or 0)).total_seconds()


class Scheduler:
    """Own the rank and daily-message asyncio tasks."""
    def __init__(self, settings: dict[str, Any], lobbies: list[Lobby], repository: RankingRepository):
        self.settings = settings
        self.lobbies = lobbies
        self.repository = repository
        self.tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        """Start rank and day-change tasks."""
        self.tasks = [asyncio.create_task(self._rank_loop()), asyncio.create_task(self._day_loop())]

    async def close(self) -> None:
        """Cancel and await scheduler tasks during shutdown."""
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    async def _rank_loop(self) -> None:
        await asyncio.sleep(RANK_INITIAL_DELAY_SECONDS)
        while True:
            try:
                await self.repository.compute_ranks()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Rank computation failed")
                return  # Original server reschedules only after a successful interaction.
            seconds = _rank_interval(self.settings)
            interval = timedelta(seconds=seconds)
            LOGGER.info("Rank-compute interval is: %s", interval)
            LOGGER.info("Scheduling next rank-compute for: %s", datetime.now() + interval)
            await asyncio.sleep(seconds)

    async def _day_loop(self) -> None:
        while True:
            now = time.localtime()
            zone = time.tzname[now.tm_isdst]
            message = f"Date: {time.ctime()} {zone}"
            for lobby in self.lobbies:
                await broadcast_system(lobby, message)
                cutoff = datetime.now() - timedelta(days=5)
                async with lobby.lock:
                    lobby.chat_history[:] = [item for item in lobby.chat_history if item.timestamp > cutoff]
            LOGGER.info("Daily lobby message sent: %s", message)
            current = datetime.now()
            tomorrow = datetime(current.year, current.month, current.day) + timedelta(days=1)
            await asyncio.sleep((tomorrow - current).total_seconds() + DAY_CHANGE_GRACE_SECONDS)
