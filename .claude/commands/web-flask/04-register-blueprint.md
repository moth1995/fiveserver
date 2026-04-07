Implement the registration blueprint in flask-web/blueprints/register.py.

Read first:
- lib/fiveserver/register.py   (source — port all logic)
- flask-web/db.py              (query functions to call)
- flask-web/crypto.py          (blowfish_encrypt, is_banned)

Create branch `web-flask/step-04-register-blueprint` from `web-flask` before making any changes.

## What to implement

`flask-web/blueprints/register.py` — four routes, no auth required.

```python
register_bp = Blueprint('register', __name__)
```

### Routes

**GET /**
- Render `register/form.html` with `serial=''`, `username=''`, `nonce=''`

**GET /md5.js**
- `send_from_directory(current_app.static_folder, 'md5.js')`

**GET /modifyUser/<nonce>**
- Look up user by nonce via `find_user_by_nonce()`
- If not found: abort(404)
- Render `register/form.html` with `serial=user['serial']`, `username=user['username']`, `nonce=nonce`

**POST /register**
- Read form fields: `serial`, `user` (username), `hash`, `nonce`
- IP ban check: `is_banned(request.remote_addr, current_app.config['BANNED_LIST'])` → abort(403)
- If nonce is empty or None → new registration:
  - Check if username exists → 409 if taken
  - `create_user()` + `create_profiles_for_user()`
  - Render `register/result.html` with `message='Registration complete'`
- Else → modification:
  - Look up user by nonce → abort(404) if not found
  - `update_user()` + clear nonce (set to None)
  - Render `register/result.html` with `message='Account updated'`
- On any DB error: abort(500)

## Registration in `create_app()`

In `flask-web/app.py`, register the blueprint:
```python
from flask_web.blueprints.register import register_bp
app.register_blueprint(register_bp)
```

Also store the banned list in app config:
```python
app.config['BANNED_LIST'] = crypto.make_fast_banned_list(cfg.get('BannedList', []))
```

## Type annotations

`from __future__ import annotations` at top. All functions fully annotated.

## Unit tests

In `flask-web/tests/test_register.py`, `unittest.TestCase` with `unittest.mock.patch('flask_web.blueprints.register.get_db')`:
1. `test_get_form` — GET / returns 200 with form HTML
2. `test_post_new_user_success` — POST /register with new username returns 200 + "Registration complete"
3. `test_post_username_taken` — POST /register with existing username returns 409
4. `test_post_modify_by_nonce` — POST /register with valid nonce returns 200
5. `test_post_invalid_nonce` — POST /register with unknown nonce returns 404

## Verification

`python -m unittest flask-web/tests/test_register.py` — all tests pass.

After verification, merge `web-flask/step-04-register-blueprint` → `web-flask`.
