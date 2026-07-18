from __future__ import annotations

import pandas as pd

from portfolio.company_resolver import ResolvedInstrument
from portfolio.config import Settings
from portfolio.lseg_research import (
    ResearchResult,
    _extract_values,
    _open_lseg_session,
    apply_screen_filters,
    build_screen_body,
    build_screen_expression,
    concise_report,
)
from portfolio.research_planner import ResearchPlan, ScreenFilters


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
            "TR.CompanyMarketCap": [20e9, 9e9, 30e9],
            "TR.PtoEPSMeanEst(Period=FY1)": [30, 20, 50],
        }
    )
    output = apply_screen_filters(frame, filters)
    assert output["TR.CommonName"].tolist() == ["A"]


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
    assert output.iloc[0]["Research Score"] > output.iloc[1]["Research Score"]


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
    with pytest.raises(Exception, match="did not reach Opened state"):
        _open_lseg_session(FakeLD(), settings)


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
    assert ld.calls == 1
    assert any("timed out" in warning for warning in result.warnings)


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

    plan = ResearchPlan(mode="screen", workflow="sector_opportunity")
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
