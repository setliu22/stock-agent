from pathlib import Path

import pytest

from portfolio.config import Settings
from portfolio.lseg_research import build_screen_expression
from portfolio.research_planner import (
    ResearchClarificationNeeded,
    ResearchPlan,
    ScreenFilters,
    UnsupportedResearchConstraint,
    build_research_plan,
)


def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path, tmp_path / "db.sqlite", None, "test", "desktop.workspace")


def test_company_request_is_planned_without_hard_coded_ticker(tmp_path) -> None:
    plan = build_research_plan("Analyze Palantir", settings(tmp_path))
    assert plan.mode == "company"
    assert plan.entities == ["Palantir"]
    assert "valuation" in plan.topics
    assert "estimates" in plan.topics


def test_company_request_strips_research_preposition(tmp_path) -> None:
    plan = build_research_plan("do some research on zbra", settings(tmp_path))
    assert plan.entities == ["zbra"]
    assert plan.intent_resolution["llm_used"] is False


def test_structurally_ambiguous_security_request_requires_semantic_planning(tmp_path) -> None:
    with pytest.raises(ResearchClarificationNeeded):
        build_research_plan("research a stock whose identifier may be zbra", settings(tmp_path))


def test_unconstrained_stock_request_does_not_silently_run_market_cap_screen(tmp_path) -> None:
    with pytest.raises(ResearchClarificationNeeded):
        build_research_plan("research stocks", settings(tmp_path))


def test_comparison_request_extracts_two_entities(tmp_path) -> None:
    plan = build_research_plan(
        "Compare Nvidia and AMD on valuation and profitability",
        settings(tmp_path),
    )
    assert plan.mode == "compare"
    assert plan.entities == ["Nvidia", "AMD"]
    assert set(plan.topics) == {"valuation", "profitability"}


def test_screen_request_extracts_filters(tmp_path) -> None:
    plan = build_research_plan(
        "Screen top 12 US Technology companies with market cap above $10B and forward P/E below 40",
        settings(tmp_path),
    )
    assert plan.mode == "screen"
    assert plan.screen.limit == 12
    assert plan.screen.country_code == "US"
    assert plan.screen.sector == "Technology"
    assert plan.screen.market_cap_min == 10_000_000_000
    assert plan.screen.forward_pe_max == 40


@pytest.mark.parametrize(
    "wording",
    [
        "P/E no higher than 15",
        "maximum P/E of 15",
        "max P/E 15",
        "P/E capped at 15",
        "P/E at or below 15",
    ],
)
def test_common_maximum_pe_wording_is_not_dropped(tmp_path, wording) -> None:
    plan = build_research_plan(
        f"screen industrial stocks with {wording}",
        settings(tmp_path),
    )
    assert plan.screen.pe_max == 15


def test_result_limit_wording_is_compiled_and_enterprise_value_is_not_an_objective(tmp_path) -> None:
    limited = build_research_plan(
        "screen industrial stocks with P/E below 15 and return 7 results",
        settings(tmp_path),
    )
    assert limited.screen.limit == 7

    ev_screen = build_research_plan(
        "list industrial stocks with enterprise value to EBITDA below 10",
        settings(tmp_path),
    )
    assert ev_screen.screen.ev_ebitda_max == 10
    assert ev_screen.selection_objectives == []
    assert ev_screen.workflow == "stock_screen"

    explicit_candidate = build_research_plan(
        "find top 15 promising industrial stocks",
        settings(tmp_path),
    )
    assert explicit_candidate.screen.limit == 15
    assert explicit_candidate.screen.limit_explicit is True

    show_me = build_research_plan(
        "show me 7 industrial stocks",
        settings(tmp_path),
    )
    assert show_me.screen.limit == 7


@pytest.mark.parametrize(
    "wording",
    [
        "list industrial stocks for a long-term investment horizon",
        "list industrial stocks for investment research",
    ],
)
def test_bare_investment_wording_is_not_candidate_intent(tmp_path, wording) -> None:
    plan = build_research_plan(wording, settings(tmp_path))
    assert plan.workflow == "stock_screen"
    assert plan.selection_objectives == []


@pytest.mark.parametrize(
    "wording",
    [
        "list industrial stocks with investment-grade debt",
        "list industrial stocks with institutional investment ownership",
    ],
)
def test_unexecutable_screen_filters_or_topics_are_rejected(tmp_path, wording) -> None:
    with pytest.raises(UnsupportedResearchConstraint, match="not ignored|cannot be executed"):
        build_research_plan(wording, settings(tmp_path))


