"""Small domain models used across the application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Purchase:
    ticker: str
    quantity: float
    price: float
    purchased_at: date
    note: str = ""


@dataclass(frozen=True)
class Holding:
    ticker: str
    quantity: float
    total_cost: float
    average_cost: float


@dataclass(frozen=True)
class HoldingSnapshot:
    ticker: str
    quantity: float
    average_cost: float
    total_cost: float
    current_price: float | None
    market_value: float | None
    gain_loss: float | None
