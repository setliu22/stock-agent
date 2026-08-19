"""Machine-readable catalog of the public LSEG Data Library capabilities.

The application uses this catalog to constrain natural-language planning. It is
not an entitlement guarantee: the user's Workspace variant and add-ons decide
which calls and content sets actually return data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ast
import importlib.metadata
import importlib.util
import json
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class Capability:
    name: str
    category: str
    purpose: str
    natural_language: tuple[str, ...] = ()
    research_relevance: str = "supporting"
    execution: str = "catalogued"


@dataclass(frozen=True)
class OperationSpec:
    operation_id: str
    callable_path: str
    purpose: str
    handler: str
    required_inputs: tuple[str, ...]
    access_points: tuple[str, ...] = ("desktop", "platform")
    entitlement: str = "content-dependent"
    response: str = "DataFrame"
    autonomous_read: bool = True
    limitations: str = "Availability and returned fields depend on entitlements."


# Exact read-only operations that deterministic workflows may execute. The raw
# installed callable inventory is broader, but natural language never invokes
# an arbitrary callable directly.
EXECUTABLE_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec("session.open", "lseg.data.open_session", "Open the configured Workspace or platform session.", "_open_lseg_session", ("session_name",), entitlement="session"),
    OperationSpec("access.get_data", "lseg.data.get_data", "Retrieve snapshots, fundamentals, estimates, reference data, events, ownership, and analytics fields.", "_safe_get_data", ("universe", "fields")),
    OperationSpec("access.get_history", "lseg.data.get_history", "Retrieve supported price and data-item histories.", "_retrieve_price_history", ("universe", "fields", "start", "end")),
    OperationSpec("discovery.search", "lseg.data.discovery.search", "Resolve natural company names and search indexed instruments and organizations.", "resolve_instrument", ("query",)),
    OperationSpec("discovery.convert_symbols", "lseg.data.discovery.convert_symbols", "Convert tickers and other identifiers to RICs.", "ticker_to_ric", ("symbols", "from_symbol_type", "to_symbol_types")),
    OperationSpec("discovery.screener", "lseg.data.discovery.Screener", "Compile and execute a constrained Workspace screen.", "_retrieve_screen", ("expression",)),
    OperationSpec("discovery.chain", "lseg.data.discovery.Chain", "Expand a requested index or chain universe.", "_retrieve_screen", ("name",)),
    OperationSpec("discovery.peers", "lseg.data.discovery.Peers", "Retrieve LSEG peer instruments for relative comparison.", "_retrieve_peers", ("instrument",)),
    OperationSpec("discovery.suppliers", "lseg.data.discovery.Suppliers", "Retrieve supplier relationships.", "_retrieve_stakeholders", ("instrument",), entitlement="relationship-data"),
    OperationSpec("discovery.customers", "lseg.data.discovery.Customers", "Retrieve customer relationships.", "_retrieve_stakeholders", ("instrument",), entitlement="relationship-data"),
    OperationSpec("news.headlines", "lseg.data.news.get_headlines", "Retrieve Reuters/LSEG headlines by instrument, query, and date.", "_retrieve_news", ("query",), entitlement="news"),
    OperationSpec("news.story", "lseg.data.news.get_story", "Retrieve selected full Reuters/LSEG stories for evidence synthesis.", "_retrieve_news_stories", ("story_id",), entitlement="news", response="text", limitations="Story retrieval is one story per request; history and archive access depend on entitlement."),
    OperationSpec("content.filings", "lseg.data.content.filings.search.Definition", "Search entitled company filings.", "_retrieve_filings", ("org_id", "start_date", "end_date"), entitlement="filings"),
    OperationSpec("local.multifactor_rank", "portfolio.lseg_research._rank_candidate_screen", "Rank candidates using value, quality, cash flow, expectations, momentum, target, risk, and data coverage.", "_rank_candidate_screen", ("screen_frame",), access_points=("local",), entitlement="none"),
    OperationSpec("local.metrics", "portfolio.lseg_research._derive_metrics", "Derive revisions, margins, returns, volatility, peer medians, and evidence coverage.", "_derive_metrics", ("research_result",), access_points=("local",), entitlement="none"),
    OperationSpec("llm.evidence_synthesis", "portfolio.lseg_research._llm_report", "Identify major opportunities, catalysts, risks, and contradictions from retrieved evidence only.", "_llm_report", ("evidence_payload",), access_points=("local",), entitlement="groq-key", response="plain text"),
)


CAPABILITIES: tuple[Capability, ...] = (
    # Session and access layer
    Capability("ld.open_session", "Session", "Open desktop, platform, or deployed data sessions."),
    Capability("ld.close_session", "Session", "Close the default data session."),
    Capability("session.Session.open/open_async/close", "Session", "Manage a session lifecycle and state callbacks."),
    Capability("session.on_state/on_event", "Session", "Observe authentication, connection, and session events."),
    Capability("ld.get_data", "Access", "Retrieve pricing snapshots plus TR fundamental, reference, estimates, ownership, events, and analytics fields.", ("company snapshot", "fundamentals", "valuation", "estimates"), "core", "implemented"),
    Capability("ld.get_history", "Access", "Retrieve intraday/interday pricing and supported fundamental/reference histories.", ("price history", "returns", "volume", "historical fundamentals"), "core", "implemented"),
    Capability("ld.open_pricing_stream", "Access", "Open a real-time market-price stream and optional OHLC recorder.", ("live price", "stream quotes")),
    Capability("PricingStream", "Access", "Open, close, snapshot, add/remove instruments, and record stream data."),
    Capability("ld.news.get_headlines", "News", "Search Reuters/LSEG headlines by query and date range.", ("latest news", "headline pressure", "recent catalysts"), "core", "implemented"),
    Capability("ld.news.get_story", "News", "Retrieve one full news story by story ID.", ("read story",), "core", "implemented"),
    Capability("ld.dates_and_calendars.add_periods", "Dates", "Add business/calendar periods using market calendars."),
    Capability("ld.dates_and_calendars.count_periods", "Dates", "Count periods between dates."),
    Capability("ld.dates_and_calendars.date_schedule", "Dates", "Generate date schedules."),
    Capability("ld.dates_and_calendars.holidays", "Dates", "Retrieve holidays for calendars."),
    Capability("ld.dates_and_calendars.is_working_day", "Dates", "Test whether a date is a working day."),
    Capability("ld.tradefeedr.get_fx_algo_parent_orders", "Tradefeedr", "Retrieve FX algo parent-order analytics."),
    Capability("ld.tradefeedr.get_fx_algo_pre_trade_forecast", "Tradefeedr", "Retrieve FX algo pre-trade forecasts."),
    # Discovery
    Capability("ld.discovery.search", "Discovery", "Search instruments, organizations, people, funds, bonds, derivatives, vessels, and other indexed content with filters, ranking, groups, and navigators.", ("find securities", "search organizations", "market universe"), "core", "implemented"),
    Capability("content.search.Definition", "Discovery", "Advanced synchronous/asynchronous search with select, filter, order, boost, grouping, terms, and navigators."),
    Capability("SearchPropertyExplorer", "Discovery", "Inspect searchable properties and navigator metadata."),
    Capability("search_templates", "Discovery", "Use and manage embedded search templates."),
    Capability("ld.discovery.convert_symbols", "Symbology", "Convert ticker, RIC, ISIN, CUSIP, SEDOL, PermID, Lipper ID, and IMO identifiers.", ("resolve ticker", "convert ISIN", "find RIC"), "core", "implemented"),
    Capability("content.symbol_conversion.Definition", "Symbology", "Advanced sync/async symbol conversion with country, asset-class, and asset-state filters."),
    Capability("ld.discovery.Peers", "Discovery", "Expand an instrument to LSEG peer-company RICs.", ("competitors", "peer comparison"), "core", "implemented"),
    Capability("ld.discovery.Screener", "Discovery", "Run Workspace SCREEN expressions over equity and other universes.", ("screen stocks", "find companies meeting criteria"), "core", "implemented"),
    Capability("ld.discovery.Chain", "Discovery", "Expand chain RICs into constituents and summary links.", ("index constituents", "option chain", "futures chain"), "core", "implemented"),
    Capability("ld.discovery.Suppliers", "Discovery", "Retrieve supplier relationships and related identifiers.", ("suppliers", "supply chain"), "core", "implemented"),
    Capability("ld.discovery.Customers", "Discovery", "Retrieve customer relationships and related identifiers.", ("customers", "revenue concentration"), "core", "implemented"),
    Capability("ld.discovery.Futures", "Discovery", "Find futures contracts for an underlying and expiry range.", ("futures contracts", "expiry list")),
    # Core content layer
    Capability("content.fundamental_and_reference.Definition", "Fundamental and Reference", "Advanced sync/async access to TR data items across fundamentals, estimates, pricing, corporate actions, ownership, fixed income, Lipper, and reference data.", ("financial statements", "ratios", "company profile"), "core", "implemented"),
    Capability("content.historical_pricing.summaries.Definition", "Historical Pricing", "Retrieve interday and intraday OHLC, quote, trade, and volume summaries.", ("historical prices", "intraday bars"), "core", "implemented"),
    Capability("content.historical_pricing.events.Definition", "Historical Pricing", "Retrieve tick-level pricing events."),
    Capability("content.pricing.Definition", "Real-time Pricing", "Create level-1 real-time pricing streams with refresh, update, status, complete, and error callbacks."),
    Capability("content.pricing.chain.Definition", "Real-time Pricing", "Stream chain constituents."),
    # News content layer
    Capability("content.news.headlines.Definition", "News", "Advanced paginated headline retrieval with metadata and async callbacks.", execution="implemented"),
    Capability("content.news.story.Definition", "News", "Retrieve full story content synchronously or asynchronously.", execution="implemented"),
    Capability("content.news.top_news.Definition", "News", "Retrieve curated top-news packages and hierarchy."),
    Capability("content.news.online_reports.Definition", "News", "Retrieve online reports and report hierarchy."),
    Capability("content.news.images.Definition", "News", "Retrieve news images and metadata."),
    # Estimates
    Capability("estimates.view_actuals.annual/interim", "Estimates", "Retrieve reported annual or interim actuals."),
    Capability("estimates.view_actuals_kpi.annual/interim", "Estimates", "Retrieve annual or interim KPI actuals."),
    Capability("estimates.view_summary.annual/interim", "Estimates", "Retrieve annual or interim consensus summaries for periodic measures.", ("consensus", "earnings estimates"), "core", "implemented"),
    Capability("estimates.view_summary.non_periodic_measures", "Estimates", "Retrieve non-periodic estimate summaries."),
    Capability("estimates.view_summary.recommendations", "Estimates", "Retrieve recommendation summaries."),
    Capability("estimates.view_summary.historical_snapshots_periodic_measures_annual", "Estimates", "Retrieve monthly annual-measure estimate snapshots."),
    Capability("estimates.view_summary.historical_snapshots_periodic_measures_interim", "Estimates", "Retrieve monthly interim-measure estimate snapshots."),
    Capability("estimates.view_summary.historical_snapshots_non_periodic_measures", "Estimates", "Retrieve monthly non-periodic estimate snapshots."),
    Capability("estimates.view_summary.historical_snapshots_recommendations", "Estimates", "Retrieve monthly recommendation snapshots."),
    Capability("estimates.view_summary_kpi.annual/interim", "Estimates", "Retrieve KPI estimate summaries."),
    Capability("estimates.view_summary_kpi.historical_snapshots_kpi", "Estimates", "Retrieve monthly KPI estimate snapshots."),
    # Filings
    Capability("content.filings.search.Definition", "Filings", "Search EDGAR and other entitled filing feeds by form, organization, date, text, and section.", ("10-K", "10-Q", "filings", "risk factors"), "core", "implemented"),
    Capability("content.filings.retrieval.Definition", "Filings", "Retrieve a filing by filename, DCN, document ID, or filing ID."),
    # ESG
    Capability("content.esg.basic_overview.Definition", "ESG", "Retrieve a compact ESG overview for instruments; disabled in this application because the configured account lacks entitlement."),
    Capability("content.esg.full_scores.Definition", "ESG", "Retrieve historical full ESG scores."),
    Capability("content.esg.full_measures.Definition", "ESG", "Retrieve detailed ESG measures."),
    Capability("content.esg.standard_scores.Definition", "ESG", "Retrieve standard ESG scores."),
    Capability("content.esg.standard_measures.Definition", "ESG", "Retrieve standard ESG measures."),
    Capability("content.esg.universe.Definition", "ESG", "Retrieve the ESG-covered universe."),
    Capability("content.esg.bulk", "ESG", "Download and manage bulk ESG packages and local databases."),
    # Ownership
    Capability("ownership.org_info.Definition", "Ownership", "Retrieve ownership organization information."),
    Capability("ownership.consolidated.investors.Definition", "Ownership", "Retrieve consolidated investors.", ("top holders",), "core", "catalogued"),
    Capability("ownership.consolidated.recent_activity.Definition", "Ownership", "Retrieve recent consolidated ownership activity."),
    Capability("ownership.consolidated.breakdown.Definition", "Ownership", "Retrieve consolidated ownership breakdowns."),
    Capability("ownership.consolidated.concentration.Definition", "Ownership", "Retrieve ownership concentration."),
    Capability("ownership.consolidated.top_n_concentration.Definition", "Ownership", "Retrieve top-N ownership concentration."),
    Capability("ownership.consolidated.shareholders_report.Definition", "Ownership", "Retrieve shareholder reports."),
    Capability("ownership.consolidated.shareholders_history_report.Definition", "Ownership", "Retrieve shareholder history reports."),
    Capability("ownership.fund.holdings.Definition", "Ownership", "Retrieve fund holdings."),
    Capability("ownership.fund.investors.Definition", "Ownership", "Retrieve fund investors."),
    Capability("ownership.fund.recent_activity.Definition", "Ownership", "Retrieve recent fund activity."),
    Capability("ownership.fund.breakdown.Definition", "Ownership", "Retrieve fund ownership breakdowns."),
    Capability("ownership.fund.concentration.Definition", "Ownership", "Retrieve fund concentration."),
    Capability("ownership.fund.top_n_concentration.Definition", "Ownership", "Retrieve top-N fund concentration."),
    Capability("ownership.fund.shareholders_report.Definition", "Ownership", "Retrieve fund shareholder reports."),
    Capability("ownership.fund.shareholders_history_report.Definition", "Ownership", "Retrieve historical fund shareholder reports."),
    Capability("ownership.insider.transaction_report.Definition", "Ownership", "Retrieve insider transaction reports.", ("insider buying", "insider selling"), "core", "implemented"),
    Capability("ownership.insider.shareholders_report.Definition", "Ownership", "Retrieve insider shareholder reports."),
    Capability("ownership.investor.holdings.Definition", "Ownership", "Retrieve an investor's holdings."),
    # Custom instruments
    Capability("custom_instruments.manage.create_formula", "Custom Instruments", "Create a formula-based custom instrument."),
    Capability("custom_instruments.manage.create_basket", "Custom Instruments", "Create a basket custom instrument."),
    Capability("custom_instruments.manage.create_udc", "Custom Instruments", "Create a user-defined custom instrument."),
    Capability("custom_instruments.manage.get/delete", "Custom Instruments", "Read or delete custom instruments."),
    Capability("custom_instruments.search.Definition", "Custom Instruments", "Search custom instruments."),
    Capability("custom_instruments.events.Definition", "Custom Instruments", "Retrieve custom-instrument events."),
    Capability("custom_instruments.summaries.Definition", "Custom Instruments", "Retrieve custom-instrument summaries."),
    Capability("custom_instruments.Definition.get_stream", "Custom Instruments", "Stream custom-instrument values."),
    # Instrument Pricing Analytics
    Capability("ipa.financial_contracts.bond.Definition", "Pricing Analytics", "Price and analyze bonds synchronously, asynchronously, or by stream."),
    Capability("ipa.financial_contracts.cap_floor.Definition", "Pricing Analytics", "Price caps and floors."),
    Capability("ipa.financial_contracts.cds.Definition", "Pricing Analytics", "Price credit-default swaps."),
    Capability("ipa.financial_contracts.cross.Definition", "Pricing Analytics", "Price FX crosses and swaps."),
    Capability("ipa.financial_contracts.fx_option.Definition", "Pricing Analytics", "Price vanilla and exotic FX options."),
    Capability("ipa.financial_contracts.eti_option.Definition", "Pricing Analytics", "Price exchange-traded-instrument options."),
    Capability("ipa.financial_contracts.repo.Definition", "Pricing Analytics", "Price repo contracts."),
    Capability("ipa.financial_contracts.swap.Definition", "Pricing Analytics", "Price interest-rate swaps."),
    Capability("ipa.financial_contracts.swaption.Definition", "Pricing Analytics", "Price swaptions."),
    Capability("ipa.financial_contracts.term_deposit.Definition", "Pricing Analytics", "Price term deposits."),
    Capability("ipa.curves.forward_curves.Definition", "Curves", "Build forward curves."),
    Capability("ipa.curves.zc_curves.Definition", "Curves", "Build zero-coupon curves."),
    Capability("ipa.curves.zc_curve_definitions.Definition", "Curves", "Retrieve or calculate zero-coupon curve definitions."),
    Capability("ipa.surfaces.cap.Definition", "Surfaces", "Build cap volatility surfaces."),
    Capability("ipa.surfaces.eti.Definition", "Surfaces", "Build equity/ETI volatility surfaces."),
    Capability("ipa.surfaces.fx.Definition", "Surfaces", "Build FX volatility surfaces."),
    Capability("ipa.surfaces.swaption.Definition", "Surfaces", "Build swaption volatility surfaces."),
    # Delivery layer
    Capability("delivery.endpoint_request.Definition", "Delivery", "Call entitled platform endpoints directly with GET, POST, PUT, or DELETE."),
    Capability("delivery.omm_stream.Definition", "Delivery", "Open low-level OMM real-time streams."),
    Capability("delivery.rdp_stream.Definition", "Delivery", "Open platform WebSocket streams."),
    Capability("delivery.cfs.buckets.Definition", "Bulk Delivery", "List content-file-store buckets."),
    Capability("delivery.cfs.packages.Definition", "Bulk Delivery", "List CFS packages."),
    Capability("delivery.cfs.files.Definition", "Bulk Delivery", "List CFS files."),
    Capability("delivery.cfs.file_sets.Definition", "Bulk Delivery", "List CFS file sets."),
    Capability("delivery.cfs.file_downloader.Definition.retrieve", "Bulk Delivery", "Download CFS files."),
)


def capability_context(*, implemented_only: bool = False) -> str:
    items: Iterable[Capability] = CAPABILITIES
    if implemented_only:
        items = (item for item in items if item.execution == "implemented")
    return "\n".join(
        f"- {item.name}: {item.purpose}"
        for item in items
    )


def _catalog_records(path: Path | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = [
        {"qualified_name": item.name, "summary": item.purpose, "category": item.category}
        for item in CAPABILITIES
    ]
    if path is not None and path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.extend(payload.get("installed_public_callable_inventory") or [])
        except (OSError, ValueError, TypeError):
            pass
    return records


def capability_answer(query: str, catalog_path: Path | None = None) -> str:
    lower = query.casefold()
    generic_terms = {
        "lseg", "refinitiv", "function", "functions", "feature", "features",
        "capability", "capabilities", "does", "have", "with", "from", "using",
        "able", "can", "what", "all",
    }
    terms = {
        word
        for word in __import__("re").findall(r"[a-z0-9]+", lower)
        if len(word) > 2 and word not in generic_terms
    }
    scored: list[tuple[int, dict[str, Any]]] = []
    for record in _catalog_records(catalog_path):
        text = " ".join(str(record.get(key) or "") for key in ("qualified_name", "summary", "category")).casefold()
        score = sum(3 if term in str(record.get("qualified_name") or "").casefold() else 1 for term in terms if term in text)
        if score:
            scored.append((score, record))
    if not scored:
        return concise_capability_summary()
    lines = ["Relevant LSEG capabilities"]
    seen: set[str] = set()
    for _, record in sorted(scored, key=lambda item: item[0], reverse=True):
        name = str(record.get("qualified_name") or record.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        summary = str(record.get("summary") or record.get("purpose") or "").strip()
        lines.append(f"• {name}: {summary}" if summary else f"• {name}")
        if len(lines) >= 9:
            break
    lines.append("Natural-language research uses only predefined read-only workflows; specialized functions require explicit parameters.")
    return "\n".join(lines)


def concise_capability_summary() -> str:
    return "\n".join(
        [
            "LSEG capabilities recognized by this application:",
            "• Predefined research workflows: company deep dive, consistent company comparison, multi-factor sector opportunity research, explicit stock screens, and market news.",
            "• Research evidence: company data, financials, quality, valuation, estimate histories and revisions, SmartEstimate, recommendations, prices, Reuters news and stories, events, guidance, ownership, insiders, filings, peers, suppliers, and customers when entitled.",
            "• The LLM only classifies intent and synthesizes retrieved evidence. It cannot invent LSEG calls, fields, tickers, or workflows.",
            "• Specialized functions such as real-time streams, Tradefeedr, custom-instrument writes, derivative pricing, curves, surfaces, low-level endpoints, and bulk delivery are catalogued but not autonomously invoked.",
            "• Exact installed-package function and class inventory: data/lseg_capabilities.json.",
            "",
            "Examples:",
            "• Analyze a ticker's valuation, estimates, news, and peers.",
            "• Compare two named securities on profitability and price momentum.",
            "• Screen a supported sector with explicit valuation constraints.",
            "• Request suppliers, ownership, or filings for one resolved company.",
        ]
    )


def _ast_signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    if isinstance(node, ast.ClassDef):
        return f"class {node.name}"
    args: list[str] = []
    positional = [*node.args.posonlyargs, *node.args.args]
    default_offset = len(positional) - len(node.args.defaults)
    for index, arg in enumerate(positional):
        value = arg.arg
        if index >= default_offset:
            value += "=?"
        args.append(value)
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    elif node.args.kwonlyargs:
        args.append("*")
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        args.append(arg.arg + ("=?" if default is not None else ""))
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({', '.join(args)})"


def runtime_public_api_inventory() -> list[dict[str, Any]]:
    """Statically inventory public callables in the installed lseg.data package.

    This does not import every module, so optional or entitlement-specific modules
    cannot break installation. It intentionally records the installed library's
    source surface in addition to the curated research capability map above.
    """
    try:
        spec = importlib.util.find_spec("lseg.data")
    except (ImportError, ModuleNotFoundError):
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    package_root = Path(next(iter(spec.submodule_search_locations)))
    records: list[dict[str, Any]] = []
    for source in sorted(package_root.rglob("*.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        relative = source.relative_to(package_root).with_suffix("")
        parts = list(relative.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        module = "lseg.data" + ("." + ".".join(parts) if parts else "")
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name.startswith("_"):
                continue
            doc = ast.get_docstring(node) or ""
            records.append(
                {
                    "qualified_name": f"{module}.{node.name}",
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "signature": _ast_signature(node),
                    "summary": doc.strip().split("\n\n", 1)[0].replace("\n", " ")[:500],
                    "source_module": module,
                }
            )
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        deduped.setdefault(record["qualified_name"], record)
    return list(deduped.values())


def export_catalog(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        installed_version = importlib.metadata.version("lseg-data")
    except importlib.metadata.PackageNotFoundError:
        installed_version = None
    payload = {
        "scope": "Curated LSEG Data Library research map plus installed-package callable inventory",
        "installed_lseg_data_version": installed_version,
        "entitlement_note": "Actual availability depends on Workspace variant, add-ons, content permissions, session type, and service limits.",
        "planner_policy": "Only curated research-safe capabilities are executable from natural language. Specialized pricing, trade, streaming, bulk-delivery, and write operations remain catalogued but require explicit parameters and are not guessed.",
        "executable_read_only_operations": [asdict(item) for item in EXECUTABLE_OPERATIONS],
        "capabilities": [asdict(item) for item in CAPABILITIES],
        "workflows": __import__("portfolio.research_workflows", fromlist=["WORKFLOWS"]).WORKFLOWS and [item.to_dict() for item in __import__("portfolio.research_workflows", fromlist=["WORKFLOWS"]).WORKFLOWS.values()],
        "installed_public_callable_inventory": runtime_public_api_inventory(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/lseg_capabilities.json")
    args = parser.parse_args()
    print(export_catalog(Path(args.output)))
