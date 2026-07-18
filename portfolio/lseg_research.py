"""Natural-language LSEG deep research executor.

The planner decides what the user asked for. This module performs only
research-safe, read-only calls, records failures caused by entitlements or field
availability, derives comparable metrics, and returns a concise evidence-based
report.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import html
import json
import math
import re
import time
from typing import Any, Callable, Iterable

import pandas as pd

from .company_resolver import ResolvedInstrument, resolve_instrument
from .config import Settings
from .research_planner import ResearchPlan, ScreenFilters, canonicalize_sector
from .research_workflows import get_workflow


ProgressCallback = Callable[[int | None, str, str], None]


def _emit_progress(
    callback: ProgressCallback | None,
    percent: int | None,
    stage: str,
    detail: str = "",
) -> None:
    """Send a best-effort progress update without letting UI code break research."""
    if callback is None:
        return
    normalized = None if percent is None else max(0, min(100, int(percent)))
    try:
        callback(normalized, stage, detail)
    except Exception:
        pass


TOPIC_FIELDS: dict[str, tuple[str, ...]] = {
    "profile": (
        "TR.CommonName", "TR.TickerSymbol", "TR.HeadquartersCountry", "TR.ExchangeName",
        "TR.TRBCEconomicSector", "TR.TRBCIndustryGroup", "TR.CompanyMarketCap", "TR.EV",
        "TR.PriceClose", "TR.OrganizationID", "TR.BusinessSummary",
    ),
    "fundamentals": (
        "TR.TotalRevenue(Period=LTM,Methodology=InterimSum)",
        "TR.GrossProfit(Period=LTM,Methodology=InterimSum)",
        "TR.OperatingProfit(Period=LTM)", "TR.NetIncomeBeforeExtraItems(Period=LTM)",
        "TR.FreeCashFlow(Period=LTM)", "TR.F.DebtTot", "TR.F.CashCashEquiv",
        "TR.FCFMean(Period=FY1)", "TR.FCFMean(Period=FY2)", "TR.WACC",
    ),
    "profitability": (
        "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", "TR.ROAPercentTrailing12M",
        "TR.OperatingProfitMarginPct5YrAvg", "TR.PretaxMarginPercent(Period=FY0)",
        "TR.EBITDATotEqtyPctTTM",
    ),
    "valuation": (
        "TR.PE", "TR.PtoEPSMeanEst(Period=FY1)", "TR.PriceToSalesPerShare",
        "TR.EVToEBITDA", "TR.PricetoCFPerShare", "TR.PriceToBVPerShare",
        "TR.DividendYield", "TR.EVtoFCFSmartEst(Period=FY1)",
    ),
    "estimates": (
        "TR.EpsSmartEst(Period=FY1)", "TR.EPSMean(Period=FY1)",
        "TR.EPSMean(Period=FY2)", "TR.EpsPreSurprisePct", "TR.EpsPreSurprise",
        "TR.EpsPreSurpriseFlag", "TR.EPSMedian(Period=FY1)", "TR.EPSLow(Period=FY1)",
        "TR.EPSHigh(Period=FY1)", "TR.EPSNumIncEstimates(Period=FY1)",
        "TR.RevenueMean(Period=FY1)", "TR.RevenueMean(Period=FY2)",
        "TR.RevenueSmartEst(Period=FY1)", "TR.RevenueSENumIncEst(Period=FY1)",
    ),
    "recommendations": ("TR.RecMean", "TR.PriceTargetMean", "TR.LTGMean"),
    "guidance": (
        "TR.GuidanceMeasure", "TR.GuidancePeriodYear", "TR.GuidancePeriodMonth",
        "TR.GuidanceDate", "TR.EstGuidHighValue", "TR.EstGuidLowValue", "TR.GuidanceText",
        "TR.GuidanceSpeaker", "TR.GuidanceDocType",
    ),
    "events": ("TR.EventType", "TR.EventTitle", "TR.EventStartDate", "TR.EventLastUpdate"),
    "risk": (
        "TR.Volatility10D", "TR.Volatility20D", "TR.Volatility30D", "TR.Volatility60D",
        "TR.Volatility90D", "TR.F.DebtTot", "TR.WACC",
    ),
    "ownership": (
        "TR.FundPortfolioName", "TR.FundInvestorType", "TR.FdAdjPctOfShrsOutHeld",
        "TR.FundAdjShrsHeld", "TR.FdAdjSharesHeldValue", "TR.FundHoldingsDate",
    ),
    "insiders": (
        "TR.InsiderFullName", "TR.InsiderFullName.date", "TR.AdjSharesTraded",
        "TR.TransactionDate", "TR.AdjSharesHeld",
    ),
}

ESTIMATE_HISTORY_FIELDS: tuple[str, ...] = (
    "TR.EPSMean(Period=FY1).calcdate", "TR.EPSMean(Period=FY1)",
    "TR.EPSNumIncEstimates(Period=FY1)", "TR.EpsSmartEst(Period=FY1)",
    "TR.EpsSENumIncEst(Period=FY1)", "TR.RevenueMean(Period=FY1)",
    "TR.RevenueNumIncEstimates(Period=FY1)", "TR.ClosePrice(Adjusted=1)",
)

FIELD_LABELS: dict[str, str] = {
    "TR.CommonName": "Company", "TR.TickerSymbol": "Ticker", "TR.HeadquartersCountry": "Country",
    "TR.ExchangeName": "Exchange", "TR.TRBCEconomicSector": "Sector", "TR.TRBCIndustryGroup": "Industry group",
    "TR.CompanyMarketCap": "Market cap", "TR.EV": "Enterprise value", "TR.PriceClose": "Price",
    "TR.TotalRevenue(Period=LTM,Methodology=InterimSum)": "LTM revenue",
    "TR.GrossProfit(Period=LTM,Methodology=InterimSum)": "LTM gross profit",
    "TR.OperatingProfit(Period=LTM)": "LTM operating profit", "TR.FreeCashFlow(Period=LTM)": "LTM free cash flow",
    "TR.F.DebtTot": "Total debt", "TR.F.CashCashEquiv": "Cash", "TR.WACC": "WACC",
    "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM": "ROE", "TR.ROAPercentTrailing12M": "ROA",
    "TR.OperatingProfitMarginPct5YrAvg": "5Y average operating margin", "TR.PretaxMarginPercent(Period=FY0)": "Pretax margin",
    "TR.PE": "Trailing P/E", "TR.PtoEPSMeanEst(Period=FY1)": "Forward P/E", "TR.PriceToSalesPerShare": "Price / sales",
    "TR.EVToEBITDA": "EV / EBITDA", "TR.PricetoCFPerShare": "Price / cash flow", "TR.PriceToBVPerShare": "Price / book",
    "TR.DividendYield": "Dividend yield", "TR.EpsSmartEst(Period=FY1)": "EPS SmartEstimate",
    "TR.EPSMean(Period=FY1)": "FY1 EPS consensus", "TR.EPSMean(Period=FY2)": "FY2 EPS consensus",
    "TR.EpsPreSurprisePct": "Predicted surprise", "TR.EPSNumIncEstimates(Period=FY1)": "EPS estimate count",
    "TR.RevenueMean(Period=FY1)": "FY1 revenue consensus", "TR.RecMean": "Recommendation mean",
    "TR.PriceTargetMean": "Mean price target", "TR.LTGMean": "Long-term growth mean",
    "TR.Volatility30D": "30-day realized volatility",
}

SCREEN_FIELDS: tuple[str, ...] = (
    "TR.CommonName", "TR.TickerSymbol", "TR.TRBCEconomicSector", "TR.TRBCIndustryGroup",
    "TR.CompanyMarketCap", "TR.PriceClose", "TR.PE", "TR.PtoEPSMeanEst(Period=FY1)",
    "TR.EVToEBITDA", "TR.DividendYield", "TR.TotalReturn3Mo",
    "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", "TR.ROAPercentTrailing12M",
    "TR.PriceTargetMean", "TR.EpsPreSurprisePct", "TR.EPSMean(Period=FY1)",
    "TR.EpsSmartEst(Period=FY1)", "TR.FCFMean(Period=FY1)", "TR.F.DebtTot",
    "TR.Volatility30D",
)

PEER_FIELDS: tuple[str, ...] = (
    "TR.CommonName", "TR.CompanyMarketCap", "TR.PE", "TR.PtoEPSMeanEst(Period=FY1)",
    "TR.PriceToSalesPerShare", "TR.EVToEBITDA", "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM",
    "TR.ROAPercentTrailing12M", "TR.TotalReturn3Mo", "TR.PriceTargetMean",
)

TRBC_SECTOR_CODES: dict[str, str] = {
    "energy": "50", "basic materials": "51", "industrials": "52", "consumer cyclicals": "53",
    "consumer non-cyclicals": "54", "financials": "55", "healthcare": "56", "technology": "57",
    "telecommunications services": "58", "utilities": "59", "real estate": "60",
}

SCREEN_CORE_FIELDS: tuple[str, ...] = (
    "TR.CommonName", "TR.TickerSymbol", "TR.TRBCEconomicSector", "TR.TRBCIndustryGroup",
    "TR.CompanyMarketCap", "TR.PriceClose",
)


class LSEGResearchError(RuntimeError):
    pass


class ResearchCancelled(RuntimeError):
    """Raised when the user stops an in-progress research workflow."""


_CALL_TIMED_OUT = object()


@dataclass(frozen=True)
class _CallFailure:
    """Preserve the underlying LSEG error so callers can react correctly."""

    error: BaseException


def _looks_like_invalid_field_error(exc: BaseException) -> bool:
    """Return True only for errors that specifically identify bad field syntax.

    Empty datasets, entitlement failures, backend errors, and timeouts must not
    trigger recursive field splitting. Those conditions are not evidence that a
    particular field is malformed.
    """
    text = f"{type(exc).__name__}: {exc}".casefold()
    phrases = (
        "invalid field",
        "unknown field",
        "unrecognized field",
        "field not found",
        "invalid field name",
        "invalid data item",
        "unrecognized data item",
    )
    return any(phrase in text for phrase in phrases)


def _is_cancelled(cancel_event: Any | None) -> bool:
    return bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())


def _raise_if_cancelled(cancel_event: Any | None) -> None:
    if _is_cancelled(cancel_event):
        raise ResearchCancelled("Research stopped by user.")


def _looks_like_timeout(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    return any(token in text for token in ("timeout", "timed out", "readtimeout", "requesttimeout"))


@dataclass
class ResearchResult:
    plan: ResearchPlan
    resolved: list[ResolvedInstrument] = field(default_factory=list)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return any(frame is not None and not frame.empty for frame in self.tables.values())


class _LSEGClient:
    def __init__(
        self,
        result: ResearchResult,
        minimum_interval: float = 0.27,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Any | None = None,
    ) -> None:
        self.result = result
        self.minimum_interval = minimum_interval
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event
        self._last_call = 0.0

    def call(
        self,
        label: str,
        function: Callable[[], Any],
        *,
        warn: bool = True,
        capture_failure: bool = False,
    ) -> Any:
        _raise_if_cancelled(self.cancel_event)
        wait = self.minimum_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            if self.cancel_event is not None and hasattr(self.cancel_event, "wait"):
                if self.cancel_event.wait(wait):
                    raise ResearchCancelled("Research stopped by user.")
            else:
                time.sleep(wait)
        _raise_if_cancelled(self.cancel_event)
        self._last_call = time.monotonic()
        call_number = len(self.result.calls) + 1
        self.result.calls.append(label)
        _emit_progress(
            self.progress_callback,
            None,
            "Querying LSEG",
            f"API request {call_number}: {label}",
        )
        try:
            value = function()
            _raise_if_cancelled(self.cancel_event)
            return value
        except ResearchCancelled:
            raise
        except Exception as exc:
            if _looks_like_timeout(exc):
                message = f"{label}: timed out and was skipped"
                self.result.warnings.append(message)
                _emit_progress(
                    self.progress_callback,
                    None,
                    "Skipping slow LSEG request",
                    f"{label} exceeded the configured request timeout; continuing without that optional evidence.",
                )
                return _CALL_TIMED_OUT
            if warn:
                self.result.warnings.append(f"{label}: {type(exc).__name__}: {exc}")
            return _CallFailure(exc) if capture_failure else None


def _session_state_text(session: Any) -> str:
    state = getattr(session, "open_state", None)
    if state is None:
        state = getattr(session, "state", None)
    if state is None:
        return "Unknown"
    name = getattr(state, "name", None)
    return str(name or state)


def _session_is_open(session: Any) -> bool:
    state = _session_state_text(session).casefold().replace("_", " ")
    return "opened" in state and "not opened" not in state and "closed" not in state


def _configure_lseg_logging(ld: Any, settings: Settings) -> str:
    log_path = settings.project_root / "data" / "lseg-data-lib.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config = ld.get_config()
    except Exception:
        return str(log_path)

    values: list[tuple[str, Any]] = [
        ("logs.transports.file.enabled", True),
        ("logs.transports.file.name", str(log_path)),
        ("logs.level", "info"),
        # LSEG officially supports this configuration key. Without it, a
        # single slow content request can wait for the server-side timeout,
        # which may be several minutes.
        ("http.request-timeout", int(max(5.0, settings.lseg_request_timeout))),
        ("sessions.default", settings.lseg_session_name),
    ]
    if settings.lseg_app_key:
        values.append(("sessions.desktop.workspace.app-key", settings.lseg_app_key))

    for key, value in values:
        try:
            config.set_param(key, value)
        except Exception:
            try:
                config[key] = value
            except Exception:
                pass
    return str(log_path)


def _open_lseg_session(ld: Any, settings: Settings) -> Any:
    log_path = _configure_lseg_logging(ld, settings)
    attempts: list[tuple[str, Callable[[], Any]]] = [
        (settings.lseg_session_name, lambda: ld.open_session(name=settings.lseg_session_name)),
    ]
    if settings.lseg_session_name == "desktop.workspace":
        attempts.append(("desktop default", lambda: ld.open_session()))

    errors: list[str] = []
    for label, opener in attempts:
        try:
            try:
                ld.close_session()
            except Exception:
                pass
            session = opener()
            deadline = time.monotonic() + max(1.0, settings.lseg_session_timeout)
            while time.monotonic() < deadline:
                state = _session_state_text(session)
                if _session_is_open(session):
                    return session
                if "closed" in state.casefold():
                    break
                time.sleep(0.2)
            errors.append(f"{label} state={_session_state_text(session)}")
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")

    app_key_hint = (
        " Add LSEG_APP_KEY to .env if your Workspace setup requires a desktop application key."
        if not settings.lseg_app_key
        else " The configured LSEG_APP_KEY was used."
    )
    raise LSEGResearchError(
        "Workspace was detected by the application, but the LSEG Python session did not reach Opened state. "
        + " | ".join(errors)
        + app_key_hint
        + f" Diagnostic log: {log_path}"
    )


def _missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _frame_from_response(response: Any) -> pd.DataFrame:
    if response is None:
        return pd.DataFrame()
    if isinstance(response, pd.DataFrame):
        return response.copy()
    data = getattr(response, "data", None)
    frame = getattr(data, "df", None)
    if isinstance(frame, pd.DataFrame):
        return frame.copy()
    frame = getattr(response, "df", None)
    if isinstance(frame, pd.DataFrame):
        return frame.copy()
    return pd.DataFrame()


def _canonicalize(frame: Any, requested_fields: Iterable[str]) -> pd.DataFrame:
    result = _frame_from_response(frame)
    if result.empty:
        return result
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = [" ".join(str(item) for item in column if item not in {None, ""}).strip() for column in result.columns]

    fields = list(requested_fields)
    instrument_columns = [column for column in result.columns if str(column).casefold() in {"instrument", "ric"}]
    rename: dict[Any, str] = {}
    if instrument_columns:
        rename[instrument_columns[0]] = "Instrument"

    def normalized(value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).casefold())

    field_keys: dict[str, list[str]] = {}
    for field_name in fields:
        field_keys.setdefault(normalized(field_name), []).append(field_name)
        base = field_name.split("(", 1)[0]
        field_keys.setdefault(normalized(base), []).append(field_name)

    matched = 0
    value_columns = [column for column in result.columns if column not in instrument_columns]
    for column in value_columns:
        key = normalized(column)
        candidates = list(dict.fromkeys(field_keys.get(key, [])))
        if len(candidates) == 1:
            rename[column] = candidates[0]
            matched += 1

    # HeaderType.NAME should make the mapping above exact. This fallback exists
    # only for older/mocked responses where titles are returned in request order.
    if matched == 0 and len(value_columns) == len(fields):
        for field_name, column in zip(fields, value_columns):
            rename[column] = field_name

    result = result.rename(columns=rename)
    return result.loc[:, ~result.columns.duplicated()]


def _combine_columns(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in frames if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    output = frames[0].copy()
    for frame in frames[1:]:
        join_columns = [column for column in ("Instrument",) if column in output.columns and column in frame.columns]
        if join_columns:
            output = output.merge(frame, on=join_columns, how="outer")
        else:
            output = pd.concat([output.reset_index(drop=True), frame.reset_index(drop=True)], axis=1)
    return output.loc[:, ~output.columns.duplicated()]


def _call_get_data(ld: Any, universe: Any, fields: tuple[str, ...], parameters: dict[str, Any] | None) -> Any:
    kwargs = {"universe": universe, "fields": list(fields), "parameters": parameters}
    header_type = getattr(getattr(ld, "HeaderType", None), "NAME", None)
    if header_type is not None:
        kwargs["header_type"] = header_type
    try:
        return ld.get_data(**kwargs)
    except TypeError as exc:
        # Preserve compatibility with older library builds and unit-test fakes.
        if "header_type" not in kwargs:
            raise
        kwargs.pop("header_type", None)
        return ld.get_data(**kwargs)


def _safe_get_data(
    ld: Any,
    client: _LSEGClient,
    universe: Any,
    fields: Iterable[str],
    *,
    parameters: dict[str, Any] | None = None,
    label: str,
    universe_chunk_size: int = 50,
    field_batch_size: int | None = None,
    isolate_invalid_fields: bool = True,
) -> pd.DataFrame:
    """Retrieve curated field groups without mistaking empty data for bad fields.

    The old implementation recursively split every empty response. That could
    turn one legitimate no-data result into eleven or more requests and was
    especially harmful for row-expanding ownership tables. This implementation
    only isolates fields when LSEG explicitly reports an invalid-field error.
    """
    requested = tuple(dict.fromkeys(str(field) for field in fields if str(field).strip()))
    failures: list[str] = []

    if isinstance(universe, (list, tuple, set)):
        universe_values = [item for item in universe]
        chunks = [
            universe_values[index:index + universe_chunk_size]
            for index in range(0, len(universe_values), universe_chunk_size)
        ] or [[]]
    else:
        chunks = [universe]

    if field_batch_size is not None and field_batch_size > 0:
        field_batches = [
            requested[index:index + field_batch_size]
            for index in range(0, len(requested), field_batch_size)
        ]
    else:
        field_batches = [requested]

    row_frames: list[pd.DataFrame] = []
    timed_out = False

    for chunk_index, universe_chunk in enumerate(chunks, start=1):
        _raise_if_cancelled(client.cancel_event)
        batch_frames: list[pd.DataFrame] = []

        def fetch(batch: tuple[str, ...]) -> list[pd.DataFrame]:
            nonlocal timed_out
            _raise_if_cancelled(client.cancel_event)
            response = client.call(
                f"{label} chunk {chunk_index}/{len(chunks)} ({len(batch)} fields)",
                lambda: _call_get_data(ld, universe_chunk, batch, parameters),
                warn=False,
                capture_failure=True,
            )
            if response is _CALL_TIMED_OUT:
                timed_out = True
                failures.extend(batch)
                return []
            if isinstance(response, _CallFailure):
                if isolate_invalid_fields and len(batch) > 1 and _looks_like_invalid_field_error(response.error):
                    midpoint = len(batch) // 2
                    return fetch(batch[:midpoint]) + fetch(batch[midpoint:])
                failures.extend(batch)
                client.result.warnings.append(
                    f"{label}: {type(response.error).__name__}: {response.error}"
                )
                return []

            frame = _canonicalize(response, batch)
            if frame.empty:
                # A valid empty response means the company has no rows for this
                # content family or date window. It is not a reason to retry each
                # field individually.
                return []
            return [frame]

        for batch in field_batches:
            if not batch or timed_out:
                break
            batch_frames.extend(fetch(batch))

        combined_chunk = _combine_columns(batch_frames)
        if not combined_chunk.empty:
            row_frames.append(combined_chunk)
        if timed_out:
            break

    combined = pd.concat(row_frames, ignore_index=True, sort=False) if row_frames else pd.DataFrame()
    if failures:
        unique_failures = list(dict.fromkeys(failures))
        client.result.warnings.append(f"{label}: unavailable fields: {', '.join(unique_failures)}")
    return combined.loc[:, ~combined.columns.duplicated()] if not combined.empty else combined


def _first_value(result: ResearchResult, table_name: str, field_name: str, ric: str | None = None) -> Any:
    frame = result.tables.get(table_name)
    if frame is None or frame.empty or field_name not in frame.columns:
        return None
    subset = frame
    if ric and "Instrument" in subset.columns:
        selected = subset[subset["Instrument"].astype(str) == ric]
        if not selected.empty:
            subset = selected
    values = subset[field_name].dropna()
    return None if values.empty else values.iloc[0]


def _numeric(value: Any) -> float | None:
    if _missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _column(frame: pd.DataFrame, field_name: str) -> pd.Series:
    if frame.empty or field_name not in frame.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[field_name], errors="coerce")


def _screen_number(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else format(number, ".15g")


def build_screen_body(filters: ScreenFilters) -> str:
    """Build the body accepted by ``lseg.data.discovery.Screener``.

    Sector wording is canonicalized and then converted to the documented TRBC
    economic-sector code. Unknown wording is rejected locally instead of being
    sent to LSEG as an invalid literal condition.
    """
    clauses = ["U(IN(Equity(active,public,primary)))/*UNV:Public*/"]
    if filters.country_code:
        clauses.append(f'IN(TR.HQCountryCode,"{filters.country_code.upper()}")')
    if filters.sector:
        canonical_sector = canonicalize_sector(filters.sector)
        if canonical_sector is None:
            raise LSEGResearchError(f"Unsupported sector wording: {filters.sector!r}.")
        sector_code = TRBC_SECTOR_CODES[canonical_sector.casefold()]
        clauses.append(f'IN(TR.TRBCEconSectorCode,"{sector_code}")')
    if filters.market_cap_min is not None:
        clauses.append(f"TR.CompanyMarketCap>={_screen_number(filters.market_cap_min)}")
    if filters.market_cap_max is not None:
        clauses.append(f"TR.CompanyMarketCap<={_screen_number(filters.market_cap_max)}")
    if filters.pe_max is not None:
        clauses.append(f"TR.PE<={_screen_number(filters.pe_max)}")
    if filters.forward_pe_max is not None:
        clauses.append(f"TR.PtoEPSMeanEst(Period=FY1)<={_screen_number(filters.forward_pe_max)}")
    if filters.ev_ebitda_max is not None:
        clauses.append(f"TR.EVToEBITDA<={_screen_number(filters.ev_ebitda_max)}")
    if filters.dividend_yield_min is not None:
        clauses.append(f"TR.DividendYield>={_screen_number(filters.dividend_yield_min)}")
    if filters.total_return_3m_min is not None:
        clauses.append(f"TR.TotalReturn3Mo>={_screen_number(filters.total_return_3m_min)}")
    top_count = 200 if filters.candidate_search else max(50, min(filters.limit * 10, 500))
    clauses.append(f"TOP(TR.CompanyMarketCap,{top_count},nnumber)")
    clauses.append("CURN=USD")
    return ", ".join(clauses)


def build_screen_expression(filters: ScreenFilters) -> str:
    """Return the complete ``SCREEN(...)`` expression used as a fallback."""
    return "SCREEN(" + build_screen_body(filters) + ")"


def _rank_candidate_screen(frame: pd.DataFrame) -> pd.DataFrame:
    """Rank candidates using independent evidence families and coverage.

    The score is not an investment recommendation. It is a transparent shortlist
    mechanism for deciding which companies receive the expensive deep dive.
    """
    output = frame.copy()
    if output.empty:
        return output

    def numeric(field: str) -> pd.Series:
        if field not in output.columns:
            return pd.Series(float("nan"), index=output.index, dtype="float64")
        return pd.to_numeric(output[field], errors="coerce")

    price = numeric("TR.PriceClose")
    target = numeric("TR.PriceTargetMean")
    output["Target Upside"] = target.div(price).sub(1).where(price > 0)

    eps_mean = numeric("TR.EPSMean(Period=FY1)")
    smart = numeric("TR.EpsSmartEst(Period=FY1)")
    output["Smart Gap"] = smart.sub(eps_mean).div(eps_mean.abs()).where(eps_mean.abs() > 0)

    debt = numeric("TR.F.DebtTot")
    fcf = numeric("TR.FCFMean(Period=FY1)")
    output["FCF to Debt"] = fcf.div(debt.abs()).where(debt.abs() > 0)

    components: list[tuple[str, pd.Series, float, str]] = []

    def add(family: str, field: str, weight: float, *, higher_is_better: bool = True, positive_only: bool = False) -> None:
        values = numeric(field)
        if positive_only:
            values = values.where(values > 0)
        valid = values.notna()
        if valid.sum() < 2:
            return
        percentile = values.rank(pct=True, method="average")
        if not higher_is_better:
            percentile = 1.0 - percentile + (1.0 / valid.sum())
        components.append((family, percentile.where(valid), weight, field))

    add("valuation", "TR.PtoEPSMeanEst(Period=FY1)", 0.13, higher_is_better=False, positive_only=True)
    add("valuation", "TR.EVToEBITDA", 0.09, higher_is_better=False, positive_only=True)
    add("quality", "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", 0.13)
    add("quality", "TR.ROAPercentTrailing12M", 0.09)
    add("cash_flow", "FCF to Debt", 0.09)
    add("income", "TR.DividendYield", 0.06)
    add("momentum", "TR.TotalReturn3Mo", 0.09)
    add("expectations", "Target Upside", 0.10)
    add("expectations", "TR.EpsPreSurprisePct", 0.07)
    add("expectations", "Smart Gap", 0.07)
    add("risk", "TR.Volatility30D", 0.08, higher_is_better=False, positive_only=True)

    if not components:
        output["Research Score"] = pd.NA
        output["Evidence Count"] = 0
        output["Evidence Families"] = ""
        return output.sort_values("TR.CompanyMarketCap", ascending=False, na_position="last")

    weighted_sum = pd.Series(0.0, index=output.index)
    available_weight = pd.Series(0.0, index=output.index)
    family_sets: dict[Any, set[str]] = {index: set() for index in output.index}
    evidence_count = pd.Series(0, index=output.index, dtype="int64")
    for family, percentile, weight, _field in components:
        valid = percentile.notna()
        weighted_sum = weighted_sum.add(percentile.fillna(0) * weight, fill_value=0)
        available_weight = available_weight.add(valid.astype(float) * weight, fill_value=0)
        evidence_count = evidence_count.add(valid.astype(int), fill_value=0).astype(int)
        for index in output.index[valid]:
            family_sets[index].add(family)

    raw_score = weighted_sum.div(available_weight.where(available_weight > 0)).mul(100)
    family_count = pd.Series({index: len(value) for index, value in family_sets.items()}, dtype="int64")
    coverage_factor = family_count.div(7).clip(lower=0.45, upper=1.0)
    output["Research Score"] = raw_score.mul(coverage_factor)
    output["Evidence Count"] = evidence_count
    output["Evidence Family Count"] = family_count
    output["Evidence Families"] = pd.Series({index: ", ".join(sorted(value)) for index, value in family_sets.items()})
    output["Data Coverage"] = evidence_count.div(max(len(components), 1))
    output["_market_cap_sort"] = numeric("TR.CompanyMarketCap")
    return output.sort_values(
        ["Research Score", "Evidence Family Count", "Evidence Count", "_market_cap_sort"],
        ascending=[False, False, False, False],
        na_position="last",
    ).drop(columns="_market_cap_sort")


def apply_screen_filters(frame: pd.DataFrame, filters: ScreenFilters) -> pd.DataFrame:
    output = frame.copy()
    tests = (
        ("TR.CompanyMarketCap", filters.market_cap_min, "min"),
        ("TR.CompanyMarketCap", filters.market_cap_max, "max"),
        ("TR.PE", filters.pe_max, "max"),
        ("TR.PtoEPSMeanEst(Period=FY1)", filters.forward_pe_max, "max"),
        ("TR.EVToEBITDA", filters.ev_ebitda_max, "max"),
        ("TR.DividendYield", filters.dividend_yield_min, "min"),
        ("TR.TotalReturn3Mo", filters.total_return_3m_min, "min"),
    )
    for field_name, threshold, direction in tests:
        if threshold is None or field_name not in output.columns:
            continue
        values = pd.to_numeric(output[field_name], errors="coerce")
        output = output[values >= threshold] if direction == "min" else output[values <= threshold]
    if filters.sector and "TR.TRBCEconomicSector" in output.columns:
        output = output[
            output["TR.TRBCEconomicSector"].astype(str).str.casefold() == filters.sector.casefold()
        ]
    if filters.candidate_search or filters.sort_by == "quality_value":
        return _rank_candidate_screen(output).head(filters.limit).reset_index(drop=True)
    sort_field = {
        "market_cap": "TR.CompanyMarketCap",
        "pe": "TR.PE",
        "forward_pe": "TR.PtoEPSMeanEst(Period=FY1)",
        "ev_ebitda": "TR.EVToEBITDA",
        "return": "TR.TotalReturn3Mo",
    }.get(filters.sort_by, "TR.CompanyMarketCap")
    if sort_field in output.columns:
        output = output.assign(_sort=pd.to_numeric(output[sort_field], errors="coerce"))
        output = output.sort_values("_sort", ascending=sort_field not in {"TR.CompanyMarketCap", "TR.TotalReturn3Mo"})
        output = output.drop(columns="_sort")
    return output.head(filters.limit).reset_index(drop=True)


def _retrieve_screen(ld: Any, client: _LSEGClient, result: ResearchResult) -> None:
    filters = result.plan.screen
    workflow = get_workflow(result.plan.workflow, result.plan.mode, candidate_search=filters.candidate_search)
    if filters.universe:
        from lseg.data.discovery import Chain

        universe: Any = list(client.call(f"Chain {filters.universe}", lambda: Chain(filters.universe), warn=True) or [])
        if not universe:
            raise LSEGResearchError(f"No constituents were returned for {filters.universe}.")
        core = _safe_get_data(ld, client, universe[:workflow.screen_limit], SCREEN_CORE_FIELDS, label="Index universe")
    else:
        body = build_screen_body(filters)
        expression = "SCREEN(" + body + ")"
        result.metrics["screen_expression"] = expression
        result.metrics["screen_body"] = body

        # LSEG's documented Python pattern is discovery.Screener(body), followed
        # by get_data on that object. A full SCREEN(...) string is retained only
        # as a compatibility fallback for library builds that accept it directly.
        discovery_fields = ("TR.CommonName", "TR.CompanyMarketCap", "TR.TRBCEconomicSector")

        def fetch_once(universe_value: Any, label: str, fields: tuple[str, ...]) -> pd.DataFrame:
            response = client.call(
                label,
                lambda: _call_get_data(ld, universe_value, fields, None),
                warn=True,
            )
            return _canonicalize(response, fields)

        core = pd.DataFrame()
        try:
            from lseg.data.discovery import Screener

            screener = Screener(body)
            core = fetch_once(screener, "Stock screen via discovery.Screener", discovery_fields)
            if core.empty:
                core = fetch_once(screener, "Stock screen retry with minimal field", ("TR.CommonName",))
        except Exception as exc:
            result.warnings.append(f"Stock screen object: {type(exc).__name__}: {exc}")

        if core.empty:
            core = fetch_once(expression, "Stock screen via SCREEN expression fallback", discovery_fields)
        if core.empty:
            raise LSEGResearchError(
                "The LSEG session opened, but the canonical TRBC stock screen returned no rows. "
                f"Sector={filters.sector!r}; expression={expression}. "
                "The application used the documented TRBC sector-code screen and tried both "
                "discovery.Screener and the full SCREEN expression. Check whether the Workspace "
                "Screener app itself returns results for the same sector and whether your account "
                "is entitled to company screening."
            )

    if core.empty or "Instrument" not in core.columns:
        raise LSEGResearchError("The screen returned no usable instrument identifiers.")

    rics = list(dict.fromkeys(str(value).strip() for value in core["Instrument"].dropna().tolist() if str(value).strip()))
    if not rics:
        raise LSEGResearchError("The screen returned no usable RICs.")
    rics = rics[:workflow.screen_limit]
    result.metrics["screen_universe_count"] = len(rics)

    enrichment = _safe_get_data(ld, client, rics, SCREEN_FIELDS, label="Stock-screen enrichment")
    frame = _combine_columns([core, enrichment])
    ranked = apply_screen_filters(frame, filters)
    result.tables["screen"] = ranked
    result.metrics["screen_ranked_count"] = len(ranked)
    if not ranked.empty and "Evidence Family Count" in ranked.columns:
        result.metrics["screen_median_evidence_families"] = float(pd.to_numeric(ranked["Evidence Family Count"], errors="coerce").median())


def _limit_per_instrument(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
    if frame.empty or "Instrument" not in frame.columns:
        return frame.head(limit)
    return frame.groupby("Instrument", sort=False, group_keys=False).head(limit).reset_index(drop=True)


def _retrieve_winner_optional_context(
    ld: Any,
    client: _LSEGClient,
    result: ResearchResult,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Enrich the already-selected winner with optional row-expanding data.

    Ownership, insiders, guidance, and event tables are useful context, but they
    must never decide whether the broad screen and core deep dive can finish.
    They are queried only after finalist ranking, for one company, with service-
    appropriate date windows and without recursive empty-result retries.
    """
    if not result.resolved:
        return
    winner = result.resolved[0]
    ric = winner.ric
    _emit_progress(
        progress_callback,
        90,
        "Enriching the leading candidate",
        f"{winner.company_name} ({ric}): checking recent guidance, events, ownership, and insider activity.",
    )

    for topic in ("guidance", "events"):
        frame = _safe_get_data(
            ld,
            client,
            [ric],
            TOPIC_FIELDS[topic],
            parameters={"SDate": -365, "EDate": 0},
            label=f"Winner {topic}",
            universe_chunk_size=1,
            field_batch_size=None,
            isolate_invalid_fields=True,
        )
        if not frame.empty:
            result.tables[topic] = _limit_per_instrument(frame, 12)

    # LSEG's documented fund-ownership example uses a narrow daily snapshot
    # window. Asking for an unconstrained current table can expand to thousands
    # of rows and block the workflow.
    ownership = _safe_get_data(
        ld,
        client,
        [ric],
        TOPIC_FIELDS["ownership"],
        parameters={"SDate": -25, "EDate": -24, "Frq": "D"},
        label="Winner ownership snapshot",
        universe_chunk_size=1,
        field_batch_size=None,
        isolate_invalid_fields=False,
    )
    if not ownership.empty:
        result.tables["ownership"] = _limit_per_instrument(ownership, 15)

    insiders = _safe_get_data(
        ld,
        client,
        [ric],
        TOPIC_FIELDS["insiders"],
        parameters={"SDate": -365, "EDate": 0, "Frq": "Q"},
        label="Winner insider activity",
        universe_chunk_size=1,
        field_batch_size=None,
        isolate_invalid_fields=False,
    )
    if not insiders.empty:
        result.tables["insiders"] = _limit_per_instrument(insiders, 15)


