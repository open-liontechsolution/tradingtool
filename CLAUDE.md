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
| `database.py` | Unified `get_db()` async context manager. SQLite via `aiosqlite` (dev), PostgreSQL via `asyncpg` (prod). SQLite schema is created inline by `init_db()`; PostgreSQL runs `alembic upgrade head` automatically at startup |
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
- **Leverage & liquidation (post-#50)**: `signal_configs.maintenance_margin_pct` (default 0.005) feeds the isolated-margin liquidation formula computed at entry fill: long → `entry × (1 − 1/lev + mm)`, short → `entry × (1 + 1/lev − mm)`. Result lands in `sim_trades.liquidation_price` (NULL for `leverage ≤ 1`). The intrabar poller checks **liquidation before stop**: if price crosses `liquidation_price` first, the trade closes with `exit_reason='liquidated'` at `liquidation_price`. Otherwise the existing stop logic runs. After every close (intrabar, candle, manual), `_maybe_mark_blown` clamps `current_portfolio` at 0 if it dipped below; when it does, `signal_configs.status` flips to `'blown'`, `blown_at` is stamped, and an `account_blown` notification fires (once — re-running is idempotent). `signal_engine._get_active_configs` excludes `status='blown'` rows, so no new signals open until the user calls `POST /api/signals/configs/{id}/reset-equity` (restores `current_portfolio = initial_portfolio`, status back to `'active'`, clears `blown_at`; sim_trade history is *not* touched).
- **Sim-trade lifecycle**: `pending_entry` → `open` → `closed`. On close, `exit_reason` ∈ `{stop_intrabar, stop_candle, exit_signal, liquidated, manual, config_deleted}`.
- **Stop levels** (post-#49): `stop_base` is the strategy's raw stop *and* the only stop level — there is no trigger-buffer separate from it. Both backtest and live close at `stop_base` for the same dataset+params. The intrabar poller compares ticker price against `stop_base`; if there's a gap (price already past `stop_base` when the ticker fires), the trade closes at the actual ticker price — not at `stop_base` — to model what an exchange stop-market would actually fill at. The candle-close path mirrors the same gap rule via the strategy emitting `stop_long`/`stop_short` and `live_tracker` falling back to the candle's open when it is past `stop_base`.
- **Trailing stop (`move_stop`)**: strategies may emit `Signal(action="move_stop", stop_price=<new_base>)`. `live_tracker._apply_stop_moves` accepts the move only if it *tightens* the stop (long: new base > current; short: new base < current); loosening moves are logged and ignored. On accept, `stop_base` on `sim_trades` is updated and a row is appended to `sim_trade_stop_moves` (only `prev_stop_base` / `new_stop_base` — there is no separate trigger to track). The `notify_event(event_type="stop_moved", ...)` call uses `reference_type="sim_trade_stop_move"` + `reference_id=sim_trade_stop_moves.id` so multiple moves per trade don't collide on the dedup index. In the backtest loop, `move_stop` simply updates `state.stop_price` — no history row (the trade_log still captures the eventual exit).
- **Same-candle exit/entry order**: when an exit fires on a closed candle, `signal_engine.scan_config` will *not* open a new entry on that same candle even if the strategy would emit one with state=flat. The `_has_trade_closed_on_candle` guard mirrors backtest's `exit_executed` short-circuit, keeping the two engines in sync.
- **Entry fill semantics (`modo_ejecucion`)**: `_fill_pending_entries` mirrors backtest. `open_next` (default): entry price is the open of the candle after the trigger and `entry_time = trigger_candle_time + step_ms`. `close_current`: entry price is the close of the trigger candle itself and `entry_time = trigger_candle_time`. Unknown/missing values fall back to `open_next` so legacy configs keep working.

## Key Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | unset | Full PostgreSQL URL; if unset, SQLite is used |
| `DB_PATH` | `data/trading_tools.db` | SQLite path (ignored when `DATABASE_URL` is set) |
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

- **Marker `slow`**: parity tests run under `@pytest.mark.slow` and are excluded from the default `pytest -q` (configured in `pyproject.toml`). Run them with `pytest -m slow tests/integration/test_parity.py`. CI has a dedicated non-blocking `test-parity` job.
- **Slot fixtures** live in `tests/fixtures/parity/<slot>.json.gz`:
  - **slot_a** — BTCUSDT 4h, 2023-2024 (~4400 candles, ~140 KiB gzipped). Base slot.
  - **slot_b** — BTCUSDT 1h, 2024 Q1 (~2200 candles, ~70 KiB). High-density, exercises trailing `move_stop`.
  - **slot_c** — ETHUSDT 4h, full 2022 (~2200 candles, ~67 KiB). Bear-market regime.
  - Regenerate any slot with `python -m tests.fixtures.parity._seed_slot_<x>` (or all the dependencies you need; the helper module `_seeder.py` does the actual download).
- **Test matrix**: `tests/integration/test_parity.py::test_parity_slot_strategy` is parametrised over `slot_name × strategy_name`. Strategies covered: `breakout`, `breakout_trailing`, `support_resistance`, `support_resistance_trailing` (×3 slots = 12 cases). All run with `modo_ejecucion=close_current` (the only mode with full engine parity post-#49 — `open_next` has a residual exit-fill gap that is its own follow-up).
- **What's compared**: structural fields per trade — `entry_time`, `exit_time`, `side`, `entry_price`, `exit_price`, normalized `exit_reason` (`stop_long`/`stop_intrabar`/`stop_candle` → `"stop"`, etc.). PnL/quantity numeric parity holds for `cost_bps=0` configs because backtest's compounding equity matches live's `current_portfolio` evolution (#48). Helper: `assert_trade_logs_equal(bt_log, live_log)`.
- **What's NOT compared yet** (deferred to dedicated follow-ups, not blockers for #51):
  - **Intrabar polling**: harness drives candle-close logic only. Intrabar exits at `stop_base` (#49) but the harness doesn't inject worst-case ticker prices per candle.
  - **Leverage liquidation parity (Block 4)**: live now models `liquidation_price` (#50) but backtest only tracks bankruptcy (`equity ≤ 0`). Slot D (SOLUSDT 15m with `leverage > 1`) is deferred until backtest also computes a per-trade `liquidation_price`.
  - **`open_next` mode**: backtest exits at the *current* candle's open while live exits at close — same trade list, different exit prices. Out of scope for the harness as configured.
- **Adding a new slot**: write a wrapper in `tests/fixtures/parity/_seed_slot_<x>.py` that calls `seed_slot(...)` from `_seeder.py`, run it once, commit the resulting `.json.gz`, and add the slot name to `_SLOTS` in `test_parity.py`.

## Deployment

- Container: multi-stage Dockerfile (Node 22 Alpine builds frontend, Python 3.13-slim runs app).
- CD: push to `develop` → GitHub Actions builds and pushes multi-arch image to GHCR, updates `helm/env/dev.yaml` image tag for Argo CD to pick up.
- Kubernetes: Helm chart in `helm/`; deployed to k3s via Argo CD.
