Run the Flask web migration service-layer steps (04-10) in sequence.

Prerequisite: foundation steps (00-03) must be complete and all tests passing.

## Step 04 — Register blueprint

Read and execute: .claude/commands/web-flask/04-register-blueprint.md

Verify: `python -m unittest flask-web/tests/test_register.py` passes.
Stop if any test fails.

## Step 05 — Register templates

Read and execute: .claude/commands/web-flask/05-register-templates.md

Verify: `python -m unittest flask-web/tests/test_register.py` still passes (no regressions now that templates exist).

## Step 06 — Admin blueprint

Read and execute: .claude/commands/web-flask/06-admin-blueprint.md

Verify: `python -m unittest flask-web/tests/test_admin.py` passes.
Stop if any test fails — the BasicAuth and userlock tests are critical.

## Step 07 — Admin templates

Read and execute: .claude/commands/web-flask/07-admin-templates.md

Verify: `python -m unittest flask-web/tests/test_admin.py` still passes.
The admin CSS file must exist at `flask-web/static/admin.css`.

## Step 08 — Stats blueprint

Read and execute: .claude/commands/web-flask/08-stats-blueprint.md

Verify: `python -m unittest flask-web/tests/test_stats.py` passes.

## Step 09 — Go internal API

Read and execute: .claude/commands/web-flask/09-go-internal-api.md

Verify: `go build ./...` in fiveserver-go/ passes.

## Step 10 — Wiring and Dockerfile

Read and execute: .claude/commands/web-flask/10-wiring-docker.md

Verify: `python -m unittest discover flask-web/tests/` — all tests pass.

## Final report

```
## Web Services Status
- [ ] 04-register-blueprint: 5 routes, IP ban, blowfish hash
- [ ] 05-register-templates: form.html, result.html
- [ ] 06-admin-blueprint: 20 endpoints, BasicAuth
- [ ] 07-admin-templates: sidebar layout, CSS, all pages
- [ ] 08-stats-blueprint: /stats/* read-only, BasicAuth
- [ ] 09-go-internal-api: /internal/online-users endpoint
- [ ] 10-wiring-docker: run.py, Dockerfile, env var overrides
```
