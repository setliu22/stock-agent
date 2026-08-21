from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from portfolio.company_resolver import ResolvedInstrument
from portfolio.config import Settings
from portfolio.lseg_research import (
    LSEGResearchError,
    ResearchResult,
    _LSEGClient,
    _canonicalize,
    _combine_columns,
    _combine_screen_core_and_enrichment,
    _deterministic_screen_report,
    _deterministic_company_report,
    _first_value,
    _rank_candidate_screen,
    _retrieve_estimate_history,
    _retrieve_price_history,
    _retrieve_winner_optional_context,
    _safe_get_data,
    build_screen_expression,
    concise_report,
)
from portfolio.research_plan import (
    ResearchPlan,
    ScreenFilters,
    UnsupportedResearchConstraint,
)


def settings(tmp_path: Path, *, groq_key: str | None = None) -> Settings:
    return Settings(tmp_path, tmp_path / "db.sqlite", groq_key, "test", "desktop.workspace")


def test_malformed_normalized_plans_fail_closed() -> None:
    with pytest.raises(UnsupportedResearchConstraint):
        ResearchPlan(mode="screen", workflow="stock_screen", screen=ScreenFilters(country_code="ZZ")).normalized()
    with pytest.raises(UnsupportedResearchConstraint):
        ResearchPlan(mode="screen", workflow="stock_screen", screen=ScreenFilters(limit=3.5)).normalized()
    with pytest.raises(UnsupportedResearchConstraint):
        ResearchPlan(mode="screen", workflow="stock_screen", entities=[{"bad": "entity"}]).normalized()
    with pytest.raises(UnsupportedResearchConstraint):
        ResearchPlan(
            mode="screen", workflow="stock_screen", screen=ScreenFilters(universe='0#.SPX),BAD')
        ).normalized()


def test_missing_ric_never_borrows_another_company_value() -> None:
    result = ResearchResult(
        plan=ResearchPlan(mode="compare", workflow="company_compare", entities=["A", "B"]),
        resolved=[
            ResolvedInstrument("A", "A", "A", "A.N", "A Corp"),
            ResolvedInstrument("B", "B", "B", "B.N", "B Corp"),
        ],
    )
    result.tables["valuation"] = pd.DataFrame({"Instrument": ["A.N"], "TR.PE": [10.0]})
    assert _first_value(result, "valuation", "TR.PE", "B.N") is None


def test_unknown_equal_width_headers_are_not_mapped_positionally() -> None:
    frame = pd.DataFrame({"Instrument": ["A.N"], "Mystery One": [10], "Mystery Two": [20]})
    canonical = _canonicalize(frame, ("TR.PE", "TR.PriceClose"))
    assert "TR.PE" not in canonical.columns
    assert "TR.PriceClose" not in canonical.columns


def test_conflicting_identity_batches_fail_instead_of_masking_mismatch() -> None:
    left = pd.DataFrame({"Instrument": ["A.N"], "TR.TRBCEconSectorCode": ["52"]})
    right = pd.DataFrame({"Instrument": ["A.N"], "TR.TRBCEconSectorCode": ["57"]})
    with pytest.raises(LSEGResearchError, match="conflicting"):
        _combine_columns([left, right])


def test_usd_screen_enrichment_overrides_local_currency_core_value() -> None:
    core = pd.DataFrame(
        {
            "Instrument": ["950160.KQ"],
            "TR.CommonName": ["Kolon TissueGene Inc"],
            "TR.HQCountryCode": ["US"],
            "TR.CompanyMarketCap": [5_276_646_936_000],
        }
    )
    enrichment = pd.DataFrame(
        {
            "Instrument": ["950160.KQ"],
            "TR.HQCountryCode": ["US"],
            "TR.CompanyMarketCap": [3_566_820_292.42],
        }
    )

    combined = _combine_screen_core_and_enrichment(core, enrichment)

    assert combined.loc[0, "TR.CompanyMarketCap"] == 3_566_820_292.42
    assert combined.loc[0, "TR.CommonName"] == "Kolon TissueGene Inc"


