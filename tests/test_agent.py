from __future__ import annotations

from datetime import date

import pytest

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

    metric_follow_up = agent.handle("what is the forward pe of this company")
    assert metric_follow_up == (
        "The retrieved forward P/E for United Parcel Service (UPS.N) is 15."
    )


def test_request_failure_follow_up_uses_prior_trace(tmp_path, monkeypatch) -> None:
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
        call_records=[
            {"request_number": 1, "label": "Screen universe", "status": "succeeded"},
            {
                "request_number": 2,
                "label": "Reuters story urn:newsml:example",
                "status": "failed",
                "error_type": "LDError",
            },
        ],
        warnings=[
            "Reuters story urn:newsml:example: LDError: Error code 403 | access denied. "
            "Missing scope: trapi.data.esg.views-basic.read."
        ],
    )
    monkeypatch.setattr("portfolio.agent.build_research_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr("portfolio.agent.run_research", lambda *_args, **_kwargs: result)
    monkeypatch.setattr("portfolio.agent.concise_report", lambda *_args, **_kwargs: "Candidate: Example")
    monkeypatch.setattr(
        agent,
        "_general_chat",
        lambda _text: (_ for _ in ()).throw(AssertionError("general chat must not run")),
    )

    assert agent.handle("find a promising us stock in the industrial sector") == "Candidate: Example"
    follow_up = agent.handle(
        "you said 1 lseg request completed do you know which one didn't"
    )

    assert "1 of 2 recorded LSEG requests succeeded" in follow_up
    assert "request #2" in follow_up
    assert "Reuters story urn:newsml:example" in follow_up
    assert "failed (LDError:" in follow_up
    assert "access denied" in follow_up

    task_wording = agent.handle("1 succeeded which task failed")
    assert "request #2" in task_wording
    assert "Reuters story urn:newsml:example" in task_wording

    analysis_wording = agent.handle("why did only 1 out of 2 analyses run")
    assert "1 of 2 recorded LSEG requests succeeded" in analysis_wording
    assert "request #2" in analysis_wording
    assert "Reuters story urn:newsml:example" in analysis_wording


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
    monkeypatch.setattr(
        agent,
        "_general_chat",
        lambda _text: "General response",
    )

    assert agent.handle("study biotech stocks") == "Screen"
    assert agent.handle("hello") == "General response"
    assert agent._last_research_result is not None
    assert agent.handle("study US stocks") == "Screen"

    assert len(plans) == 2
    assert plans[1].screen.country_code == "US"
    assert plans[1].screen.sector is None
    assert plans[1].screen.industry is None
    assert plans[1].context_parent_request is None


def test_unrelated_company_question_does_not_receive_prior_research(tmp_path, monkeypatch) -> None:
    import sys
    from types import SimpleNamespace
    from portfolio.company_resolver import ResolvedInstrument
    from portfolio.lseg_research import ResearchResult
    from portfolio.research_planner import ResearchPlan

    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    agent._last_research_result = ResearchResult(
        plan=ResearchPlan(mode="company", entities=["QCOM"]),
        resolved=[ResolvedInstrument("QCOM", "QCOM", "QCOM", "QCOM.O", "QUALCOMM Incorporated")],
    )
    calls = []

    class FakeChatGroq:
        def __init__(self, **_kwargs):
            pass

        def invoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content="Zebra Technologies makes enterprise tracking products.")

    monkeypatch.setitem(sys.modules, "langchain_groq", SimpleNamespace(ChatGroq=FakeChatGroq))

    response = agent.handle("what does zbra do")

    assert response.startswith("Zebra Technologies")
    assert len(calls) == 1
    assert calls[0][-1] == ("human", "what does zbra do")
    assert "QCOM" not in str(calls[0])
    assert "QUALCOMM" not in str(calls[0])


def test_general_chat_keeps_only_one_turn_for_pronoun_follow_up(tmp_path, monkeypatch) -> None:
    import sys
    from types import SimpleNamespace

    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    calls = []
    answers = iter(["Zebra makes tracking hardware.", "It also sells software and services."])

    class FakeChatGroq:
        def __init__(self, **_kwargs):
            pass

        def invoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content=next(answers))

    monkeypatch.setitem(sys.modules, "langchain_groq", SimpleNamespace(ChatGroq=FakeChatGroq))

    agent.handle("what does zbra do")
    response = agent.handle("what products does it make?")

    assert response.startswith("It also")
    assert [role for role, _content in calls[1]] == ["system", "human", "assistant", "human"]
    assert calls[1][1][1] == "what does zbra do"


