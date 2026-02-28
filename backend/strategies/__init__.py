"""Strategy registry: discovers and registers available strategies."""

from __future__ import annotations

from backend.strategies.base import Strategy
from backend.strategies.breakout import BreakoutStrategy
from backend.strategies.support_resistance import SupportResistanceStrategy

_REGISTRY: dict[str, type[Strategy]] = {}


def _register(cls: type[Strategy]) -> None:
    _REGISTRY[cls.name] = cls


_register(BreakoutStrategy)
_register(SupportResistanceStrategy)


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
