from __future__ import annotations

import base64
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import create_app


def _auth_header(user: str = 'admin', pw: str = 'secret') -> dict[str, str]:
    token = base64.b64encode(f'{user}:{pw}'.encode()).decode()
    return {'Authorization': f'Basic {token}'}


class TestStatsAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['ADMIN_USER'] = 'admin'
        self.app.config['ADMIN_PASSWORD'] = 'secret'
        self.client = self.app.test_client()

    def test_no_auth_returns_401(self) -> None:
        resp = self.client.get('/stats/')
        self.assertEqual(resp.status_code, 401)
        self.assertIn('WWW-Authenticate', resp.headers)

    def test_wrong_password_returns_401(self) -> None:
        resp = self.client.get('/stats/', headers=_auth_header(pw='wrong'))
        self.assertEqual(resp.status_code, 401)

    def test_correct_auth_passes(self) -> None:
        resp = self.client.get('/stats/', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)


class TestStatsHome(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['ADMIN_USER'] = 'admin'
        self.app.config['ADMIN_PASSWORD'] = 'secret'
        self.client = self.app.test_client()

    def test_home_200(self) -> None:
        resp = self.client.get('/stats/', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)

    def test_home_shows_summary_cards(self) -> None:
        resp = self.client.get('/stats/', headers=_auth_header())
        self.assertIn(b'Matches Played', resp.data)
        self.assertIn(b'Active Users', resp.data)
        self.assertIn(b'New Registrations', resp.data)

    def test_home_shows_charts(self) -> None:
        resp = self.client.get('/stats/', headers=_auth_header())
        self.assertIn(b'Matches Per Day', resp.data)
        self.assertIn(b'Most Used Teams', resp.data)
        self.assertIn(b'Top Roster Hashes', resp.data)

    def test_month_filter(self) -> None:
        resp = self.client.get('/stats/?month=2026-01', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'January 2026', resp.data)

    def test_date_range_filter(self) -> None:
        resp = self.client.get(
            '/stats/?from=2026-04-01&to=2026-04-07', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)

    def test_home_alias(self) -> None:
        resp = self.client.get('/stats/home', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)

    def test_invalid_month_falls_back(self) -> None:
        resp = self.client.get('/stats/?month=bad', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