def test_operational_turn_breaks_general_chat_memory(tmp_path, monkeypatch) -> None:
    import sys
    from types import SimpleNamespace

    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    calls = []

    class FakeChatGroq:
        def __init__(self, **_kwargs):
            pass

        def invoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content="Answer")

    monkeypatch.setitem(sys.modules, "langchain_groq", SimpleNamespace(ChatGroq=FakeChatGroq))

    agent.handle("what does zbra do")
    agent.handle("show holdings")
    agent.handle("what products does it make?")

    assert len(calls) == 2
    assert [role for role, _content in calls[-1]] == ["system", "human"]


def test_general_chat_retries_oversized_context_without_memory(tmp_path, monkeypatch) -> None:
    import sys
    from types import SimpleNamespace
    from portfolio.agent import ConversationTurn

    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    agent._recent_chat.append(ConversationTurn("Tell me about ZBRA", "A" * 3_000))
    calls = []

    class TooLarge(Exception):
        status_code = 413

    class FakeChatGroq:
        def __init__(self, **_kwargs):
            pass

        def invoke(self, messages):
            calls.append(messages)
            if len(messages) > 2:
                raise TooLarge("Request too large")
            return SimpleNamespace(content="ZBRA faces execution risk.")

    monkeypatch.setitem(sys.modules, "langchain_groq", SimpleNamespace(ChatGroq=FakeChatGroq))

    response = agent._general_chat("what risks does it have?")

    assert response == "ZBRA faces execution risk."
    assert [len(messages) for messages in calls] == [4, 2]


def test_general_chat_hides_raw_oversized_provider_error(tmp_path, monkeypatch) -> None:
    import sys
    from types import SimpleNamespace

    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))

    class TooLarge(Exception):
        status_code = 413

    class FakeChatGroq:
        def __init__(self, **_kwargs):
            pass

        def invoke(self, _messages):
            raise TooLarge("organization secret details; request too large")

    monkeypatch.setitem(sys.modules, "langchain_groq", SimpleNamespace(ChatGroq=FakeChatGroq))

    response = agent._general_chat("Explain diversification")

    assert "too large" in response.casefold()
    assert "organization secret details" not in response


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
    assert agent._last_research_result is not None


def test_ambiguous_screen_shorthand_reaches_contextual_planner(tmp_path, monkeypatch) -> None:
    from portfolio.lseg_research import ResearchResult
    from portfolio.research_planner import ResearchPlan, ScreenFilters

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    prior_plan = ResearchPlan(
        mode="screen",
        workflow="stock_screen",
        screen=ScreenFilters(industry="Biotechnology & Medical Research"),
        raw_request="study biotech stocks",
    ).normalized()
    assert not StockAgent._is_screen_refinement("why is this company undervalued?", prior_plan)
    assert not StockAgent._is_screen_refinement("study global stocks", prior_plan)

    def exercise(refinement: str) -> None:
        agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
        agent._last_research_result = ResearchResult(plan=prior_plan)
        agent._screen_refinement_available = True
        planned_with = []
        run_calls = []

        def fake_build(query, _settings, prior_plan=None):
            planned_with.append((query, prior_plan))
            return ResearchPlan(
                mode="screen",
                workflow="stock_screen",
                screen=ScreenFilters(
                    industry="Biotechnology & Medical Research",
                    country_code="US",
                ),
                raw_request=query,
            ).normalized()

        def fake_run(plan, *_args, **_kwargs):
            run_calls.append(plan)
            return ResearchResult(plan=plan)

        monkeypatch.setattr("portfolio.agent.build_research_plan", fake_build)
        monkeypatch.setattr("portfolio.agent.run_research", fake_run)
        monkeypatch.setattr("portfolio.agent.concise_report", lambda *_args, **_kwargs: "US biotech screen")

        assert agent.handle(refinement) == "US biotech screen"
        assert planned_with == [(refinement, prior_plan)]
        assert len(run_calls) == 1

    for refinement in ("focus on names headquartered stateside", "what about American names?"):
        exercise(refinement)


