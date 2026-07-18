"""Deterministic, read-only LSEG research workflows.

The language model chooses a workflow and extracts constraints. It never writes
LSEG code or chooses arbitrary API functions. This module compiles the intent
into a fixed sequence of validated read-only operations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowStage:
    stage_id: str
    purpose: str
    operations: tuple[str, ...]
    required: bool = True


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    mode: str
    purpose: str
    stages: tuple[WorkflowStage, ...]
    screen_limit: int = 200
    deep_dive_candidates: int = 5
    news_stories_per_candidate: int = 2
    minimum_screen_factor_families: int = 4
    minimum_evidence_families: int = 4

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


WORKFLOWS: dict[str, WorkflowDefinition] = {
    "company_deep_dive": WorkflowDefinition(
        workflow_id="company_deep_dive",
        mode="company",
        purpose="Build a broad evidence dossier for one named company.",
        stages=(
            WorkflowStage("resolve", "Resolve the company to a primary LSEG instrument.", ("discovery.search", "discovery.convert_symbols")),
            WorkflowStage("core", "Retrieve identity, financials, quality, valuation, estimates, recommendations, and risk.", ("access.get_data",)),
            WorkflowStage("history", "Retrieve price and estimate histories and derive momentum and revisions.", ("access.get_history", "access.get_data")),
            WorkflowStage("context", "Retrieve peers, Reuters news and stories, events, guidance, ownership, insiders, filings, ESG, suppliers, and customers when entitled.", ("discovery.peers", "news.headlines", "news.story", "content.filings", "content.esg", "discovery.suppliers", "discovery.customers"), required=False),
            WorkflowStage("synthesis", "Identify evidence-backed opportunities, catalysts, risks, and contradictions.", ("local.metrics", "llm.evidence_synthesis")),
        ),
    ),
    "company_compare": WorkflowDefinition(
        workflow_id="company_compare",
        mode="compare",
        purpose="Research named companies with the same evidence bundle and compare them consistently.",
        stages=(
            WorkflowStage("resolve", "Resolve every named company.", ("discovery.search", "discovery.convert_symbols")),
            WorkflowStage("core", "Retrieve identical cross-company data bundles.", ("access.get_data",)),
            WorkflowStage("history", "Derive price momentum, volatility, and estimate revisions.", ("access.get_history", "access.get_data")),
            WorkflowStage("context", "Retrieve Reuters news, events, guidance, and peer context.", ("news.headlines", "news.story", "discovery.peers"), required=False),
            WorkflowStage("synthesis", "Compare opportunities and risks without changing the evidence standard.", ("local.metrics", "llm.evidence_synthesis")),
        ),
        deep_dive_candidates=8,
    ),
    "sector_opportunity": WorkflowDefinition(
        workflow_id="sector_opportunity",
        mode="screen",
        purpose="Screen a sector broadly, rank with multiple independent factors, deeply research finalists, and select the best-supported candidate.",
        stages=(
            WorkflowStage("universe", "Build an investable sector universe with LSEG Screener or a requested chain.", ("discovery.screener", "discovery.chain")),
            WorkflowStage("ranking", "Retrieve value, quality, cash-flow, expectations, target, momentum, and risk factors for the broad universe.", ("access.get_data",)),
            WorkflowStage("shortlist", "Apply coverage-aware multi-factor ranking and select finalists.", ("local.multifactor_rank",)),
            WorkflowStage("deep_dive", "Retrieve comprehensive dossiers for the top five candidates.", ("access.get_data", "access.get_history", "discovery.peers", "news.headlines", "news.story", "content.filings", "content.esg")),
            WorkflowStage("synthesis", "Let the LLM identify major opportunities, catalysts, risks, and contradictions from the complete evidence package.", ("llm.evidence_synthesis", "local.claim_guard")),
        ),
        screen_limit=200,
        deep_dive_candidates=5,
        news_stories_per_candidate=2,
        minimum_screen_factor_families=5,
        minimum_evidence_families=5,
    ),
    "stock_screen": WorkflowDefinition(
        workflow_id="stock_screen",
        mode="screen",
        purpose="Run a user-specified stock screen and return ranked matches.",
        stages=(
            WorkflowStage("universe", "Compile and execute the constrained screen.", ("discovery.screener", "discovery.chain")),
            WorkflowStage("enrichment", "Retrieve requested ranking fields for matching instruments.", ("access.get_data",)),
            WorkflowStage("output", "Apply explicit filters and return concise matches.", ("local.filter_sort",)),
        ),
        deep_dive_candidates=0,
    ),
    "market_news": WorkflowDefinition(
        workflow_id="market_news",
        mode="market_news",
        purpose="Retrieve and summarize relevant Reuters/LSEG market headlines.",
        stages=(
            WorkflowStage("headlines", "Retrieve recent headlines.", ("news.headlines",)),
            WorkflowStage("stories", "Retrieve a small number of relevant stories when available.", ("news.story",), required=False),
            WorkflowStage("synthesis", "Summarize only supported market developments.", ("llm.evidence_synthesis",)),
        ),
    ),
}


def infer_workflow(mode: str, *, candidate_search: bool = False) -> str:
    if mode == "screen":
        return "sector_opportunity" if candidate_search else "stock_screen"
    if mode == "compare":
        return "company_compare"
    if mode == "market_news":
        return "market_news"
    return "company_deep_dive"


def get_workflow(workflow_id: str | None, mode: str, *, candidate_search: bool = False) -> WorkflowDefinition:
    resolved_id = workflow_id if workflow_id in WORKFLOWS else infer_workflow(mode, candidate_search=candidate_search)
    return WORKFLOWS[resolved_id]


def workflow_context() -> str:
    return "\n".join(f"- {key}: {value.purpose}" for key, value in WORKFLOWS.items())
