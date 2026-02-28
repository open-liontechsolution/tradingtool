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


@dataclass
class Signal:
    action: Literal["entry_long", "entry_short", "exit_long", "exit_short", "stop_long", "stop_short"]
    price: float = 0.0  # suggested execution price (0 = use next open)
    stop_price: float = 0.0


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
