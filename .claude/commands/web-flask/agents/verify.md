Verify the Flask web migration is complete and correct.

Read-only audit — do not make code changes. Report findings only.

## 1. Blowfish bit-exactness

Check that `flask-web/crypto.py` uses the same cipher key and ECB mode as `lib/fiveserver/register.py`.

- Read `lib/fiveserver/register.py` and extract the cipher key
- Read `flask-web/crypto.py` and confirm the same key is used
- Confirm both use `Blowfish.MODE_ECB`
- Run: `python -m unittest flask-web/tests/test_crypto.py -v` — the known-value test must pass

## 2. Admin endpoint coverage

Read `lib/fiveserver/admin.py` and list all routes (grep for `render_GET`, `render_POST`, `putChild`).
Read `flask-web/blueprints/admin.py` and list all `@admin_bp.route(...)` decorators.

Report:
- Routes in Twisted admin but missing from Flask admin
- Routes in Flask admin not in Twisted (new ones are fine)

## 3. Registration form integrity

Read `flask-web/templates/register/form.html` and verify:
- `<input type="hidden" name="nonce"` is present
- `<input type="hidden" name="hash"` is present (populated by JS)
- `<script src="/md5.js">` or equivalent is present
- `makeHash()` or equivalent JS function is called on form submit

## 4. Authentication enforcement

Read `flask-web/blueprints/admin.py` and confirm:
- `@admin_bp.before_request` calls `_require_auth()`
- All routes under `/admin` require auth (no route bypasses the before_request hook)

Read `flask-web/blueprints/stats.py` and confirm:
- `@stats_bp.before_request` also enforces auth

## 5. HTTP redirect

Read `flask-web/run.py` and confirm:
- A separate HTTP server on port 80 issues 301 redirects to HTTPS
- The main HTTPS app is the full `create_app()` result

## 6. All tests pass

Run: `python -m unittest discover flask-web/tests/ -v`

Report the full output. All tests must pass (OK, no failures or errors).

## Final report

```
## Verification Results

### Blowfish: PASS / FAIL
### Admin coverage: N/M routes matched
  Missing: [list]
### Registration form: PASS / FAIL
### Auth enforcement: PASS / FAIL
  Admin: PASS/FAIL
  Stats: PASS/FAIL
### HTTP redirect: PASS / FAIL
### Test suite: N passed, N failed

### Overall: PASS / FAIL
```