def _retrieve_candidate_deep_dive(
    ld: Any,
    client: _LSEGClient,
    result: ResearchResult,
    progress_callback: ProgressCallback | None = None,
) -> None:
    screen = result.tables.get("screen", pd.DataFrame())
    if screen.empty or "Instrument" not in screen.columns:
        return
    workflow = get_workflow(result.plan.workflow, result.plan.mode, candidate_search=True)
    shortlist = screen.head(workflow.deep_dive_candidates)
    resolved: list[ResolvedInstrument] = []
    for _, row in shortlist.iterrows():
        ric = str(row.get("Instrument") or "").strip()
        if not ric:
            continue
        ticker_value = row.get("TR.TickerSymbol")
        ticker = str(ticker_value).strip() if not _missing(ticker_value) else ric.split(".", 1)[0]
        name_value = row.get("TR.CommonName")
        name = str(name_value).strip() if not _missing(name_value) else ticker
        resolved.append(ResolvedInstrument(name, name, ticker, ric, name, resolution_source="screen"))
    result.resolved = resolved
    if not resolved:
        return

    _emit_progress(
        progress_callback,
        40,
        "Shortlist selected",
        f"Deep-researching {len(resolved)} finalists from the ranked screen.",
    )
    rics = [item.ric for item in resolved]
    result.metrics["deep_dive_count"] = len(rics)
    result.metrics["workflow_stages"] = [stage.stage_id for stage in workflow.stages]

    _emit_progress(
        progress_callback,
        45,
        "Retrieving finalist fundamentals",
        "Collecting comparable profile, financial, valuation, estimate, recommendation, and risk data.",
    )
    for topic in ("profile", "fundamentals", "profitability", "valuation", "estimates", "recommendations", "risk"):
        frame = _safe_get_data(ld, client, rics, TOPIC_FIELDS[topic], label=f"Finalist {topic}")
        if not frame.empty:
            result.tables[topic] = frame

    _emit_progress(
        progress_callback,
        55,
        "Researching finalist evidence",
        "Core finalist data is complete. Retrieving price, revisions, Reuters news, peers, filings, and ESG.",
    )

    total_candidates = max(len(resolved), 1)
    for index, item in enumerate(resolved):
        candidate_percent = 62 + int((index / total_candidates) * 24)
        _emit_progress(
            progress_callback,
            candidate_percent,
            f"Researching finalist {index + 1}/{len(resolved)}",
            f"{item.company_name} ({item.ric}): price history, estimate revisions, Reuters news, and context.",
        )
        _retrieve_price_history(ld, client, result, item)
        _retrieve_estimate_history(ld, client, result, item)
        _retrieve_news(ld, client, result, item)
        if index < 3:
            _retrieve_news_stories(ld, client, result, item, workflow.news_stories_per_candidate)
            _retrieve_peers(ld, client, result, item)
            _retrieve_filings(client, result, item)
            _retrieve_esg(client, result, item)
    _emit_progress(
        progress_callback,
        86,
        "Comparing finalists",
        "Deriving revisions, momentum, valuation, evidence coverage, and risk indicators.",
    )
    _derive_metrics(result)
    _derive_evidence_coverage(result)
    _rerank_finalists(result)

    # Optional row-expanding tables are deliberately last and cannot influence
    # whether the core research workflow completes.
    _retrieve_winner_optional_context(ld, client, result, progress_callback)
    _derive_evidence_coverage(result)


