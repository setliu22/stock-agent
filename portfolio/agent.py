"""Natural-language routing for research and portfolio actions."""

from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from enum import Enum
import json
import os
import re
from typing import Any, Callable

from .company_resolver import AmbiguousInstrumentError, InstrumentResolutionError
from .config import Settings
from .database import PortfolioDatabase
from .event_risk import run_portfolio_position_risk_review
from .lseg_capabilities import capability_answer
from .lseg_research import (
    LSEGNoMatches,
    ResearchCancelled,
    ResearchResult,
    answer_follow_up,
    can_answer_follow_up_deterministically,
    concise_report,
    is_request_diagnostics_follow_up,
    run_research,
)
from .research_planner import (
    NotResearchRequest,
    ResearchClarificationNeeded,
    ResearchPlan,
    UnsupportedResearchConstraint,
    build_research_plan,
    detect_industry,
    detect_sector,
    extract_requested_topics,
)
from .market_data import current_price
from .market_regime import (
    MacroResearchPolicy,
    MarketRegimeSnapshot,
    build_market_regime,
    macro_default_policy,
)
from .models import Purchase
from .portfolio_import import (
    PortfolioImportError,
    parse_portfolio_json_message,
    parse_portfolio_update_json_message,
)


_RESEARCH_PATTERN = re.compile(
    r"\b(analy[sz]e|research|study|examine|assess|evaluate|investigate|review|look\s+up|"
    r"tell\s+me\s+about|deep\s+dive|compare|screen|screener)\b",
    re.IGNORECASE,
)

_EQUITY_SEARCH_PATTERN = re.compile(
    r"\b(?:find|hunt\s+for|scout|seek|identify|spot|look\s+for|surface|pick\s+out|which|"
    r"show(?:\s+me)?|list)\b[^\n]{0,160}\b(?:stocks?|companies|equities|candidates?)\b",
    re.IGNORECASE,
)

_PURCHASE_PATTERN = re.compile(
    r"\b(?:buy|bought|purchase|purchased)\s+"
    r"(?P<quantity>\d+(?:\.\d+)?)\s+(?:shares?\s+(?:of\s+)?)?"
    r"(?P<ticker>[A-Za-z][A-Za-z0-9.^/-]*)\s+"
    r"(?:at|for)\s+\$?(?P<price>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_PORTFOLIO_IMPORT_INTENT = re.compile(
    r"\b(?:add|import|upload|load|enter|paste|bring|sync)\b.*"
    r"\b(?:my\s+)?(?:portfolio|holdings?|positions?)\b",
    re.IGNORECASE | re.DOTALL,
)

_PORTFOLIO_UPDATE_INTENT = re.compile(
    r"\b(?:update|modify|change|correct|edit|fix)\b.*"
    r"\b(?:my\s+)?(?:portfolio|holdings?|positions?)\b",
    re.IGNORECASE | re.DOTALL,
)

_EVENT_RISK_INTENT = re.compile(
    r"\b(?:review|check|assess|scan|flag|identify)\b.*"
    r"\b(?:my\s+)?(?:portfolio|holdings?|positions?)\b.*"
    r"\b(?:earnings?|events?|catalysts?)\b",
    re.IGNORECASE | re.DOTALL,
)

_POSITION_RISK_INTENT = re.compile(
    r"\b(?:review|check|assess|scan|flag|identify)\b.*"
    r"\b(?:my\s+)?(?:portfolio|holdings?|positions?)\b.*"
    r"\b(?:risk|sell|sold|trim|reduce|exit|thesis)\b",
    re.IGNORECASE | re.DOTALL,
)

_CANCEL_PATTERN = re.compile(
    r"\s*(?:cancel|never\s+mind|nevermind|forget\s+it|start\s+over)\s*[.!]?\s*",
    re.IGNORECASE,
)

_LSEG_CAPABILITY_INTENT = re.compile(
    r"\b(?:(?:what\s+(?:can|does)\s+)(?:lseg|refinitiv)|"
    r"(?:lseg|refinitiv)\b[^\n]{0,40}\b(?:capabilit(?:y|ies)|functions?|features?))\b",
    re.IGNORECASE,
)

