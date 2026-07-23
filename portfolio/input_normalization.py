"""Small deterministic normalizations applied before research planning."""

from __future__ import annotations

import re


_US_BEFORE_FINANCE_TERM = re.compile(
    r"\bus(?=\s+(?:stocks?|equities|companies|firms|markets?|securities|shares|"
    r"listed|headquartered|based|technology|tech|industrials?|healthcare|"
    r"financials?|banks?|energy|utilities|materials|real\s+estate)\b)",
    re.IGNORECASE,
)

_FINANCE_TERM_IN_US = re.compile(
    r"(?P<prefix>\b(?:stocks?|equities|companies|firms|markets?|securities|shares)"
    r"(?:\s+(?:listed|based|headquartered))?\s+in\s+)us\b",
    re.IGNORECASE,
)

_US_GEOGRAPHY_FILTER = re.compile(
    r"(?P<prefix>\b(?:country|geography|headquarters?|listing)\s*(?:is|=|:)?\s*)us\b",
    re.IGNORECASE,
)


def normalize_research_request(message: str) -> str:
    """Normalize lowercase ``us`` only when it clearly means United States.

    The ordinary pronoun remains untouched, so text such as ``tell us about
    Apple`` is not changed. This fixes requests such as ``analyze us stocks``.
    """

    normalized = _US_BEFORE_FINANCE_TERM.sub("US", message)
    normalized = _FINANCE_TERM_IN_US.sub(lambda match: match.group("prefix") + "US", normalized)
    normalized = _US_GEOGRAPHY_FILTER.sub(lambda match: match.group("prefix") + "US", normalized)
    return normalized
