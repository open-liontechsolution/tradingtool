"""Centralised configuration: reads environment variables once."""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_raw_db_url: str | None = os.environ.get("DATABASE_URL")
DATABASE_URL: str | None = _raw_db_url.strip().strip("'\"") if _raw_db_url else None

IS_POSTGRES: bool = bool(DATABASE_URL and DATABASE_URL.startswith("postgresql"))

DB_PATH: Path = Path(os.environ.get("DB_PATH", "data/trading_tools.db"))

# ---------------------------------------------------------------------------
# Auth / Keycloak
# ---------------------------------------------------------------------------

AUTH_ENABLED: bool = os.environ.get("AUTH_ENABLED", "false").lower() in ("true", "1", "yes")

KEYCLOAK_URL: str = os.environ.get("KEYCLOAK_URL", "")
KEYCLOAK_REALM: str = os.environ.get("KEYCLOAK_REALM", "tradingtool-dev")
KEYCLOAK_AUDIENCE: str = os.environ.get("KEYCLOAK_AUDIENCE", "tradingtool-api")
KEYCLOAK_FRONTEND_CLIENT_ID: str = os.environ.get("KEYCLOAK_FRONTEND_CLIENT_ID", "tradingtool-web")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

PORT: int = int(os.environ.get("PORT", "8000"))
HOST: str = os.environ.get("HOST", "0.0.0.0")
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "info")


def _resolve_cors_origins(raw: str, auth_enabled: bool) -> list[str]:
    """Parse CORS_ORIGINS and refuse the wildcard when auth is real.

    The combination ``Access-Control-Allow-Origin: *`` + credentials is also
    rejected by browsers, but more importantly we don't want to ship that
    config to a deployment where AUTH_ENABLED=true. Wildcard stays valid for
    local dev (AUTH_ENABLED=false) so the default zero-config flow still
    works.
    """
    items = [o.strip() for o in raw.split(",") if o.strip()]
    if items == ["*"] and auth_enabled:
        raise RuntimeError(
            "CORS_ORIGINS=* is unsafe when AUTH_ENABLED=true. "
            "Set CORS_ORIGINS to a comma-separated list of trusted origins "
            "(e.g. https://tradingtool-dev.liontechsolution.com)."
        )
    return items


CORS_ORIGINS: list[str] = _resolve_cors_origins(os.environ.get("CORS_ORIGINS", "*"), AUTH_ENABLED)

PUBLIC_BASE_URL: str = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------
# When TELEGRAM_BOT_TOKEN is empty the whole Telegram subsystem is a no-op
# (safe default for tests, CI and deployments without a bot configured).

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BOT_USERNAME: str = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
TELEGRAM_WEBHOOK_SECRET: str = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
TELEGRAM_WEBHOOK_URL: str = os.environ.get("TELEGRAM_WEBHOOK_URL", "").strip().rstrip("/")

TELEGRAM_ENABLED: bool = bool(TELEGRAM_BOT_TOKEN)
