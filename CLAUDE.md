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
| `notifications.py` | Unified dispatcher for user-facing trade events. Resolves the recipient from `signal_configs.user_id`, honours the per-config `telegram_enabled` toggle, dedupes through `notification_log (event_type, reference_type, reference_id, channel)` and sends Telegram when all gates pass. Supported event types: `entry`, `exit_signal`, `stop_hit`, `stop_moved` (reserved for trailing-stop feature — see corresponding GitHub issue) |
| `telegram_client.py` | Thin async Telegram Bot API client (`send_message`, `set_webhook`, MarkdownV2 `escape_md`). No-op when `TELEGRAM_BOT_TOKEN` is unset — safe default for tests and CI |
| `strategies/base.py` | `Strategy` ABC: defines `get_parameters()`, `init()`, `on_candle()` interface shared by all strategies |
| `strategies/breakout.py` | Breakout strategy implementation |
| `strategies/support_resistance.py` | Support/resistance strategy implementation |
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

Core tables: `klines`, `download_jobs`, `derived_metrics`, `users`, `signal_configs`, `signals`, `sim_trades`, `real_trades`, `notification_log`, `telegram_link_tokens`.

Keep the inline SQLite migration in `init_db()` and the Alembic revision in lockstep — both must produce the same final schema. Any new column/table lands in both or neither.

### Strategy plugin pattern

New strategies must extend `backend/strategies/base.py::Strategy`, implement `get_parameters()`, `init()`, and `on_candle()`, then be registered in the strategy registry used by `backtest_routes.py` and `signal_engine.py`.

### Telegram notifications

One shared bot per deployment. Each user links their own chat on first use:

1. Frontend profile page calls `POST /api/profile/telegram/link-token` → backend generates a one-time token (15 min TTL) and returns a `https://t.me/<bot>?start=<token>` deep-link.
2. User opens the link and sends `/start <token>` to the bot.
3. Telegram POSTs the update to `/api/telegram/webhook/{secret}`; the webhook consumes the token and stores `telegram_chat_id` / `telegram_username` on the user row.

Outbound alerts flow through a single entry point — `notifications.notify_event` — which live_tracker calls at four sites (entry fill, exit signal, intrabar stop, candle-close stop). The dispatcher filters by the per-config `telegram_enabled` toggle + the user's linked chat, and dedupes on `notification_log (event_type, reference_type, reference_id, channel)`. The whole subsystem is inert (no HTTP, no reply) whenever `TELEGRAM_BOT_TOKEN` is empty — this is the invariant that keeps tests and CI green without mocking the network.
### Live-mode invariants

- **Sim-trade lifecycle**: `pending_entry` → `open` → `closed`. On close, `exit_reason` ∈ `{stop_intrabar, stop_candle, exit_signal, manual, config_deleted}`.
- **Stop levels**: `stop_base` is the strategy's raw stop. `stop_trigger = stop_base × (1 − stop_cross_pct)` for longs, `× (1 + stop_cross_pct)` for shorts. Both the intrabar ticker check and the candle-close strategy state use `stop_trigger` so the two paths fire at the same price.
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

## Deployment

- Container: multi-stage Dockerfile (Node 22 Alpine builds frontend, Python 3.13-slim runs app).
- CD: push to `develop` → GitHub Actions builds and pushes multi-arch image to GHCR, updates `helm/env/dev.yaml` image tag for Argo CD to pick up.
- Kubernetes: Helm chart in `helm/`; deployed to k3s via Argo CD.
