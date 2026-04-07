from __future__ import annotations

import os
import struct
import socket
from typing import Any

import yaml


class AppConfig:
    """
    Thin wrapper around a YAML config dict.
    Ported from lib/fiveserver/config.py (YamlConfig), Twisted-free.
    """

    def __init__(self, data: dict[str, Any], yaml_file: str | None = None) -> None:
        object.__setattr__(self, '_cfg', dict(data))
        object.__setattr__(self, '_yaml_file', yaml_file)
        object.__setattr__(self, '_dirty_keys', set())
        for k, v in self._cfg.items():
            object.__setattr__(self, k, v)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        if not name.startswith('_'):
            try:
                self._cfg[name] = value
                self._dirty_keys.add(name)
            except AttributeError:
                pass

    def __getitem__(self, key: str) -> Any:
        return self._cfg[key]

    def __iter__(self):  # type: ignore[override]
        return iter(self._cfg.items())

    def get(self, key: str, default: Any = None) -> Any:
        return self._cfg.get(key, default)

    def save(self) -> None:
        """Write only changed keys back to the source YAML file.

        Reloads the original file first so structure and order are preserved.
        Admin-only keys (from admin.yaml) are never written to fiveserver.yaml.
        Uses 4-space indentation to match the project's YAML style.
        """
        if self._yaml_file is None:
            raise RuntimeError('No YAML file path set — cannot save config')
        if not self._dirty_keys:
            return
        with open(self._yaml_file, encoding='utf-8') as f:
            original: dict[str, Any] = yaml.safe_load(f) or {}
        for key in self._dirty_keys:
            original[key] = self._cfg[key]
        with open(self._yaml_file, 'wt', encoding='utf-8') as f:
            yaml.dump(original, f, indent=4, default_flow_style=False,
                      sort_keys=False, allow_unicode=True)
        self._dirty_keys.clear()


def make_fast_banned_list(banned_specs: list[str]) -> list[tuple[int, int]]:
    """
    Convert a list of IP/network specs (e.g. ['192.168.1.0/24', '10.0.0.1'])
    into (masked_ip_int, mask_int) tuples for O(n) ban checking.
    Ported from FiveServerConfig.makeFastBannedList in lib/fiveserver/config.py.
    """
    result: list[tuple[int, int]] = []
    for spec in banned_specs:
        parts = spec.split('/')
        if len(parts) == 2:
            try:
                net_str, bits = parts[0], int(parts[1])
            except ValueError:
                net_str, bits = parts[0], 0
            if bits <= 0:
                continue
        elif len(parts) == 1:
            net_str = parts[0]
            bits = 0
        else:
            continue

        quads = [0, 0, 0, 0]
        good = True
        for i, quad in enumerate(net_str.split('.')):
            if quad == '':
                continue
            try:
                quads[i] = int(quad)
            except ValueError:
                good = False
                break
        if not good:
            continue

        net_buf = b''.join(struct.pack('!B', q) for q in quads)
        net_int = struct.unpack('!I', net_buf)[0]
        if bits == 0:
            bits = sum(8 for q in quads if q != 0)
        mask = (2 ** int(bits) - 1) << (32 - int(bits))
        result.append((net_int, mask))
    return result


def is_banned(ip: str, fast_list: list[tuple[int, int]]) -> bool:
    """Return True if ip matches any (net, mask) tuple in fast_list."""
    try:
        ip_int = struct.unpack('!I', socket.inet_aton(ip))[0]
    except OSError:
        return False
    for net, mask in fast_list:
        if (net & mask) == (ip_int & mask):
            return True
    return False


def load_config(yaml_path: str, admin_yaml_path: str | None = None) -> AppConfig:
    """
    Load the main fiveserver YAML config and optionally merge admin config on top.
    Paths are resolved relative to the repo root if not absolute.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _resolve(path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(repo_root, path)

    main_path = _resolve(yaml_path)
    with open(main_path, encoding='utf-8') as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    if admin_yaml_path is not None:
        admin_path = _resolve(admin_yaml_path)
        if os.path.exists(admin_path):
            with open(admin_path, encoding='utf-8') as f:
                admin_data: dict[str, Any] = yaml.safe_load(f) or {}
            data.update(admin_data)

    return AppConfig(data, yaml_file=main_path)
