from datetime import date, datetime, timezone

import pandas as pd
import pytest

from portfolio.company_resolver import ResolvedInstrument
from portfolio.config import Settings
from portfolio.lseg_research import ResearchResult
from portfolio.market_regime import MarketRegimeSnapshot, Observation
from portfolio.research_lab import (
    CAPABILITIES,
    ApprovedResearchPlan,
    ResearchLabError,
    ThemeCandidate,
    VerifiedFinding,
    _select_theme_candidates,
    derive_findings,
    execute_research,
    propose_research,
    proposal_catalog,
    summarize_findings,
    validate_proposal_payload,
)
from portfolio.research_plan import ResearchPlan


def _payload(**overrides):
    payload = {
        "status": "ready",
        "clarification": None,
        "mode": "named",
        "securities": ["AAPL", "MSFT"],
        "discovery_scope": None,
        "discovery_theme": None,
        "result_count": 5,
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


def test_lseg_capability_catalog_exposes_backend_operations() -> None:
    lseg_capabilities = [item for item in CAPABILITIES if item.source.startswith("LSEG")]

    assert lseg_capabilities
    assert all(item.backend_operations for item in lseg_capabilities)
    catalog = proposal_catalog()
    assert all("backend_operations" in item for item in catalog["capabilities"])


def test_capability_dependencies_are_added_to_proposal() -> None:
    proposal = validate_proposal_payload(
        "Review AAPL regulatory filings",
        _payload(
            securities=["AAPL"],
            capabilities=[
                {"id": "regulatory_filings", "reason": "Review recent filings."}
            ],
            analyses=[],
        ),
    )

    assert {item.item_id for item in proposal.capabilities} >= {
        "macro_context",
        "regulatory_filings",
        "company_profile",
    }


def test_discovery_proposal_uses_supported_scope_without_inventing_stocks() -> None:
    proposal = validate_proposal_payload(
        "Which stocks are best poised to take advantage of the AI revolution?",
        _payload(
            mode="discovery",
            securities=[],
            discovery_scope="Technology",
            discovery_theme="AI revolution",
            result_count=5,
            capabilities=[
                {"id": "company_profile", "reason": "Validate business exposure."},
                {"id": "valuation_snapshot", "reason": "Compare valuations."},
            ],
            analyses=[],
        ),
    )

    assert proposal.ready
    assert proposal.mode == "discovery"
    assert proposal.securities == ()
    assert proposal.discovery_scope == "Technology"
    assert proposal.discovery_theme == "AI revolution"
    assert {item.item_id for item in proposal.capabilities} >= {
        "macro_context",
        "company_profile",
        "valuation_snapshot",
    }


def test_exact_open_ended_question_can_propose_discovery(tmp_path, monkeypatch) -> None:
    question = "which stocks are best poised to take advantage of the AI revolution?"

    def fake_invoke(_settings, _schema, _messages, **_kwargs):
        return _payload(
            mode="discovery",
            securities=[],
            discovery_scope="Technology",
            discovery_theme="AI revolution",
            result_count=5,
            capabilities=[
                {"id": "candidate_discovery", "reason": "Discover supported matches."},
                {"id": "company_profile", "reason": "Validate thematic exposure."},
                {"id": "valuation_snapshot", "reason": "Compare valuations."},
            ],
            analyses=[],
        )

    monkeypatch.setattr("portfolio.research_lab.invoke_structured_groq", fake_invoke)
    settings = Settings(tmp_path, tmp_path / "db.sqlite", "key", "test-model", "desktop.workspace")

    proposal = propose_research(question, settings)

    assert proposal.mode == "discovery"
    assert proposal.discovery_scope == "Technology"
    assert proposal.securities == ()


def test_discovery_proposal_rejects_theme_not_grounded_in_question() -> None:
    with pytest.raises(ResearchLabError, match="copied from the current question"):
        validate_proposal_payload(
            "Find cybersecurity stocks",
            _payload(
                mode="discovery",
                securities=[],
                discovery_scope="Technology",
                discovery_theme="artificial intelligence",
                capabilities=[{"id": "company_profile", "reason": "Validate exposure."}],
                analyses=[],
            ),
        )


@pytest.mark.parametrize(
    ("question", "theme", "scope"),
    [
        ("Find cybersecurity companies", "cybersecurity", "Technology"),
        ("Find companies exposed to gene editing", "gene editing", "Healthcare"),
        ("Find stocks positioned for clean energy", "clean energy", "Energy"),
    ],
)
def test_discovery_contract_is_theme_agnostic(question, theme, scope) -> None:
    proposal = validate_proposal_payload(
        question,
        _payload(
            mode="discovery",
            securities=[],
            discovery_scope=scope,
            discovery_theme=theme,
            capabilities=[
                {"id": "candidate_discovery", "reason": "Discover candidates."},
                {"id": "company_profile", "reason": "Validate exposure."},
            ],
            analyses=[],
        ),
    )

    assert proposal.discovery_theme == theme
    assert proposal.discovery_scope == scope


def test_financial_discovery_does_not_require_a_profile_relevance_phrase() -> None:
    proposal = validate_proposal_payload(
        "Find undervalued software stocks with improving earnings estimates",
        _payload(
            mode="discovery",
            securities=[],
            discovery_scope="Software",
            discovery_theme=None,
            capabilities=[
                {"id": "candidate_discovery", "reason": "Build the candidate universe."},
                {"id": "company_profile", "reason": "Identify the candidates."},
                {"id": "valuation_snapshot", "reason": "Compare valuation evidence."},
                {"id": "estimate_revisions", "reason": "Retrieve estimate history."},
            ],
            analyses=[
                {"id": "estimate_revision_change", "reason": "Calculate comparable changes."}
            ],
        ),
    )

    assert proposal.mode == "discovery"
    assert proposal.discovery_theme is None
    assert {item.item_id for item in proposal.capabilities} >= {
        "candidate_discovery",
        "valuation_snapshot",
        "estimate_revisions",
    }


def test_market_news_question_uses_market_news_mode() -> None:
    proposal = validate_proposal_payload(
        "What market developments are moving semiconductor stocks today?",
        _payload(
            mode="market_news",
            securities=[],
            discovery_scope=None,
            discovery_theme=None,
            capabilities=[
                {"id": "market_news", "reason": "Retrieve relevant Reuters headlines."}
            ],
            analyses=[],
        ),
    )

    assert proposal.mode == "market_news"
    assert {item.item_id for item in proposal.capabilities} == {
        "macro_context",
        "market_news",
    }


def test_cross_sector_discovery_can_use_all_public_equities() -> None:
    proposal = validate_proposal_payload(
        "Find companies across the market that benefit when rates fall",
        _payload(
            mode="discovery",
            securities=[],
            discovery_scope="All public equities",
            discovery_theme=None,
            capabilities=[
                {"id": "candidate_discovery", "reason": "Build a broad equity universe."},
                {"id": "company_profile", "reason": "Identify the candidates."},
                {"id": "price_history", "reason": "Retrieve returns."},
                {"id": "fed_funds_history", "reason": "Retrieve rate history."},
            ],
            analyses=[
                {"id": "falling_rate_comparison", "reason": "Compare falling-rate periods."}
            ],
        ),
    )

    assert proposal.discovery_scope == "All public equities"


def test_market_news_execution_uses_market_news_workflow(tmp_path, monkeypatch) -> None:
    approved = ApprovedResearchPlan(
        question="What is moving semiconductor stocks today?",
        securities=(),
        lookback_days=90,
        benchmark=None,
        capability_ids=("macro_context", "market_news"),
        analysis_ids=(),
        mode="market_news",
    ).validated()
    captured = []

    def fake_run(plan, *_args, **_kwargs):
        captured.append(plan)
        result = ResearchResult(plan=plan)
        result.tables["market_news"] = pd.DataFrame(
            {"headline": ["Semiconductor shares rise after an industry update"]}
        )
        return result

    monkeypatch.setattr("portfolio.research_lab.run_research", fake_run)
    settings = Settings(tmp_path, tmp_path / "db.sqlite", None, "test-model", "desktop.workspace")

    output = execute_research(approved, settings, _macro())

    assert captured[0].workflow == "market_news"
    assert any(item.finding_id == "MARKET_NEWS_1" for item in output.findings)


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


def test_discovery_approval_requires_profile_and_no_preselected_security() -> None:
    plan = ApprovedResearchPlan(
        question="Find AI stocks",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=(
            "macro_context",
            "candidate_discovery",
            "company_profile",
            "valuation_snapshot",
        ),
        analysis_ids=(),
        mode="discovery",
        discovery_scope="Technology",
        discovery_theme="AI",
        result_count=5,
    )

    assert plan.validated().discovery_scope == "Technology"

    with pytest.raises(ResearchLabError, match="preselected securities"):
        ApprovedResearchPlan(
            question=plan.question,
            securities=("NVDA",),
            lookback_days=plan.lookback_days,
            benchmark=None,
            capability_ids=plan.capability_ids,
            analysis_ids=(),
            mode="discovery",
            discovery_scope=plan.discovery_scope,
            discovery_theme=plan.discovery_theme,
        ).validated()


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


def test_theme_selection_uses_retrieved_profiles_and_preserves_python_order(
    tmp_path, monkeypatch
) -> None:
    approved = ApprovedResearchPlan(
        question="Find AI stocks",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "candidate_discovery", "company_profile"),
        analysis_ids=(),
        mode="discovery",
        discovery_scope="Technology",
        discovery_theme="AI",
        result_count=2,
    ).validated()
    screen_result = ResearchResult(
        plan=ResearchPlan(mode="screen", workflow="stock_screen")
    )
    screen_result.tables["screen"] = pd.DataFrame(
        [
            {
                "Instrument": "AAA.O",
                "TR.TickerSymbol": "AAA",
                "TR.CommonName": "Alpha",
                "TR.TRBCEconomicSector": "Technology",
                "TR.TRBCIndustry": "Software",
                "TR.BusinessSummary": "Alpha develops artificial intelligence software.",
            },
            {
                "Instrument": "BBB.O",
                "TR.TickerSymbol": "BBB",
                "TR.CommonName": "Beta",
                "TR.TRBCEconomicSector": "Technology",
                "TR.TRBCIndustry": "Software",
                "TR.BusinessSummary": "Beta sells accounting software.",
            },
            {
                "Instrument": "CCC.O",
                "TR.TickerSymbol": "CCC",
                "TR.CommonName": "Gamma",
                "TR.TRBCEconomicSector": "Technology",
                "TR.TRBCIndustry": "Semiconductors",
                "TR.BusinessSummary": "Gamma supplies processors used for AI workloads.",
            },
        ]
    )

    def fake_invoke(_settings, _schema, _messages, **_kwargs):
        return {
            "matches": [
                {"candidate_id": "C3", "relevance": "meaningful", "reason": "Explicit AI workloads."},
                {"candidate_id": "C1", "relevance": "direct", "reason": "AI is a core product."},
                {"candidate_id": "C2", "relevance": "unsupported", "reason": "No AI exposure stated."},
            ]
        }

    monkeypatch.setattr("portfolio.research_lab.invoke_structured_groq", fake_invoke)
    settings = Settings(tmp_path, tmp_path / "db.sqlite", "key", "test-model", "desktop.workspace")

    selected, missing = _select_theme_candidates(screen_result, approved, settings)

    assert [item.ticker for item in selected] == ["AAA", "CCC"]
    assert not missing