def test_planner_clarification_returns_without_lseg_call(tmp_path, monkeypatch) -> None:
    from portfolio.research_planner import ResearchClarificationNeeded

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    events = []

    def needs_clarification(*_args, **_kwargs):
        raise ResearchClarificationNeeded("Do you mean U.S.-listed or U.S.-headquartered stocks?")

    monkeypatch.setattr("portfolio.agent.build_research_plan", needs_clarification)
    monkeypatch.setattr(
        "portfolio.agent.run_research",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LSEG must not run")),
    )
    monkeypatch.setattr(
        agent,
        "_general_chat",
        lambda _text: (_ for _ in ()).throw(AssertionError("clarification must not use general chat")),
    )

    response = agent.handle(
        "research American stocks",
        progress_callback=lambda percent, stage, detail: events.append((percent, stage, detail)),
    )

    assert response == (
        "I need one clarification before running LSEG research: "
        "Do you mean U.S.-listed or U.S.-headquartered stocks?"
    )
    assert events[-1][1] == "Clarification needed"


def test_missing_peer_group_clarification_is_actionable(tmp_path, monkeypatch) -> None:
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    monkeypatch.setattr(
        "portfolio.agent.run_research",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LSEG must not run")),
    )

    response = agent.handle("do some research on a promising us stock")

    assert "supported sector or industry" in response
    assert "coherent peer group" in response
    assert "could not validate the interpreted wording" not in response


@pytest.mark.parametrize(
    ("reply", "expected_sector", "expected_industry"),
    [
        ("biotech", "Healthcare", "Biotechnology & Medical Research"),
        ("industrials", "Industrials", None),
        ("semiconductor equipment", "Technology", "Semiconductor Equipment"),
    ],
)
def test_terse_reply_completes_pending_research_clarification(
    tmp_path,
    monkeypatch,
    reply,
    expected_sector,
    expected_industry,
) -> None:
    from portfolio.lseg_research import ResearchResult

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    plans = []

    def fake_run(plan, *_args, **_kwargs):
        plans.append(plan)
        return ResearchResult(plan=plan)

    monkeypatch.setattr("portfolio.agent.run_research", fake_run)
    monkeypatch.setattr("portfolio.agent.concise_report", lambda *_args, **_kwargs: "Candidate")
    monkeypatch.setattr(
        agent,
        "_general_chat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a clarification reply must not use general chat")
        ),
    )

    first = agent.handle("research a promising us stock")
    second = agent.handle(reply)

    assert "need a supported sector or industry" in first
    assert second == "Candidate"
    assert len(plans) == 1
    assert plans[0].screen.country_code == "US"
    assert plans[0].screen.sector == expected_sector
    assert plans[0].screen.industry == expected_industry
    assert plans[0].screen.candidate_search is True
    assert agent._pending_research_query is None


def test_misspelled_research_action_still_starts_pending_pipeline(tmp_path, monkeypatch) -> None:
    from portfolio.lseg_research import ResearchResult
    from portfolio.research_planner import (
        ResearchClarificationNeeded,
        ResearchPlan,
        ScreenFilters,
    )

    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    plans = []

    def fake_build(query, *_args, **_kwargs):
        if "User clarification:" not in query:
            raise ResearchClarificationNeeded("Please describe the stock universe.")
        return ResearchPlan(
            mode="screen",
            workflow="sector_opportunity",
            screen=ScreenFilters(
                country_code="US",
                sector="Healthcare",
                industry="Biotechnology & Medical Research",
                candidate_search=True,
            ),
            raw_request=query,
        ).normalized()

    monkeypatch.setattr("portfolio.agent.build_research_plan", fake_build)
    monkeypatch.setattr(
        "portfolio.agent.run_research",
        lambda plan, *_args, **_kwargs: plans.append(plan) or ResearchResult(plan=plan),
    )
    monkeypatch.setattr("portfolio.agent.concise_report", lambda *_args, **_kwargs: "Candidate")
    monkeypatch.setattr(
        agent,
        "_general_chat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a typo-tolerant research request must not use general chat")
        ),
    )

    first = agent.handle("reserach promising stock")
    second = agent.handle("us stock in biotech")

    assert "clarification" in first.casefold()
    assert second == "Candidate"
    assert len(plans) == 1
    assert plans[0].screen.country_code == "US"
    assert plans[0].screen.industry == "Biotechnology & Medical Research"


@pytest.mark.parametrize(
    "wording",
    [
        "reseach a promising stock",
        "anlyze an undervalued company",
        "reviwe a stock",
        "scren biotech companies",
    ],
)
def test_research_action_typo_variants_reach_research_router(
    tmp_path,
    monkeypatch,
    wording,
) -> None:
    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    seen = []
    monkeypatch.setattr(
        agent,
        "research",
        lambda query, **_kwargs: seen.append(query) or "Research routed",
    )

    assert agent.handle(wording) == "Research routed"
    assert seen == [wording]


