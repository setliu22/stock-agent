"""Natural-language LSEG deep research executor.

The planner decides what the user asked for. This module performs only
research-safe, read-only calls, records failures caused by entitlements or field
availability, derives comparable metrics, and returns a concise evidence-based
report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import html
import json
import math
import re
import time
from typing import Any, Callable, Iterable

import pandas as pd

from .company_resolver import InstrumentResolutionError, ResolvedInstrument
from .config import Settings
from .market_regime import ResearchWeights, macro_default_policy
from .research_planner import (
    ResearchPlan,
    ScreenFilters,
    canonicalize_sector,
    classification_definition,
)
from .research_execution import compile_execution_request
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
        "TR.HQCountryCode", "TR.TRBCEconomicSector", "TR.TRBCEconSectorCode",
        "TR.TRBCBusinessSector", "TR.TRBCBusinessSectorCode",
        "TR.TRBCIndustryGroup", "TR.TRBCIndustryGroupCode",
        "TR.TRBCIndustry", "TR.TRBCIndustryCode", "TR.CompanyMarketCap", "TR.EV",
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
    "TR.EPSMean(Period=FY1).calcdate", "TR.EPSMean(Period=FY1).periodenddate",
    "TR.EPSMean(Period=FY1)",
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
    "TR.CommonName", "TR.TickerSymbol", "TR.HQCountryCode",
    "TR.TRBCEconomicSector", "TR.TRBCEconSectorCode",
    "TR.TRBCBusinessSector", "TR.TRBCBusinessSectorCode",
    "TR.TRBCIndustryGroup", "TR.TRBCIndustryGroupCode",
    "TR.TRBCIndustry", "TR.TRBCIndustryCode",
    "TR.CompanyMarketCap", "TR.PriceClose", "TR.PE", "TR.PtoEPSMeanEst(Period=FY1)",
    "TR.EVToEBITDA", "TR.PriceToSalesPerShare", "TR.PriceToBVPerShare",
    "TR.DividendYield", "TR.TotalReturn3Mo",
    "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", "TR.ROAPercentTrailing12M",
    "TR.PriceTargetMean", "TR.EpsPreSurprisePct", "TR.EPSMean(Period=FY1)",
    "TR.EPSMean(Period=FY2)", "TR.RevenueMean(Period=FY1)",
    "TR.RevenueMean(Period=FY2)", "TR.LTGMean", "TR.EpsSmartEst(Period=FY1)",
    "TR.PretaxMarginPercent(Period=FY0)", "TR.OperatingProfitMarginPct5YrAvg",
    "TR.FCFMean(Period=FY1)", "TR.F.DebtTot", "TR.F.CashCashEquiv",
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
    "TR.CommonName", "TR.TickerSymbol", "TR.HQCountryCode",
    "TR.TRBCEconomicSector", "TR.TRBCEconSectorCode",
    "TR.TRBCBusinessSector", "TR.TRBCBusinessSectorCode",
    "TR.TRBCIndustryGroup", "TR.TRBCIndustryGroupCode",
    "TR.TRBCIndustry", "TR.TRBCIndustryCode",
    "TR.CompanyMarketCap", "TR.PriceClose",
)


class LSEGResearchError(RuntimeError):
    pass


class LSEGNoMatches(LSEGResearchError):
    """The validated screen ran successfully but no rows met every constraint."""


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
        "unable to resolve all requested fields",
        "formula must contain at least one field or function",
    )
    if "412" in text and "identifier" in text:
        return False
    return any(phrase in text for phrase in phrases) or bool(
        re.search(r"(?:error\s+code|code)\s*[:=-]?\s*218\b", text)
    )


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
    call_records: list[dict[str, Any]] = field(default_factory=list)

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
        timeout_retries: int = 1,
    ) -> None:
        self.result = result
        self.minimum_interval = minimum_interval
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event
        self.timeout_retries = max(0, int(timeout_retries))
        self._last_call = 0.0

    def call(
        self,
        label: str,
        function: Callable[[], Any],
        *,
        warn: bool = True,
        capture_failure: bool = False,
        request_metadata: dict[str, Any] | None = None,
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
        call_number = len(self.result.call_records) + 1
        self.result.calls.append(label)
        _emit_progress(
            self.progress_callback,
            None,
            "Querying LSEG",
            f"API request {call_number}: {label}",
        )
        started = time.monotonic()
        record: dict[str, Any] = {
            "request_number": call_number,
            "label": label,
            "status": "started",
        }
        if request_metadata:
            record["request"] = _json_safe(request_metadata)
        self.result.call_records.append(record)
        attempts: list[dict[str, Any]] = []
        for attempt_index in range(self.timeout_retries + 1):
            attempt_started = time.monotonic()
            self._last_call = attempt_started
            try:
                value = function()
                _raise_if_cancelled(self.cancel_event)
                frame = _frame_from_response(value)
                attempt = {
                    "attempt": attempt_index + 1,
                    "status": "succeeded",
                    "duration_ms": round((time.monotonic() - attempt_started) * 1000, 1),
                }
                if attempt_index:
                    attempts.append(attempt)
                    record["attempts"] = attempts
                    record["retry_count"] = attempt_index
                record.update({
                    "status": "succeeded",
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                    "rows": len(frame) if not frame.empty else 0 if isinstance(value, pd.DataFrame) else None,
                })
                return value
            except ResearchCancelled:
                if attempts:
                    record["attempts"] = [
                        *attempts,
                        {
                            "attempt": attempt_index + 1,
                            "status": "cancelled",
                            "duration_ms": round((time.monotonic() - attempt_started) * 1000, 1),
                        },
                    ]
                    record["retry_count"] = attempt_index
                record.update({
                    "status": "cancelled",
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                })
                raise
            except Exception as exc:
                error_message = re.sub(r"\s+", " ", str(exc)).strip()[:1000]
                attempt_status = "timed_out" if _looks_like_timeout(exc) else "failed"
                attempt = {
                    "attempt": attempt_index + 1,
                    "status": attempt_status,
                    "duration_ms": round((time.monotonic() - attempt_started) * 1000, 1),
                    "error_type": type(exc).__name__,
                    "error_message": error_message,
                }
                if attempt_status == "timed_out" and attempt_index < self.timeout_retries:
                    attempts.append(attempt)
                    _emit_progress(
                        self.progress_callback,
                        None,
                        "Retrying slow LSEG request",
                        f"{label} timed out; retrying the read-only request before it is skipped.",
                    )
                    continue
                if attempts:
                    attempts.append(attempt)
                    record["attempts"] = attempts
                    record["retry_count"] = attempt_index
                record.update({
                    "status": attempt_status,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                    "error_type": type(exc).__name__,
                    "error_message": error_message,
                })
                if attempt_status == "timed_out":
                    self.result.warnings.append(
                        f"{label}: timed out after {self.timeout_retries} "
                        f"{'retry' if self.timeout_retries == 1 else 'retries'} and was skipped"
                    )
                    _emit_progress(
                        self.progress_callback,
                        None,
                        "Skipping slow LSEG request",
                        f"{label} exceeded the configured request timeout after "
                        f"{attempt_index + 1} attempts; the workflow will verify whether enough evidence remains.",
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

    # Display-title aliases observed in LSEG Data Library responses when an
    # older build cannot honor HeaderType.NAME. Unknown equal-width columns are
    # never mapped positionally: doing so can attribute one field's values to a
    # different requested field when LSEG changes or omits columns.
    header_aliases = {
        normalized("Company Common Name"): "TR.CommonName",
        normalized("Ticker Symbol"): "TR.TickerSymbol",
        normalized("Price Close"): "TR.PriceClose",
        normalized("Headquarters Country"): "TR.HeadquartersCountry",
        normalized("Country of Headquarters"): "TR.HeadquartersCountry",
        normalized("Country ISO Code"): "TR.HQCountryCode",
        normalized("Country ISO Code of Headquarters"): "TR.HQCountryCode",
        normalized("TRBC Economic Sector Name"): "TR.TRBCEconomicSector",
        normalized("TRBC Economic Sector Code"): "TR.TRBCEconSectorCode",
        normalized("TRBC Business Sector Name"): "TR.TRBCBusinessSector",
        normalized("TRBC Business Sector Code"): "TR.TRBCBusinessSectorCode",
        normalized("TRBC Industry Group Name"): "TR.TRBCIndustryGroup",
        normalized("TRBC Industry Group Code"): "TR.TRBCIndustryGroupCode",
        normalized("TRBC Industry Name"): "TR.TRBCIndustry",
        normalized("TRBC Industry Code"): "TR.TRBCIndustryCode",
    }

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
            continue
        aliased = header_aliases.get(key)
        if aliased in fields:
            rename[column] = aliased
            matched += 1

    result = result.rename(columns=rename)
    return result.loc[:, ~result.columns.duplicated()]


def _combine_columns(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in frames if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    output = frames[0].copy()
    for frame in frames[1:]:
        frame = frame.copy()
        if "Instrument" in output.columns and "Instrument" in frame.columns:
            if output["Instrument"].duplicated().any() or frame["Instrument"].duplicated().any():
                raise LSEGResearchError(
                    "LSEG returned row-expanding field batches without a shared record key; "
                    "combining them by row order would risk cross-record attribution."
                )
            join_columns = ["Instrument"]
            overlaps = [column for column in frame.columns if column in output.columns and column not in join_columns]
            right_names = {column: f"__right_{index}" for index, column in enumerate(overlaps)}
            frame = frame.rename(columns=right_names)
            output = output.merge(frame, on=join_columns, how="outer", validate="one_to_one")
            for column, right_column in right_names.items():
                if column in {
                    "TR.HQCountryCode", "TR.TRBCEconSectorCode", "TR.TRBCBusinessSectorCode",
                    "TR.TRBCIndustryGroupCode", "TR.TRBCIndustryCode", "TR.TickerSymbol",
                    "TR.OrganizationID",
                }:
                    left = output[column].map(_normalized_code)
                    right = output[right_column].map(_normalized_code)
                    conflicts = left.ne("") & right.ne("") & left.ne(right)
                    if conflicts.any():
                        instruments = output.loc[conflicts, "Instrument"].astype(str).head(5).tolist()
                        raise LSEGResearchError(
                            f"LSEG returned conflicting {column} values for: {', '.join(instruments)}."
                        )
                output[column] = output[column].combine_first(output[right_column])
                output = output.drop(columns=right_column)
        elif len(output) == len(frame) == 1:
            overlaps = [column for column in frame.columns if column in output.columns]
            for column in overlaps:
                output[column] = output[column].combine_first(frame[column])
            output = pd.concat(
                [output.reset_index(drop=True), frame.drop(columns=overlaps).reset_index(drop=True)], axis=1
            )
        else:
            raise LSEGResearchError(
                "LSEG returned multiple field batches without instrument identifiers; "
                "the rows cannot be combined safely."
            )
    return output.loc[:, ~output.columns.duplicated()]


def _combine_screen_core_and_enrichment(
    core: pd.DataFrame,
    enrichment: pd.DataFrame,
) -> pd.DataFrame:
    """Prefer explicitly USD-normalized enrichment over Screener display data.

    ``discovery.Screener`` can return ``TR.CompanyMarketCap`` in the listing's
    local currency even when the screen body contains ``CURN=USD``. The
    enrichment call supplies ``parameters={"Curn": "USD"}``, so it must be the
    authoritative source for overlapping value fields. Core identity fields
    still fill enrichment gaps, and _combine_columns continues to reject
    conflicting country, TRBC, ticker, or organization identities.
    """
    return _combine_columns([enrichment, core])


def _call_get_data(ld: Any, universe: Any, fields: tuple[str, ...], parameters: dict[str, Any] | None) -> Any:
    kwargs = {"universe": universe, "fields": list(fields), "parameters": parameters}
    header_type = getattr(getattr(ld, "HeaderType", None), "NAME", None)
    if header_type is not None:
        kwargs["header_type"] = header_type
    try:
        return ld.get_data(**kwargs)
    except TypeError as exc:
        # Preserve compatibility with older library builds and unit-test fakes.
        message = str(exc).casefold()
        if "header_type" not in kwargs or not (
            "header_type" in message and ("unexpected" in message or "keyword" in message)
        ):
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
    coverage = client.result.metrics.setdefault("data_request_coverage", {})

    for chunk_index, universe_chunk in enumerate(chunks, start=1):
        _raise_if_cancelled(client.cancel_event)
        batch_frames: list[pd.DataFrame] = []
        chunk_timed_out = False

        def fetch(batch: tuple[str, ...]) -> list[pd.DataFrame]:
            nonlocal chunk_timed_out
            _raise_if_cancelled(client.cancel_event)
            if isinstance(universe_chunk, (list, tuple, set)):
                universe_metadata: Any = [str(item) for item in universe_chunk]
            elif isinstance(universe_chunk, str):
                universe_metadata = universe_chunk
            else:
                universe_metadata = type(universe_chunk).__name__
            response = client.call(
                f"{label} chunk {chunk_index}/{len(chunks)} ({len(batch)} fields)",
                lambda: _call_get_data(ld, universe_chunk, batch, parameters),
                warn=False,
                capture_failure=True,
                request_metadata={
                    "operation": "get_data",
                    "universe": universe_metadata,
                    "universe_count": len(universe_chunk) if isinstance(universe_chunk, (list, tuple, set)) else 1,
                    "fields": list(batch),
                    "parameters": parameters or {},
                },
            )
            if response is _CALL_TIMED_OUT:
                chunk_timed_out = True
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
            requested_rics: set[str] = set()
            if isinstance(universe_chunk, (list, tuple, set)):
                requested_rics = {str(item).strip() for item in universe_chunk if str(item).strip()}
            elif isinstance(universe_chunk, str):
                candidate_ric = universe_chunk.strip()
                if candidate_ric and not candidate_ric.upper().startswith(("SCREEN(", "0#")):
                    requested_rics = {candidate_ric}
            if len(requested_rics) == 1 and "Instrument" not in frame.columns:
                frame.insert(0, "Instrument", next(iter(requested_rics)))
            if requested_rics:
                if "Instrument" not in frame.columns:
                    client.result.warnings.append(
                        f"{label}: explicit-instrument response had no Instrument column and was discarded."
                    )
                    failures.extend(batch)
                    return []
                returned_rics = {str(item).strip() for item in frame["Instrument"].dropna() if str(item).strip()}
                unexpected_rics = sorted(returned_rics - requested_rics)
                if unexpected_rics:
                    client.result.warnings.append(
                        f"{label}: discarded unexpected instruments returned by LSEG: "
                        + ", ".join(unexpected_rics[:20])
                    )
                    frame = frame[frame["Instrument"].astype(str).isin(requested_rics)].copy()
                    returned_rics = {
                        str(item).strip() for item in frame["Instrument"].dropna() if str(item).strip()
                    }
                coverage_record = {
                    "fields": list(batch),
                    "requested_rics": len(requested_rics),
                    "returned_rics": len(returned_rics),
                    "missing_rics": sorted(requested_rics - returned_rics)[:100],
                    "unexpected_rics": unexpected_rics[:100],
                }
                coverage.setdefault(f"{label}:chunk_{chunk_index}", []).append(coverage_record)
                if coverage_record["missing_rics"]:
                    client.result.warnings.append(
                        f"{label}: no row returned for requested instruments: "
                        + ", ".join(coverage_record["missing_rics"][:20])
                    )
                if frame.empty:
                    failures.extend(batch)
                    return []
            mapped_fields = [field for field in batch if field in frame.columns]
            missing_fields = [field for field in batch if field not in frame.columns]
            if missing_fields:
                client.result.warnings.append(
                    f"{label}: LSEG omitted returned columns for: {', '.join(missing_fields)}"
                )
                failures.extend(missing_fields)
            if not mapped_fields:
                return []
            return [frame]

        for batch in field_batches:
            if not batch or chunk_timed_out:
                break
            batch_frames.extend(fetch(batch))

        combined_chunk = _combine_columns(batch_frames)
        if not combined_chunk.empty:
            row_frames.append(combined_chunk)

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
    if ric:
        if "Instrument" not in subset.columns:
            if len(result.resolved) != 1 or result.resolved[0].ric != ric:
                return None
        else:
            selected = subset[subset["Instrument"].astype(str) == ric]
            if selected.empty:
                return None
            subset = selected
    values = subset[field_name].dropna()
    return None if values.empty else values.iloc[0]


def _numeric(value: Any) -> float | None:
    if _missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _column(frame: pd.DataFrame, field_name: str) -> pd.Series:
    if frame.empty or field_name not in frame.columns:
        return pd.Series(dtype="float64")
    return (
        pd.to_numeric(frame[field_name], errors="coerce")
        .astype("float64")
        .replace([math.inf, -math.inf], float("nan"))
    )


def _screen_number(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise LSEGResearchError("Screen thresholds must be finite numbers.")
    return str(int(number)) if number.is_integer() else format(number, ".15g")


def _screen_top_count(filters: ScreenFilters) -> int:
    return 200 if filters.candidate_search else max(50, min(filters.limit * 10, 500))


def build_screen_body(filters: ScreenFilters) -> str:
    """Build the body accepted by ``lseg.data.discovery.Screener``.

    Sector wording is canonicalized and then converted to the documented TRBC
    economic-sector code. Unknown wording is rejected locally instead of being
    sent to LSEG as an invalid literal condition.
    """
    clauses = ["U(IN(Equity(active,public,primary)))/*UNV:Public*/"]
    if filters.country_code:
        country = str(filters.country_code).strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise LSEGResearchError("Headquarters country must be a two-letter code.")
        if country not in {"US", "GB", "CA", "DE", "FR", "JP", "CN", "IN"}:
            raise LSEGResearchError(f"Unsupported headquarters-country code: {country}.")
        clauses.append(f'IN(TR.HQCountryCode,"{country}")')
    if filters.sector:
        canonical_sector = canonicalize_sector(filters.sector)
        if canonical_sector is None:
            raise LSEGResearchError(f"Unsupported sector wording: {filters.sector!r}.")
        sector_code = TRBC_SECTOR_CODES[canonical_sector.casefold()]
        clauses.append(f'IN(TR.TRBCEconSectorCode,"{sector_code}")')
    if filters.industry:
        definition = classification_definition(filters.industry)
        if definition is None:
            raise LSEGResearchError(f"Unsupported industry wording: {filters.industry!r}.")
        codes = ",".join(f'"{code}"' for code in definition.codes)
        clauses.append(f"IN({definition.code_field},{codes})")
    if filters.market_cap_min is not None:
        clauses.append(f"TR.CompanyMarketCap>={_screen_number(filters.market_cap_min)}")
    if filters.market_cap_max is not None:
        clauses.append(f"TR.CompanyMarketCap<={_screen_number(filters.market_cap_max)}")
    if filters.pe_max is not None:
        clauses.append("TR.PE>0")
        clauses.append(f"TR.PE<={_screen_number(filters.pe_max)}")
    if filters.forward_pe_max is not None:
        clauses.append("TR.PtoEPSMeanEst(Period=FY1)>0")
        clauses.append(f"TR.PtoEPSMeanEst(Period=FY1)<={_screen_number(filters.forward_pe_max)}")
    if filters.ev_ebitda_max is not None:
        clauses.append("TR.EVToEBITDA>0")
        clauses.append(f"TR.EVToEBITDA<={_screen_number(filters.ev_ebitda_max)}")
    if filters.dividend_yield_min is not None:
        clauses.append(f"TR.DividendYield>={_screen_number(filters.dividend_yield_min)}")
    if filters.total_return_3m_min is not None:
        clauses.append(f"TR.TotalReturn3Mo>={_screen_number(filters.total_return_3m_min)}")
    top_count = _screen_top_count(filters)
    clauses.append(f"TOP(TR.CompanyMarketCap,{top_count},nnumber)")
    clauses.append("CURN=USD")
    return ", ".join(clauses)


def build_screen_expression(filters: ScreenFilters) -> str:
    """Return the complete ``SCREEN(...)`` expression used as a fallback."""
    return "SCREEN(" + build_screen_body(filters) + ")"


def _rank_candidate_screen(
    frame: pd.DataFrame,
    research_weights: dict[str, float] | None = None,
) -> pd.DataFrame:
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
        return (
            pd.to_numeric(output[field], errors="coerce")
            .astype("float64")
            .replace([math.inf, -math.inf], float("nan"))
        )

    price = numeric("TR.PriceClose")
    target = numeric("TR.PriceTargetMean")
    output["Target Upside"] = target.div(price).sub(1).where(price > 0)

    eps_mean = numeric("TR.EPSMean(Period=FY1)")
    eps_fy2 = numeric("TR.EPSMean(Period=FY2)")
    smart = numeric("TR.EpsSmartEst(Period=FY1)")
    output["Smart Gap"] = smart.sub(eps_mean).div(eps_mean.abs()).where(eps_mean.abs() > 0)
    output["Forward EPS Growth"] = eps_fy2.div(eps_mean).sub(1).where(
        (eps_mean > 0) & (eps_fy2 > 0)
    )
    revenue_fy1 = numeric("TR.RevenueMean(Period=FY1)")
    revenue_fy2 = numeric("TR.RevenueMean(Period=FY2)")
    output["Forward Revenue Growth"] = revenue_fy2.div(revenue_fy1).sub(1).where(
        (revenue_fy1 > 0) & (revenue_fy2 > 0)
    )

    debt = numeric("TR.F.DebtTot")
    fcf = numeric("TR.FCFMean(Period=FY1)")
    cash = numeric("TR.F.CashCashEquiv")

    def resilience_to_debt(numerator: pd.Series) -> pd.Series:
        ratio = numerator.div(debt.abs()).where(debt.abs() > 0)
        finite = ratio.replace([math.inf, -math.inf], float("nan")).dropna()
        debt_free_score = max(float(finite.max()), 0.0) + 1.0 if not finite.empty else 1.0
        return ratio.mask((debt.abs() == 0) & numerator.notna() & (numerator >= 0), debt_free_score)

    output["FCF to Debt"] = resilience_to_debt(fcf)
    output["Cash to Debt"] = resilience_to_debt(cash)
    sector_codes = output.get("TR.TRBCEconSectorCode", pd.Series("", index=output.index)).map(_normalized_code)
    # Corporate leverage and EV/EBITDA are not comparable primary factors for
    # banks and insurers. Keep those rows eligible through price/book, ROE and
    # other applicable factors instead of rewarding an economically invalid ratio.
    financial_rows = sector_codes.eq("55")
    output.loc[financial_rows, "FCF to Debt"] = pd.NA
    output.loc[financial_rows, "Cash to Debt"] = pd.NA
    output["Applicable EV/EBITDA"] = numeric("TR.EVToEBITDA").mask(financial_rows)

    positive_signal_sets: dict[Any, set[str]] = {index: set() for index in output.index}
    positive_signals = {
        "quality": (numeric("TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM") > 0)
        | (numeric("TR.ROAPercentTrailing12M") > 0),
        "cash_flow": output["FCF to Debt"] > 0,
        "income": numeric("TR.DividendYield") > 0,
        "momentum": numeric("TR.TotalReturn3Mo") > 0,
        "expectations": (output["Target Upside"] > 0)
        | (numeric("TR.EpsPreSurprisePct") > 0)
        | (output["Smart Gap"] > 0),
    }
    for family, mask in positive_signals.items():
        for index in output.index[mask.fillna(False)]:
            positive_signal_sets[index].add(family)
    output["Positive Signal Family Count"] = pd.Series(
        {index: len(families) for index, families in positive_signal_sets.items()}, dtype="int64"
    )
    output["Positive Signal Families"] = pd.Series(
        {index: ", ".join(sorted(families)) for index, families in positive_signal_sets.items()}
    )

    try:
        weights = ResearchWeights.from_mapping(
            research_weights
            or macro_default_policy("Regime incomplete").weights.as_dict()
        ).as_dict()
    except (TypeError, ValueError) as exc:
        raise LSEGResearchError(f"Invalid research ranking weights: {exc}") from exc
    components: list[tuple[str, pd.Series, str]] = []

    def add(
        family: str,
        field: str,
        *,
        higher_is_better: bool = True,
        positive_only: bool = False,
    ) -> None:
        values = numeric(field)
        if positive_only:
            values = values.where(values > 0)
        valid = values.notna()
        if valid.sum() < 2:
            return
        percentile = values.rank(pct=True, method="average")
        if not higher_is_better:
            percentile = 1.0 - percentile + (1.0 / valid.sum())
        components.append((family, percentile.where(valid), field))

    add("growth", "Forward Revenue Growth")
    add("growth", "Forward EPS Growth")
    add("growth", "TR.LTGMean")
    add("profitability", "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM")
    add("profitability", "TR.ROAPercentTrailing12M")
    add("profitability", "TR.PretaxMarginPercent(Period=FY0)")
    add("profitability", "TR.OperatingProfitMarginPct5YrAvg")
    add(
        "valuation",
        "TR.PtoEPSMeanEst(Period=FY1)",
        higher_is_better=False,
        positive_only=True,
    )
    add(
        "valuation", "Applicable EV/EBITDA", higher_is_better=False, positive_only=True
    )
    add(
        "valuation", "TR.PriceToSalesPerShare", higher_is_better=False, positive_only=True
    )
    add(
        "valuation", "TR.PriceToBVPerShare", higher_is_better=False, positive_only=True
    )
    add("balance_sheet", "FCF to Debt")
    add("balance_sheet", "Cash to Debt")

    if not components:
        output["Research Score"] = pd.NA
        output["Evidence Count"] = 0
        output["Evidence Families"] = ""
        return output.sort_values("TR.CompanyMarketCap", ascending=False, na_position="last")

    value_fields = (
        "TR.PtoEPSMeanEst(Period=FY1)", "TR.EVToEBITDA",
        "TR.PriceToSalesPerShare", "TR.PriceToBVPerShare",
    )
    value_evidence = pd.Series(0, index=output.index, dtype="int64")
    for field_name in value_fields:
        values = numeric(field_name)
        if field_name == "TR.EVToEBITDA":
            values = values.mask(financial_rows)
        value_evidence = value_evidence.add((values > 0).astype(int), fill_value=0).astype(int)
    output["Value Evidence Count"] = value_evidence

    weighted_sum = pd.Series(0.0, index=output.index, dtype="float64")
    available_weight = pd.Series(0.0, index=output.index, dtype="float64")
    family_sets: dict[Any, set[str]] = {index: set() for index in output.index}
    evidence_count = pd.Series(0, index=output.index, dtype="int64")
    for family, family_weight in weights.items():
        family_components = [percentile for name, percentile, _field in components if name == family]
        if not family_components:
            output[f"{family.replace('_', ' ').title()} Score"] = pd.NA
            continue
        component_frame = pd.concat(family_components, axis=1)
        family_score = component_frame.mean(axis=1, skipna=True)
        valid = family_score.notna()
        output[f"{family.replace('_', ' ').title()} Score"] = family_score.mul(100)
        weighted_sum = weighted_sum.add(family_score.fillna(0) * family_weight, fill_value=0)
        available_weight = available_weight.add(valid.astype(float) * family_weight, fill_value=0)
        component_count = component_frame.notna().sum(axis=1).astype(int)
        evidence_count = evidence_count.add(component_count, fill_value=0).astype(int)
        for index in output.index[valid]:
            family_sets[index].add(family)

    raw_score = weighted_sum.div(available_weight.where(available_weight > 0)).mul(100)
    family_count = pd.Series({index: len(value) for index, value in family_sets.items()}, dtype="int64")
    coverage_factor = family_count.div(4).clip(lower=0.45, upper=1.0)
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


def _normalized_code(value: Any) -> str:
    if _missing(value):
        return ""
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def apply_screen_filters(
    frame: pd.DataFrame,
    filters: ScreenFilters,
    *,
    truncate: bool = True,
    strict: bool = True,
    research_weights: dict[str, float] | None = None,
) -> pd.DataFrame:
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
        if threshold is None:
            continue
        if field_name not in output.columns:
            if strict:
                raise LSEGResearchError(
                    f"The requested screen constraint could not be validated because LSEG did not return {field_name}."
                )
            continue
        values = pd.to_numeric(output[field_name], errors="coerce")
        if direction == "min":
            output = output[values >= threshold]
        elif field_name in {"TR.PE", "TR.PtoEPSMeanEst(Period=FY1)", "TR.EVToEBITDA"}:
            output = output[(values > 0) & (values <= threshold)]
        else:
            output = output[values <= threshold]
    if filters.country_code:
        if "TR.HQCountryCode" not in output.columns:
            if strict:
                raise LSEGResearchError(
                    "The headquarters-country constraint could not be validated because LSEG did not return TR.HQCountryCode."
                )
        else:
            output = output[
                output["TR.HQCountryCode"].map(_normalized_code).str.upper()
                == filters.country_code.upper()
            ]
    if filters.sector:
        canonical_sector = canonicalize_sector(filters.sector)
        sector_code = TRBC_SECTOR_CODES.get((canonical_sector or "").casefold())
        if "TR.TRBCEconSectorCode" in output.columns and sector_code:
            output = output[output["TR.TRBCEconSectorCode"].map(_normalized_code) == sector_code]
        elif "TR.TRBCEconomicSector" in output.columns:
            output = output[
                output["TR.TRBCEconomicSector"].astype(str).str.casefold()
                == (canonical_sector or filters.sector).casefold()
            ]
        elif strict:
            raise LSEGResearchError(
                "The TRBC sector constraint could not be validated because LSEG returned no sector code or name."
            )
    if filters.industry:
        definition = classification_definition(filters.industry)
        if definition is None:
            raise LSEGResearchError(f"Unsupported industry wording: {filters.industry!r}.")
        if definition.code_field not in output.columns:
            if strict:
                raise LSEGResearchError(
                    f"The TRBC industry constraint could not be validated because LSEG did not return {definition.code_field}."
                )
        else:
            allowed = set(definition.codes)
            output = output[output[definition.code_field].map(_normalized_code).isin(allowed)]
    if filters.candidate_search or filters.sort_by == "quality_value":
        ranked = _rank_candidate_screen(output, research_weights).reset_index(drop=True)
        return ranked.head(filters.limit) if truncate else ranked
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
    output = output.reset_index(drop=True)
    return output.head(filters.limit) if truncate else output


def _retrieve_screen(ld: Any, client: _LSEGClient, result: ResearchResult) -> None:
    filters = result.plan.screen
    workflow = get_workflow(result.plan.workflow, result.plan.mode, candidate_search=filters.candidate_search)
    requested_screen_top = (
        workflow.screen_limit if filters.universe else _screen_top_count(filters)
    )
    if filters.universe:
        from lseg.data.discovery import Chain

        chain_response = client.call(
            f"Chain {filters.universe}",
            lambda: list(Chain(filters.universe)),
            warn=False,
            capture_failure=True,
            request_metadata={"operation": "discovery.Chain", "chain": filters.universe},
        )
        if chain_response is _CALL_TIMED_OUT:
            raise LSEGResearchError(f"Constituent expansion timed out for {filters.universe}.")
        if isinstance(chain_response, _CallFailure):
            raise LSEGResearchError(
                f"Constituent expansion failed for {filters.universe}: {chain_response.error}"
            ) from chain_response.error
        universe: Any = list(chain_response or [])
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
        discovery_fields_list = ["TR.CommonName", "TR.CompanyMarketCap"]
        if filters.country_code:
            discovery_fields_list.append("TR.HQCountryCode")
        if filters.sector:
            discovery_fields_list.extend(("TR.TRBCEconomicSector", "TR.TRBCEconSectorCode"))
        if filters.industry:
            definition = classification_definition(filters.industry)
            if definition is None:
                raise LSEGResearchError(f"Unsupported industry wording: {filters.industry!r}.")
            discovery_fields_list.append(definition.code_field)
        discovery_fields = tuple(dict.fromkeys(discovery_fields_list))

        def fetch_once(universe_value: Any, label: str, fields: tuple[str, ...]) -> tuple[pd.DataFrame, BaseException | None]:
            universe_label = universe_value if isinstance(universe_value, str) else type(universe_value).__name__
            response = client.call(
                label,
                lambda: _call_get_data(ld, universe_value, fields, {"Curn": "USD"}),
                warn=False,
                capture_failure=True,
                request_metadata={
                    "operation": "get_data",
                    "universe": universe_label,
                    "fields": list(fields),
                    "parameters": {"Curn": "USD"},
                    "currency": "USD",
                },
            )
            if isinstance(response, _CallFailure):
                return pd.DataFrame(), response.error
            if response is _CALL_TIMED_OUT:
                return pd.DataFrame(), TimeoutError("screen request timed out")
            return _canonicalize(response, fields), None

        core = pd.DataFrame()
        object_error: BaseException | None = None
        try:
            from lseg.data.discovery import Screener

            screener = Screener(body)
            core, object_error = fetch_once(screener, "Stock screen via discovery.Screener", discovery_fields)
        except Exception as exc:
            object_error = exc

        if object_error is not None:
            message = f"{type(object_error).__name__}: {object_error}".casefold()
            compatibility_error = isinstance(object_error, (ImportError, AttributeError, TypeError)) and any(
                token in message for token in ("screener", "universe", "unexpected", "unsupported")
            )
            if not compatibility_error:
                raise LSEGResearchError(
                    f"The canonical discovery.Screener request failed: {type(object_error).__name__}: {object_error}"
                ) from object_error
            result.warnings.append(
                f"Stock screen object compatibility fallback: {type(object_error).__name__}: {object_error}"
            )
            core, fallback_error = fetch_once(
                expression, "Stock screen via SCREEN expression compatibility fallback", discovery_fields
            )
            if fallback_error is not None:
                raise LSEGResearchError(
                    f"Both supported screen representations failed; fallback error: "
                    f"{type(fallback_error).__name__}: {fallback_error}"
                ) from fallback_error
        if core.empty:
            raise LSEGNoMatches(
                "The validated LSEG screen completed successfully but returned no matching companies. "
                f"Criteria: {expression}"
            )

    if core.empty or "Instrument" not in core.columns:
        raise LSEGResearchError("The screen returned no usable instrument identifiers.")

    raw_screen_count = len(core)
    identity_filters = ScreenFilters(
        country_code=filters.country_code,
        sector=filters.sector,
        industry=filters.industry,
        limit=max(len(core), 3),
    )
    core = apply_screen_filters(core, identity_filters, truncate=False, strict=True)
    instrument_text = core["Instrument"].astype("string").str.strip()
    blank_instruments = instrument_text.isna() | instrument_text.eq("")
    if blank_instruments.any():
        result.warnings.append(
            f"Stock screen: discarded {int(blank_instruments.sum())} row(s) without a usable Instrument identifier."
        )
        core = core.loc[~blank_instruments].copy()
    duplicate_instruments = core["Instrument"].astype(str).duplicated(keep=False)
    if duplicate_instruments.any():
        duplicates = core.loc[duplicate_instruments, "Instrument"].astype(str).drop_duplicates().head(10)
        raise LSEGResearchError(
            "The screen returned duplicate rows for instrument identifiers: " + ", ".join(duplicates)
        )
    result.metrics["constraint_validation"] = {
        "requested_country": filters.country_code,
        "requested_sector": filters.sector,
        "requested_industry": filters.industry,
        "returned_rows": raw_screen_count,
        "validated_rows": len(core),
        "rejected_rows": raw_screen_count - len(core),
    }
    if core.empty:
        raise LSEGNoMatches(
            "LSEG returned rows for the compiled screen, but none passed local country/TRBC postcondition checks."
        )

    rics = list(dict.fromkeys(str(value).strip() for value in core["Instrument"].dropna().tolist() if str(value).strip()))
    if not rics:
        raise LSEGResearchError("The screen returned no usable RICs.")
    rics = rics[:workflow.screen_limit]
    result.metrics["screen_universe_count"] = len(rics)
    effective_screen_cap = min(requested_screen_top, workflow.screen_limit)
    result.metrics["screen_universe_cap"] = effective_screen_cap
    result.metrics["screen_requested_top"] = requested_screen_top
    result.metrics["screen_universe_scope"] = (
        f"Top {effective_screen_cap} active public primary equities by USD market capitalization "
        "after the compiled country/TRBC constraints."
    )

    enrichment = _safe_get_data(
        ld,
        client,
        rics,
        SCREEN_FIELDS,
        parameters={"Curn": "USD"},
        label="Stock-screen enrichment",
    )
    frame = _combine_screen_core_and_enrichment(core, enrichment)
    full_ranked = apply_screen_filters(
        frame,
        filters,
        truncate=False,
        strict=True,
        research_weights=result.plan.research_weights or None,
    )
    if full_ranked.empty:
        raise LSEGNoMatches(
            "The LSEG screen ran successfully, but no companies met every validated numeric and classification constraint."
        )
    result.tables["screen_universe"] = full_ranked.reset_index(drop=True)
    result.metrics["screen_matching_count"] = len(full_ranked)
    cohort_statistics: dict[str, dict[str, Any]] = {}
    for field_name, unit, positive_only in (
        ("TR.PtoEPSMeanEst(Period=FY1)", "multiple", True),
        ("TR.EVToEBITDA", "multiple", True),
        ("TR.PriceToSalesPerShare", "multiple", True),
        ("TR.PriceToBVPerShare", "multiple", True),
        ("TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", "percentage_points", False),
        ("TR.DividendYield", "percentage_points", False),
    ):
        values = _column(full_ranked, field_name).replace([math.inf, -math.inf], pd.NA).dropna()
        if positive_only:
            values = values[values > 0]
        if not values.empty:
            cohort_statistics[field_name] = {
                "median": float(values.median()),
                "valid_n": len(values),
                "unit": unit,
                "currency": "USD" if field_name.startswith("TR.CompanyMarketCap") else None,
                "sector": filters.sector,
                "industry": filters.industry,
            }
    result.metrics["cohort_statistics"] = cohort_statistics

    value_discount_count = pd.Series(0, index=full_ranked.index, dtype="int64")
    value_discount_fields: dict[Any, list[str]] = {index: [] for index in full_ranked.index}
    financial_rows = full_ranked.get(
        "TR.TRBCEconSectorCode", pd.Series("", index=full_ranked.index)
    ).map(_normalized_code).eq("55")
    for field_name in (
        "TR.PtoEPSMeanEst(Period=FY1)", "TR.EVToEBITDA",
        "TR.PriceToSalesPerShare", "TR.PriceToBVPerShare",
    ):
        values = _column(full_ranked, field_name)
        for index, value in values.items():
            if not math.isfinite(value) or value <= 0:
                continue
            if field_name == "TR.EVToEBITDA" and bool(financial_rows.loc[index]):
                continue
            peers = values.drop(index=index).replace([math.inf, -math.inf], pd.NA).dropna()
            peers = peers[peers > 0]
            if len(peers) < 5:
                continue
            median = float(peers.median())
            if value < median * 0.90:
                value_discount_count.loc[index] += 1
                value_discount_fields[index].append(field_name)
    full_ranked["Value Discount Count"] = value_discount_count
    full_ranked["Value Discount Fields"] = pd.Series(
        {index: ", ".join(fields) for index, fields in value_discount_fields.items()}
    )
    result.tables["screen_universe"] = full_ranked.reset_index(drop=True)

    eligible = full_ranked
    if filters.candidate_search:
        family_column = "Evidence Family Count"
        if family_column not in eligible.columns:
            required_failures = [
                record for record in result.call_records
                if str(record.get("label", "")).startswith("Stock-screen enrichment")
                and record.get("status") in {"failed", "timed_out", "cancelled"}
            ]
            if required_failures:
                raise LSEGResearchError(
                    "The screen matched companies, but required ranking enrichment did not complete; "
                    "the run was not classified as a true no-match."
                )
            raise LSEGNoMatches("No candidate had enough comparable value, quality, expectations, momentum, and risk data.")
        family_count = pd.to_numeric(eligible[family_column], errors="coerce").fillna(0)
        eligible = eligible[family_count >= workflow.minimum_screen_factor_families]
        if eligible.empty:
            raise LSEGNoMatches(
                f"The screen matched companies, but none had the required {workflow.minimum_screen_factor_families} "
                "independent factor families for candidate selection."
            )
        if "positive_signals" in result.plan.selection_objectives:
            if "Positive Signal Family Count" not in eligible.columns:
                eligible = eligible.iloc[0:0]
            else:
                positive_count = pd.to_numeric(
                    eligible["Positive Signal Family Count"], errors="coerce"
                ).fillna(0)
                eligible = eligible[positive_count >= 2]
            result.metrics["screen_positive_eligible_count"] = len(eligible)
            if eligible.empty:
                raise LSEGNoMatches(
                    "The screen matched companies, but none had positive signals in at least two independent "
                    "quality, cash-flow, income, momentum, or expectations families."
                )
        if "relative_value" in result.plan.selection_objectives:
            if "Value Discount Count" not in eligible.columns:
                eligible = eligible.iloc[0:0]
            else:
                value_count = pd.to_numeric(eligible["Value Discount Count"], errors="coerce").fillna(0)
                eligible = eligible[value_count >= 1]
            result.metrics["screen_value_eligible_count"] = len(eligible)
            if eligible.empty:
                raise LSEGNoMatches(
                    "The screen matched companies, but none traded at least 10% below a validated cohort median on a usable positive valuation multiple."
                )
        result.metrics["screen_evidence_eligible_count"] = len(eligible)

    ranked = eligible.head(filters.limit).reset_index(drop=True)
    result.tables["screen"] = ranked
    result.metrics["screen_ranked_count"] = len(eligible)
    result.metrics["screen_display_count"] = len(ranked)
    if "Evidence Family Count" in eligible.columns:
        result.metrics["screen_median_evidence_families"] = float(
            pd.to_numeric(eligible["Evidence Family Count"], errors="coerce").median()
        )


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
    requested_optional = set(result.plan.topics) & {
        "guidance", "events", "ownership", "insiders",
    }
    if not requested_optional:
        return
    winner = result.resolved[0]
    ric = winner.ric
    _emit_progress(
        progress_callback,
        90,
        "Enriching the leading candidate",
        f"{winner.company_name} ({ric}): checking recent guidance, events, ownership, and insider activity.",
    )

    if "guidance" in requested_optional:
        result.warnings.append(
            "Winner guidance: generic row-expanding guidance fields were not queried because they do not expose "
            "a stable shared record key and can create a Cartesian response."
        )

    for topic in ("events",):
        if topic not in requested_optional:
            continue
        try:
            frame = _safe_get_data(
                ld,
                client,
                [ric],
                TOPIC_FIELDS[topic],
                parameters={"SDate": 0, "EDate": 90},
                label=f"Winner {topic}",
                universe_chunk_size=1,
                field_batch_size=None,
                isolate_invalid_fields=True,
            )
        except LSEGResearchError as exc:
            result.warnings.append(f"Winner {topic}: optional row-expanding data was skipped: {exc}")
            continue
        if not frame.empty:
            result.tables[topic] = _limit_per_instrument(frame, 12)

    # LSEG's documented fund-ownership example uses a narrow daily snapshot
    # window. Asking for an unconstrained current table can expand to thousands
    # of rows and block the workflow.
    ownership = pd.DataFrame()
    if "ownership" in requested_optional:
        try:
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
        except LSEGResearchError as exc:
            result.warnings.append(f"Winner ownership: optional data was skipped: {exc}")
            ownership = pd.DataFrame()
    if not ownership.empty:
        result.tables["ownership"] = _limit_per_instrument(ownership, 15)

    insiders = pd.DataFrame()
    if "insiders" in requested_optional:
        try:
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
        except LSEGResearchError as exc:
            result.warnings.append(f"Winner insiders: optional data was skipped: {exc}")
            insiders = pd.DataFrame()
    if not insiders.empty:
        result.tables["insiders"] = _limit_per_instrument(insiders, 15)


def _retrieve_upcoming_events(
    ld: Any,
    client: _LSEGClient,
    result: ResearchResult,
    resolved: ResolvedInstrument,
) -> None:
    """Retrieve a bounded future event window for every portfolio-review holding."""
    try:
        frame = _safe_get_data(
            ld,
            client,
            [resolved.ric],
            TOPIC_FIELDS["events"],
            parameters={"SDate": 0, "EDate": 90},
            label=f"Upcoming events {resolved.ric}",
            universe_chunk_size=1,
            field_batch_size=None,
            isolate_invalid_fields=True,
        )
    except LSEGResearchError as exc:
        result.warnings.append(f"Upcoming events {resolved.ric}: data was skipped: {exc}")
        return
    if not frame.empty:
        existing = result.tables.get("events", pd.DataFrame())
        result.tables["events"] = pd.concat([existing, frame], ignore_index=True)


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
        ric_value = row.get("Instrument")
        ric = "" if _missing(ric_value) else str(ric_value).strip()
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
        "Core finalist data is complete. Retrieving price, revisions, Reuters news, peers, and filings.",
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
        _retrieve_news_stories(ld, client, result, item, workflow.news_stories_per_candidate)
        _retrieve_peers(ld, client, result, item)
        _retrieve_filings(client, result, item)
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
            "adjustments": ["exchangeCorrection", "manualCorrection", "CCH", "CRE", "RTS", "RPO"],
        }
        header_type = getattr(getattr(ld, "HeaderType", None), "NAME", None)
        if header_type is not None:
            kwargs["header_type"] = header_type
        try:
            return ld.get_history(**kwargs)
        except TypeError as exc:
            message = str(exc).casefold()
            if not ("header_type" in message and ("unexpected" in message or "keyword" in message)):
                raise
            kwargs.pop("header_type", None)
            return ld.get_history(**kwargs)

    response = client.call(
        f"Price history {resolved.ric}",
        call_history,
        request_metadata={
            "operation": "get_history",
            "universe": resolved.ric,
            "fields": ["TRDPRC_1"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "interval": "daily",
            "adjustments": ["exchangeCorrection", "manualCorrection", "CCH", "CRE", "RTS", "RPO"],
        },
    )
    frame = _frame_from_response(response)
    if frame.empty:
        return
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [" ".join(str(item) for item in column if item not in {None, ""}).strip() for column in frame.columns]
    date_source: Any = frame.index
    if isinstance(frame.index, pd.RangeIndex) or pd.api.types.is_numeric_dtype(frame.index.dtype):
        date_column = _date_column(frame)
        if date_column is None:
            result.tables[f"price:{resolved.ric}"] = frame
            result.warnings.append(
                f"Price history {resolved.ric}: no trustworthy date index was returned, so return metrics were omitted."
            )
            return
        date_source = frame[date_column]
    parsed_values = pd.to_datetime(date_source, errors="coerce", utc=True)
    parsed_dates = (
        parsed_values.reindex(frame.index)
        if isinstance(parsed_values, pd.Series)
        else pd.Series(parsed_values, index=frame.index)
    )
    plausible = parsed_dates.notna() & parsed_dates.dt.year.between(1990, end.year + 2)
    if not plausible.any():
        result.tables[f"price:{resolved.ric}"] = frame
        result.warnings.append(
            f"Price history {resolved.ric}: returned dates were invalid, so return metrics were omitted."
        )
        return
    frame = frame.assign(__history_date=parsed_dates).loc[plausible]
    frame = frame.sort_values("__history_date").drop_duplicates("__history_date", keep="last")
    frame = frame.set_index("__history_date")
    result.tables[f"price:{resolved.ric}"] = frame
    converted = frame.apply(pd.to_numeric, errors="coerce")
    numeric_columns = [column for column in converted.columns if converted[column].notna().any()]
    if not numeric_columns:
        return
    prices = converted[numeric_columns[0]].replace([math.inf, -math.inf], pd.NA).dropna()
    prices = prices[prices > 0]
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
    if len(prices) >= 3 and len(returns) >= 2:
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
        isolate_invalid_fields=False,
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
    value_field = "TR.EPSMean(Period=FY1)"
    period_field = "TR.EPSMean(Period=FY1).periodenddate"
    if value_field not in frame.columns or period_field not in frame.columns:
        result.warnings.append(
            f"Estimate history {resolved.ric}: FY1 period identity was unavailable, so revision metrics were omitted."
        )
        return
    observations = pd.DataFrame(
        {
            "date": dates,
            "value": pd.to_numeric(frame[value_field], errors="coerce"),
            "period": pd.to_datetime(frame[period_field], errors="coerce", utc=True),
        }
    ).dropna().sort_values("date")
    if observations.empty:
        return
    latest = observations.iloc[-1]
    now = pd.Timestamp.now(tz="UTC")
    if now - latest["date"] > pd.Timedelta(14, unit="D"):
        result.warnings.append(
            f"Estimate history {resolved.ric}: latest consensus observation was stale, so revision metrics were omitted."
        )
        return
    same_period = observations[observations["period"] == latest["period"]]
    for days in (30, 90):
        target = latest["date"] - pd.Timedelta(int(days), unit="D")
        candidates = same_period[
            (same_period["date"] <= target)
            & (same_period["date"] >= target - pd.Timedelta(14, unit="D"))
        ]
        if candidates.empty:
            continue
        prior = candidates.iloc[-1]
        if prior["value"] != 0:
            result.metrics[f"{resolved.ric}:eps_revision_{days}d"] = (
                latest["value"] - prior["value"]
            ) / abs(prior["value"])
            result.metrics[f"{resolved.ric}:eps_revision_{days}d_observation"] = {
                "latest_date": latest["date"].isoformat(),
                "prior_date": prior["date"].isoformat(),
                "period_end": latest["period"].date().isoformat(),
            }


def _retrieve_news(ld: Any, client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=min(result.plan.lookback_days, 450))
    news_rics = [resolved.ric]
    if resolved.ric.endswith(".OQ"):
        news_rics.append(resolved.ric[:-1])
    for news_ric in news_rics:
        response = client.call(
            f"News headlines {resolved.ric} via {news_ric}",
            lambda ric=news_ric: ld.news.get_headlines(
                query=f"R:{ric} AND Language:LEN",
                count=50,
                start=start.isoformat(),
                end=end.isoformat(),
            ),
            request_metadata={
                "operation": "news.get_headlines",
                "ric": news_ric,
                "language": "LEN",
                "count": 50,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        frame = _frame_from_response(response)
        if not frame.empty:
            result.tables[f"news:{resolved.ric}"] = frame.head(50).reset_index(drop=True)
            result.metrics[f"{resolved.ric}:news_ric"] = news_ric
            return


def _story_id_column(frame: pd.DataFrame) -> Any | None:
    for column in frame.columns:
        if re.sub(r"[^a-z0-9]", "", str(column).casefold()) in {"storyid", "storyidentifier"}:
            return column
    return None


def _strip_html(value: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


_COMPANY_SUFFIXES = {
    "co", "company", "corp", "corporation", "inc", "incorporated", "limited",
    "ltd", "plc",
}


def _news_entity_terms(
    resolved: ResolvedInstrument,
    *,
    include_ticker: bool = True,
) -> tuple[str, ...]:
    """Build conservative company references for validating tagged news."""
    terms: list[str] = []
    ticker = re.sub(r"[^a-z0-9]", "", resolved.ticker.casefold())
    for candidate in (resolved.company_name, resolved.query, resolved.original):
        cleaned = re.sub(r"[^a-z0-9]+", " ", str(candidate or "").casefold()).strip()
        if not include_ticker and re.sub(r"[^a-z0-9]", "", cleaned) == ticker:
            continue
        if len(cleaned) >= 4 and cleaned not in terms:
            terms.append(cleaned)
        words = cleaned.split()
        while words and words[-1] in _COMPANY_SUFFIXES:
            words.pop()
        shortened = " ".join(words)
        if len(shortened) >= 4 and shortened not in terms:
            terms.append(shortened)
    if include_ticker:
        if len(ticker) >= 3 and ticker not in terms:
            terms.append(ticker)
    return tuple(terms)


def _contains_news_entity(text: str, terms: Iterable[str]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalized)
        for term in terms
    )


def _company_story_excerpt(text: str, terms: Iterable[str], limit: int = 600) -> str | None:
    """Return only sentences that explicitly discuss the selected company."""
    cleaned = _strip_html(text)
    sentences = re.split(r"(?<=[.!?])\s+|\s*[\r\n]+\s*", cleaned)
    matches: list[str] = []
    for sentence in sentences:
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if len(sentence.split()) < 4 or not _contains_news_entity(sentence, terms):
            continue
        if len(sentence) > limit:
            positions = [
                match.start()
                for term in terms
                if (match := re.search(re.escape(term), sentence, flags=re.IGNORECASE))
            ]
            if positions:
                start = max(0, min(positions) - limit // 3)
                sentence = sentence[start : start + limit].strip()
        matches.append(sentence)
        if len(matches) >= 2:
            break
    if not matches:
        return None
    excerpt = " ".join(matches)
    return excerpt if len(excerpt) <= limit else excerpt[: limit - 1].rstrip() + "…"


def _retrieve_news_stories(ld: Any, client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument, limit: int) -> None:
    headlines = result.tables.get(f"news:{resolved.ric}", pd.DataFrame())
    if headlines.empty:
        return
    story_col = _story_id_column(headlines)
    headline_col = _headline_column(headlines)
    headline_terms = _news_entity_terms(resolved)
    story_terms = _news_entity_terms(resolved, include_ticker=False)
    if not headline_terms:
        result.tables[f"news:{resolved.ric}"] = headlines.iloc[0:0].copy()
        return
    target_count = max(1, int(limit))
    fetch_budget = min(12, max(8, target_count * 4))
    records: list[dict[str, Any]] = []
    accepted_headlines: list[dict[str, Any]] = []
    seen: set[str] = set()
    fetched = 0
    for _, row in headlines.iterrows():
        headline = (
            "" if headline_col is None or _missing(row.get(headline_col))
            else str(row.get(headline_col)).strip()
        )
        direct_headline = _contains_news_entity(headline, headline_terms)
        story_value = row.get(story_col) if story_col is not None else None
        story_id = "" if _missing(story_value) else str(story_value).strip()
        excerpt: str | None = None
        if story_id and story_id not in seen and fetched < fetch_budget:
            seen.add(story_id)
            fetched += 1
            story = client.call(
                f"News story {resolved.ric} {story_id}",
                lambda sid=story_id: ld.news.get_story(sid),
                warn=False,
                request_metadata={"operation": "news.get_story", "ric": resolved.ric, "story_id": story_id},
            )
            if story is not _CALL_TIMED_OUT and story:
                excerpt = _company_story_excerpt(str(story), story_terms)
        if not direct_headline and excerpt is None:
            continue
        display_text = headline if direct_headline else excerpt
        accepted = row.to_dict()
        if not direct_headline and headline_col is not None:
            accepted[headline_col] = display_text
        accepted["CompanyRelevantText"] = display_text
        accepted["CompanyRelevanceSource"] = "headline" if direct_headline else "story"
        accepted_headlines.append(accepted)
        if excerpt is not None:
            records.append({
                "Instrument": resolved.ric,
                "story_id": story_id,
                "company_excerpt": excerpt,
            })
        if len(accepted_headlines) >= target_count:
            break
    result.tables[f"news:{resolved.ric}"] = pd.DataFrame(
        accepted_headlines,
        columns=[*headlines.columns, "CompanyRelevantText", "CompanyRelevanceSource"],
    )
    result.metrics[f"{resolved.ric}:news_candidates"] = len(headlines)
    result.metrics[f"{resolved.ric}:news_story_checks"] = fetched
    result.metrics[f"{resolved.ric}:news_relevant"] = len(accepted_headlines)
    if records:
        result.tables[f"stories:{resolved.ric}"] = pd.DataFrame(records)


def _retrieve_market_news(ld: Any, client: _LSEGClient, result: ResearchResult) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=min(result.plan.lookback_days, 30))
    query = result.plan.raw_request.strip() or "Top News"
    response = client.call(
        "Market news",
        lambda: ld.news.get_headlines(query=query, count=40, start=start.isoformat(), end=end.isoformat()),
        request_metadata={
            "operation": "news.get_headlines", "query": query, "count": 40,
            "start": start.isoformat(), "end": end.isoformat(),
        },
    )
    frame = _frame_from_response(response)
    if not frame.empty:
        result.tables["market_news"] = frame


def _retrieve_peers(ld: Any, client: _LSEGClient, result: ResearchResult, resolved: ResolvedInstrument) -> None:
    from lseg.data.discovery import Peers

    peers = client.call(
        f"Peers {resolved.ric}",
        lambda: list(Peers(resolved.ric)),
        request_metadata={"operation": "discovery.Peers", "ric": resolved.ric},
    )
    if peers is _CALL_TIMED_OUT or not peers:
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
        request_metadata={
            "operation": "content.filings.search", "ric": resolved.ric, "organization_id": str(org_id),
            "start": start.isoformat(), "end": end.isoformat(), "limit": 10, "sort_order": "DESC",
        },
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
                "TR.PriceToSalesPerShare", "TR.PriceToBVPerShare",
                "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", "TR.ROAPercentTrailing12M",
                "TR.TotalReturn3Mo",
            ):
                values = _column(peers, field_name).dropna()
                if not values.empty:
                    result.metrics[f"{ric}:peer_median:{field_name}"] = float(values.median())

        headlines = result.tables.get(f"news:{ric}", pd.DataFrame())
        headline_col = (
            "CompanyRelevantText"
            if "CompanyRelevantText" in headlines.columns
            else _headline_column(headlines) if not headlines.empty else None
        )
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
            ("filings", f"filings:{ric}"),
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
    ordered = [item for _, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)]
    qualified = [
        item
        for item in ordered
        if int(result.metrics.get(f"{item.ric}:evidence_family_count", 0) or 0)
        >= workflow.minimum_evidence_families
    ]
    result.metrics["insufficient_evidence_finalists"] = [item.ric for item in ordered if item not in qualified]
    if not qualified:
        required_failures = [
            record for record in result.call_records
            if str(record.get("label", "")).startswith("Finalist ")
            and record.get("status") in {"failed", "timed_out", "cancelled"}
        ]
        if required_failures:
            raise LSEGResearchError(
                "Required finalist core evidence did not complete, so the run cannot be classified as a true no-match."
            )
        raise LSEGNoMatches(
            f"The finalists were retrieved, but none met the required {workflow.minimum_evidence_families} "
            "deep-research evidence families."
        )
    result.resolved = qualified
    result.metrics["selected_ric"] = qualified[0].ric


def _validate_workflow_outcome(result: ResearchResult, workflow: Any) -> None:
    """Enforce required workflow postconditions before reporting success."""
    if workflow.workflow_id == "stock_screen":
        if result.tables.get("screen", pd.DataFrame()).empty:
            raise LSEGNoMatches("The screen completed but no rows met every validated constraint.")
        return
    if workflow.workflow_id == "sector_opportunity":
        if not result.resolved:
            raise LSEGNoMatches("No finalist had enough validated evidence to support candidate selection.")
        winner = result.resolved[0]
        result.metrics["selected_ric"] = winner.ric
        count = int(result.metrics.get(f"{winner.ric}:evidence_family_count", 0) or 0)
        if count < workflow.minimum_evidence_families:
            raise LSEGNoMatches(
                f"The leading finalist had only {count} evidence families; {workflow.minimum_evidence_families} are required."
            )
        return
    if workflow.workflow_id == "market_news":
        if result.tables.get("market_news", pd.DataFrame()).empty:
            raise LSEGNoMatches("No matching Reuters/LSEG headlines were returned.")
        return
    if not result.resolved:
        raise LSEGResearchError("No named instrument was resolved.")
    sparse = [
        item.ric
        for item in result.resolved
        if int(result.metrics.get(f"{item.ric}:evidence_family_count", 0) or 0)
        < workflow.minimum_evidence_families
    ]
    if sparse:
        raise LSEGResearchError(
            "Required deep-research evidence was incomplete for: " + ", ".join(sparse)
        )
    if len(result.resolved) == 1:
        result.metrics["selected_ric"] = result.resolved[0].ric


def _finalize_call_metrics(result: ResearchResult) -> None:
    statuses = [record.get("status") for record in result.call_records]
    result.metrics["lseg_request_count"] = len(result.call_records)
    result.metrics["lseg_request_succeeded"] = statuses.count("succeeded")
    result.metrics["lseg_request_failed"] = statuses.count("failed")
    result.metrics["lseg_request_timed_out"] = statuses.count("timed_out")
    result.metrics["lseg_request_cancelled"] = statuses.count("cancelled")
    result.metrics["api_call_count"] = len(result.call_records)


def _persist_research_trace(
    result: ResearchResult,
    settings: Settings,
    outcome: str,
    error: BaseException | None = None,
) -> None:
    """Append a sanitized, table-free run record for post-hoc trace auditing."""
    _finalize_call_metrics(result)
    path = settings.project_root / "data" / "research_runs.jsonl"
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "request": result.plan.raw_request,
        "normalized_plan": result.plan.to_dict(),
        "compiled_screen": result.metrics.get("screen_expression"),
        "constraint_validation": result.metrics.get("constraint_validation"),
        "counts": {
            key: value for key, value in result.metrics.items()
            if key.startswith("screen_") or key.startswith("lseg_request_")
        },
        "selected_ric": result.metrics.get("selected_ric"),
        "resolved_rics": [item.ric for item in result.resolved],
        "evidence_coverage": result.metrics.get("evidence_coverage", {}),
        "data_request_coverage": result.metrics.get("data_request_coverage", {}),
        "call_records": result.call_records,
        "warnings": result.warnings[:30],
        "error_type": type(error).__name__ if error is not None else None,
        "error": str(error)[:1000] if error is not None else None,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_json_safe(payload), allow_nan=False, default=str) + "\n")
        result.metrics["trace_path"] = str(path)
    except Exception as exc:
        result.warnings.append(f"Research trace could not be persisted: {type(exc).__name__}: {exc}")


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
    if not plan.research_weights:
        fallback_policy = macro_default_policy(plan.macro_regime or "Regime incomplete")
        plan.macro_regime = fallback_policy.regime
        plan.research_weights = fallback_policy.weights.as_dict()
        plan.research_weight_source = fallback_policy.source
    workflow = get_workflow(plan.workflow, plan.mode, candidate_search=plan.screen.candidate_search)
    _emit_progress(
        progress_callback,
        5,
        "Preparing workflow",
        f"Selected {workflow.workflow_id.replace('_', ' ')} with {len(workflow.stages)} stages.",
    )
    result = ResearchResult(plan=plan)
    result.metrics["workflow"] = workflow.to_dict()
    result.metrics["macro_research_policy"] = {
        "regime": plan.macro_regime,
        "weights": plan.research_weights,
        "source": plan.research_weight_source,
    }
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
        execution = compile_execution_request(plan)
        plan = execution.plan
        result.plan = plan
        result.resolved.extend(execution.resolved)
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
                "Core data is complete. Retrieving price history, estimate revisions, Reuters news, peers, and filings.",
            )

            total_companies = max(len(result.resolved), 1)
            for index, resolved in enumerate(result.resolved):
                company_percent = 58 + int((index / total_companies) * 28)
                _emit_progress(
                    progress_callback,
                    company_percent,
                    f"Researching company {index + 1}/{len(result.resolved)}",
                    f"{resolved.company_name} ({resolved.ric}): price, revisions, Reuters news, peers, and filings.",
                )
                _retrieve_price_history(ld, client, result, resolved)
                _retrieve_estimate_history(ld, client, result, resolved)
                _retrieve_news(ld, client, result, resolved)
                _retrieve_news_stories(ld, client, result, resolved, workflow.news_stories_per_candidate)
                if "events" in plan.topics and workflow.workflow_id != "company_deep_dive":
                    _retrieve_upcoming_events(ld, client, result, resolved)
                _retrieve_peers(ld, client, result, resolved)
                _retrieve_filings(client, result, resolved)
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
        _validate_workflow_outcome(result, workflow)
        _finalize_call_metrics(result)
        statuses = [record.get("status") for record in result.call_records]
        _emit_progress(
            progress_callback,
            96,
            "Evidence collection complete",
            f"Attempted {len(result.call_records)} LSEG requests; "
            f"{statuses.count('succeeded')} succeeded and {statuses.count('timed_out')} timed out.",
        )
        if not result.has_data:
            detail = " | ".join(result.warnings[:5]) or "No rows were returned."
            raise LSEGResearchError(f"LSEG returned no usable research data. {detail}")
        _persist_research_trace(result, settings, "success")
        return result
    except ResearchCancelled:
        _persist_research_trace(result, settings, "cancelled")
        _emit_progress(progress_callback, None, "Research stopped", "Stopped by user.")
        raise
    except LSEGNoMatches as exc:
        _persist_research_trace(result, settings, "no_match", exc)
        _emit_progress(progress_callback, 100, "No validated matches", str(exc))
        raise
    except InstrumentResolutionError as exc:
        _persist_research_trace(result, settings, "resolution_failed", exc)
        _emit_progress(progress_callback, None, "Company not found", str(exc))
        raise
    except LSEGResearchError as exc:
        _persist_research_trace(result, settings, "failed", exc)
        _emit_progress(progress_callback, None, "Research failed", str(exc))
        raise
    except Exception as exc:
        _persist_research_trace(result, settings, "failed", exc)
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


def _latest_company_developments(frame: pd.DataFrame, limit: int = 2) -> list[str]:
    if "CompanyRelevantText" not in frame.columns:
        return _latest_titles(frame, limit)
    values = frame["CompanyRelevantText"]
    titles: list[str] = []
    for value in values.dropna().astype(str):
        cleaned = re.sub(r"\s+", " ", value).strip()
        if cleaned and cleaned not in titles:
            titles.append(cleaned)
        if len(titles) >= limit:
            break
    return titles


def _company_name(result: ResearchResult, resolved: ResolvedInstrument) -> str:
    name = _first_value(result, "profile", "TR.CommonName", resolved.ric)
    return str(name) if not _missing(name) else (resolved.company_name or resolved.query)


def _selected_resolved(result: ResearchResult) -> ResolvedInstrument | None:
    selected_ric = str(result.metrics.get("selected_ric") or "").strip()
    if selected_ric:
        for item in result.resolved:
            if item.ric == selected_ric:
                return item
    return result.resolved[0] if result.resolved else None


def _screen_row(result: ResearchResult, ric: str) -> pd.Series | None:
    frame = result.tables.get("screen", pd.DataFrame())
    if frame.empty or "Instrument" not in frame.columns:
        return None
    selected = frame[frame["Instrument"].astype(str) == ric]
    return None if selected.empty else selected.iloc[0]


def _sector_median(
    result: ResearchResult,
    field_name: str,
    *,
    exclude_ric: str | None = None,
    minimum_sample: int = 5,
) -> float | None:
    frame = result.tables.get("screen_universe", result.tables.get("screen", pd.DataFrame())).copy()
    if exclude_ric and "Instrument" in frame.columns:
        frame = frame[frame["Instrument"].astype(str) != exclude_ric]
    values = _column(frame, field_name).replace([math.inf, -math.inf], pd.NA).dropna()
    if field_name in {
        "TR.PE",
        "TR.PtoEPSMeanEst(Period=FY1)",
        "TR.EVToEBITDA",
        "TR.PriceToSalesPerShare",
        "TR.PricetoCFPerShare",
        "TR.PriceToBVPerShare",
    }:
        values = values[values > 0]
    if len(values) < minimum_sample:
        return None
    return float(values.median())


def _candidate_opportunities(result: ResearchResult, resolved: ResolvedInstrument) -> list[str]:
    ric = resolved.ric
    row = _screen_row(result, ric)
    items: list[str] = []
    financial = bool(
        row is not None and _normalized_code(row.get("TR.TRBCEconSectorCode")) == "55"
    )
    for field_name, label in (
        ("TR.PtoEPSMeanEst(Period=FY1)", "Forward P/E"),
        ("TR.EVToEBITDA", "EV/EBITDA"),
        ("TR.PriceToSalesPerShare", "Price/sales"),
        ("TR.PriceToBVPerShare", "Price/book"),
    ):
        if financial and field_name == "TR.EVToEBITDA":
            continue
        value = _numeric(_first_value(result, "valuation", field_name, ric))
        if value is None and row is not None:
            value = _numeric(row.get(field_name))
        median = _sector_median(result, field_name, exclude_ric=ric)
        if value is not None and median is not None and value > 0 and value < median * 0.90:
            items.append(
                f"{label} {_format_number(value)} is at least 10% below the screened median {_format_number(median)}."
            )
    roe = _numeric(_first_value(result, "profitability", "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", ric))
    median_roe = _sector_median(
        result, "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM", exclude_ric=ric
    )
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
    median_fpe = _sector_median(result, "TR.PtoEPSMeanEst(Period=FY1)", exclude_ric=ric)
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
        if pe is not None and pe > 0:
            valuation_parts.append(f"P/E {_format_number(pe)}")
        if fpe is not None and fpe > 0:
            valuation_parts.append(f"forward P/E {_format_number(fpe)}")
        if valuation_parts:
            lines.append("Valuation and expectations: " + ", ".join(valuation_parts) + ".")
        else:
            lines.append("Valuation and expectations: No usable positive valuation multiple was returned.")
        titles = _latest_company_developments(result.tables.get(f"news:{ric}", pd.DataFrame()), 2)
        if titles:
            lines.append("Catalyst: No positive catalyst was independently validated; recent developments include " + " | ".join(titles))
        else:
            lines.append("Catalyst: No specific catalyst was supported by the retrieved Reuters/LSEG evidence.")
        if risks:
            lines.append("Major risks: " + " ".join(risks[:2]))
        else:
            lines.append("Major risks: The retrieved quantitative fields did not identify a dominant risk; news and filing coverage may still be incomplete.")
        families = result.metrics.get(f"{ric}:evidence_families", [])
        lines.append(f"Coverage: {len(families)} evidence families available: {', '.join(families) if families else 'limited data' }.")
    return "\n".join(lines)


def _deterministic_screen_report(result: ResearchResult) -> str:
    frame = result.tables.get("screen", pd.DataFrame())
    expression = result.metrics.get("screen_expression")
    if result.plan.workflow == "sector_opportunity" or result.plan.screen.candidate_search:
        candidate = _selected_resolved(result)
        if candidate is None:
            return "The sector screen ran, but no finalist had enough usable data for a deep dive."
        ric = candidate.ric
        row = _screen_row(result, ric)
        lines = [f"Candidate: {_company_name(result, candidate)} ({ric})"]
        opportunities = _candidate_opportunities(result, candidate)
        risks = _candidate_risks(result, candidate)
        lines.append("Opportunity: " + (" ".join(opportunities[:2]) if opportunities else "The ranking was supported mainly by relative factor scores, not a clear standalone opportunity."))
        titles = _latest_company_developments(result.tables.get(f"news:{ric}", pd.DataFrame()), 2)
        if titles:
            lines.append("Catalyst: No positive catalyst was independently validated; recent developments include " + " | ".join(titles))
        else:
            lines.append("Catalyst: No specific catalyst was supported by the retrieved Reuters/LSEG evidence.")
        lines.append("Major risks: " + (" ".join(risks[:2]) if risks else "No dominant quantitative risk was available; review the retrieved news and filings."))
        alternatives: list[str] = []
        for other in (item for item in result.resolved if item.ric != ric):
            score = _numeric(result.metrics.get(f"{other.ric}:finalist_score"))
            alternatives.append(f"{_company_name(result, other)} ({other.ric})" + (f" score {_format_number(score)}" if score is not None else ""))
            if len(alternatives) >= 2:
                break
        category_scores: list[str] = []
        if row is not None:
            for category in ("Growth", "Profitability", "Valuation", "Balance Sheet"):
                score = _numeric(row.get(f"{category} Score"))
                if score is not None:
                    category_scores.append(f"{category.lower()} {_format_number(score)}")
        score_context = (
            " Category scores were " + ", ".join(category_scores) + "."
            if category_scores
            else ""
        )
        if alternatives:
            lines.append("Why selected: It had the strongest post-deep-dive score among adequately covered finalists." + score_context + " Other finalists were " + "; ".join(alternatives) + ".")
        else:
            lines.append("Why selected: It was the only finalist that met the required screen and deep-evidence postconditions." + score_context)
        families = result.metrics.get(f"{ric}:evidence_families", [])
        lines.append(
            f"Coverage: screened {int(result.metrics.get('screen_universe_count', len(frame)))} companies; "
            f"deeply researched {int(result.metrics.get('deep_dive_count', len(result.resolved)))}; "
            f"selected candidate has {len(families)} evidence families. Universe scope was capped at the top "
            f"{int(result.metrics.get('screen_universe_cap', 200))} qualifying companies by USD market capitalization. "
            f"Ranking used {result.plan.research_weight_source.replace('_', ' ')} weights for "
            f"{result.plan.macro_regime or 'an incomplete macro regime'}."
        )
        return "\n".join(lines)

    lines = [f"Screen results ({min(len(frame), 10)} shown)"]
    if result.plan.research_weights:
        lines.append(
            "Ranking weights: "
            + " | ".join(
                f"{key.replace('_', ' ').title()} {value * 100:.0f}%"
                for key, value in result.plan.research_weights.items()
            )
            + f" ({result.plan.research_weight_source.replace('_', ' ')})"
        )
    if expression:
        lines.append(f"Criteria: {expression}")
    for _, row in frame.head(10).iterrows():
        name_value = row.get("TR.CommonName")
        if _missing(name_value):
            name_value = row.get("Instrument")
        name = "Unknown" if _missing(name_value) else str(name_value)
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
        if name in {"screen", "screen_universe"}:
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
    payload = {
        "request": result.plan.raw_request,
        "workflow": result.plan.workflow,
        "investment_horizon": result.plan.investment_horizon,
        "macro_research_policy": result.metrics.get("macro_research_policy"),
        "resolved": [
            {"name": _company_name(result, item), "ticker": item.ticker, "ric": item.ric}
            for item in result.resolved
        ],
        "derived_metrics": result.metrics,
        "tables": tables,
        "unavailable": result.warnings[:20],
        "research_trace": result.calls,
        "request_trace": result.call_records,
    }
    return _json_safe(payload)


def research_context_payload(
    result: ResearchResult,
    question: str = "",
    *,
    max_characters: int = 12_000,
) -> dict[str, Any]:
    """Return a bounded, topic-specific slice of validated prior evidence."""
    selected = _selected_resolved(result)
    selected_ric = selected.ric if selected is not None else None
    lower = question.casefold()
    if re.search(r"\b(risks?|downside|concerns?|volatility|debt|leverage)\b", lower):
        table_names = ("profile", "risk", "fundamentals", f"news:{selected_ric}")
    elif re.search(r"\b(catalysts?|news|developments?|events?|drivers?)\b", lower):
        table_names = (
            "profile", f"news:{selected_ric}", f"stories:{selected_ric}",
            "events", "guidance",
        )
    elif re.search(r"\b(valuation|cheap|discount|p\s*/?\s*e|target|multiple)\b", lower):
        table_names = ("profile", "valuation", "recommendations", "screen")
    elif re.search(r"\b(what\s+does|business|products?|services?|industry|sector)\b", lower):
        table_names = ("profile",)
    else:
        table_names = (
            "profile", "fundamentals", "profitability", "valuation",
            "recommendations", "risk", f"news:{selected_ric}",
        )

    payload: dict[str, Any] = {
        "request": result.plan.raw_request[:1_000],
        "workflow": result.plan.workflow,
        "macro_research_policy": result.metrics.get("macro_research_policy"),
        "selected": (
            {
                "name": _company_name(result, selected),
                "ticker": selected.ticker,
                "ric": selected.ric,
            }
            if selected is not None
            else None
        ),
        "derived_metrics": {},
        "tables": {},
        "unavailable": [str(warning)[:300] for warning in result.warnings[:5]],
    }

    metrics = payload["derived_metrics"]
    assert isinstance(metrics, dict)
    for key, value in result.metrics.items():
        if key == "selected_ric" or (selected_ric and key.startswith(f"{selected_ric}:")):
            candidate = _json_safe(value)
            trial = {**payload, "derived_metrics": {**metrics, str(key): candidate}}
            if len(json.dumps(trial, default=str)) <= max_characters:
                metrics[str(key)] = candidate

    tables = payload["tables"]
    assert isinstance(tables, dict)
    for name in table_names:
        if not name or name in tables:
            continue
        frame = result.tables.get(name)
        if frame is None or frame.empty:
            continue
        limited = frame
        if selected_ric and "Instrument" in frame.columns:
            selected_rows = frame[frame["Instrument"].astype(str) == selected_ric]
            if not selected_rows.empty:
                limited = selected_rows
        records = _json_safe(limited.head(3).to_dict(orient="records"))
        trial = {**payload, "tables": {**tables, name: records}}
        if len(json.dumps(trial, default=str)) <= max_characters:
            tables[name] = records
    return payload


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def _plain_text_report(text: str) -> str:
    cleaned = re.sub(r"```(?:text|markdown)?", "", text, flags=re.I)
    cleaned = cleaned.replace("```", "")
    cleaned = re.sub(r"^\s{0,3}#{1,6}\s*", "", cleaned, flags=re.M)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"^\s*[-*]\s+", "• ", cleaned, flags=re.M)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _llm_report_is_valid(result: ResearchResult, text: str) -> bool:
    """Deterministically bind generated prose to the validated workflow result."""
    if not text.strip():
        return False
    lower = text.casefold()
    if any(token in lower for token in (
        "draft report", "none identified", "provided data does not contain",
        "no companies from the", "no company identified",
    )):
        return False
    workflow = result.plan.workflow
    required_labels = {
        "sector_opportunity": ("candidate:", "opportunity:", "catalyst:", "major risks:", "why selected:", "coverage:"),
        "company_deep_dive": ("company:", "opportunity:", "catalyst:", "major risks:", "valuation and expectations:", "coverage:"),
    }.get(str(workflow), ())
    lines = [line.strip().casefold() for line in text.splitlines() if line.strip()]
    if required_labels and (
        len(lines) != len(required_labels)
        or any(not any(line.startswith(label) for line in lines) for label in required_labels)
    ):
        return False
    if result.resolved and workflow in {"sector_opportunity", "company_deep_dive"}:
        selected = _selected_resolved(result)
        if selected is None:
            return False
        name = _company_name(result, selected).casefold()
        first_line = text.splitlines()[0].casefold() if text.splitlines() else lower
        identifiers = [selected.ric.casefold()]
        if len(name.strip()) >= 3:
            identifiers.append(name)
        if len(selected.ticker.strip()) >= 2:
            identifiers.append(selected.ticker.casefold())
        if not any(
            identifier
            and re.search(rf"(?<![a-z0-9]){re.escape(identifier)}(?![a-z0-9])", first_line)
            for identifier in identifiers
        ):
            return False
    return True


def _llm_report(result: ResearchResult, settings: Settings, cancel_event: Any | None = None) -> str | None:
    if not settings.groq_api_key or result.plan.workflow != "company_deep_dive":
        return None
    _raise_if_cancelled(cancel_event)
    try:
        from langchain_groq import ChatGroq

        llm = ChatGroq(model=settings.groq_model, temperature=0, max_retries=2, api_key=settings.groq_api_key)
        evidence = json.dumps(_evidence_payload(result), default=str, allow_nan=False)
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
                    "Treat the supplied macro research policy as an explanation of deterministic ranking weights, not as evidence about a company. "
                    + format_instruction,
                ),
                ("human", evidence),
            ]
        )
        _raise_if_cancelled(cancel_event)
        draft = _plain_text_report(str(getattr(response, "content", response)).strip())
        if not _llm_report_is_valid(result, draft):
            result.warnings.append("Evidence synthesis draft failed deterministic schema/entity validation.")
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
        checked = _plain_text_report(str(getattr(verification, "content", verification)).strip())
        if not _llm_report_is_valid(result, checked):
            result.warnings.append("Evidence synthesis verification failed deterministic schema/entity validation.")
            return None
        return checked
    except ResearchCancelled:
        raise
    except Exception as exc:
        result.warnings.append(f"Evidence synthesis: {type(exc).__name__}: {exc}")
        return None


def concise_report(result: ResearchResult, settings: Settings, cancel_event: Any | None = None) -> str:
    _raise_if_cancelled(cancel_event)
    if result.plan.workflow == "stock_screen":
        return _deterministic_screen_report(result)
    generated = _llm_report(result, settings, cancel_event=cancel_event)
    if generated:
        return generated
    if result.plan.mode == "screen":
        return _deterministic_screen_report(result)
    if result.plan.mode == "market_news":
        return _deterministic_news_report(result)
    return _deterministic_company_report(result)


def _deterministic_valuation_follow_up(result: ResearchResult) -> str:
    selected = _selected_resolved(result)
    if selected is None:
        return "The prior research did not select a company, so there is no valuation case to explain."

    ric = selected.ric
    name = _company_name(result, selected)
    discounts: list[str] = []
    supporting_evidence: list[str] = []
    valuation_fields = (
        ("TR.PtoEPSMeanEst(Period=FY1)", "forward P/E"),
        ("TR.EVToEBITDA", "EV/EBITDA"),
        ("TR.PriceToSalesPerShare", "price/sales"),
        ("TR.PricetoCFPerShare", "price/cash flow"),
        ("TR.PriceToBVPerShare", "price/book"),
    )
    for field_name, label in valuation_fields:
        value = _numeric(_first_value(result, "valuation", field_name, ric))
        if value is None:
            row = _screen_row(result, ric)
            value = _numeric(row.get(field_name)) if row is not None else None
        median = _sector_median(result, field_name, exclude_ric=ric)
        comparison_label = "screened cohort"
        if median is None:
            median = _numeric(result.metrics.get(f"{ric}:peer_median:{field_name}"))
            comparison_label = "direct-peer"
        if value is None or median is None or value <= 0 or median <= 0:
            continue
        difference = 1.0 - (value / median)
        if difference >= 0.10:
            discount = _format_number(abs(difference), percent=True).lstrip("+")
            discounts.append(
                f"Its {label} is {_format_number(value)} versus {_format_number(median)} for the "
                f"{comparison_label} median, about {discount} lower."
            )

    upside = result.metrics.get(f"{ric}:target_upside")
    if isinstance(upside, (int, float)) and upside > 0:
        supporting_evidence.append(
            f"The mean analyst price target implies {_format_number(upside, percent=True)} upside, "
            "but a target-price gap is expectations evidence, not proof that the shares are cheap."
        )

    if discounts:
        lines = [f"{name} ({ric}) looks relatively inexpensive on the retrieved multiples, not definitively undervalued."]
        lines.extend(discounts[:3])
    else:
        lines = [f"The retrieved evidence does not support calling {name} ({ric}) undervalued."]
        lines.append(
            "The retrieved evidence does not show a clear discount on the available valuation measures, "
            "so calling it undervalued would overstate the data."
        )
    lines.extend(supporting_evidence[:1])
    lines.append(
        "The discount could reflect real risks, so the valuation case should be weighed against the reported "
        "earnings revisions, leverage, volatility, news, and filing evidence."
    )
    return "\n".join(lines)


def _deterministic_risk_follow_up(result: ResearchResult) -> str:
    selected = _selected_resolved(result)
    if selected is None:
        return "The prior research did not select a company, so there is no company-specific risk case to explain."
    name = _company_name(result, selected)
    risks = _candidate_risks(result, selected)
    if not risks:
        return (
            f"The retrieved evidence for {name} ({selected.ric}) did not identify a dominant quantitative risk. "
            "That means risk evidence is incomplete—not that the company is risk-free. Review the retrieved Reuters headlines and filings."
        )
    return f"The main retrieved risks for {name} ({selected.ric}) are:\n" + "\n".join(
        f"• {item}" for item in risks
    )


def _deterministic_catalyst_follow_up(result: ResearchResult) -> str:
    selected = _selected_resolved(result)
    if selected is None:
        return "The prior research did not select a company, so there is no company-specific catalyst to explain."
    titles = _latest_company_developments(result.tables.get(f"news:{selected.ric}", pd.DataFrame()), 2)
    if not titles:
        return (
            f"No specific catalyst for {_company_name(result, selected)} ({selected.ric}) was supported by the "
            "retrieved Reuters/LSEG evidence. The ranking should not be interpreted as catalyst-driven."
        )
    return f"The retrieved developments for {_company_name(result, selected)} ({selected.ric}) are:\n" + "\n".join(
        f"• {title}" for title in titles
    )


def _deterministic_selection_follow_up(result: ResearchResult) -> str:
    selected = _selected_resolved(result)
    if selected is None:
        return "The prior research did not select a company."
    row = _screen_row(result, selected.ric)
    score = _numeric(row.get("Research Score")) if row is not None else None
    families = result.metrics.get(f"{selected.ric}:evidence_families", [])
    parts = [
        f"{_company_name(result, selected)} ({selected.ric}) was selected only after passing the requested "
        "country/TRBC postconditions and the minimum screen and deep-evidence thresholds."
    ]
    if score is not None:
        parts.append(f"Its final screen score was {_format_number(score)}.")
    parts.append(f"The deep dive retained {len(families)} evidence families.")
    alternatives = [item for item in result.resolved if item.ric != selected.ric][:2]
    if alternatives:
        parts.append(
            "Other adequately covered finalists were "
            + "; ".join(f"{_company_name(result, item)} ({item.ric})" for item in alternatives)
            + "."
        )
    return " ".join(parts)


def _deterministic_metric_follow_up(result: ResearchResult, question: str) -> str | None:
    selected = _selected_resolved(result)
    if selected is None:
        return None
    lower = question.casefold()
    metric_specs = (
        (
            r"\b(?:forward|fwd)\s+p\s*/?\s*e\b|\bp\s*/?\s*e\s+(?:forward|fy1)\b",
            "forward P/E",
            "TR.PtoEPSMeanEst(Period=FY1)",
            ("valuation",),
            "multiple",
        ),
        (r"\b(?:trailing\s+)?p\s*/?\s*e\b", "trailing P/E", "TR.PE", ("valuation",), "multiple"),
        (r"\bev\s*/?\s*ebitda\b", "EV/EBITDA", "TR.EVToEBITDA", ("valuation",), "multiple"),
        (
            r"\b(?:price\s*(?:to|/)\s*book|p\s*/?\s*b)\b",
            "price/book",
            "TR.PriceToBVPerShare",
            ("valuation",),
            "multiple",
        ),
        (
            r"\b(?:price\s*(?:to|/)\s*sales|p\s*/?\s*s)\b",
            "price/sales",
            "TR.PriceToSalesPerShare",
            ("valuation",),
            "multiple",
        ),
        (
            r"\b(?:return\s+on\s+equity|roe)\b",
            "ROE",
            "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM",
            ("profitability",),
            "percent",
        ),
        (
            r"\b(?:return\s+on\s+assets?|roa)\b",
            "ROA",
            "TR.ROAPercentTrailing12M",
            ("profitability",),
            "percent",
        ),
        (
            r"\b(?:market\s+cap(?:italization)?)\b",
            "market capitalization",
            "TR.CompanyMarketCap",
            ("profile",),
            "number",
        ),
        (
            r"\b(?:mean\s+)?price\s+target\b",
            "mean analyst price target",
            "TR.PriceTargetMean",
            ("recommendations",),
            "number",
        ),
        (
            r"\b(?:dividend\s+yield|yield)\b",
            "dividend yield",
            "TR.DividendYield",
            ("valuation",),
            "percent",
        ),
    )
    matched = next((spec for spec in metric_specs if re.search(spec[0], lower)), None)
    if matched is None:
        return None
    _pattern, label, field_name, table_names, value_kind = matched
    value: float | None = None
    for table_name in table_names:
        value = _numeric(_first_value(result, table_name, field_name, selected.ric))
        if value is not None:
            break
    if value is None:
        row = _screen_row(result, selected.ric)
        value = _numeric(row.get(field_name)) if row is not None else None

    name = _company_name(result, selected)
    if value is None or (value_kind == "multiple" and value <= 0):
        qualifier = (
            " as a meaningful positive multiple"
            if value_kind == "multiple"
            else ""
        )
        return (
            f"The prior LSEG evidence does not provide a usable {label}{qualifier} for "
            f"{name} ({selected.ric})."
        )
    formatted = _format_number(value) + ("%" if value_kind == "percent" else "")
    return f"The retrieved {label} for {name} ({selected.ric}) is {formatted}."


def _deterministic_request_diagnostics_follow_up(result: ResearchResult) -> str:
    records = result.call_records
    unsuccessful = [
        record for record in records if str(record.get("status", "")).casefold() != "succeeded"
    ]
    succeeded = sum(
        str(record.get("status", "")).casefold() == "succeeded" for record in records
    )
    if not records:
        return (
            "The prior result does not contain per-request trace records, so I cannot identify "
            "which LSEG request was unsuccessful."
        )
    if not unsuccessful:
        return f"All {len(records)} recorded LSEG requests succeeded."

    details: list[str] = []
    for record in unsuccessful:
        number = record.get("request_number", "?")
        label = str(record.get("label") or "unlabeled request")
        status = str(record.get("status") or "unsuccessful").replace("_", " ")
        error_type = record.get("error_type")
        error_message = str(record.get("error_message") or "").strip()
        if not error_message:
            warning_prefix = f"{label}:"
            matching_warning = next(
                (
                    warning[len(warning_prefix):].strip()
                    for warning in result.warnings
                    if warning.startswith(warning_prefix)
                    and "disabling further" not in warning.casefold()
                ),
                "",
            )
            error_message = matching_warning
        if error_type and error_message.casefold().startswith(f"{str(error_type).casefold()}:"):
            error_message = error_message.split(":", 1)[1].strip()
        cause = ""
        if error_type and error_message:
            cause = f" ({error_type}: {error_message})"
        elif error_type:
            cause = f" ({error_type})"
        elif error_message:
            cause = f" ({error_message})"
        details.append(f"request #{number}, {label}: {status}{cause}")
    return (
        f"{succeeded} of {len(records)} recorded LSEG requests succeeded. "
        f"The unsuccessful {'request was' if len(details) == 1 else 'requests were'} "
        + "; ".join(details)
        + "."
    )


def is_request_diagnostics_follow_up(
    question: str,
    result: ResearchResult | None = None,
) -> bool:
    """Recognize questions about the immediately prior LSEG request trace."""
    lower = question.casefold()
    if result is not None:
        total = len(result.call_records) or int(result.metrics.get("lseg_request_count", 0) or 0)
        succeeded = sum(
            str(record.get("status", "")).casefold() == "succeeded"
            for record in result.call_records
        )
        if not result.call_records:
            succeeded = int(result.metrics.get("lseg_request_succeeded", 0) or 0)
        referenced_counts = {int(value) for value in re.findall(r"\b\d+\b", lower)}
        asks_about_gap = bool(
            re.search(
                r"\b(?:why|what|which|only|missing|fail\w*|unsuccessful|"
                r"time(?:d)?\s*out|did(?:n't|\s+not)|not\s+run)\b",
                lower,
            )
        )
        if total > succeeded and {total, succeeded}.issubset(referenced_counts) and asks_about_gap:
            return True
    contrasts_success_and_failure = bool(
        re.search(r"\b(?:succeed\w*|complete\w*)\b", lower)
        and re.search(
            r"\b(?:fail\w*|unsuccessful|time(?:d)?\s*out|did(?:n't|\s+not)|"
            r"which|what)\b",
            lower,
        )
    )
    return bool(
        contrasts_success_and_failure
        or re.search(
            r"\b(?:lseg|api)\s+(?:requests?|calls?)\b|"
            r"\b(?:requests?|calls?)\b.{0,60}\b(?:succeed\w*|fail\w*|"
            r"time(?:d)?\s*out|complete\w*|did(?:n't|\s+not))\b",
            lower,
        )
        or re.fullmatch(
            r"\s*(?:which|what)\s+(?:one|request|call)(?:\s+(?:was|is))?\s+"
            r"(?:not\s+successful|unsuccessful|failed|time(?:d)?\s*out|did(?:n't|\s+not))\??\s*",
            lower,
        )
    )


def answer_follow_up(result: ResearchResult, question: str, settings: Settings) -> str:
    """Answer a contextual question using only the immediately prior research result."""
    lower = question.casefold()
    if is_request_diagnostics_follow_up(question, result):
        return _deterministic_request_diagnostics_follow_up(result)
    metric_answer = _deterministic_metric_follow_up(result, question)
    if metric_answer is not None:
        return metric_answer
    if re.search(
        r"\b(?:undervalu\w*|valuation|cheap|inexpensive|discount(?:ed)?|relative\s+value)\b",
        lower,
    ):
        return _deterministic_valuation_follow_up(result)
    if re.search(r"\b(risks?|downside|concerns?|go wrong)\b", lower):
        return _deterministic_risk_follow_up(result)
    if re.search(r"\b(catalysts?|developments?|drivers?)\b", lower):
        return _deterministic_catalyst_follow_up(result)
    if re.search(
        r"\b(?:selected|selection|chosen|picked|why\s+(?:this|that|it)|how\s+was\s+(?:this|that|it))\b",
        lower,
    ):
        return _deterministic_selection_follow_up(result)
    fallback = (
        "I could not answer that specific follow-up from the validated prior evidence. "
        "Try asking for a retrieved metric, valuation, risk, catalyst, selection rationale, "
        "or request diagnostic."
    )
    if not settings.groq_api_key:
        return fallback
    try:
        from langchain_groq import ChatGroq

        llm = ChatGroq(model=settings.groq_model, temperature=0, max_retries=0, api_key=settings.groq_api_key)
        evidence = json.dumps(
            research_context_payload(result, question, max_characters=10_000),
            default=str,
        )
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
