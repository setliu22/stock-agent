from datetime import date, datetime, timezone
import json

import pandas as pd
import pytest

from portfolio.company_resolver import ResolvedInstrument
from portfolio.config import Settings
from portfolio.lseg_research import ResearchResult
from portfolio.market_regime import MarketRegimeSnapshot, Observation, RegimeIndicator
from portfolio.research_lab import (
    CAPABILITIES,
    DISCOVERY_CORE_CAPABILITY_IDS,
    ApprovedResearchPlan,
    ResearchLabError,
    ThemeCandidate,
    VerifiedFinding,
    _proposal_model_catalog,
    _proposal_schema,
    _theme_scope_schema,
    _run_discovery_screen,
    _run_discovery_screens,
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
        "mode": "named",
        "securities": ["AAPL", "MSFT"],
        "discovery_scopes": [],
        "exchange_geography": None,
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
    legacy_scope = overrides.pop("discovery_scope", None) if "discovery_scope" in overrides else None
    payload.update(overrides)
    if legacy_scope is not None:
        payload["discovery_scopes"] = [legacy_scope]
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
    assert {item["entity_source"] for item in catalog["modes"]} == {
        "user_question",
        "lseg_screen",
        "none",
    }
    assert {item["value"] for item in catalog["exchange_geographies"]} >= {
        "United States",
        "Europe",
    }


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

    assert proposal.mode == "discovery"
    assert proposal.securities == ()
    assert proposal.discovery_scope == "Technology"
    assert proposal.discovery_theme == "AI revolution"
    assert {item.item_id for item in proposal.capabilities} >= {
        *DISCOVERY_CORE_CAPABILITY_IDS,
    }


def test_cross_industry_theme_can_propose_multiple_validated_universes() -> None:
    proposal = validate_proposal_payload(
        "Which companies are undervalued and related to data centers?",
        _payload(
            mode="discovery",
            securities=[],
            discovery_scopes=["Technology", "Industrials", "Utilities", "Real Estate"],
            discovery_theme="data centers",
            capabilities=[
                {"id": "candidate_discovery", "reason": "Discover candidates."},
                {"id": "company_profile", "reason": "Validate exposure."},
                {"id": "valuation_snapshot", "reason": "Compare valuations."},
            ],
            analyses=[],
        ),
    )

    assert proposal.discovery_scopes == (
        "Technology",
        "Industrials",
        "Utilities",
        "Real Estate",
    )
    assert proposal.discovery_scope_reasons == ()


def test_approved_discovery_plan_always_includes_core_evidence() -> None:
    approved = ApprovedResearchPlan(
        question="Find undervalued software companies",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "candidate_discovery", "company_profile"),
        analysis_ids=(),
        mode="discovery",
        discovery_scopes=("Software",),
        result_count=5,
    ).validated()

    assert approved.capability_ids[: len(DISCOVERY_CORE_CAPABILITY_IDS)] == (
        DISCOVERY_CORE_CAPABILITY_IDS
    )


def test_proposal_schema_uses_only_supported_array_constraints() -> None:
    scopes_schema = _proposal_schema()["properties"]["discovery_scopes"]

    assert "uniqueItems" not in scopes_schema
    assert scopes_schema["maxItems"] == 6
    assert _theme_scope_schema()["properties"]["universes"]["maxItems"] == 6


def test_proposal_model_catalog_omits_schema_and_execution_metadata() -> None:
    catalog = _proposal_model_catalog()

    assert set(catalog) == {"modes", "capabilities", "analyses"}
    assert catalog["capabilities"]["company_profile"]
    assert "backend_operations" not in json.dumps(catalog)
    assert len(json.dumps(catalog)) < 6_000


def test_supported_industry_name_uses_normal_screen_without_theme_filter() -> None:
    proposal = validate_proposal_payload(
        "What semiconductor companies are undervalued?",
        _payload(
            mode="discovery",
            securities=[],
            discovery_scope="Semiconductors",
            discovery_theme="semiconductor",
            capabilities=[
                {"id": "candidate_discovery", "reason": "Discover candidates."},
                {"id": "company_profile", "reason": "Identify companies."},
                {"id": "valuation_snapshot", "reason": "Compare valuations."},
            ],
            analyses=[],
        ),
    )

    assert proposal.discovery_scopes == ("Semiconductors",)
    assert proposal.discovery_theme is None


