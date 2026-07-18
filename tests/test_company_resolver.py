from __future__ import annotations

import pandas as pd
import pytest

from portfolio import company_resolver as resolver


def test_extracts_company_name_from_request() -> None:
    assert resolver.extract_security_reference("Analyze Palantir using LSEG") == "Palantir"
    assert resolver.extract_security_reference("research PLTR") == "PLTR"


def test_company_words_are_not_misread_as_tickers() -> None:
    assert resolver.is_explicit_ticker("PLTR")
    assert not resolver.is_explicit_ticker("Palantir")
    assert not resolver.is_explicit_ticker("Apple")


def test_quote_ranking_prefers_us_equity() -> None:
    quotes = [
        {
            "symbol": "PLTR.MX",
            "quoteType": "EQUITY",
            "exchange": "MEX",
            "longname": "Palantir Technologies Inc.",
        },
        {
            "symbol": "PLTR",
            "quoteType": "EQUITY",
            "exchange": "NMS",
            "longname": "Palantir Technologies Inc.",
        },
    ]
    assert resolver.choose_best_yahoo_quote("Palantir", quotes)["symbol"] == "PLTR"


def test_ric_ranking_prefers_primary_us_listing() -> None:
    assert resolver.choose_best_ric(["PLTR.L", "PLTR.O", "PLTR.MX"]) == "PLTR.O"
    assert resolver.choose_best_ric(["IBM.L", "IBM.N"], "NYSE") == "IBM.N"


def test_company_name_resolution_is_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resolver,
        "_search_yahoo_quotes",
        lambda query, max_results=10: [
            {
                "symbol": "PLTR",
                "quoteType": "EQUITY",
                "exchange": "NMS",
                "longname": "Palantir Technologies Inc.",
            }
        ],
    )
    resolver.company_name_to_ticker.cache_clear()
    ticker, company, exchange = resolver.company_name_to_ticker("Palantir")
    assert ticker == "PLTR"
    assert company == "Palantir Technologies Inc."
    assert exchange == "NMS"


def test_extract_frame_accepts_dataframe() -> None:
    frame = pd.DataFrame({"RIC": ["PLTR.O"]})
    assert resolver._extract_frame(frame) is frame
