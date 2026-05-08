from __future__ import annotations

import os
from typing import Any

import pymysql
import pymysql.cursors

from flask import g, current_app

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def get_db() -> pymysql.connections.Connection:
    """Return the per-request MySQL connection, creating it if needed."""
    if "db" not in g:
        cfg = current_app.config["FS_CONFIG"]
        db_cfg: dict[str, Any] = cfg["DB"]

        host: str = os.environ.get(
            "DB_HOST", db_cfg.get("readServers", ["127.0.0.1"])[0]
        )
        port: int = int(os.environ.get("DB_PORT", db_cfg.get("port", 3306)))
        db_name: str = os.environ.get("DB_NAME", db_cfg["name"])
        db_user: str = os.environ.get("DB_USER", db_cfg["user"])
        db_pass: str = os.environ.get("DB_PASSWORD", db_cfg["password"])

        g.db = pymysql.connect(
            host=host,
            port=port,
            user=db_user,
            password=db_pass,
            database=db_name,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
    return g.db  # type: ignore[return-value]


def teardown_db(exception: BaseException | None) -> None:
    """Close the DB connection at end of request."""
    db: pymysql.connections.Connection | None = g.pop("db", None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------------------
# User queries  (ported from lib/fiveserver/data.py — UserData)
# ---------------------------------------------------------------------------

_USER_COLS = (
    "id, username, serial, hash, reset_nonce, updated_on, deleted, total_online_seconds"
)


def find_user_by_id(
    conn: pymysql.connections.Connection, user_id: int
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_USER_COLS} FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()  # type: ignore[return-value]


def find_user_by_username(
    conn: pymysql.connections.Connection, username: str
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_USER_COLS} FROM users WHERE deleted = 0 AND username = %s",
            (username,),
        )
        return cur.fetchone()  # type: ignore[return-value]


def find_user_by_hash(
    conn: pymysql.connections.Connection, hash_val: str
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_USER_COLS} FROM users WHERE deleted = 0 AND hash = %s",
            (hash_val,),
        )
        return cur.fetchone()  # type: ignore[return-value]


def find_user_by_nonce(
    conn: pymysql.connections.Connection, nonce: str
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_USER_COLS} FROM users WHERE deleted = 0 AND reset_nonce = %s",
            (nonce,),
        )
        return cur.fetchone()  # type: ignore[return-value]


def browse_users(
    conn: pymysql.connections.Connection,
    offset: int = 0,
    limit: int = 50,
    search: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Return (total_count, rows) for paginated user listing."""
    with conn.cursor() as cur:
        if search:
            cur.execute(
                "SELECT COUNT(id) AS n FROM users WHERE deleted = 0 AND username LIKE %s",
                (f"%{search}%",),
            )
            total: int = cur.fetchone()["n"]  # type: ignore[index]
            cur.execute(
                f"SELECT {_USER_COLS} FROM users WHERE deleted = 0 AND username LIKE %s "
                "ORDER BY username LIMIT %s OFFSET %s",
                (f"%{search}%", limit, offset),
            )
        else:
            cur.execute("SELECT COUNT(id) AS n FROM users WHERE deleted = 0")
            total = cur.fetchone()["n"]  # type: ignore[index]
            cur.execute(
                f"SELECT {_USER_COLS} FROM users WHERE deleted = 0 "
                "ORDER BY username LIMIT %s OFFSET %s",
                (limit, offset),
            )
        rows: list[dict[str, Any]] = cur.fetchall()  # type: ignore[assignment]
    return total, rows


def create_user(
    conn: pymysql.connections.Connection, username: str, serial: str, hash_val: str
) -> int:
    """Insert a new user row and return the new id."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, serial, hash) VALUES (%s, %s, %s)",
            (username, serial, hash_val),
        )
        return cur.lastrowid  # type: ignore[return-value]


def update_user(
    conn: pymysql.connections.Connection,
    user_id: int,
    username: str,
    serial: str,
    hash_val: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET username=%s, serial=%s, hash=%s, reset_nonce=NULL "
            "WHERE id = %s",
            (username, serial, hash_val, user_id),
        )