def test_all_public_equities_cannot_be_mixed_with_narrower_universes() -> None:
    with pytest.raises(ResearchLabError, match="cannot be combined"):
        validate_proposal_payload(
            "Find companies related to data centers across the market",
            _payload(
                mode="discovery",
                securities=[],
                discovery_scopes=["All public equities", "Technology"],
                discovery_theme="data centers",
                capabilities=[
                    {"id": "candidate_discovery", "reason": "Discover candidates."},
                    {"id": "company_profile", "reason": "Validate exposure."},
                ],
                analyses=[],
            ),
        )


@pytest.mark.parametrize(
    ("question", "geography"),
    [
        ("What US stock is undervalued in industrials?", "United States"),
        ("What European stock is undervalued in industrials?", "Europe"),
    ],
)
def test_discovery_proposal_preserves_grounded_exchange_geography(
    question: str,
    geography: str,
) -> None:
    proposal = validate_proposal_payload(
        question,
        _payload(
            mode="discovery",
            securities=[],
            discovery_scope="Industrials",
            exchange_geography=geography,
            discovery_theme=None,
            result_count=1,
            capabilities=[
                {"id": "candidate_discovery", "reason": "Discover candidates."},
                {"id": "company_profile", "reason": "Identify candidates."},
                {"id": "valuation_snapshot", "reason": "Compare valuations."},
            ],
            analyses=[],
        ),
    )

    assert proposal.exchange_geography == geography


def test_discovery_proposal_rejects_invented_exchange_geography() -> None:
    with pytest.raises(ResearchLabError, match="explicitly present"):
        validate_proposal_payload(
            "What stock is undervalued in industrials?",
            _payload(
                mode="discovery",
                securities=[],
                discovery_scope="Industrials",
                exchange_geography="United States",
                discovery_theme=None,
                capabilities=[
                    {"id": "candidate_discovery", "reason": "Discover candidates."},
                    {"id": "company_profile", "reason": "Identify candidates."},
                ],
                analyses=[],
            ),
        )


def test_discovery_screen_expands_europe_to_exchange_country_codes(
    tmp_path,
    monkeypatch,
) -> None:
    approved = ApprovedResearchPlan(
        question="What European stock is undervalued in industrials?",
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
        discovery_scope="Industrials",
        exchange_geography="Europe",
        result_count=1,
    ).validated()
    captured: list[ResearchPlan] = []

    def fake_run(plan, *_args, **_kwargs):
        captured.append(plan)
        return ResearchResult(plan=plan)

    monkeypatch.setattr("portfolio.research_lab.run_research", fake_run)
    settings = Settings(
        tmp_path,
        tmp_path / "db.sqlite",
        None,
        "test-model",
        "desktop.workspace",
    )

    _run_discovery_screen(approved, settings, _macro(), progress_callback=None, cancel_event=None)

    assert captured[0].screen.sector == "Industrials"
    assert {"DE", "FR", "GB", "IT", "NL"} <= set(
        captured[0].screen.exchange_country_codes
    )
    assert "US" not in captured[0].screen.exchange_country_codes


def test_multiple_discovery_screens_are_interleaved_and_deduplicated(
    tmp_path,
    monkeypatch,
) -> None:
    approved = ApprovedResearchPlan(
        question="Find companies related to data centers",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "candidate_discovery", "company_profile"),
        analysis_ids=(),
        mode="discovery",
        discovery_scopes=("Technology", "Industrials"),
        discovery_theme="data centers",
        result_count=3,
    ).validated()

    def fake_screen(_approved, *_args, scope=None, **_kwargs):
        rows = {
            "Technology": ["TECH1.O", "SHARED.O", "TECH2.O"],
            "Industrials": ["IND1.N", "SHARED.O", "IND2.N"],
        }[scope]
        result = ResearchResult(plan=ResearchPlan(mode="screen", workflow="stock_screen"))
        result.tables["screen"] = pd.DataFrame(
            {"Instrument": rows, "TR.CommonName": rows}
        )
        return result

    monkeypatch.setattr("portfolio.research_lab._run_discovery_screen", fake_screen)
    settings = Settings(
        tmp_path,
        tmp_path / "db.sqlite",
        None,
        "test-model",
        "desktop.workspace",
    )

    result = _run_discovery_screens(
        approved,
        settings,
        _macro(),
        progress_callback=None,
        cancel_event=None,
    )

    assert result.tables["screen"]["Instrument"].tolist() == [
        "TECH1.O",
        "IND1.N",
        "SHARED.O",
        "TECH2.O",
        "IND2.N",
    ]


