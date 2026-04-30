"""Unit tests for backend.risk.{should_skip_for_max_loss, compute_risk_based_size}."""

from __future__ import annotations

import math

import pytest

from backend.risk import compute_risk_based_size, should_skip_for_max_loss


class TestNoLeverage:
    def test_long_under_threshold_does_not_skip(self):
        # entry=100, stop=99 → 1% distance, leverage=1, threshold=2% → no skip
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=99.0,
            side="long",
            leverage=1.0,
            invested_amount=None,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
        )
        assert skip is False
        assert loss == pytest.approx(0.01)

    def test_long_over_threshold_skips(self):
        # entry=100, stop=95 → 5% distance, threshold=2% → skip
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=95.0,
            side="long",
            leverage=1.0,
            invested_amount=None,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
        )
        assert skip is True
        assert loss == pytest.approx(0.05)

    def test_short_under_threshold_does_not_skip(self):
        # entry=100, stop=101 → 1% distance, threshold=2% → no skip
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=101.0,
            side="short",
            leverage=1.0,
            invested_amount=None,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
        )
        assert skip is False
        assert loss == pytest.approx(0.01)

    def test_short_over_threshold_skips(self):
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=105.0,
            side="short",
            leverage=1.0,
            invested_amount=None,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
        )
        assert skip is True
        assert loss == pytest.approx(0.05)

    def test_threshold_exactly_equal_does_not_skip(self):
        # equity_loss == max_loss_pct → not skipped (strict >, not >=)
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=98.0,
            side="long",
            leverage=1.0,
            invested_amount=None,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
        )
        assert skip is False
        assert loss == pytest.approx(0.02)


class TestWithLeverage:
    def test_leverage_amplifies_loss(self):
        # 1% stop distance × 10x leverage = 10% equity loss → exceeds 5% threshold
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=99.0,
            side="long",
            leverage=10.0,
            invested_amount=None,
            current_portfolio=10_000.0,
            max_loss_pct=0.05,
        )
        assert skip is True
        assert loss == pytest.approx(0.10)

    def test_leverage_below_one_clamped_to_one(self):
        # leverage=0.5 should still risk at least 1× the distance (notional cannot
        # be smaller than the underlying capital allocation in this model).
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=95.0,
            side="long",
            leverage=0.5,
            invested_amount=None,
            current_portfolio=10_000.0,
            max_loss_pct=0.10,
        )
        assert skip is False
        assert loss == pytest.approx(0.05)

    def test_leverage_threshold_exactly(self):
        # leverage=5 × 2% = 10% equity loss == 10% threshold → no skip
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=98.0,
            side="long",
            leverage=5.0,
            invested_amount=None,
            current_portfolio=10_000.0,
            max_loss_pct=0.10,
        )
        assert skip is False
        assert loss == pytest.approx(0.10)


class TestInvestedAmount:
    def test_partial_investment_reduces_loss(self):
        # Only half the equity is exposed (5000 / 10000) → 1% distance × 0.5 = 0.5%
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=99.0,
            side="long",
            leverage=1.0,
            invested_amount=5_000.0,
            current_portfolio=10_000.0,
            max_loss_pct=0.01,
        )
        assert skip is False
        assert loss == pytest.approx(0.005)

    def test_invested_above_portfolio_increases_loss(self):
        # invested=20k on 10k equity (= 2x leverage equivalent) → 1% × 2 = 2%
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=99.0,
            side="long",
            leverage=1.0,
            invested_amount=20_000.0,
            current_portfolio=10_000.0,
            max_loss_pct=0.01,
        )
        assert skip is True
        assert loss == pytest.approx(0.02)


class TestEdgeCases:
    def test_zero_portfolio_skips(self):
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=99.0,
            side="long",
            leverage=1.0,
            invested_amount=None,
            current_portfolio=0.0,
            max_loss_pct=0.02,
        )
        assert skip is True
        assert loss == math.inf

    def test_negative_portfolio_skips(self):
        skip, loss = should_skip_for_max_loss(
            entry_price=100.0,
            stop_base=99.0,
            side="long",
            leverage=1.0,
            invested_amount=None,
            current_portfolio=-100.0,
            max_loss_pct=0.02,
        )
        assert skip is True
        assert loss == math.inf

    def test_zero_entry_does_not_skip(self):
        # Sanity guard — entry_price <= 0 shouldn't blow up the calc.
        skip, loss = should_skip_for_max_loss(
            entry_price=0.0,
            stop_base=0.0,
            side="long",
            leverage=1.0,
            invested_amount=None,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
        )
        assert skip is False
        assert loss == 0.0


# ---------------------------------------------------------------------------
# compute_risk_based_size (#144)
# ---------------------------------------------------------------------------