def _retrieve_price_history(ld: Any, client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument) -> None:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=result.plan.lookback_days)

    def call_history() -> Any:
        kwargs = {
            "universe": resolved.ric,
            "fields": ["TRDPRC_1"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "interval": "daily",
        }
        header_type = getattr(getattr(ld, "HeaderType", None), "NAME", None)
        if header_type is not None:
            kwargs["header_type"] = header_type
        try:
            return ld.get_history(**kwargs)
        except TypeError:
            kwargs.pop("header_type", None)
            return ld.get_history(**kwargs)

    response = client.call(f"Price history {resolved.ric}", call_history)
    frame = _frame_from_response(response)
    if frame.empty:
        return
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [" ".join(str(item) for item in column if item not in {None, ""}).strip() for column in frame.columns]
    result.tables[f"price:{resolved.ric}"] = frame
    converted = frame.apply(pd.to_numeric, errors="coerce")
    numeric_columns = [column for column in converted.columns if converted[column].notna().any()]
    if not numeric_columns:
        return
    prices = converted[numeric_columns[0]].dropna()
    if len(prices) < 2:
        return
    returns = prices.pct_change().dropna()
    prefix = resolved.ric
    result.metrics[f"{prefix}:last_price"] = float(prices.iloc[-1])
    result.metrics[f"{prefix}:period_return"] = float(prices.iloc[-1] / prices.iloc[0] - 1)
    if len(prices) >= 22:
        result.metrics[f"{prefix}:return_1m"] = float(prices.iloc[-1] / prices.iloc[-22] - 1)
    if len(prices) >= 64:
        result.metrics[f"{prefix}:return_3m"] = float(prices.iloc[-1] / prices.iloc[-64] - 1)
    if len(prices) >= 127:
        result.metrics[f"{prefix}:return_6m"] = float(prices.iloc[-1] / prices.iloc[-127] - 1)
    if len(prices) >= 253:
        result.metrics[f"{prefix}:return_1y"] = float(prices.iloc[-1] / prices.iloc[-253] - 1)
    if not returns.empty:
        result.metrics[f"{prefix}:annualized_vol"] = float(returns.std() * math.sqrt(252))
        running_max = prices.cummax()
        drawdown = prices.div(running_max).sub(1)
        result.metrics[f"{prefix}:max_drawdown"] = float(drawdown.min())


def _date_column(frame: pd.DataFrame) -> Any | None:
    for column in frame.columns:
        text = str(column).casefold()
        if "calcdate" in text or "calc date" in text or text == "date" or text.endswith(".date"):
            return column
    return None


def _value_at_or_before(series: pd.Series, dates: pd.Series, cutoff: pd.Timestamp) -> float | None:
    valid = pd.DataFrame({"date": dates, "value": pd.to_numeric(series, errors="coerce")}).dropna()
    valid = valid[valid["date"] <= cutoff].sort_values("date")
    if valid.empty:
        return None
    return float(valid.iloc[-1]["value"])


def _retrieve_estimate_history(ld: Any, client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument) -> None:
    frame = _safe_get_data(
        ld, client, resolved.ric, ESTIMATE_HISTORY_FIELDS,
        parameters={"SDate": -min(max(result.plan.lookback_days, 120), 730), "EDate": 0, "Frq": "D"},
        label=f"Estimate history {resolved.ric}",
    )
    if frame.empty:
        return
    result.tables[f"estimate_history:{resolved.ric}"] = frame.tail(400).reset_index(drop=True)
    date_col = _date_column(frame)
    if date_col is None:
        return
    dates = pd.to_datetime(frame[date_col], errors="coerce", utc=True)
    if dates.dropna().empty:
        return
    latest_date = dates.max()
    for field_name, metric_name in (
        ("TR.EPSMean(Period=FY1)", "eps_revision"),
        ("TR.RevenueMean(Period=FY1)", "revenue_revision"),
        ("TR.EpsSmartEst(Period=FY1)", "smart_revision"),
    ):
        if field_name not in frame.columns:
            continue
        latest = _value_at_or_before(frame[field_name], dates, latest_date)
        if latest is None:
            continue
        for days in (30, 90):
            prior = _value_at_or_before(frame[field_name], dates, latest_date - pd.Timedelta(days=days))
            if prior not in {None, 0}:
                result.metrics[f"{resolved.ric}:{metric_name}_{days}d"] = (latest - prior) / abs(prior)


def _retrieve_news(ld: Any, client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=min(result.plan.lookback_days, 450))
    response = client.call(
        f"News headlines {resolved.ric}",
        lambda: ld.news.get_headlines(
            query=f"R:{resolved.ric} AND Language:LEN",
            count=50,
            start=start.isoformat(),
            end=end.isoformat(),
        ),
    )
    frame = _frame_from_response(response)
    if not frame.empty:
        result.tables[f"news:{resolved.ric}"] = frame.head(50).reset_index(drop=True)


def _story_id_column(frame: pd.DataFrame) -> Any | None:
    for column in frame.columns:
        if re.sub(r"[^a-z0-9]", "", str(column).casefold()) in {"storyid", "storyidentifier"}:
            return column
    return None


def _strip_html(value: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _retrieve_news_stories(ld: Any, client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument, limit: int) -> None:
    headlines = result.tables.get(f"news:{resolved.ric}", pd.DataFrame())
    if headlines.empty:
        return
    story_col = _story_id_column(headlines)
    headline_col = _headline_column(headlines)
    if story_col is None:
        return
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, row in headlines.iterrows():
        story_id = str(row.get(story_col) or "").strip()
        if not story_id or story_id in seen:
            continue
        seen.add(story_id)
        story = client.call(f"News story {resolved.ric} {story_id}", lambda sid=story_id: ld.news.get_story(sid), warn=False)
        if not story:
            continue
        records.append({
            "Instrument": resolved.ric,
            "story_id": story_id,
            "headline": str(row.get(headline_col) or "").strip() if headline_col is not None else "",
            "story_text": _strip_html(str(story))[:6000],
        })
        if len(records) >= limit:
            break
    if records:
        result.tables[f"stories:{resolved.ric}"] = pd.DataFrame(records)


def _retrieve_market_news(ld: Any, client: _LSEGClient, result: ResearchResult) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=min(result.plan.lookback_days, 30))
    query = result.plan.raw_request.strip() or "Top News"
    response = client.call(
        "Market news",
        lambda: ld.news.get_headlines(query=query, count=40, start=start.isoformat(), end=end.isoformat()),
    )
    frame = _frame_from_response(response)
    if not frame.empty:
        result.tables["market_news"] = frame


def _retrieve_peers(ld: Any, client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument) -> None:
    from lseg.data.discovery import Peers

    peers = client.call(f"Peers {resolved.ric}", lambda: list(Peers(resolved.ric)))
    if not peers:
        return
    peer_rics = [str(item) for item in peers[:20] if str(item) != resolved.ric]
    if resolved.ric not in peer_rics:
        peer_rics.insert(0, resolved.ric)
    frame = _safe_get_data(ld, client, peer_rics[:20], PEER_FIELDS, label=f"Peer comparison {resolved.ric}")
    if not frame.empty:
        result.tables[f"peers:{resolved.ric}"] = frame


def _retrieve_stakeholders(client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument, kind: str) -> None:
    if kind == "suppliers":
        from lseg.data.discovery import Suppliers as Stakeholders
    else:
        from lseg.data.discovery import Customers as Stakeholders
    object_ = Stakeholders(resolved.ric)
    response = client.call(f"{kind.title()} {resolved.ric}", object_.get_data)
    frame = _frame_from_response(response)
    if frame.empty:
        frame = _frame_from_response(getattr(object_, "df", None))
    if not frame.empty:
        result.tables[f"{kind}:{resolved.ric}"] = frame


def _retrieve_esg(client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument) -> None:
    from lseg.data.content import esg

    response = client.call(
        f"ESG overview {resolved.ric}",
        lambda: esg.basic_overview.Definition(universe=resolved.ric).get_data(),
    )
    frame = _frame_from_response(response)
    if not frame.empty:
        result.tables[f"esg:{resolved.ric}"] = frame


def _retrieve_filings(client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument) -> None:
    from lseg.data.content import filings

    org_id = _first_value(result, "profile", "TR.OrganizationID", resolved.ric)
    if _missing(org_id):
        result.warnings.append(f"Filings {resolved.ric}: organization ID unavailable.")
        return
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=result.plan.lookback_days)
    response = client.call(
        f"Filings {resolved.ric}",
        lambda: filings.search.Definition(
            org_id=str(org_id),
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            limit=10,
            sort_order="DESC",
        ).get_data(),
    )
    frame = _frame_from_response(response)
    if not frame.empty:
        result.tables[f"filings:{resolved.ric}"] = frame