def lock_user(conn: pymysql.connections.Connection, user_id: int, nonce: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET reset_nonce=%s WHERE id = %s", (nonce, user_id))


def unlock_user(conn: pymysql.connections.Connection, user_id: int) -> None:
    """Clear reset_nonce, restoring full access to the account."""
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET reset_nonce = NULL WHERE id = %s", (user_id,))


def delete_user(conn: pymysql.connections.Connection, user_id: int) -> None:
    """Soft-delete: set deleted=1."""
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET deleted = 1 WHERE id = %s", (user_id,))


# ---------------------------------------------------------------------------
# Profile queries  (ported from lib/fiveserver/data.py — ProfileData)
# ---------------------------------------------------------------------------

_PROFILE_COLS = (
    "id, user_id, ordinal, name, fav_player, fav_team, `rank`, "
    "points, disconnects, updated_on, seconds_played"
)


def get_profiles_for_user(
    conn: pymysql.connections.Connection, user_id: int
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_PROFILE_COLS} FROM profiles "
            "WHERE deleted = 0 AND user_id = %s ORDER BY ordinal",
            (user_id,),
        )
        return cur.fetchall()  # type: ignore[return-value]


def get_profile_by_id(
    conn: pymysql.connections.Connection, profile_id: int
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_PROFILE_COLS} FROM profiles WHERE deleted = 0 AND id = %s",
            (profile_id,),
        )
        return cur.fetchone()  # type: ignore[return-value]


def get_profile_by_name(
    conn: pymysql.connections.Connection, name: str
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_PROFILE_COLS} FROM profiles WHERE deleted = 0 AND name = %s",
            (name,),
        )
        return cur.fetchone()  # type: ignore[return-value]


def browse_profiles(
    conn: pymysql.connections.Connection,
    offset: int = 0,
    limit: int = 50,
    query: str = "",
) -> tuple[int, list[dict[str, Any]]]:
    with conn.cursor() as cur:
        if query:
            pattern = f"%{query}%"
            cur.execute(
                "SELECT COUNT(id) AS n FROM profiles WHERE deleted = 0 AND name LIKE %s",
                (pattern,),
            )
            total: int = cur.fetchone()["n"]  # type: ignore[index]
            cur.execute(
                f"SELECT {_PROFILE_COLS} FROM profiles WHERE deleted = 0 AND name LIKE %s "
                "ORDER BY name LIMIT %s OFFSET %s",
                (pattern, limit, offset),
            )
        else:
            cur.execute("SELECT COUNT(id) AS n FROM profiles WHERE deleted = 0")
            total = cur.fetchone()["n"]  # type: ignore[index]
            cur.execute(
                f"SELECT {_PROFILE_COLS} FROM profiles WHERE deleted = 0 "
                "ORDER BY name LIMIT %s OFFSET %s",
                (limit, offset),
            )
        rows: list[dict[str, Any]] = cur.fetchall()  # type: ignore[assignment]
    return total, rows


def get_profile_stats(
    conn: pymysql.connections.Connection, profile_id: int
) -> dict[str, Any]:
    """Return aggregated stats for a single profile (wins, losses, draws, goals, streak)."""
    with conn.cursor() as cur:
        # wins
        cur.execute(
            "SELECT COUNT(id) AS n FROM matches "
            "WHERE (profile_id_home=%s AND score_home>score_away) "
            "OR (profile_id_away=%s AND score_home<score_away)",
            (profile_id, profile_id),
        )
        wins: int = cur.fetchone()["n"]  # type: ignore[index]

        # losses
        cur.execute(
            "SELECT COUNT(id) AS n FROM matches "
            "WHERE (profile_id_home=%s AND score_home<score_away) "
            "OR (profile_id_away=%s AND score_home>score_away)",
            (profile_id, profile_id),
        )
        losses: int = cur.fetchone()["n"]  # type: ignore[index]

        # draws
        cur.execute(
            "SELECT COUNT(id) AS n FROM matches "
            "WHERE (profile_id_home=%s OR profile_id_away=%s) "
            "AND score_home=score_away",
            (profile_id, profile_id),
        )
        draws: int = cur.fetchone()["n"]  # type: ignore[index]

        # goals scored / conceded (home)
        cur.execute(
            "SELECT COALESCE(SUM(score_home),0) AS gf, COALESCE(SUM(score_away),0) AS ga "
            "FROM matches WHERE profile_id_home=%s",
            (profile_id,),
        )
        home_row = cur.fetchone()

        # goals scored / conceded (away)
        cur.execute(
            "SELECT COALESCE(SUM(score_away),0) AS gf, COALESCE(SUM(score_home),0) AS ga "
            "FROM matches WHERE profile_id_away=%s",
            (profile_id,),
        )
        away_row = cur.fetchone()

        goals_for: int = int(home_row["gf"]) + int(away_row["gf"])  # type: ignore[index]
        goals_against: int = int(home_row["ga"]) + int(away_row["ga"])  # type: ignore[index]

        # streak
        cur.execute("SELECT wins, best FROM streaks WHERE profile_id=%s", (profile_id,))
        streak_row = cur.fetchone()
        streak: int = streak_row["wins"] if streak_row else 0  # type: ignore[index]
        best_streak: int = streak_row["best"] if streak_row else 0  # type: ignore[index]

    total_games = wins + losses + draws
    win_pct: float = round(wins / total_games * 100, 1) if total_games > 0 else 0.0

    return {
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "win_pct": win_pct,
        "streak": streak,
        "best_streak": best_streak,
    }