def test_exact_open_ended_question_can_propose_discovery(tmp_path, monkeypatch) -> None:
    question = "which stocks are best poised to take advantage of the AI revolution?"
    calls = []

    def fake_invoke(_settings, schema, _messages, **_kwargs):
        calls.append(schema["title"])
        if schema["title"] == "ThemeUniverseAudit":
            return {
                "universes": [
                    {"scope": "Technology", "reason": "Enabling products."},
                    {"scope": "Industrials", "reason": "Physical infrastructure."},
                    {"scope": "Energy", "reason": "Energy inputs."},
                ]
            }
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
    assert proposal.discovery_scopes == ("Technology", "Industrials", "Energy")
    assert len(proposal.discovery_scope_reasons) == 3
    assert proposal.securities == ()
    assert calls == ["ResearchCapabilityProposal", "ThemeUniverseAudit"]


def test_schema_rejected_generation_recovers_missing_optional_analyses(
    tmp_path,
    monkeypatch,
) -> None:
    failed = _payload(
        securities=["AAPL"],
        capabilities=[{"id": "company_profile", "reason": "Describe the company."}],
        analyses=[],
    )
    failed.pop("analyses")

    class SchemaFailure(RuntimeError):
        body = {
            "error": {
                "code": "json_validate_failed",
                "failed_generation": json.dumps(failed),
            }
        }

    monkeypatch.setattr(
        "portfolio.research_lab.invoke_structured_groq",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SchemaFailure("invalid schema output")),
    )
    settings = Settings(
        tmp_path,
        tmp_path / "db.sqlite",
        "key",
        "test-model",
        "desktop.workspace",
    )

    proposal = propose_research("Research AAPL", settings)

    assert proposal.securities == ("AAPL",)
    assert proposal.analyses == ()


def test_empty_schema_failure_uses_validated_json_mode_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    calls = []

    class EmptySchemaFailure(RuntimeError):
        body = {
            "error": {
                "code": "json_validate_failed",
                "failed_generation": "",
            }
        }

    def fake_invoke(_settings, _schema, messages, **kwargs):
        calls.append(kwargs.get("method", "json_schema"))
        if len(calls) == 1:
            raise EmptySchemaFailure("Failed to validate JSON")
        assert "JSON object" in messages[0][1]
        return _payload(
            securities=["AAPL"],
            capabilities=[{"id": "company_profile", "reason": "Describe the company."}],
            analyses=[],
        )

    monkeypatch.setattr("portfolio.research_lab.invoke_structured_groq", fake_invoke)
    settings = Settings(
        tmp_path,
        tmp_path / "db.sqlite",
        "key",
        "test-model",
        "desktop.workspace",
    )

    proposal = propose_research("Research AAPL", settings)

    assert proposal.securities == ("AAPL",)
    assert calls == ["json_schema", "json_mode"]


