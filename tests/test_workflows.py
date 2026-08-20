from pathlib import Path

import pandas as pd

from portfolio.company_resolver import ResolvedInstrument
from portfolio.config import Settings
from portfolio.lseg_capabilities import EXECUTABLE_OPERATIONS
from portfolio.lseg_research import ResearchResult, _plain_text_report, concise_report
from portfolio.research_planner import ResearchPlan, ScreenFilters, build_research_plan
from portfolio.research_workflows import get_workflow


def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path, tmp_path / "db.sqlite", None, "test", "desktop.workspace")


def test_sector_request_compiles_to_fixed_workflow(tmp_path) -> None:
    plan = build_research_plan("research a promising industrials stock", settings(tmp_path))
    assert plan.workflow == "sector_opportunity"
    workflow = get_workflow(plan.workflow, plan.mode, candidate_search=True)
    assert [stage.stage_id for stage in workflow.stages] == [
        "universe", "ranking", "shortlist", "deep_dive", "synthesis"
    ]
    assert workflow.deep_dive_candidates == 5


def test_operation_registry_is_read_only_and_explicit() -> None:
    assert len(EXECUTABLE_OPERATIONS) >= 15
    assert all(operation.autonomous_read for operation in EXECUTABLE_OPERATIONS)
    assert any(operation.operation_id == "news.story" for operation in EXECUTABLE_OPERATIONS)
    assert any(operation.operation_id == "local.multifactor_rank" for operation in EXECUTABLE_OPERATIONS)


def test_position_review_retrieves_a_bounded_broader_news_packet() -> None:
    workflow = get_workflow("position_review", "compare")

    assert workflow.mode == "compare"
    assert workflow.deep_dive_candidates == 8
    assert workflow.news_stories_per_candidate == 5


def test_candidate_report_uses_deep_dive_evidence(tmp_path) -> None:
    plan = ResearchPlan(
        mode="screen",
        workflow="sector_opportunity",
        screen=ScreenFilters(sector="Industrials", candidate_search=True),
    ).normalized()
    candidate = ResolvedInstrument("UPS", "UPS", "UPS", "UPS.N", "United Parcel Service")
    alternative = ResolvedInstrument("PAYX", "PAYX", "PAYX", "PAYX.O", "Paychex")
    result = ResearchResult(plan=plan, resolved=[candidate, alternative])
    result.tables["screen"] = pd.DataFrame(
        {
            "Instrument": ["UPS.N", "PAYX.O"],
            "TR.CommonName": ["United Parcel Service", "Paychex"],
            "TR.PtoEPSMeanEst(Period=FY1)": [15.0, 25.0],
            "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM": [35.0, 20.0],
            "TR.DividendYield": [4.0, 2.0],
            "Value Discount Count": [2, 1],
            "Macro Fit": ["Supportive", "Neutral"],
        }
    )
    result.tables["profile"] = pd.DataFrame(
        {"Instrument": ["UPS.N", "PAYX.O"], "TR.CommonName": ["United Parcel Service", "Paychex"]}
    )
    result.tables["valuation"] = pd.DataFrame(
        {"Instrument": ["UPS.N", "PAYX.O"], "TR.PtoEPSMeanEst(Period=FY1)": [15.0, 25.0]}
    )
    result.tables["profitability"] = pd.DataFrame(
        {
            "Instrument": ["UPS.N", "PAYX.O"],
            "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM": [35.0, 20.0],
        }
    )
    result.metrics.update(
        {
            "screen_universe_count": 120,
            "deep_dive_count": 5,
            "UPS.N:target_upside": 0.14,
            "UPS.N:eps_revision_30d": 0.03,
            "UPS.N:annualized_vol": 0.22,
            "UPS.N:evidence_families": ["profile", "valuation", "profitability", "estimates", "price_history", "news"],
        }
    )
    text = concise_report(result, settings(tmp_path))
    assert "Candidate: United Parcel Service (UPS.N)" in text
    assert "Opportunity:" in text
    assert "Major risks:" in text
    assert "screened 120 companies" in text


def test_markdown_is_removed_for_tkinter() -> None:
    text = _plain_text_report("**Candidate:** UPS\n* Risk one\n## Coverage")
    assert "**" not in text
    assert "##" not in text
    assert "• Risk one" in text
