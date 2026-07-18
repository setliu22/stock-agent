from __future__ import annotations

from datetime import date

from portfolio.agent import StockAgent
from portfolio.config import Settings
from portfolio.database import PortfolioDatabase
from portfolio.models import Purchase


def test_show_holdings_without_network(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "portfolio.db",
        groq_api_key=None,
        groq_model="test-model",
        lseg_session_name="desktop.workspace",
    )
    database = PortfolioDatabase(settings.database_path)
    database.record_purchase(Purchase("MSFT", 2, 50, date(2026, 1, 1)))
    agent = StockAgent(settings, database)
    text = agent.show_holdings()
    assert "MSFT" in text
    assert "2 shares" in text


def test_research_failure_does_not_substitute_yahoo_snapshot(tmp_path, monkeypatch) -> None:
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    database = PortfolioDatabase(settings.database_path)
    agent = StockAgent(settings, database)

    monkeypatch.setattr("portfolio.agent.run_research", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("closed")))
    text = agent.research("research a utilities company")
    assert "No Yahoo quote snapshot was substituted" in text
    assert "Exxon" not in text


def test_research_forwards_live_progress_updates(tmp_path, monkeypatch) -> None:
    from portfolio.research_planner import ResearchPlan

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    database = PortfolioDatabase(settings.database_path)
    agent = StockAgent(settings, database)
    events: list[tuple[int | None, str, str]] = []

    class Result:
        metrics = {"api_call_count": 3}
        calls = ["one", "two", "three"]

    monkeypatch.setattr(
        "portfolio.agent.build_research_plan",
        lambda *_args, **_kwargs: ResearchPlan(mode="company", entities=["Palantir"], workflow="company_deep_dive"),
    )

    def fake_run(_plan, _settings, progress_callback=None, cancel_event=None):
        assert progress_callback is not None
        progress_callback(50, "Retrieving evidence", "Testing a live update.")
        return Result()

    monkeypatch.setattr("portfolio.agent.run_research", fake_run)
    monkeypatch.setattr("portfolio.agent.concise_report", lambda *_args, **_kwargs: "Finished report")

    text = agent.research("analyze Palantir", progress_callback=lambda p, s, d: events.append((p, s, d)))
    assert text == "Finished report"
    assert any(stage == "Retrieving evidence" for _percent, stage, _detail in events)
    assert events[-1][0] == 100
    assert events[-1][1] == "Research complete"


def test_research_returns_stopped_when_cancelled(tmp_path, monkeypatch) -> None:
    import threading
    from portfolio.lseg_research import ResearchCancelled
    from portfolio.research_planner import ResearchPlan

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    database = PortfolioDatabase(settings.database_path)
    agent = StockAgent(settings, database)
    event = threading.Event()

    monkeypatch.setattr(
        "portfolio.agent.build_research_plan",
        lambda *_args, **_kwargs: ResearchPlan(mode="company", entities=["Palantir"], workflow="company_deep_dive"),
    )

    def cancelled_run(*_args, **_kwargs):
        raise ResearchCancelled("Research stopped by user.")

    monkeypatch.setattr("portfolio.agent.run_research", cancelled_run)
    event.set()
    text = agent.research("analyze Palantir", cancel_event=event)
    assert text.startswith("Research stopped.")


