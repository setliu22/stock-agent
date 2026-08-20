"""Deterministic market-regime indicators with explicit data provenance."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import math
from typing import Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


FRED_SERIES = {
    "fed_funds": "DFF",
    "fed_balance_sheet": "WALCL",
    "cpi": "CPIAUCSL",
    "high_yield_spread": "BAMLH0A0HYM2",
}

RESEARCH_WEIGHT_KEYS = ("growth", "profitability", "valuation", "balance_sheet")


@dataclass(frozen=True)
class ResearchWeights:
    growth: float
    profitability: float
    valuation: float
    balance_sheet: float

    def __post_init__(self) -> None:
        values = self.as_dict()
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("Research weights must be finite and nonnegative.")
        if not math.isclose(sum(values.values()), 1.0, abs_tol=1e-6):
            raise ValueError("Research weights must add up to 100%.")

    @classmethod
    def from_mapping(cls, values: dict[str, float]) -> "ResearchWeights":
        if set(values) != set(RESEARCH_WEIGHT_KEYS):
            raise ValueError(
                "Research weights require growth, profitability, valuation, and balance_sheet."
            )
        return cls(**{key: float(values[key]) for key in RESEARCH_WEIGHT_KEYS})

    @classmethod
    def from_percentages(cls, values: dict[str, float]) -> "ResearchWeights":
        return cls.from_mapping({key: float(value) / 100 for key, value in values.items()})

    def as_dict(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in RESEARCH_WEIGHT_KEYS}

    def percentages(self) -> dict[str, int]:
        return {key: round(value * 100) for key, value in self.as_dict().items()}


@dataclass(frozen=True)
class MacroResearchPolicy:
    regime: str
    weights: ResearchWeights
    source: str = "macro_defaults"

    def __post_init__(self) -> None:
        if not self.regime.strip():
            raise ValueError("A macro research policy requires a regime label.")
        if self.source not in {"macro_defaults", "custom"}:
            raise ValueError("Unknown macro research policy source.")

    def with_weights(self, weights: ResearchWeights) -> "MacroResearchPolicy":
        return MacroResearchPolicy(self.regime, weights, source="custom")

    def instruction_text(self) -> str:
        percentages = self.weights.percentages()
        labels = {
            "growth": "forward revenue and EPS growth",
            "profitability": "margins and returns on capital",
            "valuation": "forward valuation relative to peers",
            "balance_sheet": "cash flow, cash, and debt resilience",
        }
        weights = " | ".join(
            f"{key.replace('_', ' ').title()} {percentages[key]}%"
            for key in RESEARCH_WEIGHT_KEYS
        )
        highest_value = max(percentages.values())
        priorities = [labels[key] for key, value in percentages.items() if value == highest_value]
        priority_text = ", ".join(priorities[:-1])
        if len(priorities) > 1:
            priority_text += f" and {priorities[-1]}"
        else:
            priority_text = priorities[0]
        return (
            f"Current regime: {self.regime}. Deterministic research weights: {weights}. "
            f"Give the most emphasis to {priority_text}. Treat the score as shortlist priority, "
            "not a return forecast or buy/sell recommendation. Use only validated retrieved data, "
            "show missing factor coverage, and do not invent unavailable metrics."
        )

    def focus_summary(self) -> str:
        percentages = self.weights.percentages()
        highest = max(percentages.values())
        priorities = [
            key.replace("_", " ").title()
            for key, value in percentages.items()
            if value == highest
        ]
        if len(priorities) == 1:
            focus = priorities[0]
        else:
            focus = ", ".join(priorities[:-1]) + f" and {priorities[-1]}"
        return f"Applied automatically to stock research. Highest priority: {focus}."


def macro_default_policy(regime: str) -> MacroResearchPolicy:
    if regime == "Easing and expanding liquidity":
        weights = ResearchWeights(0.45, 0.20, 0.15, 0.20)
    elif regime == "Tightening and contracting liquidity":
        weights = ResearchWeights(0.15, 0.30, 0.25, 0.30)
    elif regime == "Mixed liquidity regime":
        weights = ResearchWeights(0.30, 0.30, 0.20, 0.20)
    else:
        weights = ResearchWeights(0.25, 0.25, 0.25, 0.25)
    return MacroResearchPolicy(regime=regime, weights=weights)


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
    meaning: str = "No interpretation available."
    level_context: str = "Not assessed"
    level_percentile: int | None = None
    status: str = "available"


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    regime: str
    summary: str
    emphasis: tuple[str, ...]
    indicators: tuple[RegimeIndicator, ...]
    missing_evidence: tuple[str, ...]
    generated_at: datetime
    company_fit: str = "Wait for a complete regime before applying a stock-profile tilt."

    @property
    def research_policy(self) -> MacroResearchPolicy:
        return macro_default_policy(self.regime)

    def to_text(self) -> str:
        lines = [
            self.regime,
            self.summary,
            f"Stock profile to prioritize: {self.company_fit}",
            "",
            "What to emphasize:",
        ]
        lines.extend(f"- {item}" for item in self.emphasis)
        lines.extend(("", "Indicators:"))
        lines.extend(
            f"- {item.label}: {item.latest}; {item.trend}; {item.meaning} "
            f"(as of {item.as_of}, {item.source})"
            for item in self.indicators
        )
        if self.missing_evidence:
            lines.extend(("", "Not yet measured:"))
            lines.extend(f"- {item}" for item in self.missing_evidence)
        weights = self.research_policy.weights.percentages()
        lines.extend(
            (
                "",
                "Default research weights: "
                + " | ".join(
                    f"{key.replace('_', ' ').title()} {weights[key]}%"
                    for key in RESEARCH_WEIGHT_KEYS
                ),
            )
        )
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
    history = yf.Ticker("^VIX").history(period="5y", interval="1d", auto_adjust=False)
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

    regime, summary, emphasis, company_fit = _interpret(states, indicators)
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
        company_fit=company_fit,
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
        meaning="No conclusion because current data is unavailable.",
        level_context="Unavailable",
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
    level_context, level_percentile = _historical_level_context(key, observations, latest)
    period = _elapsed_period(latest.as_of, previous.as_of)
    return RegimeIndicator(
        key=key,
        label=label,
        latest=f"{latest.value:,.2f}{unit}",
        trend=f"{word} ({change:+.2f} {change_unit} over {period})",
        as_of=latest.as_of.isoformat(),
        source=source,
        meaning=_indicator_meaning(key, direction, level_percentile),
        level_context=level_context,
        level_percentile=level_percentile,
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
    period = _elapsed_period(latest.as_of, previous.as_of)
    return RegimeIndicator(
        key=key,
        label=label,
        latest=f"${latest.value / 1_000_000:,.2f}T",
        trend=f"{word} ({change:+.2f}% over {period})",
        as_of=latest.as_of.isoformat(),
        source=source,
        meaning=_indicator_meaning(key, direction),
        level_context="Absolute size contextual; use 91-day trend.",
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
    yoy_history = [
        Observation(
            as_of=observations[index].as_of,
            value=(observations[index].value / observations[index - 12].value - 1) * 100,
        )
        for index in range(12, len(observations))
        if observations[index - 12].value != 0
    ]
    level_context, level_percentile = _historical_level_context(
        key, yoy_history, yoy_history[-1]
    )
    return RegimeIndicator(
        key=key,
        label=label,
        latest=f"{latest_yoy:.2f}% YoY",
        trend=f"{word} ({change:+.2f} pp over 1 month)",
        as_of=latest.as_of.isoformat(),
        source=source,
        meaning=_indicator_meaning(key, direction, level_percentile),
        level_context=level_context,
        level_percentile=level_percentile,
    )


def _elapsed_period(latest: date, previous: date) -> str:
    days = max(1, (latest - previous).days)
    if days >= 27:
        months = max(1, round(days / 30))
        return f"{months} month{'s' if months != 1 else ''}"
    return f"{days} day{'s' if days != 1 else ''}"


def _indicator_meaning(
    key: str,
    direction: int,
    level_percentile: int | None = None,
) -> str:
    messages = {
        "fed_funds": {
            1: "Higher rates favor profitable, lower-debt companies.",
            0: "Rate pressure is unchanged; the current level still matters.",
            -1: "Lower rates can support growth-stock valuations.",
        },
        "fed_balance_sheet": {
            1: "More market liquidity can support faster-growing companies.",
            0: "Fed liquidity is unchanged; company quality matters more.",
            -1: "Less liquidity favors profits, cash flow, and resilience.",
        },
        "cpi": {
            1: "Faster inflation can keep interest rates higher.",
            0: "Inflation momentum is unchanged; its level still matters.",
            -1: "Slower inflation gives the Fed more room to lower rates.",
        },
        "high_yield_spread": {
            1: "Rising borrowing risk makes high debt less attractive.",
            0: "Corporate borrowing stress is little changed.",
            -1: "Corporate borrowing stress is easing.",
        },
        "vix": {
            1: "Rising market fear implies larger near-term price swings.",
            0: "Near-term market fear is little changed.",
            -1: "Market fear is easing, but that alone is not a buy signal.",
        },
    }
    message = messages.get(key, {}).get(direction, "Use this as supporting evidence, not a signal by itself.")
    if key == "fed_funds" and level_percentile is not None and level_percentile >= 75:
        message += " Rates remain high versus five-year history."
    if key == "cpi" and level_percentile is not None and level_percentile >= 75:
        message += " Inflation remains high versus five-year history."
    return message


def _historical_level_context(
    key: str,
    observations: list[Observation],
    latest: Observation,
) -> tuple[str, int | None]:
    """Describe the current level relative to up to five years of its own history."""
    cutoff = latest.as_of - timedelta(days=365 * 5)
    history = [item.value for item in observations if item.as_of >= cutoff]
    if len(history) < 12:
        return "Insufficient history for level context.", None
    below = sum(value < latest.value for value in history)
    equal = sum(value == latest.value for value in history)
    percentile = round(100 * (below + equal / 2) / len(history))
    if percentile <= 25:
        band = "Low"
    elif percentile <= 75:
        band = "Typical"
    elif percentile <= 90:
        band = "Elevated"
    else:
        band = "Extreme"
    subject = {
        "fed_funds": "rate",
        "cpi": "inflation",
        "high_yield_spread": "credit stress",
        "vix": "volatility",
    }.get(key, "level")
    return (
        f"{band} {subject} ({percentile}th pct, 5Y).",
        percentile,
    )


def _interpret(
    states: dict[str, int | None],
    indicators: list[RegimeIndicator],
) -> tuple[str, str, tuple[str, ...], str]:
    rate = states.get("fed_funds")
    balance = states.get("fed_balance_sheet")
    if rate is None or balance is None:
        regime = "Regime incomplete"
        summary = "Rate or Federal Reserve balance-sheet data is missing, so the app cannot choose a market environment yet."
        emphasis = ("Refresh the missing data before changing what you look for in a stock.",)
        company_fit = "No company type yet; wait for both Federal Reserve signals."
    elif rate < 0 and balance > 0:
        regime = "Easing and expanding liquidity"
        summary = "Interest rates are falling and the Federal Reserve is adding liquidity. This is the most supportive environment for growth stocks."
        emphasis = (
            "Give more weight to companies growing revenue and earnings quickly.",
            "Still check cash flow and debt; easier money does not fix a weak business.",
        )
        company_fit = (
            "Faster-growing companies, preferably with improving profits and manageable debt."
        )
    elif rate > 0 and balance < 0:
        regime = "Tightening and contracting liquidity"
        summary = "Interest rates are rising and the Federal Reserve is removing liquidity. Expensive or debt-dependent growth stocks face more pressure."
        emphasis = (
            "Give more weight to current profits, cash flow, low debt, and a reasonable stock price.",
            "Give less weight to companies that need cheap financing to survive or justify their valuation.",
        )
        company_fit = (
            "Profitable, cash-generating, lower-debt companies trading at reasonable valuations."
        )
    else:
        regime = "Mixed liquidity regime"
        summary = "Rates and Federal Reserve liquidity do not point the same way. Neither aggressive growth nor pure defense has a clear macro advantage."
        emphasis = (
            "Look for companies that have both forward growth and current profitability.",
            "Avoid high debt and do not pay a high valuation unless expected growth supports it.",
        )
        company_fit = (
            "Profitable growth companies with manageable debt and valuations supported by expected earnings."
        )

    stress = [states.get("high_yield_spread"), states.get("vix")]
    if any(value == 1 for value in stress):
        emphasis += ("Market stress is rising. Be stricter about debt and expect larger price swings.",)
    if states.get("cpi") == 1:
        emphasis += ("Inflation is speeding up, which can keep interest rates higher for longer.",)
    percentiles = {
        indicator.key: indicator.level_percentile
        for indicator in indicators
        if indicator.level_percentile is not None
    }
    elevated_stress = any(
        percentiles.get(key, 0) >= 75 for key in ("high_yield_spread", "vix")
    )
    if elevated_stress and not any(value == 1 for value in stress):
        emphasis += (
            "Market stress is still high versus recent history even though it is not getting worse.",
        )
    if percentiles.get("cpi", 0) >= 75 and states.get("cpi") != 1:
        emphasis += (
            "Inflation is still high versus the last five years even though it is not speeding up.",
        )
    if percentiles.get("fed_funds", 0) >= 75 and states.get("fed_funds") <= 0:
        emphasis += (
            "Interest rates are still high versus the last five years, even if they are stable or falling.",
        )
        company_fit += " Continue checking debt and refinancing needs."
    return regime, summary, emphasis, company_fit