def _derive_metrics(result: ResearchResult) -> None:
    for resolved in result.resolved:
        ric = resolved.ric
        price = _numeric(_first_value(result, "profile", "TR.PriceClose", ric))
        if price is None:
            price = _numeric(result.metrics.get(f"{ric}:last_price"))
        target = _numeric(_first_value(result, "recommendations", "TR.PriceTargetMean", ric))
        if price and target:
            result.metrics[f"{ric}:target_upside"] = target / price - 1

        eps_mean = _numeric(_first_value(result, "estimates", "TR.EPSMean(Period=FY1)", ric))
        smart = _numeric(_first_value(result, "estimates", "TR.EpsSmartEst(Period=FY1)", ric))
        if eps_mean not in {None, 0} and smart is not None:
            result.metrics[f"{ric}:smart_gap"] = (smart - eps_mean) / abs(eps_mean)
        low = _numeric(_first_value(result, "estimates", "TR.EPSLow(Period=FY1)", ric))
        high = _numeric(_first_value(result, "estimates", "TR.EPSHigh(Period=FY1)", ric))
        if eps_mean not in {None, 0} and low is not None and high is not None:
            result.metrics[f"{ric}:estimate_dispersion"] = (high - low) / abs(eps_mean)

        revenue = _numeric(_first_value(result, "fundamentals", "TR.TotalRevenue(Period=LTM,Methodology=InterimSum)", ric))
        gross = _numeric(_first_value(result, "fundamentals", "TR.GrossProfit(Period=LTM,Methodology=InterimSum)", ric))
        operating = _numeric(_first_value(result, "fundamentals", "TR.OperatingProfit(Period=LTM)", ric))
        fcf = _numeric(_first_value(result, "fundamentals", "TR.FreeCashFlow(Period=LTM)", ric))
        debt = _numeric(_first_value(result, "fundamentals", "TR.F.DebtTot", ric))
        cash = _numeric(_first_value(result, "fundamentals", "TR.F.CashCashEquiv", ric))
        if revenue not in {None, 0} and gross is not None:
            result.metrics[f"{ric}:gross_margin"] = gross / revenue
        if revenue not in {None, 0} and operating is not None:
            result.metrics[f"{ric}:operating_margin"] = operating / revenue
        if revenue not in {None, 0} and fcf is not None:
            result.metrics[f"{ric}:fcf_margin"] = fcf / revenue
        if debt is not None:
            result.metrics[f"{ric}:net_debt"] = debt - (cash or 0)
            if fcf not in {None, 0}:
                result.metrics[f"{ric}:debt_to_fcf"] = debt / abs(fcf)

        peer_frame = result.tables.get(f"peers:{ric}")
        if peer_frame is not None and not peer_frame.empty:
            peers = peer_frame
            if "Instrument" in peers.columns:
                peers = peers[peers["Instrument"].astype(str) != ric]
            for field_name in (
                "TR.PE", "TR.PtoEPSMeanEst(Period=FY1)", "TR.EVToEBITDA",
                "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", "TR.ROAPercentTrailing12M",
                "TR.TotalReturn3Mo", "TR.PriceTargetMean",
            ):
                values = _column(peers, field_name).dropna()
                if not values.empty:
                    result.metrics[f"{ric}:peer_median:{field_name}"] = float(values.median())

        headlines = result.tables.get(f"news:{ric}", pd.DataFrame())
        headline_col = _headline_column(headlines) if not headlines.empty else None
        if headline_col is not None:
            text = " ".join(headlines[headline_col].dropna().astype(str).head(30)).casefold()
            risk_words = ("cuts outlook", "warning", "probe", "lawsuit", "downgrade", "misses", "decline", "weak", "layoff", "recall", "debt")
            catalyst_words = ("raises outlook", "beats", "upgrade", "contract", "order", "approval", "launch", "record", "buyback", "dividend")
            result.metrics[f"{ric}:risk_headline_hits"] = sum(text.count(word) for word in risk_words)
            result.metrics[f"{ric}:catalyst_headline_hits"] = sum(text.count(word) for word in catalyst_words)


