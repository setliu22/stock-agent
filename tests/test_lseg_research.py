from __future__ import annotations

import json
import pandas as pd

from portfolio.company_resolver import ResolvedInstrument
from portfolio.config import Settings
from portfolio.lseg_research import (
    ResearchResult,
    _canonicalize,
    _extract_values,
    _open_lseg_session,
    _persist_research_trace,
    apply_screen_filters,
    build_screen_body,
    build_screen_expression,
    concise_report,
)
from portfolio.research_plan import ResearchPlan, ScreenFilters


def test_research_diagnostic_trace_omits_raw_question_and_result_tables(tmp_path) -> None:
    settings = Settings(
        tmp_path,
        tmp_path / "portfolio.db",
        None,
        "test-model",
        "desktop.workspace",
    )
    plan = ResearchPlan(
        mode="company",
        workflow="research_lab",
        entities=["AAPL"],
        topics=["profile"],
        raw_request="private question about AAPL",
    ).normalized()
    result = ResearchResult(plan=plan)
    result.tables["profile"] = pd.DataFrame(
        {"Instrument": ["AAPL.O"], "TR.BusinessSummary": ["licensed profile text"]}
    )
    result.call_records.append(
        {
            "label": "Market news",
            "status": "failed",
            "request": {
                "operation": "news.get_headlines",
                "query": "private question about AAPL",
            },
            "error_message": "private question about AAPL was rejected",
        }
    )

    _persist_research_trace(result, settings, "success")

    path = tmp_path / "data" / "research_diagnostics.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    encoded = json.dumps(payload)
    assert "request" not in payload
    assert "normalized_plan" not in payload
    assert "private question" not in encoded
    assert "licensed profile text" not in encoded
    assert payload["request_fingerprint"]
    assert payload["call_records"][0]["request"]["query_fingerprint"]
    assert payload["call_records"][0]["error_message"] == "[request] was rejected"


def test_extract_values_maps_returned_columns_to_requested_fields() -> None:
    frame = pd.DataFrame(
        {
            "Instrument": ["PLTR.O"],
            "Company Common Name": ["Palantir Technologies Inc"],
            "Price Close": [150.0],
        }
    )
    result = _extract_values(frame, ("TR.CommonName", "TR.PriceClose"))
    assert result == {"TR.CommonName": "Palantir Technologies Inc", "TR.PriceClose": 150.0}


def test_screen_expression_and_local_filters() -> None:
    filters = ScreenFilters(
        market_cap_min=10_000_000_000,
        forward_pe_max=40,
        country_code="US",
        sector="Technology",
        limit=5,
    )
    expression = build_screen_expression(filters)
    assert "TR.CompanyMarketCap>=10000000000" in expression
    assert 'IN(TR.HQCountryCode,"US")' in expression
    assert expression.startswith("SCREEN(")
    assert 'IN(TR.TRBCEconSectorCode,"57")' in expression

    frame = pd.DataFrame(
        {
            "TR.CommonName": ["A", "B", "C"],
            "TR.HQCountryCode": ["US", "US", "US"],
            "TR.TRBCEconSectorCode": ["57", "57", "57"],
            "TR.CompanyMarketCap": [20e9, 9e9, 30e9],
            "TR.PtoEPSMeanEst(Period=FY1)": [30, 20, 50],
        }
    )
    output = apply_screen_filters(frame, filters)
    assert output["TR.CommonName"].tolist() == ["A"]


def test_exchange_geography_compiles_and_is_validated_locally() -> None:
    filters = ScreenFilters(
        exchange_country_codes=("US", "CA"),
        sector="Industrials",
        limit=5,
    )

    expression = build_screen_expression(filters)
    assert 'IN(TR.ExchangeCountryCode,"US","CA")' in expression

    frame = pd.DataFrame(
        {
            "TR.CommonName": ["US listing", "Canadian listing", "European listing"],
            "TR.ExchangeCountryCode": ["US", "CA", "DE"],
            "TR.TRBCEconSectorCode": ["52", "52", "52"],
        }
    )
    output = apply_screen_filters(frame, filters)

    assert output["TR.CommonName"].tolist() == ["US listing", "Canadian listing"]


