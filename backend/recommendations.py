"""Curated recommendations catalogue: read-only loader for the YAML at backend/data/.

The catalogue is a hand-tuned mapping ``pair → recommended (strategy, timeframe, params)``
plus pre-cached multi-period backtest metrics. The recommendations API and the
refresh-cache script are the only consumers.

Reload semantics
----------------
``_load_catalog`` is ``lru_cache(maxsize=1)`` so request handlers don't hit disk
on every call. After the refresh script rewrites the YAML, or in tests that
patch ``CATALOG_PATH``, call ``reload_catalog()`` to invalidate the cache.

Source filter
-------------
``source="curated"`` is the only supported value today. Reserved future values
(``ai``, ``community``) raise ``ValueError`` so a stale frontend with an
unrecognised filter fails loudly instead of silently returning curated data.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CATALOG_PATH = Path(__file__).parent / "data" / "recommendations.yaml"

SUPPORTED_SOURCES: frozenset[str] = frozenset({"curated"})


class RecommendationCatalogError(Exception):
    """Raised when the YAML catalogue is missing or malformed."""


@lru_cache(maxsize=1)
def _load_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.exists():
        raise RecommendationCatalogError(f"Recommendations catalogue not found at {CATALOG_PATH}")
    try:
        raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RecommendationCatalogError(f"Invalid YAML in {CATALOG_PATH}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RecommendationCatalogError(f"{CATALOG_PATH} must be a mapping at top level")
    recs = raw.get("recommendations") or {}
    if not isinstance(recs, dict):
        raise RecommendationCatalogError(f"{CATALOG_PATH} 'recommendations' must be a mapping")
    return {str(pair).upper(): entry for pair, entry in recs.items()}


def reload_catalog() -> None:
    """Invalidate the in-memory cache. Call after editing the YAML or in tests."""
    _load_catalog.cache_clear()


def _validate_source(source: str) -> None:
    if source not in SUPPORTED_SOURCES:
        raise ValueError(f"Unsupported recommendation source: {source!r}. Supported: {sorted(SUPPORTED_SOURCES)}")


def list_pairs(source: str = "curated") -> list[str]:
    """Sorted list of pairs that have a primary recommendation under ``source``."""
    _validate_source(source)
    catalog = _load_catalog()
    out: list[str] = []
    for pair, entry in catalog.items():
        primary = (entry or {}).get("primary")
        if not isinstance(primary, dict):
            continue
        if primary.get("source", "curated") != source:
            continue
        out.append(pair)
    return sorted(out)


def get_recommendation(pair: str, source: str = "curated") -> dict[str, Any] | None:
    """Return the primary recommendation for ``pair`` or ``None`` if absent.

    Pair lookup is case-insensitive (Binance-style: ``BTCUSDT``). Returns ``None``
    when the pair has no entry in the catalogue, or when the entry's source does
    not match the requested filter.
    """
    _validate_source(source)
    catalog = _load_catalog()
    entry = catalog.get(pair.upper())
    if not isinstance(entry, dict):
        return None
    primary = entry.get("primary")
    if not isinstance(primary, dict):
        return None
    if primary.get("source", "curated") != source:
        return None
    return primary
