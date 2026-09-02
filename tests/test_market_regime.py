from __future__ import annotations

from datetime import date, timedelta

from portfolio.market_regime import (
    DEFENSIVE_MACRO_TILT,
    FRED_SERIES,
    MACRO_REFERENCE_ROWS,
    NEUTRAL_MACRO_TILT,
    Observation,
    TOLERANT_MACRO_TILT,
    _cpi_indicator,
    build_market_regime,
    macro_default_policy,
)


def _daily(start: float, end: float, days: int = 120) -> list[Observation]:
    first = date(2026, 1, 1)
    return [
        Observation(first + timedelta(days=index), start + (end - start) * index / days)
        for index in range(days + 1)
    ]


def _monthly_cpi(*, accelerating: bool = False) -> list[Observation]:
    observations: list[Observation] = []
    value = 280.0
    for index in range(79):
        year = 2020 + index // 12
        month = index % 12 + 1
        step = 1.0 if accelerating and index >= 67 else 0.6
        value += step
        observations.append(Observation(date(year, month, 1), value))
    return observations


def _late_move(start: float, end: float, days: int = 120) -> list[Observation]:
    first = date(2026, 1, 1)
    flat_days = days - 20
    return [
        Observation(
            first + timedelta(days=index),
            start if index <= flat_days else start + (end - start) * (index - flat_days) / 20,
        )
        for index in range(days + 1)
    ]


def test_market_regime_classifies_easing_and_expanding_liquidity() -> None:
    series = {
        FRED_SERIES["fed_funds"]: _daily(5.25, 4.25),
        FRED_SERIES["fed_balance_sheet"]: _late_move(7_000_000, 7_140_000),
        FRED_SERIES["cpi"]: _monthly_cpi(),
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
    assert len(snapshot.indicators) == 5
    assert snapshot.indicators[0].macro_tilt == TOLERANT_MACRO_TILT
    assert snapshot.indicators[2].macro_tilt == NEUTRAL_MACRO_TILT
    assert "Stock profile to prioritize" in snapshot.to_text()
    assert "not a buy or sell signal" in snapshot.to_text()


def test_market_regime_adds_inflation_and_stress_cautions() -> None:
    series = {
        FRED_SERIES["fed_funds"]: _daily(4.0, 5.0),
        FRED_SERIES["fed_balance_sheet"]: _late_move(7_200_000, 7_000_000),
        FRED_SERIES["cpi"]: _monthly_cpi(accelerating=True),
        FRED_SERIES["high_yield_spread"]: _daily(3.0, 4.5),
    }

    snapshot = build_market_regime(
        fred_loader=lambda series_id: series[series_id],
        vix_loader=lambda: _daily(15, 25),
    )

    assert snapshot.regime == "Tightening and contracting liquidity"
    assert all(
        indicator.macro_tilt == DEFENSIVE_MACRO_TILT
        for indicator in snapshot.indicators
    )
    assert any("Market stress is rising" in item for item in snapshot.emphasis)
    assert any("Inflation is speeding up" in item for item in snapshot.emphasis)


def test_market_regime_keeps_partial_data_failure_visible() -> None:
    def unavailable(_series_id: str) -> list[Observation]:
        raise ConnectionError("offline")

    snapshot = build_market_regime(fred_loader=unavailable, vix_loader=lambda: [])

    assert snapshot.regime == "Regime incomplete"
    assert all(indicator.status == "unavailable" for indicator in snapshot.indicators)
    assert all(indicator.latest == "Unavailable" for indicator in snapshot.indicators)
    assert all(indicator.macro_tilt == "Cannot assess" for indicator in snapshot.indicators)
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
        FRED_SERIES["cpi"]: _monthly_cpi(),
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
    assert rate.trend.startswith("Unchanged")
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


def test_macro_reference_covers_the_five_live_metrics() -> None:
    assert len(MACRO_REFERENCE_ROWS) == 5
    assert {row[0] for row in MACRO_REFERENCE_ROWS} == {
        "Fed funds rate",
        "Fed assets / balance sheet",
        "High-yield credit spread",
        "VIX",
        "CPI inflation",
    }
    assert [row[1] for row in MACRO_REFERENCE_ROWS] == [
        "High or rising",
        "Falling",
        "High or rising",
        "High or rising sharply",
        "High or accelerating",
    ]
    assert [row[2] for row in MACRO_REFERENCE_ROWS] == [
        "Profitable, low-leverage companies (less dependent on distant future profits or expensive refinancing)",
        "Profitable, low-leverage companies (less dependent on distant future profits or expensive refinancing)",
        "Profitable, low-leverage companies (less debt exposed to expensive refinancing)",
        "Profitable, stable companies (less uncertainty about future profits)",
        "Profitable companies with pricing power (can raise prices enough to offset higher costs)",
    ]
    reference_text = " ".join(value for row in MACRO_REFERENCE_ROWS for value in row).casefold()
    assert "investors can earn more risk-free" in reference_text
    assert "treasury prices fall" in reference_text
    assert "roughly 8%" in reference_text
    assert "mechanically causes stock prices to fall" in reference_text
    assert "pricing power" in reference_text


def test_cpi_year_over_year_uses_calendar_months_when_observations_are_missing() -> None:
    overrides = {
        date(2025, 6, 1): 320.0,
        date(2025, 7, 1): 323.0,
        date(2026, 6, 1): 331.2,
        date(2026, 7, 1): 333.982,
    }
    observations = [
        Observation(item.as_of, overrides.get(item.as_of, item.value))
        for item in _monthly_cpi()
        if item.as_of not in {date(2025, 10, 1), date(2025, 11, 1)}
    ]
    states: dict[str, int | None] = {}

    indicator = _cpi_indicator(observations, states)

    assert indicator.latest == "3.4% YoY"
    assert indicator.trend.startswith("Falling")
    assert indicator.source == "FRED CPIAUCNS (headline CPI-U)"


def test_flat_core_liquidity_signals_produce_neutral_regime() -> None:
    series = {
        FRED_SERIES["fed_funds"]: _daily(3.63, 3.63),
        FRED_SERIES["fed_balance_sheet"]: _daily(6_730_000, 6_759_955),
        FRED_SERIES["cpi"]: _monthly_cpi(),
        FRED_SERIES["high_yield_spread"]: _daily(2.8, 2.75),
    }
    snapshot = build_market_regime(
        fred_loader=lambda series_id: series[series_id],
        vix_loader=lambda: _daily(15.0, 15.1),
    )

    assert snapshot.regime == "Neutral liquidity regime"
    assert snapshot.indicators[0].macro_tilt == NEUTRAL_MACRO_TILT
    assert snapshot.indicators[1].macro_tilt == NEUTRAL_MACRO_TILT
