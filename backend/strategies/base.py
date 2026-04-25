"""Abstract base class for trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd


@dataclass
class ParameterDef:
    name: str
    type: Literal["int", "float", "bool", "str"]
    default: Any
    min: Any = None
    max: Any = None
    description: str = ""


@dataclass
class PositionState:
    side: Literal["long", "short", "flat"] = "flat"
    entry_price: float = 0.0
    entry_time: int = 0  # open_time of entry candle
    stop_price: float = 0.0
    quantity: float = 0.0
    # Set by ``backtest_engine`` at entry fill when leverage > 1; mirrors the
    # ``sim_trades.liquidation_price`` column used by live. None means "no
    # liquidation risk modelled" (unleveraged) and the engine skips the
    # liquidation check.
    liquidation_price: float | None = None


@dataclass
class Signal:
    action: Literal["entry_long", "entry_short", "exit_long", "exit_short", "stop_long", "stop_short", "move_stop"]
    price: float = 0.0  # suggested execution price (0 = use next open)
    stop_price: float = 0.0  # for entry_*: initial stop; for move_stop: new raw stop level


class Strategy(ABC):
    name: str = "base"
    description: str = ""

    @abstractmethod
    def get_parameters(self) -> list[ParameterDef]:
        """Return list of parameter definitions."""
        ...

    @abstractmethod
    def init(self, params: dict, candles: pd.DataFrame) -> None:
        """Pre-compute indicators/signals on candle data."""
        ...

    @abstractmethod
    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        """Return entry/exit/stop signals for candle at index t."""
        ...