def test_exchange_country_display_header_is_canonicalized() -> None:
    frame = pd.DataFrame(
        {
            "Instrument": ["AAPL.O"],
            "Exchange Country ISO Code": ["US"],
        }
    )

    output = _canonicalize(frame, ("TR.ExchangeCountryCode",))

    assert output.loc[0, "TR.ExchangeCountryCode"] == "US"


def test_concise_company_report_uses_derived_evidence(tmp_path) -> None:
    plan = ResearchPlan(mode="company", entities=["Palantir"], topics=["profile", "valuation"])
    resolved = ResolvedInstrument("Palantir", "Palantir", "PLTR", "PLTR.O", "Palantir Technologies")
    result = ResearchResult(plan=plan, resolved=[resolved])
    result.tables["profile"] = pd.DataFrame(
        {"Instrument": ["PLTR.O"], "TR.CommonName": ["Palantir Technologies"], "TR.PriceClose": [100.0]}
    )
    result.tables["valuation"] = pd.DataFrame(
        {"Instrument": ["PLTR.O"], "TR.PE": [80.0], "TR.PtoEPSMeanEst(Period=FY1)": [60.0]}
    )
    settings = Settings(tmp_path, tmp_path / "db", None, "test", "desktop.workspace")
    text = concise_report(result, settings)
    assert "Palantir Technologies (PLTR.O)" in text
    assert "forward P/E 60" in text


def test_company_news_rejects_tagged_spillover_and_keeps_company_excerpt() -> None:
    import portfolio.lseg_research as module

    resolved = ResolvedInstrument(
        "QCOM", "QCOM", "QCOM", "QCOM.O", "QUALCOMM Incorporated"
    )
    result = ResearchResult(
        plan=ResearchPlan(mode="company", entities=["QCOM"]),
        resolved=[resolved],
    )
    result.tables["news:QCOM.O"] = pd.DataFrame(
        {
            "Headline": [
                "VIKING CUTS SHARE STAKE IN WALT DISNEY",
                "Viking files quarterly holdings update",
                "VIKING RAISES SHARE STAKE IN BOEING",
                "Qualcomm launches next-generation mobile platform",
            ],
            "storyId": ["disney", "filing", "boeing", "qualcomm"],
        }
    )
    stories = {
        "disney": "Viking Global reduced its Walt Disney position by 46 percent.",
        "filing": (
            "The quarterly filing covered several technology investments. "
            "Viking Global increased its QUALCOMM Incorporated position by 12 percent."
        ),
        "boeing": "Viking Global increased its Boeing position during the quarter.",
        "qualcomm": "Qualcomm launched a new mobile platform for premium devices.",
    }

    class News:
        @staticmethod
        def get_story(story_id):
            return stories[story_id]

    class FakeLD:
        news = News()

    client = module._LSEGClient(result, minimum_interval=0)
    module._retrieve_news_stories(FakeLD(), client, result, resolved, limit=2)

    relevant = result.tables["news:QCOM.O"]
    assert relevant["CompanyRelevanceSource"].tolist() == ["story", "headline"]
    assert "QUALCOMM Incorporated position" in relevant.iloc[0]["CompanyRelevantText"]
    assert "Disney" not in " ".join(relevant["CompanyRelevantText"])
    assert "Boeing" not in " ".join(relevant["CompanyRelevantText"])
    assert result.metrics["QCOM.O:news_candidates"] == 4
    assert result.metrics["QCOM.O:news_relevant"] == 2
    report = module._deterministic_company_report(result)
    assert "QUALCOMM Incorporated position" in report
    assert "Disney" not in report
    assert "Boeing" not in report


