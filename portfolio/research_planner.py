"""Translate natural language into a constrained research intent.

The planner does not generate LSEG calls. It selects one predefined workflow
and extracts entities and filters. The deterministic workflow compiler chooses
all API operations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import re
from typing import Any, Callable

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


class UnsupportedResearchConstraint(ValueError):
    """The request contains a constraint that cannot be compiled safely."""


@dataclass(frozen=True)
class TRBCClassification:
    """A curated natural-language alias for one exact TRBC hierarchy node."""

    label: str
    parent_sector: str
    code_field: str
    codes: tuple[str, ...]
    aliases: tuple[str, ...]


# LSEG Screener supports filters at each TRBC hierarchy level. These entries
# deliberately use exact code fields observed through Workspace rather than
# treating industries as economic sectors or relying on fuzzy text matching.
TRBC_CLASSIFICATIONS: tuple[TRBCClassification, ...] = (
    TRBCClassification(
        "Biotechnology & Medical Research", "Healthcare", "TR.TRBCIndustryCode", ("56202010",),
        ("biotechnology", "biotech", "biotechnology and medical research", "biotechnology & medical research"),
    ),
    TRBCClassification(
        "Pharmaceuticals", "Healthcare", "TR.TRBCIndustryGroupCode", ("562010",),
        ("pharmaceuticals", "pharmaceutical", "pharma"),
    ),
    TRBCClassification(
        "Medical Equipment & Supplies", "Healthcare", "TR.TRBCIndustryGroupCode", ("561010",),
        ("medical equipment", "medical devices", "medical device", "healthcare equipment"),
    ),
    TRBCClassification(
        "Semiconductors", "Technology", "TR.TRBCIndustryCode", ("57101010",),
        ("semiconductors", "semiconductor", "chips", "chip", "chipmakers", "chipmaker", "chip makers"),
    ),
    TRBCClassification(
        "Semiconductor Equipment", "Technology", "TR.TRBCIndustryCode", ("57101020",),
        ("semiconductor equipment", "chip equipment", "semiconductor equipment makers"),
    ),
    TRBCClassification(
        "Semiconductors & Semiconductor Equipment", "Technology", "TR.TRBCIndustryGroupCode", ("571010",),
        ("semiconductors and semiconductor equipment", "semiconductors & semiconductor equipment"),
    ),
    TRBCClassification(
        "Software", "Technology", "TR.TRBCIndustryCode", ("57201020",),
        ("software", "software companies", "software stocks"),
    ),
    TRBCClassification(
        "Aerospace & Defense", "Industrials", "TR.TRBCIndustryCode", ("52101010",),
        ("aerospace and defense", "aerospace & defense", "aerospace", "defense", "defense contractor", "a&d"),
    ),
    TRBCClassification(
        "Banks", "Financials", "TR.TRBCIndustryCode", ("55101010",),
        ("banks", "bank", "banking", "bank stocks"),
    ),
    TRBCClassification(
        "Insurance", "Financials", "TR.TRBCBusinessSectorCode", ("5530",),
        ("insurance", "insurers", "insurance companies"),
    ),
    TRBCClassification(
        "Automobiles & Auto Parts", "Consumer Cyclicals", "TR.TRBCIndustryGroupCode", ("531010",),
        ("automobiles", "automotive", "automakers", "automaker", "auto manufacturers", "auto makers"),
    ),
    TRBCClassification(
        "Oil & Gas", "Energy", "TR.TRBCIndustryGroupCode", ("501020",),
        ("oil and gas", "oil & gas", "oil gas"),
    ),
)

_INDUSTRY_ALIASES: dict[str, TRBCClassification] = {
    alias.casefold(): definition
    for definition in TRBC_CLASSIFICATIONS
    for alias in (definition.label, *definition.aliases)
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


def classification_definition(value: str | None) -> TRBCClassification | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value).strip().casefold())
    cleaned = re.sub(r"\b(?:sector|industry|space)\b$", "", cleaned).strip()
    return _INDUSTRY_ALIASES.get(cleaned)


def canonicalize_industry(value: str | None) -> str | None:
    definition = classification_definition(value)
    return definition.label if definition is not None else None


def detect_sector(text: str) -> str | None:
    """Find a supported sector phrase in free text, preferring longer aliases."""
    lower = re.sub(r"\s+", " ", text.casefold())
    for alias in sorted(_SECTOR_ALIASES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lower):
            return _SECTOR_ALIASES[alias]
    return None


def detect_industry(text: str) -> str | None:
    """Find a supported lower-level TRBC classification in free text."""
    lower = re.sub(r"\s+", " ", text.casefold())
    for alias in sorted(_INDUSTRY_ALIASES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lower):
            return _INDUSTRY_ALIASES[alias].label
    return None


def _detected_values(text: str, aliases: dict[str, Any], value: Callable[[Any], str]) -> list[str]:
    lower = re.sub(r"\s+", " ", text.casefold())

    def contextual(alias: str) -> bool:
        escaped = re.escape(alias)
        patterns = (
            rf"(?<![a-z]){escaped}(?![a-z])(?:\s+|-)(?:economic\s+)?(?:sector|industry|space|stocks?|compan(?:y|ies)|equities)\b",
            rf"\b(?:sector|industry|space)\s+(?:for|of|in)?\s*(?<![a-z]){escaped}(?![a-z])",
            rf"\b(?:in|within|among|from)\s+(?:the\s+)?(?<![a-z]){escaped}(?![a-z])",
            rf"\b(?:find|screen|list|show|research|study|examine|assess|evaluate)\b[^.;,]{{0,45}}(?<![a-z]){escaped}(?![a-z])\s*$",
        )
        return any(re.search(pattern, lower) for pattern in patterns)

    found = {
        value(target)
        for alias, target in aliases.items()
        if contextual(alias)
    }
    return sorted(found)

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
    industry: str | None = None
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
    context_parent_request: str | None = None

    def normalized(self) -> "ResearchPlan":
        self.mode = self.mode if self.mode in {"company", "compare", "screen", "market_news"} else "company"
        if not isinstance(self.entities, list) or not all(isinstance(item, str) for item in self.entities):
            raise UnsupportedResearchConstraint("Research entities must be a list of strings.")
        self.entities = [item.strip() for item in self.entities if item.strip()][:8]
        self.topics = [topic for topic in dict.fromkeys(self.topics) if topic in VALID_TOPICS]
        if not self.topics and self.mode not in {"screen", "market_news"}:
            self.topics = list(DEFAULT_TOPICS)
        try:
            self.lookback_days = int(self.lookback_days or 365)
        except (TypeError, ValueError) as exc:
            raise UnsupportedResearchConstraint("Research lookback must be an integer number of days.") from exc
        if not 7 <= self.lookback_days <= 1825:
            raise UnsupportedResearchConstraint("Research lookback must be between 7 and 1,825 days.")
        if self.screen.sector:
            raw_sector = self.screen.sector
            self.screen.sector = canonicalize_sector(raw_sector)
            if self.screen.sector is None:
                implied_industry = canonicalize_industry(raw_sector)
                if implied_industry and not self.screen.industry:
                    self.screen.industry = implied_industry
                else:
                    raise UnsupportedResearchConstraint(
                        f"Unsupported sector wording: {raw_sector!r}. "
                        f"Supported TRBC economic sectors: {', '.join(_SECTOR_NAMES)}."
                    )
        if self.screen.industry:
            raw_industry = self.screen.industry
            definition = classification_definition(raw_industry)
            if definition is None:
                raise UnsupportedResearchConstraint(
                    f"Unsupported industry wording: {raw_industry!r}. The request was not broadened."
                )
            if self.screen.sector and self.screen.sector != definition.parent_sector:
                raise UnsupportedResearchConstraint(
                    f"Conflicting TRBC constraints: {self.screen.sector!r} does not contain "
                    f"{definition.label!r} ({definition.parent_sector})."
                )
            self.screen.industry = definition.label
            self.screen.sector = definition.parent_sector
        numeric_fields = (
            "market_cap_min", "market_cap_max", "pe_max", "forward_pe_max",
            "ev_ebitda_max", "dividend_yield_min", "total_return_3m_min",
        )
        for field_name in numeric_fields:
            value = getattr(self.screen, field_name)
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise UnsupportedResearchConstraint(f"{field_name} must be numeric.") from exc
            if not math.isfinite(number):
                raise UnsupportedResearchConstraint(f"{field_name} must be finite.")
            setattr(self.screen, field_name, number)
        if self.screen.country_code:
            country = str(self.screen.country_code).strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", country):
                raise UnsupportedResearchConstraint("Headquarters country must be a two-letter ISO code.")
            if country not in set(_COUNTRY_WORDS.values()):
                raise UnsupportedResearchConstraint(
                    f"Headquarters country {country!r} is not in the deterministic country catalog."
                )
            self.screen.country_code = country
        if self.screen.market_cap_min is not None and self.screen.market_cap_min < 0:
            raise UnsupportedResearchConstraint("Minimum market capitalization cannot be negative.")
        if self.screen.market_cap_max is not None and self.screen.market_cap_max < 0:
            raise UnsupportedResearchConstraint("Maximum market capitalization cannot be negative.")
        for field_name in ("pe_max", "forward_pe_max", "ev_ebitda_max"):
            value = getattr(self.screen, field_name)
            if value is not None and value <= 0:
                raise UnsupportedResearchConstraint(f"{field_name} must be positive.")
        if self.screen.dividend_yield_min is not None and self.screen.dividend_yield_min < 0:
            raise UnsupportedResearchConstraint("Minimum dividend yield cannot be negative.")
        if (
            self.screen.market_cap_min is not None
            and self.screen.market_cap_max is not None
            and self.screen.market_cap_min > self.screen.market_cap_max
        ):
            raise UnsupportedResearchConstraint("Minimum market capitalization exceeds the maximum.")
        if isinstance(self.screen.limit, bool) or not re.fullmatch(r"[+-]?\d+", str(self.screen.limit).strip()):
            raise UnsupportedResearchConstraint("Screen result limit must be an integer.")
        self.screen.limit = int(self.screen.limit)
        if not 1 <= self.screen.limit <= 50:
            raise UnsupportedResearchConstraint("Screen result limit must be between 1 and 50.")
        if self.screen.sort_by not in {"market_cap", "pe", "forward_pe", "ev_ebitda", "return", "quality_value"}:
            raise UnsupportedResearchConstraint(f"Unsupported screen sort key: {self.screen.sort_by!r}.")
        if self.screen.universe and not re.fullmatch(r"[A-Za-z0-9#.^=_-]{1,64}", str(self.screen.universe)):
            raise UnsupportedResearchConstraint("The requested chain/universe identifier is malformed.")
        if self.investment_horizon not in {"short_term", "medium_term", "long_term"}:
            raise UnsupportedResearchConstraint(
                "Investment horizon must be short_term, medium_term, or long_term."
            )
        if self.screen.candidate_search:
            self.screen.sort_by = "quality_value"
            if self.screen.limit == 15:
                self.screen.limit = 8
        self.workflow = get_workflow(self.workflow, self.mode, candidate_search=self.screen.candidate_search).workflow_id
        self.mode = WORKFLOWS[self.workflow].mode
        if self.workflow == "company_deep_dive":
            if len(self.entities) != 1:
                raise UnsupportedResearchConstraint("A company deep dive requires exactly one named company, ticker, or RIC.")
            if re.match(r"^(?:a|an|some|any)\b", self.entities[0], re.I):
                raise UnsupportedResearchConstraint(
                    "A generic company description cannot be resolved as a named security. Specify a supported industry or a company name."
                )
        elif self.workflow == "company_compare":
            if not 2 <= len(self.entities) <= 8:
                raise UnsupportedResearchConstraint("A company comparison requires between two and eight named companies.")
            if any(entity.casefold() in {"it", "this", "that", "the company", "the stock"} for entity in self.entities):
                raise UnsupportedResearchConstraint("A comparison cannot resolve a pronoun without explicit prior-company routing.")
        elif self.workflow == "sector_opportunity":
            if self.entities:
                raise UnsupportedResearchConstraint("A sector opportunity request cannot also name a company.")
            if not (self.screen.sector or self.screen.industry):
                raise UnsupportedResearchConstraint("A sector opportunity request requires a supported TRBC classification.")
        elif self.workflow == "stock_screen" and self.entities:
            raise UnsupportedResearchConstraint("A stock screen cannot also contain named-company entities.")
        return self

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["effective_request"] = self.effective_request
        return payload

    @property
    def effective_request(self) -> str:
        """Return the current turn plus inherited intent for policy checks."""
        return " ".join(
            part.strip()
            for part in (self.context_parent_request, self.raw_request)
            if isinstance(part, str) and part.strip()
        )


_SCALED_NUMBER_PATTERN = (
    r"\$?\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*"
    r"(?:trillion|billion|million|tn|bn|[tbm])?(?![a-z])"
)


def _number_with_scale(text: str) -> float | None:
    match = re.fullmatch(
        r"\s*\$?\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
        r"(trillion|billion|million|tn|bn|[tbm])?\s*",
        text,
        re.I,
    )
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
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
    value = re.sub(
        r"\s+(?:as|for)\s+(?:a|an)?\s*(?:good|promising|potential|possible)?\s*(?:investment|buy|pick|candidate|bargain|value)\s*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s+(?:stock|shares?|company)\s*$", "", value, flags=re.I)
    value = re.sub(
        r"\s+(?:on|across|for)\s+(?:its\s+)?(?:valuation|fundamentals?|financials?|profitability|margins?|earnings|estimates?|"
        r"revisions?|news|peers?|competitors?|ownership|insiders?|esg|filings?|guidance|price|momentum|"
        r"risk|volatility|suppliers?|customers?|events?|catalysts?)(?:\s*,?\s*(?:and\s+)?(?:its\s+)?"
        r"(?:valuation|fundamentals?|financials?|profitability|margins?|earnings|estimates?|revisions?|news|peers?|competitors?|"
        r"ownership|insiders?|esg|filings?|guidance|price|momentum|risk|volatility|suppliers?|customers?|"
        r"events?|catalysts?))*\s*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b(?:using|via|with)\s+(?:lseg|refinitiv|workspace)\b.*$", "", value, flags=re.I)
    value = re.sub(
        r"\b(?:and\s+)?(?:its\s+)?(?:valuation|fundamentals?|financials?|profitability|margins?|earnings|estimates?|revisions?|news|peers?|competitors?|ownership|insiders?|esg|filings?|guidance|price|momentum|risk|volatility|suppliers?|customers?|events?|catalysts?)(?:\s*,?\s*(?:and\s+)?(?:its\s+)?(?:valuation|fundamentals?|financials?|profitability|margins?|earnings|estimates?|revisions?|news|peers?|competitors?|ownership|insiders?|esg|filings?|guidance|price|momentum|risk|volatility|suppliers?|customers?|events?|catalysts?))*\s*$",
        "", value, flags=re.I,
    )
    return value.strip(" ,.;:")


def _named_security_subject(text: str) -> str | None:
    """Return an explicitly named company/ticker before parsing taxonomy words."""
    match = re.search(
        r"^\s*(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
        r"(?:analy[sz]e|research|study|examine|assess|review|investigate|evaluate|find|look\s+up)\s+"
        r"(.+?)\s+(?:stock|shares?)\s*[?.!]*$",
        text,
        re.I,
    )
    if not match:
        return None
    subject = match.group(1).strip(" ,.;:")
    lower = subject.casefold()
    if not subject or re.match(
        r"^(?:a|an|one|some|any)\b|"
        r"\b(?:promising|undervalued|cheap|bargain|best|good|strong|attractive|value|candidate|opportunity)\b",
        lower,
    ):
        return None
    if canonicalize_sector(subject) or classification_definition(subject):
        return None
    country_subjects = {
        "us", "u.s.", "united states", "american", "uk", "u.k.", "united kingdom",
        "british", "canada", "canadian", "germany", "german", "france", "french",
        "japan", "japanese", "china", "chinese", "india", "indian",
    }
    if lower in country_subjects:
        return None
    if lower in {
        "quantum computing", "robotics", "cybersecurity", "fintech", "renewable energy",
        "clean energy", "investment bank", "investment banks", "regional bank",
        "regional banks", "mining", "steel",
    }:
        return None
    return subject


def _extract_entities(text: str, mode: str) -> list[str]:
    if mode in {"screen", "market_news"}:
        return []
    cleaned = text.strip()
    named_as = re.search(
        r"\b(?:analy[sz]e|research|study|examine|assess|review|investigate|evaluate)\s+"
        r"(?:a|an)\s+.+?\s+named\s+(.+)$",
        cleaned,
        re.I,
    )
    if named_as:
        return [_strip_topic_tail(named_as.group(1))]
    qualified = re.search(
        r"\b(?:analy[sz]e|research|study|examine|assess|review|investigate|evaluate)\s+"
        r"([^,]+),\s+(?:a|an)\s+.+?\s+company\b",
        cleaned,
        re.I,
    )
    if qualified:
        return [_strip_topic_tail(qualified.group(1))]
    if mode == "compare":
        match = re.search(r"\b(?:compare|evaluate|review)\s+(.+)$", cleaned, re.I)
        if match:
            body = _strip_topic_tail(match.group(1))
            contrasted = re.split(r"\s+(?:vs\.?|versus|with)\s+", body, maxsplit=1, flags=re.I)
            if len(contrasted) == 2:
                first = contrasted[0].strip(" ,")
                others = [
                    item.strip(" ,")
                    for item in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", contrasted[1], flags=re.I)
                    if item.strip(" ,")
                ]
                return [first, *others]
            comma_parts = [
                item.strip(" ,")
                for item in re.split(r"\s*,\s*(?:and\s+)?", body, flags=re.I)
                if item.strip(" ,")
            ]
            if len(comma_parts) >= 2:
                return comma_parts
            and_parts = [item.strip() for item in re.split(r"\s+and\s+", body, flags=re.I) if item.strip()]
            if len(and_parts) == 2:
                return and_parts
    match = re.search(
        r"\b(?:analy[sz]e|research|study|examine|assess|review|investigate|evaluate|look\s+up|"
        r"tell\s+me\s+about|show|find|deep\s+dive\s+(?:on|into)?)\s+(.+)$",
        cleaned, re.I,
    )
    if match:
        subject = _strip_topic_tail(match.group(1))
        subject = re.sub(r"\s+(?:in|within)\s+(?:the\s+)?[a-z0-9 &/+-]+\s+(?:sector|industry|space)\s*$", "", subject, flags=re.I)
        taxonomy_terms = "|".join(
            re.escape(item) for item in sorted((*_SECTOR_ALIASES, *_INDUSTRY_ALIASES), key=len, reverse=True)
        )
        subject = re.sub(rf"^(?:{taxonomy_terms})\s+company\s+", "", subject, flags=re.I)
        subject = re.sub(rf"\s+(?:{taxonomy_terms})\s+company$", "", subject, flags=re.I)
        if subject:
            return [re.sub(r"['’]s$", "", subject, flags=re.I).strip()]
    return []


def _country_code(lower: str) -> str | None:
    for phrase, code in sorted(_COUNTRY_WORDS.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", lower):
            return code
    return None


def _validate_request_constraints(text: str) -> tuple[str | None, str | None, str | None]:
    """Detect ambiguity or unsupported constraints before any LSEG call."""
    lower = re.sub(r"\s+", " ", text.casefold())
    sectors = _detected_values(lower, _SECTOR_ALIASES, str)
    industries = _detected_values(lower, _INDUSTRY_ALIASES, lambda item: item.label)
    countries: list[str] = []
    taxonomy_terms = "|".join(
        re.escape(item)
        for item in sorted((*_SECTOR_ALIASES, *_INDUSTRY_ALIASES), key=len, reverse=True)
    )
    country_terms = "|".join(
        re.escape(item)
        for item in sorted(
            (item for item in _COUNTRY_WORDS if item not in {"us", "u.s."}),
            key=len,
            reverse=True,
        )
    )
    for phrase, code in _COUNTRY_WORDS.items():
        if phrase == "us":
            uppercase_us = bool(re.search(r"(?<![A-Za-z])US(?![A-Za-z])", text))
            explicit_lowercase_us = bool(
                re.search(
                    rf"(?:^|\b(?:study|research|screen|analy[sz]e|review|investigate|examine|assess|evaluate|find|list|"
                    rf"focus\s+on|narrow\s+to|filter\s+(?:for|to)|what\s+about)\s+)"
                    rf"(?:(?:only|just|all)\s+)?(?:the\s+)?us\s+"
                    rf"(?:(?:{taxonomy_terms})\s+)?(?:stocks?|companies|equities)\b|"
                    rf"(?:^|\b(?:study|research|screen|analy[sz]e|review|investigate|examine|assess|evaluate|find|list)\s+)"
                    rf"(?:(?:only|just|all)\s+)?(?:the\s+)?us\s+and\s+(?:{country_terms})\s+"
                    rf"(?:(?:{taxonomy_terms})\s+)?(?:stocks?|companies|equities)\b|"
                    r"\b(?:stocks?|companies|equities)\s+(?:in|from|headquartered\s+in)\s+(?:the\s+)?us\b",
                    lower,
                )
            )
            if not uppercase_us and not explicit_lowercase_us:
                # In phrases such as "tell us about biotech stocks", "us" is
                # a pronoun. Only explicit stock-geography grammar maps the
                # lowercase token to the United States.
                continue
        escaped = re.escape(phrase)
        contextual = (
            rf"(?<![a-z]){escaped}(?![a-z])[^.;,]{{0,40}}\b(?:stocks?|companies|equities)\b|"
            rf"\b(?:stocks?|companies|equities)\b[^.;,]{{0,40}}\b(?:in|from|headquartered\s+in)\s+(?:the\s+)?"
            rf"(?<![a-z]){escaped}(?![a-z])"
        )
        if re.search(contextual, lower):
            countries.append(code)
    countries = sorted(set(countries))

    if re.search(r"\b(?:incorporated|domiciled|operating|doing business|with operations?)\s+in\s+", lower):
        raise UnsupportedResearchConstraint(
            "Incorporation, domicile, and operating geography are not equivalent to headquarters country."
        )
    if re.search(r"\b(?:nasdaq|nyse|amex|lse|tsx|euronext)[ -]?(?:listed|traded)?\b", lower):
        raise UnsupportedResearchConstraint(
            "Exchange/listing constraints are not compiled by this headquarters-country screen."
        )
    if re.search(r"\b(?:s&p\s*500|russell\s*\d+|nasdaq\s*100|dow\s+jones)\b", lower):
        raise UnsupportedResearchConstraint(
            "Named-index membership must be supplied as an explicit supported LSEG chain and was not ignored."
        )
    if re.search(r"\b(?:north american|latin american|asia[- ]pacific|emea)\b", lower):
        raise UnsupportedResearchConstraint(
            "Regional geography is not equivalent to one headquarters-country code."
        )
    if re.search(r"\b(?:large|mid|small)[ -]cap\b[^.;,]{0,35}\b(?:stocks?|companies|equities)\b", lower):
        raise UnsupportedResearchConstraint(
            "Large-, mid-, and small-cap labels require an explicit numeric market-cap threshold and were not ignored."
        )
    unsupported_taxonomy = re.search(
        r"\b(quantum computing|robotics|cybersecurity|fintech|renewable energy|clean energy|"
        r"investment banks?|regional banks?|mining|steel)\b[^.;,]{0,30}\b(?:stocks?|companies|equities|sector|industry)\b",
        lower,
    )
    if unsupported_taxonomy:
        raise UnsupportedResearchConstraint(
            f"The requested classification {unsupported_taxonomy.group(1)!r} is not in the deterministic TRBC catalog."
        )

    country_terms = "|".join(re.escape(item) for item in sorted(_COUNTRY_WORDS, key=len, reverse=True))
    if re.search(
        rf"(?<![a-z])(?:{country_terms})(?![a-z])[ -]?(?:listed|traded)\b|"
        rf"\blisted\s+(?:in|on)\s+(?:the\s+)?(?:{country_terms})(?![a-z])",
        lower,
    ):
        raise UnsupportedResearchConstraint(
            "A listing-country constraint is different from headquarters country. Specify a headquarters country or an exchange/MIC explicitly."
        )
    if len(sectors) > 1:
        raise UnsupportedResearchConstraint(
            "Multiple economic sectors were requested. Run one sector at a time so rankings and peer medians remain comparable."
        )
    if len(industries) > 1:
        raise UnsupportedResearchConstraint(
            "Multiple industries were requested. Run one TRBC classification at a time so the request is not silently broadened."
        )
    if len(countries) > 1:
        raise UnsupportedResearchConstraint(
            "Multiple countries were requested. Run one headquarters-country screen at a time."
        )

    known_terms = sorted((*_SECTOR_ALIASES.keys(), *_INDUSTRY_ALIASES.keys()), key=len, reverse=True)
    for term in known_terms:
        if re.search(
            rf"(?:\b(?:exclude|excluding|except|without|outside|no|not\s+in)\b[^.;,]{{0,30}}|"
            rf"\bnon[- ])(?<![a-z]){re.escape(term)}(?![a-z])",
            lower,
        ):
            raise UnsupportedResearchConstraint(
                "Exclusionary sector and industry screens are not compiled yet; the request was stopped instead of reversing the filter."
            )
    for term in sorted(_COUNTRY_WORDS, key=len, reverse=True):
        if re.search(
            rf"(?:\b(?:no|outside|exclude|excluding)\b[^.;,]{{0,30}}|\b(?:non|ex)[- ]|"
            rf"\bnot\b[^.;,]{{0,35}})(?<![a-z]){re.escape(term)}(?![a-z])",
            lower,
        ):
            raise UnsupportedResearchConstraint(
                "Negative country screens are not compiled yet; the request was stopped instead of reversing the headquarters filter."
            )
    if re.search(r"(?:,|\bbut)\s+not\b|\bexcept\s+[A-Z]", text):
        raise UnsupportedResearchConstraint(
            "Named-company exclusions are not compiled yet; the request was stopped instead of dropping the exclusion."
        )
    if re.search(r"[£€]\s*\d|\d\s*(?:gbp|eur)\b", lower):
        raise UnsupportedResearchConstraint(
            "Non-USD market-cap thresholds require an explicit currency conversion policy and were not treated as USD."
        )
    market_cap_unit = re.search(
        r"market\s+cap(?:italization)?\s*(?:over|above|under|below|[<>]=?|greater than|less than|at least|at most)"
        r"\s*\$?\s*[\d,.]+\s+([a-z]+)",
        lower,
    )
    if market_cap_unit and market_cap_unit.group(1) not in {
        "trillion", "billion", "million", "tn", "bn", "t", "b", "m",
    }:
        raise UnsupportedResearchConstraint(
            f"Unrecognized market-cap unit: {market_cap_unit.group(1)!r}."
        )
    if re.search(r"\b(?:european|europe|eu|australian|australia|korean|south korea|south korean|georgia)\b", lower):
        if re.search(r"\b(?:stocks?|companies|equities|headquartered|in|from)\b", lower):
            raise UnsupportedResearchConstraint(
                "The requested geography is unsupported or ambiguous and was not omitted from the screen."
            )

    explicit = re.search(
        r"\b(?:in|within|from)\s+(?:the\s+)?([a-z][a-z0-9 &/+-]{1,45}?)\s+(?:sector|industry|space)\b",
        lower,
    )
    if explicit and not sectors and not industries:
        phrase = explicit.group(1).strip()
        raise UnsupportedResearchConstraint(
            f"Unrecognized sector or industry constraint: {phrase!r}. The application will not run a broader screen."
        )

    unsupported_metric = re.search(
        r"\b(revenue growth|sales growth|earnings growth|eps growth|return on equity|roe|debt[- /]?to[- /]?equity|"
        r"profit margin|operating margin|beta|trading volume|share price)\b[^.;,]{0,35}\b(above|below|under|over|"
        r"at least|at most|less than|greater than|between)\b|"
        r"\b(above|below|under|over|at least|at most|less than|greater than|between)\b[^.;,]{0,35}\b"
        r"(revenue growth|sales growth|earnings growth|eps growth|return on equity|roe|debt[- /]?to[- /]?equity|"
        r"profit margin|operating margin|beta|trading volume|share price)\b",
        lower,
    )
    if unsupported_metric:
        metric = next((group for group in unsupported_metric.groups() if group and group not in {
            "above", "below", "under", "over", "at least", "at most", "less than", "greater than", "between"
        }), "requested metric")
        raise UnsupportedResearchConstraint(
            f"The explicit {metric} threshold is not supported by the deterministic screen and was not ignored."
        )
    unsupported_direction = re.search(
        r"\b(?:p/?e|pe|forward\s+(?:p/?e|pe)|fwd\s+(?:p/?e|pe)|ev\s*/?\s*ebitda)\b"
        r"[^.;,]{0,20}\b(?:above|over|greater than|at least|>=)\b|"
        r"\b(?:dividend yield|(?:3[- ]month|three[- ]month)\s+(?:total\s+)?return)\b"
        r"[^.;,]{0,20}\b(?:below|under|less than|at most|<=)\b",
        lower,
    )
    if unsupported_direction:
        raise UnsupportedResearchConstraint(
            "That threshold direction is not supported by the deterministic screen and was not reversed or ignored."
        )
    if re.search(r"\bmarket\s+cap(?:italization)?\s+between\b", lower) and not re.search(
        r"\bmarket\s+cap(?:italization)?\s+between\s+\$?\s*\d", lower
    ):
        raise UnsupportedResearchConstraint("The market-cap range could not be parsed safely.")
    unknown_geography = re.search(
        r"\b(?:stocks?|companies|equities)\s+(?:headquartered\s+)?in\s+(?:the\s+)?([a-z][a-z .'-]{1,35})\s*$",
        lower,
    )
    if unknown_geography and not sectors and not industries and not countries:
        raise UnsupportedResearchConstraint(
            f"Unrecognized or ambiguous geography: {unknown_geography.group(1).strip()!r}."
        )
    if re.search(r"\b(?:strong|weak|high|low)\s+(?:financial performance|fundamentals|growth|quality)\b", lower):
        raise UnsupportedResearchConstraint(
            "The qualitative financial constraint is not yet mapped to a deterministic threshold and was not ignored."
        )

    return sectors[0] if sectors else None, industries[0] if industries else None, countries[0] if countries else None


def _deterministic_plan(text: str) -> ResearchPlan:
    lower = text.casefold()
    named_security_subject = _named_security_subject(text)
    constraint_text = text
    if named_security_subject:
        # Company names such as American Express, British American Tobacco,
        # Industrial Logistics Properties, or United States Steel must not be
        # reinterpreted as geography or taxonomy merely because the user adds
        # the singular noun "stock".
        constraint_text = re.sub(
            re.escape(named_security_subject),
            "named-security",
            text,
            count=1,
            flags=re.I,
        )
    detected_sector, detected_industry, detected_country = _validate_request_constraints(
        constraint_text
    )
    opportunity_words = re.search(
        r"\b(good|best|strong|attractive|promising|undervalued|bargain|cheap|value|investment|investable|buy|pick|candidate|opportunity)\b",
        lower,
    )
    explicit_screen_word = bool(re.search(r"\b(screen|screener)\b", lower))
    generic_classification_company = bool(
        re.search(r"\b(?:a|an|one|some)\s+(?:[a-z&-]+\s+){0,3}(?:company|stock)\b", lower)
    )
    classification_collection = bool(re.search(r"\b(?:stocks|companies|equities)\b", lower))
    unspecified_sector_company = bool(
        (detected_sector or detected_industry)
        and (
            opportunity_words
            or generic_classification_company
        )
        and not (explicit_screen_word and not opportunity_words)
    )

    named_find = re.search(r"^\s*find\s+(.+?)\s+(?:stock|shares?)\s*[?.!]*$", text, re.I)
    named_find_subject = named_find.group(1).strip() if named_find else ""
    generic_find_words = re.search(
        r"\b(promising|undervalued|cheap|bargain|best|good|strong|attractive|a|an|some|any)\b",
        named_find_subject.casefold(),
    ) if named_find_subject else None
    explicitly_named_company = bool(
        re.search(
            r"^\s*(?:analy[sz]e|research|study|examine|assess|review|investigate|evaluate)\s+"
            r"(?:(?:a|an)\s+.+?\s+named\s+.+|[^,]+,\s+(?:a|an)\s+.+?\s+company)\b",
            text,
            re.I,
        )
    )

    if explicitly_named_company or named_security_subject:
        mode, workflow = "company", "company_deep_dive"
    elif unspecified_sector_company:
        mode, workflow = "screen", "sector_opportunity"
    elif re.search(r"\bcompare\b|\bversus\b|\bvs\.?\b", lower):
        mode, workflow = "compare", "company_compare"
    elif re.search(r"\bmarket news\b|\btop news\b|\bwhat(?:'s| is) happening\b", lower):
        mode, workflow = "market_news", "market_news"
    elif named_find and not generic_find_words:
        mode, workflow = "company", "company_deep_dive"
    elif (detected_sector or detected_industry) and classification_collection:
        mode, workflow = "screen", "stock_screen"
    elif (
        re.search(
            r"\b(?:screen|screener|find|show(?:\s+me)?|list|research|study|examine|assess|evaluate|"
            r"analy[sz]e|review|investigate|focus\s+on|narrow\s+to|filter\s+(?:for|to)|what\s+about)\b",
            lower,
        )
        or re.search(r"\b(?:stocks?|companies|equities)\b[^.;,]{0,20}\binstead\b", lower)
    ) and re.search(r"\b(stocks?|companies|equities)\b", lower):
        mode, workflow = "screen", "stock_screen"
    else:
        mode, workflow = "company", "company_deep_dive"

    topics = [topic for word, topic in _TOPIC_WORDS.items() if re.search(rf"\b{re.escape(word)}\b", lower)]
    if not topics and mode in {"company", "compare"}:
        topics = list(DEFAULT_TOPICS)

    screen = ScreenFilters(sector=detected_sector, industry=detected_industry, country_code=detected_country)
    if mode == "screen":
        market_cap_range = re.search(
            rf"market\s+cap(?:italization)?\s+between\s+({_SCALED_NUMBER_PATTERN})\s+and\s+({_SCALED_NUMBER_PATTERN})",
            lower,
        )
        if market_cap_range:
            lower_bound = market_cap_range.group(1)
            upper_bound = market_cap_range.group(2)
            upper_suffix = re.search(r"(trillion|billion|million|tn|bn|[tbm])\s*$", upper_bound, re.I)
            if upper_suffix and not re.search(r"(trillion|billion|million|tn|bn|[tbm])\s*$", lower_bound, re.I):
                lower_bound += upper_suffix.group(1)
            screen.market_cap_min = _number_with_scale(lower_bound)
            screen.market_cap_max = _number_with_scale(upper_bound)
        minimum_matches = re.findall(
            rf"market\s+cap(?:italization)?\s*(?:over|above|>|>=|greater than|at least)\s*({_SCALED_NUMBER_PATTERN})|"
            rf"(?:over|above|>|>=|greater than|at least)\s*({_SCALED_NUMBER_PATTERN})\s+market\s+cap(?:italization)?",
            lower,
        )
        parsed_minima = [
            _number_with_scale(first or second) for first, second in minimum_matches if (first or second)
        ]
        parsed_minima = [value for value in parsed_minima if value is not None]
        if parsed_minima:
            values = ([screen.market_cap_min] if screen.market_cap_min is not None else []) + parsed_minima
            screen.market_cap_min = max(values)
        maximum_matches = re.findall(
            rf"market\s+cap(?:italization)?\s*(?:under|below|<|<=|less than|at most)\s*({_SCALED_NUMBER_PATTERN})|"
            rf"(?:under|below|<|<=|less than|at most)\s*({_SCALED_NUMBER_PATTERN})\s+market\s+cap(?:italization)?",
            lower,
        )
        parsed_maxima = [
            _number_with_scale(first or second) for first, second in maximum_matches if (first or second)
        ]
        parsed_maxima = [value for value in parsed_maxima if value is not None]
        if parsed_maxima:
            values = ([screen.market_cap_max] if screen.market_cap_max is not None else []) + parsed_maxima
            screen.market_cap_max = min(values)
        fpe = re.search(r"(?:forward|fwd)\s+(?:p/?e|pe)\s*(?:under|below|<|<=|less than|at most)\s*([+-]?\d+(?:\.\d+)?)", lower)
        if fpe:
            screen.forward_pe_max = float(fpe.group(1))
        pe_text = re.sub(r"(?:forward|fwd)\s+(?:p/?e|pe)\s*(?:under|below|<|<=|less than|at most)\s*[+-]?\d+(?:\.\d+)?", "", lower)
        pe = re.search(r"(?:p/?e|pe)\s*(?:under|below|<|<=|less than|at most)\s*([+-]?\d+(?:\.\d+)?)", pe_text)
        if pe:
            screen.pe_max = float(pe.group(1))
        ev_ebitda = re.search(r"(?:ev\s*/?\s*ebitda|enterprise value to ebitda)\s*(?:under|below|<|<=|less than|at most)\s*([+-]?\d+(?:\.\d+)?)", lower)
        if ev_ebitda:
            screen.ev_ebitda_max = float(ev_ebitda.group(1))
        dividend = re.search(r"dividend\s+yield\s*(?:over|above|>=|greater than|at least)\s*(\d+(?:\.\d+)?)\s*%?", lower)
        if dividend:
            screen.dividend_yield_min = float(dividend.group(1))
        return_3m = re.search(r"(?:3[- ]month|three[- ]month)\s+(?:total\s+)?return\s*(?:over|above|>=|greater than|at least)\s*(-?\d+(?:\.\d+)?)\s*%?", lower)
        if return_3m:
            screen.total_return_3m_min = float(return_3m.group(1))
        limit = re.search(r"\b(?:top|show|find|list)\s+(-?\d+(?:\.\d+)?)\b", lower)
        if limit:
            screen.limit = limit.group(1)  # normalized() validates exact integer syntax.
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


def _contextual_screen_plan(text: str, prior_plan: ResearchPlan) -> ResearchPlan:
    """Overlay an explicit follow-up turn on a prior successful screen plan.

    A geography-only turn such as ``study us stocks`` inherits the prior TRBC
    classification. Any taxonomy or country stated in the new turn replaces
    that dimension. Other omitted constraints remain intact, which makes the
    effective LSEG request visible and deterministic instead of relying on an
    LLM to reconstruct conversation state.
    """
    if prior_plan.mode != "screen":
        raise UnsupportedResearchConstraint(
            "Only a prior stock screen can be refined with an abbreviated universe request."
        )

    lower = re.sub(r"\s+", " ", text.casefold()).strip()
    if re.search(
        r"\b(?:remove|drop|clear|ignore)\b[^.;,]{0,35}"
        r"\b(?:filter|constraint|limit|country|geography|sector|industry|market\s+cap|p/?e|ev/?ebitda)\b",
        lower,
    ):
        raise UnsupportedResearchConstraint(
            "Removing an inherited screen constraint requires a complete replacement request; "
            "the constraint was not silently discarded."
        )

    explicit_fresh_screen = bool(
        re.search(
            r"\b(?:new|fresh)\s+screen\b|\bstart\s+(?:over|a\s+new\s+screen)\b|"
            r"\bfrom\s+scratch\b|\b(?:just|only)\s+list\b|"
            r"\b(?:global|worldwide|international)\b[^.;,]{0,30}\b(?:stocks|companies|equities)\b|"
            r"\b(?:stocks|companies|equities)\b[^.;,]{0,20}\bglobally\b",
            lower,
        )
    )
    if explicit_fresh_screen:
        return _deterministic_plan(text)

    current = _deterministic_plan(text)
    if current.mode != "screen":
        raise UnsupportedResearchConstraint(
            "The follow-up could not be compiled as a stock-screen refinement."
        )

    reset_all_filters = bool(
        re.search(r"\ball\b[^.;,]{0,45}\b(?:stocks|companies|equities)\b", lower)
    )
    merged = (
        ScreenFilters()
        if reset_all_filters
        else ScreenFilters(**asdict(prior_plan.screen))
    )
    clear_taxonomy = reset_all_filters or bool(
        re.search(
            r"\bacross\s+all\s+(?:sectors|industries)\b",
            lower,
        )
    )
    if clear_taxonomy:
        merged.sector = None
        merged.industry = None
    if current.screen.industry:
        # An explicit lower-level classification replaces both inherited TRBC
        # dimensions; normalized() restores its exact parent sector.
        merged.sector = None
        merged.industry = current.screen.industry
    elif current.screen.sector:
        # An explicit economic sector broadens/replaces an inherited industry,
        # rather than creating an impossible cross-taxonomy conjunction.
        merged.sector = current.screen.sector
        merged.industry = None

    if current.screen.country_code:
        merged.country_code = current.screen.country_code

    numeric_fields = (
        "market_cap_min", "market_cap_max", "pe_max", "forward_pe_max",
        "ev_ebitda_max", "dividend_yield_min", "total_return_3m_min",
    )
    for field_name in numeric_fields:
        value = getattr(current.screen, field_name)
        if value is not None:
            setattr(merged, field_name, value)

    if current.screen.universe:
        merged.universe = current.screen.universe
    if re.search(r"\b(?:top|show|find|list)\s+-?\d", lower):
        merged.limit = current.screen.limit

    opportunity_requested = bool(
        re.search(
            r"\b(?:good|best|strong|attractive|promising|undervalued|bargain|cheap|value|"
            r"investment|investable|buy|pick|candidate|opportunity)\b",
            lower,
        )
    )
    inherited_candidate_search = prior_plan.screen.candidate_search and not reset_all_filters
    merged.candidate_search = bool(
        inherited_candidate_search or current.screen.candidate_search or opportunity_requested
    )
    if merged.candidate_search and not (merged.sector or merged.industry):
        raise UnsupportedResearchConstraint(
            "A promising/value candidate refinement requires a supported sector or industry."
        )
    if merged.candidate_search:
        merged.sort_by = "quality_value"
    elif prior_plan.screen.candidate_search and (reset_all_filters or clear_taxonomy):
        merged.sort_by = current.screen.sort_by

    lookback_is_explicit = bool(
        re.search(r"\b(?:last|past|over)\s+\d+\s*(?:days?|weeks?|months?|years?)\b", lower)
        or re.search(r"\b(?:today|this\s+week|this\s+month|last\s+quarter)\b", lower)
    )
    horizon_is_explicit = bool(
        re.search(r"\b(?:short[- ]term|next\s+few\s+weeks|next\s+quarter|long[- ]term|multi[- ]year|years)\b", lower)
    )
    topics = list(dict.fromkeys([*prior_plan.topics, *current.topics]))
    workflow = "sector_opportunity" if merged.candidate_search else "stock_screen"
    return ResearchPlan(
        mode="screen",
        workflow=workflow,
        entities=[],
        topics=topics,
        lookback_days=current.lookback_days if lookback_is_explicit else prior_plan.lookback_days,
        investment_horizon=(
            current.investment_horizon if horizon_is_explicit else prior_plan.investment_horizon
        ),
        screen=merged,
        raw_request=text,
        planner="deterministic_contextual",
        context_parent_request=prior_plan.effective_request,
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
                "industry, ev_ebitda_max, dividend_yield_min, total_return_3m_min, limit, lookback_days, investment_horizon. "
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
        industry=payload.get("industry"),
        limit=payload.get("limit") or 15,
        candidate_search=workflow == "sector_opportunity",
    )
    entities_payload = payload.get("entities") or []
    if not isinstance(entities_payload, list) or not all(isinstance(item, str) for item in entities_payload):
        raise ValueError("Planner entities must be a JSON list of strings.")
    return ResearchPlan(
        mode=mode,
        workflow=workflow,
        entities=entities_payload,
        topics=list(DEFAULT_TOPICS),
        lookback_days=int(payload.get("lookback_days") or 365),
        investment_horizon=str(payload.get("investment_horizon") or "medium_term"),
        screen=screen,
        raw_request=text,
        planner="groq_intent_only",
    ).normalized()


def build_research_plan(
    text: str,
    settings: Settings,
    prior_plan: ResearchPlan | None = None,
) -> ResearchPlan:
    fallback = (
        _contextual_screen_plan(text, prior_plan)
        if prior_plan is not None
        else _deterministic_plan(text)
    )
    # User constraints, entities, and workflow selection are safety-critical.
    # The deterministic compiler is therefore authoritative; generated intent
    # output cannot add, remove, or reinterpret a screen constraint.
    return fallback
