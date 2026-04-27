---
name: qa-walkthrough
description: Manual deep walkthrough of the QA deployment driven by an agent (you) using playwright-cli + Firefox. Covers every user-facing panel, exercises real flows, captures visual sanity, and writes a dated markdown report to `qa-walkthrough-reports/`. ONLY runs when the user explicitly invokes it ("haz un walkthrough de qa", "/qa-walkthrough", "ejecuta la skill de qa", "revisa qa con playwright"). Never auto-fires — this is human-on-demand validation, not CI. Required tools: playwright-cli (firefox profile), kubectl with kubeconfig, gh, the QA URL `https://tradingtool-qa.liontechsolution.com`. The skill is read-only by default; it creates one dummy signal_config and deletes it on cleanup, never opens real trades.
---

# qa-walkthrough

A guided manual walkthrough of the QA deployment, executed by you (the agent) on the user's local machine through playwright-cli + Firefox. The output is a markdown report in `qa-walkthrough-reports/<YYYY-MM-DD>.md` (or `<YYYY-MM-DD>--<HH-MM>.md` if multiple runs same day) capturing what was tested, what worked, what looked off, with screenshot file paths for anything visually suspicious.

## When this fires

ONLY when the user explicitly asks. Trigger phrases include:

- "haz un walkthrough de qa"
- "ejecuta el qa-walkthrough"
- "/qa-walkthrough"
- "revisa qa a fondo"
- "smoke profundo"

