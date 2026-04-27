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


# ---------------------------------------------------------------------------
# CSP — pure function tests for _build_csp
# ---------------------------------------------------------------------------


def test_build_csp_returns_none_when_keycloak_url_empty():
    from backend.app import _build_csp

    assert _build_csp("") is None


def test_build_csp_includes_keycloak_url_in_connect_and_frame():
    from backend.app import _build_csp

    csp = _build_csp("https://keycloak.example.com")
    assert csp is not None
    assert "connect-src 'self' https://keycloak.example.com" in csp
    assert "frame-src https://keycloak.example.com" in csp


def test_build_csp_keeps_report_uri_pointing_at_app_endpoint():
    from backend.app import _build_csp

    csp = _build_csp("https://keycloak.example.com")
    assert "report-uri /api/csp-report" in csp


def test_build_csp_blocks_third_party_default_sources():
    from backend.app import _build_csp

    csp = _build_csp("https://keycloak.example.com")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp


def test_build_csp_allows_google_fonts():
    from backend.app import _build_csp

    csp = _build_csp("https://keycloak.example.com")
    assert "https://fonts.googleapis.com" in csp
    assert "https://fonts.gstatic.com" in csp


# ---------------------------------------------------------------------------
# CSP — header attached to responses (enforcing, post-#82)
# ---------------------------------------------------------------------------


def test_csp_header_is_enforcing_not_report_only(monkeypatch):
    """Lock in the post-#82 contract: the header is `Content-Security-Policy`
    (enforcing), not `Content-Security-Policy-Report-Only`. Regression
    guard against a downgrade back to Report-Only mode."""
    import backend.app as app_module

    monkeypatch.setattr(app_module, "_CSP", "default-src 'self'; report-uri /api/csp-report")

    client = TestClient(_build_app())
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert "Content-Security-Policy" in resp.headers
    assert "Content-Security-Policy-Report-Only" not in resp.headers
    assert "report-uri /api/csp-report" in resp.headers["Content-Security-Policy"]


def test_csp_header_absent_when_keycloak_url_empty(monkeypatch):
    """Local-dev path with AUTH_ENABLED=false leaves _CSP=None, so no
    CSP header is attached (the other baseline headers still are)."""
    import backend.app as app_module

    monkeypatch.setattr(app_module, "_CSP", None)

    client = TestClient(_build_app())
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert "Content-Security-Policy" not in resp.headers
    assert "Content-Security-Policy-Report-Only" not in resp.headers
    # Baseline headers still present.
    assert resp.headers["X-Frame-Options"] == "DENY"
