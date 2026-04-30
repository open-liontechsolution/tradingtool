"""Shared risk helpers used by both the backtest engine and the live signal engine.

Keeping this logic in one module guarantees parity between backtest and live —
the same pattern as ``live_tracker.compute_liquidation_price`` (#58 Gap 1).
"""

from __future__ import annotations

import math
from typing import Literal


def should_skip_for_max_loss(
    entry_price: float,
    stop_base: float,
    side: Literal["long", "short"],
    leverage: float,
    invested_amount: float | None,
    current_portfolio: float,
    max_loss_pct: float,
) -> tuple[bool, float]:
    """Return ``(skip, equity_loss_pct)`` for a candidate entry.

    The estimated loss is computed assuming the trade enters at
    ``entry_price`` and gets stopped at ``stop_base`` (the strategy's raw
    stop, post-#49 — there is no separate trigger buffer). The notional
    exposed is ``(invested_amount or current_portfolio) * leverage``, so the
    equity-loss percentage is

        notional_share = (invested_amount or current_portfolio) / current_portfolio
        distance_pct   = abs(entry_price - stop_base) / entry_price
        equity_loss_pct = notional_share * max(leverage, 1.0) * distance_pct

    With the default config (``invested_amount = current_portfolio``,
    ``leverage = 1``) this collapses to ``equity_loss_pct = distance_pct``.
    Under leverage the impact on equity scales linearly with leverage.

    Defensive cases:
    - ``current_portfolio <= 0`` → ``(True, inf)``: no equity left to risk.
    - ``entry_price <= 0`` → ``(False, 0.0)``: sanity, shouldn't happen.

    The ``side`` argument exists for symmetry / future use (e.g. asymmetric
    risk rules); the formula itself is side-symmetric because we use the
    absolute distance.
    """
    if current_portfolio <= 0:
        return True, math.inf
    if entry_price <= 0:
        return False, 0.0

    distance_pct = abs(entry_price - stop_base) / entry_price
    notional_share = (
        (invested_amount / current_portfolio) if (invested_amount is not None and invested_amount > 0) else 1.0
    )
    equity_loss_pct = notional_share * max(leverage, 1.0) * distance_pct
    return equity_loss_pct > max_loss_pct, equity_loss_pct
