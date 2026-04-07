Implement Jinja2 templates for the registration blueprint.

Read first:
- web/form-sample.html    (original registration form — port this)
- web/result-sample.html  (original result page — port this)
- flask-web/static/md5.js (to confirm the JS function name)

Create branch `web-flask/step-05-register-templates` from `web-flask` before making any changes.

## What to implement

### flask-web/templates/base.html

Minimal HTML5 base template:
- `<html lang="en">` with a `<head>` block (meta charset, viewport, title block)
- `<body>` with a `{% block content %}{% endblock %}` area
- No external CSS imports — keep it simple and self-contained
- Link to `/static/admin.css` only on admin pages (not here)

### flask-web/templates/register/form.html

Port `web/form-sample.html` exactly, replacing Python `%(var)s` placeholders with Jinja2 `{{ var }}`.

Required template variables (passed by the blueprint):
- `serial` — pre-filled serial (empty string for new registrations)
- `username` — pre-filled username
- `nonce` — empty for new, filled for modify

The form must:
- `<form method="POST" action="/register">`
- `<input type="hidden" name="nonce" value="{{ nonce }}">`
- Include the client-side `makeHash()` JS function (calls `hex_md5(...)`)
- `<script src="/md5.js"></script>` to load the MD5 library
- Call `makeHash()` on form submit to populate a hidden `hash` field before POST

### flask-web/templates/register/result.html

Port `web/result-sample.html`. Template variable: `message` (success or error string).

## No new Python code in this step

This step is templates only. The blueprint from step-04 already renders these templates.

## Verification

Start the app (`python flask-web/run.py`) and open `http://localhost/` in a browser.
- The registration form loads without errors
- Client-side validation runs (serial length, username alphanum, password length)
- Submitting the form POSTs to `/register`

Also confirm: `python -m unittest flask-web/tests/test_register.py` still passes (templates exist now, so `render_template` calls won't fail).

After verification, merge `web-flask/step-05-register-templates` → `web-flask`.
