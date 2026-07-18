"""Translate natural language into a constrained research intent.

The planner does not generate LSEG calls. It selects one predefined workflow
and extracts entities and filters. The deterministic workflow compiler chooses
all API operations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any

from .config import Settings
from .research_workflows import WORKFLOWS, get_workflow, infer_workflow, workflow_context


VALID_TOPICS = {
    "profile", "fundamentals", "profitability", "valuation", "estimates",
    "recommendations", "guidance", "price", "risk", "news", "events",
    "ownership", "insiders", "esg", "filings", "peers", "suppliers", "customers",
}

DEFAULT_TOPICS = [
    "profile", "fundamentals", "profitability", "valuation", "estimates",
    "recommendations", "price", "risk", "news", "events", "guidance",
    "ownership", "insiders", "peers", "filings", "esg",
]

_TOPIC_WORDS = {
    "fundamental": "fundamentals", "fundamentals": "fundamentals", "financials": "fundamentals",
    "margin": "profitability", "margins": "profitability", "profitability": "profitability",
    "valuation": "valuation", "multiple": "valuation", "multiples": "valuation",
    "earnings": "estimates", "estimate": "estimates", "estimates": "estimates",
    "revision": "estimates", "revisions": "estimates", "consensus": "estimates",
    "analyst": "recommendations", "recommendation": "recommendations", "target": "recommendations",
    "guidance": "guidance", "price": "price", "return": "price", "momentum": "price",
    "volatility": "risk", "risk": "risk", "news": "news", "catalyst": "news",
    "event": "events", "events": "events", "ownership": "ownership", "holder": "ownership",
    "holders": "ownership", "insider": "insiders", "insiders": "insiders",
    "esg": "esg", "filing": "filings", "filings": "filings", "10-k": "filings", "10-q": "filings",
    "peer": "peers", "peers": "peers", "competitor": "peers", "competitors": "peers",
    "supplier": "suppliers", "suppliers": "suppliers", "customer": "customers", "customers": "customers",
}

_SECTOR_NAMES = (
    "Energy", "Basic Materials", "Industrials", "Consumer Cyclicals",
    "Consumer Non-Cyclicals", "Financials", "Healthcare", "Technology",
    "Telecommunications Services", "Utilities", "Real Estate",
)

# Natural-language aliases are normalized before any LSEG screen is built.
# This prevents inputs such as "industrial sector" from becoming the invalid
# literal screen value "industrial".
_SECTOR_ALIASES: dict[str, str] = {
    "energy": "Energy",
    "oil and gas": "Energy",
    "oil & gas": "Energy",
    "materials": "Basic Materials",
    "material": "Basic Materials",
    "basic materials": "Basic Materials",
    "industrials": "Industrials",
    "industrial": "Industrials",
    "industrial sector": "Industrials",
    "consumer discretionary": "Consumer Cyclicals",
    "consumer cyclicals": "Consumer Cyclicals",
    "consumer cyclical": "Consumer Cyclicals",
    "consumer staples": "Consumer Non-Cyclicals",
    "consumer non-cyclicals": "Consumer Non-Cyclicals",
    "consumer non-cyclical": "Consumer Non-Cyclicals",
    "financials": "Financials",
    "financial": "Financials",
    "healthcare": "Healthcare",
    "health care": "Healthcare",
    "technology": "Technology",
    "tech": "Technology",
    "information technology": "Technology",
    "telecommunications services": "Telecommunications Services",
    "telecommunications": "Telecommunications Services",
    "telecom": "Telecommunications Services",
    "communication services": "Telecommunications Services",
    "utilities": "Utilities",
    "utility": "Utilities",
    "real estate": "Real Estate",
    "reit": "Real Estate",
    "reits": "Real Estate",
}


def canonicalize_sector(value: str | None) -> str | None:
    """Map user/LLM sector wording to the exact LSEG TRBC sector name."""
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value).strip().casefold())
    cleaned = re.sub(r"\bsector\b$", "", cleaned).strip()
    if not cleaned:
        return None
    return _SECTOR_ALIASES.get(cleaned)


def detect_sector(text: str) -> str | None:
    """Find a supported sector phrase in free text, preferring longer aliases."""
    lower = re.sub(r"\s+", " ", text.casefold())
    for alias in sorted(_SECTOR_ALIASES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lower):
            return _SECTOR_ALIASES[alias]
    return None

_COUNTRY_WORDS = {
    "us": "US", "u.s.": "US", "united states": "US", "american": "US",
    "uk": "GB", "u.k.": "GB", "united kingdom": "GB", "british": "GB",
    "canada": "CA", "canadian": "CA", "germany": "DE", "german": "DE",
    "france": "FR", "french": "FR", "japan": "JP", "japanese": "JP",
    "china": "CN", "chinese": "CN", "india": "IN", "indian": "IN",
}


@dataclass
class ScreenFilters:
    market_cap_min: float | None = None
    market_cap_max: float | None = None
    pe_max: float | None = None
    forward_pe_max: float | None = None
    ev_ebitda_max: float | None = None
    dividend_yield_min: float | None = None
    total_return_3m_min: float | None = None
    country_code: str | None = None
    sector: str | None = None
    universe: str | None = None
    limit: int = 15
    sort_by: str = "market_cap"
    candidate_search: bool = False


@dataclass
class ResearchPlan:
    mode: str = "company"
    workflow: str | None = None
    entities: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=lambda: list(DEFAULT_TOPICS))
    lookback_days: int = 365
    investment_horizon: str = "medium_term"
    screen: ScreenFilters = field(default_factory=ScreenFilters)
    raw_request: str = ""
    planner: str = "deterministic"

    def normalized(self) -> "ResearchPlan":
        self.mode = self.mode if self.mode in {"company", "compare", "screen", "market_news"} else "company"
        self.entities = [str(item).strip() for item in self.entities if str(item).strip()][:8]
        self.topics = [topic for topic in dict.fromkeys(self.topics) if topic in VALID_TOPICS]
        if not self.topics and self.mode not in {"screen", "market_news"}:
            self.topics = list(DEFAULT_TOPICS)
        self.lookback_days = max(7, min(int(self.lookback_days or 365), 1825))
        if self.screen.sector:
            raw_sector = self.screen.sector
            self.screen.sector = canonicalize_sector(raw_sector)
            if self.screen.sector is None:
                raise ValueError(
                    f"Unsupported sector wording: {raw_sector!r}. "
                    f"Supported TRBC sectors: {', '.join(_SECTOR_NAMES)}."
                )
        self.screen.limit = max(3, min(int(self.screen.limit or 15), 50))
        self.investment_horizon = self.investment_horizon if self.investment_horizon in {"short_term", "medium_term", "long_term"} else "medium_term"
        if self.screen.candidate_search:
            self.screen.sort_by = "quality_value"
            if self.screen.limit == 15:
                self.screen.limit = 8
        self.workflow = get_workflow(self.workflow, self.mode, candidate_search=self.screen.candidate_search).workflow_id
        self.mode = WORKFLOWS[self.workflow].mode
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number_with_scale(text: str) -> float | None:
    match = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*(trillion|billion|million|tn|t|bn|b|m)?", text, re.I)
    if not match:
        return None
    value = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    multiplier = {
        "trillion": 1e12, "tn": 1e12, "t": 1e12,
        "billion": 1e9, "bn": 1e9, "b": 1e9,
        "million": 1e6, "m": 1e6,
    }.get(suffix, 1.0)
    return value * multiplier


def _extract_lookback(text: str) -> int:
    lower = text.casefold()
    match = re.search(r"(?:last|past|over)\s+(\d+)\s*(day|week|month|year)s?", lower)
    if match:
        n = int(match.group(1))
        return n * {"day": 1, "week": 7, "month": 30, "year": 365}[match.group(2)]
    if "today" in lower:
        return 7
    if "this week" in lower:
        return 14
    if "this month" in lower:
        return 30
    if "last quarter" in lower:
        return 120
    return 365


def _strip_topic_tail(value: str) -> str:
    value = re.sub(r"\b(?:using|via|with)\s+(?:lseg|refinitiv|workspace)\b.*$", "", value, flags=re.I)
    value = re.sub(
        r"\b(?:and\s+)?(?:its\s+)?(?:valuation|fundamentals?|financials?|earnings|estimates?|revisions?|news|peers?|competitors?|ownership|insiders?|esg|filings?|guidance|price|momentum|risk|volatility|suppliers?|customers?|events?|catalysts?)(?:\s*,?\s*(?:and\s+)?(?:its\s+)?(?:valuation|fundamentals?|financials?|earnings|estimates?|revisions?|news|peers?|competitors?|ownership|insiders?|esg|filings?|guidance|price|momentum|risk|volatility|suppliers?|customers?|events?|catalysts?))*\s*$",
        "", value, flags=re.I,
    )
    return value.strip(" ,.;:")


def _extract_entities(text: str, mode: str) -> list[str]:
    if mode in {"screen", "market_news"}:
        return []
    cleaned = text.strip()
    if mode == "compare":
        match = re.search(r"\bcompare\s+(.+?)\s+(?:vs\.?|versus|with|and)\s+(.+?)(?:\s+(?:on|across|for|using|via)\b|$)", cleaned, re.I)
        if match:
            return [_strip_topic_tail(match.group(1)), _strip_topic_tail(match.group(2))]
    match = re.search(
        r"\b(?:analy[sz]e|research|review|investigate|evaluate|look\s+up|tell\s+me\s+about|show|find|deep\s+dive\s+(?:on|into)?)\s+(.+)$",
        cleaned, re.I,
    )
    if match:
        subject = _strip_topic_tail(match.group(1))
        if subject:
            return [re.sub(r"['’]s$", "", subject, flags=re.I).strip()]
    return []


def _country_code(lower: str) -> str | None:
    for phrase, code in sorted(_COUNTRY_WORDS.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", lower):
            return code
    return None


def _deterministic_plan(text: str) -> ResearchPlan:
    lower = text.casefold()
    detected_sector = detect_sector(lower)
    opportunity_words = re.search(
        r"\b(good|best|strong|attractive|promising|undervalued|bargain|cheap|value|investment|investable|buy|pick|candidate|opportunity)\b",
        lower,
    )
    unspecified_sector_company = bool(
        detected_sector
        and (
            opportunity_words
            or re.search(r"\b(compan(?:y|ies)|stocks?|equities|investment)\b", lower)
            or re.search(r"\b(?:a|an|one|some)\s+(?:[a-z]+\s+){0,3}(?:company|stock)\b", lower)
        )
    )

    if unspecified_sector_company:
        mode, workflow = "screen", "sector_opportunity"
    elif re.search(r"\bcompare\b|\bversus\b|\bvs\.?\b", lower):
        mode, workflow = "compare", "company_compare"
    elif re.search(r"\bmarket news\b|\btop news\b|\bwhat(?:'s| is) happening\b", lower):
        mode, workflow = "market_news", "market_news"
    elif re.search(r"\b(screen|screener|find|show me|list)\b", lower) and re.search(r"\b(stocks?|companies|equities)\b", lower):
        mode, workflow = "screen", "stock_screen"
    else:
        mode, workflow = "company", "company_deep_dive"

    topics = [topic for word, topic in _TOPIC_WORDS.items() if re.search(rf"\b{re.escape(word)}\b", lower)]
    if not topics and mode in {"company", "compare"}:
        topics = list(DEFAULT_TOPICS)

    screen = ScreenFilters(sector=detected_sector, country_code=_country_code(lower))
    if mode == "screen":
        market_cap = re.search(r"market\s+cap(?:italization)?\s*(?:over|above|>=|greater than|at least)\s*([^,;]+)", lower)
        if not market_cap:
            market_cap = re.search(r"(?:over|above|>=|greater than|at least)\s*(\$?\s*\d+(?:\.\d+)?\s*(?:trillion|billion|million|tn|t|bn|b|m)?)\s+market\s+cap", lower)
        if market_cap:
            screen.market_cap_min = _number_with_scale(market_cap.group(1))
        fpe = re.search(r"(?:forward|fwd)\s+(?:p/?e|pe)\s*(?:under|below|<=|less than)\s*(\d+(?:\.\d+)?)", lower)
        if fpe:
            screen.forward_pe_max = float(fpe.group(1))
        pe_text = re.sub(r"(?:forward|fwd)\s+(?:p/?e|pe)\s*(?:under|below|<=|less than)\s*\d+(?:\.\d+)?", "", lower)
        pe = re.search(r"(?:p/?e|pe)\s*(?:under|below|<=|less than)\s*(\d+(?:\.\d+)?)", pe_text)
        if pe:
            screen.pe_max = float(pe.group(1))
        limit = re.search(r"\b(?:top|show|find|list)\s+(\d+)\b", lower)
        if limit:
            screen.limit = int(limit.group(1))
        screen.candidate_search = workflow == "sector_opportunity"

    horizon = "medium_term"
    if re.search(r"\b(short[- ]term|next few weeks|next quarter)\b", lower):
        horizon = "short_term"
    elif re.search(r"\b(long[- ]term|multi[- ]year|years)\b", lower):
        horizon = "long_term"

    return ResearchPlan(
        mode=mode,
        workflow=workflow,
        entities=_extract_entities(text, mode),
        topics=topics,
        lookback_days=_extract_lookback(text),
        investment_horizon=horizon,
        screen=screen,
        raw_request=text,
        planner="deterministic",
    ).normalized()


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Planner did not return JSON.")
    return json.loads(text[start : end + 1])


def _llm_plan(text: str, settings: Settings) -> ResearchPlan:
    from langchain_groq import ChatGroq

    llm = ChatGroq(model=settings.groq_model, temperature=0, max_retries=2, api_key=settings.groq_api_key)
    response = llm.invoke(
        [
            (
                "system",
                "Classify the request into one predefined research workflow and extract only user constraints. "
                "Return JSON only. Never choose a company or ticker the user did not name. Never generate LSEG fields, functions, code, or API calls. "
                "Use sector_opportunity when the user asks for a promising, good, or investable company in a sector.\n\n"
                "Allowed workflows:\n" + workflow_context() + "\n\n"
                "JSON keys: workflow, entities, sector, country_code, market_cap_min, market_cap_max, pe_max, forward_pe_max, "
                "ev_ebitda_max, dividend_yield_min, total_return_3m_min, limit, lookback_days, investment_horizon. "
                "investment_horizon must be short_term, medium_term, or long_term.",
            ),
            ("human", text),
        ]
    )
    payload = _extract_json(str(getattr(response, "content", response)))
    workflow = str(payload.get("workflow") or "company_deep_dive")
    if workflow not in WORKFLOWS:
        raise ValueError("Unknown workflow.")
    mode = WORKFLOWS[workflow].mode
    screen = ScreenFilters(
        market_cap_min=payload.get("market_cap_min"),
        market_cap_max=payload.get("market_cap_max"),
        pe_max=payload.get("pe_max"),
        forward_pe_max=payload.get("forward_pe_max"),
        ev_ebitda_max=payload.get("ev_ebitda_max"),
        dividend_yield_min=payload.get("dividend_yield_min"),
        total_return_3m_min=payload.get("total_return_3m_min"),
        country_code=payload.get("country_code"),
        sector=payload.get("sector"),
        limit=payload.get("limit") or 15,
        candidate_search=workflow == "sector_opportunity",
    )
    return ResearchPlan(
        mode=mode,
        workflow=workflow,
        entities=list(payload.get("entities") or []),
        topics=list(DEFAULT_TOPICS),
        lookback_days=int(payload.get("lookback_days") or 365),
        investment_horizon=str(payload.get("investment_horizon") or "medium_term"),
        screen=screen,
        raw_request=text,
        planner="groq_intent_only",
    ).normalized()


def build_research_plan(text: str, settings: Settings) -> ResearchPlan:
    fallback = _deterministic_plan(text)
    if not settings.groq_api_key:
        return fallback
    try:
        plan = _llm_plan(text, settings)
        # The deterministic parser is the safety authority for unspecified sector
        # opportunity requests. It prevents the LLM from inventing a company.
        if fallback.workflow == "sector_opportunity":
            return fallback
        if plan.mode in {"company", "compare"} and not plan.entities:
            plan.entities = fallback.entities
        if plan.mode == "screen":
            for key in ScreenFilters.__dataclass_fields__:
                value = getattr(plan.screen, key)
                if value in {None, ""}:
                    setattr(plan.screen, key, getattr(fallback.screen, key))
        return plan.normalized()
    except Exception:
        return fallback