def test_discovery_without_profile_filter_skips_semantic_model(tmp_path, monkeypatch) -> None:
    approved = ApprovedResearchPlan(
        question="Find undervalued software stocks",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "candidate_discovery", "company_profile"),
        analysis_ids=(),
        mode="discovery",
        discovery_scope="Software",
        discovery_theme=None,
        result_count=2,
    ).validated()
    result = ResearchResult(plan=ResearchPlan(mode="screen", workflow="stock_screen"))
    result.tables["screen"] = pd.DataFrame(
        [
            {"Instrument": "AAA.O", "TR.TickerSymbol": "AAA", "TR.CommonName": "Alpha"},
            {"Instrument": "BBB.O", "TR.TickerSymbol": "BBB", "TR.CommonName": "Beta"},
        ]
    )
    monkeypatch.setattr(
        "portfolio.research_lab.invoke_structured_groq",
        lambda *_args, **_kwargs: pytest.fail("semantic classification should not run"),
    )
    settings = Settings(tmp_path, tmp_path / "db.sqlite", "key", "test-model", "desktop.workspace")

    selected, missing = _select_theme_candidates(result, approved, settings)

    assert [item.ticker for item in selected] == ["AAA", "BBB"]
    assert not missing


def test_discovery_execution_researches_only_semantically_selected_rics(
    tmp_path, monkeypatch
) -> None:
    approved = ApprovedResearchPlan(
        question="Find AI stocks",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "candidate_discovery", "company_profile"),
        analysis_ids=(),
        mode="discovery",
        discovery_scope="Technology",
        discovery_theme="AI",
        result_count=1,
    ).validated()
    settings = Settings(tmp_path, tmp_path / "db.sqlite", None, "test-model", "desktop.workspace")
    screen_result = ResearchResult(plan=ResearchPlan(mode="screen", workflow="stock_screen"))
    monkeypatch.setattr("portfolio.research_lab._run_discovery_screen", lambda *_args, **_kwargs: screen_result)
    monkeypatch.setattr(
        "portfolio.research_lab._select_theme_candidates",
        lambda *_args, **_kwargs: (
            [ThemeCandidate("NVDA.O", "NVDA", "NVIDIA", "direct", "AI is core.", "Summary")],
            [],
        ),
    )
    captured = []

    def fake_run(plan, *_args, **_kwargs):
        captured.append(plan)
        resolved = ResolvedInstrument("NVDA.O", "NVDA.O", "NVDA", "NVDA.O", "NVIDIA")
        result = ResearchResult(plan=plan, resolved=[resolved])
        result.tables["profile"] = pd.DataFrame(
            {"Instrument": ["NVDA.O"], "TR.BusinessSummary": ["NVIDIA develops computing platforms."]}
        )
        return result

    monkeypatch.setattr("portfolio.research_lab.run_research", fake_run)

    output = execute_research(approved, settings, _macro())

    assert captured[0].entities == ["NVDA.O"]
    assert any(item.finding_id == "DISCOVERY_NVDA" for item in output.findings)


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
