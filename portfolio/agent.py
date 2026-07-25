"""Natural-language routing for research and portfolio actions."""

from __future__ import annotations

from datetime import date
import json
import os
import re
from typing import Any, Callable

from .config import Settings
from .database import PortfolioDatabase
from .lseg_capabilities import capability_answer
from .lseg_research import (
    LSEGNoMatches,
    ResearchCancelled,
    ResearchResult,
    answer_follow_up,
    concise_report,
    is_request_diagnostics_follow_up,
    research_context_payload,
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


ProgressCallback = Callable[[int | None, str, str], None]


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


class StockAgent:
    def __init__(self, settings: Settings, database: PortfolioDatabase) -> None:
        self.settings = settings
        self.database = database
        self._last_research_result: ResearchResult | None = None
        self._screen_refinement_available = False
        self._pending_research_query: str | None = None
        self._pending_research_prior_plan: ResearchPlan | None = None

    def handle(
        self,
        message: str,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Any | None = None,
    ) -> str:
        text = message.strip()
        if not text:
            return "Enter a request."

        lower = text.casefold()
        operational_command = bool(
            "lseg capabilities" in lower
            or "lseg functions" in lower
            or "what can lseg" in lower
            or "what does lseg" in lower
            or "show holdings" in lower
            or lower in {"holdings", "portfolio"}
            or "calculate return" in lower
            or "portfolio return" in lower
            or _PURCHASE_PATTERN.search(text)
        )
        if self._pending_research_query is not None:
            if re.fullmatch(
                r"\s*(?:cancel|never\s+mind|nevermind|forget\s+it|start\s+over)\s*[.!]?\s*",
                lower,
            ):
                self._pending_research_query = None
                self._pending_research_prior_plan = None
                return "Okay, I discarded the pending research request."
            if _is_research_request(text):
                # An explicit new research instruction replaces the unfinished
                # request. A terse reply instead fills the requested slot.
                self._pending_research_query = None
                self._pending_research_prior_plan = None
            elif not operational_command:
                pending_query = self._pending_research_query
                pending_prior_plan = self._pending_research_prior_plan
                self._pending_research_query = None
                self._pending_research_prior_plan = None
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
            and self._is_screen_refinement(text, self._last_research_result.plan)
        ):
            # Capture the successful plan before research() deliberately clears
            # stale result context. The planner will inherit only omitted screen
            # constraints and will replace constraints stated in this turn.
            prior_plan = self._last_research_result.plan
            return self.research(
                text,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                prior_plan=prior_plan,
            )
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
        prior_result = self._last_research_result
        self._screen_refinement_available = False
        return self._general_chat(text, research_result=prior_result)

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
        self._pending_research_query = None
        self._pending_research_prior_plan = None
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

    @staticmethod
    def _is_screen_refinement(text: str, prior_plan: ResearchPlan) -> bool:
        """Recognize a new screen turn that refines the last screen.

        Evidence questions such as "why is this company undervalued?" are
        handled separately. This path is only for plural-universe requests, so
        "study Apple stock" remains a fresh company deep dive.
        """
        if prior_plan.mode != "screen":
            return False
        lower = re.sub(r"\s+", " ", text.casefold()).strip()
        explicit_fresh_screen = bool(
            re.search(
                r"\b(?:new|fresh)\s+screen\b|\bstart\s+(?:over|a\s+new\s+screen)\b|"
                r"\bfrom\s+scratch\b|\b(?:just|only)\s+list\b|"
                r"\b(?:global|worldwide|international)\b[^.;,]{0,30}\b(?:stocks|companies|equities)\b|"
                r"\b(?:stocks|companies|equities)\b[^.;,]{0,20}\bglobally\b|"
                r"\ball\b[^.;,]{0,45}\b(?:stocks|companies|equities)\b",
                lower,
            )
        )
        if explicit_fresh_screen:
            return False
        action = bool(
            re.search(
                r"\b(?:study|research|screen|analy[sz]e|examine|assess|evaluate|review|investigate|"
                r"find|list|show(?:\s+me)?|return|display|give\s+me|focus\s+on|narrow\s+to|"
                r"filter\s+(?:for|to)|what\s+about)\b",
                lower,
            )
        )
        plural_universe = bool(re.search(r"\b(?:stocks|companies|equities)\b", lower))
        contextual_universe = bool(
            re.search(r"\b(?:names|ones|candidates|picks)\b", lower)
            or re.search(
                r"\b(?:stateside|domestic|american|u\.?s\.?|united\s+states|canadian|"
                r"british|u\.?k\.?|european|japanese|chinese|australian|indian)\b",
                lower,
            )
        )
        replacement = plural_universe and bool(re.search(r"\binstead\b", lower))
        terse_contextual = bool(
            re.fullmatch(
                r"\s*(?:(?:what\s+about|maybe)\s+)?"
                r"(?:stateside|domestic|american|u\.?s\.?|united\s+states|canadian|british|"
                r"u\.?k\.?|japanese|chinese|indian)"
                r"(?:[- ]headquartered)?\s+(?:names|ones|stocks|companies|equities|picks|candidates)"
                r"(?:\s*,?\s*please)?\??\s*",
                lower,
            )
        )
        numeric_refinement = bool(
            re.search(
                r"\b(?:first|top|show(?:\s+me)?|give\s+me|return|display)\s+\d+\b"
                r"[^.;,]{0,45}\b(?:stocks|companies|equities|names)\b",
                lower,
            )
        )
        contextual_geography = bool(
            re.search(
                r"\b(?:stateside|domestic|american|u\.?s\.?|united\s+states|canadian|"
                r"british|u\.?k\.?|european|japanese|chinese|australian|indian)\b",
                lower,
            )
            and (
                contextual_universe
                or plural_universe
                or re.search(
                    r"\b(?:same|those|them|that|only|headquartered|restrict|focus|"
                    r"how\s+about|what\s+about)\b",
                    lower,
                )
            )
        )
        return (
            (action and (plural_universe or contextual_universe))
            or replacement
            or terse_contextual
            or numeric_refinement
            or contextual_geography
        )

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
        research_result: ResearchResult | None = None,
    ) -> str:
        if not self.settings.groq_api_key:
            return (
                "I can research a company, record a purchase, show holdings, or calculate return. "
                "Set GROQ_API_KEY in .env for general chat."
            )
        try:
            from langchain_groq import ChatGroq

            llm = ChatGroq(
                model=self.settings.groq_model,
                temperature=0.2,
                max_retries=2,
                api_key=self.settings.groq_api_key,
            )
            response = llm.invoke(
                [
                    (
                        "system",
                        "You are a concise stock research assistant. Explain uncertainty clearly. "
                        "Do not claim access to live data unless data was supplied by a tool. "
                        "When prior LSEG research context is supplied, use it for any relevant "
                        "follow-up and never claim the conversation just started or lacks context "
                        "that is present there.",
                    ),
                    (
                        "human",
                        (
                            f"PRIOR LSEG RESEARCH CONTEXT:\n"
                            f"{json.dumps(research_context_payload(research_result), default=str)}\n\n"
                            f"CURRENT USER MESSAGE:\n{text}"
                            if research_result is not None
                            else text
                        ),
                    ),
                ]
            )
            return str(getattr(response, "content", response)).strip()
        except Exception as exc:
            return f"Groq request failed: {type(exc).__name__}: {exc}"

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
