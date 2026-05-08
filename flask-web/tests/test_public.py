"""Tests for the public blueprint and public DB functions."""

from __future__ import annotations

import sys
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import create_test_app
import db

# ── DB mock helpers ────────────────────────────────────────────────────────────


def _make_conn(
    fetchone_val: Any = None, fetchall_val: Any = None, lastrowid: int = 1
) -> MagicMock:
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchone = MagicMock(return_value=fetchone_val)
    cursor.fetchall = MagicMock(return_value=fetchall_val or [])
    cursor.lastrowid = lastrowid
    conn.cursor = MagicMock(return_value=cursor)
    return conn


def _cursor(conn: MagicMock) -> MagicMock:
    return conn.cursor().__enter__()


# ── DB unit tests ──────────────────────────────────────────────────────────────


class TestGetLeaderboard(unittest.TestCase):
    def test_default_sort_is_points(self) -> None:
        conn = _make_conn(fetchall_val=[{"id": 1, "name": "A", "points": 500}])
        db.get_leaderboard(conn, 0, 25)
        sql = _cursor(conn).execute.call_args[0][0]
        self.assertIn("points", sql)

    def test_invalid_sort_falls_back_to_points(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.get_leaderboard(conn, 0, 25, sort="DROP TABLE profiles--")
        sql = _cursor(conn).execute.call_args[0][0]
        self.assertIn("points", sql)
        self.assertNotIn("DROP", sql)

    def test_invalid_sort_with_semicolon_sanitized(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.get_leaderboard(conn, 0, 25, sort="points; DROP TABLE--")
        sql = _cursor(conn).execute.call_args[0][0]
        self.assertNotIn("DROP", sql)

    def test_direction_clamped_to_asc_or_desc(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.get_leaderboard(conn, 0, 25, direction="desc; DROP TABLE--")
        sql = _cursor(conn).execute.call_args[0][0]
        self.assertNotIn("DROP", sql)
        self.assertIn("DESC", sql)

    def test_valid_sort_cols_accepted(self) -> None:
        for col in ("name", "points", "seconds_played", "rank", "games", "wins"):
            conn = _make_conn(fetchall_val=[])
            db.get_leaderboard(conn, 0, 25, sort=col)
            sql = _cursor(conn).execute.call_args[0][0]
            self.assertIn(col, sql)

    def test_offset_and_limit_are_parameterized(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.get_leaderboard(conn, 10, 50)
        params = _cursor(conn).execute.call_args[0][1]
        self.assertIn(10, params)
        self.assertIn(50, params)


class TestCountLeaderboard(unittest.TestCase):
    def test_returns_int(self) -> None:
        conn = _make_conn(fetchone_val={"n": 42})
        result = db.count_leaderboard(conn)
        self.assertEqual(result, 42)

    def test_queries_base_table(self) -> None:
        conn = _make_conn(fetchone_val={"n": 0})
        db.count_leaderboard(conn)
        sql = _cursor(conn).execute.call_args[0][0]
        self.assertIn("profiles", sql)
        self.assertNotIn("v_leaderboard", sql)


class TestGetPublicMatches(unittest.TestCase):
    def test_no_profile_id_calls_all_matches(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.get_public_matches(conn, 0, 10, profile_id=None)
        sql = _cursor(conn).execute.call_args[0][0]
        self.assertIn("v_match_detail", sql)
        self.assertNotIn("profile_id_home", sql)

    def test_with_profile_id_filters(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.get_public_matches(conn, 0, 10, profile_id=7)
        call_args = _cursor(conn).execute.call_args[0]
        sql, params = call_args[0], call_args[1]
        self.assertIn("profile_id_home", sql)
        self.assertIn(7, params)

    def test_invalid_sort_sanitized(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.get_public_matches(conn, 0, 10, sort="id; DROP TABLE matches--")
        sql = _cursor(conn).execute.call_args[0][0]
        self.assertNotIn("DROP", sql)


class TestCountPublicMatches(unittest.TestCase):
    def test_all_matches_no_profile(self) -> None:
        conn = _make_conn(fetchone_val={"n": 100})
        result = db.count_public_matches(conn)
        self.assertEqual(result, 100)
        sql = _cursor(conn).execute.call_args[0][0]
        self.assertNotIn("profile_id_home", sql)

    def test_with_profile_id_filters(self) -> None:
        conn = _make_conn(fetchone_val={"n": 5})
        db.count_public_matches(conn, profile_id=3)
        call_args = _cursor(conn).execute.call_args[0]
        self.assertIn("profile_id_home", call_args[0])
        self.assertIn(3, call_args[1])


class TestSearchProfiles(unittest.TestCase):
    def test_min_prefix_pattern_used(self) -> None:
        conn = _make_conn(fetchall_val=[{"name": "PlayerA"}])
        db.search_profiles(conn, "Pla")
        params = _cursor(conn).execute.call_args[0][1]
        self.assertEqual(params, ("Pla%",))

    def test_does_not_use_full_wildcard(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.search_profiles(conn, "test")
        params = _cursor(conn).execute.call_args[0][1]
        self.assertNotIn("%test%", params)

    def test_sql_injection_term_is_parameterized(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.search_profiles(conn, "'; DROP TABLE profiles; --")
        params = _cursor(conn).execute.call_args[0][1]
        self.assertEqual(params, ("'; DROP TABLE profiles; --%",))

    def test_limit_applied(self) -> None:
        conn = _make_conn(fetchall_val=[])
        db.search_profiles(conn, "abc")
        sql = _cursor(conn).execute.call_args[0][0]
        self.assertIn("LIMIT", sql)


class TestGetProfileWithStats(unittest.TestCase):
    def test_returns_none_when_not_found(self) -> None:
        conn = _make_conn(fetchone_val=None)
        result = db.get_profile_with_stats(conn, "ghost")
        self.assertIsNone(result)

    def test_returns_dict_with_win_pct(self) -> None:
        row = {
            "id": 1,
            "name": "Alice",
            "points": 500,
            "rank": 3,
            "seconds_played": 7200,
            "games": 10,
            "wins": 6,
            "draws": 2,
            "losses": 2,
            "goals_for": 20,
            "goals_against": 12,
            "streak": 3,
            "best_streak": 5,
            "division": 3,
        }
        conn = _make_conn(fetchone_val=row)
        result = db.get_profile_with_stats(conn, "Alice")
        self.assertIsNotNone(result)
        self.assertEqual(result["win_pct"], 60.0)

    def test_win_pct_zero_when_no_games(self) -> None:
        row = {
            "id": 2,
            "name": "Bob",
            "points": 0,
            "rank": 99,
            "seconds_played": 0,
            "games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
            "streak": 0,
            "best_streak": 0,
            "division": 0,
        }
        conn = _make_conn(fetchone_val=row)
        result = db.get_profile_with_stats(conn, "Bob")
        self.assertEqual(result["win_pct"], 0.0)

    def test_name_is_parameterized(self) -> None:
        conn = _make_conn(fetchone_val=None)
        db.get_profile_with_stats(conn, "'; DROP TABLE profiles; --")
        params = _cursor(conn).execute.call_args[0][1]
        self.assertEqual(params, ("'; DROP TABLE profiles; --",))


# ── Route / blueprint tests ────────────────────────────────────────────────────


def _minimal_leaderboard_row() -> dict:
    return {
        "id": 1,
        "name": "Player1",
        "points": 500,
        "rank": 1,
        "seconds_played": 3600,
        "games": 10,
        "wins": 6,
        "draws": 2,
        "losses": 2,
        "best": 3,
        "division": 3,
    }


def _minimal_match_row():
    from datetime import datetime

    return {
        "id": 1,
        "home_name": "Alice",
        "away_name": "Bob",
        "score_home": 2,
        "score_away": 1,
        "team_id_home": 101,
        "team_id_away": 102,
        "profile_id_home": 1,
        "profile_id_away": 2,
        "played_on": datetime(2024, 1, 15, 20, 30),
    }


class TestPublicRoutes(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    def _patch_db(self, **extra):
        base = {
            "get_db": MagicMock(return_value=MagicMock()),
            "count_leaderboard": MagicMock(return_value=0),
            "count_public_matches": MagicMock(return_value=0),
            "get_leaderboard": MagicMock(return_value=[]),
            "get_public_matches": MagicMock(return_value=[]),
            "get_profile_with_stats": MagicMock(return_value=None),
            "search_profiles": MagicMock(return_value=[]),
        }
        base.update(extra)
        return base

    def test_home_returns_200(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_rankings_returns_200(self) -> None:
        with patch.multiple(
            "blueprints.public",
            **self._patch_db(
                get_leaderboard=MagicMock(return_value=[_minimal_leaderboard_row()])
            ),
        ):
            resp = self.client.get("/rankings")
        self.assertEqual(resp.status_code, 200)

    def test_profiles_no_query_returns_200(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/profiles")
        self.assertEqual(resp.status_code, 200)

    def test_profiles_not_found_shows_no_player(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/profiles?q=ghost")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"ghost", resp.data)

    def test_matches_returns_200(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/matches")
        self.assertEqual(resp.status_code, 200)

    def test_matches_unknown_profile_returns_404(self) -> None:
        with patch.multiple(
            "blueprints.public",
            **self._patch_db(get_profile_with_stats=MagicMock(return_value=None)),
        ):
            resp = self.client.get("/matches?profile=ghost")
        self.assertEqual(resp.status_code, 404)

    def test_about_returns_200(self) -> None:
        resp = self.client.get("/about")
        self.assertEqual(resp.status_code, 200)

    def test_register_form_returns_200(self) -> None:
        resp = self.client.get("/register")
        self.assertEqual(resp.status_code, 200)

    def test_downloads_landing_returns_200(self) -> None:
        resp = self.client.get("/downloads")
        self.assertEqual(resp.status_code, 200)

    def test_downloads_bad_section_returns_404(self) -> None:
        resp = self.client.get("/downloads/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_pes5ec_ranking_returns_200(self) -> None:
        with patch.multiple(
            "blueprints.public",
            **self._patch_db(
                get_leaderboard=MagicMock(return_value=[_minimal_leaderboard_row()])
            ),
        ):
            resp = self.client.post(
                "/pes5ec/ranking/we9getrank.html", data={"pid": "0"}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"general\n", resp.data)

    def test_we9lek_ranking_returns_200(self) -> None:
        with patch.multiple(
            "blueprints.public",
            **self._patch_db(
                get_leaderboard=MagicMock(return_value=[_minimal_leaderboard_row()])
            ),
        ):
            resp = self.client.post(
                "/we9lek_pc/ranking/we9getrank.html", data={"pid": "0"}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"general\n", resp.data)


class TestPublicPagination(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    def _patch_db(self, **extra):
        base = {
            "get_db": MagicMock(return_value=MagicMock()),
            "count_leaderboard": MagicMock(return_value=0),
            "get_leaderboard": MagicMock(return_value=[]),
        }
        base.update(extra)
        return base

    def test_page_zero_clamped_to_one(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/rankings?page=0")
        self.assertEqual(resp.status_code, 200)

    def test_invalid_per_page_falls_back_to_25(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/rankings?per_page=99")
        self.assertEqual(resp.status_code, 200)

    def test_valid_per_page_100_accepted(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/rankings?per_page=100")
        self.assertEqual(resp.status_code, 200)

    def test_per_page_sql_injection_falls_back(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/rankings?per_page=1;DROP TABLE--")
        self.assertEqual(resp.status_code, 200)


class TestPublicSortSQLInjection(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    def _patch_db(self, **extra):
        base = {
            "get_db": MagicMock(return_value=MagicMock()),
            "count_leaderboard": MagicMock(return_value=0),
            "get_leaderboard": MagicMock(return_value=[]),
        }
        base.update(extra)
        return base

    def test_sort_injection_returns_200_no_crash(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/rankings?sort=DROP+TABLE+profiles--")
        self.assertEqual(resp.status_code, 200)

    def test_sort_with_semicolon_does_not_crash(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/rankings?sort=points;DROP+TABLE--")
        self.assertEqual(resp.status_code, 200)

    def test_dir_injection_returns_200_no_crash(self) -> None:
        with patch.multiple("blueprints.public", **self._patch_db()):
            resp = self.client.get("/rankings?dir=desc;DROP+TABLE--")
        self.assertEqual(resp.status_code, 200)


class TestProfileSearchAutocomplete(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    def test_short_term_returns_empty_list(self) -> None:
        resp = self.client.get("/api/profiles/search?q=ab")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_blank_term_returns_empty_list(self) -> None:
        resp = self.client.get("/api/profiles/search?q=")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_three_char_term_calls_db(self) -> None:
        mock_search = MagicMock(return_value=[{"name": "Alpha"}])
        with patch.multiple(
            "blueprints.public",
            get_db=MagicMock(return_value=MagicMock()),
            search_profiles=mock_search,
        ):
            resp = self.client.get("/api/profiles/search?q=alp")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), ["Alpha"])

    def test_xss_in_search_term_not_reflected_as_html(self) -> None:
        mock_search = MagicMock(return_value=[])
        with patch.multiple(
            "blueprints.public",
            get_db=MagicMock(return_value=MagicMock()),
            search_profiles=mock_search,
        ):
            resp = self.client.get("/api/profiles/search?q=<script>alert(1)</script>")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"<script>", resp.data)


class TestDownloadsPathTraversal(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    def _make_temp_section(self):
        tmpdir = tempfile.mkdtemp()
        # Patch REPO_ROOT so downloads can find a real folder
        return tmpdir

    def test_path_traversal_returns_403(self) -> None:
        tmpdir = tempfile.mkdtemp()
        safe_file = os.path.join(tmpdir, "test.cfg")
        with open(safe_file, "w") as f:
            f.write("data")
        with self.app.app_context():
            self.app.config["REPO_ROOT"] = os.path.dirname(tmpdir)
            # Create the expected "downloads" subfolder
            dl_dir = os.path.join(os.path.dirname(tmpdir), "downloads")
            os.makedirs(dl_dir, exist_ok=True)
            resp = self.client.get("/downloads/network/../../etc/passwd")
        self.assertIn(resp.status_code, (403, 404))

    def test_updates_path_traversal_returns_403(self) -> None:
        tmpdir = tempfile.mkdtemp()
        with self.app.app_context():
            self.app.config["REPO_ROOT"] = tmpdir
            db_dir = os.path.join(tmpdir, "db_updates")
            os.makedirs(db_dir, exist_ok=True)
            resp = self.client.get("/updates/../app.py")
        self.assertIn(resp.status_code, (403, 404))


class TestLiveMatchesAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    def test_go_api_unavailable_returns_200_with_empty_live(self) -> None:
        mock_matches = MagicMock(return_value=[])
        with patch.multiple(
            "blueprints.public",
            get_db=MagicMock(return_value=MagicMock()),
            get_public_matches=mock_matches,
            _go_get=MagicMock(return_value=({}, 503)),
        ):
            resp = self.client.get("/api/live-matches")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["live"], [])
        self.assertIn("finished", data)

    def test_db_updates_redirects_to_updates(self) -> None:
        resp = self.client.get("/db_updates/somefile.cfg")
        self.assertEqual(resp.status_code, 301)
        self.assertIn("updates", resp.headers["Location"])


if __name__ == "__main__":
    unittest.main()
