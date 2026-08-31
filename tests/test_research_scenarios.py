"""Permanent product-level contracts for representative Research Lab questions."""

from __future__ import annotations

import pytest

from portfolio.research_lab import validate_proposal_payload


def _item(item_id: str) -> dict[str, str]:
    return {"id": item_id, "reason": "Required by this scenario."}


@pytest.mark.parametrize(
    ("question", "payload", "expected"),
    [
        (
            "What semiconductor companies are undervalued?",
            {
                "mode": "discovery",
                "securities": [],
                "discovery_scopes": ["Semiconductors"],
                "exchange_geography": None,
                "discovery_theme": None,
                "result_count": 5,
                "lookback_days": 365,
                "benchmark": None,
                "selection_objectives": ["relative_value"],
                "capabilities": [_item("candidate_discovery"), _item("valuation_snapshot")],
                "analyses": [],
            },
            ("discovery", ("Semiconductors",), ("relative_value",), None),
        ),
        (
            "Which companies related to data centers have strong fundamentals?",
            {
                "mode": "discovery",
                "securities": [],
                "discovery_scopes": ["Technology", "Utilities", "Industrials"],
                "exchange_geography": None,
                "discovery_theme": "data centers",
                "result_count": 5,
                "lookback_days": 365,
                "benchmark": None,
                "selection_objectives": ["positive_signals"],
                "capabilities": [_item("candidate_discovery"), _item("company_profile")],
                "analyses": [],
            },
            (
                "discovery",
                ("Technology", "Utilities", "Industrials"),
                ("positive_signals",),
                None,
            ),
        ),
        (
            "What European industrial stock is undervalued?",
            {
                "mode": "discovery",
                "securities": [],
                "discovery_scopes": ["Industrials"],
                "exchange_geography": "Europe",
                "discovery_theme": None,
                "result_count": 1,
                "lookback_days": 365,
                "benchmark": None,
                "selection_objectives": ["relative_value"],
                "capabilities": [_item("candidate_discovery"), _item("valuation_snapshot")],
                "analyses": [],
            },
            ("discovery", ("Industrials",), ("relative_value",), "Europe"),
        ),
        (
            "How did AAPL perform when the 10-year Treasury yield fell?",
            {
                "mode": "named",
                "securities": ["AAPL"],
                "discovery_scopes": [],
                "exchange_geography": None,
                "discovery_theme": None,
                "result_count": 5,
                "lookback_days": 730,
                "benchmark": None,
                "selection_objectives": [],
                "capabilities": [_item("price_history"), _item("treasury_yield_history")],
                "analyses": [_item("falling_rate_comparison")],
            },
            ("named", (), (), None),
        ),
        (
            "What market developments are moving semiconductor stocks today?",
            {
                "mode": "market_news",
                "securities": [],
                "discovery_scopes": [],
                "exchange_geography": None,
                "discovery_theme": None,
                "result_count": 5,
                "lookback_days": 90,
                "benchmark": None,
                "selection_objectives": [],
                "capabilities": [_item("market_news")],
                "analyses": [],
            },
            ("market_news", (), (), None),
        ),
    ],
)
def test_research_question_contracts(question, payload, expected) -> None:
    proposal = validate_proposal_payload(question, payload)

    assert (
        proposal.mode,
        proposal.discovery_scopes,
        proposal.selection_objectives,
        proposal.exchange_geography,
    ) == expected