def test_contextual_follow_up_uses_prior_research(tmp_path, monkeypatch) -> None:
    from portfolio.company_resolver import ResolvedInstrument
    from portfolio.lseg_research import ResearchResult
    from portfolio.research_planner import ResearchPlan, ScreenFilters

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    plan = ResearchPlan(
        mode="screen",
        workflow="sector_opportunity",
        screen=ScreenFilters(sector="Industrials", country_code="US", candidate_search=True),
    ).normalized()
    result = ResearchResult(
        plan=plan,
        resolved=[ResolvedInstrument("UPS", "UPS", "UPS", "UPS.N", "United Parcel Service")],
    )
    import pandas as pd

    result.tables["screen_universe"] = pd.DataFrame({
        "Instrument": ["UPS.N", "PAYX.O", "A.N", "B.N", "C.N", "D.N"],
        "TR.PtoEPSMeanEst(Period=FY1)": [15.0, 20.0, 21.0, 22.0, 23.0, 24.0],
    })
    result.tables["screen"] = result.tables["screen_universe"].head(2).copy()
    result.tables["profile"] = pd.DataFrame({"Instrument": ["UPS.N"], "TR.CommonName": ["United Parcel Service"]})
    result.tables["valuation"] = pd.DataFrame({
        "Instrument": ["UPS.N"],
        "TR.PtoEPSMeanEst(Period=FY1)": [15.0],
    })
    monkeypatch.setattr("portfolio.agent.build_research_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr("portfolio.agent.run_research", lambda *_args, **_kwargs: result)
    monkeypatch.setattr("portfolio.agent.concise_report", lambda *_args, **_kwargs: "Candidate: UPS")

    assert agent.handle("find a promising industrials stock") == "Candidate: UPS"
    follow_up = agent.handle("why is this company undervalued?")
    assert "United Parcel Service" in follow_up
    assert "forward P/E is 15" in follow_up
    assert "versus 22" in follow_up
    assert "definitively undervalued" in follow_up


def test_study_geography_follow_up_reruns_lseg_with_prior_biotech_context(
    tmp_path, monkeypatch
) -> None:
    from portfolio.lseg_research import ResearchResult, build_screen_expression

    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-groq-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    plans = []

    def fake_run(plan, *_args, **_kwargs):
        plans.append(plan)
        return ResearchResult(plan=plan)

    monkeypatch.setattr("portfolio.agent.run_research", fake_run)
    monkeypatch.setattr(
        "portfolio.agent.concise_report",
        lambda result, *_args, **_kwargs: f"LSEG screen: {build_screen_expression(result.plan.screen)}",
    )
    monkeypatch.setattr(
        agent,
        "_general_chat",
        lambda _text: (_ for _ in ()).throw(AssertionError("research must not use generic chat")),
    )
    monkeypatch.setattr(
        "portfolio.agent.answer_follow_up",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a constraint refinement must rerun LSEG")
        ),
    )

    first = agent.handle("can you study biotech stocks")
    second = agent.handle("study us stocks")

    assert len(plans) == 2
    assert 'IN(TR.TRBCIndustryCode,"56202010")' in first
    assert 'IN(TR.HQCountryCode,"US")' in second
    assert 'IN(TR.TRBCIndustryCode,"56202010")' in second
    assert plans[1].screen.country_code == "US"
    assert plans[1].screen.industry == "Biotechnology & Medical Research"
    assert plans[1].context_parent_request == "can you study biotech stocks"


def test_failed_contextual_refinement_discards_prior_result(tmp_path, monkeypatch) -> None:
    from portfolio.lseg_research import LSEGNoMatches, ResearchResult

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    calls = 0

    def fake_run(plan, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise LSEGNoMatches("No U.S. matches")
        return ResearchResult(plan=plan)

    monkeypatch.setattr("portfolio.agent.run_research", fake_run)
    monkeypatch.setattr("portfolio.agent.concise_report", lambda *_args, **_kwargs: "Biotech screen")

    assert agent.handle("can you study biotech stocks") == "Biotech screen"
    failure = agent.handle("study us stocks")
    assert failure.startswith("No adequately supported company was found")
    assert agent._last_research_result is None


def test_unrelated_turn_prevents_stale_screen_inheritance(tmp_path, monkeypatch) -> None:
    from portfolio.lseg_research import ResearchResult

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    plans = []

    def fake_run(plan, *_args, **_kwargs):
        plans.append(plan)
        return ResearchResult(plan=plan)

    monkeypatch.setattr("portfolio.agent.run_research", fake_run)
    monkeypatch.setattr("portfolio.agent.concise_report", lambda *_args, **_kwargs: "Screen")
    monkeypatch.setattr(agent, "_general_chat", lambda _text: "General response")

    assert agent.handle("study biotech stocks") == "Screen"
    assert agent.handle("hello") == "General response"
    assert agent.handle("study US stocks") == "Screen"

    assert len(plans) == 2
    assert plans[1].screen.country_code == "US"
    assert plans[1].screen.sector is None
    assert plans[1].screen.industry is None
    assert plans[1].context_parent_request is None


def test_lseg_capability_request_precedes_screen_refinement(tmp_path, monkeypatch) -> None:
    from portfolio.lseg_research import ResearchResult

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    plans = []

    def fake_run(plan, *_args, **_kwargs):
        plans.append(plan)
        return ResearchResult(plan=plan)

    monkeypatch.setattr("portfolio.agent.run_research", fake_run)
    monkeypatch.setattr("portfolio.agent.concise_report", lambda *_args, **_kwargs: "Screen")
    monkeypatch.setattr("portfolio.agent.capability_answer", lambda *_args, **_kwargs: "Capabilities")

    assert agent.handle("study biotech stocks") == "Screen"
    assert agent.handle("research LSEG capabilities for stocks") == "Capabilities"
    assert len(plans) == 1
    assert agent._last_research_result is None
