"""User, profile, settings, and match persistence."""

from datetime import timedelta
from dataclasses import dataclass
import logging
from typing import Any, Callable

from .connection import MySQLDatabase
from ..models import MatchRecord, Profile, ProfileStats, User


LOGGER = logging.getLogger(__name__)


def _pes5_profile(row: tuple[Any, ...]) -> Profile:
    """Read the PES5 projection including favourite player/team columns."""
    identity, user_id, index, name, fav_player, fav_team, rank, points, disconnects, _, seconds = row
    return Profile(index, user_id, identity, name, rank, 0, points, disconnects,
                   timedelta(seconds=seconds), fav_player, fav_team)


def _pes6_profile(row: tuple[Any, ...]) -> Profile:
    """Read the PES6 projection including rating and comment columns."""
    identity, user_id, index, name, rank, rating, points, disconnects, _, seconds, comment = row
    return Profile(index, user_id, identity, name, rank, rating, points, disconnects,
                   timedelta(seconds=seconds), comment=comment)


@dataclass(frozen=True)
class ProfileColumns:
    """Schema-specific projection and row reader, shared by both load paths."""
    select: str
    decode: Callable[[tuple[Any, ...]], Profile]


PES5_PROFILE_COLUMNS = ProfileColumns(
    "id,user_id,ordinal,name,fav_player,fav_team,`rank`,points,disconnects,updated_on,seconds_played", _pes5_profile,
)
PES6_PROFILE_COLUMNS = ProfileColumns(
    "id,user_id,ordinal,name,`rank`,rating,points,disconnects,updated_on,seconds_played,comment", _pes6_profile,
)


