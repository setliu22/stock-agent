"""Deterministic market-regime indicators with explicit data provenance."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from typing import Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


FRED_SERIES = {
    "fed_funds": "DFF",
    "fed_balance_sheet": "WALCL",
    "cpi": "CPIAUCSL",
    "high_yield_spread": "BAMLH0A0HYM2",
}


@dataclass(frozen=True)
class Observation:
    as_of: date
    value: float


@dataclass(frozen=True)
class RegimeIndicator:
    key: str
    label: str
    latest: str
    trend: str
    as_of: str
    source: str
    status: str = "available"


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    regime: str
    summary: str
    emphasis: tuple[str, ...]
    indicators: tuple[RegimeIndicator, ...]
    missing_evidence: tuple[str, ...]
    generated_at: datetime

    def to_text(self) -> str:
        lines = [self.regime, self.summary, "", "What to emphasize:"]
        lines.extend(f"- {item}" for item in self.emphasis)
        lines.extend(("", "Indicators:"))
        lines.extend(
            f"- {item.label}: {item.latest}; {item.trend} "
            f"(as of {item.as_of}, {item.source})"
            for item in self.indicators
        )
        if self.missing_evidence:
            lines.extend(("", "Not yet measured:"))
            lines.extend(f"- {item}" for item in self.missing_evidence)
        lines.extend(("", "This is a market-condition checklist, not a buy or sell signal."))
        return "\n".join(lines)


SeriesLoader = Callable[[str], list[Observation]]


def fetch_fred_series(series_id: str, timeout: int = 12) -> list[Observation]:
    """Fetch a public FRED series without requiring credentials."""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urlencode({"id": series_id})
    request = Request(url, headers={"User-Agent": "stock-agent/1.0"})
    with urlopen(request, timeout=timeout) as response:
        content = response.read().decode("utf-8")
    observations: list[Observation] = []
    for row in csv.DictReader(StringIO(content)):
        raw_value = row.get(series_id)
        if not raw_value or raw_value == ".":
            continue
        raw_date = row.get("DATE") or row.get("observation_date")
        if not raw_date:
            continue
        observations.append(
            Observation(as_of=date.fromisoformat(raw_date), value=float(raw_value))
        )
    if not observations:
        raise RuntimeError(f"FRED returned no observations for {series_id}.")
    return observations


def fetch_vix_series() -> list[Observation]:
    """Fetch recent VIX closes through the application's market-data package."""
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance is not installed.") from exc
    history = yf.Ticker("^VIX").history(period="6mo", interval="1d", auto_adjust=False)
    if history is None or history.empty or "Close" not in history.columns:
        raise RuntimeError("Yahoo Finance returned no VIX history.")
    return [
        Observation(as_of=timestamp.date(), value=float(value))
        for timestamp, value in history["Close"].dropna().items()
    ]


def build_market_regime(
    fred_loader: SeriesLoader = fetch_fred_series,
    vix_loader: Callable[[], list[Observation]] = fetch_vix_series,
) -> MarketRegimeSnapshot:
    """Build a regime view from raw observations; never substitute LLM estimates."""
    states: dict[str, int | None] = {}
    indicators: list[RegimeIndicator] = []

    fed_funds = _load(fred_loader, FRED_SERIES["fed_funds"])
    indicators.append(
        _change_indicator(
            "fed_funds",
            "Effective federal funds rate",
            fed_funds,
            lookback_days=90,
            unit="%",
            change_unit="pp",
            source="FRED DFF",
            tolerance=0.05,
            states=states,
        )
    )
    balance_sheet = _load(fred_loader, FRED_SERIES["fed_balance_sheet"])
    indicators.append(
        _percent_change_indicator(
            "fed_balance_sheet",
            "Federal Reserve assets",
            balance_sheet,
            lookback_days=91,
            source="FRED WALCL",
            states=states,
            tolerance=0.5,
        )
    )
    cpi = _load(fred_loader, FRED_SERIES["cpi"])
    indicators.append(_cpi_indicator(cpi, states))
    credit = _load(fred_loader, FRED_SERIES["high_yield_spread"])
    indicators.append(
        _change_indicator(
            "high_yield_spread",
            "US high-yield option-adjusted spread",
            credit,
            lookback_days=90,
            unit="%",
            change_unit="pp",
            source="FRED BAMLH0A0HYM2",
            tolerance=0.05,
            states=states,
        )
    )
    try:
        vix = _clean(vix_loader())
    except Exception:
        vix = []
    indicators.append(
        _change_indicator(
            "vix",
            "CBOE Volatility Index",
            vix,
            lookback_days=30,
            unit="",
            change_unit="points",
            source="Yahoo Finance ^VIX",
            tolerance=0.5,
            states=states,
        )
    )

    regime, summary, emphasis = _interpret(states)
    return MarketRegimeSnapshot(
        regime=regime,
        summary=summary,
        emphasis=emphasis,
        indicators=tuple(indicators),
        missing_evidence=(
            "FINRA margin debt is not fetched automatically.",
            "Sector leadership and earnings breadth need a validated equity-data workflow.",
        ),
        generated_at=datetime.now(timezone.utc),
    )


def _load(loader: SeriesLoader, series_id: str) -> list[Observation]:
    try:
        return _clean(loader(series_id))
    except Exception:
        return []


