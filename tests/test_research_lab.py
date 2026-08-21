from datetime import date, datetime, timezone

import pandas as pd
import pytest

from portfolio.company_resolver import ResolvedInstrument
from portfolio.config import Settings
from portfolio.lseg_research import ResearchResult
from portfolio.market_regime import MarketRegimeSnapshot, Observation
from portfolio.research_lab import (
    ApprovedResearchPlan,
    ResearchLabError,
    VerifiedFinding,
    derive_findings,
    execute_research,
    summarize_findings,
    validate_proposal_payload,
)
from portfolio.research_plan import ResearchPlan


def _payload(**overrides):
    payload = {
        "status": "ready",
        "clarification": None,
        "securities": ["AAPL", "MSFT"],
        "lookback_days": 365,
        "benchmark": None,
        "capabilities": [
            {"id": "price_history", "reason": "Returns are needed."},
            {"id": "treasury_yield_history", "reason": "The question asks about rates."},
        ],
        "analyses": [
            {"id": "falling_rate_comparison", "reason": "Compare falling-rate periods."}
        ],
    }
    payload.update(overrides)
    return payload


def _macro() -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        regime="Neutral liquidity regime",
        summary="Core liquidity signals are stable.",
        emphasis=(),
        indicators=(),
        missing_evidence=(),
        generated_at=datetime.now(timezone.utc),
        company_fit="No defensive or risk-tolerant tilt is active.",
    )


def test_proposal_adds_required_macro_and_analysis_dependencies() -> None:
    proposal = validate_proposal_payload(
        "Compare AAPL and MSFT when rates fall",
        _payload(),
    )

    capabilities = {item.item_id for item in proposal.capabilities}
    assert proposal.ready
    assert capabilities >= {"macro_context", "price_history", "treasury_yield_history"}


def test_proposal_rejects_an_invented_security() -> None:
    with pytest.raises(ResearchLabError, match="not present in the question"):
        validate_proposal_payload(
            "Compare AAPL and MSFT",
            _payload(securities=["AAPL", "NVDA"]),
        )


def test_proposal_rejects_unknown_capability() -> None:
    with pytest.raises(ResearchLabError, match="unknown capability"):
        validate_proposal_payload(
            "Compare AAPL and MSFT",
            _payload(capabilities=[{"id": "arbitrary_lseg_call", "reason": "No."}]),
        )


def test_approval_requires_analysis_dependencies_and_one_rate_series() -> None:
    plan = ApprovedResearchPlan(
        question="Compare AAPL and MSFT when rates fall",
        securities=("AAPL", "MSFT"),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "price_history"),
        analysis_ids=("falling_rate_comparison",),
    )
    with pytest.raises(ResearchLabError, match="exactly one"):
        plan.validated()


def test_research_lab_plan_accepts_one_explicit_security() -> None:
    plan = ResearchPlan(
        mode="company",
        workflow="research_lab",
        entities=["AAPL"],
        topics=["price"],
    ).normalized()

    assert plan.workflow == "research_lab"
    assert plan.mode == "compare"


def test_research_lab_plan_keeps_eight_securities_plus_benchmark() -> None:
    entities = [f"TICKER{index}" for index in range(8)] + ["SPY"]
    plan = ResearchPlan(
        mode="compare",
        workflow="research_lab",
        entities=entities,
        topics=["price", "benchmark_price"],
        benchmark="SPY",
    ).normalized()

    assert plan.entities == entities


def test_execution_compiles_only_approved_lseg_topics(tmp_path, monkeypatch) -> None:
    approved = ApprovedResearchPlan(
        question="Describe AAPL",
        securities=("AAPL",),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "company_profile"),
        analysis_ids=(),
    ).validated()
    settings = Settings(tmp_path, tmp_path / "db.sqlite", None, "test-model", "desktop.workspace")
    captured = []

    def fake_run(plan, *_args, **_kwargs):
        captured.append(plan)
        resolved = ResolvedInstrument("AAPL", "AAPL", "AAPL", "AAPL.O", "Apple")
        result = ResearchResult(plan=plan, resolved=[resolved])
        result.tables["profile"] = pd.DataFrame(
            {
                "Instrument": ["AAPL.O"],
                "TR.BusinessSummary": ["Apple designs and sells consumer technology products."],
            }
        )
        return result

    monkeypatch.setattr("portfolio.research_lab.run_research", fake_run)

    output = execute_research(approved, settings, _macro())

    assert captured[0].topics == ["profile"]
    assert output.findings[1].finding_id == "PROFILE_AAPL"


