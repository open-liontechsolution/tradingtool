# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Trading Tools Laboratory — a full-stack application for downloading historical crypto data from Binance, running backtests, and monitoring live signals and simulated trades. Backend is FastAPI (Python 3.13), frontend is React 19 + Vite.

## Commands

### Backend

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt       # production deps
pip install -r requirements-dev.txt   # adds pytest, pytest-asyncio, ruff

# Run
python run.py                         # uvicorn on :8000

# Lint / format
ruff check backend/ tests/
ruff format --check backend/ tests/
ruff format backend/ tests/           # auto-fix

# Tests
pytest -q                             # all tests
pytest tests/test_foo.py::test_bar -v # single test
```

### Frontend

```bash
cd frontend
npm install
npm run dev    # dev server on :5173, proxied to :8000
npm run build  # produces frontend/dist/ (served by FastAPI in prod)
npm run lint   # ESLint
```

### Database migrations (PostgreSQL only)

```bash
DATABASE_URL=postgresql://... alembic upgrade head
```

## Architecture

### Backend modules (`backend/`)

| Module | Role |
|---|---|
| `app.py` | FastAPI factory: registers routers, CORS, static file mount, starts two background asyncio loops at lifespan |
| `config.py` | Reads all env vars once at import; single source of truth for configuration |
| `auth.py` | Keycloak JWT validation via JWKS. `AUTH_ENABLED=false` bypasses auth with a hard-coded admin user |
| `database.py` | Unified `get_db()` async context manager. SQLite via `aiosqlite` (dev), PostgreSQL via `asyncpg` (prod). SQLite schema is created inline by `init_db()`; PostgreSQL runs `alembic upgrade head` automatically at startup. Postgres traffic goes through a shared asyncpg pool (`init_pg_pool` / `close_pg_pool`, hooked into the FastAPI lifespan); pool size is tunable via `PG_POOL_MIN_SIZE` / `PG_POOL_MAX_SIZE` / `PG_POOL_MAX_INACTIVE_LIFETIME` (see `init_pg_pool` docstring) |
| `binance_client.py` | Async `httpx` Binance Spot API client with 429/418 rate-limit handling and exponential backoff. Singleton instance |
| `download_engine.py` | Downloads klines in batches with upsert; tracks job progress in `download_jobs` table; `ensure_candles()` auto-fetches missing history |
| `metrics_engine.py` | Loads klines as pandas DataFrame, computes technical indicators (SMA, EMA, ATR, Donchian, etc.), saves to `derived_metrics` |
| `backtest_engine.py` | Vectorised backtest runner; results held in memory keyed by `backtest_id` |
| `backtest_metrics.py` | Computes performance stats (Sharpe, drawdown, win-rate, etc.) from a trade log |
| `signal_engine.py` | Background loop: polls active `signal_configs`, calls `ensure_candles`, runs `strategy.on_candle()`, persists signals, spawns sim trades |
| `live_tracker.py` | Background loop: updates open `sim_trades` (entry fills, stop-outs, exits) from live Binance ticker price; emits user-facing notifications via `notifications.notify_event` |
| `notifications.py` | Unified dispatcher for user-facing trade events. Resolves the recipient from `signal_configs.user_id`, honours the per-config `telegram_enabled` toggle, dedupes through `notification_log (event_type, reference_type, reference_id, channel)` and sends Telegram when all gates pass. Supported event types: `entry`, `exit_signal`, `stop_hit`, `stop_moved`, `liquidated`, `account_blown`. Note that `stop_moved` uses `reference_type="sim_trade_stop_move"` + `reference_id=sim_trade_stop_moves.id` so each trailing move is a distinct dedup key (many moves per trade are expected); `account_blown` uses `reference_type="signal_config"` + `reference_id=config_id` so each config can blow at most once before reset |
| `telegram_client.py` | Thin async Telegram Bot API client (`send_message`, `set_webhook`, MarkdownV2 `escape_md`). No-op when `TELEGRAM_BOT_TOKEN` is unset — safe default for tests and CI |
| `strategies/base.py` | `Strategy` ABC: defines `get_parameters()`, `init()`, `on_candle()` interface shared by all strategies |
| `strategies/breakout.py` | Breakout strategy implementation |
| `strategies/breakout_trailing.py` | Breakout variant that inherits from `BreakoutStrategy` and emits `move_stop` using a rolling Min/Max trailing reference |
| `strategies/support_resistance.py` | Support/resistance strategy implementation |
| `strategies/support_resistance_trailing.py` | Support/resistance variant that inherits from `SupportResistanceStrategy` and emits `move_stop` from the latest confirmed zigzag level |
| `api/data_routes.py` | `/api/pairs`, `/api/download`, `/api/candles`, `/api/metrics`, ... |
| `api/backtest_routes.py` | `/api/strategies`, `/api/backtest` |
| `api/signal_routes.py` | `/api/signals`, `/api/sim-trades`, `/api/real-trades`. `signal_configs` payloads accept `telegram_enabled` on create/patch |
| `api/profile_routes.py` | `/api/profile/telegram` — link-token issuance, status, unlink |
| `api/telegram_routes.py` | `/api/telegram/webhook/{secret}` — **not** protected by Keycloak; authenticated via path secret + `X-Telegram-Bot-Api-Secret-Token` header |

### Frontend modules (`frontend/src/`)

| Module | Role |
|---|---|
| `auth/` | OIDC login via `oidc-client-ts`; `AuthProvider` fetches config from `/api/auth/config`; `apiFetch` wraps `fetch` with Bearer token |
| `DataManager.jsx` | Download historical data, view candles, compute metrics (admin-only) |
| `BacktestPanel.jsx` | Configure and run backtests; shows equity chart and trade log |
| `SignalsPanel.jsx` | Manage signal configs (incl. per-config Telegram toggle); view active signals and sim/real trades |
| `ProfilePanel.jsx` | User profile page; Telegram linking flow (generate link → user pastes deep-link → poll until bound) |
| `EquityChart.jsx` | Recharts equity curve |
| `TradeReviewChart.jsx` | `lightweight-charts` candlestick chart for individual trade review |

### Database

Two modes selected by the `DATABASE_URL` env var:

- **SQLite (default/dev):** schema created inline by `init_db()`, file at `data/trading_tools.db`. Additive migrations use `PRAGMA table_info` + `ALTER TABLE` guards.
- **PostgreSQL (prod):** Alembic runs automatically at startup. Migrations in `alembic/versions/`.

Core tables: `klines`, `download_jobs`, `derived_metrics`, `users`, `signal_configs`, `signals`, `sim_trades`, `sim_trade_stop_moves`, `real_trades`, `notification_log`, `telegram_link_tokens`.

Keep the inline SQLite migration in `init_db()` and the Alembic revision in lockstep — both must produce the same final schema. Any new column/table lands in both or neither.

### Strategy plugin pattern

New strategies must extend `backend/strategies/base.py::Strategy`, implement `get_parameters()`, `init()`, and `on_candle()`, then be registered in the strategy registry used by `backtest_routes.py` and `signal_engine.py`.

### Telegram notifications

One shared bot per deployment. Each user links their own chat on first use:

1. Frontend profile page calls `POST /api/profile/telegram/link-token` → backend generates a one-time token (15 min TTL) and returns a `https://t.me/<bot>?start=<token>` deep-link.
2. User opens the link and sends `/start <token>` to the bot.
3. Telegram POSTs the update to `/api/telegram/webhook/{secret}`; the webhook consumes the token and stores `telegram_chat_id` / `telegram_username` on the user row.

