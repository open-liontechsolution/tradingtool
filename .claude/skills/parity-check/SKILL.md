---
name: parity-check
description: Guard the backtest↔live engine parity invariant. Detects whether the current diff touches the parity-sensitive engine paths and, if so, runs the slow parity harness locally (or offers to fire the nightly workflow). Use before requesting review on changes to backtest_engine, signal_engine, live_tracker, or strategies.
disable-model-invocation: true
---

# parity-check

CLAUDE.md cadence: the parity harness is NOT run at PR time (too slow). Anyone touching
`backend/{backtest_engine,signal_engine,live_tracker,strategies}/` is expected to run it
manually before review. This skill makes that step impossible to forget.

## What to do

1. **Scope the diff.** Run `git diff --name-only main...HEAD` (and `git status --short` for
   uncommitted work). Decide whether any path matches the parity-sensitive set:
   - `backend/backtest_engine.py`
   - `backend/signal_engine.py`
   - `backend/live_tracker.py`
   - `backend/strategies/**`
   - `backend/risk.py` (shared sizing/skip helpers — feeds both engines)
   - `tests/integration/test_parity.py` or `tests/fixtures/parity/**`

2. **If nothing matches:** report "no parity-sensitive paths touched — harness not required" and stop.

3. **If something matches:** run the harness locally and report pass/fail per slot:
   ```bash
   .venv/bin/python -m pytest -m slow tests/integration/test_parity.py -v
   ```
   To scope to a single slot while iterating, use `PARITY_SLOTS=slot_a` (parsed by
   `_enabled_slots()`). All four slots run when unset.

4. **If a case fails**, surface the failing slot × strategy and the first diverging trade
   (`assert_trade_logs_equal` prints the mismatch). Remind the user the usual root cause is a
   formula/ordering/param-reading change wired into only ONE engine — point at the specific
   "must be wired in lockstep" note in CLAUDE.md for that feature (#142 skip toggle, #144 sizing,
   #50/#58 liquidation, etc.).

5. **Mention the CI alternative**: the full matrix also runs nightly on `develop`, and can be
   fired manually with `gh workflow run "Parity nightly"` — useful if local fixtures are missing
   or you want the canonical runner result before review.

Report only — do not edit code. If parity breaks, offer to investigate the divergence as a
separate step.
