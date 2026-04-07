from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db


def _make_conn(fetchone_val: Any = None,
               fetchall_val: Any = None,
               lastrowid: int = 42) -> MagicMock:
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
        expected: dict[str, Any] = {'id': 1, 'username': 'player1'}
        conn = _make_conn(fetchone_val=expected)
        result = db.find_user_by_username(conn, 'player1')
        self.assertEqual(result, expected)
        # Verify parameterized query was used
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn('username = %s', args[0])
        self.assertEqual(args[1], ('player1',))

    def test_returns_none_when_not_found(self) -> None:
        conn = _make_conn(fetchone_val=None)
        result = db.find_user_by_username(conn, 'ghost')
        self.assertIsNone(result)


class TestFindUserByNonce(unittest.TestCase):

    def test_returns_row(self) -> None:
        expected: dict[str, Any] = {'id': 5, 'reset_nonce': 'abc123'}
        conn = _make_conn(fetchone_val=expected)
        result = db.find_user_by_nonce(conn, 'abc123')
        self.assertEqual(result, expected)


class TestCreateUser(unittest.TestCase):

    def test_returns_new_id(self) -> None:
        conn = _make_conn(lastrowid=99)
        new_id = db.create_user(conn, 'newplayer', 'SERIAL123456789', 'abc123hash')
        self.assertEqual(new_id, 99)
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn('INSERT INTO users', args[0])
        self.assertEqual(args[1], ('newplayer', 'SERIAL123456789', 'abc123hash'))


class TestUpdateUser(unittest.TestCase):

    def test_executes_update(self) -> None:
        conn = _make_conn()
        db.update_user(conn, 7, 'updated', 'SER', 'newhash')
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn('UPDATE users SET', args[0])
        self.assertIn('reset_nonce=NULL', args[0])


class TestLockUser(unittest.TestCase):

    def test_sets_nonce(self) -> None:
        conn = _make_conn()
        db.lock_user(conn, 3, 'nonce999')
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn('reset_nonce=%s', args[0])
        self.assertEqual(args[1], ('nonce999', 3))


class TestDeleteUser(unittest.TestCase):

    def test_soft_delete(self) -> None:
        conn = _make_conn()
        db.delete_user(conn, 2)
        cursor = conn.cursor().__enter__()
        args = cursor.execute.call_args[0]
        self.assertIn('deleted = 1', args[0])
        self.assertEqual(args[1], (2,))


class TestBrowseUsers(unittest.TestCase):

    def test_returns_total_and_rows(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [{'n': 3}]
        cursor.fetchall.return_value = [{'id': 1}, {'id': 2}, {'id': 3}]
        total, rows = db.browse_users(conn, offset=0, limit=50)
        self.assertEqual(total, 3)
        self.assertEqual(len(rows), 3)

    def test_search_uses_like(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [{'n': 1}]
        cursor.fetchall.return_value = [{'id': 1}]
        db.browse_users(conn, search='play')
        calls = cursor.execute.call_args_list
        # First call is COUNT with LIKE
        self.assertIn('LIKE', calls[0][0][0])


class TestGetProfileStats(unittest.TestCase):

    def test_returns_stats_dict(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        cursor.fetchone.side_effect = [
            {'n': 5},                                   # wins
            {'n': 2},                                   # losses
            {'n': 1},                                   # draws
            {'gf': 10, 'ga': 4},                        # home goals
            {'gf': 3, 'ga': 2},                         # away goals
            {'wins': 3, 'best': 5},                     # streak
        ]
        stats = db.get_profile_stats(conn, 1)
        self.assertEqual(stats['wins'], 5)
        self.assertEqual(stats['losses'], 2)
        self.assertEqual(stats['draws'], 1)
        self.assertEqual(stats['goals_for'], 13)
        self.assertEqual(stats['goals_against'], 6)
        self.assertEqual(stats['streak'], 3)
        self.assertEqual(stats['best_streak'], 5)


class TestCreateProfilesForUser(unittest.TestCase):

    def test_creates_three_profiles(self) -> None:
        conn = _make_conn()
        cursor = conn.cursor().__enter__()
        db.create_profiles_for_user(conn, 10)
        self.assertEqual(cursor.execute.call_count, 3)
        for i, c in enumerate(cursor.execute.call_args_list):
            args = c[0]
            self.assertIn('INSERT INTO profiles', args[0])
            self.assertEqual(args[1][0], 10)   # user_id
            self.assertEqual(args[1][1], i)    # ordinal 0, 1, 2


if __name__ == '__main__':
    unittest.main()