def test_ambiguous_story_does_not_pass_on_ticker_metadata_alone() -> None:
    import portfolio.lseg_research as module

    resolved = ResolvedInstrument(
        "QCOM", "QCOM", "QCOM", "QCOM.O", "QUALCOMM Incorporated"
    )
    terms = module._news_entity_terms(resolved, include_ticker=False)

    assert module._company_story_excerpt("Related tickers: DIS.N BA.N QCOM.O", terms) is None


def test_candidate_screen_ranks_quality_and_value() -> None:
    filters = ScreenFilters(sector="Utilities", candidate_search=True, limit=3)
    frame = pd.DataFrame(
        {
            "Instrument": ["A.N", "B.N", "C.N"],
            "TR.CommonName": ["A", "B", "C"],
            "TR.TRBCEconomicSector": ["Utilities", "Utilities", "Utilities"],
            "TR.CompanyMarketCap": [100e9, 50e9, 20e9],
            "TR.PtoEPSMeanEst(Period=FY1)": [35, 15, 25],
            "TR.EVToEBITDA": [20, 9, 14],
            "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM": [5, 18, 10],
            "TR.ROAPercentTrailing12M": [2, 8, 5],
            "TR.DividendYield": [1, 4, 3],
            "TR.TotalReturn3Mo": [-5, 8, 2],
        }
    )
    output = apply_screen_filters(frame, filters)
    assert output.iloc[0]["TR.CommonName"] == "B"
    assert output.iloc[0]["Value Discount Count"] > output.iloc[1]["Value Discount Count"]


def test_macro_regime_is_reported_without_changing_valuation_first_ranking() -> None:
    import portfolio.lseg_research as module

    frame = pd.DataFrame(
        {
            "Instrument": ["G.N", "M.N", "D.N"],
            "TR.CommonName": ["Growth", "Middle", "Defensive"],
            "TR.CompanyMarketCap": [30e9, 25e9, 20e9],
            "TR.RevenueMean(Period=FY1)": [100, 100, 100],
            "TR.RevenueMean(Period=FY2)": [150, 120, 105],
            "TR.EPSMean(Period=FY1)": [1, 2, 2],
            "TR.EPSMean(Period=FY2)": [2, 2.4, 2.1],
            "TR.LTGMean": [30, 15, 5],
            "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM": [5, 15, 30],
            "TR.ROAPercentTrailing12M": [2, 8, 15],
            "TR.PretaxMarginPercent(Period=FY0)": [3, 12, 25],
            "TR.PtoEPSMeanEst(Period=FY1)": [40, 20, 10],
            "TR.EVToEBITDA": [25, 14, 7],
            "TR.PriceToSalesPerShare": [10, 5, 2],
            "TR.PriceToBVPerShare": [10, 5, 2],
            "TR.F.DebtTot": [100, 50, 20],
            "TR.FCFMean(Period=FY1)": [2, 10, 30],
            "TR.F.CashCashEquiv": [5, 15, 30],
        }
    )
    easing = module._rank_candidate_screen(frame, "Easing and expanding liquidity")
    tightening = module._rank_candidate_screen(frame, "Tightening and contracting liquidity")

    assert easing.iloc[0]["TR.CommonName"] == "Defensive"
    assert tightening.iloc[0]["TR.CommonName"] == "Defensive"
    assert tightening.iloc[0]["Macro Fit"] == "Supportive"
    assert {"Growth Percentile", "Profitability Percentile", "Valuation Percentile", "Financial Resilience Percentile"}.issubset(
        easing.columns
    )


def test_debt_free_company_keeps_financial_resilience_coverage() -> None:
    import portfolio.lseg_research as module

    frame = pd.DataFrame(
        {
            "Instrument": ["CASH.N", "DEBT.N"],
            "TR.CompanyMarketCap": [10e9, 10e9],
            "TR.F.DebtTot": [0, 100],
            "TR.FCFMean(Period=FY1)": [20, 20],
            "TR.F.CashCashEquiv": [50, 10],
        }
    )

    ranked = module._rank_candidate_screen(frame)

    cash = ranked.loc[ranked["Instrument"] == "CASH.N"].iloc[0]
    debt = ranked.loc[ranked["Instrument"] == "DEBT.N"].iloc[0]
    assert cash["Financial Resilience Percentile"] > debt["Financial Resilience Percentile"]
    assert "financial_resilience" in cash["Evidence Families"]


