from pathlib import Path

from portfolio.config import Settings
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