def _derive_evidence_coverage(result: ResearchResult) -> None:
    families = (
        "profile", "fundamentals", "profitability", "valuation", "estimates", "recommendations",
        "risk", "guidance", "events", "ownership", "insiders",
    )
    for resolved in result.resolved:
        ric = resolved.ric
        available: list[str] = []
        for family in families:
            frame = result.tables.get(family)
            if frame is None or frame.empty:
                continue
            subset = frame
            if "Instrument" in frame.columns:
                subset = frame[frame["Instrument"].astype(str) == ric]
            if not subset.empty and subset.drop(columns=["Instrument"], errors="ignore").notna().any().any():
                available.append(family)
        for family, table_name in (
            ("price_history", f"price:{ric}"), ("estimate_history", f"estimate_history:{ric}"),
            ("news", f"news:{ric}"), ("stories", f"stories:{ric}"), ("peers", f"peers:{ric}"),
            ("filings", f"filings:{ric}"), ("esg", f"esg:{ric}"),
        ):
            frame = result.tables.get(table_name)
            if frame is not None and not frame.empty:
                available.append(family)
        result.metrics[f"{ric}:evidence_families"] = available
        result.metrics[f"{ric}:evidence_family_count"] = len(available)
    result.metrics["evidence_coverage"] = {
        resolved.ric: result.metrics.get(f"{resolved.ric}:evidence_families", [])
        for resolved in result.resolved
    }



