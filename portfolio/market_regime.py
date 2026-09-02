"""Deterministic market-regime indicators with explicit data provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable


FRED_SERIES = {
    "fed_funds": "DFF",
    "fed_balance_sheet": "WALCL",
    "cpi": "CPIAUCNS",
    "high_yield_spread": "BAMLH0A0HYM2",
}

DEFENSIVE_MACRO_TILT = "Safer / profitable / low-leverage"
NEUTRAL_MACRO_TILT = "Neutral"
TOLERANT_MACRO_TILT = "More tolerant of high-growth / high-leverage"

MACRO_REFERENCE_ROWS = (
    (
        "Fed funds rate",
        "High or rising",
        "Profitable, low-leverage companies (less dependent on distant future profits or expensive refinancing)",
        "Higher rates mean investors can earn more risk-free, so they are less willing to pay as much today for profits that arrive far in the future. This especially hurts high-growth companies because more of their value comes from distant future profits. Higher rates also make bank loans and corporate bonds more expensive, so high-leverage companies pay more interest when they refinance debt. That reduces expected profits and equity value. Both mechanisms can lower stock prices.",
    ),
    (
        "Fed assets / balance sheet",
        "Falling",
        "Profitable, low-leverage companies (less dependent on distant future profits or expensive refinancing)",
        "The Fed owns a large amount of Treasury bonds. Normally, when those bonds mature, the Fed can use the repayment to buy replacement Treasuries. When the Fed shrinks its balance sheet, it stops replacing some of the bonds that mature. As a result, a larger share of Treasury debt must be held by investors other than the Fed. If investors will not hold that additional amount at existing prices, Treasury prices fall (and Treasury yields increase) until the higher yields make them attractive enough to buy. Higher Treasury yields then hurt high-growth companies because investors can earn more risk-free, so distant future profits are worth less today. They hurt high-leverage companies because corporate borrowing and refinancing rates also tend to rise, increasing interest expense and reducing expected profits. Both effects can reduce equity value and stock price.",
    ),
    (
        "High-yield credit spread",
        "High or rising",
        "Profitable, low-leverage companies (less debt exposed to expensive refinancing)",
        "A high-yield spread is the extra interest rate risky companies must pay above the Treasury rate. For example, if Treasuries yield 4% and a company has a 4% credit spread, investors will demand roughly 8% to lend to it. If the spread rises, refinancing becomes more expensive even if Treasury rates do not change. Companies with lots of debt experience a larger increase in total interest expense, reducing expected profits and cash available to shareholders. That lowers equity value and stock price.",
    ),
    (
        "VIX",
        "High or rising sharply",
        "Profitable, stable companies (less uncertainty about future profits)",
        "The VIX is mainly a signal of how much volatility investors expect, rather than something that mechanically causes stock prices to fall. When uncertainty is high, investors generally require a higher expected return to take stock-market risk. If the company’s expected future profits are unchanged, investors must pay a lower price today to earn that higher expected return. That means lower equity value and stock price, particularly for companies investors consider risky or uncertain.",
    ),
    (
        "CPI inflation",
        "High or accelerating",
        "Profitable companies with pricing power (can raise prices enough to offset higher costs)",
        "Inflation can raise wages, materials, transportation, and other costs. If a company cannot raise the prices it charges customers enough to compensate, its profit margins shrink. Lower expected future profits mean lower equity value and therefore a lower stock price. High inflation can also cause interest rates to stay higher because the Fed may keep borrowing costs elevated to slow spending and investment, reduce demand in the economy, and bring inflation back down. Higher rates then create the additional high-growth and high-leverage effects described above.",
    ),
)

@dataclass(frozen=True)
class MacroResearchPolicy:
    regime: str
    rules: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.regime.strip():
            raise ValueError("A macro research policy requires a regime label.")

    def instruction_text(self) -> str:
        return (
            f"Current market setting: {self.regime}. Research rules: "
            + " ".join(self.rules)
            + " Use retrieved data only, show missing data, and do not make up metrics."
        )

    def focus_summary(self) -> str:
        return "Applied automatically: check valuation first, then the market setting."


def macro_default_policy(regime: str) -> MacroResearchPolicy:
    if regime == "Easing and expanding liquidity":
        rules = (
            "Require peer-relative valuation evidence.",
            "Prefer above-median forward growth.",
            "Show weak profitability or financial resilience as cautions, not hidden penalties.",
        )
    elif regime == "Tightening and contracting liquidity":
        rules = (
            "Require peer-relative valuation evidence.",
            "Prefer above-median profitability and financial resilience.",
            "Flag weak cash flow or debt resilience as a macro caution.",
        )
    elif regime == "Mixed liquidity regime":
        rules = (
            "Require peer-relative valuation evidence.",
            "Prefer companies with above-median growth and profitability.",
            "Flag weak financial resilience as a macro caution.",
        )
    elif regime == "Neutral liquidity regime":
        rules = (
            "Require peer-relative valuation evidence.",
            "Do not apply a growth or defensive macro preference while both core liquidity signals are stable.",
        )
    else:
        rules = (
            "Rank by peer-relative valuation evidence.",
            "Do not apply a macro preference until the regime is complete.",
        )
    return MacroResearchPolicy(regime=regime, rules=rules)


@dataclass(frozen=True)
class Observation:
    as_of: date
    value: float
    observed_at: datetime | None = None


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
    macro_tilt: str = "Cannot assess"


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
            f"- {item.label}: {item.latest}; {item.trend}; macro tilt {item.macro_tilt}; {item.meaning} "
            f"(as of {item.as_of}, {item.source})"
            for item in self.indicators
        )
        if self.missing_evidence:
            lines.extend(("", "Not yet measured:"))
            lines.extend(f"- {item}" for item in self.missing_evidence)
        lines.extend(("", "Research rules:"))
        lines.extend(f"- {rule}" for rule in self.research_policy.rules)
        lines.extend(("", "This is a market-condition checklist, not a buy or sell signal."))
        return "\n".join(lines)


SeriesLoader = Callable[[str], list[Observation]]


def fetch_fred_series(series_id: str) -> list[Observation]:
    """Fetch recent public FRED observations through pandas-datareader."""
    try:
        from pandas_datareader import data as web
    except Exception as exc:
        raise RuntimeError("pandas-datareader is not installed.") from exc
    start = date.today() - timedelta(days=365 * 6)
    frame = web.DataReader(series_id, "fred", start=start)
    observations = [
        Observation(as_of=timestamp.date(), value=float(value))
        for timestamp, value in frame[series_id].dropna().items()
    ]
    if not observations:
        raise RuntimeError(f"FRED returned no observations for {series_id}.")
    return observations


def fetch_vix_series() -> list[Observation]:
    """Fetch VIX history and the latest timestamped intraday value with yfinance."""
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance is not installed.") from exc
    ticker = yf.Ticker("^VIX")
    history = ticker.history(period="5y", interval="1d", auto_adjust=False)
    if history is None or history.empty or "Close" not in history.columns:
        raise RuntimeError("Yahoo Finance returned no VIX history.")
    observations = [
        Observation(as_of=timestamp.date(), value=float(value))
        for timestamp, value in history["Close"].dropna().items()
    ]
    try:
        intraday = ticker.history(period="1d", interval="1m", auto_adjust=False)
        latest = intraday["Close"].dropna()
        if not latest.empty:
            timestamp = latest.index[-1].to_pydatetime()
            observations = [item for item in observations if item.as_of != timestamp.date()]
            observations.append(
                Observation(
                    as_of=timestamp.date(),
                    value=float(latest.iloc[-1]),
                    observed_at=timestamp,
                )
            )
    except Exception:
        pass
    return observations


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


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _historical_change(
    observations: list[Observation],
    lookback_days: int,
    *,
    percent: bool = False,
) -> tuple[Observation, Observation, float, int] | None:
    """Classify the latest change against rolling changes from the prior five years."""
    changes: list[tuple[Observation, Observation, float]] = []
    cutoff = observations[-1].as_of - timedelta(days=365 * 5) if observations else date.min
    prior_index = -1
    for index, current in enumerate(observations):
        if current.as_of < cutoff:
            continue
        target = current.as_of - timedelta(days=lookback_days)
        while (
            prior_index + 1 < index
            and observations[prior_index + 1].as_of <= target
        ):
            prior_index += 1
        if prior_index < 0:
            continue
        prior = observations[prior_index]
        if percent and prior.value == 0:
            continue
        change = (
            (current.value / prior.value - 1) * 100
            if percent
            else current.value - prior.value
        )
        changes.append((current, prior, change))
    if len(changes) < 4:
        return None
    current, prior, latest_change = changes[-1]
    distribution = [change for _current, _prior_item, change in changes]
    lower_quartile = _percentile(distribution, 0.25)
    upper_quartile = _percentile(distribution, 0.75)
    if latest_change < 0 and latest_change <= lower_quartile:
        direction = -1
    elif latest_change > 0 and latest_change >= upper_quartile:
        direction = 1
    else:
        direction = 0
    return current, prior, latest_change, direction


def _historical_monthly_change(
    observations: list[Observation],
) -> tuple[Observation, Observation, float, int] | None:
    """Classify the latest month-to-month change against five years of such changes."""
    if len(observations) < 5:
        return None
    cutoff = observations[-1].as_of - timedelta(days=365 * 5)
    changes = [
        (current, previous, current.value - previous.value)
        for previous, current in zip(observations, observations[1:])
        if current.as_of >= cutoff and 20 <= (current.as_of - previous.as_of).days <= 45
    ]
    if len(changes) < 4:
        return None
    current, previous, latest_change = changes[-1]
    distribution = [change for _current, _previous, change in changes]
    lower_quartile = _percentile(distribution, 0.25)
    upper_quartile = _percentile(distribution, 0.75)
    if latest_change < 0 and latest_change <= lower_quartile:
        direction = -1
    elif latest_change > 0 and latest_change >= upper_quartile:
        direction = 1
    else:
        direction = 0
    return current, previous, latest_change, direction


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
        macro_tilt="Cannot assess",
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
    states: dict[str, int | None],
) -> RegimeIndicator:
    trend = _historical_change(observations, lookback_days)
    if trend is None:
        return _unavailable(key, label, source, states)
    latest, previous, change, direction = trend
    states[key] = direction
    display_direction = (change > 0) - (change < 0)
    word = {1: "Rising", 0: "Unchanged", -1: "Falling"}[display_direction]
    level_context, level_percentile = _historical_level_context(key, observations, latest)
    period = _elapsed_period(latest.as_of, previous.as_of)
    return RegimeIndicator(
        key=key,
        label=label,
        latest=f"{latest.value:,.2f}{unit}",
        trend=f"{word} ({change:+.2f} {change_unit} over {period})",
        as_of=_observation_time(latest),
        source=source,
        meaning=_indicator_meaning(key, direction, level_percentile),
        level_context=level_context,
        level_percentile=level_percentile,
        macro_tilt=_macro_tilt(key, direction, level_percentile),
    )


def _percent_change_indicator(
    key: str,
    label: str,
    observations: list[Observation],
    *,
    lookback_days: int,
    source: str,
    states: dict[str, int | None],
) -> RegimeIndicator:
    trend = _historical_change(observations, lookback_days, percent=True)
    if trend is None:
        return _unavailable(key, label, source, states)
    latest, previous, change, direction = trend
    states[key] = direction
    display_direction = (change > 0) - (change < 0)
    word = {1: "Expanding", 0: "Unchanged", -1: "Contracting"}[display_direction]
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
        macro_tilt=_macro_tilt(key, direction),
    )


def _cpi_indicator(observations: list[Observation], states: dict[str, int | None]) -> RegimeIndicator:
    key = "cpi"
    label = "Consumer Price Index inflation"
    source = "FRED CPIAUCNS (headline CPI-U)"
    by_month = {(item.as_of.year, item.as_of.month): item for item in observations}
    yoy_history: list[Observation] = []
    for current in observations:
        prior = by_month.get((current.as_of.year - 1, current.as_of.month))
        if prior is None or prior.value == 0:
            continue
        yoy_history.append(
            Observation(
                as_of=current.as_of,
                value=(current.value / prior.value - 1) * 100,
            )
        )
    if len(yoy_history) < 2:
        return _unavailable(key, label, source, states)
    trend = _historical_monthly_change(yoy_history)
    if trend is None:
        return _unavailable(key, label, source, states)
    latest, previous, change, direction = trend
    latest_yoy = latest.value
    previous_yoy = previous.value
    display_change = round(latest_yoy, 1) - round(previous_yoy, 1)
    states[key] = direction
    display_direction = (change > 0) - (change < 0)
    word = {1: "Rising", 0: "Unchanged", -1: "Falling"}[display_direction]
    level_context, level_percentile = _historical_level_context(
        key, yoy_history, yoy_history[-1]
    )
    return RegimeIndicator(
        key=key,
        label=label,
        latest=f"{latest_yoy:.1f}% YoY",
        trend=f"{word} ({display_change:+.1f} pp over {_elapsed_period(latest.as_of, previous.as_of)})",
        as_of=latest.as_of.isoformat(),
        source=source,
        meaning=_indicator_meaning(key, direction, level_percentile),
        level_context=level_context,
        level_percentile=level_percentile,
        macro_tilt=_macro_tilt(key, direction, level_percentile),
    )


def _observation_time(observation: Observation) -> str:
    return (
        observation.observed_at.isoformat()
        if observation.observed_at is not None
        else observation.as_of.isoformat()
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
            1: "Higher rates favor profitable, low-leverage companies.",
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
            1: "Rising borrowing risk makes high leverage less attractive.",
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


def _macro_tilt(
    key: str,
    direction: int,
    level_percentile: int | None = None,
) -> str:
    """Translate a macro observation into a defensive, neutral, or tolerant tilt."""
    elevated = level_percentile is not None and level_percentile >= 75
    low = level_percentile is not None and level_percentile <= 25
    if key == "fed_funds":
        if elevated:
            return DEFENSIVE_MACRO_TILT
        if low:
            return TOLERANT_MACRO_TILT
    elif key == "fed_balance_sheet":
        if direction < 0:
            return DEFENSIVE_MACRO_TILT
        if direction > 0:
            return TOLERANT_MACRO_TILT
    elif key in {"high_yield_spread", "vix"}:
        if elevated:
            return DEFENSIVE_MACRO_TILT
        if low:
            return TOLERANT_MACRO_TILT
    elif key == "cpi":
        if elevated or direction > 0:
            return DEFENSIVE_MACRO_TILT
        if low and direction < 0:
            return TOLERANT_MACRO_TILT
    return NEUTRAL_MACRO_TILT


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
    suffix = "th" if 10 <= percentile % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(percentile % 10, "th")
    return (f"{band} {subject} ({percentile}{suffix} pct, 5Y).", percentile)


def _interpret(
    states: dict[str, int | None],
    indicators: list[RegimeIndicator],
) -> tuple[str, str, tuple[str, ...], str]:
    indicator_tilts = {indicator.key: indicator.macro_tilt for indicator in indicators}
    rate_tilt = indicator_tilts.get("fed_funds")
    balance_tilt = indicator_tilts.get("fed_balance_sheet")
    valid_tilts = {DEFENSIVE_MACRO_TILT, NEUTRAL_MACRO_TILT, TOLERANT_MACRO_TILT}
    if rate_tilt not in valid_tilts or balance_tilt not in valid_tilts:
        regime = "Regime incomplete"
        summary = "Some rate or Fed data is missing, so the app cannot set a market view yet."
        emphasis = ("Refresh the missing data before changing your stock search.",)
        company_fit = "No stock preference yet; wait for both core Fed signals."
    elif rate_tilt == TOLERANT_MACRO_TILT and balance_tilt == TOLERANT_MACRO_TILT:
        regime = "Easing and expanding liquidity"
        summary = "Rates are falling and the Fed is adding liquidity. This usually helps growth stocks most."
        emphasis = (
            "Focus on companies growing revenue and earnings quickly.",
            "Still check cash flow and debt; easier money does not fix a weak business.",
        )
        company_fit = (
            "Faster-growing companies, preferably with improving profits and manageable leverage."
        )
    elif rate_tilt == DEFENSIVE_MACRO_TILT and balance_tilt == DEFENSIVE_MACRO_TILT:
        regime = "Tightening and contracting liquidity"
        summary = "Rates are rising and the Fed is removing liquidity. Expensive or high-leverage growth stocks face more pressure."
        emphasis = (
            "Focus on current profits, cash flow, low leverage, and a reasonable stock price.",
            "Be cautious with companies that need cheap financing to survive or justify their valuation.",
        )
        company_fit = (
            "Profitable, cash-generating, low-leverage companies trading at reasonable valuations."
        )
    elif rate_tilt == NEUTRAL_MACRO_TILT and balance_tilt == NEUTRAL_MACRO_TILT:
        regime = "Neutral liquidity regime"
        summary = "Rates and the Fed balance sheet are broadly stable. The market data does not favor growth or defense."
        emphasis = (
            "Use company fundamentals and peer-relative valuation as the primary filters.",
            "Treat inflation, credit spreads, and volatility as cautions rather than automatic stock-selection rules.",
        )
        company_fit = "No broad macro preference; prioritize company quality and valuation."
    else:
        regime = "Mixed liquidity regime"
        summary = "Rates and Fed liquidity point in different directions. Neither growth nor defensive stocks has a clear advantage."
        emphasis = (
            "Look for companies that have both forward growth and current profitability.",
            "Avoid high leverage, and do not pay a high valuation unless expected growth supports it.",
        )
        company_fit = (
            "Profitable growth companies with manageable leverage and valuations supported by expected earnings."
        )

    stress = [states.get("high_yield_spread"), states.get("vix")]
    if any(value == 1 for value in stress):
        emphasis += ("Market stress is rising. Be stricter about leverage and refinancing risk, and expect larger price swings.",)
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
        company_fit += " Continue checking leverage and refinancing needs."
    return regime, summary, emphasis, company_fit