def test_invalid_entity_source_is_replanned_from_compiler_error(tmp_path, monkeypatch) -> None:
    question = "what semiconducctor companies are still undervalued?"
    payloads = [
        _payload(securities=[]),
        _payload(
            mode="discovery",
            securities=[],
            discovery_scope="Semiconductors",
            discovery_theme=None,
            capabilities=[
                {"id": "candidate_discovery", "reason": "Retrieve candidates."},
                {"id": "company_profile", "reason": "Identify candidates."},
                {"id": "valuation_snapshot", "reason": "Compare valuation evidence."},
            ],
            analyses=[],
        ),
    ]
    requests = []

    def fake_invoke(_settings, _schema, messages, **_kwargs):
        requests.append(json.loads(messages[-1][1]))
        return payloads.pop(0)

    monkeypatch.setattr("portfolio.research_lab.invoke_structured_groq", fake_invoke)
    settings = Settings(tmp_path, tmp_path / "db.sqlite", "key", "test-model", "desktop.workspace")

    proposal = propose_research(question, settings)

    assert proposal.mode == "discovery"
    assert proposal.discovery_scope == "Semiconductors"
    assert "previous_plan_compiler_error" in requests[1]


def test_missing_rate_input_is_replanned_before_approval(tmp_path, monkeypatch) -> None:
    question = "Compare AAPL and MSFT when rates fall"
    rate_analysis = [
        {"id": "falling_rate_comparison", "reason": "Compare falling-rate periods."}
    ]
    payloads = [
        _payload(
            capabilities=[
                {"id": "price_history", "reason": "Retrieve stock returns."}
            ],
            analyses=rate_analysis,
        ),
        _payload(
            capabilities=[
                {"id": "price_history", "reason": "Retrieve stock returns."},
                {"id": "fed_funds_history", "reason": "Measure falling rates."},
            ],
            analyses=rate_analysis,
        ),
    ]
    requests = []

    def fake_invoke(_settings, _schema, messages, **_kwargs):
        requests.append(json.loads(messages[-1][1]))
        return payloads.pop(0)

    monkeypatch.setattr("portfolio.research_lab.invoke_structured_groq", fake_invoke)
    settings = Settings(tmp_path, tmp_path / "db.sqlite", "key", "test-model", "desktop.workspace")

    proposal = propose_research(question, settings)

    assert {item.item_id for item in proposal.capabilities} >= {
        "price_history",
        "fed_funds_history",
    }
    assert "Choose one rate measure" in requests[1]["previous_plan_compiler_error"]


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
    with pytest.raises(ResearchLabError, match="Choose one rate measure"):
        plan.validated()


def test_rate_analysis_contract_is_exposed_to_the_proposal_model() -> None:
    analyses = {item["id"]: item for item in proposal_catalog()["analyses"]}

    assert analyses["falling_rate_comparison"]["requires_exactly_one"] == [
        "fed_funds_history",
        "treasury_yield_history",
    ]


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


def test_theme_selection_batches_candidates_without_losing_earlier_results(
    tmp_path,
    monkeypatch,
) -> None:
    approved = ApprovedResearchPlan(
        question="Find companies related to data centers",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "candidate_discovery", "company_profile"),
        analysis_ids=(),
        mode="discovery",
        discovery_scopes=("Technology", "Industrials"),
        discovery_theme="data centers",
        result_count=8,
    ).validated()
    result = ResearchResult(plan=ResearchPlan(mode="screen", workflow="stock_screen"))
    result.tables["screen"] = pd.DataFrame(
        [
            {
                "Instrument": f"RIC{index}.N",
                "TR.TickerSymbol": f"T{index}",
                "TR.CommonName": f"Company {index}",
                "TR.BusinessSummary": "Provides infrastructure used by data centers.",
                "Discovery scope": "Technology" if index % 2 else "Industrials",
            }
            for index in range(12)
        ]
    )
    requests = []

    def fake_invoke(_settings, _schema, messages, **_kwargs):
        request = json.loads(messages[-1][1])
        requests.append(request)
        return {
            "matches": [
                {
                    "candidate_id": item["candidate_id"],
                    "relevance": "direct",
                    "reason": "The retrieved profile explicitly supports the theme.",
                }
                for item in request["candidates"]
            ]
        }

    monkeypatch.setattr("portfolio.research_lab.invoke_structured_groq", fake_invoke)
    settings = Settings(
        tmp_path,
        tmp_path / "db.sqlite",
        "key",
        "test-model",
        "desktop.workspace",
    )

    selected, _missing = _select_theme_candidates(result, approved, settings)

    assert len(requests) == 2
    assert [item.ticker for item in selected] == [f"T{index}" for index in range(8)]


