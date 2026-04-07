Implement the YamlConfig loader in flask-web/config.py.

Read first:
- lib/fiveserver/config.py  (source of truth — port this class, drop Twisted)
- etc/conf/fiveserver.yaml  (config structure reference)
- etc/conf/admin.yaml       (admin config reference)

Create branch `web-flask/step-01-config` from `web-flask` before making any changes.

## What to implement

Port `FiveServerConfig` from `lib/fiveserver/config.py` into `flask-web/config.py` as a plain Python class with no Twisted dependencies. Name it `AppConfig`.

Key methods to port (drop anything that touches `reactor`, `defer`, or Twisted):
- `__init__(self, data: dict[str, Any])` — store the raw YAML dict
- `__getitem__(self, key: str) -> Any` — dict-style access
- `get(self, key: str, default: Any = None) -> Any`
- `save(self, path: str) -> None` — write back to YAML file (used for debug/maxusers toggles)
- `makeFastBannedList(self) -> list[tuple[int, int]]` — port the banned-IP CIDR list builder
- `isBanned(self, ip: str) -> bool` — check IP against fast banned list

Add a top-level loader function:
```python
def load_config(yaml_path: str, admin_yaml_path: str) -> AppConfig:
    ...
```

This function reads both YAML files, merges admin keys into the config dict, and returns an `AppConfig` instance.

## Type annotations

All parameters and return types must be annotated. Use `from __future__ import annotations` at the top.

## Unit test

In `flask-web/tests/test_config.py` (new file), add a `unittest.TestCase` that:
1. Loads `etc/conf/fiveserver.yaml` via `load_config()`
2. Asserts `config['DB']['name'] == 'fiveserver'`
3. Asserts `config.get('NonExistent', 'fallback') == 'fallback'`

## Verification

`python -m unittest flask-web/tests/test_config.py` — all tests pass.

After verification, merge `web-flask/step-01-config` → `web-flask`.
