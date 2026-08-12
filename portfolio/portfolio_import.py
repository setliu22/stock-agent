"""Deterministic import of portfolio positions supplied as JSON."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
import re
from difflib import SequenceMatcher
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
    replacement_lots: tuple[Purchase, ...] | None = None


_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "ticker": ("ticker", "symbol", "ric"),
    "quantity": ("quantity", "shares", "units", "qty"),
    "purchaseprice": ("purchaseprice", "purchasepricepershare", "averagecost", "averagecostpershare", "avgcost", "costbasis", "entryprice", "price"),
    "purchasedat": ("purchasedat", "purchasedate", "acquiredat", "date"),
    "note": ("note", "notes"),
    "container": ("holdings", "positions", "purchases", "assets", "stocks", "securities", "equities", "portfolio", "data", "correctedpurchasedata", "purchasesbyticker", "lotsbyticker"),
}


def parse_portfolio_update_json_message(message: str) -> list[PortfolioUpdate] | None:
    """Parse partial position patches without filling omitted values."""
    payload = _extract_json(message)
    if payload is None:
        return None
    grouped = _grouped_lot_records(payload)
    if grouped is not None:
        updates: list[PortfolioUpdate] = []
        for ticker, records in grouped.items():
            lots: list[Purchase] = []
            for index, record in enumerate(records, start=1):
                try:
                    lots.append(_purchase_from_record({"ticker": ticker, **record}, index))
                except PortfolioImportError as exc:
                    raise PortfolioImportError(f"Could not update {ticker}: {exc}") from exc
            if not lots:
                raise PortfolioImportError(f"Could not update {ticker}: no purchase lots were provided.")
            updates.append(
                PortfolioUpdate(
                    ticker=ticker.upper(),
                    fields=frozenset({"replacement_lots"}),
                    replacement_lots=tuple(lots),
                )
            )
        return updates
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


def _grouped_lot_records(payload: Any) -> dict[str, list[dict[str, Any]]] | None:
    if not isinstance(payload, dict):
        return None
    normalized = _normalize_mapping(payload, "container")
    container = next((value for key, value in normalized.items() if key in {"correctedpurchasedata", "purchasesbyticker", "lotsbyticker"} and isinstance(value, dict)), None)
    if container is None:
        return None
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ticker, records in container.items():
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise PortfolioImportError(f"{ticker}: purchase data must be a JSON list of objects.")
        grouped[str(ticker).strip().upper()] = records
    return grouped


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
        normalized = _normalize_mapping(payload, "container")
        records = None
        for key in _KEY_ALIASES["container"]:
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
        _has_position_fields(item, require_quantity=require_quantity)
        for item in records
    ):
        return None
    return records


def _purchase_from_record(record: dict[str, Any], index: int) -> Purchase:
    values = _normalize_mapping(record, "fields")
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
    values = _normalize_mapping(record, "fields")
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
    date_key = "purchasedat" if "purchasedat" in values else None
    if date_key is not None:
        purchased_at = _date(values[date_key])
        if purchased_at is None:
            raise PortfolioImportError(f"Update {index} ({ticker}): purchase date must be YYYY-MM-DD.")
        fields.add("purchased_at")
    note_key = "note" if "note" in values else None
    if note_key is not None:
        note = _text(values[note_key]) or ""
        fields.add("note")
    if not fields:
        raise PortfolioImportError(f"Update {index} ({ticker}): no update fields were provided.")
    return PortfolioUpdate(ticker=ticker, quantity=quantity, price=price, purchased_at=purchased_at, note=note, fields=frozenset(fields))


def _has_position_fields(values: dict[str, Any], *, require_quantity: bool = True) -> bool:
    values = _normalize_mapping(values, "fields")
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


def _normalize_mapping(values: dict[str, Any], scope: str) -> dict[str, Any]:
    """Normalize known key variants; reject ambiguous close matches, ignore metadata."""
    field_aliases = tuple(
        alias for canonical, names in _KEY_ALIASES.items() if canonical != "container" for alias in names
    )
    aliases = _KEY_ALIASES["container"] + field_aliases if scope == "container" else field_aliases
    canonical_by_alias = {
        alias: canonical
        for canonical, names in _KEY_ALIASES.items()
        if canonical != "container" or scope == "container"
        for alias in names
    }
    output: dict[str, Any] = {}
    for raw_key, value in values.items():
        token = _key(raw_key)
        if scope == "container" and token in _KEY_ALIASES["container"]:
            output[token] = value
            continue
        if token in canonical_by_alias:
            output[canonical_by_alias[token]] = value
            continue
        scores_by_canonical: dict[str, float] = {}
        for alias in aliases:
            canonical = alias if alias in _KEY_ALIASES["container"] else canonical_by_alias[alias]
            scores_by_canonical[canonical] = max(
                scores_by_canonical.get(canonical, 0.0),
                SequenceMatcher(None, token, alias).ratio(),
            )
        candidates = sorted(scores_by_canonical.items(), key=lambda item: (item[1], item[0]), reverse=True)
        if candidates and candidates[0][1] >= 0.88 and (
            len(candidates) == 1 or candidates[0][1] - candidates[1][1] >= 0.06
        ):
            output[candidates[0][0]] = value
    return output


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