def _rerank_finalists(result: ResearchResult) -> None:
    """Combine the broad multi-factor screen with deep-dive evidence coverage.

    This is a shortlist priority score, not a return forecast. It prevents a
    candidate with one attractive metric and sparse evidence from automatically
    remaining first.
    """
    workflow = get_workflow(result.plan.workflow, result.plan.mode, candidate_search=True)
    ranked: list[tuple[float, ResolvedInstrument]] = []
    for resolved in result.resolved:
        ric = resolved.ric
        row = _screen_row(result, ric)
        base = _numeric(row.get("Research Score")) if row is not None else None
        score = base if base is not None else 50.0
        family_count = int(result.metrics.get(f"{ric}:evidence_family_count", 0) or 0)
        score += min(family_count, 12) * 1.25
        if family_count < workflow.minimum_evidence_families:
            score -= 15.0
        revision = result.metrics.get(f"{ric}:eps_revision_30d")
        if isinstance(revision, (int, float)):
            score += max(-6.0, min(6.0, revision * 100))
        smart_gap = result.metrics.get(f"{ric}:smart_gap")
        if isinstance(smart_gap, (int, float)):
            score += max(-4.0, min(4.0, smart_gap * 100))
        upside = result.metrics.get(f"{ric}:target_upside")
        if isinstance(upside, (int, float)):
            score += max(-5.0, min(5.0, upside * 20))
        risk_hits = result.metrics.get(f"{ric}:risk_headline_hits")
        if isinstance(risk_hits, int):
            score -= min(risk_hits, 4) * 1.5
        vol = result.metrics.get(f"{ric}:annualized_vol")
        if isinstance(vol, (int, float)) and vol > 0.45:
            score -= 4.0
        result.metrics[f"{ric}:finalist_score"] = score
        ranked.append((score, resolved))
    result.resolved = [item for _, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)]


def run_research(
    plan: ResearchPlan,
    settings: Settings,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Any | None = None,
) -> ResearchResult:
    try:
        import lseg.data as ld
    except Exception as exc:
        raise LSEGResearchError("The lseg-data package is not installed.") from exc

    _raise_if_cancelled(cancel_event)
    plan = plan.normalized()
    workflow = get_workflow(plan.workflow, plan.mode, candidate_search=plan.screen.candidate_search)
    _emit_progress(
        progress_callback,
        5,
        "Preparing workflow",
        f"Selected {workflow.workflow_id.replace('_', ' ')} with {len(workflow.stages)} stages.",
    )
    result = ResearchResult(plan=plan)
    result.metrics["workflow"] = workflow.to_dict()
    session = None
    try:
        _emit_progress(
            progress_callback,
            8,
            "Connecting to Workspace",
            f"Opening LSEG session {settings.lseg_session_name}.",
        )
        session = _open_lseg_session(ld, settings)
        _emit_progress(
            progress_callback,
            12,
            "Workspace connected",
            f"LSEG session state: {_session_state_text(session)}.",
        )
        client = _LSEGClient(result, progress_callback=progress_callback, cancel_event=cancel_event)
        result.calls.append(f"Session {_session_state_text(session)}")

        if workflow.workflow_id in {"sector_opportunity", "stock_screen"}:
            _emit_progress(
                progress_callback,
                16,
                "Building the stock universe",
                "Executing the validated LSEG screen and resolving matching instruments.",
            )
            _retrieve_screen(ld, client, result)
            _emit_progress(
                progress_callback,
                35,
                "Screen ranked",
                f"Ranked {result.metrics.get('screen_ranked_count', 0)} candidates from "
                f"{result.metrics.get('screen_universe_count', 0)} screened instruments.",
            )
            if workflow.workflow_id == "sector_opportunity":
                _retrieve_candidate_deep_dive(ld, client, result, progress_callback)
        elif workflow.workflow_id == "market_news":
            _emit_progress(progress_callback, 25, "Retrieving market news", "Searching current Reuters/LSEG headlines.")
            _retrieve_market_news(ld, client, result)
            _emit_progress(progress_callback, 88, "Organizing market evidence", "Preparing the relevant headline set.")
        else:
            if not plan.entities:
                raise LSEGResearchError("No company, ticker, or RIC was identified in the request.")
            _emit_progress(
                progress_callback,
                16,
                "Resolving instruments",
                f"Resolving {len(plan.entities)} named company or ticker reference(s).",
            )
            for entity in plan.entities:
                result.resolved.append(resolve_instrument(entity))
            rics = [item.ric for item in result.resolved]
            _emit_progress(
                progress_callback,
                24,
                "Retrieving company fundamentals",
                f"Collecting a consistent evidence bundle for {len(rics)} instrument(s).",
            )
            result.metrics["deep_dive_count"] = len(rics)
            result.metrics["workflow_stages"] = [stage.stage_id for stage in workflow.stages]

            # Every deep-dive workflow receives the same comprehensive core.
            for topic in ("profile", "fundamentals", "profitability", "valuation", "estimates", "recommendations", "risk"):
                frame = _safe_get_data(ld, client, rics, TOPIC_FIELDS[topic], label=topic.title())
                if not frame.empty:
                    result.tables[topic] = frame

            _emit_progress(
                progress_callback,
                45,
                "Researching company evidence",
                "Core data is complete. Retrieving price history, estimate revisions, Reuters news, peers, filings, and ESG.",
            )

            total_companies = max(len(result.resolved), 1)
            for index, resolved in enumerate(result.resolved):
                company_percent = 58 + int((index / total_companies) * 28)
                _emit_progress(
                    progress_callback,
                    company_percent,
                    f"Researching company {index + 1}/{len(result.resolved)}",
                    f"{resolved.company_name} ({resolved.ric}): price, revisions, Reuters news, peers, filings, and ESG.",
                )
                _retrieve_price_history(ld, client, result, resolved)
                _retrieve_estimate_history(ld, client, result, resolved)
                _retrieve_news(ld, client, result, resolved)
                if index < 3:
                    _retrieve_news_stories(ld, client, result, resolved, workflow.news_stories_per_candidate)
                    _retrieve_peers(ld, client, result, resolved)
                    _retrieve_filings(client, result, resolved)
                    _retrieve_esg(client, result, resolved)
                if "suppliers" in plan.topics:
                    _retrieve_stakeholders(client, result, resolved, "suppliers")
                if "customers" in plan.topics:
                    _retrieve_stakeholders(client, result, resolved, "customers")

            _emit_progress(
                progress_callback,
                86,
                "Deriving research findings",
                "Calculating revisions, momentum, valuation comparisons, and evidence coverage.",
            )
            _derive_metrics(result)
            _derive_evidence_coverage(result)

            # Only a single-company deep dive receives row-expanding ownership
            # and insider enrichment. Comparisons stay on a consistent evidence
            # bundle rather than blocking on optional holder tables.
            if workflow.workflow_id == "company_deep_dive":
                _retrieve_winner_optional_context(ld, client, result, progress_callback)
                _derive_evidence_coverage(result)

        _raise_if_cancelled(cancel_event)
        result.metrics["api_call_count"] = len(result.calls)
        _emit_progress(
            progress_callback,
            96,
            "Evidence collection complete",
            f"Completed {len(result.calls)} LSEG API operations; preparing the concise report.",
        )
        if not result.has_data:
            detail = " | ".join(result.warnings[:5]) or "No rows were returned."
            raise LSEGResearchError(f"LSEG returned no usable research data. {detail}")
        return result
    except ResearchCancelled:
        _emit_progress(progress_callback, None, "Research stopped", "Stopped by user.")
        raise
    except LSEGResearchError as exc:
        _emit_progress(progress_callback, None, "Research failed", str(exc))
        raise
    except Exception as exc:
        _emit_progress(progress_callback, None, "Research failed", f"{type(exc).__name__}: {exc}")
        raise LSEGResearchError(f"LSEG research failed: {type(exc).__name__}: {exc}") from exc
    finally:
        if session is not None:
            try:
                ld.close_session()
            except Exception:
                pass


