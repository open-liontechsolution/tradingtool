"""Unit tests for the YAML recommendations catalogue loader.

The loader is a thin ``yaml.safe_load`` + lru_cache wrapper, so the tests focus
on behaviours the higher layers (route, refresh script) rely on:

- catalogue is keyed by uppercase pair; lookups are case-insensitive
- missing pair returns None (not raise) so the route can render a clean empty state
- source filter is strictly ``curated``; future sources reject loudly
- malformed YAML raises ``RecommendationCatalogError`` at load time
- ``reload_catalog`` invalidates the lru_cache so refresh-script edits land
"""

from __future__ import annotations

import pytest

from backend import recommendations as rec_module

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
  ETHUSDT:
    primary:
      strategy: mean_reversion_bb
      timeframe: '4h'
      source: curated
      params: {}
"""


@pytest.fixture(autouse=True)
def _isolate_catalog(tmp_path, monkeypatch):
    """Point the loader at a temp YAML and clear caches before/after each test.

    The lru_cache is module-level so leakage across tests is real; clearing on
    both sides guarantees independence.
    """
    catalog = tmp_path / "recommendations.yaml"
    monkeypatch.setattr(rec_module, "CATALOG_PATH", catalog)
    rec_module.reload_catalog()
    yield catalog
    rec_module.reload_catalog()


def test_get_recommendation_returns_primary_dict(_isolate_catalog):
    _isolate_catalog.write_text(_GOOD_YAML, encoding="utf-8")

    rec = rec_module.get_recommendation("BTCUSDT")

    assert rec is not None
    assert rec["strategy"] == "mean_reversion_bb"
    assert rec["timeframe"] == "4h"
    assert rec["params"]["bb_period"] == 30
    assert rec["metrics_cached"]["1y"]["n_trades"] == 23


def test_get_recommendation_is_case_insensitive(_isolate_catalog):
    _isolate_catalog.write_text(_GOOD_YAML, encoding="utf-8")

    rec_upper = rec_module.get_recommendation("BTCUSDT")
    rec_lower = rec_module.get_recommendation("btcusdt")
    rec_mixed = rec_module.get_recommendation("BtcUsdt")

    assert rec_upper == rec_lower == rec_mixed


def test_get_recommendation_returns_none_for_unknown_pair(_isolate_catalog):
    _isolate_catalog.write_text(_GOOD_YAML, encoding="utf-8")

    assert rec_module.get_recommendation("DOGEUSDT") is None


def test_unsupported_source_raises_value_error(_isolate_catalog):
    _isolate_catalog.write_text(_GOOD_YAML, encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported recommendation source"):
        rec_module.get_recommendation("BTCUSDT", source="ai")


def test_list_pairs_returns_sorted_uppercase(_isolate_catalog):
    _isolate_catalog.write_text(_GOOD_YAML, encoding="utf-8")

    assert rec_module.list_pairs() == ["BTCUSDT", "ETHUSDT"]


def test_missing_catalogue_raises(_isolate_catalog):
    # _isolate_catalog points to a path that doesn't exist yet
    with pytest.raises(rec_module.RecommendationCatalogError, match="not found"):
        rec_module.get_recommendation("BTCUSDT")


def test_malformed_yaml_raises(_isolate_catalog):
    _isolate_catalog.write_text("recommendations: [not a mapping", encoding="utf-8")

    with pytest.raises(rec_module.RecommendationCatalogError, match="Invalid YAML"):
        rec_module.get_recommendation("BTCUSDT")


def test_top_level_must_be_mapping(_isolate_catalog):
    _isolate_catalog.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(rec_module.RecommendationCatalogError, match="must be a mapping"):
        rec_module.get_recommendation("BTCUSDT")


def test_reload_catalog_picks_up_yaml_edits(_isolate_catalog):
    _isolate_catalog.write_text(_GOOD_YAML, encoding="utf-8")
    assert rec_module.get_recommendation("DOGEUSDT") is None

    _isolate_catalog.write_text(
        _GOOD_YAML
        + "  DOGEUSDT:\n    primary:\n      strategy: breakout\n      timeframe: '1d'\n      source: curated\n      params: {}\n",
        encoding="utf-8",
    )
    rec_module.reload_catalog()

    rec = rec_module.get_recommendation("DOGEUSDT")
    assert rec is not None
    assert rec["strategy"] == "breakout"


def test_entry_with_other_source_filtered_out(_isolate_catalog):
    _isolate_catalog.write_text(
        """\
recommendations:
  BTCUSDT:
    primary:
      strategy: mean_reversion_bb
      timeframe: '4h'
      source: ai
      params: {}
""",
        encoding="utf-8",
    )

    # default source=curated → pair is filtered out even though it exists
    assert rec_module.get_recommendation("BTCUSDT") is None
    assert rec_module.list_pairs() == []