@pytest.mark.parametrize(
    "wording",
    [
        "find 10% dividend yield industrial stocks",
        "find 10%-yielding industrial stocks",
        "find 5 billion market cap industrial stocks",
        "show 20 P/E industrial stocks",
        "list 3-month return 7% industrial stocks",
        "list industrial stocks sorted by P/E",
        "show industrial stocks with the lowest forward P/E",
        "find industrial stocks ranked by dividend yield",
    ],
)
def test_unsupported_metric_shorthand_or_ordering_is_never_misread_as_limit(
    tmp_path,
    wording,
) -> None:
    with pytest.raises(UnsupportedResearchConstraint, match="shorthand|ordering"):
        build_research_plan(wording, settings(tmp_path))


def test_result_limit_and_supported_metric_can_coexist(tmp_path) -> None:
    plan = build_research_plan(
        "find 7 industrial stocks with 3-month return above 10%",
        settings(tmp_path),
    )

    assert plan.screen.limit == 7
    assert plan.screen.total_return_3m_min == 10


@pytest.mark.parametrize(
    "wording",
    [
        "what are best practices for researching industrial stocks?",
        "research whether industrial stocks are a good hedge",
        "study strong dollar risks for industrial stocks",
        "analyze industrial stocks before I buy bonds",
        "what makes an industrial stock attractive?",
        "research the best way to value industrial stocks",
    ],
)
def test_conceptual_sector_wording_is_not_compiled_as_candidate_screen(tmp_path, wording) -> None:
    with pytest.raises(ResearchClarificationNeeded):
        build_research_plan(wording, settings(tmp_path))


def test_plain_screen_rejects_topics_it_would_not_retrieve(tmp_path) -> None:
    with pytest.raises(UnsupportedResearchConstraint, match="cannot be executed"):
        build_research_plan("study biotech stocks and recent news", settings(tmp_path))


def test_semantic_candidate_wording_requires_semantic_provider(tmp_path) -> None:
    with pytest.raises(ResearchClarificationNeeded):
        build_research_plan(
            "research a sector company that might be a good investment",
            settings(tmp_path),
        )


def test_llm_sector_alias_is_normalized() -> None:
    plan = ResearchPlan(
        mode="screen",
        workflow="sector_opportunity",
        screen=ScreenFilters(sector="industrial", candidate_search=True),
    ).normalized()
    assert plan.screen.sector == "Industrials"


def test_study_routes_to_biotech_screen_and_study_company_resolves_entity(tmp_path) -> None:
    biotech = build_research_plan("can you study biotech stocks", settings(tmp_path))
    assert biotech.workflow == "stock_screen"
    assert biotech.entities == []
    assert biotech.screen.sector == "Healthcare"
    assert biotech.screen.industry == "Biotechnology & Medical Research"
    assert 'IN(TR.TRBCIndustryCode,"56202010")' in build_screen_expression(biotech.screen)

    company = build_research_plan("study Apple", settings(tmp_path))
    assert company.workflow == "company_deep_dive"
    assert company.entities == ["Apple"]


def test_lowercase_us_is_country_only_in_explicit_stock_geography(tmp_path) -> None:
    explicit = build_research_plan("study us stocks", settings(tmp_path))
    modified = build_research_plan("study only us stocks", settings(tmp_path))
    pronoun = build_research_plan("show us biotech stocks", settings(tmp_path))

    assert explicit.screen.country_code == "US"
    assert modified.screen.country_code == "US"
    assert pronoun.screen.country_code is None
    assert pronoun.screen.industry == "Biotechnology & Medical Research"


def test_candidate_request_without_peer_group_names_missing_constraint(tmp_path) -> None:
    with pytest.raises(
        ResearchClarificationNeeded,
        match="supported sector or industry.*coherent peer group",
    ):
        build_research_plan(
            "do some research on a promising us stock",
            settings(tmp_path),
        )


def test_esg_request_is_rejected_before_lseg_when_entitlement_is_disabled(tmp_path) -> None:
    with pytest.raises(
        UnsupportedResearchConstraint,
        match="ESG retrieval is disabled.*required entitlement",
    ):
        build_research_plan("analyze Apple's ESG profile", settings(tmp_path))


def test_contextual_screen_change_requires_semantic_provider(tmp_path) -> None:
    prior = build_research_plan("study biotech stocks", settings(tmp_path))
    with pytest.raises(ResearchClarificationNeeded):
        build_research_plan("change the previous screen", settings(tmp_path), prior_plan=prior)
