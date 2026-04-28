---
name: qa-walkthrough
description: Manual deep walkthrough of the QA deployment driven by an agent (you) using playwright-cli + Firefox. Covers every user-facing panel, exercises real flows (including a small live Binance download to validate the data path), captures visual sanity, files one GitHub issue per real finding (severity-labeled), and writes a dated markdown report to `qa-walkthrough-reports/`. ONLY runs when the user explicitly invokes it ("haz un walkthrough de qa", "/qa-walkthrough", "ejecuta la skill de qa", "revisa qa con playwright"). Never auto-fires — this is human-on-demand validation, not CI. Required tools: playwright-cli (firefox profile), kubectl with kubeconfig, gh, the QA URL `https://tradingtool-qa.liontechsolution.com`. The skill creates one dummy signal_config (deleted on cleanup) and writes klines to QA Postgres via a small download (idempotent, kept). Never opens real trades.
---

# qa-walkthrough

A guided manual walkthrough of the QA deployment, executed by you (the agent) on the user's local machine through playwright-cli + Firefox. The output is two-fold: (1) a markdown report at `qa-walkthrough-reports/<YYYY-MM-DD>.md` (or `<YYYY-MM-DD>--<HH-MM>.md` for multiple same-day runs) capturing what was tested and what was found, and (2) one GitHub issue per real finding, labelled by severity, so follow-up work has an owner. Screenshots are taken locally during the run, used for triage, then deleted at the end — the issues become the durable record of bugs.

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
2. Verify the pairs dropdown loads, the interval dropdown is populated, the date pickers render, and the existing **Local Data Coverage** table loads (driven by `/api/coverage`).
3. **Trigger a real download — small but live**. The point is to exercise the Binance fetch path, the upsert into `klines`, and the progress UI end-to-end. Choose:
   - **Pair**: any pair from the dropdown that's *not* the most-loaded one in Coverage (so the test exercises a fetch, not a no-op upsert). `ETHUSDT` is a good default; if Coverage already has `ETHUSDT 1d` covering the past year, switch to a sibling like `BNBUSDT` or `SOLUSDT`.
   - **Interval**: `1d` — keeps the request small and the run fast (~365 candles per year).
   - **Window**: the last ~1 year (e.g. start = today − 365 days, end = today). This forces the engine to fetch *recent* data, which is the whole point — stale data tests nothing about live API health.
   - Click **Download / Update**.
