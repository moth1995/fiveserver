from __future__ import annotations

import sys
import os
import unittest

# Allow imports from flask-web/ without installation
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import AppConfig, load_config, make_fast_banned_list, is_banned


class TestAppConfig(unittest.TestCase):

    def _make_cfg(self) -> AppConfig:
        return AppConfig({'DB': {'name': 'fiveserver', 'user': 'u', 'password': 'p'},
                          'MaxUsers': 100})

    def test_getitem(self) -> None:
        cfg = self._make_cfg()
        self.assertEqual(cfg['MaxUsers'], 100)

    def test_get_present(self) -> None:
        cfg = self._make_cfg()
        self.assertEqual(cfg.get('MaxUsers'), 100)

    def test_get_default(self) -> None:
        cfg = self._make_cfg()
        self.assertEqual(cfg.get('NonExistent', 'fallback'), 'fallback')

    def test_setattr_updates_cfg(self) -> None:
        cfg = self._make_cfg()
        cfg.MaxUsers = 200
        self.assertEqual(cfg['MaxUsers'], 200)

    def test_attribute_access(self) -> None:
        cfg = self._make_cfg()
        self.assertEqual(cfg.MaxUsers, 100)


class TestLoadConfig(unittest.TestCase):

    def test_load_fiveserver_yaml(self) -> None:
        cfg = load_config('etc/conf/fiveserver.yaml')
        db: dict = cfg['DB']
        self.assertEqual(db['name'], 'fiveserver')

    def test_load_with_admin_yaml(self) -> None:
        cfg = load_config('etc/conf/fiveserver.yaml', 'etc/conf/admin.yaml')
        self.assertIn('AdminUser', cfg._cfg)

    def test_get_nonexistent_returns_default(self) -> None:
        cfg = load_config('etc/conf/fiveserver.yaml')
        self.assertIsNone(cfg.get('NoSuchKey'))


class TestBanList(unittest.TestCase):

    def test_cidr_ban(self) -> None:
        fast = make_fast_banned_list(['192.168.1.0/24'])
        self.assertTrue(is_banned('192.168.1.5', fast))

    def test_cidr_not_matching(self) -> None:
        fast = make_fast_banned_list(['192.168.1.0/24'])
        self.assertFalse(is_banned('10.0.0.1', fast))

    def test_host_ban(self) -> None:
        fast = make_fast_banned_list(['10.0.0.1'])
        self.assertTrue(is_banned('10.0.0.1', fast))

    def test_empty_ban_list(self) -> None:
        fast = make_fast_banned_list([])
        self.assertFalse(is_banned('1.2.3.4', fast))

    def test_invalid_spec_skipped(self) -> None:
        fast = make_fast_banned_list(['not-an-ip'])
        self.assertFalse(is_banned('1.2.3.4', fast))


if __name__ == '__main__':
    unittest.main()
