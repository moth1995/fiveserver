from __future__ import annotations

import base64
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

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
        with patch('blueprints.stats._get_online_users', return_value=[]):
            resp = self.client.get('/stats/', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)


class TestStatsHome(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['ADMIN_USER'] = 'admin'
        self.app.config['ADMIN_PASSWORD'] = 'secret'
        self.client = self.app.test_client()

    def test_home_200_no_online(self) -> None:
        with patch('blueprints.stats._get_online_users', return_value=[]):
            resp = self.client.get('/stats/', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Online', resp.data)

    def test_home_shows_online_users(self) -> None:
        fake_users = [{'username': 'player1', 'lobby': 'Lobby A', 'since': '10:00'}]
        with patch('blueprints.stats._get_online_users', return_value=fake_users):
            resp = self.client.get('/stats/home', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'player1', resp.data)


class TestStatsUsers(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['ADMIN_USER'] = 'admin'
        self.app.config['ADMIN_PASSWORD'] = 'secret'
        self.client = self.app.test_client()

    def test_users_200(self) -> None:
        fake_users = [{'id': 1, 'username': 'tester', 'rank': 5,
                       'points': 100, 'seconds_played': 7200}]
        with patch('blueprints.stats.get_db') as mock_get_db, \
             patch('blueprints.stats.browse_users', return_value=(1, fake_users)):
            mock_get_db.return_value = MagicMock()
            resp = self.client.get('/stats/users', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'tester', resp.data)

    def test_users_empty(self) -> None:
        with patch('blueprints.stats.get_db') as mock_get_db, \
             patch('blueprints.stats.browse_users', return_value=(0, [])):
            mock_get_db.return_value = MagicMock()
            resp = self.client.get('/stats/users', headers=_auth_header())
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'No users found', resp.data)


if __name__ == '__main__':
    unittest.main()
