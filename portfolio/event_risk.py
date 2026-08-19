"""Evidence-driven position reviews for portfolio holdings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
import json
import math
from typing import Any, Callable, Iterable

import pandas as pd

from .market_regime import MarketRegimeSnapshot, build_market_regime
from .models import Holding


ProgressCallback = Callable[[int | None, str, str], None]
SECTION_ORDER = ("Company thesis", "Valuation", "Macro fit", "Event risk")


@dataclass(frozen=True)
class RiskSignal:
    category: str
    label: str
    detail: str
    points: int


@dataclass
class HoldingPositionRisk:
    ticker: str
    quantity: float
    average_cost: float
    rating: str
    score: int
    confidence: str
    sections: dict[str, list[str]] = field(default_factory=dict)
    signals: list[RiskSignal] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    upcoming_event: str | None = None
    event_date: str | None = None
    days_to_event: int | None = None

    @property
    def review_before_event(self) -> bool:
        return self.days_to_event is not None and self.days_to_event <= 30


@dataclass
class PortfolioPositionRiskReview:
    holdings: list[HoldingPositionRisk]
    generated_at: str
    macro_regime: str
    horizon_days: int = 90
    llm_summary: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "horizon_days": self.horizon_days,
            "generated_at": self.generated_at,
            "macro_regime": self.macro_regime,
            "holdings": [asdict(item) for item in self.holdings],
        }

    def to_text(self) -> str:
        if not self.holdings:
            return "No portfolio holdings are available for position-risk review."
        lines = [
            "Portfolio position-risk review",
            f"Macro regime: {self.macro_regime}",
            "This is an evidence-based review, not an automatic instruction to trade.",
        ]
        for item in self.holdings:
            lines.append(
                f"\n{item.ticker}: {item.rating} ({item.score}/10, confidence {item.confidence})"
            )
            for section in SECTION_ORDER:
                details = item.sections.get(section) or ["Insufficient evidence."]
                lines.append(f"{section}: {' '.join(details)}")
            lines.append(f"Conclusion: {_conclusion(item)}")
            if item.conditions:
                lines.append("Reassess if: " + "; ".join(item.conditions) + ".")
            if item.missing:
                lines.append(f"Missing: {', '.join(item.missing)}")
        if self.llm_summary:
            lines.extend(["", "Portfolio priorities:", self.llm_summary])
        return "\n".join(lines)


# Compatibility names for callers that imported the original event-only model.
HoldingEventRisk = HoldingPositionRisk
PortfolioEventRiskReview = PortfolioPositionRiskReview


def build_portfolio_review_plan(tickers: Iterable[str]) -> Any:
    """Create a fixed, validated research plan without LLM routing."""
    from .research_planner import ResearchPlan

    clean = list(
        dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker.strip())
    )
    if not clean:
        raise ValueError("No holdings are available for position-risk review.")
    if len(clean) > 8:
        raise ValueError("A portfolio review batch cannot contain more than eight holdings.")
    return ResearchPlan(
        mode="compare" if len(clean) > 1 else "company",
        workflow="company_compare" if len(clean) > 1 else "company_deep_dive",
        entities=clean,
        topics=[
            "fundamentals",
            "profitability",
            "valuation",
            "estimates",
            "recommendations",
            "price",
            "risk",
            "news",
            "events",
        ],
        lookback_days=365,
        investment_horizon="medium_term",
        raw_request="Portfolio position-risk review: " + ", ".join(clean),
    ).normalized()


def score_portfolio_position_risk(
    holdings: Iterable[Holding],
    result: Any,
    macro_snapshot: MarketRegimeSnapshot,
    *,
    today: date | None = None,
    horizon_days: int = 90,
) -> PortfolioPositionRiskReview:
    """Score retrieved evidence by risk family; missing facts never add risk."""
    today = today or date.today()
    resolved_by_ticker = {str(item.ticker).upper(): item for item in result.resolved}
    output: list[HoldingPositionRisk] = []
    for holding in holdings:
        ticker = holding.ticker.upper()
        resolved = resolved_by_ticker.get(ticker)
        if resolved is None:
            output.append(
                HoldingPositionRisk(
                    ticker=ticker,
                    quantity=holding.quantity,
                    average_cost=holding.average_cost,
                    rating="REVIEW",
                    score=0,
                    confidence="low",
                    sections={section: ["Insufficient evidence."] for section in SECTION_ORDER},
                    conditions=["the instrument can be resolved and evidence becomes available"],
                    missing=["LSEG instrument resolution"],
                )
            )
            continue

        ric = resolved.ric
        signals: list[RiskSignal] = []
        sections: dict[str, list[str]] = {section: [] for section in SECTION_ORDER}
        missing: list[str] = ["stored original investment thesis"]
        conditions: list[str] = []

        growth = _growth_metrics(result, ric)
        profitability = _profitability_metrics(result, ric)
        leverage = _leverage_metrics(result, ric)
        valuation = _valuation_metrics(result, ric)

        _score_company_thesis(
            result, ric, growth, profitability, leverage, signals, sections, missing
        )
        _score_valuation(result, ric, growth, valuation, signals, sections, missing)
        _score_macro_fit(
            macro_snapshot,
            growth,
            profitability,
            leverage,
            valuation,
            signals,
            sections,
            missing,
        )
        event = _score_event_risk(
            result,
            ric,
            today,
            horizon_days,
            signals,
            sections,
            missing,
        )

        section_caps = {"Company thesis": 4, "Valuation": 2, "Macro fit": 2, "Event risk": 2}
        score = min(
            10,
            sum(
                min(
                    section_caps[section],
                    sum(signal.points for signal in signals if signal.category == section),
                )
                for section in SECTION_ORDER
            ),
        )
        rating = _rating(score, signals)
        evidence_count = int(result.metrics.get(f"{ric}:evidence_family_count", 0) or 0)
        confidence = "high" if evidence_count >= 8 else "medium" if evidence_count >= 4 else "low"
        conditions.extend(_reassessment_conditions(signals, macro_snapshot.regime))
        output.append(
            HoldingPositionRisk(
                ticker=ticker,
                quantity=holding.quantity,
                average_cost=holding.average_cost,
                rating=rating,
                score=score,
                confidence=confidence,
                sections=sections,
                signals=signals,
                conditions=list(dict.fromkeys(conditions)),
                missing=list(dict.fromkeys(missing)),
                upcoming_event=event[0] if event else None,
                event_date=event[1].isoformat() if event else None,
                days_to_event=(event[1] - today).days if event else None,
            )
        )
    return PortfolioPositionRiskReview(
        holdings=output,
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        macro_regime=macro_snapshot.regime,
        horizon_days=horizon_days,
    )


def score_portfolio_event_risk(
    holdings: Iterable[Holding],
    result: Any,
    *,
    today: date | None = None,
    horizon_days: int = 90,
    macro_snapshot: MarketRegimeSnapshot | None = None,
) -> PortfolioPositionRiskReview:
    """Compatibility wrapper for the former event-only scorer."""
    return score_portfolio_position_risk(
        holdings,
        result,
        macro_snapshot or _incomplete_macro_snapshot(),
        today=today,
        horizon_days=horizon_days,
    )


def run_portfolio_position_risk_review(
    settings: Any,
    holdings: Iterable[Holding],
    *,
    macro_snapshot: MarketRegimeSnapshot | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Any | None = None,
) -> PortfolioPositionRiskReview:
    """Retrieve consistent dossiers, score locally, then optionally summarize."""
    from .lseg_research import run_research

    holdings = list(holdings)
    if not holdings:
        return PortfolioPositionRiskReview(
            holdings=[],
            generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
            macro_regime=macro_snapshot.regime if macro_snapshot else "Not evaluated",
        )
    if macro_snapshot is None:
        if progress_callback:
            progress_callback(2, "Refreshing market regime", "Checking rates, liquidity, inflation, credit, and volatility.")
        macro_snapshot = build_market_regime()
    if progress_callback:
        progress_callback(5, "Preparing position review", f"Checking {len(holdings)} holding(s).")
    reviews: list[PortfolioPositionRiskReview] = []
    for start in range(0, len(holdings), 8):
        batch = holdings[start : start + 8]
        plan = build_portfolio_review_plan(item.ticker for item in batch)
        policy = macro_snapshot.research_policy
        plan.macro_regime = policy.regime
        plan.research_weights = policy.weights.as_dict()
        plan.research_weight_source = policy.source
        if progress_callback:
            progress_callback(
                min(90, 5 + int(start / max(len(holdings), 1) * 85)),
                "Preparing position review",
                f"Checking holdings {start + 1}-{start + len(batch)} of {len(holdings)}.",
            )
        result = run_research(
            plan,
            settings,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        reviews.append(score_portfolio_position_risk(batch, result, macro_snapshot))
    review = PortfolioPositionRiskReview(
        holdings=[item for batch_review in reviews for item in batch_review.holdings],
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        macro_regime=macro_snapshot.regime,
        horizon_days=90,
    )
    review.llm_summary = _llm_summary(review, settings)
    if progress_callback:
        progress_callback(100, "Position review complete", "Thesis, valuation, macro, and event evidence was scored separately.")
    return review


def run_portfolio_event_risk_review(*args: Any, **kwargs: Any) -> PortfolioPositionRiskReview:
    """Compatibility wrapper for the former event-only workflow."""
    return run_portfolio_position_risk_review(*args, **kwargs)


def _score_company_thesis(
    result: Any,
    ric: str,
    growth: dict[str, float | None],
    profitability: dict[str, float | None],
    leverage: dict[str, float | None],
    signals: list[RiskSignal],
    sections: dict[str, list[str]],
    missing: list[str],
) -> None:
    revision = _metric(result, f"{ric}:eps_revision_30d")
    if revision is None:
        missing.append("30-day EPS revision history")
    elif revision <= -0.05:
        _add_signal(signals, "Company thesis", "Falling estimates", f"FY1 EPS consensus fell {abs(revision):.1%} over roughly 30 days.", 3)
    elif revision <= -0.02:
        _add_signal(signals, "Company thesis", "Falling estimates", f"FY1 EPS consensus fell {abs(revision):.1%} over roughly 30 days.", 2)
    elif revision < 0:
        _add_signal(signals, "Company thesis", "Softening estimates", f"FY1 EPS consensus edged down {abs(revision):.1%} over roughly 30 days.", 1)
    else:
        sections["Company thesis"].append(f"EPS estimates are stable to improving ({revision:+.1%} over roughly 30 days).")

    revenue_growth = growth["revenue"]
    eps_growth = growth["eps"]
    growth_parts = []
    if revenue_growth is not None:
        growth_parts.append(f"forward revenue growth {revenue_growth:+.1%}")
        if revenue_growth < 0:
            _add_signal(signals, "Company thesis", "Revenue contraction", f"Consensus implies {revenue_growth:.1%} forward revenue growth.", 2)
        elif revenue_growth < 0.05:
            _add_signal(signals, "Company thesis", "Slowing growth", f"Consensus implies only {revenue_growth:.1%} forward revenue growth.", 1)
    else:
        missing.append("forward revenue growth")
    if eps_growth is not None:
        growth_parts.append(f"forward EPS growth {eps_growth:+.1%}")
        if eps_growth < 0:
            _add_signal(signals, "Company thesis", "EPS contraction", f"Positive consensus EPS is expected to decline {abs(eps_growth):.1%}.", 2)
    if growth_parts:
        sections["Company thesis"].append("Consensus shows " + " and ".join(growth_parts) + ".")

    operating_margin = profitability["operating_margin"]
    fcf_margin = profitability["fcf_margin"]
    if operating_margin is None and fcf_margin is None:
        missing.append("operating and free-cash-flow margins")
    else:
        margin_parts = []
        if operating_margin is not None:
            margin_parts.append(f"operating margin {operating_margin:.1%}")
        if fcf_margin is not None:
            margin_parts.append(f"FCF margin {fcf_margin:.1%}")
        sections["Company thesis"].append("Current profitability: " + ", ".join(margin_parts) + ".")
        if operating_margin is not None and fcf_margin is not None and operating_margin < 0 and fcf_margin < 0:
            _add_signal(signals, "Company thesis", "Weak profitability", "Both operating profit and free cash flow are negative relative to revenue.", 2)
        elif any(value is not None and value < 0 for value in (operating_margin, fcf_margin)):
            _add_signal(signals, "Company thesis", "Weak profitability", "At least one current operating or cash-flow margin is negative.", 1)

    historical_margin = profitability["historical_operating_margin"]
    if operating_margin is not None and historical_margin is not None:
        margin_change = operating_margin - historical_margin
        sections["Company thesis"].append(
            f"LTM operating margin is {margin_change:+.1%} versus its five-year average."
        )
        if margin_change <= -0.10:
            _add_signal(signals, "Company thesis", "Margin deterioration", "LTM operating margin is at least 10 percentage points below its five-year average.", 2)
        elif margin_change <= -0.05:
            _add_signal(signals, "Company thesis", "Margin deterioration", "LTM operating margin is at least 5 percentage points below its five-year average.", 1)
    else:
        missing.append("historical operating-margin comparison")

    debt_to_fcf = leverage["debt_to_fcf"]
    net_debt = leverage["net_debt"]
    if net_debt is None:
        missing.append("net debt")
    elif net_debt <= 0:
        sections["Company thesis"].append("Cash covers reported total debt.")
    elif fcf_margin is not None and fcf_margin <= 0:
        _add_signal(signals, "Company thesis", "Refinancing exposure", "The company has net debt while current free cash flow is non-positive.", 2)
    elif debt_to_fcf is not None and debt_to_fcf > 5:
        _add_signal(signals, "Company thesis", "High leverage", f"Total debt is about {debt_to_fcf:.1f} times LTM free cash flow.", 2)
    elif debt_to_fcf is not None and debt_to_fcf > 3:
        _add_signal(signals, "Company thesis", "Leverage watch", f"Total debt is about {debt_to_fcf:.1f} times LTM free cash flow.", 1)

    for signal in signals:
        if signal.category == "Company thesis":
            sections["Company thesis"].append(signal.detail)
    if not sections["Company thesis"]:
        sections["Company thesis"].append("No thesis deterioration was supported by the retrieved company evidence.")


def _score_valuation(
    result: Any,
    ric: str,
    growth: dict[str, float | None],
    valuation: dict[str, float | None],
    signals: list[RiskSignal],
    sections: dict[str, list[str]],
    missing: list[str],
) -> None:
    fpe = valuation["forward_pe"]
    peer_fpe = valuation["peer_forward_pe"]
    target_upside = valuation["target_upside"]
    run_up = _metric(result, f"{ric}:return_1m")
    valuation_risk = False
    if fpe is not None and fpe > 0:
        sections["Valuation"].append(f"Forward P/E is {fpe:.1f}x.")
        if peer_fpe is not None and peer_fpe > 0 and fpe > peer_fpe * 1.25:
            _add_signal(signals, "Valuation", "Peer premium", f"Forward P/E is more than 25% above the peer median of {peer_fpe:.1f}x.", 1)
            valuation_risk = True
        expected_growth = max(
            (value for value in (growth["eps"], growth["revenue"], growth["long_term"]) if value is not None and value > 0),
            default=None,
        )
        if expected_growth is not None:
            growth_adjusted = fpe / (expected_growth * 100)
            sections["Valuation"].append(f"Forward P/E is {growth_adjusted:.1f} times the strongest available expected growth rate.")
            if growth_adjusted > 2.0 + 1e-9:
                _add_signal(signals, "Valuation", "Growth-adjusted valuation", "The forward multiple is high relative to the strongest available expected growth rate.", 1)
                valuation_risk = True
    else:
        missing.append("usable forward P/E")

    if target_upside is None:
        missing.append("analyst target comparison")
    elif target_upside <= -0.15:
        _add_signal(signals, "Valuation", "Target value exceeded", f"Mean analyst target is {abs(target_upside):.1%} below the current price.", 2)
        valuation_risk = True
    elif target_upside < 0:
        _add_signal(signals, "Valuation", "Target value exceeded", f"Mean analyst target is {abs(target_upside):.1%} below the current price.", 1)
        valuation_risk = True
    else:
        sections["Valuation"].append(f"Mean analyst target is {target_upside:.1%} above the current price.")

    if run_up is not None and run_up > 0.08:
        if valuation_risk:
            sections["Valuation"].append(f"The {run_up:.1%} one-month run-up increases the importance of those valuation checks; the run-up itself is not a sell signal.")
        else:
            sections["Valuation"].append(f"Price rose {run_up:.1%} over one month, but a run-up alone is not a sell signal and adds no risk points.")
    for signal in signals:
        if signal.category == "Valuation" and signal.detail not in sections["Valuation"]:
            sections["Valuation"].append(signal.detail)
    if not sections["Valuation"]:
        sections["Valuation"].append("No valuation-based reduce signal was supported by the available comparisons.")


def _score_macro_fit(
    snapshot: MarketRegimeSnapshot,
    growth: dict[str, float | None],
    profitability: dict[str, float | None],
    leverage: dict[str, float | None],
    valuation: dict[str, float | None],
    signals: list[RiskSignal],
    sections: dict[str, list[str]],
    missing: list[str],
) -> None:
    high_growth = any(value is not None and value >= 0.20 for value in growth.values())
    unprofitable = any(
        value is not None and value < 0
        for value in (profitability["operating_margin"], profitability["fcf_margin"])
    )
    leveraged = bool(
        leverage["net_debt"] is not None
        and leverage["net_debt"] > 0
        and (
            profitability["fcf_margin"] is not None
            and profitability["fcf_margin"] <= 0
            or leverage["debt_to_fcf"] is not None
            and leverage["debt_to_fcf"] > 5
        )
    )
    high_multiple = bool(
        valuation["forward_pe"] is not None
        and growth["best"] is not None
        and growth["best"] > 0
        and valuation["forward_pe"] / (growth["best"] * 100) > 2.0 + 1e-9
    )
    traits = [
        label
        for label, present in (
            ("high growth", high_growth),
            ("weak profitability", unprofitable),
            ("refinancing exposure", leveraged),
            ("growth-sensitive valuation", high_multiple),
        )
        if present
    ]
    profile = ", ".join(traits) if traits else "profitable or lower-liquidity-sensitivity characteristics"
    sections["Macro fit"].append(f"Profile: {profile}. Current regime: {snapshot.regime}.")

    credit_worsening = _indicator_rising(snapshot, "high_yield_spread")
    volatility_rising = _indicator_rising(snapshot, "vix")
    tightening = snapshot.regime == "Tightening and contracting liquidity"
    mixed = snapshot.regime == "Mixed liquidity regime"
    if tightening and len(traits) >= 3:
        _add_signal(signals, "Macro fit", "Macro exit risk", "Tightening liquidity conflicts with several financing- and multiple-sensitive company characteristics.", 2)
    elif tightening and len(traits) >= 2:
        _add_signal(signals, "Macro fit", "Macro sensitivity", "Tightening liquidity is a headwind for this growth- and multiple-sensitive profile.", 1)
    elif mixed and unprofitable and leveraged:
        _add_signal(signals, "Macro fit", "Macro exit risk", "A mixed-liquidity regime offers limited support to an unprofitable company with refinancing exposure.", 2)
    elif mixed and len(traits) >= 3:
        _add_signal(signals, "Macro fit", "Macro sensitivity", "The mixed-liquidity regime only partly supports this liquidity-sensitive profile.", 1)
    elif snapshot.regime == "Easing and expanding liquidity" and len(traits) >= 2:
        sections["Macro fit"].append("The regime currently supports growth-sensitive assets, but that support should be reassessed if policy reverses.")
    else:
        sections["Macro fit"].append("The current regime does not create a position-specific exit signal from the retrieved characteristics.")

    if credit_worsening and (leveraged or unprofitable):
        _add_signal(signals, "Macro fit", "Credit deterioration", "High-yield spreads are rising, which increases financing risk for this profile.", 1)
    if volatility_rising:
        sections["Macro fit"].append("Market volatility is rising; this is context, not an independent sell signal.")
    if snapshot.regime == "Regime incomplete":
        missing.append("complete Fed rate and balance-sheet regime")
    for signal in signals:
        if signal.category == "Macro fit" and signal.detail not in sections["Macro fit"]:
            sections["Macro fit"].append(signal.detail)


def _score_event_risk(
    result: Any,
    ric: str,
    today: date,
    horizon_days: int,
    signals: list[RiskSignal],
    sections: dict[str, list[str]],
    missing: list[str],
) -> tuple[str, date] | None:
    frame = result.tables.get("events")
    event = _next_event(frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(), ric, today, horizon_days)
    if event is None:
        if frame is None:
            missing.append("upcoming material event calendar")
            sections["Event risk"].append("No validated material event date was available.")
        else:
            sections["Event risk"].append(f"No material event was found in the next {horizon_days} days.")
    else:
        event_name, event_day = event
        days = (event_day - today).days
        sections["Event risk"].append(f"{event_name} is {days} days away ({event_day.isoformat()}).")
        if days <= 14:
            _add_signal(signals, "Event risk", "Immediate event", "A material event is within 14 days and warrants a pre-event thesis check.", 2)
        elif days <= 30:
            _add_signal(signals, "Event risk", "Near-term event", "A material event is within 30 days and warrants monitoring.", 1)
        else:
            sections["Event risk"].append("The event is not close enough to create an immediate action trigger.")

    volatility = _metric(result, f"{ric}:annualized_vol")
    if volatility is not None:
        sections["Event risk"].append(f"Annualized realized volatility is approximately {volatility:.1%}; volatility alone adds no risk points.")
    else:
        missing.append("annualized volatility context")
    return event


def _growth_metrics(result: Any, ric: str) -> dict[str, float | None]:
    revenue_fy1 = _table_numeric(result, "estimates", "TR.RevenueMean(Period=FY1)", ric)
    revenue_fy2 = _table_numeric(result, "estimates", "TR.RevenueMean(Period=FY2)", ric)
    eps_fy1 = _table_numeric(result, "estimates", "TR.EPSMean(Period=FY1)", ric)
    eps_fy2 = _table_numeric(result, "estimates", "TR.EPSMean(Period=FY2)", ric)
    long_term = _table_numeric(result, "recommendations", "TR.LTGMean", ric)
    if long_term is not None and abs(long_term) > 1:
        long_term /= 100
    revenue = revenue_fy2 / revenue_fy1 - 1 if revenue_fy1 and revenue_fy1 > 0 and revenue_fy2 is not None and revenue_fy2 > 0 else None
    eps = eps_fy2 / eps_fy1 - 1 if eps_fy1 and eps_fy1 > 0 and eps_fy2 is not None and eps_fy2 > 0 else None
    best = max((value for value in (revenue, eps, long_term) if value is not None and value > 0), default=None)
    return {"revenue": revenue, "eps": eps, "long_term": long_term, "best": best}


def _profitability_metrics(result: Any, ric: str) -> dict[str, float | None]:
    historical = _table_numeric(
        result,
        "profitability",
        "TR.OperatingProfitMarginPct5YrAvg",
        ric,
    )
    if historical is not None and abs(historical) > 1:
        historical /= 100
    return {
        "operating_margin": _metric(result, f"{ric}:operating_margin"),
        "fcf_margin": _metric(result, f"{ric}:fcf_margin"),
        "historical_operating_margin": historical,
    }


def _leverage_metrics(result: Any, ric: str) -> dict[str, float | None]:
    return {
        "net_debt": _metric(result, f"{ric}:net_debt"),
        "debt_to_fcf": _metric(result, f"{ric}:debt_to_fcf"),
    }


def _valuation_metrics(result: Any, ric: str) -> dict[str, float | None]:
    return {
        "forward_pe": _table_numeric(result, "valuation", "TR.PtoEPSMeanEst(Period=FY1)", ric),
        "peer_forward_pe": _metric(result, f"{ric}:peer_median:TR.PtoEPSMeanEst(Period=FY1)"),
        "target_upside": _metric(result, f"{ric}:target_upside"),
    }


def _table_numeric(result: Any, table_name: str, field_name: str, ric: str) -> float | None:
    frame = result.tables.get(table_name, pd.DataFrame())
    if frame is None or frame.empty or field_name not in frame.columns:
        return None
    subset = frame
    if "Instrument" in frame.columns:
        subset = frame[frame["Instrument"].astype(str) == ric]
    if subset.empty:
        return None
    values = pd.to_numeric(subset[field_name], errors="coerce").dropna()
    if values.empty:
        return None
    value = float(values.iloc[0])
    return value if math.isfinite(value) else None


def _next_event(frame: pd.DataFrame, ric: str, today: date, horizon_days: int) -> tuple[str, date] | None:
    if frame is None or frame.empty:
        return None
    subset = frame
    if "Instrument" in frame.columns:
        subset = frame[frame["Instrument"].astype(str) == ric]
    date_col = next((column for column in subset.columns if "eventstartdate" in str(column).casefold()), None)
    if date_col is None:
        return None
    title_col = next((column for column in subset.columns if "eventtitle" in str(column).casefold()), None)
    type_col = next((column for column in subset.columns if "eventtype" in str(column).casefold()), None)
    dates = pd.to_datetime(subset[date_col], errors="coerce", utc=True).dt.date
    end = today.fromordinal(today.toordinal() + horizon_days)
    candidates = []
    for index, event_day in dates.items():
        if event_day is None or pd.isna(event_day) or not today <= event_day <= end:
            continue
        title = str(subset.loc[index, title_col]).strip() if title_col and pd.notna(subset.loc[index, title_col]) else ""
        event_type = str(subset.loc[index, type_col]).strip() if type_col and pd.notna(subset.loc[index, type_col]) else ""
        label = title or event_type or "LSEG event"
        if any(term in f"{title} {event_type}".casefold() for term in ("dividend", "ex-div", "distribution")):
            continue
        candidates.append((event_day, label))
    if not candidates:
        return None
    event_day, title = sorted(candidates, key=lambda item: item[0])[0]
    return title, event_day


def _indicator_rising(snapshot: MarketRegimeSnapshot, key: str) -> bool:
    indicator = next((item for item in snapshot.indicators if item.key == key), None)
    return bool(indicator and indicator.status == "available" and indicator.trend.startswith("Rising"))


def _metric(result: Any, key: str) -> float | None:
    value = result.metrics.get(key)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def _add_signal(signals: list[RiskSignal], category: str, label: str, detail: str, points: int) -> None:
    signals.append(RiskSignal(category, label, detail, points))


def _rating(score: int, signals: list[RiskSignal]) -> str:
    company_points = sum(signal.points for signal in signals if signal.category == "Company thesis")
    independent_categories = len({signal.category for signal in signals if signal.points > 0})
    if score >= 9 and company_points >= 3 and independent_categories >= 3:
        return "EXIT CANDIDATE"
    if score >= 7 and independent_categories >= 2:
        return "TRIM"
    if score >= 3:
        return "REVIEW"
    return "HOLD"


def _reassessment_conditions(signals: list[RiskSignal], regime: str) -> list[str]:
    categories = {signal.category for signal in signals}
    conditions = ["revenue or EPS expectations deteriorate materially"]
    if "Company thesis" not in categories:
        conditions.append("profitability weakens or leverage rises")
    if "Valuation" not in categories:
        conditions.append("valuation moves materially beyond growth or target evidence")
    if regime != "Tightening and contracting liquidity":
        conditions.append("rates, Fed liquidity, or credit conditions become materially tighter")
    return conditions


def _conclusion(item: HoldingPositionRisk) -> str:
    if item.rating == "HOLD":
        return "No evidence-backed reduce or exit trigger is present now."
    if item.rating == "REVIEW":
        return "No automatic sell trigger; review the flagged evidence and the conditions that would invalidate the thesis."
    if item.rating == "TRIM":
        return "Multiple independent risks support reviewing position size, subject to your thesis, target value, taxes, and risk tolerance."
    return "Severe thesis risk plus corroborating evidence makes this an exit candidate for immediate human review, not an automatic order."


def _incomplete_macro_snapshot() -> MarketRegimeSnapshot:
    from datetime import datetime, timezone

    return MarketRegimeSnapshot(
        regime="Regime incomplete",
        summary="Macro observations were not supplied to the compatibility scorer.",
        emphasis=("Refresh the Market tab before relying on macro fit.",),
        indicators=(),
        missing_evidence=("Current macro observations",),
        generated_at=datetime.now(timezone.utc),
    )


def _llm_summary(review: PortfolioPositionRiskReview, settings: Any) -> str | None:
    if not getattr(settings, "groq_api_key", None):
        return None
    try:
        from langchain_groq import ChatGroq

        response = ChatGroq(
            model=settings.groq_model,
            temperature=0,
            max_retries=1,
            api_key=settings.groq_api_key,
        ).invoke(
            [
                (
                    "system",
                    "Summarize only the supplied deterministic position-risk evidence. Preserve each HOLD, REVIEW, TRIM, or EXIT CANDIDATE classification. Separate company thesis, valuation, macro fit, and event risk. A price run-up or volatility alone is not a sell signal. Do not invent facts, infer a missing original thesis, or issue personalized trade instructions. Return at most eight concise lines.",
                ),
                ("human", json.dumps(review.as_payload(), default=str)),
            ]
        )
        text = str(getattr(response, "content", response)).strip()
        return text or None
    except Exception:
        return None
