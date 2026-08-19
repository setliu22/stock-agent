from datetime import date, datetime, timezone
from types import SimpleNamespace

import pandas as pd

from portfolio.agent import StockAgent
from portfolio.company_resolver import ResolvedInstrument
from portfolio.config import Settings
from portfolio.database import PortfolioDatabase
from portfolio.event_risk import (
    build_portfolio_review_plan,
    run_portfolio_position_risk_review,
    score_portfolio_position_risk,
)
from portfolio.market_regime import MarketRegimeSnapshot, RegimeIndicator
from portfolio.models import Holding, Purchase


def _macro(regime: str = "Mixed liquidity regime", *, credit_rising: bool = False):
    return MarketRegimeSnapshot(
        regime=regime,
        summary="Test macro snapshot",
        emphasis=(),
        indicators=(
            RegimeIndicator(
                key="high_yield_spread",
                label="High-yield spread",
                latest="3.5%",
                trend="Rising (+0.2 pp)" if credit_rising else "Stable (+0.0 pp)",
                as_of="2026-08-01",
                source="test",
            ),
        ),
        missing_evidence=(),
        generated_at=datetime.now(timezone.utc),
    )


def _result(*, event_type: str = "Earnings", event_title: str = "Quarterly earnings"):
    ric = "AAPL.O"
    return SimpleNamespace(
        resolved=[ResolvedInstrument("AAPL", "AAPL", "AAPL", ric, "Apple")],
        tables={
            "events": pd.DataFrame(
                [
                    {
                        "Instrument": ric,
                        "TR.EventType": event_type,
                        "TR.EventTitle": event_title,
                        "TR.EventStartDate": "2026-08-20",
                    }
                ]
            ),
            "estimates": pd.DataFrame(
                [
                    {
                        "Instrument": ric,
                        "TR.EPSMean(Period=FY1)": 5.0,
                        "TR.EPSMean(Period=FY2)": 6.0,
                        "TR.RevenueMean(Period=FY1)": 100.0,
                        "TR.RevenueMean(Period=FY2)": 110.0,
                    }
                ]
            ),
            "recommendations": pd.DataFrame(
                [{"Instrument": ric, "TR.LTGMean": 15.0}]
            ),
            "valuation": pd.DataFrame(
                [{"Instrument": ric, "TR.PtoEPSMeanEst(Period=FY1)": 40.0}]
            ),
        },
        metrics={
            f"{ric}:eps_revision_30d": -0.06,
            f"{ric}:return_1m": 0.18,
            f"{ric}:annualized_vol": 0.65,
            f"{ric}:operating_margin": 0.20,
            f"{ric}:fcf_margin": 0.15,
            f"{ric}:net_debt": -10.0,
            f"{ric}:target_upside": -0.10,
            f"{ric}:peer_median:TR.PtoEPSMeanEst(Period=FY1)": 20.0,
            f"{ric}:evidence_family_count": 9,
        },
    )


def test_portfolio_review_plan_is_fixed_and_validated():
    plan = build_portfolio_review_plan(["aapl", "MSFT"])

    assert plan.workflow == "company_compare"
    assert plan.entities == ["AAPL", "MSFT"]
    assert set(plan.topics) >= {
        "fundamentals",
        "profitability",
        "valuation",
        "estimates",
        "events",
    }
    assert plan.investment_horizon == "medium_term"


def test_position_review_batches_more_than_eight_holdings(monkeypatch):
    settings = SimpleNamespace(groq_api_key=None, groq_model="test-model")
    holdings = [Holding(f"TICK{i}", 1, 100, 100) for i in range(9)]
    calls = []

    def fake_run(plan, *_args, **_kwargs):
        calls.append(plan.entities)
        return SimpleNamespace(
            resolved=[
                ResolvedInstrument(ticker, ticker, ticker, f"{ticker}.O", ticker)
                for ticker in plan.entities
            ],
            tables={},
            metrics={
                f"{ticker}.O:evidence_family_count": 1 for ticker in plan.entities
            },
        )

    monkeypatch.setattr("portfolio.lseg_research.run_research", fake_run)
    review = run_portfolio_position_risk_review(
        settings, holdings, macro_snapshot=_macro()
    )

    assert [len(batch) for batch in calls] == [8, 1]
    assert [item.ticker for item in review.holdings] == [f"TICK{i}" for i in range(9)]


