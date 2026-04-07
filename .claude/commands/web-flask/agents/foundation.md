Run the Flask web migration foundation steps (00-03) in sequence.

These steps build the infrastructure layer: directory scaffold, config loader, DB layer, and crypto helpers. Each step has its own branch. After each step, run the tests before proceeding.

## Step 00 — Scaffold

Read and execute: .claude/commands/web-flask/00-scaffold.md

Verify: the flask-web/ directory exists and Python can import the stub app.

## Step 01 — Config

Read and execute: .claude/commands/web-flask/01-config.md

Verify: `python -m unittest flask-web/tests/test_config.py` passes.
Stop if any test fails — do not proceed to step 02.

## Step 02 — DB layer

Read and execute: .claude/commands/web-flask/02-db.md

Verify: `python -m unittest flask-web/tests/test_db.py` passes.
Stop if any test fails.

## Step 03 — Crypto

Read and execute: .claude/commands/web-flask/03-crypto.md

Verify: `python -m unittest flask-web/tests/test_crypto.py` passes.
The blowfish known-value test MUST pass — this is the critical correctness gate.
Stop if it fails.

## Final report

After all steps:
```
## Foundation Status
- [ ] 00-scaffold: flask-web/ created, imports OK
- [ ] 01-config: YamlConfig loads fiveserver.yaml
- [ ] 02-db: PyMySQL query functions mocked and tested
- [ ] 03-crypto: blowfish round-trip + known-value test pass
```

Report any failures and do NOT proceed to the services agent if any step failed.
