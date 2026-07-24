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
    from portfolio.research_planner import NotResearchRequest

    with pytest.raises(NotResearchRequest):
        build_research_plan(wording, settings(tmp_path))


def test_plain_screen_rejects_topics_it_would_not_retrieve(tmp_path) -> None:
    with pytest.raises(UnsupportedResearchConstraint, match="cannot be executed"):
        build_research_plan("study biotech stocks and recent news", settings(tmp_path))


def test_unspecified_sector_investment_request_becomes_candidate_screen(tmp_path) -> None:
    plan = build_research_plan(
        "do some research on a utilities company that might be a good investment",
        settings(tmp_path),
    )
    assert plan.mode == "screen"
    assert plan.entities == []
    assert plan.screen.sector == "Utilities"
    assert plan.screen.candidate_search is True
    assert plan.screen.sort_by == "quality_value"


def test_bargain_buy_in_industrial_sector_uses_sector_opportunity(tmp_path) -> None:
    plan = build_research_plan(
        "Can you do some research on a potential bargain buy in the industrial sector?",
        settings(tmp_path),
    )
    assert plan.workflow == "sector_opportunity"
    assert plan.mode == "screen"
    assert plan.screen.sector == "Industrials"
    assert plan.screen.candidate_search is True


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


@pytest.mark.parametrize(
    "wording",
    [
        "do some research on a promising us industrials stock",
        "find an undervalued us technology company",
        "screen attractive us healthcare equities",
        "research one well-covered us financial stock",
    ],
)
def test_lowercase_us_as_noun_phrase_modifier_is_geography(tmp_path, wording) -> None:
    plan = build_research_plan(wording, settings(tmp_path))
    assert plan.screen.country_code == "US"


@pytest.mark.parametrize(
    "wording",
    [
        "show us biotech stocks",
        "tell us about industrial stocks",
        "help us find technology companies",
        "give us an overview of healthcare equities",
    ],
)
def test_lowercase_us_as_recipient_remains_a_pronoun(tmp_path, wording) -> None:
    plan = build_research_plan(wording, settings(tmp_path))
    assert plan.screen.country_code is None


def test_candidate_request_without_peer_group_names_missing_constraint(tmp_path) -> None:
    with pytest.raises(
        ResearchClarificationNeeded,
        match="supported sector or industry.*coherent peer group",
    ):
        build_research_plan(
            "do some research on a promising us stock",
            settings(tmp_path),
        )


@pytest.mark.parametrize(
    "wording",
    [
        "do analyses of stocc. find most promising one in us biotech industry",
        "do analysis of stoc. select best one in us biotech industry",
        "take a look and find the strongest candidate in us biotech industry",
    ],
)
def test_generic_candidate_description_compiles_as_taxonomy_screen(tmp_path, wording) -> None:
    plan = build_research_plan(wording, settings(tmp_path))

    assert plan.mode == "screen"
    assert plan.workflow == "sector_opportunity"
    assert plan.entities == []
    assert plan.screen.country_code == "US"
    assert plan.screen.industry == "Biotechnology & Medical Research"
    assert plan.screen.candidate_search is True
    assert "positive_signals" in plan.selection_objectives


def test_contextual_screen_refinement_inherits_and_replaces_dimensions(tmp_path) -> None:
    biotech = build_research_plan("can you study biotech stocks", settings(tmp_path))
    us_biotech = build_research_plan("study us stocks", settings(tmp_path), prior_plan=biotech)

    expression = build_screen_expression(us_biotech.screen)
    assert us_biotech.planner == "deterministic_contextual"
    assert us_biotech.context_parent_request == "can you study biotech stocks"
    assert us_biotech.to_dict()["effective_request"] == (
        "can you study biotech stocks study us stocks"
    )
    assert us_biotech.screen.country_code == "US"
    assert us_biotech.screen.industry == "Biotechnology & Medical Research"
    assert 'IN(TR.HQCountryCode,"US")' in expression
    assert 'IN(TR.TRBCIndustryCode,"56202010")' in expression

    technology = build_research_plan(
        "study technology stocks instead", settings(tmp_path), prior_plan=us_biotech
    )
    assert technology.screen.country_code == "US"
    assert technology.screen.sector == "Technology"
    assert technology.screen.industry is None

    canadian = build_research_plan(
        "study Canadian stocks instead", settings(tmp_path), prior_plan=us_biotech
    )
    assert canadian.screen.country_code == "CA"
    assert canadian.screen.industry is None

    candidate = build_research_plan(
        "find a promising undervalued biotech stock under $500M market cap",
        settings(tmp_path),
    )
    candidate_us = build_research_plan(
        "study us stocks", settings(tmp_path), prior_plan=candidate
    )
    assert candidate_us.workflow == "sector_opportunity"
    assert candidate_us.screen.country_code == "US"
    assert candidate_us.screen.market_cap_max == 500_000_000
    assert candidate_us.screen.candidate_search is True
    assert "promising undervalued" in candidate_us.effective_request

    all_stocks = build_research_plan(
        "study all US stocks", settings(tmp_path), prior_plan=candidate
    )
    assert all_stocks.workflow == "stock_screen"
    assert all_stocks.screen.country_code == "US"
    assert all_stocks.screen.sector is None
    assert all_stocks.screen.industry is None
    assert all_stocks.screen.market_cap_max is None
    assert all_stocks.screen.candidate_search is False
    assert all_stocks.screen.limit == 15
    assert all_stocks.screen.sort_by == "market_cap"

    fresh = build_research_plan(
        "start a new screen for US technology stocks",
        settings(tmp_path),
        prior_plan=candidate,
    )
    assert fresh.planner == "deterministic"
    assert fresh.context_parent_request is None
    assert fresh.screen.country_code == "US"
    assert fresh.screen.sector == "Technology"
    assert fresh.screen.market_cap_max is None
    assert fresh.screen.candidate_search is False

    global_screen = build_research_plan(
        "study global stocks", settings(tmp_path), prior_plan=us_biotech
    )
    assert global_screen.screen.country_code is None
    assert global_screen.screen.sector is None
    assert global_screen.screen.industry is None


def test_contextual_screen_action_variants_compile(tmp_path) -> None:
    prior = build_research_plan("study biotech stocks", settings(tmp_path))
    for text in (
        "focus on us stocks",
        "narrow to US stocks",
        "filter for US stocks",
        "what about US stocks?",
    ):
        plan = build_research_plan(text, settings(tmp_path), prior_plan=prior)
        assert plan.screen.country_code == "US", text
        assert plan.screen.industry == "Biotechnology & Medical Research", text

    replacement = build_research_plan("US stocks instead", settings(tmp_path), prior_plan=prior)
    assert replacement.screen.country_code == "US"
    assert replacement.screen.industry is None