class TestRiskBasedSizeUnclipped:
    def test_long_basic(self):
        # entry=100, stop=99 → distance=1. equity=10k, risk_pct=1% → risk=100.
        # target_qty = 100/1 = 100. target_notional = 100*100 = 10k.
        # max_notional (lev=1) = 10k. min = 10k → just at the cap, NOT clipped
        # since target == max (strict > rule).
        invested, qty, clipped = compute_risk_based_size(
            side="long",
            entry_price=100.0,
            stop_base=99.0,
            current_portfolio=10_000.0,
            max_loss_pct=0.01,
            leverage=1.0,
        )
        assert invested == pytest.approx(10_000.0)
        assert qty == pytest.approx(100.0)
        assert clipped is False

    def test_long_well_under_cap(self):
        # entry=100, stop=95 (5% distance), risk_pct=1% on 10k → risk=100.
        # target_qty = 100/5 = 20. target_notional = 20*100 = 2000.
        # max_notional (lev=1) = 10k → not clipped. invested = 2000.
        invested, qty, clipped = compute_risk_based_size(
            side="long",
            entry_price=100.0,
            stop_base=95.0,
            current_portfolio=10_000.0,
            max_loss_pct=0.01,
            leverage=1.0,
        )
        assert invested == pytest.approx(2_000.0)
        assert qty == pytest.approx(20.0)
        assert clipped is False
        # Sanity: realised loss-if-stopped == risk_amount.
        assert qty * abs(100.0 - 95.0) == pytest.approx(100.0)

    def test_short_symmetric(self):
        # Mirror of long_well_under_cap with stop above entry.
        invested, qty, clipped = compute_risk_based_size(
            side="short",
            entry_price=100.0,
            stop_base=105.0,
            current_portfolio=10_000.0,
            max_loss_pct=0.01,
            leverage=1.0,
        )
        assert invested == pytest.approx(2_000.0)
        assert qty == pytest.approx(20.0)
        assert clipped is False

    def test_leverage_expands_max_notional(self):
        # entry=100, stop=99 (1% distance), risk_pct=2% on 10k → risk=200.
        # target_qty = 200/1 = 200. target_notional = 200*100 = 20k.
        # max_notional (lev=5) = 50k → not clipped. invested = 20k.
        invested, qty, clipped = compute_risk_based_size(
            side="long",
            entry_price=100.0,
            stop_base=99.0,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
            leverage=5.0,
        )
        assert invested == pytest.approx(20_000.0)
        assert qty == pytest.approx(200.0)
        assert clipped is False


class TestRiskBasedSizeClipped:
    def test_tight_stop_at_lev_one_clips(self):
        # entry=100, stop=99.9 (0.1% distance), risk_pct=2% on 10k → risk=200.
        # target_qty = 200/0.1 = 2000. target_notional = 2000*100 = 200k.
        # max_notional (lev=1) = 10k → CLIPPED. invested = 10k, qty = 100.
        invested, qty, clipped = compute_risk_based_size(
            side="long",
            entry_price=100.0,
            stop_base=99.9,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
            leverage=1.0,
        )
        assert invested == pytest.approx(10_000.0)
        assert qty == pytest.approx(100.0)
        assert clipped is True
        # When clipped, realised loss-if-stopped < risk_amount (here ~10).
        assert qty * abs(100.0 - 99.9) == pytest.approx(10.0)

    def test_short_clipped(self):
        invested, qty, clipped = compute_risk_based_size(
            side="short",
            entry_price=100.0,
            stop_base=100.1,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
            leverage=1.0,
        )
        assert invested == pytest.approx(10_000.0)
        assert qty == pytest.approx(100.0)
        assert clipped is True

    def test_leverage_below_one_clamped_to_one(self):
        # leverage=0.5 should be treated as 1.0 (mirrors should_skip_for_max_loss).
        # Without the clamp the cap would be 5k, but we want it 10k.
        invested, qty, clipped = compute_risk_based_size(
            side="long",
            entry_price=100.0,
            stop_base=99.0,
            current_portfolio=10_000.0,
            max_loss_pct=0.02,
            leverage=0.5,
        )
        # target = 200/1 * 100 = 20k, max = 10k (after clamp) → clipped
        assert invested == pytest.approx(10_000.0)
        assert qty == pytest.approx(100.0)
        assert clipped is True


class TestRiskBasedSizeDegenerate:
    def test_zero_entry_raises(self):
        with pytest.raises(ValueError):
            compute_risk_based_size(
                side="long",
                entry_price=0.0,
                stop_base=99.0,
                current_portfolio=10_000.0,
                max_loss_pct=0.01,
                leverage=1.0,
            )

    def test_zero_portfolio_raises(self):
        with pytest.raises(ValueError):
            compute_risk_based_size(
                side="long",
                entry_price=100.0,
                stop_base=99.0,
                current_portfolio=0.0,
                max_loss_pct=0.01,
                leverage=1.0,
            )

    def test_zero_distance_raises(self):
        # entry == stop is logically impossible for a real signal but should
        # raise rather than silently divide by zero.
        with pytest.raises(ValueError):
            compute_risk_based_size(
                side="long",
                entry_price=100.0,
                stop_base=100.0,
                current_portfolio=10_000.0,
                max_loss_pct=0.01,
                leverage=1.0,
            )

    def test_zero_max_loss_pct_raises(self):
        # max_loss_pct=0 would silently produce invested=0 / quantity=0 (no-op
        # trade that still occupies the active slot). Must raise — see #147.
        with pytest.raises(ValueError):
            compute_risk_based_size(
                side="long",
                entry_price=100.0,
                stop_base=99.0,
                current_portfolio=10_000.0,
                max_loss_pct=0.0,
                leverage=1.0,
            )

    def test_negative_max_loss_pct_raises(self):
        with pytest.raises(ValueError):
            compute_risk_based_size(
                side="long",
                entry_price=100.0,
                stop_base=99.0,
                current_portfolio=10_000.0,
                max_loss_pct=-0.01,
                leverage=1.0,
            )