_SHOW_HOLDINGS_INTENT = re.compile(
    r"(?:^\s*(?:holdings|portfolio|positions)\s*[?.!]*\s*$|"
    r"\b(?:show|list|view|display)\b[^\n]{0,30}\b(?:holdings|portfolio|positions)\b)",
    re.IGNORECASE,
)

_CALCULATE_RETURN_INTENT = re.compile(
    r"\b(?:(?:calculate|show|display)\b[^\n]{0,30}\b(?:portfolio\s+)?(?:return|performance)|"
    r"portfolio\s+(?:return|performance))\b",
    re.IGNORECASE,
)


ProgressCallback = Callable[[int | None, str, str], None]


@dataclass(slots=True)
class PendingTask:
    """Conversation state for a multi-turn operation."""

    kind: str
    original_request: str
    payload: Any = None


@dataclass(slots=True, frozen=True)
class ConversationTurn:
    user: str
    assistant: str
    sequence: int = 0


class OperationalCommand(str, Enum):
    CAPABILITIES = "capabilities"
    SHOW_HOLDINGS = "show_holdings"
    CALCULATE_RETURN = "calculate_return"
    POSITION_RISK = "position_risk"
    RECORD_PURCHASE = "record_purchase"


_EQUITY_CONTEXT_PATTERN = re.compile(
    r"\b(?:stocks?|companies|company|equities|equity|shares?|tickers?|securities|"
    r"investments?|sector|industry|market|valuation|financials?|"
    r"biotech|industrials?)\b",
    re.IGNORECASE,
)


def _is_research_request(text: str, *, semantic_fallback: bool = False) -> bool:
    lower = text.casefold()
    has_search_action = bool(
        re.search(
            r"\b(?:find|hunt\s+for|scout|seek|identify|spot|look\s+for|surface|zero\s+in\s+on|"
            r"pick\s+out|which|show(?:\s+me)?|list)\b",
            lower,
        )
    )
    has_catalogued_taxonomy = bool(detect_sector(text) or detect_industry(text))
    return bool(
        _RESEARCH_PATTERN.search(text)
        or _EQUITY_SEARCH_PATTERN.search(text)
        or (has_search_action and has_catalogued_taxonomy)
        or bool(re.search(r"\bmarket\b[^\n]{0,30}\bnews\b", lower))
        or (semantic_fallback and _EQUITY_CONTEXT_PATTERN.search(text))
    )


def _is_cancel_request(text: str) -> bool:
    return bool(_CANCEL_PATTERN.fullmatch(text))


def _operational_command(text: str) -> OperationalCommand | None:
    if _LSEG_CAPABILITY_INTENT.search(text):
        return OperationalCommand.CAPABILITIES
    if _CALCULATE_RETURN_INTENT.search(text):
        return OperationalCommand.CALCULATE_RETURN
    if _SHOW_HOLDINGS_INTENT.search(text):
        return OperationalCommand.SHOW_HOLDINGS
    if _is_event_risk_request(text):
        return OperationalCommand.POSITION_RISK
    if _PURCHASE_PATTERN.search(text):
        return OperationalCommand.RECORD_PURCHASE
    return None


def _is_event_risk_request(text: str) -> bool:
    lower = text.casefold()
    return bool(
        _EVENT_RISK_INTENT.search(text)
        or _POSITION_RISK_INTENT.search(text)
        or "position risk" in lower
        or "event risk" in lower
        or "earnings risk" in lower
    )


def _is_contextual_chat_follow_up(text: str) -> bool:
    lower = re.sub(r"\s+", " ", text.casefold()).strip()
    return bool(
        re.search(r"\b(it|its|they|their|them|this|that|those)\b", lower)
        or re.match(r"^(?:and|also|why|how|what about|tell me more|go on)\b", lower)
    )


