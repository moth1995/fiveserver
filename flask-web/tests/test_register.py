"""Tests for registration routes (now in the public blueprint)."""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from helpers import create_test_app


class TestRegisterForm(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()

    def test_get_form_returns_200(self) -> None:
        resp = self.client.get("/register")
        self.assertEqual(resp.status_code, 200)

    def test_get_md5js_returns_200(self) -> None:
        resp = self.client.get("/md5.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"md5", resp.data.lower())

    def test_modify_user_not_found_returns_404(self) -> None:
        with (
            patch("blueprints.public.get_db", return_value=MagicMock()),
            patch("blueprints.public.find_user_by_nonce", return_value=None),
        ):
            resp = self.client.get("/modifyUser/badnonce")
        self.assertEqual(resp.status_code, 404)

    def test_modify_user_found_returns_200(self) -> None:
        user = {
            "id": 1,
            "username": "player1",
            "serial": "SER01234567890123456",
            "reset_nonce": "validnonce",
        }
        with (
            patch("blueprints.public.get_db", return_value=MagicMock()),
            patch("blueprints.public.find_user_by_nonce", return_value=user),
        ):
            resp = self.client.get("/modifyUser/validnonce")
        self.assertEqual(resp.status_code, 200)


class TestRegisterPost(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = self.app.test_client()
        self.valid_hash = "098f6bcd4621d373cade4e832627b4f6"

    def _post(self, username="newuser", nonce="", extra=None):  # type: ignore[return]
        data = {
            "user": username,
            "serial": "SERIAL01234567890123",
            "hash": self.valid_hash,
            "nonce": nonce,
        }
        if extra:
            data.update(extra)
        return self.client.post("/register", data=data)

    def test_new_registration_success(self) -> None:
        with (
            patch("blueprints.public.get_db", return_value=MagicMock()),
            patch("blueprints.public.find_user_by_username", return_value=None),
            patch("blueprints.public.create_user", return_value=1),
            patch("blueprints.public.record_user_registration"),
        ):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Registration complete", resp.data)

    def test_username_taken_returns_409(self) -> None:
        existing = {"id": 5, "username": "newuser"}
        with (
            patch("blueprints.public.get_db", return_value=MagicMock()),
            patch("blueprints.public.find_user_by_username", return_value=existing),
        ):
            resp = self._post()
        self.assertEqual(resp.status_code, 409)
        self.assertIn(b"taken", resp.data)

    def test_modify_by_nonce_success(self) -> None:
        user = {
            "id": 3,
            "username": "oldname",
            "serial": "OLD",
            "reset_nonce": "nonce1",
        }
        with (
            patch("blueprints.public.get_db", return_value=MagicMock()),
            patch("blueprints.public.find_user_by_nonce", return_value=user),
            patch("blueprints.public.update_user") as mock_update,
        ):
            resp = self._post(username="newname", nonce="nonce1")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"updated", resp.data)
        mock_update.assert_called_once()

    def test_modify_invalid_nonce_returns_404(self) -> None:
        with (
            patch("blueprints.public.get_db", return_value=MagicMock()),
            patch("blueprints.public.find_user_by_nonce", return_value=None),
        ):
            resp = self._post(nonce="badnonce")
        self.assertEqual(resp.status_code, 404)

    def test_banned_ip_returns_403(self) -> None:
        from config import make_fast_banned_list

        with self.app.test_request_context():
            self.app.config["BANNED_LIST"] = make_fast_banned_list(["127.0.0.1"])
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.app.config["BANNED_LIST"] = []


if __name__ == "__main__":
    unittest.main()
