from pathlib import Path

from portfolio.config import Settings
from portfolio.lseg_research import build_screen_expression
from portfolio.research_planner import ResearchPlan, ScreenFilters, build_research_plan


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
    assert canadian.screen.industry == "Biotechnology & Medical Research"

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
        "US stocks instead",
    ):
        plan = build_research_plan(text, settings(tmp_path), prior_plan=prior)
        assert plan.screen.country_code == "US", text
        assert plan.screen.industry == "Biotechnology & Medical Research", text