def test_closed_lseg_session_raises_clear_error(tmp_path) -> None:
    class Session:
        open_state = "Closed"

    class Config:
        def set_param(self, *_args):
            return None

    class FakeLD:
        def get_config(self):
            return Config()

        def close_session(self):
            return None

        def open_session(self, *args, **kwargs):
            return Session()

    settings = Settings(tmp_path, tmp_path / "db", None, "test", "desktop.workspace", None, 0.01)
    import pytest
    with pytest.raises(Exception, match="Open LSEG Workspace, sign in") as error:
        _open_lseg_session(FakeLD(), settings)

    assert "state=Closed" not in str(error.value)


def test_industrials_screen_uses_trbc_code() -> None:
    expression = build_screen_expression(ScreenFilters(sector="Industrials", candidate_search=True))
    assert 'IN(TR.TRBCEconSectorCode,"52")' in expression
    assert "TOP(TR.CompanyMarketCap,200,nnumber)" in expression
    assert expression.endswith(")")


def test_lseg_client_reports_each_api_call() -> None:
    from portfolio.lseg_research import ResearchResult, _LSEGClient

    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["AAPL"]))
    events: list[tuple[int | None, str, str]] = []
    client = _LSEGClient(result, minimum_interval=0, progress_callback=lambda p, s, d: events.append((p, s, d)))
    assert client.call("Test request", lambda: 42) == 42
    assert events[-1][1] == "Querying LSEG"
    assert "API request 1" in events[-1][2]
    assert "Test request" in events[-1][2]


def test_industrial_singular_is_canonicalized_to_trbc_code() -> None:
    filters = ScreenFilters(sector="industrial", candidate_search=True)
    expression = build_screen_expression(filters)
    body = build_screen_body(filters)
    assert 'IN(TR.TRBCEconSectorCode,"52")' in expression
    assert 'IN(TR.TRBCEconomicSector,"industrial")' not in expression
    assert not body.startswith("SCREEN(")



def test_lseg_client_cancellation_prevents_call() -> None:
    import threading
    import pytest
    from portfolio.lseg_research import ResearchCancelled, ResearchResult, _LSEGClient

    event = threading.Event()
    event.set()
    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["AAPL"]))
    client = _LSEGClient(result, minimum_interval=0, cancel_event=event)
    called = False

    def function():
        nonlocal called
        called = True
        return 1

    with pytest.raises(ResearchCancelled):
        client.call("cancelled request", function)
    assert called is False


def test_timeout_does_not_recursively_split_fields() -> None:
    from portfolio.lseg_research import ResearchResult, _LSEGClient, _safe_get_data

    class FakeLD:
        HeaderType = None

        def __init__(self) -> None:
            self.calls = 0

        def get_data(self, **_kwargs):
            self.calls += 1
            raise TimeoutError("request timed out")

    ld = FakeLD()
    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["AAPL"]))
    client = _LSEGClient(result, minimum_interval=0)
    frame = _safe_get_data(ld, client, ["AAPL.O"], ("TR.PE", "TR.PriceClose"), label="Slow topic")
    assert frame.empty
    assert ld.calls == 2
    record = result.call_records[0]
    assert record["status"] == "timed_out"
    assert record["retry_count"] == 1
    assert [attempt["status"] for attempt in record["attempts"]] == ["timed_out", "timed_out"]
    assert any("timed out" in warning for warning in result.warnings)


def test_lseg_client_retries_timeout_once_and_records_recovery() -> None:
    from portfolio.lseg_research import ResearchResult, _LSEGClient

    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["AAPL"]))
    client = _LSEGClient(result, minimum_interval=0)
    calls = 0

    def eventually_succeeds():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary timeout")
        return 42

    assert client.call("Transient request", eventually_succeeds) == 42
    assert calls == 2
    assert len(result.call_records) == 1
    record = result.call_records[0]
    assert record["status"] == "succeeded"
    assert record["retry_count"] == 1
    assert [attempt["status"] for attempt in record["attempts"]] == ["timed_out", "succeeded"]
    assert result.warnings == []