def _format_number(value: Any, *, percent: bool = False) -> str:
    number = _numeric(value)
    if number is None:
        return "n/a"
    if percent:
        return f"{number * 100:+.1f}%"
    if abs(number) >= 1_000_000_000_000:
        return f"{number / 1_000_000_000_000:.2f}T"
    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _headline_column(frame: pd.DataFrame) -> Any | None:
    for column in frame.columns:
        lowered = str(column).casefold()
        if "headline" in lowered or lowered in {"text", "title"}:
            return column
    return None


def _latest_titles(frame: pd.DataFrame, limit: int = 2) -> list[str]:
    column = _headline_column(frame)
    if column is None:
        return []
    titles: list[str] = []
    for value in frame[column].dropna().astype(str):
        cleaned = re.sub(r"\s+", " ", value).strip()
        if cleaned and cleaned not in titles:
            titles.append(cleaned)
        if len(titles) >= limit:
            break
    return titles


def _company_name(result: ResearchResult, resolved: ResolvedInstrument) -> str:
    name = _first_value(result, "profile", "TR.CommonName", resolved.ric)
    return str(name) if not _missing(name) else (resolved.company_name or resolved.query)


def _screen_row(result: ResearchResult, ric: str) -> pd.Series | None:
    frame = result.tables.get("screen", pd.DataFrame())
    if frame.empty or "Instrument" not in frame.columns:
        return None
    selected = frame[frame["Instrument"].astype(str) == ric]
    return None if selected.empty else selected.iloc[0]


def _sector_median(result: ResearchResult, field_name: str) -> float | None:
    frame = result.tables.get("screen", pd.DataFrame())
    values = _column(frame, field_name).dropna()
    return None if values.empty else float(values.median())


def _candidate_opportunities(result: ResearchResult, resolved: ResolvedInstrument) -> list[str]:
    ric = resolved.ric
    row = _screen_row(result, ric)
    items: list[str] = []
    fpe = _numeric(_first_value(result, "valuation", "TR.PtoEPSMeanEst(Period=FY1)", ric))
    median_fpe = _sector_median(result, "TR.PtoEPSMeanEst(Period=FY1)")
    if fpe and median_fpe and fpe < median_fpe * 0.9:
        items.append(f"Forward P/E {_format_number(fpe)} is below the screened median {_format_number(median_fpe)}.")
    roe = _numeric(_first_value(result, "profitability", "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", ric))
    median_roe = _sector_median(result, "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM")
    if roe is not None and median_roe is not None and roe > median_roe:
        items.append(f"ROE {_format_number(roe)}% exceeds the screened median {_format_number(median_roe)}%.")
    fcf_margin = result.metrics.get(f"{ric}:fcf_margin")
    if isinstance(fcf_margin, (int, float)) and fcf_margin > 0.08:
        items.append(f"Free-cash-flow margin is {_format_number(fcf_margin, percent=True)}.")
    revision = result.metrics.get(f"{ric}:eps_revision_30d")
    if isinstance(revision, (int, float)) and revision > 0.01:
        items.append(f"FY1 EPS consensus rose {_format_number(revision, percent=True)} over roughly 30 days.")
    smart_gap = result.metrics.get(f"{ric}:smart_gap")
    if isinstance(smart_gap, (int, float)) and smart_gap > 0.005:
        items.append(f"SmartEstimate is {_format_number(smart_gap, percent=True)} above mean EPS consensus.")
    upside = result.metrics.get(f"{ric}:target_upside")
    if isinstance(upside, (int, float)) and upside > 0.08:
        items.append(f"Mean analyst target is {_format_number(upside, percent=True)} above the current price.")
    momentum = result.metrics.get(f"{ric}:return_3m")
    if isinstance(momentum, (int, float)) and momentum > 0.05:
        items.append(f"Three-month price return is {_format_number(momentum, percent=True)}.")
    if row is not None and not _missing(row.get("TR.DividendYield")):
        dividend = _numeric(row.get("TR.DividendYield"))
        if dividend is not None and dividend > 2:
            items.append(f"Dividend yield is {_format_number(dividend)}%.")
    return items[:4]


def _candidate_risks(result: ResearchResult, resolved: ResolvedInstrument) -> list[str]:
    ric = resolved.ric
    items: list[str] = []
    fpe = _numeric(_first_value(result, "valuation", "TR.PtoEPSMeanEst(Period=FY1)", ric))
    median_fpe = _sector_median(result, "TR.PtoEPSMeanEst(Period=FY1)")
    if fpe and median_fpe and fpe > median_fpe * 1.25:
        items.append(f"Forward P/E {_format_number(fpe)} is well above the screened median {_format_number(median_fpe)}.")
    revision = result.metrics.get(f"{ric}:eps_revision_30d")
    if isinstance(revision, (int, float)) and revision < -0.01:
        items.append(f"FY1 EPS consensus fell {_format_number(abs(revision), percent=True)} over roughly 30 days.")
    upside = result.metrics.get(f"{ric}:target_upside")
    if isinstance(upside, (int, float)) and upside < 0:
        items.append(f"Mean analyst target is {_format_number(abs(upside), percent=True)} below the current price.")
    vol = result.metrics.get(f"{ric}:annualized_vol")
    if isinstance(vol, (int, float)) and vol > 0.35:
        items.append(f"Annualized realized volatility is {_format_number(vol, percent=True)}.")
    drawdown = result.metrics.get(f"{ric}:max_drawdown")
    if isinstance(drawdown, (int, float)) and drawdown < -0.20:
        items.append(f"Maximum drawdown in the retrieved period was {_format_number(drawdown, percent=True)}.")
    debt_to_fcf = result.metrics.get(f"{ric}:debt_to_fcf")
    if isinstance(debt_to_fcf, (int, float)) and debt_to_fcf > 5:
        items.append(f"Total debt equals about {_format_number(debt_to_fcf)} times LTM free cash flow.")
    dispersion = result.metrics.get(f"{ric}:estimate_dispersion")
    if isinstance(dispersion, (int, float)) and dispersion > 0.20:
        items.append(f"Analyst EPS range is wide at {_format_number(dispersion, percent=True)} of consensus.")
    risk_hits = result.metrics.get(f"{ric}:risk_headline_hits")
    if isinstance(risk_hits, int) and risk_hits > 0:
        items.append(f"Recent headlines contain {risk_hits} risk-related signal(s); read the cited stories before acting.")
    return items[:4]


def _deterministic_company_report(result: ResearchResult) -> str:
    lines: list[str] = []
    for index, resolved in enumerate(result.resolved):
        if index:
            lines.append("")
        ric = resolved.ric
        lines.append(f"Company: {_company_name(result, resolved)} ({ric})")
        opportunities = _candidate_opportunities(result, resolved)
        risks = _candidate_risks(result, resolved)
        if opportunities:
            lines.append("Opportunity: " + " ".join(opportunities[:2]))
        else:
            lines.append("Opportunity: No strong opportunity signal was supported by the available fields.")
        valuation_parts: list[str] = []
        pe = _numeric(_first_value(result, "valuation", "TR.PE", ric))
        fpe = _numeric(_first_value(result, "valuation", "TR.PtoEPSMeanEst(Period=FY1)", ric))
        if pe is not None:
            valuation_parts.append(f"P/E {_format_number(pe)}")
        if fpe is not None:
            valuation_parts.append(f"forward P/E {_format_number(fpe)}")
        if valuation_parts:
            lines.append("Valuation and expectations: " + ", ".join(valuation_parts) + ".")
        titles = _latest_titles(result.tables.get(f"news:{ric}", pd.DataFrame()), 2)
        if titles:
            lines.append("Recent developments: " + " | ".join(titles))
        if risks:
            lines.append("Major risks: " + " ".join(risks[:2]))
        else:
            lines.append("Major risks: The retrieved quantitative fields did not identify a dominant risk; news and filing coverage may still be incomplete.")
        families = result.metrics.get(f"{ric}:evidence_families", [])
        lines.append(f"Evidence: {len(families)} families available: {', '.join(families) if families else 'limited data' }.")
    return "\n".join(lines)


def _deterministic_screen_report(result: ResearchResult) -> str:
    frame = result.tables.get("screen", pd.DataFrame())
    expression = result.metrics.get("screen_expression")
    if result.plan.workflow == "sector_opportunity" or result.plan.screen.candidate_search:
        if not result.resolved:
            return "The sector screen ran, but no finalist had enough usable data for a deep dive."
        candidate = result.resolved[0]
        ric = candidate.ric
        row = _screen_row(result, ric)
        lines = [f"Candidate: {_company_name(result, candidate)} ({ric})"]
        opportunities = _candidate_opportunities(result, candidate)
        risks = _candidate_risks(result, candidate)
        lines.append("Opportunity: " + (" ".join(opportunities[:2]) if opportunities else "The ranking was supported mainly by relative factor scores, not a clear standalone opportunity."))
        titles = _latest_titles(result.tables.get(f"news:{ric}", pd.DataFrame()), 2)
        if titles:
            lines.append("Catalysts or developments: " + " | ".join(titles))
        lines.append("Major risks: " + (" ".join(risks[:2]) if risks else "No dominant quantitative risk was available; review the retrieved news and filings."))
        alternatives: list[str] = []
        for other in result.resolved[1:3]:
            other_row = _screen_row(result, other.ric)
            score = _numeric(other_row.get("Research Score")) if other_row is not None else None
            alternatives.append(f"{_company_name(result, other)} ({other.ric})" + (f" score {_format_number(score)}" if score is not None else ""))
        if alternatives:
            lines.append("Other finalists: " + "; ".join(alternatives) + ".")
        families = result.metrics.get(f"{ric}:evidence_families", [])
        lines.append(
            f"Coverage: screened {int(result.metrics.get('screen_universe_count', len(frame)))} companies; "
            f"deeply researched {int(result.metrics.get('deep_dive_count', len(result.resolved)))}; "
            f"selected candidate has {len(families)} evidence families."
        )
        return "\n".join(lines)

    lines = [f"Screen results ({len(frame)} shown)"]
    if expression:
        lines.append(f"Criteria: {expression}")
    for _, row in frame.head(10).iterrows():
        name = row.get("TR.CommonName") or row.get("Instrument") or "Unknown"
        ticker = row.get("TR.TickerSymbol")
        label = f"{name} ({ticker})" if not _missing(ticker) else str(name)
        parts: list[str] = []
        for field_name in ("TR.CompanyMarketCap", "TR.PE", "TR.PtoEPSMeanEst(Period=FY1)", "TR.EVToEBITDA", "TR.TotalReturn3Mo"):
            value = row.get(field_name)
            if _missing(value):
                continue
            formatted = f"{_format_number(value)}%" if field_name == "TR.TotalReturn3Mo" else _format_number(value)
            parts.append(f"{FIELD_LABELS.get(field_name, field_name)} {formatted}")
        lines.append(f"• {label}: " + ", ".join(parts[:4]))
    return "\n".join(lines)


