"""Resolve company names and exchange tickers to LSEG RICs.

Company names are matched through yfinance search. The resulting ticker is then
converted with LSEG's symbol conversion service. No company-to-ticker lookup
list is hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Any, Iterable, Mapping, Sequence


_REQUEST_WORDS = {
    "analyze", "analyse", "research", "review", "investigate", "evaluate",
    "stock", "company", "shares", "using", "use", "with", "via", "lseg",
    "refinitiv", "workspace", "please", "tell", "me", "about", "look", "up",
}

_US_EXCHANGES = {
    "NMS", "NGM", "NCM", "NAS", "NASDAQ", "NYQ", "NYSE", "ASE", "AMEX",
    "PCX", "BATS",
}


@dataclass(frozen=True)
class ResolvedInstrument:
    original: str
    query: str
    ticker: str
    ric: str
    company_name: str | None = None
    exchange: str | None = None
    resolution_source: str = "lseg"


class InstrumentResolutionError(RuntimeError):
    """Raised when a company or ticker cannot be resolved safely."""


def extract_security_reference(text: str) -> str:
    cleaned = re.sub(r"[^\w.&'/-]+", " ", str(text), flags=re.UNICODE).strip()
    if not cleaned:
        raise InstrumentResolutionError("No company name or ticker was provided.")

    tokens = cleaned.split()
    kept = [token for token in tokens if token.casefold() not in _REQUEST_WORDS]
    result = " ".join(kept).strip(" ,.;:")
    return result or cleaned


def is_probable_ticker(value: str) -> bool:
    value = value.strip()
    if not value or " " in value or len(value) > 24:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9^][A-Za-z0-9.^/_-]*", value))


def is_explicit_ticker(value: str) -> bool:
    value = value.strip()
    if not is_probable_ticker(value):
        return False
    return value == value.upper() or any(char.isdigit() or char in ".^/" for char in value)


def _normalise_ticker(value: str) -> str:
    return value.strip().upper().replace("/", "-")


def _quote_text(quote: Mapping[str, Any]) -> str:
    return " ".join(
        str(quote.get(key) or "")
        for key in ("shortname", "longname", "name", "symbol")
    ).casefold()


def _score_quote(query: str, quote: Mapping[str, Any]) -> tuple[int, str]:
    symbol = str(quote.get("symbol") or "").strip()
    if not symbol:
        return (-10_000, "")

    quote_type = str(quote.get("quoteType") or quote.get("typeDisp") or "").upper()
    exchange = str(quote.get("exchange") or quote.get("exchDisp") or "").upper()
    text = _quote_text(quote)
    q = query.casefold().strip()

    score = 0
    if quote_type == "EQUITY":
        score += 80
    elif quote_type == "ETF":
        score += 40
    elif quote_type:
        score -= 30

    if exchange in _US_EXCHANGES:
        score += 35

    if text == q:
        score += 120
    elif q and q in text:
        score += 60

    q_words = {word for word in re.findall(r"[a-z0-9]+", q) if len(word) > 1}
    text_words = set(re.findall(r"[a-z0-9]+", text))
    score += 8 * len(q_words & text_words)

    if symbol.upper() == query.upper():
        score += 150

    return (score, symbol)


def choose_best_yahoo_quote(query: str, quotes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    candidates = [quote for quote in quotes if str(quote.get("symbol") or "").strip()]
    if not candidates:
        raise InstrumentResolutionError(f"No listed security matched {query!r}.")

    ranked = sorted(candidates, key=lambda quote: _score_quote(query, quote), reverse=True)
    best = ranked[0]
    if _score_quote(query, best)[0] < 20:
        names = ", ".join(str(quote.get("symbol")) for quote in ranked[:5])
        raise InstrumentResolutionError(
            f"The company name {query!r} was ambiguous. Candidate tickers: {names}."
        )
    return best


def _search_yahoo_quotes(query: str, max_results: int = 10) -> list[Mapping[str, Any]]:
    try:
        import yfinance as yf
    except Exception as exc:
        raise InstrumentResolutionError("yfinance is unavailable for company-name resolution.") from exc

    try:
        search = yf.Search(query, max_results=max_results, news_count=0, raise_errors=True)
        quotes = getattr(search, "quotes", None) or []
    except TypeError:
        search = yf.Search(query, max_results=max_results, news_count=0)
        quotes = getattr(search, "quotes", None) or []
    except Exception as exc:
        raise InstrumentResolutionError(
            f"Company-name search failed for {query!r}: {type(exc).__name__}: {exc}"
        ) from exc

    return [quote for quote in quotes if isinstance(quote, Mapping)]


@lru_cache(maxsize=512)
def company_name_to_ticker(company_name: str) -> tuple[str, str | None, str | None]:
    query = extract_security_reference(company_name)
    quote = choose_best_yahoo_quote(query, _search_yahoo_quotes(query))
    ticker = _normalise_ticker(str(quote.get("symbol") or ""))
    if not ticker:
        raise InstrumentResolutionError(f"No ticker was returned for {query!r}.")

    company = str(quote.get("longname") or quote.get("shortname") or "").strip() or None
    exchange = str(quote.get("exchange") or quote.get("exchDisp") or "").strip() or None
    return ticker, company, exchange


def _ric_score(ric: str, exchange_hint: str | None = None) -> tuple[int, str]:
    upper = ric.upper().strip()
    score = 0
    if upper.endswith(".O"):
        score += 100
    elif upper.endswith(".N"):
        score += 95
    elif upper.endswith(".A"):
        score += 80
    elif upper.endswith(".K"):
        score += 70

    hint = (exchange_hint or "").upper()
    if hint in {"NMS", "NGM", "NCM", "NAS", "NASDAQ"} and upper.endswith(".O"):
        score += 120
    elif hint in {"NYQ", "NYSE"} and upper.endswith(".N"):
        score += 120
    elif hint in {"ASE", "AMEX"} and upper.endswith(".A"):
        score += 120
    elif hint in {"LSE", "LONDON"} and upper.endswith(".L"):
        score += 120

    if any(marker in upper for marker in ("^", "=", "ATMIV", " VOL")):
        score -= 100
    return (score, upper)


def choose_best_ric(rics: Iterable[str], exchange_hint: str | None = None) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for value in rics:
        ric = str(value).strip()
        if ric and ric not in seen:
            seen.add(ric)
            unique.append(ric)
    if not unique:
        raise InstrumentResolutionError("LSEG returned no RIC candidates.")
    return max(unique, key=lambda ric: _ric_score(ric, exchange_hint))


def _extract_frame(response: Any) -> Any:
    if response is None:
        return None
    if hasattr(response, "columns") and hasattr(response, "empty"):
        return response
    data = getattr(response, "data", None)
    frame = getattr(data, "df", None)
    if frame is not None:
        return frame
    return getattr(response, "df", None)


def _frame_ric_values(frame: Any) -> list[str]:
    if frame is None or getattr(frame, "empty", True):
        return []
    columns = {str(column).upper(): column for column in frame.columns}
    ric_column = columns.get("RIC")
    if ric_column is None:
        for upper, original in columns.items():
            if upper.endswith("RIC"):
                ric_column = original
                break
    if ric_column is None:
        return []
    return frame[ric_column].dropna().astype(str).tolist()


def _find_column(frame: Any, *candidates: str) -> Any | None:
    if frame is None:
        return None
    normalized = {re.sub(r"[^a-z0-9]", "", str(column).casefold()): column for column in frame.columns}
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]", "", candidate.casefold())
        if key in normalized:
            return normalized[key]
    return None


def _search_lseg_company(query: str) -> ResolvedInstrument | None:
    """Resolve a natural company name directly with LSEG Search when available."""
    try:
        from lseg.data.discovery import Views, search

        frame = search(
            query=query,
            view=Views.EQUITY_QUOTES,
            select="RIC,DocumentTitle,TickerSymbol,ExchangeCode,AssetState",
            top=10,
        )
    except Exception:
        return None
    if frame is None or getattr(frame, "empty", True):
        return None
    ric_col = _find_column(frame, "RIC")
    title_col = _find_column(frame, "DocumentTitle", "CommonName", "CompanyName")
    ticker_col = _find_column(frame, "TickerSymbol", "Ticker")
    exchange_col = _find_column(frame, "ExchangeCode", "ExchangeName")
    if ric_col is None:
        return None

    q_words = {word for word in re.findall(r"[a-z0-9]+", query.casefold()) if len(word) > 1}
    ranked: list[tuple[int, Any]] = []
    for _, row in frame.iterrows():
        ric = str(row.get(ric_col) or "").strip()
        if not ric:
            continue
        title = str(row.get(title_col) or "") if title_col is not None else ""
        ticker = str(row.get(ticker_col) or "") if ticker_col is not None else ""
        text_words = set(re.findall(r"[a-z0-9]+", f"{title} {ticker}".casefold()))
        score = 10 * len(q_words & text_words)
        if query.casefold() in title.casefold():
            score += 80
        score += _ric_score(ric, str(row.get(exchange_col) or "") if exchange_col is not None else None)[0]
        ranked.append((score, row))
    if not ranked:
        return None
    _, row = max(ranked, key=lambda item: item[0])
    ric = str(row.get(ric_col)).strip()
    ticker = str(row.get(ticker_col) or ric.split(".", 1)[0]).strip().upper() if ticker_col is not None else ric.split(".", 1)[0]
    company = str(row.get(title_col) or "").strip() or None if title_col is not None else None
    exchange = str(row.get(exchange_col) or "").strip() or None if exchange_col is not None else None
    return ResolvedInstrument(
        original=query,
        query=query,
        ticker=ticker,
        ric=ric,
        company_name=company,
        exchange=exchange,
        resolution_source="lseg_search",
    )


@lru_cache(maxsize=512)
def ticker_to_ric(ticker: str, exchange_hint: str | None = None) -> str:
    ticker = _normalise_ticker(ticker)
    if not ticker:
        raise InstrumentResolutionError("No ticker was provided for LSEG conversion.")

    errors: list[str] = []

    try:
        from lseg.data.discovery import SymbolTypes, convert_symbols

        response = convert_symbols(
            symbols=[ticker],
            from_symbol_type=SymbolTypes.TICKER_SYMBOL,
            to_symbol_types=[SymbolTypes.RIC],
            preferred_country_code="USA" if not exchange_hint else None,
        )
        values = _frame_ric_values(_extract_frame(response))
        if values:
            return choose_best_ric(values, exchange_hint)
    except TypeError:
        try:
            from lseg.data.discovery import SymbolTypes, convert_symbols

            response = convert_symbols(
                symbols=[ticker],
                from_symbol_type=SymbolTypes.TICKER_SYMBOL,
                to_symbol_types=[SymbolTypes.RIC],
            )
            values = _frame_ric_values(_extract_frame(response))
            if values:
                return choose_best_ric(values, exchange_hint)
        except Exception as exc:
            errors.append(f"discovery.convert_symbols: {type(exc).__name__}: {exc}")
    except Exception as exc:
        errors.append(f"discovery.convert_symbols: {type(exc).__name__}: {exc}")

    try:
        from lseg.data.content import symbol_conversion

        response = symbol_conversion.Definition(
            symbols=[ticker],
            from_symbol_type=symbol_conversion.SymbolTypes.TICKER_SYMBOL,
            to_symbol_types=[symbol_conversion.SymbolTypes.RIC],
        ).get_data()
        values = _frame_ric_values(_extract_frame(response))
        if values:
            return choose_best_ric(values, exchange_hint)
    except Exception as exc:
        errors.append(f"content.symbol_conversion: {type(exc).__name__}: {exc}")

    detail = "; ".join(errors) or "no RIC rows returned"
    raise InstrumentResolutionError(f"LSEG could not convert ticker {ticker!r}: {detail}")


def _strip_guessed_market_suffix(value: str) -> str:
    upper = value.strip().upper()
    for suffix in (".LON", ".NASDAQ", ".NYSE", ".AMEX", ".US"):
        if upper.endswith(suffix):
            return upper[: -len(suffix)]
    return upper


def resolve_instrument(value: str) -> ResolvedInstrument:
    original = str(value).strip()
    query = extract_security_reference(original)

    company_name: str | None = None
    exchange: str | None = None

    if is_explicit_ticker(query):
        ticker = _strip_guessed_market_suffix(_normalise_ticker(query))
        if "." in ticker and not ticker.endswith((".LON", ".NASDAQ", ".NYSE", ".US")):
            return ResolvedInstrument(
                original=original,
                query=query,
                ticker=ticker.split(".", 1)[0],
                ric=ticker,
                resolution_source="explicit_ric",
            )
    else:
        lseg_match = _search_lseg_company(query)
        if lseg_match is not None:
            return ResolvedInstrument(
                original=original,
                query=query,
                ticker=lseg_match.ticker,
                ric=lseg_match.ric,
                company_name=lseg_match.company_name,
                exchange=lseg_match.exchange,
                resolution_source=lseg_match.resolution_source,
            )
        ticker, company_name, exchange = company_name_to_ticker(query)

    ric = ticker_to_ric(ticker, exchange)
    return ResolvedInstrument(
        original=original,
        query=query,
        ticker=ticker,
        ric=ric,
        company_name=company_name,
        exchange=exchange,
        resolution_source="company_search_then_lseg" if company_name else "lseg_symbol_conversion",
    )
