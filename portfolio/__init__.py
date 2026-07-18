"""Local stock research and portfolio package."""

from __future__ import annotations

from typing import Any

__all__ = ["StockAgentController"]


def __getattr__(name: str) -> Any:
    if name == "StockAgentController":
        from .controller import StockAgentController

        return StockAgentController
    raise AttributeError(name)
