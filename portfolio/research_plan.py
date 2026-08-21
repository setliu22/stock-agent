"""Validated research plans and the supported LSEG classification catalog."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import re
from typing import Any

from .research_workflows import WORKFLOWS, get_workflow


VALID_TOPICS = {
    "profile", "fundamentals", "profitability", "valuation", "estimates",
    "recommendations", "guidance", "price", "risk", "news", "events",
    "ownership", "insiders", "filings", "peers", "suppliers", "customers",
    "estimate_history", "benchmark_price",
}
VALID_SELECTION_OBJECTIVES = {"positive_signals", "relative_value"}
DEFAULT_TOPICS = [
    "profile", "fundamentals", "profitability", "valuation", "estimates",
    "recommendations", "price", "risk", "news", "peers", "filings",
]

_SECTOR_NAMES = (
    "Energy", "Basic Materials", "Industrials", "Consumer Cyclicals",
    "Consumer Non-Cyclicals", "Financials", "Healthcare", "Technology",
    "Telecommunications Services", "Utilities", "Real Estate",
)

# Canonical labels and accepted aliases map to exact LSEG TRBC values.
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
    "capital goods": "Industrials",
    "industrial goods": "Industrials",
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
    """One exact TRBC hierarchy node and its accepted input aliases."""

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
        ("pharmaceuticals", "pharmaceutical", "pharma", "drug maker", "drug makers", "drugmaker", "drugmakers"),
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


def supported_research_taxonomy_options() -> tuple[tuple[str, str], ...]:
    """Return display labels and exact values supported by the research compiler."""
    sectors = tuple((f"{sector} (Sector)", sector) for sector in _SECTOR_NAMES)
    industries = tuple(
        (
            f"{definition.label} ({definition.parent_sector} industry)",
            definition.label,
        )
        for definition in sorted(TRBC_CLASSIFICATIONS, key=lambda item: item.label)
    )
    return sectors + industries

_INDUSTRY_ALIASES: dict[str, TRBCClassification] = {
    alias.casefold(): definition
    for definition in TRBC_CLASSIFICATIONS
    for alias in (definition.label, *definition.aliases)
}


def canonicalize_sector(value: str | None) -> str | None:
    """Map a UI or imported sector value to the exact LSEG TRBC name."""
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
    limit_explicit: bool = False
    sort_by: str = "market_cap"
    candidate_search: bool = False


@dataclass
class ResearchPlan:
    mode: str = "company"
    workflow: str | None = None
    entities: list[str] = field(default_factory=list)
    # Screen plans should not silently request every row-expanding company
    # topic. Company/compare defaults are applied by normalized() instead.
    topics: list[str] = field(default_factory=list)
    selection_objectives: list[str] = field(default_factory=list)
    lookback_days: int = 365
    investment_horizon: str = "medium_term"
    screen: ScreenFilters = field(default_factory=ScreenFilters)
    raw_request: str = ""
    macro_regime: str | None = None
    benchmark: str | None = None

    def normalized(self) -> "ResearchPlan":
        self.mode = self.mode if self.mode in {"company", "compare", "screen", "market_news"} else "company"
        if not isinstance(self.entities, list) or not all(isinstance(item, str) for item in self.entities):
            raise UnsupportedResearchConstraint("Research entities must be a list of strings.")
        self.entities = [item.strip() for item in self.entities if item.strip()]
        if not isinstance(self.selection_objectives, list) or not all(
            isinstance(item, str) for item in self.selection_objectives
        ):
            raise UnsupportedResearchConstraint("Selection objectives must be a list of strings.")
        unknown_objectives = set(self.selection_objectives) - VALID_SELECTION_OBJECTIVES
        if unknown_objectives:
            raise UnsupportedResearchConstraint(
                "Unsupported selection objectives: " + ", ".join(sorted(unknown_objectives))
            )
        self.selection_objectives = list(dict.fromkeys(self.selection_objectives))
        if self.macro_regime is not None:
            self.macro_regime = str(self.macro_regime).strip()[:120] or None
        if self.benchmark is not None:
            self.benchmark = str(self.benchmark).strip()[:120] or None
        self.topics = [topic for topic in dict.fromkeys(self.topics) if topic in VALID_TOPICS]
        if not self.topics and self.mode not in {"screen", "market_news"}:
            self.topics = list(DEFAULT_TOPICS)
        try:
            # Preserve zero so it is rejected by the range check rather than
            # being silently replaced with the default.
            self.lookback_days = int(self.lookback_days)
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
        if not isinstance(self.screen.limit_explicit, bool):
            raise UnsupportedResearchConstraint("Screen limit provenance must be boolean.")
        if self.screen.sort_by not in {"market_cap", "pe", "forward_pe", "ev_ebitda", "return", "quality_value"}:
            raise UnsupportedResearchConstraint(f"Unsupported screen sort key: {self.screen.sort_by!r}.")
        if self.screen.universe and not re.fullmatch(r"[A-Za-z0-9#.^=_-]{1,64}", str(self.screen.universe)):
            raise UnsupportedResearchConstraint("The requested chain/universe identifier is malformed.")
        if self.investment_horizon not in {"short_term", "medium_term", "long_term"}:
            raise UnsupportedResearchConstraint(
                "Investment horizon must be short_term, medium_term, or long_term."
            )
        if self.mode == "screen" and self.selection_objectives:
            if not (self.screen.sector or self.screen.industry):
                raise UnsupportedResearchConstraint(
                    "A candidate-selection request requires a supported sector or industry so value and quality comparisons use a coherent peer group."
                )
            self.screen.candidate_search = True
        if self.mode == "screen" and self.topics and not self.screen.candidate_search:
            raise UnsupportedResearchConstraint(
                "Requested company-level topics cannot be executed for every row of a plain stock screen. "
                "Ask for a ranked candidate in a supported sector/industry or remove those topics."
            )
        if self.screen.candidate_search:
            self.screen.sort_by = "quality_value"
            if self.screen.limit == 15 and not self.screen.limit_explicit:
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
            if self.entities[0].casefold() in {"it", "this", "that", "the company", "the stock"}:
                raise UnsupportedResearchConstraint(
                    "A company deep dive requires an explicit company, ticker, or RIC."
                )
            if re.match(
                r"^(?:what|why|how|whether|if|when|where|who|which)\b",
                self.entities[0].strip(),
                re.I,
            ):
                raise UnsupportedResearchConstraint(
                    "An interrogative or clausal phrase cannot be resolved as a named security."
                )
        elif self.workflow in {"company_compare", "position_review", "research_lab"}:
            minimum = 1 if self.workflow in {"position_review", "research_lab"} else 2
            maximum = 9 if self.workflow == "research_lab" and self.benchmark else 8
            if not minimum <= len(self.entities) <= maximum:
                label = {
                    "position_review": "A position review",
                    "research_lab": "A Research Lab plan",
                }.get(self.workflow, "A company comparison")
                raise UnsupportedResearchConstraint(
                    f"{label} requires between {minimum} and {maximum} named securities."
                )
            if any(entity.casefold() in {"it", "this", "that", "the company", "the stock"} for entity in self.entities):
                raise UnsupportedResearchConstraint(
                    "A named-company workflow cannot resolve a pronoun without explicit prior-company routing."
                )
        elif self.workflow == "sector_opportunity":
            if self.entities:
                raise UnsupportedResearchConstraint("A sector opportunity request cannot also name a company.")
            if not (self.screen.sector or self.screen.industry):
                raise UnsupportedResearchConstraint("A sector opportunity request requires a supported TRBC classification.")
        elif self.workflow == "stock_screen" and self.entities:
            raise UnsupportedResearchConstraint("A stock screen cannot also contain named-company entities.")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
