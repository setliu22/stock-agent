"""Natural-language routing for research and portfolio actions."""

from __future__ import annotations

from datetime import date
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
    run_research,
)
from .research_planner import UnsupportedResearchConstraint, build_research_plan
from .market_data import current_price
from .models import Purchase


_RESEARCH_PATTERN = re.compile(
    r"\b(analy[sz]e|research|investigate|review|look\s+up|tell\s+me\s+about|deep\s+dive|compare|screen|screener)\b",
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


class StockAgent:
    def __init__(self, settings: Settings, database: PortfolioDatabase) -> None:
        self.settings = settings
        self.database = database
        self._last_research_result: ResearchResult | None = None

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
        if self._last_research_result is not None and self._is_research_follow_up(text):
            return answer_follow_up(self._last_research_result, text, self.settings)
        if (
            "lseg capabilities" in lower
            or "lseg functions" in lower
            or "what can lseg" in lower
            or "what does lseg" in lower
        ):
            return capability_answer(text, self.settings.project_root / "data" / "lseg_capabilities.json")
        if (
            _RESEARCH_PATTERN.search(text)
            or "market news" in lower
            or (re.search(r"\b(find|show|list)\b", lower) and re.search(r"\b(stocks?|companies|equities)\b", lower))
        ):
            return self.research(text, progress_callback=progress_callback, cancel_event=cancel_event)
        if "show holdings" in lower or lower in {"holdings", "portfolio"}:
            return self.show_holdings()
        if "calculate return" in lower or "portfolio return" in lower:
            return self.calculate_return()
        if match := _PURCHASE_PATTERN.search(text):
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
        return self._general_chat(text)

    def research(
        self,
        query: str,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Any | None = None,
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
        self._last_research_result = None
        try:
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                raise ResearchCancelled("Research stopped by user.")
            plan = build_research_plan(query, self.settings)
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                raise ResearchCancelled("Research stopped by user.")
            progress(4, "Research plan ready", f"Workflow: {plan.workflow or plan.mode}.")
            result = run_research(
                plan, self.settings, progress_callback=progress, cancel_event=cancel_event
            )
            progress(97, "Synthesizing report", "Identifying the most important opportunities, catalysts, risks, and contradictions.")
            report = concise_report(result, self.settings, cancel_event=cancel_event)
            self._last_research_result = result
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
    def _is_research_follow_up(text: str) -> bool:
        lower = text.casefold()
        refers_to_prior_result = bool(
            re.search(r"\b(this|that|the)\s+(company|stock|candidate|pick)\b", lower)
            or re.search(r"\b(it|its)\b", lower)
        )
        research_question = bool(
            re.search(
                r"\b(why|how|what|explain|elaborate|undervalued|valuation|risk|catalyst|selected|promising)\b",
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

    def _general_chat(self, text: str) -> str:
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
                        "Do not claim access to live data unless data was supplied by a tool.",
                    ),
                    ("human", text),
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
