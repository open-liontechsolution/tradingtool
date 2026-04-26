"""FastAPI application factory. Mounts API routes and serves frontend static files."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.responses import Response

from backend.api.backtest_routes import router as backtest_router
from backend.api.data_routes import router as data_router
from backend.api.profile_routes import router as profile_router
from backend.api.signal_routes import router as signal_router
from backend.api.telegram_routes import router as telegram_router
from backend.auth import get_current_user
from backend.config import (
    AUTH_ENABLED,
    CORS_ORIGINS,
    IS_POSTGRES,
    KEYCLOAK_AUDIENCE,
    KEYCLOAK_FRONTEND_CLIENT_ID,
    KEYCLOAK_REALM,
    KEYCLOAK_URL,
    TELEGRAM_ENABLED,
    TELEGRAM_WEBHOOK_URL,
)
from backend.database import get_db, init_db
from backend.live_tracker import run_live_tracker
from backend.rate_limit import limiter
from backend.signal_engine import run_signal_scanner
from backend.telegram_client import set_webhook as telegram_set_webhook

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Database backend: %s", "PostgreSQL" if IS_POSTGRES else "SQLite (ephemeral)")
    await init_db()

    if TELEGRAM_ENABLED and TELEGRAM_WEBHOOK_URL:
        logger.info("Registering Telegram webhook at %s", TELEGRAM_WEBHOOK_URL)
        ok = await telegram_set_webhook(TELEGRAM_WEBHOOK_URL)
        if not ok:
            logger.warning("Telegram setWebhook call failed — check token/URL")

    scanner_task = asyncio.create_task(run_signal_scanner())
    tracker_task = asyncio.create_task(run_live_tracker())
    yield
    scanner_task.cancel()
    tracker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await scanner_task
    with contextlib.suppress(asyncio.CancelledError):
        await tracker_task


app = FastAPI(
    title="Trading Tools Laboratory",
    version="1.0.0",
    lifespan=lifespan,
)

# Rate limiting — `application_limits` (set in backend.rate_limit) caps every
# request per-client at 60/min by default. The middleware short-circuits with
# a 429 when the bucket is exhausted; the handler renders a clean JSON body.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Attach baseline security headers to every response.

    CSP is intentionally NOT included here yet — it needs end-to-end
    validation in QA against the OIDC/silent-renew iframe and the
    Google Fonts loaded by index.css before we enforce. Tracked
    separately. The four headers below are safe-by-construction
    (no allowlist tuning needed) and add immediate value.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # HSTS only takes effect over HTTPS, browsers ignore it on http://.
    response.headers.setdefault(
        "Strict-Transport-Security",
        "max-age=31536000; includeSubDomains",
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    logger.error("422 on %s %s — body: %s — errors: %s", request.method, request.url.path, body.decode(), exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors(), "body": body.decode()})


app.include_router(data_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(backtest_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(signal_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(profile_router, prefix="/api", dependencies=[Depends(get_current_user)])
# Telegram webhook is authenticated via path secret + header, NOT via Keycloak.
app.include_router(telegram_router, prefix="/api")


@app.get("/api/auth/config", tags=["auth"])
async def auth_config() -> JSONResponse:
    """Public endpoint returning frontend auth configuration (no auth required)."""
    return JSONResponse(
        content={
            "auth_enabled": AUTH_ENABLED,
            "keycloak_url": KEYCLOAK_URL,
            "keycloak_realm": KEYCLOAK_REALM,
            "keycloak_client_id": KEYCLOAK_FRONTEND_CLIENT_ID,
            "keycloak_audience": KEYCLOAK_AUDIENCE,
        }
    )


@app.get("/healthz", tags=["health"])
async def liveness() -> JSONResponse:
    return JSONResponse(content={"status": "alive"})


@app.get("/readyz", tags=["health"])
async def readiness() -> Response:
    try:
        async with get_db() as db:
            await db.execute("SELECT 1")
        return JSONResponse(content={"status": "ready"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readiness check failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "unavailable", "detail": str(exc)})


# Serve frontend static files if the dist directory exists
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
