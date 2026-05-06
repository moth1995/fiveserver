"""Tests for the admin blueprint."""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from helpers import create_test_app


def _login(client, username: str = "fives", password: str = "fives"):
    """POST to /admin/login and return the response."""
    return client.post(
        "/admin/login", data={"username": username, "password": password}
    )


def _mock_db_patch(browse_users_result=None, find_user=None):
    return {
        "get_db": MagicMock(return_value=MagicMock()),
        "browse_users": MagicMock(return_value=browse_users_result or (0, [])),
        "find_user_by_username": MagicMock(return_value=find_user),
        "find_user_by_id": MagicMock(return_value=find_user),
        "get_profiles_for_user": MagicMock(return_value=[]),
        "lock_user": MagicMock(),
        "delete_user": MagicMock(),
        "_get_online_users": MagicMock(return_value=[]),
    }


class TestAdminAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    def test_unauthenticated_redirects_to_login(self) -> None:
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login", resp.headers["Location"])

    def test_login_page_accessible(self) -> None:
        resp = self.client.get("/admin/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Sign in", resp.data)

    def test_wrong_password_shows_error(self) -> None:
        resp = _login(self.client, password="wrong")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Invalid", resp.data)

    def test_correct_login_redirects_to_home(self) -> None:
        resp = _login(self.client)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin", resp.headers["Location"])

    def test_after_login_admin_accessible(self) -> None:
        _login(self.client)
        patches = _mock_db_patch()
        with patch.multiple("blueprints.admin", **patches):
            resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 200)

    def test_logout_clears_session(self) -> None:
        _login(self.client)
        self.client.get("/admin/logout")
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login", resp.headers["Location"])


class TestAdminUsers(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()
        _login(self.client)

    def test_users_list_200(self) -> None:
        rows = [
            {
                "id": 1,
                "username": "p1",
                "serial": "S",
                "hash": "h",
                "reset_nonce": None,
                "deleted": 0,
                "updated_on": None,
            }
        ]
        patches = _mock_db_patch(browse_users_result=(1, rows))
        with patch.multiple("blueprints.admin", **patches):
            resp = self.client.get("/admin/users")
        self.assertEqual(resp.status_code, 200)

    def test_user_detail_not_found_404(self) -> None:
        patches = _mock_db_patch(find_user=None)
        with patch.multiple("blueprints.admin", **patches):
            resp = self.client.get("/admin/users/9999")
        self.assertEqual(resp.status_code, 404)

    def test_user_detail_found_200(self) -> None:
        user = {
            "id": 1,
            "username": "p1",
            "serial": "S",
            "hash": "h",
            "reset_nonce": None,
            "deleted": 0,
            "updated_on": None,
        }
        patches = _mock_db_patch(find_user=user)
        with patch.multiple("blueprints.admin", **patches):
            resp = self.client.get("/admin/users/1")
        self.assertEqual(resp.status_code, 200)


class TestAdminUserLock(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()
        _login(self.client)

    def test_userlock_get_returns_200(self) -> None:
        resp = self.client.get("/admin/userlock")
        self.assertEqual(resp.status_code, 200)

    def test_userlock_post_success(self) -> None:
        user = {"id": 3, "username": "target"}
        patches = _mock_db_patch(find_user=user)
        with patch.multiple("blueprints.admin", **patches):
            resp = self.client.post("/admin/userlock", data={"username": "target"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"locked", resp.data.lower())
        patches["lock_user"].assert_called_once()

    def test_userlock_post_unknown_user_returns_404(self) -> None:
        patches = _mock_db_patch(find_user=None)
        with patch.multiple("blueprints.admin", **patches):
            resp = self.client.post("/admin/userlock", data={"username": "ghost"})
        self.assertEqual(resp.status_code, 404)


class TestAdminSettings(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()
        _login(self.client)

    def test_settings_get_200(self) -> None:
        resp = self.client.get("/admin/settings")
        self.assertEqual(resp.status_code, 200)

    def test_settings_post_200(self) -> None:
        resp = self.client.post("/admin/settings", data={"max_users": "500"})
        self.assertEqual(resp.status_code, 200)


class TestAdminLog(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()
        _login(self.client)

    def test_log_get_200(self) -> None:
        resp = self.client.get("/admin/log")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
