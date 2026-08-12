"""Evidence-driven review of portfolio holdings near earnings and events."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
import json
import math
from typing import Any, Callable, Iterable

import pandas as pd

from .models import Holding


ProgressCallback = Callable[[int | None, str, str], None]


@dataclass(frozen=True)
class RiskSignal:
    label: str
    detail: str
    points: int


@dataclass
class HoldingEventRisk:
    ticker: str
    quantity: float
    average_cost: float
    rating: str
    score: int
    confidence: str
    upcoming_event: str | None = None
    event_date: str | None = None
    days_to_event: int | None = None
    signals: list[RiskSignal] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def review_before_event(self) -> bool:
        return self.rating == "Review soon"


@dataclass
class PortfolioEventRiskReview:
    holdings: list[HoldingEventRisk]
    generated_at: str
    horizon_days: int = 90
    llm_summary: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "horizon_days": self.horizon_days,
            "generated_at": self.generated_at,
            "holdings": [
                {
                    **asdict(item),
                    "signals": [asdict(signal) for signal in item.signals],
                }
                for item in self.holdings
            ],
        }

    def to_text(self) -> str:
        if not self.holdings:
            return "No portfolio holdings are available for event-risk review."
        lines = [
            f"Portfolio event-risk review (next {self.horizon_days} days)",
            "This is an evidence-based review prompt, not an automatic sell recommendation.",
        ]
        for item in self.holdings:
            event = ""
            if item.upcoming_event:
                event = f"; event={item.upcoming_event}"
                if item.event_date:
                    event += f" on {item.event_date}"
                if item.days_to_event is not None:
                    event += f" ({item.days_to_event} days)"
            lines.append(
                f"\n{item.ticker}: {item.rating} (score {item.score}/10, confidence {item.confidence}){event}"
            )
            for signal in item.signals:
                lines.append(f"- {signal.label}: {signal.detail}")
            if item.missing:
                lines.append(f"- Missing: {', '.join(item.missing)}")
        if self.llm_summary:
            lines.extend(["", "Evidence summary:", self.llm_summary])
        return "\n".join(lines)


def build_portfolio_review_plan(tickers: Iterable[str]) -> Any:
    """Create a fixed, validated research plan without asking the LLM to route it."""
    from .research_planner import ResearchPlan

    clean = list(dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker.strip()))
    if not clean:
        raise ValueError("No holdings are available for event-risk review.")
    if len(clean) > 8:
        raise ValueError("A portfolio review batch cannot contain more than eight holdings.")
    return ResearchPlan(
        mode="compare" if len(clean) > 1 else "company",
        workflow="company_compare" if len(clean) > 1 else "company_deep_dive",
        entities=clean,
        topics=["valuation", "estimates", "price", "risk", "news", "events"],
        lookback_days=365,
        investment_horizon="short_term",
        raw_request="Portfolio event-risk review: " + ", ".join(clean),
    ).normalized()


def score_portfolio_event_risk(
    holdings: Iterable[Holding],
    result: Any,
    *,
    today: date | None = None,
    horizon_days: int = 90,
) -> PortfolioEventRiskReview:
    """Score only retrieved facts; missing evidence lowers confidence, not risk."""
    today = today or date.today()
    resolved_by_ticker = {str(item.ticker).upper(): item for item in result.resolved}
    output: list[HoldingEventRisk] = []
    for holding in holdings:
        ticker = holding.ticker.upper()
        resolved = resolved_by_ticker.get(ticker)
        if resolved is None:
            output.append(
                HoldingEventRisk(
                    ticker=ticker,
                    quantity=holding.quantity,
                    average_cost=holding.average_cost,
                    rating="Insufficient data",
                    score=0,
                    confidence="low",
                    missing=["LSEG instrument resolution"],
                )
            )
            continue
        ric = resolved.ric
        signals: list[RiskSignal] = []
        missing: list[str] = []
        event = _next_event(result.tables.get("events", pd.DataFrame()), ric, today, horizon_days)
        if event is not None:
            event_name, event_day = event
            days = (event_day - today).days
            points = 3 if days <= 14 else 2 if days <= 30 else 1
            signals.append(RiskSignal("Upcoming event", f"{event_name} is {days} days away.", points))
        else:
            missing.append("upcoming LSEG event date")
            days = None
            event_name = event_day = None

        revision = _metric(result, f"{ric}:eps_revision_30d")
        if revision is None:
            missing.append("EPS revision history")
        elif revision <= -0.05:
            signals.append(RiskSignal("Negative estimate revision", f"FY1 EPS consensus is down {abs(revision):.1%} over 30 days.", 2))
        elif revision <= -0.02:
            signals.append(RiskSignal("Negative estimate revision", f"FY1 EPS consensus is down {abs(revision):.1%} over 30 days.", 1))

        return_1m = _metric(result, f"{ric}:return_1m")
        if return_1m is None:
            missing.append("one-month price return")
        elif return_1m >= 0.15:
            signals.append(RiskSignal("Recent run-up", f"Price is up {return_1m:.1%} over one month.", 2))
        elif return_1m >= 0.08:
            signals.append(RiskSignal("Recent run-up", f"Price is up {return_1m:.1%} over one month.", 1))

        volatility = _metric(result, f"{ric}:annualized_vol")
        if volatility is None:
            missing.append("annualized volatility")
        elif volatility >= 0.60:
            signals.append(RiskSignal("Elevated volatility", f"Annualized volatility is approximately {volatility:.1%}.", 2))
        elif volatility >= 0.40:
            signals.append(RiskSignal("Elevated volatility", f"Annualized volatility is approximately {volatility:.1%}.", 1))

        risk_hits = result.metrics.get(f"{ric}:risk_headline_hits")
        if isinstance(risk_hits, int) and risk_hits >= 2:
            signals.append(RiskSignal("Recent risk headlines", f"Retrieved headlines contain {risk_hits} risk indicator(s).", 2))
        elif risk_hits is None:
            missing.append("headline risk scan")

        score = min(10, sum(signal.points for signal in signals))
        if event is None and not signals:
            rating = "Insufficient data"
        elif score >= 5:
            rating = "Review soon"
        elif score >= 2:
            rating = "Monitor"
        else:
            rating = "No near-term event concern"
        evidence_count = int(result.metrics.get(f"{ric}:evidence_family_count", 0) or 0)
        confidence = "high" if evidence_count >= 6 and not missing else "medium" if evidence_count >= 3 else "low"
        output.append(
            HoldingEventRisk(
                ticker=ticker,
                quantity=holding.quantity,
                average_cost=holding.average_cost,
                rating=rating,
                score=score,
                confidence=confidence,
                upcoming_event=event_name,
                event_date=event_day.isoformat() if event_day else None,
                days_to_event=days,
                signals=signals,
                missing=list(dict.fromkeys(missing)),
            )
        )
    return PortfolioEventRiskReview(
        holdings=output,
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        horizon_days=horizon_days,
    )


def run_portfolio_event_risk_review(
    settings: Any,
    holdings: Iterable[Holding],
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Any | None = None,
) -> PortfolioEventRiskReview:
    """Retrieve one compare dossier, score it locally, then optionally explain it with Groq."""
    from .lseg_research import run_research

    holdings = list(holdings)
    if not holdings:
        return PortfolioEventRiskReview(
            holdings=[],
            generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        )
    if progress_callback:
        progress_callback(5, "Preparing portfolio review", f"Checking {len(holdings)} holding(s).")
    reviews: list[PortfolioEventRiskReview] = []
    for start in range(0, len(holdings), 8):
        batch = holdings[start : start + 8]
        plan = build_portfolio_review_plan(item.ticker for item in batch)
        if progress_callback:
            progress_callback(
                min(90, 5 + int(start / max(len(holdings), 1) * 85)),
                "Preparing portfolio review",
                f"Checking holdings {start + 1}-{start + len(batch)} of {len(holdings)}.",
            )
        result = run_research(plan, settings, progress_callback=progress_callback, cancel_event=cancel_event)
        review = score_portfolio_event_risk(batch, result)
        review.llm_summary = _llm_summary(review, settings)
        reviews.append(review)
    review = PortfolioEventRiskReview(
        holdings=[item for batch_review in reviews for item in batch_review.holdings],
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        horizon_days=90,
        llm_summary="\n".join(
            summary for summary in (item.llm_summary for item in reviews) if summary
        ) or None,
    )
    if progress_callback:
        progress_callback(100, "Portfolio review complete", "Event and estimate evidence was scored.")
    return review


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
        candidates.append((event_day, title or event_type or "LSEG event"))
    if not candidates:
        return None
    event_day, title = sorted(candidates, key=lambda item: item[0])[0]
    return title, event_day


def _metric(result: Any, key: str) -> float | None:
    value = result.metrics.get(key)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def _llm_summary(review: PortfolioEventRiskReview, settings: Any) -> str | None:
    if not getattr(settings, "groq_api_key", None):
        return None
    try:
        from langchain_groq import ChatGroq

        response = ChatGroq(
            model=settings.groq_model,
            temperature=0,
            max_retries=2,
            api_key=settings.groq_api_key,
        ).invoke([
            ("system", "Summarize the supplied portfolio event-risk scores only. Do not invent dates or facts, and do not issue buy or sell instructions. Return at most five concise lines."),
            ("human", json.dumps(review.as_payload(), default=str)),
        ])
        text = str(getattr(response, "content", response)).strip()
        return text or None
    except Exception:
        return None