def test_scalar_get_data_discards_unrequested_instruments() -> None:
    class FakeLD:
        HeaderType = None

        @staticmethod
        def get_data(**_kwargs):
            return pd.DataFrame({"Instrument": ["A.N", "ROGUE.N"], "TR.PE": [10.0, 1.0]})

    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["A"]))
    frame = _safe_get_data(FakeLD(), _LSEGClient(result, minimum_interval=0), "A.N", ("TR.PE",), label="Test")
    assert frame["Instrument"].tolist() == ["A.N"]
    assert any("unexpected instruments" in warning for warning in result.warnings)


def test_multi_ric_response_without_instrument_is_rejected() -> None:
    class FakeLD:
        HeaderType = None

        @staticmethod
        def get_data(**_kwargs):
            return pd.DataFrame({"TR.PE": [10.0, 20.0]})

    result = ResearchResult(plan=ResearchPlan(mode="compare", entities=["A", "B"]))
    frame = _safe_get_data(
        FakeLD(), _LSEGClient(result, minimum_interval=0), ["A.N", "B.N"], ("TR.PE",), label="Test"
    )
    assert frame.empty
    assert any("no Instrument" in warning for warning in result.warnings)


def test_stock_screen_report_handles_nullable_names_and_honest_count(tmp_path: Path) -> None:
    plan = ResearchPlan(mode="screen", workflow="stock_screen", screen=ScreenFilters()).normalized()
    result = ResearchResult(plan=plan)
    result.tables["screen"] = pd.DataFrame(
        {
            "Instrument": [f"RIC{i}.N" for i in range(12)],
            "TR.CommonName": [pd.NA, *[f"Company {i}" for i in range(1, 12)]],
            "TR.CompanyMarketCap": [1_000_000_000 + i for i in range(12)],
        }
    )
    report = concise_report(result, settings(tmp_path))
    assert report.startswith("Screen results (10 shown)")
    assert report.count("• ") == 10
    assert "RIC0.N" in report
    assert "Company:" not in report


def test_nullable_lseg_numeric_columns_do_not_break_ranking() -> None:
    frame = pd.DataFrame(
        {
            "Instrument": ["A.N", "B.N"],
            "TR.CompanyMarketCap": pd.Series([1_000_000_000, 2_000_000_000], dtype="Int64"),
            "TR.PtoEPSMeanEst(Period=FY1)": pd.Series([10.0, pd.NA], dtype="Float64"),
            "TR.PriceToBVPerShare": pd.Series([1.0, 2.0], dtype="Float64"),
            "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM": pd.Series([10.0, 12.0], dtype="Float64"),
        }
    )
    ranked = _rank_candidate_screen(frame)
    assert len(ranked) == 2
    assert ranked["Value Evidence Count"].notna().all()


def test_estimate_revision_does_not_cross_fiscal_period_rollover() -> None:
    class FakeLD:
        HeaderType = None

        @staticmethod
        def get_data(**_kwargs):
            return pd.DataFrame(
                {
                    "Instrument": ["A.N", "A.N"],
                    "TR.EPSMean(Period=FY1).calcdate": ["2026-06-01", "2026-07-01"],
                    "TR.EPSMean(Period=FY1).periodenddate": ["2026-12-31", "2027-12-31"],
                    "TR.EPSMean(Period=FY1)": [5.0, 7.0],
                }
            )

    plan = ResearchPlan(mode="company", entities=["A"], lookback_days=365)
    result = ResearchResult(plan=plan)
    resolved = ResolvedInstrument("A", "A", "A", "A.N", "A Corp")
    _retrieve_estimate_history(FakeLD(), _LSEGClient(result, minimum_interval=0), result, resolved)
    assert "A.N:eps_revision_30d" not in result.metrics