def test_python_findings_report_returns_drawdown_and_rate_sample_sizes() -> None:
    approved = ApprovedResearchPlan(
        question="Compare AAPL and MSFT when the 10-year Treasury yield falls",
        securities=("AAPL", "MSFT"),
        lookback_days=365,
        benchmark=None,
        capability_ids=(
            "macro_context",
            "price_history",
            "treasury_yield_history",
        ),
        analysis_ids=(
            "return_comparison",
            "maximum_drawdown",
            "rate_change_correlation",
            "falling_rate_comparison",
        ),
    ).validated()
    aapl = ResolvedInstrument("AAPL", "AAPL", "AAPL", "AAPL.O", "Apple")
    msft = ResolvedInstrument("MSFT", "MSFT", "MSFT", "MSFT.O", "Microsoft")
    result = ResearchResult(
        plan=ResearchPlan(mode="compare", workflow="research_lab", entities=["AAPL", "MSFT"]),
        resolved=[aapl, msft],
    )
    index = pd.date_range("2025-01-01", periods=360, freq="D")
    result.tables["price:AAPL.O"] = pd.DataFrame(
        {"TRDPRC_1": [100 + index_value * 0.2 + (index_value % 13) for index_value in range(len(index))]},
        index=index,
    )
    result.tables["price:MSFT.O"] = pd.DataFrame(
        {"TRDPRC_1": [120 + index_value * 0.1 - (index_value % 7) for index_value in range(len(index))]},
        index=index,
    )
    observations = [
        Observation(date_value.date(), 4.5 + ((position // 30) % 2) * 0.3 - (position % 30) * 0.01)
        for position, date_value in enumerate(index)
    ]

    findings, missing = derive_findings(
        approved,
        result,
        _macro(),
        {"treasury_yield_history": observations},
        primary_count=2,
    )

    ids = {item.finding_id for item in findings}
    assert {"RETURN_AAPL", "RETURN_MSFT", "DRAWDOWN_AAPL", "RATE_CORRELATION_AAPL"} <= ids
    rate_finding = next(item for item in findings if item.finding_id == "RATE_CORRELATION_AAPL")
    assert "common daily observations" in rate_finding.text
    assert not any("rate history" == item for item in missing)


def test_news_capability_exposes_company_relevant_story_excerpt() -> None:
    approved = ApprovedResearchPlan(
        question="What changed recently for AAPL?",
        securities=("AAPL",),
        lookback_days=180,
        benchmark=None,
        capability_ids=("macro_context", "company_news"),
        analysis_ids=(),
    ).validated()
    aapl = ResolvedInstrument("AAPL", "AAPL", "AAPL", "AAPL.O", "Apple")
    result = ResearchResult(
        plan=ResearchPlan(mode="compare", workflow="research_lab", entities=["AAPL"]),
        resolved=[aapl],
    )
    result.tables["stories:AAPL.O"] = pd.DataFrame(
        {
            "Instrument": ["AAPL.O"],
            "company_excerpt": ["Apple raised its outlook after reporting stronger service revenue."],
        }
    )

    findings, _missing = derive_findings(
        approved, result, _macro(), {}, primary_count=1
    )

    story = next(item for item in findings if item.finding_id == "STORY_AAPL_1")
    assert "raised its outlook" in story.text


def test_deterministic_summary_prioritizes_approved_analysis(tmp_path) -> None:
    approved = ApprovedResearchPlan(
        question="How does AAPL react to rate changes?",
        securities=("AAPL",),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "price_history", "treasury_yield_history"),
        analysis_ids=("rate_change_correlation",),
    ).validated()
    findings = [
        VerifiedFinding("MACRO_REGIME", "Macro", "Neutral.", ("FRED",)),
        VerifiedFinding("PROFILE_AAPL", "Profile", "Technology company.", ("LSEG",)),
        VerifiedFinding(
            "RATE_CORRELATION_AAPL",
            "Rate correlation",
            "Pearson correlation -0.25 across 200 common daily observations.",
            ("LSEG", "FRED"),
        ),
    ]
    settings = Settings(tmp_path, tmp_path / "db.sqlite", None, "test-model", "desktop.workspace")

    report = summarize_findings(approved, findings, [], settings)

    assert report.index("RATE_CORRELATION_AAPL") < report.index("MACRO_REGIME")
