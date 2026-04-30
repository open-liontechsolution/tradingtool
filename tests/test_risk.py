"""Unit tests for backend.risk.should_skip_for_max_loss (#142)."""

from __future__ import annotations

import math

import pytest

from backend.risk import should_skip_for_max_loss


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
