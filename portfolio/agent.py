"""Natural-language routing for research and portfolio actions."""

from __future__ import annotations

from collections import deque
from datetime import date
from dataclasses import dataclass
import os
import re
from typing import Any, Callable

from .company_resolver import AmbiguousInstrumentError, InstrumentResolutionError
from .config import Settings
from .database import PortfolioDatabase
from .event_risk import run_portfolio_event_risk_review
from .lseg_capabilities import capability_answer
from .lseg_research import (
    LSEGNoMatches,
    ResearchCancelled,
    ResearchResult,
    answer_follow_up,
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
)
from .market_data import current_price
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

_AMBIGUOUS_EQUITY_RESEARCH_PATTERN = re.compile(
    r"^(?=[^\n]{1,180}$)"
    r"(?=.*\b(?:find|hunt\s+for|scout|seek|identify|spot|look\s+for|surface|"
    r"zero\s+in\s+on|pick\s+out|which)\b)"
    r"(?=.*\b(?:stocks?|companies|equities|names?|plays?|picks?|candidates?|"
    r"industrials?|biotech|chipmakers?|banks?)\b)"
    r"(?=.*\b(?:good|best|strong|attractive|appealing|promising|compelling|standout|"
    r"underappreciated|undervalued|overlooked|underpriced|mispriced|cheap|discounted|"
    r"industrials?|biotech|technology|tech|healthcare|financials?|energy|utilities|"
    r"chipmakers?|banks?)\b).*$",
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


_EQUITY_CONTEXT_PATTERN = re.compile(
    r"\b(?:stocks?|companies|company|equities|equity|shares?|tickers?|securities|"
    r"investments?|sector|industry|market|valuation|financials?|"
    r"biotech|industrials?)\b",
    re.IGNORECASE,
)


def _is_research_request(text: str, *, semantic_fallback: bool = False) -> bool:
    lower = text.casefold()
    return bool(
        _RESEARCH_PATTERN.search(text)
        or _AMBIGUOUS_EQUITY_RESEARCH_PATTERN.search(text)
        or "market news" in lower
        or (
            re.search(r"\b(find|show|list)\b", lower)
            and re.search(r"\b(stocks?|companies|equities)\b", lower)
        )
        or (semantic_fallback and _EQUITY_CONTEXT_PATTERN.search(text))
    )


def _is_event_risk_request(text: str) -> bool:
    lower = text.casefold()
    return bool(
        _EVENT_RISK_INTENT.search(text)
        or "event risk" in lower
        or "earnings risk" in lower
        or "should be sold" in lower
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
        if self._pending_task is not None and self._pending_task.kind == "portfolio_update":
            if re.fullmatch(r"\s*(?:cancel|never\s+mind|nevermind|forget\s+it|start\s+over)\s*[.!]?\s*", lower):
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
        if self._pending_portfolio_import and re.fullmatch(
            r"\s*(?:cancel|never\s+mind|nevermind|forget\s+it)\s*[.!]?\s*", lower
        ):
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
            or lower in {"holdings", "portfolio"}
            or "show holdings" in lower
            or "calculate return" in lower
            or "portfolio return" in lower
            or _is_event_risk_request(text)
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
        operational_command = bool(
            "lseg capabilities" in lower
            or "lseg functions" in lower
            or "what can lseg" in lower
            or "what does lseg" in lower
            or "show holdings" in lower
            or lower in {"holdings", "portfolio"}
            or "calculate return" in lower
            or "portfolio return" in lower
            or _is_event_risk_request(text)
            or "review portfolio" in lower
            or "should be sold" in lower
            or _PURCHASE_PATTERN.search(text)
        )
        if self._pending_research_query is not None:
            if re.fullmatch(
                r"\s*(?:cancel|never\s+mind|nevermind|forget\s+it|start\s+over)\s*[.!]?\s*",
                lower,
            ):
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

        if (
            "lseg capabilities" in lower
            or "lseg functions" in lower
            or "what can lseg" in lower
            or "what does lseg" in lower
        ):
            self._screen_refinement_available = False
            return capability_answer(text, self.settings.project_root / "data" / "lseg_capabilities.json")
        if self._last_research_result is not None and self._is_research_follow_up(
            text,
            self._last_research_result,
        ):
            return answer_follow_up(self._last_research_result, text, self.settings)
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
        if _is_event_risk_request(text):
            self._screen_refinement_available = False
            return self.review_event_risk(progress_callback, cancel_event)
        if _is_research_request(
            text,
            semantic_fallback=bool(self.settings.groq_api_key) and not operational_command,
        ):
            return self.research(text, progress_callback=progress_callback, cancel_event=cancel_event)
        if "show holdings" in lower or lower in {"holdings", "portfolio"}:
            self._screen_refinement_available = False
            return self.show_holdings()
        if "calculate return" in lower or "portfolio return" in lower:
            self._screen_refinement_available = False
            return self.calculate_return()
        if (
            _is_event_risk_request(text)
        ):
            self._screen_refinement_available = False
            return self.review_event_risk(progress_callback, cancel_event)
        if match := _PURCHASE_PATTERN.search(text):
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
        refers_to_prior_result = bool(
            re.search(r"\b(this|that|the)\s+(company|stock|candidate|pick|one|name)\b", lower)
            or re.search(r"\b(it|its)\b", lower)
        )
        research_question = bool(
            re.search(
                r"\b(why|how|what|explain|elaborate|undervalued|valuation|cheap|inexpensive|"
                r"discount|relative\s+value|risk|catalyst|selected|chosen|promising)\b",
                lower,
            )
        )
        direct_follow_up = bool(
            re.fullmatch(
                r"\s*(?:why\s+(?:is\s+)?(?:it|this company|this stock)?\s*undervalued\??|"
                r"what\s+(?:are|is)\s+(?:the\s+)?(?:major\s+)?(?:risks?|catalysts?)\??|"
                r"what(?:'s|\s+is)\s+(?:the\s+)?(?:risk|catalyst|downside)\??|"
                r"what\s+could\s+go\s+wrong\??|"
                r"why\s+(?:was\s+)?(?:it|this company|this stock)\s+selected\??|"
                r"why\s+(?:this|that)\s+(?:one|name|pick|candidate)\??|"
                r"how\s+was\s+(?:it|this|that)(?:\s+(?:one|name|pick|candidate))?\s+chosen\??|"
                r"why\s+(?:does\s+)?(?:it|this company|this stock)?\s*(?:look|seem)\s+cheap\??|"
                r"why\s+(?:the\s+)?discount\??|"
                r"is\s+(?:the\s+)?valuation\s+(?:really\s+)?(?:attractive|cheap|inexpensive)\??|"
                r"tell\s+me\s+more(?:\s+about\s+(?:it|this company|this stock))?\.?|"
                r"explain\s+(?:the\s+)?valuation\.?)\s*",
                lower,
            )
        )
        return (refers_to_prior_result and research_question) or direct_follow_up

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

    def review_event_risk(
        self,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Any | None = None,
    ) -> str:
        review = run_portfolio_event_risk_review(
            self.settings,
            self.database.holdings(),
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        return review.to_text()

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
                "Do not claim access to live data unless data was supplied by a tool."
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
