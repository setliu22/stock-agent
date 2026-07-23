"""Current-price retrieval and portfolio return calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from portfolio.cloud_portfolios import CloudPurchase


@dataclass(slots=True)
class PositionMetrics:
    ticker: str
    quantity: float
    average_cost: float
    total_cost: float
    current_price: float | None
    market_value: float | None
    gain_loss: float | None
    return_percent: float | None


@dataclass(slots=True)
class PortfolioMetrics:
    positions: list[PositionMetrics]
    total_cost: float
    priced_cost: float
    market_value: float
    gain_loss: float
    return_percent: float | None
    excluded_purchase_count: int
    priced_position_count: int
    refreshed_at: str


def fetch_latest_prices(tickers: Iterable[str]) -> dict[str, float]:
    """Fetch latest prices with yfinance, skipping unavailable tickers."""
    import yfinance as yf

    prices: dict[str, float] = {}
    for ticker in sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()}):
        try:
            item = yf.Ticker(ticker)
            price = None
            try:
                fast = item.fast_info
                price = fast.get("last_price") if hasattr(fast, "get") else fast["last_price"]
            except Exception:
                price = None
            if price is None:
                history = item.history(period="5d", interval="1d", auto_adjust=False)
                if not history.empty:
                    price = history["Close"].dropna().iloc[-1]
            if price is not None and float(price) >= 0:
                prices[ticker] = float(price)
        except Exception:
            continue
    return prices


def calculate_portfolio_metrics(
    purchases: Iterable[CloudPurchase],
    current_prices: dict[str, float],
) -> PortfolioMetrics:
    grouped: dict[str, dict[str, float]] = {}
    excluded = 0
    for purchase in purchases:
        if (
            not purchase.ticker
            or purchase.quantity is None
            or purchase.purchase_price is None
            or purchase.quantity <= 0
            or purchase.purchase_price < 0
        ):
            excluded += 1
            continue
        ticker = purchase.ticker.upper()
        record = grouped.setdefault(ticker, {"quantity": 0.0, "cost": 0.0})
        record["quantity"] += purchase.quantity
        record["cost"] += purchase.quantity * purchase.purchase_price

    positions: list[PositionMetrics] = []
    total_cost = 0.0
    total_market_value = 0.0
    priced_position_count = 0
    for ticker, values in sorted(grouped.items()):
        quantity = values["quantity"]
        cost = values["cost"]
        average_cost = cost / quantity if quantity else 0.0
        price = current_prices.get(ticker)
        market_value = quantity * price if price is not None else None
        gain_loss = market_value - cost if market_value is not None else None
        return_percent = (gain_loss / cost * 100) if gain_loss is not None and cost else None
        total_cost += cost
        if market_value is not None:
            total_market_value += market_value
            priced_position_count += 1
        positions.append(
            PositionMetrics(
                ticker=ticker,
                quantity=quantity,
                average_cost=average_cost,
                total_cost=cost,
                current_price=price,
                market_value=market_value,
                gain_loss=gain_loss,
                return_percent=return_percent,
            )
        )

    priced_cost = sum(position.total_cost for position in positions if position.current_price is not None)
    gain_loss = total_market_value - priced_cost
    return_percent = (gain_loss / priced_cost * 100) if priced_cost else None
    return PortfolioMetrics(
        positions=positions,
        total_cost=total_cost,
        priced_cost=priced_cost,
        market_value=total_market_value,
        gain_loss=gain_loss,
        return_percent=return_percent,
        excluded_purchase_count=excluded,
        priced_position_count=priced_position_count,
        refreshed_at=datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
    )
