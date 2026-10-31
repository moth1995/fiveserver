"""Domain records shared by repositories and handlers."""

from dataclasses import dataclass
from datetime import timedelta


@dataclass
class Profile:
    """Persistent player profile with game-visible statistics and settings."""
    index: int
    user_id: int
    id: int = 0
    name: str = ""
    rank: int = 0
    rating: int = 0
    points: int = 0
    disconnects: int = 0
    play_time: timedelta = timedelta()
    fav_player: int = 0
    fav_team: int = 0
    comment: str | bytes | None = None
    settings1: bytes | None = None
    settings2: bytes | None = None


@dataclass
class User:
    """Account record with credential hash, three profile slots, and active game info."""
    id: int
    username: str
    hash: str
    profiles: list[Profile]
    game_version: int | None = None
    roster_hash: bytes | None = None
    game_name: str | None = None


@dataclass(frozen=True)
class ProfileStats:
    """Aggregated match, goal, streak, and recent-team statistics."""
    wins: int = 0
    losses: int = 0
    draws: int = 0
    goals_scored: int = 0
    goals_allowed: int = 0
    streak_current: int = 0
    streak_best: int = 0
    teams: tuple[int, ...] = ()


@dataclass(frozen=True)
class MatchRecord:
    """Schema-neutral match result used by repository transactions."""
    home_players: tuple[int, ...]
    away_players: tuple[int, ...]
    score_home: int
    score_away: int
    team_home: int
    team_away: int
