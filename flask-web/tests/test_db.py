from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import MagicMock
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db


def _make_conn(
    fetchone_val: Any = None, fetchall_val: Any = None, lastrowid: int = 42
) -> MagicMock:
    """Build a mock pymysql connection with a context-manager cursor."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchone = MagicMock(return_value=fetchone_val)
    cursor.fetchall = MagicMock(return_value=fetchall_val or [])
    cursor.lastrowid = lastrowid
    conn.cursor = MagicMock(return_value=cursor)
    return conn


class TestFindUserByUsername(unittest.TestCase):
    def test_returns_row(self) -> None:
        expected: dict[str, Any] = {"id": 1, "username": "player1"}
        conn = _make_conn(fetchone_val=expected)
        result = db.find_user_by_username(conn, "player1")
        self.assertEqual(result, expected)
        # Verify parameterized query was used
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn("username = %s", args[0])
        self.assertEqual(args[1], ("player1",))

    def test_returns_none_when_not_found(self) -> None:
        conn = _make_conn(fetchone_val=None)
        result = db.find_user_by_username(conn, "ghost")
        self.assertIsNone(result)


class TestFindUserByNonce(unittest.TestCase):
    def test_returns_row(self) -> None:
        expected: dict[str, Any] = {"id": 5, "reset_nonce": "abc123"}
        conn = _make_conn(fetchone_val=expected)
        result = db.find_user_by_nonce(conn, "abc123")
        self.assertEqual(result, expected)


class TestCreateUser(unittest.TestCase):
    def test_returns_new_id(self) -> None:
        conn = _make_conn(lastrowid=99)
        new_id = db.create_user(conn, "newplayer", "SERIAL123456789", "abc123hash")
        self.assertEqual(new_id, 99)
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn("INSERT INTO users", args[0])
        self.assertEqual(args[1], ("newplayer", "SERIAL123456789", "abc123hash"))


class TestUpdateUser(unittest.TestCase):
    def test_executes_update(self) -> None:
        conn = _make_conn()
        db.update_user(conn, 7, "updated", "SER", "newhash")
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn("UPDATE users SET", args[0])
        self.assertIn("reset_nonce=NULL", args[0])


class TestLockUser(unittest.TestCase):
    def test_sets_nonce(self) -> None:
        conn = _make_conn()
        db.lock_user(conn, 3, "nonce999")
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn("reset_nonce=%s", args[0])
        self.assertEqual(args[1], ("nonce999", 3))


class TestDeleteUser(unittest.TestCase):
    def test_soft_delete(self) -> None:
        conn = _make_conn()
        db.delete_user(conn, 2)
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn("deleted = 1", args[0])
        self.assertEqual(args[1], (2,))


class TestBrowseUsers(unittest.TestCase):
    def test_returns_total_and_rows(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [{"n": 3}]
        cursor.fetchall.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
        total, rows = db.browse_users(conn, offset=0, limit=50)
        self.assertEqual(total, 3)
        self.assertEqual(len(rows), 3)

    def test_search_uses_like(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [{"n": 1}]
        cursor.fetchall.return_value = [{"id": 1}]
        db.browse_users(conn, search="play")
        calls = cursor.execute.call_args_list
        # First call is COUNT with LIKE
        self.assertIn("LIKE", calls[0][0][0])


class TestBrowseProfiles(unittest.TestCase):
    def test_returns_total_and_rows(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [{"n": 5}]
        cursor.fetchall.return_value = [{"id": i} for i in range(1, 6)]
        total, rows = db.browse_profiles(conn, offset=0, limit=50)
        self.assertEqual(total, 5)
        self.assertEqual(len(rows), 5)

    def test_search_uses_like(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [{"n": 2}]
        cursor.fetchall.return_value = [{"id": 1}, {"id": 2}]
        db.browse_profiles(conn, query="hero")
        calls = cursor.execute.call_args_list
        # First call is COUNT with LIKE
        self.assertIn("LIKE", calls[0][0][0])


class TestGetProfileStats(unittest.TestCase):
    def test_returns_stats_dict(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [
            {"wins": 5, "losses": 2, "draws": 1, "goals_for": 13, "goals_against": 6},  # aggregation
            {"wins": 3, "best": 5},  # streak
        ]
        stats = db.get_profile_stats(conn, 1)
        self.assertEqual(stats["wins"], 5)
        self.assertEqual(stats["losses"], 2)
        self.assertEqual(stats["draws"], 1)
        self.assertEqual(stats["goals_for"], 13)
        self.assertEqual(stats["goals_against"], 6)
        self.assertEqual(stats["streak"], 3)
        self.assertEqual(stats["best_streak"], 5)


class TestRecordUserRegistration(unittest.TestCase):
    def test_inserts_with_ignore(self) -> None:
        conn = _make_conn()
        db.record_user_registration(conn, 7)
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn("INSERT IGNORE INTO user_registrations", args[0])
        self.assertEqual(args[1], (7,))


class TestStatsMatchesPerDay(unittest.TestCase):
    def test_fills_zero_days(self) -> None:
        from datetime import date

        conn = _make_conn(fetchall_val=[{"d": date(2026, 4, 15), "count": 3}])
        result = db.stats_matches_per_day(conn, date(2026, 4, 1), date(2026, 4, 30))
        self.assertEqual(len(result), 30)  # April has 30 days
        self.assertEqual(result[14]["date"], "2026-04-15")
        self.assertEqual(result[14]["count"], 3)
        self.assertEqual(result[0]["count"], 0)

    def test_empty_month_all_zeros(self) -> None:
        from datetime import date

        conn = _make_conn(fetchall_val=[])
        result = db.stats_matches_per_day(conn, date(2026, 2, 1), date(2026, 2, 28))
        self.assertTrue(all(d["count"] == 0 for d in result))
        self.assertEqual(len(result), 28)


class TestStatsTopTeams(unittest.TestCase):
    def test_returns_rows(self) -> None:
        expected = [{"team_id": 1, "count": 10}, {"team_id": 5, "count": 7}]
        conn = _make_conn(fetchall_val=expected)
        result = db.stats_top_teams(conn, "2026-04-01", "2026-04-30")
        self.assertEqual(result, expected)
        cursor = conn.cursor().__enter__()
        sql = cursor.execute.call_args[0][0]
        self.assertIn("team_id_home", sql)
        self.assertIn("team_id_away", sql)
        self.assertIn("UNION ALL", sql)

    def test_default_limit_10(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.stats_top_teams(conn, "2026-04-01", "2026-04-30")
        params = conn.cursor().__enter__().execute.call_args[0][1]
        self.assertEqual(params[-1], 10)


class TestStatsTopRosters(unittest.TestCase):
    def test_returns_rows(self) -> None:
        expected = [{"hash": "aabbcc", "count": 5}]
        conn = _make_conn(fetchall_val=expected)
        result = db.stats_top_rosters(conn, "2026-04-01", "2026-04-30")
        self.assertEqual(result, expected)
        cursor = conn.cursor().__enter__()
        sql = cursor.execute.call_args[0][0]
        self.assertIn("match_rosters", sql)
        self.assertIn("home_roster_hash", sql)
        self.assertIn("away_roster_hash", sql)


class TestStatsSummary(unittest.TestCase):
    def test_returns_all_fields(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [
            {"n": 20, "avg_goals": 2.4},  # matches query
            {"n": 8},  # active_users
            {"n": 3},  # new_users
        ]
        result = db.stats_summary(conn, "2026-04-01", "2026-04-30")
        self.assertEqual(result["total_matches"], 20)
        self.assertEqual(result["active_users"], 8)
        self.assertEqual(result["new_users"], 3)
        self.assertEqual(result["avg_goals_per_match"], 2.4)

    def test_avg_goals_rounded(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [
            {"n": 5, "avg_goals": 2.666},
            {"n": 2},
            {"n": 1},
        ]
        result = db.stats_summary(conn, "2026-04-01", "2026-04-30")
        self.assertEqual(result["avg_goals_per_match"], 2.7)


if __name__ == "__main__":
    unittest.main()