def _clean(observations: Iterable[Observation]) -> list[Observation]:
    return sorted(observations, key=lambda item: item.as_of)


def _prior(observations: list[Observation], days: int) -> Observation | None:
    if not observations:
        return None
    target = observations[-1].as_of - timedelta(days=days)
    candidates = [item for item in observations[:-1] if item.as_of <= target]
    return candidates[-1] if candidates else None


def _direction(change: float, tolerance: float) -> int:
    if change > tolerance:
        return 1
    if change < -tolerance:
        return -1
    return 0


def _unavailable(key: str, label: str, source: str, states: dict[str, int | None]) -> RegimeIndicator:
    states[key] = None
    return RegimeIndicator(
        key=key,
        label=label,
        latest="Unavailable",
        trend="Could not refresh",
        as_of="—",
        source=source,
        status="unavailable",
    )


def _change_indicator(
    key: str,
    label: str,
    observations: list[Observation],
    *,
    lookback_days: int,
    unit: str,
    change_unit: str,
    source: str,
    tolerance: float,
    states: dict[str, int | None],
) -> RegimeIndicator:
    previous = _prior(observations, lookback_days)
    if not observations or previous is None:
        return _unavailable(key, label, source, states)
    latest = observations[-1]
    change = latest.value - previous.value
    direction = _direction(change, tolerance)
    states[key] = direction
    word = {1: "Rising", 0: "Stable", -1: "Falling"}[direction]
    return RegimeIndicator(
        key=key,
        label=label,
        latest=f"{latest.value:,.2f}{unit}",
        trend=f"{word} ({change:+.2f} {change_unit} vs. {previous.as_of.isoformat()})",
        as_of=latest.as_of.isoformat(),
        source=source,
    )


def _percent_change_indicator(
    key: str,
    label: str,
    observations: list[Observation],
    *,
    lookback_days: int,
    source: str,
    states: dict[str, int | None],
    tolerance: float,
) -> RegimeIndicator:
    previous = _prior(observations, lookback_days)
    if not observations or previous is None or previous.value == 0:
        return _unavailable(key, label, source, states)
    latest = observations[-1]
    change = (latest.value / previous.value - 1) * 100
    direction = _direction(change, tolerance)
    states[key] = direction
    word = {1: "Expanding", 0: "Stable", -1: "Contracting"}[direction]
    return RegimeIndicator(
        key=key,
        label=label,
        latest=f"${latest.value / 1_000_000:,.2f}T",
        trend=f"{word} ({change:+.2f}% vs. {previous.as_of.isoformat()})",
        as_of=latest.as_of.isoformat(),
        source=source,
    )


def _cpi_indicator(
    observations: list[Observation], states: dict[str, int | None]
) -> RegimeIndicator:
    key = "cpi"
    label = "Consumer Price Index inflation"
    source = "FRED CPIAUCSL"
    if len(observations) < 14 or observations[-13].value == 0 or observations[-14].value == 0:
        return _unavailable(key, label, source, states)
    latest_yoy = (observations[-1].value / observations[-13].value - 1) * 100
    previous_yoy = (observations[-2].value / observations[-14].value - 1) * 100
    change = latest_yoy - previous_yoy
    direction = _direction(change, 0.05)
    states[key] = direction
    word = {1: "Accelerating", 0: "Stable", -1: "Cooling"}[direction]
    latest = observations[-1]
    return RegimeIndicator(
        key=key,
        label=label,
        latest=f"{latest_yoy:.2f}% YoY",
        trend=f"{word} ({change:+.2f} pp vs. prior month)",
        as_of=latest.as_of.isoformat(),
        source=source,
    )


def _interpret(states: dict[str, int | None]) -> tuple[str, str, tuple[str, ...]]:
    rate = states.get("fed_funds")
    balance = states.get("fed_balance_sheet")
    if rate is None or balance is None:
        regime = "Regime incomplete"
        summary = "The two Federal Reserve inputs are not both available, so no quadrant was assigned."
        emphasis = ("Refresh the missing observations before using the framework.",)
    elif rate < 0 and balance > 0:
        regime = "Easing and expanding liquidity"
        summary = "Policy rates are falling while the Federal Reserve balance sheet is expanding."
        emphasis = (
            "Growth can receive more valuation support, but verify earnings quality and valuation.",
            "Check credit conditions before assuming refinancing will remain easy.",
        )
    elif rate > 0 and balance < 0:
        regime = "Tightening and contracting liquidity"
        summary = "Policy rates are rising while the Federal Reserve balance sheet is contracting."
        emphasis = (
            "Prioritize profitability, durable cash flow, and balance-sheet resilience.",
            "Apply more valuation discipline to highly leveraged or long-duration growth companies.",
        )
    else:
        regime = "Mixed liquidity regime"
        summary = "The Federal Reserve inputs are moving in different directions or are broadly stable."
        emphasis = (
            "Favor businesses that combine growth with profitability instead of relying on liquidity alone.",
            "Treat leverage and refinancing needs as company-specific risks.",
        )

    stress = [states.get("high_yield_spread"), states.get("vix")]
    if any(value == 1 for value in stress):
        emphasis += ("At least one market-stress measure is rising; inspect credit and volatility separately.",)
    if states.get("cpi") == 1:
        emphasis += ("Inflation is accelerating, which may constrain policy easing.",)
    return regime, summary, emphasis