class UserRepository:
    """Persist users, profiles, settings, matches, and streaks with the original SQL."""
    def __init__(self, database: MySQLDatabase, *, pes6: bool):
        self.database = database
        self.pes6 = pes6
        self.profile_columns = PES6_PROFILE_COLUMNS if pes6 else PES5_PROFILE_COLUMNS

    async def store_match(self, match: MatchRecord) -> int:
        """Store a match, its participants, and all streak changes in one transaction."""
        pool = self.database.write_pools[0]
        async with pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    if self.pes6:
                        await cursor.execute(
                            "INSERT INTO matches (score_home, score_away, team_id_home, team_id_away) "
                            "VALUES (%s,%s,%s,%s)",
                            (match.score_home, match.score_away, match.team_home, match.team_away),
                        )
                    else:
                        await cursor.execute(
                            "INSERT INTO matches (profile_id_home, profile_id_away, score_home, score_away, "
                            "team_id_home, team_id_away) VALUES (%s,%s,%s,%s,%s,%s)",
                            (match.home_players[0], match.away_players[0], match.score_home,
                             match.score_away, match.team_home, match.team_away),
                        )
                    await cursor.execute("SELECT LAST_INSERT_ID()")
                    match_id = int((await cursor.fetchone())[0])
                    if self.pes6:
                        for profile_id in match.home_players:
                            await cursor.execute(
                                "INSERT INTO matches_played (match_id, profile_id, home) VALUES (%s, %s, 1)",
                                (match_id, profile_id),
                            )
                        for profile_id in match.away_players:
                            await cursor.execute(
                                "INSERT INTO matches_played (match_id, profile_id, home) VALUES (%s, %s, 0)",
                                (match_id, profile_id),
                            )
                    for profile_id, home in (
                        *((identity, True) for identity in match.home_players),
                        *((identity, False) for identity in match.away_players),
                    ):
                        win = (match.score_home > match.score_away if home
                               else match.score_away > match.score_home)
                        await cursor.execute("SELECT wins, best FROM streaks WHERE profile_id=%s", (profile_id,))
                        row = await cursor.fetchone()
                        wins, best = (int(row[0]), int(row[1])) if row else (0, 0)
                        wins = wins + 1 if win else 0
                        best = max(wins, best)
                        await cursor.execute(
                            "INSERT INTO streaks (profile_id, wins, best) VALUES (%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE wins=%s, best=%s",
                            (profile_id, wins, best, wins, best),
                        )
                await connection.commit()
                LOGGER.info(
                    "Match transaction committed: id=%d home=%s away=%s score=%d:%d",
                    match_id, match.home_players, match.away_players,
                    match.score_home, match.score_away,
                )
                return match_id
            except BaseException:
                LOGGER.exception("Match transaction rolled back")
                await connection.rollback()
                raise


    async def by_hash(self, value: str) -> User | None:
        """Load a user and the three profile slots selected by stored credential hash."""
        rows = await self.database.read(
            "SELECT id,username,serial,hash,reset_nonce,updated_on "
            "FROM users WHERE deleted = 0 AND hash = %s",
            value,
        )
        if not rows:
            return None
        user_id, username, _, user_hash, _, _ = rows[0]
        sql = (f"SELECT {self.profile_columns.select} "
               "FROM profiles WHERE deleted = 0 AND user_id = %s ORDER BY updated_on ASC")
        profile_rows = await self.database.read(sql, user_id)
        profiles = [Profile(index, user_id) for index in range(3)]
        for row in profile_rows:
            profile = self.profile_columns.decode(row)
            profiles[profile.index] = profile
        return User(user_id, username, user_hash, profiles)

    async def profile_name_exists(self, name: str) -> bool:
        """Return whether a non-deleted profile already uses the name."""
        rows = await self.database.read("SELECT id FROM profiles WHERE deleted = 0 AND name = %s", name)
        return bool(rows)

    async def save_profile(self, profile: Profile) -> Profile:
        """Insert or update a profile and refresh its generated ID."""
        seconds = int(profile.play_time.total_seconds())
        if self.pes6:
            sql = (
                "INSERT INTO profiles (id,user_id,ordinal,name,`rank`,rating,points,disconnects,seconds_played,comment) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                "deleted=0, user_id=%s, ordinal=%s, name=%s, `rank`=%s, rating=%s, "
                "points=%s, disconnects=%s, seconds_played=%s, comment=%s"
            )
            fields: tuple[Any, ...] = (profile.user_id, profile.index, profile.name, profile.rank, profile.rating,
                      profile.points, profile.disconnects, seconds, profile.comment)
        else:
            sql = (
                "INSERT INTO profiles (id,user_id,ordinal,name,fav_player,fav_team,`rank`,points,disconnects,seconds_played) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                "deleted=0, user_id=%s, ordinal=%s, name=%s, fav_player=%s, fav_team=%s, "
                "`rank`=%s, points=%s, disconnects=%s, seconds_played=%s"
            )
            fields = (profile.user_id, profile.index, profile.name, profile.fav_player, profile.fav_team,
                      profile.rank, profile.points, profile.disconnects, seconds)
        await self.database.write(sql, profile.id, *fields, *fields)
        # The original storeProfile re-reads by name after writing.
        rows = await self.database.read("SELECT id FROM profiles WHERE deleted = 0 AND name = %s", profile.name)
        profile.id = rows[0][0]
        return profile

    async def delete_profile(self, profile: Profile) -> None:
        """Soft-delete a profile without removing its database row."""
        await self.database.write("UPDATE profiles SET deleted = 1 WHERE id = %s", profile.id)

    async def games_played(self, profile_id: int) -> int:
        """Count played matches using the PES5 or PES6 schema."""
        if self.pes6:
            rows = await self.database.read("SELECT count(id) FROM matches_played WHERE profile_id=%s", profile_id)
        else:
            rows = await self.database.read(
                "SELECT count(id) FROM matches WHERE profile_id_home=%s OR profile_id_away=%s",
                profile_id,
                profile_id,
            )
        return int(rows[0][0])

    async def last_matches(self, profile_id: int) -> list[tuple[Any, ...]]:
        """Return up to ten PES5-profile results, newest first."""
        return await self.database.read(
            "SELECT opponent_profile_id, my_score, opp_score, my_team_id, opp_team_id, played_on "
            "FROM ("
            "SELECT id, profile_id_away AS opponent_profile_id, "
            "score_home AS my_score, score_away AS opp_score, "
            "team_id_home AS my_team_id, team_id_away AS opp_team_id, played_on "
            "FROM matches WHERE profile_id_home = %s "
            "UNION ALL "
            "SELECT id, profile_id_home AS opponent_profile_id, "
            "score_away AS my_score, score_home AS opp_score, "
            "team_id_away AS my_team_id, team_id_home AS opp_team_id, played_on "
            "FROM matches WHERE profile_id_away = %s "
            ") as t ORDER BY t.id DESC LIMIT 10",
            profile_id,
            profile_id,
        )

    async def profile_name(self, profile_id: int) -> str:
        """Resolve a profile ID to its current display name."""
        rows = await self.database.read(
            "SELECT name FROM profiles WHERE deleted = 0 AND id = %s",
            profile_id,
        )
        return str(rows[0][0]) if rows else "Profile not found"

    async def profile_by_id(self, profile_id: int) -> Profile | None:
        """Load a profile using the game-specific column layout."""
        sql = f"SELECT {self.profile_columns.select} FROM profiles WHERE deleted = 0 AND id = %s"
        rows = await self.database.read(sql, profile_id)
        if not rows:
            return None
        return self.profile_columns.decode(rows[0])

    async def profile_stats(self, profile_id: int) -> ProfileStats:
        """Compute wins, losses, draws, goals, streaks, and recent teams."""
        if self.pes6:
            counts = (
                "SELECT count(matches.id) FROM matches, matches_played WHERE matches.id=matches_played.match_id "
                "AND profile_id=%s AND ((home=1 and score_home>score_away) OR (home=0 and score_home<score_away))",
                "SELECT count(matches.id) FROM matches, matches_played WHERE matches.id=matches_played.match_id "
                "AND profile_id=%s AND ((home=1 and score_home<score_away) OR (home=0 and score_home>score_away))",
                "SELECT count(matches.id) FROM matches, matches_played WHERE matches.id=matches_played.match_id "
                "AND profile_id=%s AND score_home=score_away",
            )
            goal_queries = (
                "SELECT sum(score_home),sum(score_away) FROM matches, matches_played "
                "WHERE matches.id=matches_played.match_id AND profile_id=%s AND home=1",
                "SELECT sum(score_away),sum(score_home) FROM matches, matches_played "
                "WHERE matches.id=matches_played.match_id AND profile_id=%s AND home=0",
            )
        else:
            counts = (
                "SELECT count(id) FROM matches WHERE profile_id_home=%s AND score_home>score_away "
                "OR profile_id_away=%s AND score_home<score_away",
                "SELECT count(id) FROM matches WHERE profile_id_home=%s AND score_home<score_away "
                "OR profile_id_away=%s AND score_home>score_away",
                "SELECT count(id) FROM matches WHERE profile_id_home=%s AND score_home=score_away "
                "OR profile_id_away=%s AND score_home=score_away",
            )
            goal_queries = (
                "SELECT sum(score_home),sum(score_away) FROM matches WHERE profile_id_home=%s",
                "SELECT sum(score_away),sum(score_home) FROM matches WHERE profile_id_away=%s",
            )
        count_args = (profile_id,) if self.pes6 else (profile_id, profile_id)
        wins, losses, draws = [int((await self.database.read(sql, *count_args))[0][0]) for sql in counts]
        home, away = [
            (await self.database.read(sql, profile_id))[0] for sql in goal_queries
        ]
        streak_rows = await self.database.read("SELECT wins, best FROM streaks WHERE profile_id=%s", profile_id)
        current, best = streak_rows[0] if streak_rows else (0, 0)
        teams: tuple[int, ...] = ()
        if self.pes6:
            rows = await self.database.read(
                "SELECT match_id, team_id_home, team_id_away, home FROM matches_played, matches "
                "WHERE profile_id=%s AND matches.id=match_id ORDER BY match_id DESC LIMIT %s",
                profile_id, 5,
            )
            teams = tuple(row[1] if row[3] else row[2] for row in rows)
        return ProfileStats(
            wins, losses, draws,
            int(home[0] or 0) + int(away[0] or 0),
            int(home[1] or 0) + int(away[1] or 0),
            int(current), int(best), teams,
        )

    async def profile_settings(self, profile_id: int) -> tuple[bytes | None, bytes | None]:
        """Load the two compressed settings blobs for a profile."""
        rows = await self.database.read(
            "SELECT settings1, settings2 FROM settings WHERE profile_id=%s", profile_id
        )
        return (rows[0][0], rows[0][1]) if rows else (None, None)

    async def store_profile_settings(self, profile: Profile) -> None:
        """Persist compressed settings blobs for a profile."""
        await self.database.write(
            "INSERT INTO settings (profile_id, settings1, settings2) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE settings1=%s, settings2=%s",
            profile.id, profile.settings1, profile.settings2,
            profile.settings1, profile.settings2,
        )
