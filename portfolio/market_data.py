"""Free market-data fallback helpers based on yfinance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .company_resolver import company_name_to_ticker, extract_security_reference, is_explicit_ticker


@dataclass(frozen=True)
class MarketSnapshot:
    ticker: str
    company_name: str | None
    current_price: float | None
    currency: str | None
    market_cap: float | None
    trailing_pe: float | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_yahoo_ticker(query: str) -> tuple[str, str | None]:
    reference = extract_security_reference(query)
    if is_explicit_ticker(reference):
        return reference.upper(), None
    ticker, company_name, _exchange = company_name_to_ticker(reference)
    return ticker, company_name


def current_price(ticker: str) -> float | None:
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance is not installed.") from exc

    instrument = yf.Ticker(ticker)
    try:
        fast_info = instrument.fast_info
        value = fast_info.get("last_price") if hasattr(fast_info, "get") else None
        parsed = _number(value)
        if parsed is not None:
            return parsed
    except Exception:
        pass

    history = instrument.history(period="5d", auto_adjust=False)
    if history is None or history.empty or "Close" not in history.columns:
        return None
    return _number(history["Close"].dropna().iloc[-1])


def snapshot(query: str) -> MarketSnapshot:
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance is not installed.") from exc

    ticker, searched_name = resolve_yahoo_ticker(query)
    instrument = yf.Ticker(ticker)
    info: dict[str, Any] = {}
    try:
        info = instrument.info or {}
    except Exception:
        info = {}

    price = _number(info.get("currentPrice") or info.get("regularMarketPrice"))
    if price is None:
        price = current_price(ticker)

    return MarketSnapshot(
        ticker=ticker,
        company_name=info.get("longName") or info.get("shortName") or searched_name,
        current_price=price,
        currency=info.get("currency"),
        market_cap=_number(info.get("marketCap")),
        trailing_pe=_number(info.get("trailingPE")),
        fifty_two_week_high=_number(info.get("fiftyTwoWeekHigh")),
        fifty_two_week_low=_number(info.get("fiftyTwoWeekLow")),
    )