# ---------------------------------------------------------------------------
# Metrics queries  (requires sql/metrics.sql applied)
# ---------------------------------------------------------------------------


def record_user_registration(
    conn: pymysql.connections.Connection, user_id: int
) -> None:
    """Insert a registration timestamp for a new user (metrics.sql table)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT IGNORE INTO user_registrations (user_id) VALUES (%s)", (user_id,)
        )


def stats_matches_per_day(
    conn: pymysql.connections.Connection, date_from: Any, date_to: Any
) -> list[dict[str, Any]]:
    """Return [{date, label, count}] for every day in [date_from, date_to]."""
    from datetime import date as date_cls, timedelta

    if not isinstance(date_from, date_cls):
        date_from = date_cls.fromisoformat(str(date_from))
    if not isinstance(date_to, date_cls):
        date_to = date_cls.fromisoformat(str(date_to))

    with conn.cursor() as cur:
        cur.execute(
            "SELECT DATE(played_on) AS d, COUNT(*) AS count "
            "FROM matches "
            "WHERE DATE(played_on) BETWEEN %s AND %s "
            "GROUP BY DATE(played_on)",
            (date_from, date_to),
        )
        rows: dict[str, int] = {str(r["d"]): r["count"] for r in cur.fetchall()}  # type: ignore[index]

    span = (date_to - date_from).days + 1
    result = []
    for i in range(span):
        d = date_from + timedelta(days=i)
        key = str(d)
        label = str(d.day) if span <= 31 else d.strftime("%b %d").lstrip("0")
        result.append({"date": key, "label": label, "count": rows.get(key, 0)})
    return result


def stats_top_teams(
    conn: pymysql.connections.Connection, date_from: Any, date_to: Any, limit: int = 10
) -> list[dict[str, Any]]:
    """Return [{team_id, count}] of most-picked teams in the date range."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT team_id, COUNT(*) AS count FROM ("
            "  SELECT team_id_home AS team_id FROM matches "
            "  WHERE DATE(played_on) BETWEEN %s AND %s AND team_id_home >= 0 "
            "  UNION ALL "
            "  SELECT team_id_away FROM matches "
            "  WHERE DATE(played_on) BETWEEN %s AND %s AND team_id_away >= 0 "
            ") t GROUP BY team_id ORDER BY count DESC LIMIT %s",
            (date_from, date_to, date_from, date_to, limit),
        )
        return cur.fetchall()  # type: ignore[return-value]