def test_planner_general_route_uses_general_chat_without_lseg_call(tmp_path, monkeypatch) -> None:
    from portfolio.research_planner import NotResearchRequest

    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    general_queries = []

    def not_research(*_args, **_kwargs):
        raise NotResearchRequest("This is a general question.")

    monkeypatch.setattr("portfolio.agent.build_research_plan", not_research)
    monkeypatch.setattr(
        "portfolio.agent.run_research",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LSEG must not run")),
    )
    monkeypatch.setattr(
        agent,
        "_general_chat",
        lambda text: general_queries.append(text) or "General answer",
    )

    assert agent.handle("research what diversification means") == "General answer"
    assert general_queries == ["research what diversification means"]


def test_ambiguous_equity_wording_and_terse_context_reach_planner(tmp_path, monkeypatch) -> None:
    from portfolio.research_planner import ResearchPlan, ScreenFilters

    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    seen = []
    monkeypatch.setattr(
        agent,
        "research",
        lambda query, **_kwargs: seen.append(query) or "planned",
    )

    assert agent.handle("hunt for an underappreciated industrial name") == "planned"
    assert seen == ["hunt for an underappreciated industrial name"]

    prior = ResearchPlan(
        mode="screen",
        workflow="stock_screen",
        screen=ScreenFilters(industry="Biotechnology & Medical Research"),
        raw_request="study biotech stocks",
    ).normalized()
    assert StockAgent._is_screen_refinement("American names?", prior)
    assert StockAgent._is_screen_refinement("stateside names, please", prior)
    for wording in (
        "show 7 stocks",
        "show me 7 stocks",
        "give me 7 stocks",
        "first 7 stocks",
        "show 7 names",
        "show me 7 names",
    ):
        assert StockAgent._is_screen_refinement(wording, prior), wording


def test_contextual_clarification_preserves_prior_screen_without_lseg(tmp_path, monkeypatch) -> None:
    from portfolio.lseg_research import ResearchResult
    from portfolio.research_planner import ResearchClarificationNeeded, ResearchPlan, ScreenFilters

    settings = Settings(tmp_path, tmp_path / "portfolio.db", "fake-key", "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    prior_plan = ResearchPlan(
        mode="screen",
        workflow="stock_screen",
        screen=ScreenFilters(industry="Biotechnology & Medical Research", country_code="CA"),
        raw_request="study Canadian biotech stocks",
    ).normalized()
    prior_result = ResearchResult(plan=prior_plan)
    agent._last_research_result = prior_result
    agent._screen_refinement_available = True

    monkeypatch.setattr(
        "portfolio.agent.build_research_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ResearchClarificationNeeded("Which country should domestic refer to?")
        ),
    )
    monkeypatch.setattr(
        "portfolio.agent.run_research",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LSEG must not run")),
    )

    response = agent.handle("focus on domestic stocks")

    assert "Which country" in response
    assert agent._last_research_result is prior_result
    assert agent._screen_refinement_available is True
    assert StockAgent._is_screen_refinement("US-headquartered names", prior_plan)


def test_broad_but_guarded_candidate_wording_reaches_semantic_planner(tmp_path, monkeypatch) -> None:
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    seen = []
    monkeypatch.setattr(agent, "research", lambda query, **_kwargs: seen.append(query) or "planned")

    requests = (
        "find a good industrial company",
        "which industrial stock looks undervalued?",
        "surface an overlooked industrial stock",
        "zero in on a compelling industrial name",
        "find an undervalued biotech play",
        "find a cheap chipmaker",
        "find a compelling bank",
    )
    for request in requests:
        assert agent.handle(request) == "planned", request

    assert seen == list(requests)


def test_natural_geography_followups_reach_prior_screen_planner() -> None:
    from portfolio.research_planner import ResearchPlan, ScreenFilters

    prior = ResearchPlan(
        mode="screen",
        workflow="stock_screen",
        screen=ScreenFilters(industry="Biotechnology & Medical Research"),
        raw_request="study biotech stocks",
    ).normalized()

    for wording in (
        "only US names",
        "those headquartered in the US",
        "same but American",
        "how about US names",
        "restrict that to US companies",
        "focus only on US names",
    ):
        assert StockAgent._is_screen_refinement(wording, prior), wording
