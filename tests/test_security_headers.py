"""Tests for the security_headers middleware in backend.app.

Verifies the four baseline headers are attached to every response, both for
API endpoints (e.g. /api/auth/config) and for arbitrary paths handled by
FastAPI (e.g. a 404). CSP is intentionally not asserted yet — it lives in
a follow-up once we validate it end-to-end against OIDC in QA.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_headers.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod

    dbmod.DB_PATH = __import__("pathlib").Path(db_path)
    yield


def _build_app() -> FastAPI:
    """Mount only the security_headers middleware on a tiny app."""
    from backend.app import security_headers

    app = FastAPI()
    app.middleware("http")(security_headers)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return app


def test_headers_present_on_200():
    client = TestClient(_build_app())
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "max-age=31536000" in resp.headers["Strict-Transport-Security"]
    assert "includeSubDomains" in resp.headers["Strict-Transport-Security"]


def test_headers_present_on_404():
    """Even FastAPI's auto-404 should carry the headers (middleware always runs)."""
    client = TestClient(_build_app())
    resp = client.get("/nonexistent")
    assert resp.status_code == 404
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_existing_header_is_not_overwritten():
    """`setdefault` semantics: a downstream handler can override these
    if it has a real reason to (the test docs the contract)."""
    from backend.app import security_headers

    app = FastAPI()
    app.middleware("http")(security_headers)

    @app.get("/embed-allowed")
    async def embed_allowed():
        from fastapi.responses import JSONResponse

        return JSONResponse(
            content={"ok": True},
            headers={"X-Frame-Options": "SAMEORIGIN"},
        )

    client = TestClient(app)
    resp = client.get("/embed-allowed")
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    # The other defaults still apply.
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