def stats_top_rosters(
    conn: pymysql.connections.Connection, date_from: Any, date_to: Any, limit: int = 10
) -> list[dict[str, Any]]:
    """Return [{hash, count}] of most-used roster hashes. Requires match_rosters table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT roster_hash AS hash, COUNT(*) AS count FROM ("
            "  SELECT mr.home_roster_hash AS roster_hash "
            "  FROM match_rosters mr JOIN matches m ON mr.match_id = m.id "
            "  WHERE DATE(m.played_on) BETWEEN %s AND %s AND mr.home_roster_hash IS NOT NULL "
            "  UNION ALL "
            "  SELECT mr.away_roster_hash "
            "  FROM match_rosters mr JOIN matches m ON mr.match_id = m.id "
            "  WHERE DATE(m.played_on) BETWEEN %s AND %s AND mr.away_roster_hash IS NOT NULL "
            ") r GROUP BY roster_hash ORDER BY count DESC LIMIT %s",
            (date_from, date_to, date_from, date_to, limit),
        )
        return cur.fetchall()  # type: ignore[return-value]


def stats_summary(
    conn: pymysql.connections.Connection, date_from: Any, date_to: Any
) -> dict[str, Any]:
    """Return aggregate summary stats for the given date range."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n, "
            "COALESCE(AVG(score_home + score_away), 0) AS avg_goals "
            "FROM matches WHERE DATE(played_on) BETWEEN %s AND %s",
            (date_from, date_to),
        )
        match_row = cur.fetchone()
        total_matches: int = match_row["n"]  # type: ignore[index]
        avg_goals: float = round(float(match_row["avg_goals"]), 1)  # type: ignore[index]

        cur.execute(
            "SELECT COUNT(DISTINCT p.user_id) AS n FROM ("
            "  SELECT profile_id_home AS pid FROM matches WHERE DATE(played_on) BETWEEN %s AND %s "
            "  UNION "
            "  SELECT profile_id_away FROM matches WHERE DATE(played_on) BETWEEN %s AND %s "
            ") ids JOIN profiles p ON p.id = ids.pid",
            (date_from, date_to, date_from, date_to),
        )
        active_users: int = cur.fetchone()["n"]  # type: ignore[index]

        cur.execute(
            "SELECT COUNT(*) AS n FROM user_registrations "
            "WHERE DATE(registered_at) BETWEEN %s AND %s",
            (date_from, date_to),
        )
        new_users: int = cur.fetchone()["n"]  # type: ignore[index]

    return {
        "total_matches": total_matches,
        "active_users": active_users,
        "new_users": new_users,
        "avg_goals_per_match": avg_goals,
    }