def _groq_status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _is_oversized_groq_request(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return _groq_status_code(exc) == 413 or "request too large" in message


def _friendly_groq_error(exc: BaseException) -> str:
    if _is_oversized_groq_request(exc):
        return (
            "This chat request is too large for the current Groq free-tier limit. "
            "Start a shorter request; your portfolio and saved research were not cleared."
        )
    if _groq_status_code(exc) == 429 or "rate limit" in str(exc).casefold():
        return "Groq's free-tier rate limit was reached. Wait briefly, then try again."
    return "The chat service could not complete this request. Try again shortly."


class StockAgent:
    def __init__(self, settings: Settings, database: PortfolioDatabase) -> None:
        self.settings = settings
        self.database = database
        self._last_research_result: ResearchResult | None = None
        self._screen_refinement_available = False
        self._pending_research_query: str | None = None
        self._pending_research_prior_plan: ResearchPlan | None = None
        self._pending_task: PendingTask | None = None
        self._recent_chat: deque[ConversationTurn] = deque(maxlen=1)
        self._turn_sequence = 0
        self._research_policy = macro_default_policy("Regime incomplete")
        self._market_snapshot: MarketRegimeSnapshot | None = None

    def set_research_policy(self, policy: MacroResearchPolicy) -> None:
        self._research_policy = policy

    def set_market_snapshot(self, snapshot: MarketRegimeSnapshot) -> None:
        self._market_snapshot = snapshot

    def handle(
        self,
        message: str,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Any | None = None,
    ) -> str:
        self._turn_sequence += 1
        text = message.strip()
        if not text:
            return "Enter a request."

        lower = text.casefold()
        command = _operational_command(text)
        if self._pending_task is not None and self._pending_task.kind == "portfolio_update":
            if _is_cancel_request(text):
                self._clear_pending_task()
                return "Okay, I cancelled the portfolio update."
            try:
                updates = parse_portfolio_update_json_message(text)
            except PortfolioImportError as exc:
                return str(exc)
            if updates is not None:
                self._clear_pending_task()
                updated, added = self.database.apply_portfolio_updates(updates)
                return f"Updated {updated} existing position(s) and added {added} new position(s)."
            if not _PORTFOLIO_UPDATE_INTENT.search(text):
                return "Paste the update JSON now, or say cancel."
        if _PORTFOLIO_UPDATE_INTENT.search(text):
            self._set_pending_task("portfolio_update", text)
            self._screen_refinement_available = False
            return (
                "Paste the portfolio update JSON in your next message. Include the ticker and only the fields "
                "you want to change. A new ticker must include quantity and purchase price. Say cancel to stop."
            )
        if _PORTFOLIO_IMPORT_INTENT.search(text) and self._pending_research_query is not None:
            self._clear_pending_task()
        if self._pending_portfolio_import and _is_cancel_request(text):
            self._clear_pending_task()
            return "Okay, I cancelled the portfolio import."
        try:
            portfolio_import = parse_portfolio_json_message(text)
        except PortfolioImportError as exc:
            self._screen_refinement_available = False
            return str(exc)
        if portfolio_import is not None:
            self._clear_pending_task()
            self._screen_refinement_available = False
            count = self.database.record_purchases(portfolio_import.purchases)
            return (
                f"Imported {count} portfolio position(s) into the local portfolio. "
                "Positions without a purchase date were recorded with today's date; "
                "no LSEG research request was run."
            )
        if self._pending_portfolio_import and (
            _is_research_request(text)
            or command is not None
        ):
            self._clear_pending_task()

        if self._pending_portfolio_import:
            return (
                "I’m ready to import your portfolio. Paste the JSON positions now, or say cancel. "
                "Each position needs a ticker/symbol, shares/quantity, and purchase price or average cost."
            )
        if _PORTFOLIO_IMPORT_INTENT.search(text):
            self._set_pending_task("portfolio_import", text)
            self._screen_refinement_available = False
            return (
                "Paste the portfolio JSON in your next message. Each position needs a ticker/symbol, "
                "shares/quantity, and purchase price or average cost. Say cancel to stop."
            )
        operational_command = command is not None
        if self._pending_research_query is not None:
            if _is_cancel_request(text):
                self._pending_research_query = None
                self._pending_research_prior_plan = None
                self._clear_pending_task()
                return "Okay, I discarded the pending research request."
            if _is_research_request(text):
                # An explicit new research instruction replaces the unfinished
                # request. A terse reply instead fills the requested slot.
                self._pending_research_query = None
                self._pending_research_prior_plan = None
                self._clear_pending_task()
            elif not operational_command:
                pending_query = self._pending_research_query
                pending_prior_plan = self._pending_research_prior_plan
                self._pending_research_query = None
                self._pending_research_prior_plan = None
                self._clear_pending_task()
                completed_query = f"{pending_query}\nUser clarification: {text}"
                return self.research(
                    completed_query,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                    prior_plan=pending_prior_plan,
                )

        if command is OperationalCommand.CAPABILITIES:
            self._screen_refinement_available = False
            return capability_answer(text, self.settings.project_root / "data" / "lseg_capabilities.json")
        if self._last_research_result is not None:
            semantic_route = None
            if self.settings.groq_api_key and not operational_command:
                semantic_route = self._semantic_research_follow_up(
                    text,
                    self._last_research_result,
                )
            research_follow_up = (
                semantic_route
                if semantic_route is not None
                else self._is_research_follow_up(text, self._last_research_result)
            )
            if research_follow_up:
                return answer_follow_up(
                    self._last_research_result,
                    text,
                    self.settings,
                )
        if (
            self._last_research_result is not None
            and self._screen_refinement_available
            and not operational_command
            and _is_research_request(text, semantic_fallback=bool(self.settings.groq_api_key))
        ):
            prior_plan = self._last_research_result.plan
            return self.research(
                text,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                prior_plan=prior_plan,
            )
        if command is OperationalCommand.POSITION_RISK:
            self._screen_refinement_available = False
            return self.review_position_risk(progress_callback, cancel_event)
        if _is_research_request(
            text,
            semantic_fallback=bool(self.settings.groq_api_key) and not operational_command,
        ):
            return self.research(text, progress_callback=progress_callback, cancel_event=cancel_event)
        if command is OperationalCommand.SHOW_HOLDINGS:
            self._screen_refinement_available = False
            return self.show_holdings()
        if command is OperationalCommand.CALCULATE_RETURN:
            self._screen_refinement_available = False
            return self.calculate_return()
        if command is OperationalCommand.RECORD_PURCHASE and (match := _PURCHASE_PATTERN.search(text)):
            self._screen_refinement_available = False
            purchase = Purchase(
                ticker=match.group("ticker").upper(),
                quantity=float(match.group("quantity")),
                price=float(match.group("price")),
                purchased_at=date.today(),
                note="Entered through chat",
            )
            self.database.record_purchase(purchase)
            return (
                f"Recorded {purchase.quantity:g} shares of {purchase.ticker} "
                f"at ${purchase.price:,.2f} per share."
            )
        self._screen_refinement_available = False
        return self._general_chat(text)

    def research(
        self,
        query: str,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Any | None = None,
        prior_plan: ResearchPlan | None = None,
    ) -> str:
        def progress(percent: int | None, stage: str, detail: str = "") -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(percent, stage, detail)
            except Exception:
                pass

        progress(2, "Interpreting request", "Translating the request into a constrained research workflow.")
        # A new research attempt invalidates pronoun-based context immediately.
        # If it fails or returns no matches, follow-ups must not discuss a stale
        # company from an earlier request.
        previous_result = self._last_research_result
        previous_refinement_available = self._screen_refinement_available
        self._recent_chat.clear()
        self._pending_research_query = None
        self._pending_research_prior_plan = None
        self._clear_pending_task()
        self._last_research_result = None
        self._screen_refinement_available = False
        try:
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                raise ResearchCancelled("Research stopped by user.")
            plan = build_research_plan(query, self.settings, prior_plan=prior_plan)
            plan.macro_regime = self._research_policy.regime
            plan.research_weights = self._research_policy.weights.as_dict()
            plan.research_weight_source = self._research_policy.source
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                raise ResearchCancelled("Research stopped by user.")
            progress(4, "Research plan ready", f"Workflow: {plan.workflow or plan.mode}.")
            result = run_research(
                plan, self.settings, progress_callback=progress, cancel_event=cancel_event
            )
            progress(97, "Synthesizing report", "Identifying the most important opportunities, catalysts, risks, and contradictions.")
            report = concise_report(result, self.settings, cancel_event=cancel_event)
            self._last_research_result = result
            self._screen_refinement_available = plan.mode == "screen"
            request_count = result.metrics.get("lseg_request_count", len(getattr(result, "call_records", [])))
            progress(
                100,
                "Research complete",
                f"Finished with {request_count} LSEG requests; "
                f"{result.metrics.get('lseg_request_succeeded', 0)} succeeded.",
            )
            return report
        except ResearchCancelled:
            progress(None, "Research stopped", "Stopped by user.")
            return "Research stopped. Partial results were discarded."
        except NotResearchRequest:
            progress(None, "General question", "No LSEG research request was needed.")
            return self._general_chat(query)
        except ResearchClarificationNeeded as exc:
            self._pending_research_query = query
            self._pending_research_prior_plan = prior_plan
            self._set_pending_task("research_clarification", query, prior_plan)
            # A clarification is not a failed replacement screen. Preserve the
            # exact prior screen so a concise answer such as "US-headquartered
            # names" can complete the same contextual request.
            if prior_plan is not None and previous_result is not None:
                self._last_research_result = previous_result
                self._screen_refinement_available = previous_refinement_available
            progress(None, "Clarification needed", str(exc))
            return f"I need one clarification before running LSEG research: {exc}"
        except UnsupportedResearchConstraint as exc:
            progress(None, "Request needs revision", str(exc))
            return f"I did not run an LSEG request because the research constraint could not be compiled safely: {exc}"
        except LSEGNoMatches as exc:
            progress(100, "No validated matches", str(exc))
            return f"No adequately supported company was found after validating the requested constraints. {exc}"
        except AmbiguousInstrumentError as exc:
            progress(None, "Company is ambiguous", str(exc))
            return f"I found multiple possible listed securities. {exc} Specify the ticker, then try again."
        except InstrumentResolutionError as exc:
            progress(None, "Company not found", str(exc))
            return (
                "I couldn't identify the requested listed company or ticker. "
                f"{exc} Check the ticker or include the company name, then try again."
            )
        except Exception as exc:
            progress(None, "Research failed", f"{type(exc).__name__}: {exc}")
            return (
                f"LSEG research could not run: {type(exc).__name__}: {exc}\n"
                "No Yahoo quote snapshot was substituted because it would not answer a screening "
                "or deep-research request. Run Test LSEG.command to verify the Workspace connection."
            )

    @staticmethod
    def _is_research_follow_up(
        text: str,
        result: ResearchResult | None = None,
    ) -> bool:
        lower = text.casefold()
        if is_request_diagnostics_follow_up(text, result):
            return True
        if result is not None and can_answer_follow_up_deterministically(result, text):
            return True
        requested_topics = extract_requested_topics(text)
        topic_follow_up = bool(
            requested_topics
            and not _RESEARCH_PATTERN.search(text)
            and not _EQUITY_SEARCH_PATTERN.search(text)
        )
        return topic_follow_up

    def _semantic_research_follow_up(
        self,
        text: str,
        result: ResearchResult,
    ) -> bool | None:
        """Classify ambiguous context references without granting tool control."""
        if not self.settings.groq_api_key:
            return None
        selected_ric = str(result.metrics.get("selected_ric") or "").strip()
        identities = [
            {
                "ticker": item.ticker,
                "ric": item.ric,
                "company": item.company_name,
                "selected": item.ric == selected_ric if selected_ric else index == 0,
            }
            for index, item in enumerate(result.resolved[:8])
        ]
        if not identities:
            return None
        schema = {
            "title": "PriorResearchContextRoute",
            "description": "Decide whether the current message refers to the prior research result.",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "route": {
                    "type": "string",
                    "enum": ["prior_research_follow_up", "independent_request"],
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["route", "confidence"],
        }
        try:
            from langchain_groq import ChatGroq

            llm = ChatGroq(
                model=self.settings.groq_model,
                temperature=0,
                max_retries=0,
                api_key=self.settings.groq_api_key,
            )
            structured = llm.with_structured_output(
                schema,
                method="json_mode",
                include_raw=False,
            )
            payload = structured.invoke(
                [
                    (
                        "system",
                        "Classify context only. A prior_research_follow_up asks about, expands on, "
                        "or refers implicitly to one or more supplied prior companies or their prior "
                        "research evidence. An independent_request changes subject, names a different "
                        "company, starts fresh research, or is unrelated. Do not answer the request. "
                        "Return only the exact schema.",
                    ),
                    (
                        "human",
                        json.dumps(
                            {
                                "current_message": text[:2_000],
                                "prior_research_identities": identities,
                            },
                            sort_keys=True,
                        ),
                    ),
                ]
            )
            if not isinstance(payload, dict) or set(payload) != {"route", "confidence"}:
                return None
            route = payload.get("route")
            confidence = payload.get("confidence")
            if (
                route in {"prior_research_follow_up", "independent_request"}
                and isinstance(confidence, (int, float))
                and not isinstance(confidence, bool)
                and 0.8 <= float(confidence) <= 1
            ):
                return route == "prior_research_follow_up"
        except Exception:
            pass
        return None

    def show_holdings(self) -> str:
        holdings = self.database.holdings()
        if not holdings:
            return "No purchases are recorded yet."
        lines = ["Current recorded holdings:"]
        for holding in holdings:
            lines.append(
                f"• {holding.ticker}: {holding.quantity:g} shares, "
                f"average cost ${holding.average_cost:,.2f}, total cost ${holding.total_cost:,.2f}"
            )
        return "\n".join(lines)

    def calculate_return(self) -> str:
        holdings = self.database.holdings()
        if not holdings:
            return "No purchases are recorded yet."

        total_cost = 0.0
        total_value = 0.0
        lines = ["Portfolio return using the latest available Yahoo prices:"]
        unavailable: list[str] = []
        for holding in holdings:
            try:
                price = current_price(holding.ticker)
            except Exception:
                price = None
            if price is None:
                unavailable.append(holding.ticker)
                continue
            value = holding.quantity * price
            gain = value - holding.total_cost
            gain_pct = gain / holding.total_cost * 100 if holding.total_cost else 0.0
            total_cost += holding.total_cost
            total_value += value
            lines.append(
                f"• {holding.ticker}: ${value:,.2f} value, {gain:+,.2f} ({gain_pct:+.2f}%)"
            )

        if total_cost:
            total_gain = total_value - total_cost
            total_pct = total_gain / total_cost * 100
            lines.extend(
                [
                    "",
                    f"Total value: ${total_value:,.2f}",
                    f"Total gain/loss: ${total_gain:+,.2f} ({total_pct:+.2f}%)",
                ]
            )
        if unavailable:
            lines.append(f"Prices unavailable for: {', '.join(unavailable)}")
        return "\n".join(lines)

    def review_position_risk(
        self,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Any | None = None,
    ) -> str:
        holdings = self.database.holdings()
        if not holdings:
            return "No portfolio holdings are available for position-risk review."
        snapshot = self._market_snapshot
        if (
            snapshot is None
            or datetime.now(timezone.utc) - snapshot.generated_at > timedelta(minutes=15)
        ):
            if progress_callback is not None:
                progress_callback(
                    2,
                    "Refreshing market regime",
                    "Checking rates, Fed liquidity, inflation, credit, and volatility.",
                )
            snapshot = build_market_regime()
            self._market_snapshot = snapshot
        review = run_portfolio_position_risk_review(
            self.settings,
            holdings,
            macro_snapshot=snapshot,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        return review.to_text()

    def review_event_risk(
        self,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Any | None = None,
    ) -> str:
        return self.review_position_risk(progress_callback, cancel_event)

    @property
    def _pending_portfolio_import(self) -> bool:
        return self._pending_task is not None and self._pending_task.kind == "portfolio_import"

    def _set_pending_task(self, kind: str, original_request: str, payload: Any = None) -> None:
        self._recent_chat.clear()
        self._pending_task = PendingTask(
            kind=kind,
            original_request=original_request,
            payload=payload,
        )

    def _clear_pending_task(self) -> None:
        self._pending_task = None
        self._pending_research_query = None
        self._pending_research_prior_plan = None

    def _analyze_with_groq(self, data_text: str) -> str | None:
        if not self.settings.groq_api_key:
            return None
        try:
            from langchain_groq import ChatGroq

            llm = ChatGroq(
                model=self.settings.groq_model,
                temperature=0,
                max_retries=2,
                api_key=self.settings.groq_api_key,
            )
            response = llm.invoke(
                [
                    (
                        "system",
                        "You are a careful equity research assistant. Use only the supplied data. "
                        "Separate facts from interpretation, mention missing fields, and do not invent a recommendation.",
                    ),
                    (
                        "human",
                        "Summarize the main implications of this LSEG snapshot in 4 to 7 concise bullet points:\n\n"
                        + data_text,
                    ),
                ]
            )
            content = getattr(response, "content", "")
            return str(content).strip() or None
        except Exception as exc:
            return f"Groq analysis was unavailable: {type(exc).__name__}: {exc}"

    def _general_chat(
        self,
        text: str,
    ) -> str:
        if not self.settings.groq_api_key:
            return (
                "I can research a company, record a purchase, show holdings, or calculate return. "
                "Set GROQ_API_KEY in .env for general chat."
            )
        if len(text) > 12_000:
            return (
                "This message is too large for the current Groq free-tier limit. "
                "Shorten it and try again; no application data was cleared."
            )
        try:
            from langchain_groq import ChatGroq

            llm = ChatGroq(
                model=self.settings.groq_model,
                temperature=0.2,
                max_retries=0,
                api_key=self.settings.groq_api_key,
            )
            system_message = (
                "You are a concise stock research assistant. Explain uncertainty clearly. "
                "Do not claim access to live data unless data was supplied by a tool. "
                + self._research_policy.instruction_text()
            )
            use_recent = bool(
                _is_contextual_chat_follow_up(text)
                and self._recent_chat
                and (
                    self._turn_sequence == 0
                    or self._recent_chat[-1].sequence == self._turn_sequence - 1
                )
            )
            messages: list[tuple[str, str]] = [("system", system_message)]
            if use_recent:
                turn = self._recent_chat[-1]
                messages.extend(
                    [
                        ("human", turn.user[-1_500:]),
                        ("assistant", turn.assistant[-3_000:]),
                    ]
                )
            messages.append(("human", text))
            try:
                response = llm.invoke(messages)
            except Exception as exc:
                if not use_recent or not _is_oversized_groq_request(exc):
                    raise
                self._recent_chat.clear()
                response = llm.invoke([("system", system_message), ("human", text)])
            answer = str(getattr(response, "content", response)).strip()
            if answer:
                self._recent_chat.append(
                    ConversationTurn(
                        user=text[-1_500:],
                        assistant=answer[-3_000:],
                        sequence=self._turn_sequence,
                    )
                )
            return answer
        except Exception as exc:
            return _friendly_groq_error(exc)

    @staticmethod
    def _number(value: Any) -> str:
        if value is None:
            return "Unavailable"
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _money(value: Any, currency: str | None) -> str:
        if value is None:
            return "Unavailable"
        currency_label = f" {currency}" if currency else ""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return f"{value}{currency_label}"
        if abs(number) >= 1_000_000_000:
            return f"${number / 1_000_000_000:,.2f}B{currency_label}"
        if abs(number) >= 1_000_000:
            return f"${number / 1_000_000:,.2f}M{currency_label}"
        return f"${number:,.2f}{currency_label}"
