#!/usr/bin/env python3
"""Thin JSON adapter between the native app and LSEG's desktop Python library.

The native Swift code decides what to request and interprets the result. This
process only opens the supported Workspace session, performs bounded read-only
requests, and serializes the returned rows as JSON.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_project_environment() -> None:
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _float_setting(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _session_state(session: Any) -> str:
    state = getattr(session, "open_state", None) or getattr(session, "state", None)
    return str(getattr(state, "name", None) or state or "Unknown")


def _session_is_open(session: Any) -> bool:
    state = _session_state(session).casefold().replace("_", " ")
    return "opened" in state and "not opened" not in state and "closed" not in state


def _configure_session(ld: Any, session_name: str, app_key: str | None) -> None:
    try:
        config = ld.get_config()
    except Exception:
        return
    values = (
        ("logs.transports.file.enabled", False),
        ("logs.level", "info"),
        ("http.request-timeout", int(max(5, _float_setting("LSEG_REQUEST_TIMEOUT", 20)))),
        ("sessions.default", session_name),
    )
    for key, value in values:
        try:
            config.set_param(key, value)
        except Exception:
            try:
                config[key] = value
            except Exception:
                pass
    if app_key:
        try:
            config.set_param("sessions.desktop.workspace.app-key", app_key)
        except Exception:
            try:
                config["sessions.desktop.workspace.app-key"] = app_key
            except Exception:
                pass


def _open_session(ld: Any) -> Any:
    _load_project_environment()
    session_name = os.getenv("LSEG_SESSION", "desktop.workspace").strip() or "desktop.workspace"
    app_key = os.getenv("LSEG_APP_KEY", "").strip() or None
    _configure_session(ld, session_name, app_key)
    attempts = [(session_name, lambda: ld.open_session(name=session_name))]
    if session_name == "desktop.workspace":
        attempts.append(("desktop default", lambda: ld.open_session()))
    errors = []
    for label, opener in attempts:
        try:
            try:
                ld.close_session()
            except Exception:
                pass
            session = opener()
            deadline = time.monotonic() + max(1, _float_setting("LSEG_SESSION_TIMEOUT", 8))
            while time.monotonic() < deadline:
                state = _session_state(session)
                if _session_is_open(session):
                    return session
                if "closed" in state.casefold():
                    break
                time.sleep(0.2)
            errors.append(f"{label} state={_session_state(session)}")
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
    detail = "; ".join(errors)
    if session_name == "desktop.workspace":
        raise RuntimeError(
            "LSEG Workspace is not connected. Open Workspace, sign in, and keep it running, "
            f"then retry. Session details: {detail}"
        )
    raise RuntimeError(f"The configured LSEG session could not connect. {detail}")


COMPANY_FIELDS = (
    "TR.CommonName",
    "TR.TickerSymbol",
    "TR.TRBCEconomicSector",
    "TR.TRBCBusinessSector",
    "TR.TRBCIndustryGroup",
    "TR.TRBCIndustry",
    "TR.CompanyMarketCap",
    "TR.PriceClose",
    "TR.BusinessSummary",
    "TR.TotalRevenue(Period=LTM,Methodology=InterimSum)",
    "TR.ReturnonAvgTotEqtyPctNetIncomeBeforeExtraItemsTTM",
    "TR.PE",
    "TR.PtoEPSMeanEst(Period=FY1)",
    "TR.EVToEBITDA",
    "TR.PriceTargetMean",
    "TR.EPSMean(Period=FY1)",
    "TR.RevenueMean(Period=FY1)",
    "TR.F.DebtTot",
    "TR.F.CashCashEquiv",
)


FACTS = {
    "TR.COMPANYMARKETCAP": ("Market cap", "USD"),
    "TR.PRICECLOSE": ("Share price", "USD"),
    "TR.TOTALREVENUE(PERIOD=LTM,METHODOLOGY=INTERIMSUM)": ("LTM revenue", "USD"),
    "TR.RETURNONAVGTOTEQTYPCTNETINCOMEBEFOREEXTRAITEMSTTM": ("Return on equity", "%"),
    "TR.PE": ("Trailing P/E", "x"),
    "TR.PTOEPSMEANEST(PERIOD=FY1)": ("Forward P/E", "x"),
    "TR.EVTOEBITDA": ("EV / EBITDA", "x"),
    "TR.PRICETARGETMEAN": ("Mean price target", "USD"),
    "TR.EPSMEAN(PERIOD=FY1)": ("FY1 EPS estimate", "USD/share"),
    "TR.REVENUEMEAN(PERIOD=FY1)": ("FY1 revenue estimate", "USD"),
    "TR.F.DEBTTOT": ("Total debt", "USD"),
    "TR.F.CASHCASHEQUIV": ("Cash and equivalents", "USD"),
}


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if bool(value != value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _value(row: Any, name: str) -> Any:
    target = name.upper()
    for column in row.index:
        if str(column).upper() == target:
            return _clean(row[column])
    return None


def _record(row: Any, resolved: Any | None = None) -> dict[str, Any]:
    ric = str(_value(row, "Instrument") or getattr(resolved, "ric", "")).strip()
    ticker = str(
        _value(row, "TR.TickerSymbol")
        or getattr(resolved, "ticker", "")
        or ric.split(".", 1)[0]
    ).strip().upper()
    name = str(
        _value(row, "TR.CommonName")
        or getattr(resolved, "company_name", "")
        or ticker
    ).strip()
    classification = next(
        (
            str(value).strip()
            for value in (
                _value(row, "TR.TRBCIndustry"),
                _value(row, "TR.TRBCIndustryGroup"),
                _value(row, "TR.TRBCBusinessSector"),
                _value(row, "TR.TRBCEconomicSector"),
            )
            if value
        ),
        "",
    )
    facts = []
    for field, (label, unit) in FACTS.items():
        value = _value(row, field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            facts.append({"label": label, "value": float(value), "unit": unit})
    market_cap = _value(row, "TR.CompanyMarketCap")
    return {
        "ticker": ticker,
        "ric": ric,
        "name": name,
        "industry": classification,
        "businessSummary": str(_value(row, "TR.BusinessSummary") or "").strip(),
        "marketCap": float(market_cap) if isinstance(market_cap, (int, float)) else None,
        "facts": facts,
    }


def _get_data(ld: Any, universe: Any) -> Any:
    kwargs = {
        "universe": universe,
        "fields": list(COMPANY_FIELDS),
        "parameters": {"Curn": "USD"},
    }
    header_type = getattr(getattr(ld, "HeaderType", None), "NAME", None)
    if header_type is not None:
        kwargs["header_type"] = header_type
    try:
        return ld.get_data(**kwargs)
    except TypeError as exc:
        if "header_type" not in str(exc).casefold():
            raise
        kwargs.pop("header_type", None)
        return ld.get_data(**kwargs)


def _response_frame(response: Any) -> Any:
    if response is None:
        return None
    if hasattr(response, "columns") and hasattr(response, "empty"):
        return response
    data = getattr(response, "data", None)
    frame = getattr(data, "df", None)
    return frame if frame is not None else getattr(response, "df", None)


def _ric_values(response: Any) -> list[str]:
    frame = _response_frame(response)
    if frame is None or getattr(frame, "empty", True):
        return []
    for column in frame.columns:
        if str(column).upper() == "RIC" or str(column).upper().endswith(".RIC"):
            return [str(value).strip() for value in frame[column].dropna() if str(value).strip()]
    return []


def _ric_score(ric: str) -> tuple[int, str]:
    upper = ric.upper().strip()
    score = 0
    if upper.endswith(".O"):
        score = 100
    elif upper.endswith(".N"):
        score = 95
    elif upper.endswith(".A"):
        score = 80
    elif upper.endswith(".K"):
        score = 70
    if any(marker in upper for marker in ("^", "=", "ATMIV", " VOL")):
        score -= 100
    return score, upper


def _ticker_to_ric(ticker: str) -> str:
    ticker = str(ticker).strip().upper().replace("/", "-")
    if not ticker:
        raise ValueError("No ticker was provided.")
    errors = []
    try:
        from lseg.data.discovery import SymbolTypes, convert_symbols

        try:
            response = convert_symbols(
                symbols=[ticker],
                from_symbol_type=SymbolTypes.TICKER_SYMBOL,
                to_symbol_types=[SymbolTypes.RIC],
                preferred_country_code="USA",
            )
        except TypeError:
            response = convert_symbols(
                symbols=[ticker],
                from_symbol_type=SymbolTypes.TICKER_SYMBOL,
                to_symbol_types=[SymbolTypes.RIC],
            )
        values = _ric_values(response)
        if values:
            return max(values, key=_ric_score)
    except Exception as exc:
        errors.append(f"discovery conversion: {type(exc).__name__}: {exc}")
    try:
        from lseg.data.content import symbol_conversion

        response = symbol_conversion.Definition(
            symbols=[ticker],
            from_symbol_type=symbol_conversion.SymbolTypes.TICKER_SYMBOL,
            to_symbol_types=[symbol_conversion.SymbolTypes.RIC],
        ).get_data()
        values = _ric_values(response)
        if values:
            return max(values, key=_ric_score)
    except Exception as exc:
        errors.append(f"content conversion: {type(exc).__name__}: {exc}")
    raise RuntimeError(f"LSEG could not convert ticker {ticker}: {'; '.join(errors)}")


def _company_records(ld: Any, tickers: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    resolved = []
    failures = []
    for ticker in tickers[:8]:
        try:
            normalized = str(ticker).strip().upper()
            resolved.append(
                SimpleNamespace(
                    ticker=normalized,
                    ric=_ticker_to_ric(normalized),
                    company_name="",
                )
            )
        except Exception as exc:
            failures.append(f"{ticker}: {type(exc).__name__}: {exc}")
    if not resolved:
        return [], failures
    frame = _get_data(ld, [item.ric for item in resolved])
    by_ric = {item.ric: item for item in resolved}
    records = []
    for _, row in frame.iterrows():
        ric = str(_value(row, "Instrument") or "").strip()
        records.append(_record(row, by_ric.get(ric)))
    return records, failures


def _screen_records(ld: Any, screens: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    from lseg.data.discovery import Screener

    batches = []
    failures = []
    for screen in screens[:6]:
        label = str(screen.get("label") or "Screen")
        body = str(screen.get("body") or "").strip()
        if not body:
            continue
        try:
            try:
                frame = _get_data(ld, Screener(body))
            except Exception:
                frame = _get_data(ld, f"SCREEN({body})")
            batch = []
            for _, row in frame.iterrows():
                record = _record(row)
                record["universe"] = label
                batch.append(record)
            batches.append(batch)
        except Exception as exc:
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
    # Interleave screens so a broad first industry cannot consume the entire budget.
    records = []
    seen = set()
    for index in range(max((len(batch) for batch in batches), default=0)):
        for batch in batches:
            if index >= len(batch):
                continue
            record = batch[index]
            identity = record["ric"] or record["ticker"]
            if not identity or identity in seen:
                continue
            seen.add(identity)
            records.append(record)
            if len(records) >= limit:
                return records, failures
    return records, failures


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        operation = str(payload.get("operation") or "status")
        import lseg.data as ld

        _open_session(ld)
        if operation == "status":
            output = {"ok": True, "companies": [], "failures": []}
        elif operation == "company":
            companies, failures = _company_records(ld, list(payload.get("tickers") or []))
            output = {"ok": True, "companies": companies, "failures": failures}
        elif operation == "screen":
            companies, failures = _screen_records(
                ld,
                list(payload.get("screens") or []),
                max(1, min(int(payload.get("limit") or 16), 120)),
            )
            output = {"ok": True, "companies": companies, "failures": failures}
        else:
            raise ValueError(f"Unsupported bridge operation: {operation}")
        try:
            ld.close_session()
        except Exception:
            pass
        sys.stdout.write(json.dumps(output, allow_nan=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}", "companies": [], "failures": []},
                allow_nan=False,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