def stats_top_online_users(
    conn: pymysql.connections.Connection, limit: int = 10
) -> list[dict[str, Any]]:
    """Return [{username, total_online_seconds}] sorted by most time online (all-time)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT username, total_online_seconds "
            "FROM users WHERE deleted = 0 AND total_online_seconds > 0 "
            "ORDER BY total_online_seconds DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Public site queries  (requires sql/views.sql applied)
# ---------------------------------------------------------------------------

_LEADERBOARD_SORT_COLS: frozenset[str] = frozenset(
    {
        "name",
        "points",
        "seconds_played",
        "rank",
        "games",
        "wins",
        "draws",
        "losses",
        "best",
        "division",
    }
)
_MATCH_SORT_COLS: frozenset[str] = frozenset(
    {"id", "played_on", "score_home", "score_away"}
)


def get_leaderboard(
    conn: pymysql.connections.Connection,
    offset: int,
    limit: int,
    sort: str = "points",
    direction: str = "desc",
    division: int | None = None,
) -> list[dict[str, Any]]:
    """Return rows from v_leaderboard with allowlist-validated sort and optional division filter."""
    col = sort if sort in _LEADERBOARD_SORT_COLS else "points"
    dir_ = "ASC" if direction.lower() == "asc" else "DESC"
    with conn.cursor() as cur:
        if division is not None:
            cur.execute(
                f"SELECT rank, id, name, points, seconds_played, games, wins, draws, losses, best, division "
                f"FROM v_leaderboard WHERE division = %s ORDER BY {col} {dir_} LIMIT %s OFFSET %s",
                (division, limit, offset),
            )
        else:
            cur.execute(
                f"SELECT rank, id, name, points, seconds_played, games, wins, draws, losses, best, division "
                f"FROM v_leaderboard ORDER BY {col} {dir_} LIMIT %s OFFSET %s",
                (limit, offset),
            )
        return cur.fetchall()  # type: ignore[return-value]


def count_leaderboard(
    conn: pymysql.connections.Connection, division: int | None = None
) -> int:
    """Count leaderboard rows, optionally filtered by division."""
    with conn.cursor() as cur:
        if division is not None:
            cur.execute(
                "SELECT COUNT(*) AS n FROM v_leaderboard WHERE division = %s",
                (division,),
            )
        else:
            cur.execute("SELECT COUNT(*) AS n FROM profiles WHERE deleted = 0")
        return int(cur.fetchone()["n"])  # type: ignore[index]


def _fetch_all_matches(
    conn: pymysql.connections.Connection,
    offset: int,
    limit: int,
    sort: str,
    direction: str,
) -> list[dict[str, Any]]:
    col = sort if sort in _MATCH_SORT_COLS else "id"
    dir_ = "ASC" if direction.lower() == "asc" else "DESC"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM v_match_detail ORDER BY {col} {dir_} LIMIT %s OFFSET %s",
            (limit, offset),
        )
        return cur.fetchall()  # type: ignore[return-value]


def _fetch_profile_matches(
    conn: pymysql.connections.Connection,
    profile_id: int,
    offset: int,
    limit: int,
    sort: str,
    direction: str,
) -> list[dict[str, Any]]:
    col = sort if sort in _MATCH_SORT_COLS else "id"
    dir_ = "ASC" if direction.lower() == "asc" else "DESC"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM v_match_detail "
            f"WHERE profile_id_home = %s OR profile_id_away = %s "
            f"ORDER BY {col} {dir_} LIMIT %s OFFSET %s",
            (profile_id, profile_id, limit, offset),
        )
        return cur.fetchall()  # type: ignore[return-value]


def get_public_matches(
    conn: pymysql.connections.Connection,
    offset: int,
    limit: int,
    profile_id: int | None = None,
    sort: str = "id",
    direction: str = "desc",
) -> list[dict[str, Any]]:
    """Return paginated matches from v_match_detail, optionally filtered by profile."""
    if profile_id is None:
        return _fetch_all_matches(conn, offset, limit, sort, direction)
    return _fetch_profile_matches(conn, profile_id, offset, limit, sort, direction)


def count_public_matches(
    conn: pymysql.connections.Connection, profile_id: int | None = None
) -> int:
    """Count total matches, optionally filtered by profile."""
    with conn.cursor() as cur:
        if profile_id is None:
            cur.execute("SELECT COUNT(*) AS n FROM matches")
        else:
            cur.execute(
                "SELECT COUNT(*) AS n FROM matches "
                "WHERE profile_id_home = %s OR profile_id_away = %s",
                (profile_id, profile_id),
            )
        return int(cur.fetchone()["n"])  # type: ignore[index]


def search_profiles(
    conn: pymysql.connections.Connection, term: str
) -> list[dict[str, Any]]:
    """Return up to 10 non-deleted profiles whose name starts with term."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name FROM profiles WHERE deleted = 0 AND name LIKE %s LIMIT 10",
            (term + "%",),
        )
        return cur.fetchall()  # type: ignore[return-value]


def get_profile_with_stats(
    conn: pymysql.connections.Connection, name: str
) -> dict[str, Any] | None:
    """Single-query profile + v_profile_stats + streaks join for the public profile page."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.id, p.name, p.points, p.rank, p.seconds_played, "
            "COALESCE(ps.games, 0) AS games, "
            "COALESCE(ps.wins, 0) AS wins, "
            "COALESCE(ps.draws, 0) AS draws, "
            "COALESCE(ps.losses, 0) AS losses, "
            "COALESCE(ps.goals_for, 0) AS goals_for, "
            "COALESCE(ps.goals_against, 0) AS goals_against, "
            "COALESCE(s.wins, 0) AS streak, "
            "COALESCE(s.best, 0) AS best_streak, "
            "CASE WHEN p.points < 250 THEN 0 "
            "     WHEN p.points < 450 THEN 1 "
            "     WHEN p.points < 600 THEN 2 "
            "     WHEN p.points < 750 THEN 3 "
            "     ELSE 4 END AS division "
            "FROM profiles p "
            "LEFT JOIN v_profile_stats ps ON ps.id = p.id "
            "LEFT JOIN streaks s ON s.profile_id = p.id "
            "WHERE p.name = %s AND p.deleted = 0",
            (name,),
        )
        row: dict[str, Any] | None = cur.fetchone()  # type: ignore[assignment]
    if row is None:
        return None
    games = row["games"]
    row["win_pct"] = round(row["wins"] / games * 100, 1) if games else 0.0
    return row