def test_position_risk_separates_thesis_valuation_macro_and_event_evidence():
    review = score_portfolio_position_risk(
        [Holding("AAPL", 10, 1500, 150)],
        _result(),
        _macro(),
        today=date(2026, 8, 5),
    )

    item = review.holdings[0]
    assert item.rating == "REVIEW"
    assert item.score == 6
    assert item.days_to_event == 15
    assert set(item.sections) == {
        "Company thesis",
        "Valuation",
        "Macro fit",
        "Event risk",
    }
    assert {signal.label for signal in item.signals} == {
        "Falling estimates",
        "Peer premium",
        "Target value exceeded",
        "Near-term event",
    }
    assert "Recent run-up" not in {signal.label for signal in item.signals}
    assert "Elevated volatility" not in {signal.label for signal in item.signals}
    assert "run-up itself is not a sell signal" in " ".join(item.sections["Valuation"])
    assert "volatility alone adds no risk points" in " ".join(item.sections["Event risk"])


def test_unprofitable_leveraged_growth_gets_macro_exit_risk_in_mixed_regime():
    result = _result()
    result.metrics.update(
        {
            "AAPL.O:eps_revision_30d": 0.01,
            "AAPL.O:operating_margin": -0.10,
            "AAPL.O:fcf_margin": -0.20,
            "AAPL.O:net_debt": 500.0,
            "AAPL.O:debt_to_fcf": 8.0,
            "AAPL.O:target_upside": 0.20,
        }
    )

    review = score_portfolio_position_risk(
        [Holding("AAPL", 10, 1500, 150)], result, _macro(), today=date(2026, 8, 5)
    )

    item = review.holdings[0]
    macro_signals = [signal for signal in item.signals if signal.category == "Macro fit"]
    assert any(signal.label == "Macro exit risk" and signal.points == 2 for signal in macro_signals)


def test_ordinary_dividend_is_not_treated_as_downside_event_risk():
    result = _result(event_type="Dividend", event_title="Quarterly dividend")

    review = score_portfolio_position_risk(
        [Holding("AAPL", 10, 1500, 150)], result, _macro(), today=date(2026, 8, 5)
    )

    item = review.holdings[0]
    assert item.upcoming_event is None
    assert not any(signal.category == "Event risk" for signal in item.signals)


def test_prefilled_position_risk_prompt_routes_to_position_review(tmp_path, monkeypatch):
    settings = Settings(
        tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace"
    )
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    monkeypatch.setattr(
        agent, "review_position_risk", lambda *_args: "position review invoked"
    )

    response = agent.handle(
        "Review my portfolio positions for reasons to hold, review, trim, or consider exiting."
    )

    assert response == "position review invoked"


def test_position_review_reports_macro_refresh_before_research(tmp_path, monkeypatch):
    settings = Settings(
        tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace"
    )
    database = PortfolioDatabase(settings.database_path)
    database.record_purchase(Purchase("AAPL", 1, 100, date(2026, 1, 1)))
    agent = StockAgent(settings, database)
    progress = []
    monkeypatch.setattr("portfolio.agent.build_market_regime", lambda: _macro())
    monkeypatch.setattr(
        "portfolio.agent.run_portfolio_position_risk_review",
        lambda *_args, **_kwargs: SimpleNamespace(to_text=lambda: "position review"),
    )

    response = agent.review_position_risk(
        lambda percent, stage, detail: progress.append((percent, stage, detail))
    )

    assert response == "position review"
    assert progress[0][0:2] == (2, "Refreshing market regime")
