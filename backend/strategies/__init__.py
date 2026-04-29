"""Strategy registry: discovers and registers available strategies."""

from __future__ import annotations

from backend.strategies.base import Strategy
from backend.strategies.breakout import BreakoutStrategy
from backend.strategies.breakout_trailing import BreakoutTrailingStrategy
from backend.strategies.donchian_adx_atr import DonchianAdxAtrStrategy
from backend.strategies.donchian_long_term import DonchianLongTermStrategy
from backend.strategies.support_resistance import SupportResistanceStrategy
from backend.strategies.support_resistance_trailing import SupportResistanceTrailingStrategy
from backend.strategies.zigzag_momentum import ZigzagMomentumStrategy

_REGISTRY: dict[str, type[Strategy]] = {}


def _register(cls: type[Strategy]) -> None:
    _REGISTRY[cls.name] = cls


_register(BreakoutStrategy)
_register(BreakoutTrailingStrategy)
_register(SupportResistanceStrategy)
_register(SupportResistanceTrailingStrategy)
_register(DonchianAdxAtrStrategy)
_register(DonchianLongTermStrategy)
_register(ZigzagMomentumStrategy)


def get_strategy(name: str) -> Strategy:
    """Instantiate a strategy by name."""
    cls = _REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown strategy: {name!r}. Available: {list(_REGISTRY.keys())}")
    return cls()


def list_strategies() -> list[dict]:
    """Return metadata for all registered strategies."""
    result = []
    for name, cls in _REGISTRY.items():
        instance = cls()
        result.append(
            {
                "name": name,
                "description": instance.description,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "default": p.default,
                        "min": p.min,
                        "max": p.max,
                        "description": p.description,
                    }
                    for p in instance.get_parameters()
                ],
            }
        )
    return result
