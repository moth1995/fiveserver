from __future__ import annotations

import base64
import calendar
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from helpers import create_test_app


def _auth_header(user: str = 'fives', pw: str = 'fives') -> dict[str, str]:
    token = base64.b64encode(f'{user}:{pw}'.encode()).decode()
    return {'Authorization': f'Basic {token}'}


_FAKE_SUMMARY = {
    'total_matches': 42,
    'active_users': 10,
    'new_users': 3,
    'avg_goals_per_match': 2.5,
}
_FAKE_TEAMS = [{'team_id': 1, 'count': 20}, {'team_id': 5, 'count': 15}]
_FAKE_ROSTERS = [{'hash': 'aabbcc', 'count': 8}]


def _fake_matches_per_day(conn, year, month):
    days = calendar.monthrange(year, month)[1]
    return [{'day': d, 'count': 0} for d in range(1, days + 1)]


def _patch_db(test_fn):
    """Decorator: patch get_db + all stats query functions in the stats blueprint."""
    for decorator in [
        patch('blueprints.stats.get_db', return_value=MagicMock()),
        patch('blueprints.stats.stats_matches_per_day', side_effect=_fake_matches_per_day),
        patch('blueprints.stats.stats_top_teams', return_value=_FAKE_TEAMS),
        patch('blueprints.stats.stats_top_rosters', return_value=_FAKE_ROSTERS),
        patch('blueprints.stats.stats_summary', return_value=_FAKE_SUMMARY),
    ]:
        test_fn = decorator(test_fn)
    return test_fn


class TestStatsAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    def test_no_auth_returns_401(self) -> None:
        resp = self.client.get('/stats/')
        self.assertEqual(resp.status_code, 401)
        self.assertIn('WWW-Authenticate', resp.headers)

    def test_wrong_password_returns_401(self) -> None:
        resp = self.client.get('/stats/', headers=_auth_header(pw='wrong'))
        self.assertEqual(resp.status_code, 401)

    @_patch_db
    def test_correct_auth_passes(self, *_) -> None:
        resp = self.client.get('/stats/', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)


class TestStatsHome(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    @_patch_db
    def test_home_200(self, *_) -> None:
        resp = self.client.get('/stats/', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)

    @_patch_db
    def test_home_shows_summary_cards(self, *_) -> None:
        resp = self.client.get('/stats/', headers=_auth_header())
        self.assertIn(b'Matches Played', resp.data)
        self.assertIn(b'Active Users', resp.data)
        self.assertIn(b'New Registrations', resp.data)

    @_patch_db
    def test_home_shows_charts(self, *_) -> None:
        resp = self.client.get('/stats/', headers=_auth_header())
        self.assertIn(b'Matches Per Day', resp.data)
        self.assertIn(b'Most Used Teams', resp.data)
        self.assertIn(b'Top Roster Hashes', resp.data)

    @_patch_db
    def test_month_filter(self, *_) -> None:
        resp = self.client.get('/stats/?month=2026-01', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'January 2026', resp.data)

    @_patch_db
    def test_date_range_filter(self, *_) -> None:
        resp = self.client.get(
            '/stats/?from=2026-04-01&to=2026-04-07', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)

    @_patch_db
    def test_home_alias(self, *_) -> None:
        resp = self.client.get('/stats/home', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)

    @_patch_db
    def test_invalid_month_falls_back(self, *_) -> None:
        resp = self.client.get('/stats/?month=bad', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
