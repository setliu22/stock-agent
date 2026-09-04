#!/usr/bin/env python3
"""Thin JSON adapter between the native app and LSEG's desktop Python library.

The native Swift code decides what to request and interprets the result. This
process only opens the supported Workspace session, performs bounded read-only
requests, and serializes the returned rows as JSON.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


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


def _company_records(ld: Any, tickers: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    from portfolio.company_resolver import resolve_instrument

    resolved = []
    failures = []
    for ticker in tickers[:8]:
        try:
            resolved.append(resolve_instrument(str(ticker)))
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

    records = []
    failures = []
    seen = set()
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
            for _, row in frame.iterrows():
                record = _record(row)
                identity = record["ric"] or record["ticker"]
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                records.append(record)
                if len(records) >= limit:
                    return records, failures
        except Exception as exc:
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
    return records, failures


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        operation = str(payload.get("operation") or "status")
        import lseg.data as ld
        from portfolio.config import get_settings
        from portfolio.lseg_research import _open_lseg_session

        _open_lseg_session(ld, get_settings())
        if operation == "status":
            output = {"ok": True, "companies": [], "failures": []}
        elif operation == "company":
            companies, failures = _company_records(ld, list(payload.get("tickers") or []))
            output = {"ok": True, "companies": companies, "failures": failures}
        elif operation == "screen":
            companies, failures = _screen_records(
                ld,
                list(payload.get("screens") or []),
                max(1, min(int(payload.get("limit") or 16), 48)),
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
