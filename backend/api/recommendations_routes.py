"""REST API for the curated recommendations catalogue (#140).

Endpoints
---------
- ``GET /api/recommendations`` → sorted list of pairs with a curated rec.
- ``GET /api/recommendations/{pair}`` → primary rec for the pair, or null.

Everything is read-only: requests just consult the in-memory cached YAML loaded
by ``backend.recommendations``. Writes happen via the offline refresh script.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.recommendations import (
    RecommendationCatalogError,
    get_recommendation,
    list_pairs,
)

router = APIRouter(tags=["recommendations"])

_NO_RECOMMENDATION_MESSAGE = "No hay recomendación validada para este par. Usa Backtest manual para investigar."


class RecommendationResponse(BaseModel):
    pair: str
    source: str
    recommendation: dict[str, Any] | None
    message: str | None = None


@router.get("/recommendations", response_model=list[str])
async def list_recommendations(source: str = Query("curated")) -> list[str]:
    """Return the sorted list of pairs with a primary recommendation for ``source``."""
    try:
        return list_pairs(source=source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RecommendationCatalogError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/recommendations/{pair}", response_model=RecommendationResponse)
async def get_recommendation_for_pair(
    pair: str,
    source: str = Query("curated"),
) -> RecommendationResponse:
    """Return the primary recommendation for ``pair`` (case-insensitive).

    Returns ``recommendation: null`` with a human-readable ``message`` when the
    pair has no curated entry — the frontend renders an empty-state CTA in that
    case rather than a 404, mirroring the chosen UX in #140 (option 1).
    """
    try:
        rec = get_recommendation(pair, source=source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RecommendationCatalogError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    pair_upper = pair.upper()
    if rec is None:
        return RecommendationResponse(
            pair=pair_upper,
            source=source,
            recommendation=None,
            message=_NO_RECOMMENDATION_MESSAGE,
        )
    return RecommendationResponse(
        pair=pair_upper,
        source=source,
        recommendation=rec,
        message=None,
    )