def _deterministic_news_report(result: ResearchResult) -> str:
    frame = result.tables.get("market_news", pd.DataFrame())
    titles = _latest_titles(frame, 7)
    lines = ["Market news"]
    lines.extend(f"• {title}" for title in titles)
    if not titles:
        lines.append("• Headlines were returned, but no recognizable headline column was present.")
    return "\n".join(lines)


def _evidence_payload(result: ResearchResult) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for name, frame in result.tables.items():
        if frame is None or frame.empty:
            continue
        if name == "screen":
            limited = frame.head(12).copy()
        elif name.startswith("estimate_history:"):
            limited = frame.tail(20).copy()
        elif name.startswith("news:"):
            limited = frame.head(12).copy()
        elif name.startswith("stories:"):
            limited = frame.head(3).copy()
        else:
            limited = frame.head(15).copy()
        for column in limited.columns:
            limited[column] = limited[column].map(
                lambda value: None if _missing(value) else str(value) if not isinstance(value, (int, float, bool)) else value
            )
        tables[name] = limited.to_dict(orient="records")
    return {
        "request": result.plan.raw_request,
        "workflow": result.plan.workflow,
        "investment_horizon": result.plan.investment_horizon,
        "resolved": [
            {"name": _company_name(result, item), "ticker": item.ticker, "ric": item.ric}
            for item in result.resolved
        ],
        "derived_metrics": result.metrics,
        "tables": tables,
        "unavailable": result.warnings[:20],
        "research_trace": result.calls,
    }


def _plain_text_report(text: str) -> str:
    cleaned = re.sub(r"```(?:text|markdown)?", "", text, flags=re.I)
    cleaned = cleaned.replace("```", "")
    cleaned = re.sub(r"^\s{0,3}#{1,6}\s*", "", cleaned, flags=re.M)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"^\s*[-*]\s+", "• ", cleaned, flags=re.M)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _llm_report(result: ResearchResult, settings: Settings, cancel_event: Any | None = None) -> str | None:
    if not settings.groq_api_key:
        return None
    _raise_if_cancelled(cancel_event)
    try:
        from langchain_groq import ChatGroq

        llm = ChatGroq(model=settings.groq_model, temperature=0, max_retries=2, api_key=settings.groq_api_key)
        evidence = json.dumps(_evidence_payload(result), default=str)
        workflow = result.plan.workflow
        if workflow == "sector_opportunity":
            format_instruction = (
                "Return exactly these plain-text lines, with no Markdown symbols:\n"
                "Candidate: one finalist, or 'No adequately supported candidate'\n"
                "Opportunity: the two strongest evidence-backed advantages\n"
                "Catalyst: the strongest supported catalyst or recent development\n"
                "Major risks: the two most material supported risks\n"
                "Why selected: why it beat the other finalists\n"
                "Coverage: number screened, number deeply researched, and evidence families available"
            )
        elif workflow == "company_compare":
            format_instruction = (
                "Return at most seven plain-text lines: Best-supported relative strength, valuation comparison, expectations, catalyst, major risks, winner if evidence supports one, and coverage. No Markdown symbols."
            )
        elif workflow == "market_news":
            format_instruction = "Return a plain-text title and at most six concise developments. No Markdown symbols."
        else:
            format_instruction = (
                "Return exactly these plain-text lines: Company, Opportunity, Catalyst, Major risks, Valuation and expectations, Coverage. No Markdown symbols."
            )

        response = llm.invoke(
            [
                (
                    "system",
                    "You are the evidence-synthesis stage of a deterministic LSEG research workflow. "
                    "You do not choose API calls and you may not add outside knowledge. Use only supplied evidence. "
                    "Identify major opportunities, catalysts, risks, contradictions, and missing coverage. "
                    "Do not call a company promising because of one metric. Do not issue a buy or sell recommendation. "
                    "Every factual claim must be directly supported by a supplied value, story, headline, filing, or derived metric. "
                    + format_instruction,
                ),
                ("human", evidence),
            ]
        )
        _raise_if_cancelled(cancel_event)
        draft = _plain_text_report(str(getattr(response, "content", response)).strip())
        if not draft:
            return None

        # A second constrained pass acts as a claim guard. It removes statements
        # that cannot be traced to the retrieved evidence rather than adding new analysis.
        verification = llm.invoke(
            [
                (
                    "system",
                    "Verify the draft report against the supplied evidence. Remove or rewrite every unsupported claim. "
                    "Preserve the requested labels, concise length, and plain-text formatting. Add nothing from outside the evidence. Return only the corrected report.",
                ),
                ("human", "EVIDENCE:\n" + evidence + "\n\nDRAFT:\n" + draft),
            ]
        )
        _raise_if_cancelled(cancel_event)
        return _plain_text_report(str(getattr(verification, "content", verification)).strip()) or draft
    except ResearchCancelled:
        raise
    except Exception as exc:
        result.warnings.append(f"Evidence synthesis: {type(exc).__name__}: {exc}")
        return None


def concise_report(result: ResearchResult, settings: Settings, cancel_event: Any | None = None) -> str:
    _raise_if_cancelled(cancel_event)
    generated = _llm_report(result, settings, cancel_event=cancel_event)
    if generated:
        return generated
    if result.plan.mode == "screen":
        return _deterministic_screen_report(result)
    if result.plan.mode == "market_news":
        return _deterministic_news_report(result)
    return _deterministic_company_report(result)


def _deterministic_valuation_follow_up(result: ResearchResult) -> str:
    if not result.resolved:
        return "The prior research did not select a company, so there is no valuation case to explain."

    selected = result.resolved[0]
    ric = selected.ric
    name = _company_name(result, selected)
    comparisons: list[str] = []
    valuation_fields = (
        ("TR.PtoEPSMeanEst(Period=FY1)", "forward P/E"),
        ("TR.EVToEBITDA", "EV/EBITDA"),
        ("TR.PricetoCFPerShare", "price/cash flow"),
        ("TR.PriceToBVPerShare", "price/book"),
    )
    for field_name, label in valuation_fields:
        value = _numeric(_first_value(result, "valuation", field_name, ric))
        if value is None:
            row = _screen_row(result, ric)
            value = _numeric(row.get(field_name)) if row is not None else None
        median = _sector_median(result, field_name)
        if value is None or median is None or value <= 0 or median <= 0:
            continue
        difference = 1.0 - (value / median)
        if difference > 0:
            discount = _format_number(abs(difference), percent=True).lstrip("+")
            comparisons.append(
                f"Its {label} is {_format_number(value)} versus {_format_number(median)} for the screened "
                f"peer median, about {discount} lower."
            )

    upside = result.metrics.get(f"{ric}:target_upside")
    if isinstance(upside, (int, float)) and upside > 0:
        comparisons.append(
            f"The mean analyst price target implies {_format_number(upside, percent=True)} upside, "
            "which supports the relative-value case but does not prove intrinsic value."
        )

    lines = [f"{name} ({ric}) looks relatively inexpensive rather than definitively undervalued."]
    if comparisons:
        lines.extend(comparisons[:3])
    else:
        lines.append(
            "The retrieved evidence does not show a clear discount on the available valuation measures, "
            "so calling it undervalued would overstate the data."
        )
    lines.append(
        "The discount could reflect real risks, so the valuation case should be weighed against the reported "
        "earnings revisions, leverage, volatility, news, and filing evidence."
    )
    return "\n".join(lines)


def answer_follow_up(result: ResearchResult, question: str, settings: Settings) -> str:
    """Answer a contextual question using only the immediately prior research result."""
    lower = question.casefold()
    if "undervalu" in lower or "valuation" in lower:
        fallback = _deterministic_valuation_follow_up(result)
    else:
        fallback = concise_report(result, replace(settings, groq_api_key=None))

    if not settings.groq_api_key:
        return fallback
    try:
        from langchain_groq import ChatGroq

        llm = ChatGroq(model=settings.groq_model, temperature=0, max_retries=2, api_key=settings.groq_api_key)
        evidence = json.dumps(_evidence_payload(result), default=str)
        response = llm.invoke(
            [
                (
                    "system",
                    "Answer the follow-up using only the supplied prior LSEG research evidence. "
                    "Refer to the selected company by name. Distinguish relative cheapness from proven intrinsic undervaluation, "
                    "quantify relevant comparisons, mention contrary evidence or missing data, and do not add outside knowledge. "
                    "Return concise plain text with no Markdown heading.",
                ),
                ("human", f"FOLLOW-UP: {question}\n\nPRIOR EVIDENCE:\n{evidence}"),
            ]
        )
        draft = _plain_text_report(str(getattr(response, "content", response)).strip())
        if not draft:
            return fallback
        verification = llm.invoke(
            [
                (
                    "system",
                    "Check the answer against the evidence. Remove unsupported claims and preserve a concise direct answer. "
                    "Return only corrected plain text.",
                ),
                ("human", f"EVIDENCE:\n{evidence}\n\nANSWER:\n{draft}"),
            ]
        )
        return _plain_text_report(str(getattr(verification, "content", verification)).strip()) or draft
    except Exception:
        return fallback


# Compatibility wrappers retained for older tests and external scripts.
@dataclass
class LSEGResearchResult:
    resolved: ResolvedInstrument
    values: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _extract_values(frame: Any, requested_fields: tuple[str, ...]) -> dict[str, Any]:
    canonical = _canonicalize(frame, requested_fields)
    if canonical.empty:
        return {}
    row = canonical.iloc[0]
    return {field_name: row[field_name] for field_name in requested_fields if field_name in canonical.columns}


def deterministic_summary(result: LSEGResearchResult) -> str:
    name = result.resolved.company_name or result.resolved.query
    lines = [f"{name} ({result.resolved.ric})"]
    for field_name, value in result.values.items():
        lines.append(f"• {FIELD_LABELS.get(field_name, field_name)}: {_format_number(value)}")
    if result.warnings:
        lines.append("Unavailable: " + "; ".join(result.warnings))
    return "\n".join(lines)
