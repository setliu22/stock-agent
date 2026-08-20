from __future__ import annotations

from datetime import date, timedelta

from portfolio.market_regime import (
    FRED_SERIES,
    Observation,
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
    assert snapshot.indicators[0].level_context != "Not assessed"
    assert snapshot.company_fit.startswith("Faster-growing companies")
    assert "over 3 months" in snapshot.indicators[0].trend
    assert "growth-stock valuations" in snapshot.indicators[0].meaning
    assert "Stock profile to prioritize" in snapshot.to_text()
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
    assert any("Market stress is rising" in item for item in snapshot.emphasis)
    assert any("Inflation is speeding up" in item for item in snapshot.emphasis)


def test_market_regime_keeps_partial_data_failure_visible() -> None:
    def unavailable(_series_id: str) -> list[Observation]:
        raise ConnectionError("offline")

    snapshot = build_market_regime(fred_loader=unavailable, vix_loader=lambda: [])

    assert snapshot.regime == "Regime incomplete"
    assert all(indicator.status == "unavailable" for indicator in snapshot.indicators)
    assert all(indicator.latest == "Unavailable" for indicator in snapshot.indicators)
    assert "FINRA margin debt" in snapshot.missing_evidence[0]


def test_stable_trend_does_not_hide_an_elevated_rate_level() -> None:
    first = date(2021, 1, 1)
    long_rate_history = [
        Observation(first + timedelta(days=index), 1.0 if index < 1800 else 5.0)
        for index in range(2001)
    ]
    stable_history = [
        Observation(first + timedelta(days=index), 7_000_000.0)
        for index in range(2001)
    ]
    credit_history = [
        Observation(first + timedelta(days=index), 3.0)
        for index in range(2001)
    ]
    series = {
        FRED_SERIES["fed_funds"]: long_rate_history,
        FRED_SERIES["fed_balance_sheet"]: stable_history,
        FRED_SERIES["cpi"]: _monthly(
            [300, 301, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 312, 313]
        ),
        FRED_SERIES["high_yield_spread"]: credit_history,
    }

    snapshot = build_market_regime(
        fred_loader=lambda series_id: series[series_id],
        vix_loader=lambda: [
            Observation(first + timedelta(days=index), 18.0)
            for index in range(2001)
        ],
    )

    rate = next(item for item in snapshot.indicators if item.key == "fed_funds")
    assert rate.trend.startswith("Stable")
    assert rate.level_context.startswith("Extreme rate")
    assert any("rates are still high" in item.casefold() for item in snapshot.emphasis)
    assert "refinancing needs" in snapshot.company_fit
    assert "Rates remain high" in rate.meaning


def test_macro_regimes_produce_explicit_plain_language_rules() -> None:
    easing = macro_default_policy("Easing and expanding liquidity")
    tightening = macro_default_policy("Tightening and contracting liquidity")
    mixed = macro_default_policy("Mixed liquidity regime")

    assert any("growth" in rule for rule in easing.rules)
    assert any("profitability and financial resilience" in rule for rule in tightening.rules)
    assert any("growth and profitability" in rule for rule in mixed.rules)
    assert "%" not in easing.instruction_text()
