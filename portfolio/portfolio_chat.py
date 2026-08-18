"""Conversational extraction for cloud portfolio purchases.

The deterministic parser handles common statements locally. When Groq is
already configured for the stock agent, a constrained JSON extraction pass can
fill in ambiguous wording. The model never writes to the cloud directly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
import json
import os
import re
from typing import Any


PURCHASE_VERBS = re.compile(
    r"\b(?:bought|buy|purchased|purchase|acquired|add(?:ed)?|picked\s+up)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class PurchaseDraft:
    original_message: str
    portfolio_name: str | None = None
    security_name: str | None = None
    ticker: str | None = None
    quantity: float | None = None
    purchase_price: float | None = None
    purchased_at: str | None = None
    note: str = ""

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.portfolio_name:
            missing.append("portfolio")
        if not self.ticker:
            missing.append("ticker")
        if self.quantity is None:
            missing.append("number of shares")
        if self.purchase_price is None:
            missing.append("purchase price per share")
        if not self.purchased_at:
            missing.append("purchase date")
        return missing

    def summary(self) -> str:
        values = {
            "portfolio": self.portfolio_name or "N/A",
            "ticker": self.ticker or self.security_name or "N/A",
            "shares": _format_number(self.quantity),
            "price": f"${self.purchase_price:,.2f}" if self.purchase_price is not None else "N/A",
            "date": self.purchased_at or "N/A",
        }
        return ", ".join(f"{key}={value}" for key, value in values.items())


def is_purchase_statement(message: str) -> bool:
    return bool(PURCHASE_VERBS.search(message)) and bool(
        re.search(r"\b(?:shares?|stock|position|portfolio|ticker)\b", message, re.IGNORECASE)
    )


def parse_purchase_statement(message: str, *, use_llm: bool = True) -> PurchaseDraft:
    draft = _parse_deterministically(message)
    if use_llm and draft.missing_fields() and os.getenv("GROQ_API_KEY", "").strip():
        draft = _merge(draft, _parse_with_groq(message))
    return draft


def parse_follow_up(draft: PurchaseDraft, reply: str, *, use_llm: bool = True) -> PurchaseDraft:
    combined = f"{draft.original_message}\nMissing-information reply: {reply}"
    parsed = _parse_deterministically(reply, follow_up=True)
    if use_llm and os.getenv("GROQ_API_KEY", "").strip():
        parsed = _merge(parsed, _parse_with_groq(combined))
    return _merge(draft, parsed, overwrite_missing_only=True)


def missing_prompt(draft: PurchaseDraft) -> str:
    fields = draft.missing_fields()
    if not fields:
        return ""
    joined = ", ".join(fields[:-1]) + (f" and {fields[-1]}" if len(fields) > 1 else fields[0])
    return (
        f"I found: {draft.summary()}.\n"
        f"Reply once with the missing {joined}. Example: "
        'Retirement, ticker ABCD, 5 shares, $22.40, 2026-07-21. '
        "Anything still unclear after your reply will be stored as N/A; an unknown portfolio uses Unsorted."
    )


def _parse_deterministically(message: str, *, follow_up: bool = False) -> PurchaseDraft:
    text = message.strip()
    quantity = _first_float(
        text,
        [
            r"\b(\d+(?:\.\d+)?)\s*(?:shares?|units?)\b",
            r"\bquantity\s*[:=]?\s*(\d+(?:\.\d+)?)\b",
        ],
    )
    purchase_price = _first_float(
        text,
        [
            r"(?:\bat\b|\bfor\b|\bprice(?:\s+per\s+share)?\b)\s*[:=]?\s*\$\s*(\d+(?:\.\d+)?)",
            r"(?:\bat\b|\bprice(?:\s+per\s+share)?\b)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:dollars?|usd)\b",
            r"\$\s*(\d+(?:\.\d+)?)\b",
        ],
    )
    ticker = _extract_ticker(text)
    portfolio_name = _extract_portfolio(text)
    purchased_at = _extract_date(text)
    security_name = _extract_security_name(text, ticker)
    note_match = re.search(r"\bnote\s*[:=]\s*(.+)$", text, re.IGNORECASE)
    note = note_match.group(1).strip() if note_match else ("" if follow_up else text)
    return PurchaseDraft(
        original_message=text,
        portfolio_name=portfolio_name,
        security_name=security_name,
        ticker=ticker,
        quantity=quantity,
        purchase_price=purchase_price,
        purchased_at=purchased_at,
        note=note,
    )


def _extract_ticker(text: str) -> str | None:
    patterns = [
        r"\bticker\s*[:=]?\s*\$?([A-Za-z][A-Za-z0-9.-]{0,9})\b",
        r"\bshares?\s+(?:of|in)\s+\$?([A-Za-z][A-Za-z0-9.-]{0,9})\b",
        r"\bstock\s+(?:of|in)?\s*\$?([A-Za-z][A-Za-z0-9.-]{0,9})\b",
        r"\$([A-Za-z][A-Za-z0-9.-]{0,9})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).upper()
            if candidate not in {"OF", "IN", "AT", "FOR", "THE", "MY", "NA"}:
                return candidate
    for token in re.findall(r"\b[A-Z][A-Z0-9.-]{1,5}\b", text):
        if token not in {"USD", "N/A", "NA", "I"}:
            return token
    return None


def _extract_portfolio(text: str) -> str | None:
    explicit = re.search(
        r"\bportfolio\s*[:=]\s*([A-Za-z0-9][A-Za-z0-9 _-]{0,79}?)(?=\s+(?:at|for|ticker|price|on)\b|[,.;]|$)",
        text,
        re.IGNORECASE,
    )
    if explicit:
        value = " ".join(explicit.group(1).strip().split())
        return value if value.casefold() not in {"n/a", "na", "unknown"} else None

    natural = re.search(
        r"\b(?:(?:in|into|to)\s+(?:my\s+)?)?portfolio\s+"
        r"([A-Za-z0-9][A-Za-z0-9 _-]{0,69}?)(?=\s+(?:at|for|ticker|price|on)\b|[,.;]|$)",
        text,
        re.IGNORECASE,
    )
    if natural:
        suffix = " ".join(natural.group(1).strip().split())
        if suffix.casefold() not in {"n/a", "na", "unknown"}:
            return f"Portfolio {suffix}"
    return None


def _extract_security_name(text: str, ticker: str | None) -> str | None:
    if ticker:
        return ticker
    match = re.search(
        r"\bshares?\s+(?:of|in)\s+([A-Za-z][A-Za-z0-9 &.'-]{1,60}?)(?=\s+(?:at|for|on|into|to)\b|[,.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        value = " ".join(match.group(1).strip().split())
        if value.casefold() not in {"n/a", "na", "unknown"}:
            return value
    return None


def _extract_date(text: str) -> str | None:
    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1)).isoformat()
        except ValueError:
            return None
    lowered = text.casefold()
    if re.search(r"\btoday\b", lowered):
        return date.today().isoformat()
    if re.search(r"\byesterday\b", lowered):
        return (date.today() - timedelta(days=1)).isoformat()
    return None


def _first_float(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


def _parse_with_groq(message: str) -> PurchaseDraft:
    try:
        from langchain_groq import ChatGroq
    except Exception:
        return PurchaseDraft(original_message=message)
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    system = (
        "Extract a stock purchase into strict JSON. Never infer unavailable facts. "
        "Return only keys portfolio_name, security_name, ticker, quantity, purchase_price, "
        "purchased_at, note. Use null for unknown values. Ticker must be uppercase. "
        "purchased_at must be YYYY-MM-DD only when explicitly stated."
    )
    try:
        response = ChatGroq(model=model, temperature=0).invoke(
            [("system", system), ("human", message)]
        )
        raw = str(getattr(response, "content", response)).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return PurchaseDraft(original_message=message)
        return PurchaseDraft(
            original_message=message,
            portfolio_name=_text(payload.get("portfolio_name")),
            security_name=_text(payload.get("security_name")),
            ticker=_text(payload.get("ticker"), uppercase=True),
            quantity=_number(payload.get("quantity")),
            purchase_price=_number(payload.get("purchase_price")),
            purchased_at=_valid_date(payload.get("purchased_at")),
            note=_text(payload.get("note")) or "",
        )
    except Exception:
        return PurchaseDraft(original_message=message)


def _merge(
    base: PurchaseDraft,
    extra: PurchaseDraft,
    *,
    overwrite_missing_only: bool = False,
) -> PurchaseDraft:
    values: dict[str, Any] = {}
    for field in (
        "portfolio_name",
        "security_name",
        "ticker",
        "quantity",
        "purchase_price",
        "purchased_at",
        "note",
    ):
        old = getattr(base, field)
        new = getattr(extra, field)
        if overwrite_missing_only:
            values[field] = old if old not in (None, "") else new
        else:
            values[field] = new if new not in (None, "") else old
    return replace(base, **values)


def _text(value: Any, *, uppercase: bool = False) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in {"n/a", "na", "unknown", "none", "null"}:
        return None
    return text.upper() if uppercase else text


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _valid_date(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _format_number(value: float | None) -> str:
    return "N/A" if value is None else f"{value:g}"
