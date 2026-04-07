Orchestrate the full Flask web layer migration end-to-end.

Use this for a fresh migration from zero to a running Flask app.
Branch `web-flask` must already exist (created from master).

## Phase 1 — Foundation

Spawn a sub-agent with the contents of:
  .claude/commands/web-flask/agents/foundation.md

Wait for it to complete. Confirm all 4 foundation steps passed before continuing.
If any step failed, stop and report the failure — do not proceed to Phase 2.

## Phase 2 — Web Services

Spawn a sub-agent with the contents of:
  .claude/commands/web-flask/agents/web-services.md

Wait for it to complete.

## Phase 3 — Verification

Spawn a sub-agent with the contents of:
  .claude/commands/web-flask/agents/verify.md

## Final report

```
## Migration Status

### Completed
- [ ] 00-scaffold
- [ ] 01-config
- [ ] 02-db
- [ ] 03-crypto
- [ ] 04-register-blueprint
- [ ] 05-register-templates
- [ ] 06-admin-blueprint
- [ ] 07-admin-templates
- [ ] 08-stats-blueprint
- [ ] 09-go-internal-api
- [ ] 10-wiring-docker

### Test results
<unittest discover output>

### Next steps
- Run against a real MySQL database
- Test registration flow end-to-end with a browser
- Validate Blowfish compatibility with existing user records
- Phase 2: web accounts (email login + game account linking)
```
