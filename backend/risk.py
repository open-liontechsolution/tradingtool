"""Shared risk helpers used by both the backtest engine and the live signal engine.

Keeping this logic in one module guarantees parity between backtest and live —
the same pattern as ``live_tracker.compute_liquidation_price`` (#58 Gap 1).
"""

from __future__ import annotations

import math
from typing import Literal


def compute_risk_based_size(
    *,
    side: Literal["long", "short"],
    entry_price: float,
    stop_base: float,
    current_portfolio: float,
    max_loss_pct: float,
    leverage: float,
) -> tuple[float, float, bool]:
    """Return ``(invested_amount, quantity, sizing_clipped)`` for a risk-based entry (#144).

    Sizes the position so that, if the trade gets stopped at ``stop_base`` from
    a fill at ``entry_price``, the realised loss equals
    ``current_portfolio * max_loss_pct``. The leverage budget caps the result:
    when the target notional exceeds ``current_portfolio * leverage`` it is
    clipped (and ``sizing_clipped=True``), in which case the realised
    loss-if-stopped will *exceed* ``max_loss_pct`` — that's exactly when #142's
    skip filter remains useful as a safety net.

    Formula::

        distance        = abs(entry_price - stop_base)
        risk_amount     = current_portfolio * max_loss_pct
        target_qty      = risk_amount / distance
        target_notional = target_qty * entry_price
        max_notional    = current_portfolio * max(leverage, 1.0)
        notional        = min(target_notional, max_notional)
        quantity        = notional / entry_price
        sizing_clipped  = (target_notional > max_notional)

    Side-symmetric (``abs(...)`` on the distance). Caller must guarantee
    ``entry_price > 0``, ``current_portfolio > 0`` and ``distance > 0`` —
    these are all satisfied by any real entry signal (entry/stop are emitted
    by a strategy on a closed candle with a non-zero price). Raises
    ``ValueError`` on degenerate input so a misuse fails loud rather than
    silently producing a zero-sized trade.
    """
    if entry_price <= 0:
        raise ValueError(f"entry_price must be > 0, got {entry_price}")
    if current_portfolio <= 0:
        raise ValueError(f"current_portfolio must be > 0, got {current_portfolio}")
    distance = abs(entry_price - stop_base)
    if distance <= 0:
        raise ValueError(f"distance entry_price-stop_base must be > 0, got entry={entry_price} stop={stop_base}")

    lev = max(leverage, 1.0)
    risk_amount = current_portfolio * max_loss_pct
    target_qty = risk_amount / distance
    target_notional = target_qty * entry_price
    max_notional = current_portfolio * lev
    notional = min(target_notional, max_notional)
    quantity = notional / entry_price
    sizing_clipped = target_notional > max_notional
    # ``side`` is accepted for symmetry / future use (e.g. asymmetric risk
    # rules); the math is side-symmetric because ``distance`` uses ``abs(...)``.
    _ = side
    return notional, quantity, sizing_clipped


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
