from __future__ import annotations

from datetime import date, timedelta

import pytest

from portfolio.market_regime import (
    FRED_SERIES,
    MacroResearchPolicy,
    Observation,
    ResearchWeights,
    build_market_regime,
    macro_default_policy,
)


def _daily(start: float, end: float, days: int = 120) -> list[Observation]:
    first = date(2026, 1, 1)
    return [
        Observation(first + timedelta(days=index), start + (end - start) * index / days)
        for index in range(days + 1)
    ]


def _monthly(values: list[float]) -> list[Observation]:
    return [
        Observation(date(2025 + index // 12, index % 12 + 1, 1), value)
        for index, value in enumerate(values)
    ]


def test_market_regime_classifies_easing_and_expanding_liquidity() -> None:
    series = {
        FRED_SERIES["fed_funds"]: _daily(5.25, 4.25),
        FRED_SERIES["fed_balance_sheet"]: _daily(7_000_000, 7_140_000),
        FRED_SERIES["cpi"]: _monthly(
            [300, 301, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 312, 313]
        ),
        FRED_SERIES["high_yield_spread"]: _daily(4.0, 3.5),
    }

    snapshot = build_market_regime(
        fred_loader=lambda series_id: series[series_id],
        vix_loader=lambda: _daily(24, 18),
    )

    assert snapshot.regime == "Easing and expanding liquidity"
    assert all(indicator.status == "available" for indicator in snapshot.indicators)
    assert snapshot.indicators[1].latest == "$7.14T"
    assert "not a buy or sell signal" in snapshot.to_text()


def test_market_regime_adds_inflation_and_stress_cautions() -> None:
    series = {
        FRED_SERIES["fed_funds"]: _daily(4.0, 5.0),
        FRED_SERIES["fed_balance_sheet"]: _daily(7_200_000, 7_000_000),
        FRED_SERIES["cpi"]: _monthly(
            [300, 301, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 313, 316]
        ),
        FRED_SERIES["high_yield_spread"]: _daily(3.0, 4.5),
    }

    snapshot = build_market_regime(
        fred_loader=lambda series_id: series[series_id],
        vix_loader=lambda: _daily(15, 25),
    )

    assert snapshot.regime == "Tightening and contracting liquidity"
    assert any("market-stress" in item for item in snapshot.emphasis)
    assert any("Inflation is accelerating" in item for item in snapshot.emphasis)


def test_market_regime_keeps_partial_data_failure_visible() -> None:
    def unavailable(_series_id: str) -> list[Observation]:
        raise ConnectionError("offline")

    snapshot = build_market_regime(fred_loader=unavailable, vix_loader=lambda: [])

    assert snapshot.regime == "Regime incomplete"
    assert all(indicator.status == "unavailable" for indicator in snapshot.indicators)
    assert all(indicator.latest == "Unavailable" for indicator in snapshot.indicators)
    assert "FINRA margin debt" in snapshot.missing_evidence[0]


def test_macro_regimes_produce_explicit_research_weights() -> None:
    assert macro_default_policy("Easing and expanding liquidity").weights.percentages() == {
        "growth": 45,
        "profitability": 20,
        "valuation": 15,
        "balance_sheet": 20,
    }
    assert macro_default_policy("Mixed liquidity regime").weights.percentages() == {
        "growth": 30,
        "profitability": 30,
        "valuation": 20,
        "balance_sheet": 20,
    }
    assert macro_default_policy("Tightening and contracting liquidity").weights.percentages() == {
        "growth": 15,
        "profitability": 30,
        "valuation": 25,
        "balance_sheet": 30,
    }


def test_research_weights_reject_incomplete_or_unbalanced_values() -> None:
    with pytest.raises(ValueError, match="require"):
        ResearchWeights.from_mapping({"growth": 1.0})
    with pytest.raises(ValueError, match="100%"):
        ResearchWeights.from_percentages(
            {"growth": 30, "profitability": 30, "valuation": 30, "balance_sheet": 30}
        )
    with pytest.raises(ValueError, match="source"):
        MacroResearchPolicy(
            "Mixed liquidity regime",
            ResearchWeights(0.25, 0.25, 0.25, 0.25),
            source="generated",
        )
