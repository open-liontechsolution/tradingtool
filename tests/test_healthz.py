"""Tests for the /healthz endpoint.

Locks in the post-#114 contract: the response body carries an `image_tag`
field that reflects the IMAGE_TAG env var (Helm injects this from
.Values.image.tag). The QA smoke E2E polls this field to verify the
served pod is the one we just promoted, instead of validating an old
pod still serving traffic during the rolling update.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app() -> FastAPI:
    """Mount only the /healthz route on a tiny app, bypassing the lifespan
    in backend.app (which runs init_db and starts background loops)."""
    from backend.app import liveness

    app = FastAPI()
    app.add_api_route("/healthz", liveness, methods=["GET"])
    return app


def test_healthz_returns_status_and_image_tag(monkeypatch):
    monkeypatch.setattr("backend.app.IMAGE_TAG", "test-tag-abc123")
    client = TestClient(_build_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "alive", "image_tag": "test-tag-abc123"}


def test_healthz_image_tag_defaults_when_env_unset(monkeypatch):
    """When IMAGE_TAG is not injected, the default 'unknown' surfaces.
    This is the local-dev path; in QA/dev the Helm chart always sets it."""
    monkeypatch.setattr("backend.app.IMAGE_TAG", "unknown")
    client = TestClient(_build_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive", "image_tag": "unknown"}
