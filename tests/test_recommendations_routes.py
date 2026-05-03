"""HTTP-layer tests for /api/recommendations.

The loader itself is exercised in test_recommendations_loader.py — these tests
focus on the route contract: status codes, response shape, the ``source`` query
param, and the empty-state behaviour for pairs without a curated entry.

We patch ``CATALOG_PATH`` to a temp file so the tests are independent of the
shipped seed catalogue.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import recommendations as rec_module
from backend.auth import AuthUser, get_current_user

_GOOD_YAML = """\
recommendations:
  BTCUSDT:
    primary:
      strategy: mean_reversion_bb
      timeframe: '4h'
      source: curated
      validated_at: '2026-04-15'
      params:
        bb_period: 30
        bb_std: 3.0
      metrics_cached:
        '1y': { profit: 0.18, dd: -0.09, composite: 2.0, n_trades: 23 }
      metrics_computed_at: '2026-04-15T14:23:00Z'
"""


@pytest.fixture(autouse=True)
def _use_temp_db_and_catalog(tmp_path, monkeypatch):
    """Per-test isolation: temp DB path, temp YAML catalogue, rate-limit off.

    The DB env shim mirrors the project test convention even though /api/recommendations
    doesn't touch the DB — keeps the harness uniform and future-proofs against
    auth or middleware acquiring a connection.
    """
    db_path = str(tmp_path / "test_recommendations_routes.db")
    os.environ["DB_PATH"] = db_path

    import backend.database as dbmod
    from backend.rate_limit import limiter

    dbmod.DB_PATH = Path(db_path)
    prev_enabled = limiter.enabled
    limiter.enabled = False

    catalog = tmp_path / "recommendations.yaml"
    monkeypatch.setattr(rec_module, "CATALOG_PATH", catalog)
    rec_module.reload_catalog()

    try:
        yield catalog
    finally:
        limiter.enabled = prev_enabled
        rec_module.reload_catalog()


def _build_app() -> FastAPI:
    from backend.api import recommendations_routes

    app = FastAPI()
    app.include_router(recommendations_routes.router, prefix="/api")

    async def _override():
        return AuthUser(id=1, keycloak_sub="sub", email="t@x.com", username="t", roles=[])

    app.dependency_overrides[get_current_user] = _override
    return app


def test_get_recommendation_for_known_pair(_use_temp_db_and_catalog):
    _use_temp_db_and_catalog.write_text(_GOOD_YAML, encoding="utf-8")
    rec_module.reload_catalog()

    client = TestClient(_build_app())
    resp = client.get("/api/recommendations/BTCUSDT")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pair"] == "BTCUSDT"
    assert body["source"] == "curated"
    assert body["message"] is None
    assert body["recommendation"]["strategy"] == "mean_reversion_bb"
    assert body["recommendation"]["timeframe"] == "4h"
    assert body["recommendation"]["params"]["bb_period"] == 30


def test_get_recommendation_lowercase_pair(_use_temp_db_and_catalog):
    _use_temp_db_and_catalog.write_text(_GOOD_YAML, encoding="utf-8")
    rec_module.reload_catalog()

    client = TestClient(_build_app())
    resp = client.get("/api/recommendations/btcusdt")

    assert resp.status_code == 200
    body = resp.json()
    # response normalises pair to uppercase regardless of the request casing
    assert body["pair"] == "BTCUSDT"
    assert body["recommendation"] is not None


def test_get_recommendation_unknown_pair_returns_null_with_message(_use_temp_db_and_catalog):
    _use_temp_db_and_catalog.write_text(_GOOD_YAML, encoding="utf-8")
    rec_module.reload_catalog()

    client = TestClient(_build_app())
    resp = client.get("/api/recommendations/UNKNOWNUSDT")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pair"] == "UNKNOWNUSDT"
    assert body["recommendation"] is None
    assert body["message"] is not None
    assert "Backtest" in body["message"]  # CTA mentions Backtest manual


def test_unsupported_source_returns_400(_use_temp_db_and_catalog):
    _use_temp_db_and_catalog.write_text(_GOOD_YAML, encoding="utf-8")
    rec_module.reload_catalog()

    client = TestClient(_build_app())
    resp = client.get("/api/recommendations/BTCUSDT?source=ai")

    assert resp.status_code == 400
    assert "Unsupported recommendation source" in resp.json()["detail"]


def test_list_recommendations_returns_sorted_pairs(_use_temp_db_and_catalog):
    _use_temp_db_and_catalog.write_text(
        _GOOD_YAML
        + "  ETHUSDT:\n    primary:\n      strategy: breakout\n      timeframe: '1d'\n      source: curated\n      params: {}\n",
        encoding="utf-8",
    )
    rec_module.reload_catalog()

    client = TestClient(_build_app())
    resp = client.get("/api/recommendations")

    assert resp.status_code == 200
    assert resp.json() == ["BTCUSDT", "ETHUSDT"]


def test_list_recommendations_empty_when_catalogue_empty(_use_temp_db_and_catalog):
    _use_temp_db_and_catalog.write_text("recommendations: {}\n", encoding="utf-8")
    rec_module.reload_catalog()

    client = TestClient(_build_app())
    resp = client.get("/api/recommendations")

    assert resp.status_code == 200
    assert resp.json() == []


def test_missing_catalogue_returns_500(_use_temp_db_and_catalog):
    # Don't create the catalogue file — loader raises RecommendationCatalogError
    rec_module.reload_catalog()

    client = TestClient(_build_app())
    resp = client.get("/api/recommendations/BTCUSDT")

    assert resp.status_code == 500
    assert "not found" in resp.json()["detail"]