Never on PR merge, never as part of CI, never proactively. The CI smoke (`build-qa.yml::smoke-e2e`) covers the basic surface (#114 follow-up to make it actually test the new pod). This skill complements that with judgment-rich visual review that no scripted test can do.

## Why Firefox

Chrome / Chromium has rendering bugs in WebKit-based environments running through playwright-cli (the user has hit them — preserved as memory). Firefox via Playwright works cleanly and is the default for this skill. If Firefox is unavailable, ask the user before falling back.

## Pre-flight checklist (run before any navigation)

1. **Confirm playwright-cli is available**: `which playwright-cli` or check the `playwright-cli` MCP tool surface.
2. **Confirm QA URL is reachable**: `curl -s -o /dev/null -w '%{http_code}\n' https://tradingtool-qa.liontechsolution.com/healthz` should return 200. If not, abort and report.
3. **Confirm the QA deployment is the version we want to test**:
   - Read `helm/env/qa.yaml` for the current `image.tag`.
   - `kubectl -n tradingtool-qa get pod -o jsonpath='{.items[*].spec.containers[*].image}'` — confirm it matches the tag in `helm/env/qa.yaml`.
   - If they don't match, Argo hasn't synced yet OR the pod is still rolling. **Wait or report and ask** — testing the wrong version is the bug #114 wants to prevent.
4. **Login credentials**: smoke user from secret `SMOKE_USER_QA` / `SMOKE_PASSWORD_QA` (Keycloak realm `tradingtool-qa`, client `tradingtool-api`). The user will paste these at run time — never store them or log them.
5. **Reports directory**: `mkdir -p qa-walkthrough-reports/screenshots/<YYYY-MM-DD>`. The screenshot subdir is gitignored.

## Navigation procedure

Drive Firefox via playwright-cli through these stages, in order. Take a screenshot ONLY when something looks off (broken layout, error toast, blank chart, console error). Green-path screenshots are noise.

### Stage 0 — Login

1. Navigate to `https://tradingtool-qa.liontechsolution.com/`.
2. Cloudflare Access bypass policy lets you through without service tokens (real users go this way too).
3. The frontend redirects to Keycloak (`https://keycloak-dev.liontechsolution.com/realms/tradingtool-qa/protocol/openid-connect/auth?...`). Fill the login form with the smoke user.
4. Wait for the SPA to load post-login. The header should show the user's email/name.
5. Open DevTools console (Playwright supports this). Note any errors. Acceptable: `CSP report-only` violations are blocked but the site works (CSP is enforcing post-#82, but old caches may still log violations briefly).

### Stage 1 — DataManager (admin only — smoke user IS admin per memory)

1. Click the DataManager tab.
2. Verify the pairs dropdown loads (calls `/api/pairs`).
3. **Read-only check**: do NOT trigger a real download. Just confirm the form renders, the candles preview area is reachable, and metrics computation form opens.
4. Look for: layout overflow, missing icons, broken Recharts.

### Stage 2 — SignalsPanel (creates one config — cleanup mandatory)

1. Click the SignalsPanel tab.
2. List existing configs (calls `/api/signals/configs`). Note count.
3. **Create one dummy config** with a clearly identifiable payload — include `qa_walkthrough_run_id: <ISO timestamp>` in `params` so it never collides with anything else and is trivially identifiable for cleanup:
   ```json
   {
     "symbol": "BTCUSDT",
     "interval": "1d",
     "strategy": "breakout",
     "params": {"lookback": 20, "stop_pct": 2.0, "qa_walkthrough_run_id": "<ISO>"},
     "initial_portfolio": 1000,
     "leverage": 1,
     "cost_bps": 0,
     "telegram_enabled": false
   }
   ```
4. Verify the config appears in the list with the expected fields (no `params` mangling, no Telegram toggle inverted, etc.).
5. Verify the active signals + sim_trades sub-views render (they may be empty — that's fine).
6. **`POST /api/sim-trades/{id}/close` is endpoint-only validated**: do not actually close any sim trade. Just confirm the button exists and is disabled appropriately when there's no open trade.
7. **Cleanup**: DELETE the dummy config you created. Verify it disappears from the list.

### Stage 3 — BacktestPanel

1. Click the BacktestPanel tab.
2. Configure a small backtest (e.g. BTCUSDT 4h breakout over the past 30 days). Real data may not be in QA DB if no one has downloaded — if the run errors with "no data", note it and skip without retry.
3. If it runs: verify equity chart renders, trade log table populates, summary stats (Sharpe, drawdown) appear.
4. Click on a trade to open `TradeReviewChart` — confirm `lightweight-charts` candlestick renders without overflow.

### Stage 4 — ProfilePanel

1. Click the ProfilePanel tab.
2. Verify Telegram link status shows correctly (linked/unlinked depending on this user).
3. **Read-only**: do not generate a link token (would create a notification log entry and could trigger Telegram).

### Stage 5 — Visual sanity sweep

1. Resize viewport to **1920x1080**: confirm no horizontal overflow on any panel.
2. Resize to **1366x768** (common laptop): confirm sidebar/charts adapt.
3. Spot-check dark-mode / light-mode if applicable (the app may not support both — check first).
4. DevTools console: count error-level logs. Anything other than expected CSP report-only violations gets noted in the report.

### Stage 6 — Cleanup verification

Before producing the report:

1. **Confirm no orphan dummy config in DB**:
   ```bash
   kubectl -n data-dev exec platform-postgres-dev-1 -c postgres -- \
     psql -U postgres -d tradingtool-qa -c "
     SELECT id FROM signal_configs
     WHERE params::text LIKE '%qa_walkthrough_run_id%';
     "
   ```
   Should return zero rows. If any, DELETE them by id.
2. Close the browser.

## Report format

Write `qa-walkthrough-reports/<YYYY-MM-DD>.md` (or with `--HH-MM` suffix if a same-day run already exists). Skeleton:

```markdown
# QA Walkthrough — <ISO date>

## Environment
- QA URL: https://tradingtool-qa.liontechsolution.com
- Image tag tested: <from helm/env/qa.yaml at start>
- Pod confirmed running tag: <from kubectl> (matched: yes/no)
- Browser: Firefox via playwright-cli (version X)
- User: <smoke user>
- Run started: <ISO timestamp>
- Run finished: <ISO timestamp>

## Per-stage results

### Stage 0 — Login
- Result: ✅ pass / ⚠️ warn / ❌ fail
- Notes: ...
- Screenshots: (none if green)

[... one section per stage ...]

## Summary

- Stages passed: N/6
- Issues found: <count>
- Console errors: <count, with breakdown>
- New issues to file (if any): <bulleted, with concrete repro>

## Cleanup verified
- [x] Dummy config deleted (id=X)
- [x] No leftover qa_walkthrough_run_id rows in DB
```

If any stage fails, also include the exact reproduction steps and at least one screenshot at `qa-walkthrough-reports/screenshots/<date>/<stage>-<short>.png`. The screenshot path is referenced in the report (markdown image syntax) but the file itself is gitignored — the user reads it locally.

## What this skill is NOT

- Not a load test (use `hey` or `wrk` for that).
- Not a security audit (separate concerns; CSP, rate limits, etc. live in their own tests).
- Not a parity validation (that's `parity-nightly.yml`).
- Not for prod (when prod exists, will need its own skill or extension — credentials and risk profile differ).

## Updating this skill

When the frontend gains a new panel or the API gains a flow worth covering, add a Stage section here. The skill grows with the codebase. Discovered a new visual gotcha worth checking? Add it to the Visual sanity sweep. Treat this file as living documentation of what "QA looks healthy" means.

## Related

- CI smoke E2E: `.github/workflows/build-qa.yml::smoke-e2e` — covers the four basic endpoints (auth/config, login, list, create+delete a config).
- Issue #114 — making the CI smoke wait for the actual rollout. When that lands, the CI smoke covers more confidently and this skill focuses on visual + exploratory.