def test_descending_price_history_is_sorted_before_returns() -> None:
    class FakeLD:
        HeaderType = None

        def __init__(self) -> None:
            self.kwargs = {}

        def get_history(self, **kwargs):
            self.kwargs = kwargs
            return pd.DataFrame(
                {"TRDPRC_1": [110.0, 100.0, 90.0]},
                index=pd.to_datetime(["2026-07-03", "2026-07-02", "2026-07-01"]),
            )

    ld = FakeLD()
    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["A"], lookback_days=365))
    resolved = ResolvedInstrument("A", "A", "A", "A.N", "A Corp")
    _retrieve_price_history(ld, _LSEGClient(result, minimum_interval=0), result, resolved)
    assert result.metrics["A.N:last_price"] == 110.0
    assert result.metrics["A.N:period_return"] == pytest.approx(110 / 90 - 1)
    assert "CCH" in ld.kwargs["adjustments"]


def test_numeric_price_history_index_does_not_create_1970_returns() -> None:
    class FakeLD:
        HeaderType = None

        @staticmethod
        def get_history(**_kwargs):
            return pd.DataFrame({"TRDPRC_1": [90.0, 100.0, 110.0]})

    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["A"], lookback_days=365))
    resolved = ResolvedInstrument("A", "A", "A", "A.N", "A Corp")
    _retrieve_price_history(FakeLD(), _LSEGClient(result, minimum_interval=0), result, resolved)
    assert "A.N:last_price" not in result.metrics
    assert any("no trustworthy date index" in warning for warning in result.warnings)


def test_negative_multiples_are_not_reported_as_usable_valuation() -> None:
    resolved = ResolvedInstrument("Loss Co", "Loss Co", "LOSS", "LOSS.N", "Loss Co")
    result = ResearchResult(
        plan=ResearchPlan(mode="company", workflow="company_deep_dive", entities=["Loss Co"]).normalized(),
        resolved=[resolved],
    )
    result.tables["profile"] = pd.DataFrame(
        {"Instrument": ["LOSS.N"], "TR.CommonName": ["Loss Co"]}
    )
    result.tables["valuation"] = pd.DataFrame(
        {"Instrument": ["LOSS.N"], "TR.PE": [-8.0], "TR.PtoEPSMeanEst(Period=FY1)": [-5.0]}
    )
    text = _deterministic_company_report(result)
    assert "P/E -" not in text
    assert "No usable positive valuation multiple" in text


def test_call_trace_records_sanitized_request_semantics() -> None:
    result = ResearchResult(plan=ResearchPlan(mode="company", entities=["A"]))
    client = _LSEGClient(result, minimum_interval=0)
    client.call(
        "Test get_data",
        lambda: pd.DataFrame({"Instrument": ["A.N"], "TR.PE": [10.0]}),
        request_metadata={
            "operation": "get_data", "universe": ["A.N"], "fields": ["TR.PE"],
            "parameters": {"Curn": "USD"},
        },
    )
    request = result.call_records[0]["request"]
    assert request == {
        "operation": "get_data", "universe": ["A.N"], "fields": ["TR.PE"],
        "parameters": {"Curn": "USD"},
    }


def test_unrequested_row_expanding_optional_data_is_not_queried(monkeypatch) -> None:
    plan = ResearchPlan(
        mode="screen",
        workflow="sector_opportunity",
        screen=ScreenFilters(sector="Industrials", candidate_search=True),
        raw_request="find a promising undervalued US stock in the industrials sector",
    ).normalized()
    result = ResearchResult(
        plan=plan,
        resolved=[ResolvedInstrument("A", "A", "A", "A.N", "A Corp")],
    )
    monkeypatch.setattr(
        "portfolio.lseg_research._safe_get_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("optional call should not run")),
    )
    _retrieve_winner_optional_context(object(), _LSEGClient(result, minimum_interval=0), result)
    assert result.call_records == []
