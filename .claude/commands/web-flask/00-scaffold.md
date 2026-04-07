Create the flask-web/ directory tree with stub files for the fiveserver web layer migration.

Read the plan first: C:\Users\marco\.claude\plans\serialized-moseying-cosmos.md
Read the reference skill format: .claude/commands/go-migration/00-scaffold.md

Create branch `web-flask/step-00-scaffold` from `web-flask` before making any changes.

## What to create

Create `flask-web/` at the repo root (sibling of `lib/`, `fiveserver-go/`) with this structure:

```
flask-web/
├── app.py                     (stub: from flask import Flask; def create_app(): return Flask(__name__))
├── config.py                  (empty stub with package-level comment)
├── db.py                      (empty stub)
├── crypto.py                  (empty stub)
├── blueprints/
│   ├── __init__.py            (empty)
│   ├── register.py            (stub Blueprint)
│   ├── admin.py               (stub Blueprint)
│   └── stats.py               (stub Blueprint)
├── templates/
│   ├── base.html              (minimal HTML5 skeleton)
│   ├── register/
│   │   ├── form.html          (empty block extends base.html)
│   │   └── result.html        (empty block extends base.html)
│   ├── admin/
│   │   ├── layout.html        (empty block)
│   │   ├── home.html          (extends layout.html)
│   │   ├── users.html         (extends layout.html)
│   │   ├── user_detail.html   (extends layout.html)
│   │   ├── profile_detail.html(extends layout.html)
│   │   ├── log.html           (extends layout.html)
│   │   ├── banned.html        (extends layout.html)
│   │   └── settings.html      (extends layout.html)
│   └── stats/
│       ├── home.html          (extends layout.html)
│       └── users.html         (extends layout.html)
├── static/
│   ├── md5.js                 (copy from web/md5.js — unchanged)
│   └── admin.css              (empty placeholder)
├── run.py                     (stub: print("fiveserver web starting"))
├── requirements.txt           (see below)
└── tests/
    ├── __init__.py            (empty)
    ├── test_register.py       (stub unittest.TestCase)
    ├── test_admin.py          (stub unittest.TestCase)
    └── test_stats.py          (stub unittest.TestCase)
```

## requirements.txt

```
Flask>=3.0
PyMySQL>=1.1
PyYAML>=6.0
pycryptodome>=3.20
bcrypt>=4.1
gunicorn>=22.0
```

## Code style rules (apply to all stubs and all future steps)

- `from __future__ import annotations` at top of every .py file
- Type annotations on all function signatures and non-obvious variables
- Tests use `unittest.TestCase` only — no pytest

## Verification

Run: `python -c "from flask_web.app import create_app; print('OK')"` from repo root — should print OK without error.

(Add `flask-web/` to sys.path or rename to `flask_web` for importability — use `flask-web` as directory name but keep `flask_web` as the Python package by adding `__init__.py` at `flask-web/__init__.py`.)

After verification, merge `web-flask/step-00-scaffold` → `web-flask`.