def test_theme_selection_splits_a_batch_after_provider_json_truncation(
    tmp_path,
    monkeypatch,
) -> None:
    approved = ApprovedResearchPlan(
        question="Find companies related to data centers",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "candidate_discovery", "company_profile"),
        analysis_ids=(),
        mode="discovery",
        discovery_scopes=("Technology",),
        discovery_theme="data centers",
        result_count=4,
    ).validated()
    result = ResearchResult(plan=ResearchPlan(mode="screen", workflow="stock_screen"))
    result.tables["screen"] = pd.DataFrame(
        [
            {
                "Instrument": f"RIC{index}.N",
                "TR.TickerSymbol": f"T{index}",
                "TR.CommonName": f"Company {index}",
                "TR.BusinessSummary": "Provides infrastructure used by data centers.",
                "Discovery scope": "Technology",
            }
            for index in range(8)
        ]
    )
    batch_sizes = []

    class JsonGenerationFailure(RuntimeError):
        body = {"error": {"code": "json_validate_failed"}}

    def fake_invoke(_settings, _schema, messages, **_kwargs):
        request = json.loads(messages[-1][1])
        candidates = request["candidates"]
        batch_sizes.append(len(candidates))
        if len(candidates) == 5:
            raise JsonGenerationFailure("max completion tokens reached")
        return {
            "matches": [
                {
                    "candidate_id": item["candidate_id"],
                    "relevance": "direct",
                    "reason": "The retrieved profile explicitly supports the theme.",
                }
                for item in candidates
            ]
        }

    monkeypatch.setattr("portfolio.research_lab.invoke_structured_groq", fake_invoke)
    settings = Settings(tmp_path, tmp_path / "db.sqlite", "key", "test-model", "desktop.workspace")

    selected, missing = _select_theme_candidates(result, approved, settings)

    assert batch_sizes == [5, 2, 3]
    assert [item.ticker for item in selected] == ["T0", "T1", "T2", "T3"]
    assert not missing


def test_theme_selection_rejects_incomplete_classifier_output(
    tmp_path,
    monkeypatch,
) -> None:
    approved = ApprovedResearchPlan(
        question="Find companies related to wind turbines",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "candidate_discovery", "company_profile"),
        analysis_ids=(),
        mode="discovery",
        discovery_scopes=("Industrials",),
        discovery_theme="wind turbines",
        result_count=2,
    ).validated()
    result = ResearchResult(plan=ResearchPlan(mode="screen", workflow="stock_screen"))
    result.tables["screen"] = pd.DataFrame(
        [
            {
                "Instrument": "AAA.N",
                "TR.TickerSymbol": "AAA",
                "TR.CommonName": "Alpha",
                "TR.BusinessSummary": "Alpha manufactures wind turbine components.",
                "Discovery scope": "Industrials",
            },
            {
                "Instrument": "BBB.N",
                "TR.TickerSymbol": "BBB",
                "TR.CommonName": "Beta",
                "TR.BusinessSummary": "Beta maintains renewable power equipment.",
                "Discovery scope": "Industrials",
            },
        ]
    )

    def incomplete_response(_settings, _schema, messages, **_kwargs):
        request = json.loads(messages[-1][1])
        assert "screen_evidence" not in request["candidates"][0]
        return {
            "matches": [
                {
                    "candidate_id": "C1",
                    "relevance": "direct",
                    "reason": "The retrieved profile explicitly names turbine components.",
                }
            ]
        }

    monkeypatch.setattr(
        "portfolio.research_lab.invoke_structured_groq",
        incomplete_response,
    )
    settings = Settings(
        tmp_path,
        tmp_path / "db.sqlite",
        "key",
        "test-model",
        "desktop.workspace",
    )

    with pytest.raises(ResearchLabError, match="did not classify every supplied candidate"):
        _select_theme_candidates(result, approved, settings)


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


