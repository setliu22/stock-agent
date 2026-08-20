from __future__ import annotations

import pytest

from portfolio import company_resolver as resolver


def test_extracts_company_name_from_request() -> None:
    assert resolver.extract_security_reference("Analyze Palantir using LSEG") == "Palantir"
    assert resolver.extract_security_reference("research PLTR") == "PLTR"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("do some research on zbra", "zbra"),
        ("Could you please conduct an analysis of Qualcomm?", "Qualcomm"),
        ("using LSEG to analyze QCOM", "QCOM"),
        ("research Research In Motion", "Research In Motion"),
        ("research ON", "ON"),
    ],
)
def test_extracts_security_without_leaking_or_deleting_command_words(query, expected) -> None:
    assert resolver.extract_security_reference(query) == expected


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


def test_quote_ranking_rejects_equally_plausible_companies() -> None:
    quotes = [
        {"symbol": "ACME", "quoteType": "EQUITY", "exchange": "NMS", "longname": "Acme Holdings"},
        {"symbol": "ACM", "quoteType": "EQUITY", "exchange": "NYQ", "longname": "Acme Corporation"},
    ]
    with pytest.raises(resolver.AmbiguousInstrumentError, match="ACME.*ACM"):
        resolver.choose_best_yahoo_quote("Acme", quotes)


def test_exact_ticker_wins_even_when_other_candidates_score_similarly() -> None:
    quotes = [
        {"symbol": "ON", "quoteType": "EQUITY", "exchange": "NMS", "longname": "ON Semiconductor"},
        {"symbol": "ONON", "quoteType": "EQUITY", "exchange": "NYQ", "longname": "On Holding"},
    ]
    assert resolver.choose_best_yahoo_quote("ON", quotes)["symbol"] == "ON"


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