Outbound alerts flow through a single entry point — `notifications.notify_event` — which live_tracker calls at seven sites (entry fill, exit signal, intrabar stop, candle-close stop, trailing stop move, intrabar liquidation, account-blown transition). The dispatcher filters by the per-config `telegram_enabled` toggle + the user's linked chat, and dedupes on `notification_log (event_type, reference_type, reference_id, channel)`. The whole subsystem is inert (no HTTP, no reply) whenever `TELEGRAM_BOT_TOKEN` is empty — this is the invariant that keeps tests and CI green without mocking the network.
### Live-mode invariants

- **Equity model (post-#48)**: `signal_configs.initial_portfolio` is the *immutable* starting capital (set at create time). `signal_configs.current_portfolio` starts equal to `initial_portfolio` and evolves: each closed sim_trade applies its `net_pnl` atomically (in the same transaction that flips the trade to `closed`). New sim_trades dimension against `current_portfolio` at the moment of entry — that snapshot lands in `sim_trades.portfolio` for audit. Editing `initial_portfolio` (via PATCH) is treated as a relabel of the starting capital and does *not* touch `current_portfolio`.
- **Leverage & liquidation (post-#50, post-#58 Gap 1)**: `signal_configs.maintenance_margin_pct` (default 0.005) feeds the isolated-margin liquidation formula computed at entry fill: long → `entry × (1 − 1/lev + mm)`, short → `entry × (1 + 1/lev − mm)`. The shared helper is `backend.live_tracker.compute_liquidation_price` — backtest imports it directly so both engines apply the same formula. Result lands in `sim_trades.liquidation_price` (NULL for `leverage ≤ 1`). Liquidation is checked in **two** places, both with priority over the strategy's stop: (a) the intrabar poller (`_check_intrabar_stops`) compares the live ticker against `liquidation_price`, and (b) the candle-close path (`_check_candle_close_exits`) compares the candle's low/high against `liquidation_price` as a backstop in case the intrabar poll missed the cross. Either path closes the trade at `liquidation_price` with `exit_reason='liquidated'`. After every close, `_maybe_mark_blown` clamps `current_portfolio` at 0 if it dipped below; when it does, `signal_configs.status` flips to `'blown'`, `blown_at` is stamped, and an `account_blown` notification fires (once — idempotent). `signal_engine._get_active_configs` excludes `status='blown'` rows, so no new signals open until the user calls `POST /api/signals/configs/{id}/reset-equity` (restores `current_portfolio = initial_portfolio`, status back to `'active'`, clears `blown_at`; sim_trade history is *not* touched). Backtest mirrors this with a local `blown` flag that suppresses subsequent entries — keeping the two engines aligned through and beyond a liquidation event.
- **Sim-trade lifecycle**: `pending_entry` → `open` → (`pending_exit` →) `closed`. The intermediate `pending_exit` state only appears under `modo_ejecucion='open_next'` (#58 Gap 2): when the strategy fires an exit/stop signal at candle close, the trade flips to `pending_exit` with `pending_exit_reason` recording why; `_fill_pending_exits` closes it at the next candle's open. `close_current` skips the intermediate state and closes immediately. On close, `exit_reason` ∈ `{stop_intrabar, stop_candle, exit_signal, liquidated, manual, config_deleted}`.
- **Stop levels** (post-#49): `stop_base` is the strategy's raw stop *and* the only stop level — there is no trigger-buffer separate from it. Both backtest and live close at `stop_base` for the same dataset+params. The intrabar poller compares ticker price against `stop_base`; if there's a gap (price already past `stop_base` when the ticker fires), the trade closes at the actual ticker price — not at `stop_base` — to model what an exchange stop-market would actually fill at. The candle-close path mirrors the same gap rule via the strategy emitting `stop_long`/`stop_short` and `live_tracker` falling back to the candle's open when it is past `stop_base`.
- **Trailing stop (`move_stop`)**: strategies may emit `Signal(action="move_stop", stop_price=<new_base>)`. `live_tracker._apply_stop_moves` accepts the move only if it *tightens* the stop (long: new base > current; short: new base < current); loosening moves are logged and ignored. On accept, `stop_base` on `sim_trades` is updated and a row is appended to `sim_trade_stop_moves` (only `prev_stop_base` / `new_stop_base` — there is no separate trigger to track). The `notify_event(event_type="stop_moved", ...)` call uses `reference_type="sim_trade_stop_move"` + `reference_id=sim_trade_stop_moves.id` so multiple moves per trade don't collide on the dedup index. In the backtest loop, `move_stop` simply updates `state.stop_price` — no history row (the trade_log still captures the eventual exit).
- **Same-candle exit/entry order**: when an exit fires on a closed candle, `signal_engine.scan_config` will *not* open a new entry on that same candle even if the strategy would emit one with state=flat. In `close_current` the `_has_trade_closed_on_candle` guard mirrors backtest's `exit_executed` short-circuit. In `open_next` (#58 Gap 2) the trade is in `pending_exit` between signal and fill, and `_has_active_trade` (which now includes `pending_exit`) blocks the same-candle scan; the next iteration sees `status='closed'` and is free to enter on the fill candle, matching backtest's post-fill entry handling.
- **Entry / exit fill semantics (`modo_ejecucion`)** (post-#58 Gap 2):
  - `close_current`: entry fills at the **close of the trigger candle**; exit fills at the **close of the signal candle**. `entry_time = trigger_candle_time`, `exit_time = signal_candle_open_time`. Both engines fire and fill in the same iteration.
  - `open_next` (default): entry fills at the **open of the candle after the trigger** (`entry_time = trigger_candle_time + step_ms`); exit fills at the **open of the candle after the signal** (`exit_time = signal_candle_open_time + step_ms`). Backtest queues `pending_entry` / `pending_exit` and fills on the next iteration; live persists `status='pending_exit'` (with `pending_exit_reason`) and closes via `_fill_pending_exits` at the next candle's open. Liquidations never defer — they're intrabar events that fire immediately at `liquidation_price`.
  - Unknown/missing values fall back to `open_next` so legacy configs keep working.
- **Max-loss-per-trade risk filter (#142)**: optional per-config gate at the entry decision point. When `signal_configs.max_loss_per_trade_enabled` is true and the strategy emits `entry_long`/`entry_short`, both engines compute `equity_loss_pct = notional_share × max(leverage, 1) × distance_pct` (where `distance_pct = abs(entry_price - stop_base) / entry_price` and `entry_price` is the **trigger candle's close** in both engines — chosen for parity, not the realised fill price). If it exceeds `max_loss_per_trade_pct`, the entry is dropped. Shared helper: `backend.risk.should_skip_for_max_loss` (same DRY pattern as `compute_liquidation_price`). Live records the skipped setup as `signals (status='skipped_risk', stop_price=stop_base)` so the user can audit how many setups got dropped per config — dedup uses the existing `idx_signals_dedup (config_id, trigger_candle_time)` unique index. Backtest skips silently (no audit row, mirrors how `blown=True` drops entries). Backtest reads the toggle + threshold from the strategy params dict; live reads them from `signal_configs` columns; both must be wired in lockstep when configuring a parity case.

## Key Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | unset | Full PostgreSQL URL; if unset, SQLite is used |
| `DB_PATH` | `data/trading_tools.db` | SQLite path (ignored when `DATABASE_URL` is set) |
| `PG_POOL_MIN_SIZE` | `2` | Minimum idle connections in the asyncpg pool |
| `PG_POOL_MAX_SIZE` | `20` | Maximum connections in the asyncpg pool (per replica) |
| `PG_POOL_MAX_INACTIVE_LIFETIME` | `300` | Seconds before an idle connection is recycled |
| `AUTH_ENABLED` | `false` | Set `true` to enable Keycloak JWT validation |
| `KEYCLOAK_URL` | `""` | Keycloak base URL |
| `KEYCLOAK_REALM` | `tradingtool-dev` | Keycloak realm |
| `KEYCLOAK_AUDIENCE` | `tradingtool-api` | JWT audience / API client ID |
| `KEYCLOAK_FRONTEND_CLIENT_ID` | `tradingtool-web` | Returned to frontend via `/api/auth/config` |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `PUBLIC_BASE_URL` | `""` | Used to build "Ver trade" links in outbound Telegram messages |
| `TELEGRAM_BOT_TOKEN` | `""` | Bot API token. **When empty, the whole Telegram subsystem is no-op** — tests and CI rely on this |
| `TELEGRAM_BOT_USERNAME` | `""` | Bot username (no `@`); used to build the `https://t.me/<bot>?start=<token>` deep-link in link-token responses |
| `TELEGRAM_WEBHOOK_SECRET` | `""` | Secret embedded in the webhook path and verified in the `X-Telegram-Bot-Api-Secret-Token` header |
| `TELEGRAM_WEBHOOK_URL` | `""` | If set (together with the bot token), `setWebhook` is invoked once at app lifespan startup |
| `IMAGE_TAG` | `unknown` | Injected by Helm from `.Values.image.tag`; surfaced in `/healthz` so the QA smoke can verify the served pod matches the just-promoted build (#114). Don't read in app code. |

Frontend-only (placed in `frontend/.env.development.local`, only needed to override dev defaults):
`VITE_AUTH_ENABLED`, `VITE_KEYCLOAK_URL`, `VITE_KEYCLOAK_REALM`, `VITE_KEYCLOAK_CLIENT_ID`

## Code Style

- Linter/formatter: **ruff** (`ruff.toml`). `target-version = "py313"`, line-length 120.
- Tests: **pytest** + **pytest-asyncio**. Async tests use `@pytest.mark.asyncio` directly — no project-level `conftest.py`.

## Testing conventions

- **No shared `conftest.py`**: each test file defines its own `_use_temp_db` autouse fixture (sets `DB_PATH` env var *and* patches `backend.database.DB_PATH`) plus local `_insert_config` / `_setup_db` helpers. Follow this pattern in new tests.
- **Time-travel tests**: `signal_engine.py` and `live_tracker.py` each define their own `_now_ms()` (copy, not shared import). Patch both when a test spans the two modules: `patch("backend.signal_engine._now_ms", return_value=fake_ms)` and `patch("backend.live_tracker._now_ms", ...)`.
- **Patch imported functions at the consumer**: `ensure_candles` and `load_candles_df` are imported into `signal_engine` / `live_tracker`. Patch `backend.signal_engine.ensure_candles` (the binding in that module), not `backend.download_engine.ensure_candles`.
- `klines` numeric fields are stored as TEXT strings (Binance format) — cast with `str()` on insert, `float()` on read.

### Parity harness (`tests/integration/test_parity.py`)

Replays a fixed klines fixture through both the backtest engine and the live engine (`signal_engine.scan_config` + `live_tracker._fill_pending_entries` + `_check_candle_close_exits`) and asserts trade-log equivalence — same entries, same exits, same exit reasons, same prices.

- **Marker `slow`**: parity tests run under `@pytest.mark.slow` and are excluded from the default `pytest -q` (configured in `pyproject.toml`). Run them with `pytest -m slow tests/integration/test_parity.py`.
- **Cadence in CI** (post-#69, revised in #92):
  - **PR-time**: NOT run. The slot_a sample tried in #69 still took >8 min on ubuntu-latest runners (well above the 30-40s projection), burning quota on every PR while being non-blocking anyway. Removed from `ci.yml`.
  - **Nightly** (`.github/workflows/parity-nightly.yml`): full matrix on `develop` at 03:00 UTC. Plus `workflow_dispatch` so anyone touching `backend/{backtest_engine,signal_engine,live_tracker,strategies}/` can fire it manually with `gh workflow run "Parity nightly"` before requesting review.
  - `PARITY_SLOTS` env var still works (parsed by `_enabled_slots()` in `test_parity.py`) — useful when running locally or manually scoping a nightly to a specific slot. Empty/unset → all 4 slots.
- **Slot fixtures** live in `tests/fixtures/parity/<slot>.json.gz`:
  - **slot_a** — BTCUSDT 4h, 2023-2024 (~4400 candles, ~140 KiB gzipped). Base slot.
  - **slot_b** — BTCUSDT 1h, 2024 Q1 (~2200 candles, ~70 KiB). High-density, exercises trailing `move_stop`.
  - **slot_c** — ETHUSDT 4h, full 2022 (~2200 candles, ~67 KiB). Bear-market regime.
  - **slot_d** — SOLUSDT 15m, 2024 Q2 (~8700 candles, ~210 KiB). Volatile / dense; the leveraged-liquidation matrix lives here.
  - Regenerate any slot with `python -m tests.fixtures.parity._seed_slot_<x>` (or all the dependencies you need; the helper module `_seeder.py` does the actual download).
- **Test matrix** (three functions in `test_parity.py`, ~32 cases total):
  - `test_parity_slot_strategy` — unleveraged (`leverage=1.0`) over `slot_name × strategy_name` in `close_current` mode. 4 slots × 4 strategies = 16 cases.
  - `test_parity_open_next_slot_strategy` — same matrix as above but with `modo_ejecucion='open_next'`. Slot D excluded (composition with leverage is well-covered). 3 slots × 4 strategies = 12 cases. Exercises the deferred fill semantic from #58 Gap 2.
  - `test_parity_leveraged_slot_d` — slot_d × strategy with `leverage=10`, `maintenance_margin_pct=0.005`, `close_current`. Exercises liquidation parity (#58 Gap 1): both engines close at `liquidation_price` with `exit_reason='liquidated'` when the candle's low/high crosses it, and stop opening new entries from that point onward (live: `status='blown'`; backtest: local `blown` flag).
- **What's compared**: structural fields per trade — `entry_time`, `exit_time`, `side`, `entry_price`, `exit_price`, normalized `exit_reason` (`stop_long`/`stop_intrabar`/`stop_candle` → `"stop"`; `liquidated` → `"liquidated"`; etc.). PnL/quantity numeric parity holds for `cost_bps=0` configs because backtest's compounding equity matches live's `current_portfolio` evolution (#48). Helper: `assert_trade_logs_equal(bt_log, live_log)`.
- **Liquidation formula**: shared between engines via `backend.live_tracker.compute_liquidation_price`. Backtest reads `leverage` and `maintenance_margin_pct` from the strategy params dict; live reads them from `signal_configs` columns. Both produce the same per-trade `liquidation_price` for the same `(side, entry_price, leverage, mm)` tuple.
- **What's NOT compared yet**:
  - **Intrabar polling**: harness drives candle-close logic only. Intrabar exits in live (`_check_intrabar_stops`) and the candle-close fallback share the same `stop_base` gap-fill rule (#49) plus the same `liquidation_price` priority (#50/#58), so the harness exercises the relevant logic without injecting worst-case ticker prices per candle.
- **Adding a new slot**: write a wrapper in `tests/fixtures/parity/_seed_slot_<x>.py` that calls `seed_slot(...)` from `_seeder.py`, run it once, commit the resulting `.json.gz`, and add the slot name to `_SLOTS` in `test_parity.py`.

## Deployment

- Container: multi-stage Dockerfile (Node 22 Alpine builds frontend, Python 3.13-slim runs app).
- Kubernetes: Helm chart in `helm/`; deployed to k3s via Argo CD. The Argo `Application` manifests live in `argocd/` for reproducibility but are NOT picked up by the chart itself (they're applied manually with `kubectl apply` to the `argocd` namespace).
- Two environments live in the same k3s cluster, isolated by namespace and Secret:

| Aspect | dev | qa |
|---|---|---|
| Namespace (app) | `tradingtool-dev` | `tradingtool-qa` |
| Helm values file | `helm/env/dev.yaml` | `helm/env/qa.yaml` |
| Secret name | `tradingtools-secret-dev` | `tradingtools-secret-qa` |
| Public URL | `tradingtool-dev.liontechsolution.com` | `tradingtool-qa.liontechsolution.com` |
| Postgres (CNPG) | shared cluster `platform-postgres-dev` in `data-dev`, DB `tradingtool-dev`, user `tradingtool-dev-user` | shared cluster `platform-postgres-dev` in `data-dev`, DB `tradingtool-qa`, user `tradingtool-qa-user` |
| Keycloak realm | `tradingtool-dev` | `tradingtool-qa` |
| Telegram bot | dedicated bot from `@BotFather` | dedicated bot from `@BotFather` |
| Cloudflare Tunnel | dedicated tunnel (sidecar pattern) | dedicated tunnel (sidecar pattern) |
| `LOG_LEVEL` | `debug` | `info` |
| Argo `syncPolicy` | manual | automated (prune + selfHeal) |
| Build workflow | `.github/workflows/build-dev.yml` | `.github/workflows/build-qa.yml` |
| Trigger | push to `develop` (every commit) | `workflow_dispatch` (manual) **or** push of tag `v*.*.*-rc*` |

- **dev pipeline**: push to `develop` → `build-dev.yml` builds multi-arch image, scans with Trivy (HIGH/CRITICAL fixable blocks promotion), updates `helm/env/dev.yaml` image tag, and finally runs a `cleanup-old-versions` job that prunes GHCR (keep last 90 — multi-arch creates 3 versions per push, so this retains ~30 builds, ≈3 days of heavy activity). The cleanup runs **last** because Trivy resolves the manifest-list digest and pulls platform-child manifests; deleting GHCR versions before Trivy finishes can vanish those children mid-scan and break the resolution. Argo `trading-tool` Application is sync-manual, so a human still clicks Sync in the Argo UI.
- **qa pipeline**: same shape as dev plus a `resolve-tag` job at the front that branches on event:
  - `push: tags: 'v*.*.*-rc*'` → image tag is the version itself (e.g. `v1.2.0-rc1`).
  - `workflow_dispatch` → image tag is `qa-<short_sha>`.
  
  After Trivy passes, `update-tag` writes the new tag to `helm/env/qa.yaml`. The Argo `trading-tool-qa` Application is sync-automated, so any commit to develop that touches `helm/env/qa.yaml` rolls out without manual click.
- **qa smoke E2E**: last job of `build-qa.yml`. Polls `/healthz` until 200 (10-min timeout), checks `/api/auth/config`, then — if `KEYCLOAK_QA_URL` / `SMOKE_USER_QA` / `SMOKE_PASSWORD_QA` repo secrets are configured — does a Direct Access Grants login against `tradingtool-qa` realm and round-trips a dummy `signal_config` (POST → GET → DELETE). All curl calls against `tradingtool-qa.liontechsolution.com` carry `CF-Access-Client-Id` / `CF-Access-Client-Secret` headers (repo secrets `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`) so Cloudflare Access lets them through the bot-protection layer; real users hit a separate Bypass policy and don't see Access at all. Keycloak lives on a different host outside the QA Access Application, so its token request runs without CF headers. The job is `continue-on-error: true` until the smoke pattern is stable; promote to required once it has been green for a few weeks. Without the smoke secrets the auth block is skipped (just logs a warning).
- **QA versioning convention**: tags follow `vMAJOR.MINOR.PATCH-rcN`. The mental model has four bump kinds (matching the inputs of the `Tag QA release candidate` workflow added in #85):
  - **major** (`v1.2.3-rc4` → `v2.0.0-rc1`) — breaking changes / redesign.
  - **minor** (`v1.2.3-rc4` → `v1.3.0-rc1`) — new feature, backwards-compatible.
  - **patch** (`v1.2.3-rc4` → `v1.2.4-rc1`) — bug fix on a version already in prod (starts a fresh QA cycle).
  - **hotfix** (`v1.2.3-rc4` → `v1.2.3-rc5`) — iterate within the current QA cycle because the previous rc had a bug.
  When a `-rcN` is validated in QA it gets re-tagged as the prod release (separate workflow when prod exists).
- **One-click QA tagging** (post-#85): Actions → **Tag QA release candidate** → Run workflow → choose `bump` → it computes the next tag from the last `vX.Y.Z-rcN`, pushes it, and `build-qa.yml` fires automatically on the new tag. No need to remember the last version or to be in a checkout with push privileges.
- **Secrets**: never committed. Live under `helm/secrets/<env>.yaml` (per-env, gitignored). Template at `helm/secrets/example.yaml` (committed). Apply with `kubectl apply -f helm/secrets/<env>.yaml`. Legacy locations (`helm/env/secrets.yaml`, `helm/env/secrets-*.yaml`) stay covered by `.gitignore` for backwards compatibility, but new env manifests should land under `helm/secrets/`.
- **Postgres provisioning**: the CNPG cluster `platform-postgres-dev` is shared. The dev DB+user were created by hand; the qa DB (`tradingtool-qa`) and user (`tradingtool-qa-user`) are created the same way (one-off `psql` against the superuser, see `argocd/qa-application.yaml` header for the actual SQL).

## Supply chain (post-#79)

- **SBOM per build**: `build-dev.yml` and `build-qa.yml` each include a `generate-sbom` job that runs `anchore/sbom-action` against the freshly pushed image digest and uploads the SPDX-JSON output as a workflow artifact (`sbom-dev-<sha>.spdx.json` / `sbom-qa-<tag>.spdx.json`). Non-blocking on failure — Trivy already gates on CVEs; the SBOM exists for offline auditing. Download from the Actions run page → Artifacts.
- **Dependency review on PRs**: `ci.yml` has a `dependency-review` job using `actions/dependency-review-action@v4`. Blocks PRs that introduce dependencies with CVEs ≥ HIGH. Posts a comment on the PR when it fails (`comment-summary-in-pr: on-failure`).
- **Secret scanning (gitleaks)** (post-#80): `.github/workflows/secret-scan.yml` runs gitleaks on every PR to develop/main and on every push to any branch (so a leak that lands on a feature branch before a PR exists still gets caught — see the #73 incident). It's a required gate (no `continue-on-error`); the check **context** reported by Actions is just the job name **`Gitleaks (CLI)`** — that's what goes in `required_status_checks[].context` (NOT `Secret scan / Gitleaks (CLI)`, which is the workflow/job display name in the UI but never the API context — see CI gotcha "Required-context name vs UI display name"). GitHub's native secret scanning + push protection are enabled separately via repo Settings → Code security and analysis.
- Out of scope here: `cosign` signing / SLSA provenance, scanning the SBOM against an external vuln DB (Trivy already covers the image surface).

## Branch protection (post-#81)

Both `develop` and `main` are protected via repository **rulesets** (Settings → Rules → Rulesets — not the legacy "Branch protection rules" UI). Public repos on free tier support rulesets fully. Rulesets can be edited in the UI or via `gh api repos/:owner/:repo/rulesets/<id>` — the JSON bodies used to apply the current config live at `/tmp/tt-rulesets/*.json` in #81's PR description for reference.

| Aspect | `develop-merge-protection` (id 13359417) | `main-protection` (id 15652898) |
|---|---|---|
| `deletion` rule | ON | ON |
| `non_fast_forward` rule | ON | ON |
| `required_linear_history` | OFF (squash/rebase suffices) | **ON** (no merge commits) |
| `pull_request.required_approving_review_count` | 0 | 0 |
| `pull_request.dismiss_stale_reviews_on_push` | ON | ON |
| `pull_request.required_review_thread_resolution` | ON | ON |
| `pull_request.allowed_merge_methods` | squash, rebase | squash, rebase |
| `required_status_checks.strict_required_status_checks_policy` | ON (branches up-to-date before merge) | ON |
| Required checks (API contexts — job names only, not `workflow / job`) | Lint backend (ruff) / Lint frontend (ESLint) / Test backend (pytest) / Build frontend (Vite) / **Gitleaks (CLI)** | Lint backend (ruff) / Lint frontend (ESLint) / Test backend (pytest) / Build frontend (Vite) |
| `bypass_actors` | one Integration (actor_id 3008410) — kept so the auto-bot can push tag bumps to `helm/env/dev.yaml` from `build-dev.yml::update-tag` | empty |

Approvals stay at **0** because the project is solo today; status checks already gate. Bump to `1` (and add yourself as a bypass actor, or wait for the second collaborator) when the team grows.

`required_linear_history` only applies to `main` because we want a clean linear release history when prod releases start landing there. `develop` allows non-linear-history-but-squash-merged PRs to keep the integration branch ergonomics intact.

The Gitleaks check is required only on `develop` because the secret-scan workflow runs on every PR to develop/main + every push (#80) — by the time a PR reaches main from develop it has already passed Gitleaks once.

If a future workflow needs to push directly to `main` (e.g., a release-promotion workflow that tags `vX.Y.Z` from `develop@HEAD`), add its GitHub App as a bypass actor on `main-protection` — same pattern as develop's tag-bump bot.

## CI gotchas worth remembering

- **Pushes from `GITHUB_TOKEN` don't fire downstream workflows.** GitHub suppresses them as loop prevention. So any workflow that pushes a tag/commit and expects another workflow to react (e.g. `tag-qa-release.yml` → `build-qa.yml::on.push.tags`) MUST author the push with the GitHub App token (`TT_APP_ID` / `TT_APP_PRIVATE_KEY`), wired through `actions/checkout`'s `token:` so subsequent `git push` runs under App credentials. `build-dev.yml::update-tag`, `build-qa.yml::update-tag`, and `tag-qa-release.yml` all follow this pattern. We hit this on the first run after #85 (PR #100 fix).
- **`paths-ignore` + required status checks = catch-22.** The `develop-merge-protection` ruleset requires `Lint backend / Lint frontend / Test backend / Build frontend` to pass. If `ci.yml`'s `paths-ignore` is broad enough that a PR touches ONLY ignored paths, CI skips entirely → required checks never report → mergeStateStatus=BLOCKED. Resolved in #92 (removed `.github/workflows/**`) and #103 (removed `helm/env/**`). `ci.yml` now has no `paths-ignore` — every PR runs CI. Auto-bot tag bumps to `helm/env/**` are direct pushes to develop, never PRs, so PR-time filtering never helped them anyway.
- **Multi-arch + GHCR cleanup race.** `actions/delete-package-versions@v5` is not manifest-list-aware. Each platform child of a multi-arch manifest list shows up as a separate "package version" sorted by `created_at`. With low retention (we had `min-versions-to-keep: 15`) and a busy day of merges, the cleanup will eventually delete a platform child of a still-needed manifest list, leaving it un-pullable on that arch. Hit on the cluster's arm64 nodes during the post-#102 build (incident: #103). Mitigation: bumped retention to 90 (≈30 multi-arch builds, ≈3 days at 10 builds/day) which delays the race by an order of magnitude. Long-term fix is a manifest-list-aware cleanup (e.g. enumerate tags, find their referenced child digests, build an explicit "do not delete" set), tracked as a follow-up issue.
- **Required-check context name vs UI display name.** The GitHub UI shows checks as `<Workflow name> / <Job name>` (e.g. `Secret scan / Gitleaks (CLI)`), but the **API context** that goes in `required_status_checks[].context` is just the job name (`Gitleaks (CLI)`). Set the wrong one and the ruleset shows "Expected — Waiting for status to be reported" forever, even though the check actually ran and passed. Verify against `gh api repos/:owner/:repo/commits/<sha>/check-runs -q '.check_runs[].name'` — that's the truth. Hit when bootstrapping the Gitleaks required gate in #118 (initial commit used the UI display name; fixed in the same PR after a refresh on the screenshot showed it stuck pending).
- **Trailing newlines in GitHub Secrets break CF Access service tokens.** `gh secret set NAME` (interactive) and the web UI both store the trailing `\n` from the user pressing Enter. Curl in CI then sends `<value>\n` in the header; CF Access compares byte-for-byte and returns 403 silently. The token's "Last Seen" never updates, which is the diagnostic — if a freshly set service token shows "Not Seen Yet" after a smoke run, the secret almost certainly has a trailing newline. Set with `gh secret set NAME --body "$VAL"` (the `--body` flag never appends a newline) or `printf '%s' "$VAL" | gh secret set NAME`. The smoke E2E job in `build-qa.yml` now strips whitespace from both `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` defensively and logs their trimmed lengths (id=39, secret=64) so the next occurrence is instantly diagnosable.
- **`Closes #N` only auto-closes when the PR merges to the default branch.** Repo default is `main`; most active work merges to `develop` (and only later promotes to `main`). So a PR that merges to `develop` with `Closes #123` in the body does NOT close the issue — GitHub waits for the change to land on `main`. Implications during the pre-prod era (no production deployment yet): bugs found in QA and fixed via `develop` PRs need a manual `gh issue close <N> --reason completed --comment "Fixed in #<PR>, merged to develop"`. Once prod is up and `develop → main` promotion is the norm, prod-bound features and prod bug fixes will auto-close on the main merge — but anything resolved purely in QA before reaching prod still needs the manual close. Hit on PRs #130 / #131 (issues #125 / #127 / #128 stayed open after merge to develop until closed by hand).

## QA validation

- **CI smoke E2E** (`build-qa.yml::smoke-e2e`): scripted, runs after every QA build, validates `/healthz` + `/api/auth/config` + Keycloak login + create/get/delete a signal_config. Catches functional regressions on the API surface. Post-#114 the smoke poléa `/healthz` until the served `image_tag` matches the just-promoted rc (cap 4 min, hard-fail on timeout) — so we're always validating the new pod, not whichever pod was already serving traffic during the rolling update. Still soft-gated (`continue-on-error: true`) until the new contract has been green for ~2 weeks of releases.
- **Deep walkthrough** (`.claude/skills/qa-walkthrough/SKILL.md`): on-demand agent-driven exploration via playwright-cli + Firefox. Triggered only when the user explicitly asks ("haz un walkthrough de qa", "/qa-walkthrough"). Covers visual + functional flows that scripted tests can't judge (broken layouts, blank charts, console errors), creates a dummy config + cleans it up, writes a dated markdown report to `qa-walkthrough-reports/`. Screenshots are gitignored (heavy, regenerable); the markdown reports ARE committed for historical regression review. The skill is project-scoped so it evolves with the codebase — when a new panel ships, add a Stage section to the SKILL.md.
