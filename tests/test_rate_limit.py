"""Tests for backend.rate_limit + the SlowAPI integration in backend.app.

The production limiter pulls its config from env at import time, so each
test builds a fresh ``Limiter`` with a tight per-test limit, mounts the
SlowAPI middleware on a minimal app, and exercises 200 → 200 → 429.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from backend.rate_limit import client_key


def _build_app(limit: str, *, enabled: bool = True) -> FastAPI:
    """Build a minimal FastAPI with a fresh Limiter applying ``limit``."""
    limiter = Limiter(
        key_func=client_key,
        application_limits=[limit],
        enabled=enabled,
    )
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return app


def test_under_limit_returns_200():
    client = TestClient(_build_app("3/minute"))
    for _ in range(3):
        resp = client.get("/ping")
        assert resp.status_code == 200


def test_over_limit_returns_429():
    client = TestClient(_build_app("2/minute"))
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    resp = client.get("/ping")
    assert resp.status_code == 429
    body = resp.text.lower()
    # SlowAPI's default handler returns a body that mentions the limit.
    assert "rate limit" in body or "ratelimit" in body or "2 per" in body


def test_disabled_limiter_lets_everything_through():
    client = TestClient(_build_app("1/minute", enabled=False))
    for _ in range(5):
        assert client.get("/ping").status_code == 200


# ---------------------------------------------------------------------------
# client_key — bucket selection prefers the actual client, not the proxy peer
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal stand-in for fastapi.Request shaped just enough for client_key."""

    def __init__(self, headers: dict[str, str], client_host: str | None = "127.0.0.1"):
        self.headers = headers

        class _C:
            host = client_host

        self.client = _C() if client_host is not None else None


def test_client_key_prefers_cf_connecting_ip():
    req = _FakeRequest(
        headers={"cf-connecting-ip": "203.0.113.1", "x-forwarded-for": "10.0.0.1"},
    )
    assert client_key(req) == "203.0.113.1"


def test_client_key_falls_back_to_xff_first_hop():
    req = _FakeRequest(
        headers={"x-forwarded-for": "203.0.113.2, 10.0.0.1, 192.168.1.1"},
    )
    assert client_key(req) == "203.0.113.2"


def test_client_key_falls_back_to_request_client_host():
    req = _FakeRequest(headers={}, client_host="198.51.100.1")
    assert client_key(req) == "198.51.100.1"


def test_client_key_returns_unknown_when_no_source():
    req = _FakeRequest(headers={}, client_host=None)
    assert client_key(req) == "unknown"


def test_client_key_strips_whitespace_in_xff():
    req = _FakeRequest(headers={"x-forwarded-for": "   203.0.113.3   "})
    assert client_key(req) == "203.0.113.3"