4. Watch the progress UI:
   - A `download_jobs` row gets created server-side; the SPA polls it.
   - The progress bar / counter should move from 0 toward 100% within a few seconds (1d / 1y is one Binance request worth of candles).
   - On completion the Coverage table updates to reflect the new pair/interval row (or the existing row's `To` date advances to today).
   - The **Calculate Derived Metrics** button appears next to the download button (it's gated on `isCompleted` — see `DataManager.jsx:499`). You don't have to click it; just confirm it surfaces. If the button never appears after the job finishes, that's a regression worth filing.
5. Sanity-check what the run cost in rate-limit budget: the **Binance API Weight** indicator (top of DataConfiguration) should still be well under 1200 — a 1d / 1y request is cheap.
6. Look for: layout overflow, missing icons, broken Recharts, error toasts on the job row, jobs that hang at 0% (network or auth issue), or completion without the Coverage row updating.

**Failure handling**: if the download errors out (Binance 4xx/5xx, our backend 500, or job stuck at 0%), capture the toast / job-status text, note it as a Stage 1 finding, and continue to Stage 2 — this Stage's failure is itself the bug. Don't retry more than once; a flaky third-party isn't worth chasing inside the walkthrough.

**State left behind**: this stage writes klines into QA Postgres. That's intentional and not cleaned up — Binance market data is idempotent (upsert key = `symbol+interval+open_time`), it's useful for follow-up backtests, and it doesn't pollute anything. Stage 6's cleanup only targets the dummy `signal_config`, not klines.

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

Before triaging findings:

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

### Stage 7 — Triage findings → GitHub issues

Every real finding from stages 0–5 gets one GitHub issue. The report alone is not the tracking system — issues are. The report is the snapshot, the issues are the backlog.

**Severity labels** (created once via `gh label create`; idempotent with `--force`):

| Label | Meaning | Examples |
|---|---|---|
| `priority/p1` | Critical — blocks production, regression, or security | login broken, data loss, RCE, secret exposure |
| `priority/p2` | High — visible bug, core flow, affects all users | broken chart axis, key API returning 500, layout overflow on default viewport |
| `priority/p3` | Medium — minor bug or affects some users | wrong copy, console error that doesn't break flow, edge-case UX glitch |
| `priority/p4` | Low — UX nit, hardening, cleanup | duplicate field name, defensive null-check, deprecation warning |

Plus one type label per issue:
- `bug` — something is broken vs. its intended behavior.
- `enhancement` — improvement, hardening, UX nit (when "broken" is too strong).

**What does NOT get an issue**:
- Harness-only artifacts. Example: `navigator.language === "undefined"` in playwright Firefox triggering `Intl.DateTimeFormat` errors that real users never see. Note these in the report under "Informational / harness-only" and move on. (Optional: file a `priority/p4` enhancement if a defensive fix is cheap and worthwhile.)
- Already-filed bugs. Always run `gh issue list --search "<keyword> in:title"` first to avoid duplicates. If a matching open issue exists, add a comment instead of opening a new one.

**Issue contents**:

| Field | Content |
|---|---|
| Title | Short imperative summary, no priority prefix (the labels carry that signal). e.g. `Fix equity chart x-axis showing "Jan 1, 70"` |
| Body | What's broken / how to repro / expected vs. actual / root cause if known (`file.ext:line`) / proposed fix |
| Labels | One severity (`priority/pN`) + one type (`bug` or `enhancement`) |

**Screenshots in issues**: `gh issue create` does NOT support image upload. Most findings don't need a screenshot — a clear textual repro plus a `file:line` root-cause reference is more useful than a PNG. When a screenshot is genuinely essential (e.g., a layout glitch with no obvious DOM signal), reference the local path in the issue body and instruct the user to drag-drop it into the GitHub UI. Do this BEFORE Stage 8, since Stage 8 deletes the screenshots.

**Filing pattern**:

```bash
gh issue create \
  --title "<short summary>" \
  --label "bug,priority/p2" \
  --body-file - <<'EOF'
## What's broken
<one paragraph>

## Repro
1. Log in to https://tradingtool-qa.liontechsolution.com
2. <click path>
3. Observe <symptom>

## Expected vs. actual
- Expected: <X>
- Actual: <Y>

## Root cause (suspected)
`path/to/file.ext:LINE` — <code excerpt or one-line explanation>

## Proposed fix
<one or two sentences>

---
Found during qa-walkthrough on <YYYY-MM-DD> (image tag `vX.Y.Z-rcN`).
EOF
```

Capture each returned URL or `#N`. Append an `## Issues filed` section to the report listing them, sorted by descending severity:

```markdown
## Issues filed
- #142 — [`bug` `priority/p2`] Fix equity chart x-axis showing "Jan 1, 70"
- #143 — [`bug` `priority/p3`] Move pre-login inline script out of index.html
- #144 — [`enhancement` `priority/p4`] Default lightweight-charts locale defensively
```

If you found NOTHING that warrants an issue, write `## Issues filed\n\n_None — clean run._` instead. Don't skip the section; absence is also a signal.

### Stage 8 — Screenshot cleanup

Tracking has now moved to GitHub issues. Local screenshots were a temporary triage aid. Delete this run's subdir:

```bash
rm -rf qa-walkthrough-reports/screenshots/<YYYY-MM-DD>
```

The committed `<YYYY-MM-DD>.md` keeps the textual record plus the `## Issues filed` links. Future readers should consult the issues for live status — the report is a frozen snapshot. (Screenshots are gitignored anyway, so deleting them only affects local disk.)

If you flagged a "drag this screenshot into issue #N" instruction in Stage 7, confirm with the user it's been done before deleting. If the user is offline / hasn't actioned it, leave that one screenshot in place and note the exception in the report.

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

- Stages passed: N/8
- Real findings (filed as issues): <count>
- Harness-only / informational: <count>
- Console errors: <count, with one-line breakdown of types>

## Issues filed
- #N1 — [`bug` `priority/p2`] <title> — <one-liner>
- #N2 — [`bug` `priority/p3`] <title> — <one-liner>
- _or:_ _None — clean run._

## Cleanup verified
- [x] Dummy config deleted (id=X)
- [x] No leftover qa_walkthrough_run_id rows in DB
- [x] Screenshots dir removed (`qa-walkthrough-reports/screenshots/<YYYY-MM-DD>/`)
```

During the walkthrough, screenshots are temporary triage aids saved at `qa-walkthrough-reports/screenshots/<date>/<stage>-<short>.png`. The committed report can describe the bug textually without inline image links — readers consult the linked GitHub issue for the live record. Stage 8 deletes the screenshot subdir; do not link to a path that won't exist after the run ends.

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
