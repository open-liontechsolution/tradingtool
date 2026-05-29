---
name: schema-lockstep
description: Verify the inline SQLite schema in backend/database.py::init_db() is in lockstep with the latest Alembic revision. Reports any table or column that exists in one path but not the other. Use before opening a PR that adds or changes a DB column/table, or when CLAUDE.md's "lands in both or neither" rule needs an audit.
disable-model-invocation: true
---

# schema-lockstep

CLAUDE.md invariant: *"Keep the inline SQLite migration in `init_db()` and the Alembic revision in lockstep — both must produce the same final schema. Any new column/table lands in both or neither."* Nothing in CI enforces this. This skill audits it on demand.

## What to do

1. **Read the SQLite source of truth.** Open `backend/database.py` and read `init_db()` (around line 272). Capture:
   - Every `CREATE TABLE` and its columns/types from the inline SQL strings.
   - Every additive `ALTER TABLE ... ADD COLUMN` guarded by `PRAGMA table_info` (these are the incremental migrations).
   The *effective* SQLite schema is the base `CREATE TABLE` plus all the guarded `ADD COLUMN`s.

2. **Read the Postgres source of truth.** List `alembic/versions/*.py` in revision order (follow `down_revision` links, not filename order, to get the true chain head). Accumulate the net schema: `op.create_table`, `op.add_column`, `op.drop_column`, `op.alter_column`. The result of replaying the whole chain is the *effective* Postgres schema.

3. **Diff the two effective schemas**, table by table. For each table report:
   - Columns present in SQLite but missing in Alembic (or vice-versa).
   - Type/nullability/default mismatches that would change behaviour (note SQLite TEXT-vs-numeric storage quirks documented in CLAUDE.md — `klines` numeric fields are intentionally TEXT).
   - Tables present in one path only.

4. **Cross-check the docs.** CLAUDE.md keeps a "Core tables" list in the Database section. Flag any table that exists in the schema but is missing from that list (or vice-versa).

## Output

A compact report:

```
## schema-lockstep audit

✅ in lockstep: klines, download_jobs, derived_metrics, ...
⚠️  DRIFT:
  - sim_trades.sizing_clipped — present in init_db() (ALTER guard line N) but NOT in any alembic revision
  - <table>.<col> — present in alembic/0NN_*.py but NOT in init_db()
📝 docs: CLAUDE.md core-tables list missing `<table>`
```

If everything matches, say so explicitly and name the head Alembic revision you replayed to. Do **not** edit any files — this skill only reports. If drift is found, offer to write the missing migration as a separate step.