def test_discovery_candidate_keeps_peer_relative_valuation_evidence(
    tmp_path,
) -> None:
    approved = ApprovedResearchPlan(
        question="Find undervalued industrial stocks",
        securities=(),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "candidate_discovery", "company_profile"),
        analysis_ids=(),
        mode="discovery",
        discovery_scopes=("Industrials",),
        result_count=1,
    ).validated()
    result = ResearchResult(plan=ResearchPlan(mode="screen", workflow="stock_screen"))
    result.tables["screen"] = pd.DataFrame(
        [
            {
                "Instrument": "AAA.N",
                "TR.TickerSymbol": "AAA",
                "TR.CommonName": "Alpha",
                "Discovery scope": "Industrials",
                "TR.PtoEPSMeanEst(Period=FY1)": 12.0,
                "TR.EVToEBITDA": 8.0,
                "Evidence Family Count": 5,
            }
        ]
    )
    result.metrics["cohort_statistics_by_scope"] = {
        "Industrials": {
            "TR.PtoEPSMeanEst(Period=FY1)": {"median": 18.0},
            "TR.EVToEBITDA": {"median": 11.0},
        }
    }
    settings = Settings(
        tmp_path,
        tmp_path / "db.sqlite",
        None,
        "test-model",
        "desktop.workspace",
    )

    selected, _missing = _select_theme_candidates(result, approved, settings)

    assert "forward P/E 12" in selected[0].screen_evidence
    assert "Industrials median 18" in selected[0].screen_evidence


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
    monkeypatch.setattr("portfolio.research_lab._run_discovery_screens", lambda *_args, **_kwargs: screen_result)
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


def test_macro_finding_contains_all_standardized_signal_context() -> None:
    approved = ApprovedResearchPlan(
        question="Research AAPL",
        securities=("AAPL",),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context",),
        analysis_ids=(),
    ).validated()
    labels = (
        "Effective federal funds rate",
        "Federal Reserve assets",
        "Consumer Price Index inflation",
        "US high-yield option-adjusted spread",
        "CBOE Volatility Index",
    )
    snapshot = MarketRegimeSnapshot(
        regime="Mixed liquidity regime",
        summary="Core signals disagree.",
        emphasis=(),
        indicators=tuple(
            RegimeIndicator(
                key=f"signal_{index}",
                label=label,
                latest=f"{index + 1}.0",
                trend="Stable",
                as_of="2026-08-27",
                source="FRED" if index < 4 else "Yahoo Finance ^VIX",
                meaning="Use as context.",
                macro_tilt="Neutral",
            )
            for index, label in enumerate(labels)
        ),
        missing_evidence=(),
        generated_at=datetime.now(timezone.utc),
        company_fit="Favor profitable growth with manageable debt.",
    )

    findings, _missing = derive_findings(
        approved,
        None,
        snapshot,
        {},
        primary_count=1,
    )

    macro_text = findings[0].text
    assert all(label in macro_text for label in labels)
    assert "Application rules:" in macro_text
    assert "Stock profile to prioritize:" in macro_text


def test_model_summary_cannot_omit_macro_context(tmp_path, monkeypatch) -> None:
    approved = ApprovedResearchPlan(
        question="Research AAPL",
        securities=("AAPL",),
        lookback_days=365,
        benchmark=None,
        capability_ids=("macro_context", "company_profile"),
        analysis_ids=(),
    ).validated()
    findings = [
        VerifiedFinding("MACRO_REGIME", "Macro", "Neutral liquidity.", ("FRED",)),
        VerifiedFinding("PROFILE_AAPL", "Profile", "Technology company.", ("LSEG",)),
    ]
    monkeypatch.setattr(
        "portfolio.research_lab.invoke_structured_groq",
        lambda *_args, **_kwargs: {
            "highlights": [
                {
                    "finding_id": "PROFILE_AAPL",
                    "interpretation": "The profile establishes the company identity.",
                }
            ],
            "caveats": [],
        },
    )
    settings = Settings(
        tmp_path,
        tmp_path / "db.sqlite",
        "key",
        "test-model",
        "desktop.workspace",
    )

    report = summarize_findings(approved, findings, [], settings)

    assert "PROFILE_AAPL" in report
    assert "MACRO_REGIME" in report
