"""Deterministic import of portfolio positions supplied as JSON."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
import re
from typing import Any

from .models import Purchase


class PortfolioImportError(ValueError):
    """Raised when JSON looks like a portfolio but contains invalid positions."""


@dataclass(frozen=True)
class PortfolioImport:
    purchases: list[Purchase]
    source_position_count: int


@dataclass(frozen=True)
class PortfolioUpdate:
    ticker: str
    quantity: float | None = None
    price: float | None = None
    purchased_at: date | None = None
    note: str | None = None
    fields: frozenset[str] = frozenset()


def parse_portfolio_update_json_message(message: str) -> list[PortfolioUpdate] | None:
    """Parse partial position patches without filling omitted values."""
    payload = _extract_json(message)
    if payload is None:
        return None
    records = _records(payload, require_quantity=False)
    if records is None:
        return None
    updates: list[PortfolioUpdate] = []
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        try:
            updates.append(_update_from_record(record, index))
        except PortfolioImportError as exc:
            errors.append(str(exc))
    if errors:
        raise PortfolioImportError("Could not update the portfolio JSON: " + " ".join(errors))
    return updates


def parse_portfolio_json_message(message: str) -> PortfolioImport | None:
    """Parse portfolio-shaped JSON, returning None for unrelated JSON/text."""
    payload = _extract_json(message)
    if payload is None:
        return None
    records = _records(payload)
    if records is None:
        return None
    if not records:
        raise PortfolioImportError("The portfolio JSON contains no positions.")

    purchases: list[Purchase] = []
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        try:
            purchases.append(_purchase_from_record(record, index))
        except PortfolioImportError as exc:
            errors.append(str(exc))
    if errors:
        raise PortfolioImportError("Could not import the portfolio JSON: " + " ".join(errors))
    return PortfolioImport(purchases=purchases, source_position_count=len(records))


def _extract_json(message: str) -> Any | None:
    text = message.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    decoder = json.JSONDecoder()
    starts = [start for marker in ("{", "[") if (start := text.find(marker)) >= 0]
    for start in sorted(starts):
        try:
            payload, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        return payload
    return None


def _records(payload: Any, *, require_quantity: bool = True) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        normalized = {_key(key): value for key, value in payload.items()}
        records = None
        for key in (
            "holdings",
            "positions",
            "purchases",
            "assets",
            "stocks",
            "securities",
            "equities",
            "portfolio",
            "data",
        ):
            candidate = normalized.get(key)
            if isinstance(candidate, list):
                records = candidate
                break
        if records is None and _has_position_fields(normalized, require_quantity=require_quantity):
            records = [payload]
    else:
        return None
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise PortfolioImportError("Portfolio positions must be a JSON list of objects.")
    if not any(
        _has_position_fields({_key(key): value for key, value in item.items()}, require_quantity=require_quantity)
        for item in records
    ):
        return None
    return records


def _purchase_from_record(record: dict[str, Any], index: int) -> Purchase:
    values = {_key(key): value for key, value in record.items()}
    ticker = _text(_first(values, "ticker", "symbol", "ric"))
    quantity = _number(_first(values, "quantity", "shares", "units", "qty"))
    price = _number(
        _first(
            values,
            "purchaseprice",
            "purchasepricepershare",
            "averagecost",
            "averagecostpershare",
            "avgcost",
            "costbasis",
            "entryprice",
            "price",
        )
    )
    if not ticker:
        raise PortfolioImportError(f"Position {index}: ticker/symbol is missing.")
    if quantity is None or quantity <= 0:
        raise PortfolioImportError(f"Position {index} ({ticker}): shares/quantity must be greater than zero.")
    if price is None or price < 0:
        raise PortfolioImportError(
            f"Position {index} ({ticker}): purchase price/average cost is required and cannot be negative."
        )
    purchased_at = _date(_first(values, "purchasedat", "purchasedate", "acquiredat", "date"))
    note = _text(_first(values, "note", "notes")) or ""
    if purchased_at is None:
        purchased_at = date.today()
        note = (note + " | " if note else "") + "Imported; purchase date unavailable"
    return Purchase(ticker=ticker.upper(), quantity=quantity, price=price, purchased_at=purchased_at, note=note)


def _update_from_record(record: dict[str, Any], index: int) -> PortfolioUpdate:
    values = {_key(key): value for key, value in record.items()}
    ticker = _text(_first(values, "ticker", "symbol", "ric"))
    if not ticker:
        raise PortfolioImportError(f"Update {index}: ticker/symbol is missing.")
    ticker = ticker.upper()
    if ticker in {"ALL", "*"}:
        ticker = "*"
    fields: set[str] = set()
    quantity = price = None
    purchased_at = None
    note = None
    quantity_value = _first(values, "quantity", "shares", "units", "qty")
    if quantity_value is not None:
        quantity = _number(quantity_value)
        if quantity is None or quantity <= 0:
            raise PortfolioImportError(f"Update {index} ({ticker}): quantity must be greater than zero.")
        fields.add("quantity")
    price_value = _first(values, "purchaseprice", "purchasepricepershare", "averagecost", "averagecostpershare", "avgcost", "price")
    if price_value is not None:
        price = _number(price_value)
        if price is None or price < 0:
            raise PortfolioImportError(f"Update {index} ({ticker}): purchase price cannot be negative.")
        fields.add("price")
    date_key = next((key for key in ("purchasedat", "purchasedate", "acquiredat", "date") if key in values), None)
    if date_key is not None:
        purchased_at = _date(values[date_key])
        if purchased_at is None:
            raise PortfolioImportError(f"Update {index} ({ticker}): purchase date must be YYYY-MM-DD.")
        fields.add("purchased_at")
    note_key = next((key for key in ("note", "notes") if key in values), None)
    if note_key is not None:
        note = _text(values[note_key]) or ""
        fields.add("note")
    if not fields:
        raise PortfolioImportError(f"Update {index} ({ticker}): no update fields were provided.")
    return PortfolioUpdate(ticker=ticker, quantity=quantity, price=price, purchased_at=purchased_at, note=note, fields=frozenset(fields))


def _has_position_fields(values: dict[str, Any], *, require_quantity: bool = True) -> bool:
    if _first(values, "ticker", "symbol", "ric") is None:
        return False
    if not require_quantity:
        return True
    return _first(values, "quantity", "shares", "units", "qty") is not None


def _first(values: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in values:
            return values[key]
    return None


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
