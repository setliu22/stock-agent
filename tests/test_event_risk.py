from datetime import date
from types import SimpleNamespace

import pandas as pd

from portfolio.company_resolver import ResolvedInstrument
from portfolio.event_risk import build_portfolio_review_plan, score_portfolio_event_risk
from portfolio.models import Holding


def _result():
    return SimpleNamespace(
        resolved=[ResolvedInstrument("AAPL", "AAPL", "AAPL", "AAPL.O", "Apple")],
        tables={
            "events": pd.DataFrame(
                [
                    {
                        "Instrument": "AAPL.O",
                        "TR.EventType": "Earnings",
                        "TR.EventTitle": "Quarterly earnings",
                        "TR.EventStartDate": "2026-08-20",
                    }
                ]
            )
        },
        metrics={
            "AAPL.O:eps_revision_30d": -0.06,
            "AAPL.O:return_1m": 0.18,
            "AAPL.O:annualized_vol": 0.65,
            "AAPL.O:risk_headline_hits": 2,
            "AAPL.O:evidence_family_count": 6,
        },
    )


def test_portfolio_review_plan_is_fixed_and_validated():
    plan = build_portfolio_review_plan(["aapl", "MSFT"])

    assert plan.workflow == "company_compare"
    assert plan.entities == ["AAPL", "MSFT"]
    assert plan.topics == ["valuation", "estimates", "price", "risk", "news", "events"]
    assert plan.investment_horizon == "short_term"


def test_event_risk_flags_converging_signals_without_llm():
    review = score_portfolio_event_risk(
        [Holding("AAPL", 10, 1500, 150)],
        _result(),
        today=date(2026, 8, 5),
    )

    item = review.holdings[0]
    assert item.rating == "Review soon"
    assert item.score == 10
    assert item.days_to_event == 15
    assert {signal.label for signal in item.signals} == {
        "Upcoming event",
        "Negative estimate revision",
        "Recent run-up",
        "Elevated volatility",
        "Recent risk headlines",
    }
    assert item.confidence == "high"


def test_event_risk_does_not_call_missing_event_a_sell_signal():
    result = _result()
    result.tables = {}
    result.metrics = {"AAPL.O:evidence_family_count": 2}

    review = score_portfolio_event_risk(
        [Holding("AAPL", 10, 1500, 150)], result, today=date(2026, 8, 5)
    )

    item = review.holdings[0]
    assert item.rating == "Insufficient data"
    assert item.score == 0
    assert "upcoming LSEG event date" in item.missing