def test_lseg_http_timeout_is_configured(tmp_path) -> None:
    from portfolio.lseg_research import _configure_lseg_logging

    class Config:
        def __init__(self) -> None:
            self.values = {}

        def set_param(self, key, value):
            self.values[key] = value

    class FakeLD:
        def __init__(self) -> None:
            self.config = Config()

        def get_config(self):
            return self.config

    ld = FakeLD()
    settings = Settings(
        tmp_path, tmp_path / "db", None, "test", "desktop.workspace", None, 8.0, 17.0
    )
    _configure_lseg_logging(ld, settings)
    assert ld.config.values["http.request-timeout"] == 17.0


def test_empty_response_is_not_recursively_split() -> None:
    from portfolio.lseg_research import ResearchResult, _LSEGClient, _safe_get_data

    class FakeLD:
        HeaderType = None

        def __init__(self) -> None:
            self.calls = 0

        def get_data(self, **_kwargs):
            self.calls += 1
            return pd.DataFrame()

    ld = FakeLD()
    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["AAPL"]))
    client = _LSEGClient(result, minimum_interval=0)
    frame = _safe_get_data(
        ld,
        client,
        ["AAPL.O"],
        ("TR.FundPortfolioName", "TR.FundInvestorType", "TR.FundHoldingsDate"),
        label="Empty ownership",
    )
    assert frame.empty
    assert ld.calls == 1


def test_only_explicit_invalid_field_errors_trigger_isolation() -> None:
    from portfolio.lseg_research import ResearchResult, _LSEGClient, _safe_get_data

    class FakeLD:
        HeaderType = None

        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def get_data(self, **kwargs):
            fields = tuple(kwargs["fields"])
            self.calls.append(fields)
            if "TR.BadField" in fields:
                raise ValueError("Invalid field name: TR.BadField")
            return pd.DataFrame({"Instrument": ["AAPL.O"], fields[0]: [1.0]})

    ld = FakeLD()
    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["AAPL"]))
    client = _LSEGClient(result, minimum_interval=0)
    frame = _safe_get_data(
        ld,
        client,
        ["AAPL.O"],
        ("TR.PE", "TR.BadField"),
        label="Field isolation",
    )
    assert not frame.empty
    assert "TR.PE" in frame.columns
    assert len(ld.calls) == 3


def test_winner_context_uses_narrow_ownership_window(monkeypatch) -> None:
    import portfolio.lseg_research as module

    plan = ResearchPlan(
        mode="screen",
        workflow="sector_opportunity",
        topics=["ownership", "insiders"],
        raw_request="show ownership and insider activity",
    )
    winner = ResolvedInstrument("A", "A", "A", "A.N", "A Corp")
    result = ResearchResult(plan=plan, resolved=[winner])
    client = module._LSEGClient(result, minimum_interval=0)
    seen: dict[str, dict | None] = {}

    def fake_safe_get_data(_ld, _client, _universe, _fields, *, parameters=None, label, **_kwargs):
        seen[label] = parameters
        return pd.DataFrame()

    monkeypatch.setattr(module, "_safe_get_data", fake_safe_get_data)
    module._retrieve_winner_optional_context(object(), client, result)

    assert seen["Winner ownership snapshot"] == {"SDate": -25, "EDate": -24, "Frq": "D"}
    assert seen["Winner insider activity"] == {"SDate": -365, "EDate": 0, "Frq": "Q"}


def test_stock_screen_top_count_has_one_trace_authority() -> None:
    import portfolio.lseg_research as module

    filters = ScreenFilters(limit=15, candidate_search=False)
    assert module._screen_top_count(filters) == 150
    assert "TOP(TR.CompanyMarketCap,150,nnumber)" in module.build_screen_body(filters)
