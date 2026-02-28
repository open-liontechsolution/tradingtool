"""Centralised configuration: reads environment variables once."""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

IS_POSTGRES: bool = bool(DATABASE_URL and DATABASE_URL.startswith("postgresql"))

DB_PATH: Path = Path(os.environ.get("DB_PATH", "data/trading_tools.db"))

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

PORT: int = int(os.environ.get("PORT", "8000"))
HOST: str = os.environ.get("HOST", "0.0.0.0")
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "info")
