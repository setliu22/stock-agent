"""Tkinter desktop interface for Stock Agent."""

from __future__ import annotations

from datetime import date, datetime
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from portfolio.config import save_supabase_settings
from portfolio.controller import StockAgentController
from portfolio.cloud_portfolios import AuthResult, friendly_auth_error
from portfolio.company_resolver import InstrumentResolutionError
from portfolio.lseg_research import LSEGResearchError, ResearchCancelled
from portfolio.market_regime import (
    DEFENSIVE_MACRO_TILT,
    MACRO_REFERENCE_ROWS,
    MarketRegimeSnapshot,
    NEUTRAL_MACRO_TILT,
    TOLERANT_MACRO_TILT,
)
from portfolio.research_lab import (
    ANALYSES,
    CAPABILITIES,
    DISCOVERY_CORE_CAPABILITY_IDS,
    ApprovedResearchPlan,
    ResearchLabError,
    ResearchProposal,
    research_discovery_scope_options,
)
from portfolio.research_plan import (
    exchange_geography_options,
    supported_research_taxonomy_options,
)


EXPECTED_RESEARCH_ERRORS = (
    InstrumentResolutionError,
    LSEGResearchError,
    ResearchLabError,
)

RESEARCH_EXAMPLE_QUESTIONS = (
    "What undervalued companies have meaningful exposure to data-center construction?",
    "What European semiconductor companies look undervalued?",
    "Compare AAPL and MSFT on valuation, profitability, balance-sheet strength, and earnings expectations.",
    "Which of AAPL, MSFT, and NVDA performed best when the 10-year Treasury yield fell?",
    "What recent Reuters developments are materially affecting semiconductor stocks?",
)


def friendly_research_error(error: BaseException) -> str:
    """Keep actionable research failures readable while preserving unknown diagnostics."""
    if isinstance(error, EXPECTED_RESEARCH_ERRORS):
        return str(error)
    return f"Unexpected error: {type(error).__name__}: {error}"


def tab_drag_target(
    current_index: int,
    tab_count: int,
    horizontal_delta: int,
    *,
    threshold: int = 44,
) -> int | None:
    """Return the adjacent page selected by a horizontal tab-strip drag."""
    if abs(horizontal_delta) < threshold or tab_count < 2:
        return None
    step = 1 if horizontal_delta > 0 else -1
    target = max(0, min(tab_count - 1, current_index + step))
    return target if target != current_index else None


NUMERIC_PORTFOLIO_COLUMNS = frozenset(
    {
        "quantity",
        "average_cost",
        "total_cost",
        "current_price",
        "market_value",
        "gain_loss",
        "return_percent",
    }
)


def sort_portfolio_rows(
    rows: list[dict[str, Any]],
    column: str,
    *,
    descending: bool,
) -> list[dict[str, Any]]:
    """Sort portfolio rows by raw values while keeping missing values last."""
    if column not in NUMERIC_PORTFOLIO_COLUMNS and column != "ticker":
        return list(rows)
    present = [row for row in rows if row.get(column) is not None]
    missing = [row for row in rows if row.get(column) is None]
    if column in NUMERIC_PORTFOLIO_COLUMNS:
        present.sort(key=lambda row: float(row[column]), reverse=descending)
    else:
        present.sort(key=lambda row: str(row[column]).casefold(), reverse=descending)
    return present + missing


def _center_dialog(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    dialog.update_idletasks()
    width = dialog.winfo_width()
    height = dialog.winfo_height()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    dialog.geometry(f"+{x}+{y}")


def _select_all_text(widget: tk.Text) -> str:
    widget.tag_add("sel", "1.0", "end-1c")
    widget.mark_set("insert", "end-1c")
    widget.see("insert")
    return "break"


def _collapse_text_selection(widget: tk.Text, *, to_end: bool) -> str | None:
    ranges = widget.tag_ranges("sel")
    if not ranges:
        return None
    widget.mark_set("insert", ranges[1] if to_end else ranges[0])
    widget.tag_remove("sel", "1.0", "end")
    widget.see("insert")
    return "break"


class PurchaseDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Record purchase")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: tuple[str, float, float, date, str] | None = None

        self.security = tk.StringVar()
        self.quantity = tk.StringVar()
        self.price = tk.StringVar()
        self.purchase_date = tk.StringVar(value=date.today().isoformat())
        self.note = tk.StringVar()

        fields = [
            ("Company or ticker", self.security),
            ("Shares", self.quantity),
            ("Price per share", self.price),
            ("Date (YYYY-MM-DD)", self.purchase_date),
            ("Note", self.note),
        ]
        frame = ttk.Frame(self, padding=18)
        frame.grid(sticky="nsew")
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            entry = ttk.Entry(frame, textvariable=variable, width=34)
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            if row == 0:
                entry.focus_set()

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right")
        self.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self.destroy())

    def _save(self) -> None:
        try:
            security = self.security.get().strip()
            if not security:
                raise ValueError("Enter a company or ticker.")
            quantity = float(self.quantity.get())
            price = float(self.price.get())
            purchased_at = date.fromisoformat(self.purchase_date.get().strip())
            if quantity <= 0:
                raise ValueError("Shares must be greater than zero.")
            if price < 0:
                raise ValueError("Price cannot be negative.")
        except ValueError as exc:
            messagebox.showerror("Invalid purchase", str(exc), parent=self)
            return
        self.result = (security, quantity, price, purchased_at, self.note.get().strip())
        self.destroy()


class PurchaseMethodDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Record purchase")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: str | None = None

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="How would you like to add purchases?", style="Section.TLabel").pack(
            anchor="w", pady=(0, 16)
        )
        ttk.Button(
            frame,
            text="Enter one purchase",
            style="Accent.TButton",
            command=lambda: self._choose("manual"),
        ).pack(fill="x", pady=(0, 8))
        ttk.Button(
            frame,
            text="Import bulk JSON (AI-assisted)",
            command=lambda: self._choose("json"),
        ).pack(fill="x")
        ttk.Button(frame, text="Cancel", command=self.destroy).pack(anchor="e", pady=(16, 0))
        self.bind("<Escape>", lambda _event: self.destroy())

    def _choose(self, value: str) -> None:
        self.result = value
        self.destroy()


class PortfolioJsonDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Import portfolio JSON")
        self.geometry("700x470")
        self.minsize(560, 360)
        self.transient(parent)
        self.grab_set()
        self.result: str | None = None

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Portfolio JSON", style="Section.TLabel").pack(anchor="w")
        self.editor = ScrolledText(
            frame,
            wrap="none",
            font=("TkFixedFont", 10),
            background=StockAgentApp.SURFACE,
            foreground=StockAgentApp.TEXT,
            insertbackground=StockAgentApp.TEXT,
            selectbackground="#285665",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=StockAgentApp.BORDER,
            padx=12,
            pady=10,
        )
        self.editor.pack(fill="both", expand=True, pady=(8, 14))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Import", style="Accent.TButton", command=self._submit).pack(
            side="right"
        )
        self.editor.focus_set()
        self.bind("<Escape>", lambda _event: self.destroy())

    def _submit(self) -> None:
        value = self.editor.get("1.0", "end").strip()
        if not value:
            messagebox.showerror("JSON required", "Paste portfolio JSON to import.", parent=self)
            return
        self.result = value
        self.destroy()


class IndustryResearchDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, options: tuple[tuple[str, str], ...]) -> None:
        super().__init__(parent)
        self.title("Start industry research")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(background=StockAgentApp.BG)
        self.result: tuple[str, int] | None = None
        self.option_values = dict(options)
        self.industry = tk.StringVar()
        self.stock_count = tk.IntVar(value=5)

        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Start industry research", style="DialogTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="Choose a market category and result count.",
            style="DialogMuted.TLabel",
        ).pack(anchor="w", pady=(4, 18))

        form = ttk.Frame(frame, style="Panel.TFrame", padding=18)
        form.pack(fill="x")
        ttk.Label(form, text="Industry or sector", style="DialogLabel.TLabel").pack(anchor="w")
        self.category = ttk.Combobox(
            form,
            textvariable=self.industry,
            values=tuple(self.option_values),
            state="readonly",
            width=48,
        )
        self.category.pack(fill="x", pady=(8, 16))
        ttk.Label(form, text="Stocks to return", style="DialogLabel.TLabel").pack(anchor="w")
        count = ttk.Combobox(
            form,
            textvariable=self.stock_count,
            values=tuple(range(1, 21)),
            state="readonly",
            width=7,
        )
        count.pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text="Start research", style="Accent.TButton", command=self._submit).pack(
            side="right"
        )
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 8))
        self.category.focus_set()
        self.bind("<Return>", lambda _event: self._submit())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after_idle(lambda: _center_dialog(self, parent))

    def _submit(self) -> None:
        selected = self.industry.get().strip()
        industry = self.option_values.get(selected, "")
        try:
            count = int(self.stock_count.get())
        except (TypeError, ValueError):
            count = 0
        if not industry:
            messagebox.showerror("Industry required", "Select an industry or sector.", parent=self)
            return
        if not 1 <= count <= 20:
            messagebox.showerror("Invalid range", "Choose between 1 and 20 stocks.", parent=self)
            return
        self.result = (industry, count)
        self.destroy()


class ResearchExamplesDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Research examples")
        self.resizable(False, False)
        self.transient(parent)
        self.configure(background=StockAgentApp.BG)
        self.result: str | None = None

        frame = ttk.Frame(self, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Example questions", style="DialogTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="These examples match workflows the Research Lab can execute with validated data.",
            style="DialogMuted.TLabel",
        ).pack(anchor="w", pady=(4, 14))
        self.examples = tk.Listbox(
            frame,
            width=108,
            height=len(RESEARCH_EXAMPLE_QUESTIONS),
            background=StockAgentApp.SURFACE_ALT,
            foreground=StockAgentApp.TEXT,
            selectbackground="#285665",
            selectforeground=StockAgentApp.TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=StockAgentApp.BORDER,
            activestyle="none",
            font=(getattr(parent, "ui_font", "TkDefaultFont"), 10),
        )
        for question in RESEARCH_EXAMPLE_QUESTIONS:
            self.examples.insert("end", question)
        self.examples.selection_set(0)
        self.examples.pack(fill="x")
        self.examples.bind("<Double-Button-1>", lambda _event: self._submit())

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(
            buttons,
            text="Use example",
            style="Accent.TButton",
            command=self._submit,
        ).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )
        self.bind("<Return>", lambda _event: self._submit())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.grab_set()
        self.after_idle(lambda: _center_dialog(self, parent))

    def _submit(self) -> None:
        selected = self.examples.curselection()
        if not selected:
            return
        self.result = RESEARCH_EXAMPLE_QUESTIONS[selected[0]]
        self.destroy()


class ResearchApprovalDialog(tk.Toplevel):
    TIMEFRAMES = {
        "3 months": 90,
        "6 months": 180,
        "1 year": 365,
        "2 years": 730,
        "5 years": 1825,
    }

    def __init__(self, parent: tk.Misc, proposal: ResearchProposal) -> None:
        super().__init__(parent)
        self.title("Approve research plan")
        self.geometry("1040x720")
        self.minsize(940, 640)
        self.transient(parent)
        self.configure(background=StockAgentApp.BG)
        self.result: ApprovedResearchPlan | None = None
        self.proposal = proposal
        self.ui_font = getattr(parent, "ui_font", "TkDefaultFont")
        self.discovery_mode = proposal.mode == "discovery"
        self.market_news_mode = proposal.mode == "market_news"
        self.securities = tk.StringVar(value="; ".join(proposal.securities))
        self.proposed_discovery_scopes = proposal.discovery_scopes or (
            (proposal.discovery_scope,) if proposal.discovery_scope else ()
        )
        self.discovery_scope_reasons = {
            item.item_id: item.reason for item in proposal.discovery_scope_reasons
        }
        self.exchange_geography = tk.StringVar(
            value=proposal.exchange_geography or "All exchanges"
        )
        self.result_count = tk.IntVar(value=proposal.result_count)
        self.benchmark = tk.StringVar(value=proposal.benchmark or "")
        timeframe_label = next(
            (
                label
                for label, days in self.TIMEFRAMES.items()
                if days == proposal.lookback_days
            ),
            f"{proposal.lookback_days} days",
        )
        self.timeframe = tk.StringVar(value=timeframe_label)
        selected_capabilities = {item.item_id for item in proposal.capabilities}
        selected_analyses = {item.item_id for item in proposal.analyses}
        self.required_capabilities = {
            item.capability_id
            for item in CAPABILITIES
            if item.required
            or (
                self.discovery_mode
                and item.capability_id in DISCOVERY_CORE_CAPABILITY_IDS
            )
            or (self.market_news_mode and item.capability_id == "market_news")
        }
        capability_order = {
            capability_id: index
            for index, capability_id in enumerate(DISCOVERY_CORE_CAPABILITY_IDS)
        }
        visible_capabilities = [
            item
            for item in CAPABILITIES
            if proposal.mode in item.modes
            and (
                item.capability_id != "benchmark_prices"
                or proposal.benchmark is not None
            )
        ]
        self.visible_capabilities = tuple(
            sorted(
                visible_capabilities,
                key=lambda item: (
                    item.capability_id not in capability_order,
                    capability_order.get(item.capability_id, len(CAPABILITIES)),
                    CAPABILITIES.index(item),
                ),
            )
        )
        visible_capability_ids = {item.capability_id for item in self.visible_capabilities}
        self.visible_analyses = tuple(
            item
            for item in ANALYSES
            if set(item.required_capabilities) <= visible_capability_ids
        )
        self.capability_vars = {
            item.capability_id: tk.BooleanVar(
                value=item.capability_id in self.required_capabilities
                or item.capability_id in selected_capabilities
            )
            for item in CAPABILITIES
        }
        self.analysis_vars = {
            item.analysis_id: tk.BooleanVar(value=item.analysis_id in selected_analyses)
            for item in ANALYSES
        }

        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Approve research plan", style="DialogTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            outer,
            text=proposal.question,
            style="DialogMuted.TLabel",
            wraplength=880,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(4, 14))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 14))
        if self.discovery_mode:
            ttk.Label(controls, text="Discovery universes", style="DialogMuted.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            ttk.Label(controls, text="Results", style="DialogMuted.TLabel").grid(
                row=0, column=2, sticky="w", padx=(12, 0)
            )
            ttk.Label(
                controls,
                text="Exchange market",
                style="DialogMuted.TLabel",
            ).grid(
                row=0, column=1, sticky="w", padx=(12, 0)
            )
            self.discovery_scope_values = [
                value for _label, value in research_discovery_scope_options()
            ]
            self.discovery_scope_list = tk.Listbox(
                controls,
                selectmode="multiple",
                exportselection=False,
                height=4,
                background=StockAgentApp.SURFACE_ALT,
                foreground=StockAgentApp.TEXT,
                selectbackground="#285665",
                selectforeground=StockAgentApp.TEXT,
                relief="flat",
                highlightthickness=1,
                highlightbackground=StockAgentApp.BORDER,
                font=(self.ui_font, 10),
            )
            for index, value in enumerate(self.discovery_scope_values):
                self.discovery_scope_list.insert("end", value)
                if value in self.proposed_discovery_scopes:
                    self.discovery_scope_list.selection_set(index)
            if self.proposed_discovery_scopes:
                first_selected = self.discovery_scope_values.index(
                    self.proposed_discovery_scopes[0]
                )
                self.discovery_scope_list.see(first_selected)
            self.discovery_scope_list.grid(row=1, column=0, sticky="ew", pady=(5, 0))
            ttk.Combobox(
                controls,
                textvariable=self.exchange_geography,
                values=[
                    "All exchanges",
                    *[label for label, _value in exchange_geography_options()],
                ],
                state="readonly",
            ).grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(5, 0))
            ttk.Spinbox(
                controls,
                from_=1,
                to=8,
                textvariable=self.result_count,
                width=8,
            ).grid(row=1, column=2, sticky="ew", padx=(12, 0), pady=(5, 0))
            timeframe_column = 3
            benchmark_column = 4 if proposal.benchmark else None
        elif not self.market_news_mode:
            ttk.Label(controls, text="Securities (separate with ;)", style="DialogMuted.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            ttk.Entry(controls, textvariable=self.securities).grid(
                row=1, column=0, sticky="ew", pady=(5, 0)
            )
            timeframe_column = 1
            benchmark_column = 2 if proposal.benchmark else None
        else:
            timeframe_column = 0
            benchmark_column = None
        ttk.Label(controls, text="Timeframe", style="DialogMuted.TLabel").grid(
            row=0, column=timeframe_column, sticky="w", padx=(12, 0)
        )
        if benchmark_column is not None:
            ttk.Label(
                controls,
                text="Comparison benchmark",
                style="DialogMuted.TLabel",
            ).grid(
                row=0, column=benchmark_column, sticky="w", padx=(12, 0)
            )
        timeframe_values = list(self.TIMEFRAMES)
        if timeframe_label not in timeframe_values:
            timeframe_values.append(timeframe_label)
        ttk.Combobox(
            controls,
            textvariable=self.timeframe,
            values=timeframe_values,
            state="readonly",
            width=14,
        ).grid(row=1, column=timeframe_column, sticky="ew", padx=(12, 0), pady=(5, 0))
        if benchmark_column is not None:
            ttk.Entry(controls, textvariable=self.benchmark, width=18).grid(
                row=1, column=benchmark_column, sticky="ew", padx=(12, 0), pady=(5, 0)
            )
        controls.columnconfigure(0, weight=1)
        if self.discovery_scope_reasons:
            rationale = "  |  ".join(
                f"{scope}: {self.discovery_scope_reasons[scope]}"
                for scope in self.proposed_discovery_scopes
                if scope in self.discovery_scope_reasons
            )
            ttk.Label(
                outer,
                text="Planner coverage: " + rationale,
                style="DialogMuted.TLabel",
                wraplength=960,
                justify="left",
            ).pack(fill="x", anchor="w", pady=(0, 12))
        if proposal.selection_objectives:
            labels = {
                "relative_value": "Require peer-relative value evidence",
                "positive_signals": "Require positive evidence from multiple families",
            }
            ttk.Label(
                outer,
                text="Candidate rules: "
                + "  |  ".join(labels[item] for item in proposal.selection_objectives),
                style="DialogMuted.TLabel",
                wraplength=960,
                justify="left",
            ).pack(fill="x", anchor="w", pady=(0, 12))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        data_frame = ttk.Frame(body)
        data_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        analysis_frame = ttk.Frame(body)
        analysis_frame.grid(row=0, column=1, sticky="nsew")
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        ttk.Label(
            data_frame,
            text="Data to retrieve · core evidence first",
            style="Section.TLabel",
        ).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        reasons = {item.item_id: item.reason for item in proposal.capabilities}
        split = (len(self.visible_capabilities) + 1) // 2
        optional_heading_added = False
        for index, capability in enumerate(self.visible_capabilities):
            row = ttk.Frame(data_frame)
            row.grid(
                row=index % split + 1,
                column=index // split,
                sticky="new",
                padx=(0, 12),
                pady=(7, 0),
            )
            if (
                capability.capability_id not in self.required_capabilities
                and not optional_heading_added
            ):
                ttk.Label(row, text="Optional data", style="Section.TLabel").pack(
                    anchor="w", pady=(0, 5)
                )
                optional_heading_added = True
            check = ttk.Checkbutton(
                row,
                text=(
                    f"{capability.label} · {capability.source}"
                    + (
                        " · Always included"
                        if capability.capability_id in self.required_capabilities
                        else ""
                    )
                ),
                variable=self.capability_vars[capability.capability_id],
                command=lambda capability_id=capability.capability_id: self._enforce_exclusive_capability(
                    capability_id
                ),
            )
            check.pack(anchor="w")
            if capability.capability_id in self.required_capabilities:
                check.configure(state="disabled")
            reason = reasons.get(capability.capability_id)
            if reason and capability.capability_id not in self.required_capabilities:
                ttk.Label(
                    row,
                    text=reason,
                    style="Muted.TLabel",
                    wraplength=245,
                    justify="left",
                ).pack(anchor="w", padx=(24, 0))

        ttk.Label(
            analysis_frame,
            text="Optional calculations · question-specific",
            style="Section.TLabel",
        ).pack(anchor="w")
        analysis_reasons = {item.item_id: item.reason for item in proposal.analyses}
        for analysis in self.visible_analyses:
            row = ttk.Frame(analysis_frame)
            row.pack(fill="x", pady=(7, 0))
            ttk.Checkbutton(
                row,
                text=analysis.label,
                variable=self.analysis_vars[analysis.analysis_id],
            ).pack(anchor="w")
            reason = analysis_reasons.get(analysis.analysis_id)
            if reason:
                ttk.Label(
                    row,
                    text=reason,
                    style="Muted.TLabel",
                    wraplength=400,
                    justify="left",
                ).pack(anchor="w", padx=(24, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(
            buttons,
            text="Run approved plan",
            style="Accent.TButton",
            command=self._submit,
        ).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )
        self.bind("<Escape>", lambda _event: self.destroy())
        self.grab_set()
        self.after_idle(lambda: _center_dialog(self, parent))

    def _enforce_exclusive_capability(self, selected_id: str) -> None:
        if not self.capability_vars[selected_id].get():
            return
        for analysis in ANALYSES:
            if selected_id not in analysis.exactly_one_capability:
                continue
            for capability_id in analysis.exactly_one_capability:
                if capability_id != selected_id:
                    self.capability_vars[capability_id].set(False)

    def _submit(self) -> None:
        securities = tuple(
            item.strip()
            for item in re.split(r"[;\n]", self.securities.get())
            if item.strip()
        )
        timeframe_text = self.timeframe.get().strip()
        if timeframe_text in self.TIMEFRAMES:
            lookback_days = self.TIMEFRAMES[timeframe_text]
        else:
            match = re.fullmatch(r"(\d+)\s+days", timeframe_text)
            lookback_days = int(match.group(1)) if match else 0
        try:
            result_count = int(self.result_count.get()) if self.discovery_mode else 5
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror(
                "Invalid result count",
                "Choose between one and eight discovery results.",
                parent=self,
            )
            return
        selected_scopes = (
            tuple(
                self.discovery_scope_values[index]
                for index in self.discovery_scope_list.curselection()
            )
            if self.discovery_mode
            else ()
        )
        approved = ApprovedResearchPlan(
            question=self.proposal.question,
            securities=() if self.discovery_mode else securities,
            lookback_days=lookback_days,
            benchmark=self.benchmark.get().strip() or None,
            capability_ids=tuple(
                item.capability_id
                for item in CAPABILITIES
                if self.capability_vars[item.capability_id].get()
            ),
            analysis_ids=tuple(
                item.analysis_id
                for item in ANALYSES
                if self.analysis_vars[item.analysis_id].get()
            ),
            mode=self.proposal.mode,
            discovery_scope=(selected_scopes[0] if selected_scopes else None),
            discovery_scopes=selected_scopes,
            exchange_geography=(
                self.exchange_geography.get().strip()
                if self.exchange_geography.get().strip() != "All exchanges"
                else None
            ),
            discovery_theme=self.proposal.discovery_theme,
            result_count=result_count,
            selection_objectives=self.proposal.selection_objectives,
        )
        try:
            self.result = approved.validated()
        except ResearchLabError as exc:
            messagebox.showerror("Incomplete research plan", str(exc), parent=self)
            return
        self.destroy()


class PositionRiskDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, tickers: list[str]) -> None:
        super().__init__(parent)
        self.title("Review portfolio risk")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(background=StockAgentApp.BG)
        self.result: tuple[list[str], str | None] | None = None
        self.scope = tk.StringVar(value="all")
        self.selections = {ticker: tk.BooleanVar(value=False) for ticker in tickers}
        self.objective = tk.StringVar()
        self.horizon = tk.StringVar()
        self.exit_conditions = tk.StringVar()

        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Review position risk", style="DialogTitle.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Select the positions to include.",
            style="DialogMuted.TLabel",
        ).pack(anchor="w", pady=(4, 18))

        scope_panel = ttk.Frame(frame, style="Panel.TFrame", padding=16)
        scope_panel.pack(fill="x")
        ttk.Radiobutton(
            scope_panel,
            text=f"Entire portfolio ({len(tickers)} stocks)",
            variable=self.scope,
            value="all",
            command=self._update_scope,
            style="Dialog.TRadiobutton",
        ).pack(anchor="w")
        ttk.Radiobutton(
            scope_panel,
            text="Specific stocks",
            variable=self.scope,
            value="specific",
            command=self._update_scope,
            style="Dialog.TRadiobutton",
        ).pack(anchor="w", pady=(10, 0))

        ttk.Separator(scope_panel).pack(fill="x", pady=14)
        self.stock_frame = ttk.Frame(scope_panel, style="Surface.TFrame")
        self.stock_frame.pack(fill="x")
        self.stock_checks: list[ttk.Checkbutton] = []
        for index, ticker in enumerate(tickers):
            check = ttk.Checkbutton(
                self.stock_frame,
                text=ticker,
                variable=self.selections[ticker],
                style="Dialog.TCheckbutton",
            )
            check.grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 28), pady=5)
            self.stock_checks.append(check)

        context_panel = ttk.Frame(frame, style="Panel.TFrame", padding=16)
        context_panel.pack(fill="x", pady=(12, 0))
        ttk.Label(
            context_panel,
            text="Optional decision context",
            style="SurfaceSection.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            context_panel,
            text="Used for this review only, sent to Groq for interpretation, and not saved as a thesis.",
            style="DialogMuted.TLabel",
            wraplength=650,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 10))
        for row, (label, variable) in enumerate(
            (
                ("Holding objective", self.objective),
                ("Expected horizon", self.horizon),
                ("Exit or review conditions", self.exit_conditions),
            ),
            start=2,
        ):
            ttk.Label(context_panel, text=label, style="DialogMuted.TLabel").grid(
                row=row, column=0, sticky="w", pady=4
            )
            ttk.Entry(context_panel, textvariable=variable, width=54).grid(
                row=row, column=1, sticky="ew", padx=(12, 0), pady=4
            )
        context_panel.columnconfigure(1, weight=1)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text="Start review", style="Accent.TButton", command=self._submit).pack(
            side="right"
        )
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 8))
        self._update_scope()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after_idle(lambda: _center_dialog(self, parent))

    def _update_scope(self) -> None:
        state = "normal" if self.scope.get() == "specific" else "disabled"
        for check in self.stock_checks:
            check.configure(state=state)

    def _submit(self) -> None:
        if self.scope.get() == "all":
            tickers = list(self.selections)
        else:
            tickers = [ticker for ticker, selected in self.selections.items() if selected.get()]
            if not tickers:
                messagebox.showerror(
                    "Select stocks", "Select at least one portfolio stock.", parent=self
                )
                return
        context_parts = [
            f"Objective: {self.objective.get().strip()}" if self.objective.get().strip() else "",
            f"Expected horizon: {self.horizon.get().strip()}" if self.horizon.get().strip() else "",
            (
                f"Exit or review conditions: {self.exit_conditions.get().strip()}"
                if self.exit_conditions.get().strip()
                else ""
            ),
        ]
        self.result = (tickers, "; ".join(item for item in context_parts if item) or None)
        self.destroy()


def period_performance(history: list[Any], sessions: int) -> tuple[float, float] | None:
    """Return value and percentage change over the selected history window."""
    points = history[-(sessions + 1):]
    if len(points) < 2 or points[0].market_value == 0:
        return None
    change = points[-1].market_value - points[0].market_value
    return change, change / points[0].market_value * 100


def _performance_time_label(value: date | datetime, *, end: bool = False) -> str:
    if isinstance(value, datetime):
        return value.strftime("%-I:%M %p" if end else "%b %-d %-I:%M %p")
    return value.strftime("%b %-d, %Y")


class StockAgentApp(tk.Tk):
    BG = "#0D0F12"
    SURFACE = "#15181D"
    SURFACE_ALT = "#1B1F25"
    BORDER = "#2B3139"
    TEXT = "#F2F4F7"
    MUTED = "#929AA6"
    ACCENT = "#5AC8E8"
    POSITIVE = "#34C759"
    NEGATIVE = "#FF453A"
    WARNING = "#FFD60A"

    def __init__(self) -> None:
        super().__init__()
        self.title("Stock Agent")
        self.geometry("1280x820")
        self.minsize(1100, 700)
        self.controller = StockAgentController()
        self.auth_busy = False
        self.results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.research_started_at: float | None = None
        self.current_progress = 0
        self.current_stage = "Idle"
        self.current_detail = ""
        self.research_progress_seen = False
        self._last_elapsed_second = -1
        self.current_step_started_at: float | None = None
        self.is_busy = False
        self.current_task_cancellable = False
        self.stop_requested = False
        self.cancel_event: threading.Event | None = None
        self.market_refresh_busy = False
        self.portfolio_refresh_busy = False
        self.portfolio_refresh_generation = 0
        self.current_holdings: list[Any] = []
        self.delete_buttons: dict[str, ttk.Button] = {}
        self.holdings_sort_column: str | None = None
        self.holdings_sort_descending = False
        self._tab_drag_anchor_x: int | None = None
        families = set(tkfont.families(self))
        self.ui_font = next(
            (name for name in ("SF Pro Text", "Helvetica Neue", "Arial", "DejaVu Sans") if name in families),
            "TkDefaultFont",
        )
        self.mono_font = next(
            (name for name in ("SF Mono", "Menlo", "DejaVu Sans Mono") if name in families),
            "TkFixedFont",
        )
        self._configure_style()
        self._build_ui()
        self.after(100, self._poll_results)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.configure(background=self.BG)
        style.configure("TFrame", background=self.BG)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT, font=(self.ui_font, 11))
        style.configure("AppTitle.TLabel", background=self.BG, foreground=self.TEXT, font=(self.ui_font, 15, "bold"))
        style.configure("Title.TLabel", background=self.BG, foreground=self.TEXT, font=(self.ui_font, 22, "bold"))
        style.configure("Section.TLabel", background=self.BG, foreground=self.TEXT, font=(self.ui_font, 13, "bold"))
        style.configure("SurfaceSection.TLabel", background=self.SURFACE, foreground=self.TEXT, font=(self.ui_font, 13, "bold"))
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED, font=(self.ui_font, 10))
        style.configure(
            "DialogTitle.TLabel",
            background=self.BG,
            foreground=self.TEXT,
            font=(self.ui_font, 18, "bold"),
        )
        style.configure(
            "DialogMuted.TLabel",
            background=self.BG,
            foreground=self.MUTED,
            font=(self.ui_font, 10),
        )
        style.configure(
            "DialogLabel.TLabel",
            background=self.SURFACE,
            foreground=self.MUTED,
            font=(self.ui_font, 9, "bold"),
        )
        style.configure("Surface.TFrame", background=self.SURFACE)
        style.configure(
            "Panel.TFrame",
            background=self.SURFACE,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
            borderwidth=1,
            relief="solid",
        )
        style.configure("MetricLabel.TLabel", background=self.SURFACE, foreground=self.MUTED, font=(self.ui_font, 9))
        style.configure("MetricValue.TLabel", background=self.SURFACE, foreground=self.TEXT, font=(self.ui_font, 17, "bold"))
        style.configure("Positive.MetricValue.TLabel", background=self.SURFACE, foreground=self.POSITIVE, font=(self.ui_font, 17, "bold"))
        style.configure("Negative.MetricValue.TLabel", background=self.SURFACE, foreground=self.NEGATIVE, font=(self.ui_font, 17, "bold"))
        style.configure("ProgressTitle.TLabel", background=self.SURFACE, font=(self.ui_font, 10, "bold"))
        style.configure("ProgressDetail.TLabel", background=self.SURFACE, foreground=self.MUTED, font=(self.ui_font, 9))
        style.configure(
            "TButton",
            background=self.SURFACE_ALT,
            foreground=self.TEXT,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
            borderwidth=1,
            relief="flat",
            padding=(12, 7),
            font=(self.ui_font, 10),
        )
        style.map("TButton", background=[("active", "#242A32"), ("pressed", "#20252C")], foreground=[("disabled", "#666D76")])
        style.configure("Toolbar.TButton", padding=(10, 6), font=(self.ui_font, 9))
        style.configure("Accent.TButton", background=self.ACCENT, foreground="#071014", bordercolor=self.ACCENT, lightcolor=self.ACCENT, darkcolor=self.ACCENT, font=(self.ui_font, 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#7AD7EF"), ("pressed", "#45B4D4")])
        style.configure(
            "DeleteIcon.TButton",
            background=self.SURFACE_ALT,
            foreground=self.MUTED,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
            borderwidth=1,
            relief="flat",
            padding=0,
            font=(self.ui_font, 11, "bold"),
        )
        style.map(
            "DeleteIcon.TButton",
            background=[("active", "#242A32"), ("pressed", "#20252C")],
            foreground=[("active", self.TEXT), ("pressed", self.TEXT)],
        )
        style.configure("Segment.TButton", padding=(10, 4), font=(self.ui_font, 9))
        style.configure("Selected.Segment.TButton", background="#193841", foreground=self.ACCENT, bordercolor="#32616D", padding=(10, 4), font=(self.ui_font, 9, "bold"))
        style.configure("TNotebook", background=self.BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=self.BG, foreground=self.MUTED, bordercolor=self.BG, lightcolor=self.BG, darkcolor=self.BG, padding=(18, 9), font=(self.ui_font, 10))
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.SURFACE_ALT), ("active", self.SURFACE)],
            foreground=[("selected", self.TEXT), ("active", self.TEXT)],
            bordercolor=[("selected", self.BORDER), ("!selected", self.BG)],
            lightcolor=[("selected", self.BORDER), ("!selected", self.BG)],
            darkcolor=[("selected", self.BORDER), ("!selected", self.BG)],
        )
        style.configure("Treeview", background=self.SURFACE, fieldbackground=self.SURFACE, foreground=self.TEXT, bordercolor=self.BORDER, lightcolor=self.BORDER, darkcolor=self.BORDER, borderwidth=1, rowheight=36, font=(self.ui_font, 10))
        style.configure("Market.Treeview", rowheight=31, font=(self.ui_font, 9))
        style.map("Treeview", background=[("selected", "#204A55")], foreground=[("selected", self.TEXT)])
        style.configure("Treeview.Heading", background=self.SURFACE_ALT, foreground=self.MUTED, bordercolor=self.BORDER, lightcolor=self.BORDER, darkcolor=self.BORDER, relief="flat", font=(self.ui_font, 9, "bold"), padding=(8, 7))
        style.map("Treeview.Heading", background=[("active", "#20262D")])
        style.configure("TEntry", fieldbackground=self.SURFACE_ALT, foreground=self.TEXT, insertcolor=self.TEXT, bordercolor=self.BORDER, padding=8)
        style.map("TEntry", bordercolor=[("focus", self.ACCENT)])
        style.configure(
            "TCombobox",
            fieldbackground=self.SURFACE_ALT,
            background=self.SURFACE_ALT,
            foreground=self.TEXT,
            arrowcolor=self.MUTED,
            bordercolor=self.BORDER,
            padding=7,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.SURFACE_ALT)],
            foreground=[("readonly", self.TEXT)],
            bordercolor=[("focus", self.ACCENT)],
        )
        self.option_add("*TCombobox*Listbox.background", self.SURFACE_ALT)
        self.option_add("*TCombobox*Listbox.foreground", self.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", "#204A55")
        self.option_add("*TCombobox*Listbox.selectForeground", self.TEXT)
        self.option_add("*TCombobox*Listbox.font", (self.ui_font, 10))
        self.option_add("*TCombobox*Listbox.relief", "flat")
        style.configure("TCheckbutton", background=self.BG, foreground=self.TEXT, font=(self.ui_font, 10))
        style.map("TCheckbutton", background=[("active", self.BG)], foreground=[("active", self.TEXT)])
        style.configure(
            "Dialog.TCheckbutton",
            background=self.SURFACE,
            foreground=self.TEXT,
            font=(self.ui_font, 10),
        )
        style.map(
            "Dialog.TCheckbutton",
            background=[("active", self.SURFACE)],
            foreground=[("disabled", "#666D76"), ("active", self.TEXT)],
        )
        style.configure(
            "Dialog.TRadiobutton",
            background=self.SURFACE,
            foreground=self.TEXT,
            font=(self.ui_font, 10),
        )
        style.map(
            "Dialog.TRadiobutton",
            background=[("active", self.SURFACE)],
            foreground=[("active", self.TEXT)],
        )
        style.configure("TSeparator", background=self.BORDER)
        style.configure("Horizontal.TProgressbar", background=self.ACCENT, troughcolor=self.SURFACE_ALT, bordercolor=self.BORDER)
        style.configure(
            "Horizontal.TScale",
            background=self.SURFACE,
            troughcolor=self.SURFACE_ALT,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
            sliderrelief="flat",
        )
        style.configure(
            "Vertical.TScrollbar",
            background=self.SURFACE_ALT,
            troughcolor=self.SURFACE,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
            arrowcolor=self.MUTED,
        )

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=(20, 12, 20, 20))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Stock Agent", style="AppTitle.TLabel").pack(
            anchor="w", pady=(0, 10)
        )

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook
        self.chat_tab = ttk.Frame(notebook, padding=(8, 16, 8, 8))
        self.holdings_tab = ttk.Frame(notebook, padding=(8, 16, 8, 8))
        self.market_tab = ttk.Frame(notebook, padding=(8, 16, 8, 8))
        self.account_tab = ttk.Frame(notebook, padding=(8, 16, 8, 8))
        notebook.add(self.chat_tab, text="Research Lab")
        notebook.add(self.holdings_tab, text="Portfolio")
        notebook.add(self.market_tab, text="Market")
        notebook.add(self.account_tab, text="Account")
        notebook.bind("<ButtonPress-1>", self._start_tab_drag, add="+")
        notebook.bind("<B1-Motion>", self._continue_tab_drag, add="+")
        notebook.bind("<ButtonRelease-1>", self._finish_tab_drag, add="+")
        self._build_chat_tab()
        self._build_holdings_tab()
        self._build_market_tab()
        self._build_account_tab()
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

    def _on_tab_changed(self, _event: tk.Event) -> None:
        selected = self.notebook.select()
        if selected == str(self.holdings_tab):
            self.refresh_holdings()
        elif selected == str(self.market_tab):
            self.refresh_market_regime()

    def _start_tab_drag(self, event: tk.Event) -> None:
        # Include the blank area after the last tab so the whole top strip can
        # be scrubbed, not only the text inside an individual tab.
        if not 0 <= event.y <= 44:
            self._tab_drag_anchor_x = None
            return
        self._tab_drag_anchor_x = event.x

    def _continue_tab_drag(self, event: tk.Event) -> None:
        if self._tab_drag_anchor_x is None:
            return
        delta = event.x - self._tab_drag_anchor_x
        target = tab_drag_target(
            self.notebook.index("current"), len(self.notebook.tabs()), delta
        )
        if target is None:
            return
        self.notebook.select(target)
        self._tab_drag_anchor_x = event.x

    def _finish_tab_drag(self, _event: tk.Event) -> None:
        self._tab_drag_anchor_x = None

    def _build_chat_tab(self) -> None:
        header = ttk.Frame(self.chat_tab)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="Research Lab", style="Title.TLabel").pack(side="left")
        self.stop_button = ttk.Button(
            header,
            text="Stop",
            style="Toolbar.TButton",
            command=self._request_stop,
            state="disabled",
        )
        self.stop_button.pack(side="right")
        ttk.Button(
            header,
            text="Example questions",
            style="Toolbar.TButton",
            command=self.show_research_examples,
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            header,
            text="Method",
            style="Toolbar.TButton",
            command=self.show_research_methodology,
        ).pack(side="right", padx=(0, 8))

        composer = ttk.Frame(self.chat_tab, style="Panel.TFrame", padding=12)
        composer.pack(fill="x", pady=(0, 12))
        ttk.Label(composer, text="Research idea", style="SurfaceSection.TLabel").pack(
            anchor="w", pady=(0, 7)
        )
        input_row = ttk.Frame(composer, style="Surface.TFrame")
        input_row.pack(fill="x")
        self.research_question = tk.Text(
            input_row,
            height=3,
            wrap="word",
            font=(self.ui_font, 11),
            background=self.SURFACE_ALT,
            foreground=self.TEXT,
            insertbackground=self.TEXT,
            selectbackground="#285665",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            padx=10,
            pady=8,
        )
        self.research_question.pack(side="left", fill="x", expand=True)
        self.propose_research_button = ttk.Button(
            input_row,
            text="Build plan",
            style="Accent.TButton",
            command=self.start_research_proposal,
        )
        self.propose_research_button.pack(side="right", anchor="s", padx=(10, 0))
        self.research_question.bind(
            "<Control-Return>", lambda _event: self.start_research_proposal()
        )
        self.research_question.bind(
            "<Command-Return>", lambda _event: self.start_research_proposal()
        )
        self.research_question.bind(
            "<Control-a>", lambda _event: _select_all_text(self.research_question)
        )
        self.research_question.bind(
            "<Command-a>", lambda _event: _select_all_text(self.research_question)
        )
        self.research_question.bind(
            "<Left>",
            lambda _event: _collapse_text_selection(self.research_question, to_end=False),
        )
        self.research_question.bind(
            "<Right>",
            lambda _event: _collapse_text_selection(self.research_question, to_end=True),
        )

        transcript_frame = ttk.Frame(self.chat_tab)
        self.transcript = ScrolledText(
            transcript_frame,
            wrap="word",
            font=(self.mono_font, 11),
            background=self.SURFACE,
            foreground=self.TEXT,
            insertbackground="#ffffff",
            selectbackground="#285665",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            highlightcolor=self.BORDER,
            padx=16,
            pady=14,
            spacing1=2,
            spacing3=3,
        )
        self.transcript.pack(fill="both", expand=True)
        self.transcript.insert("end", "Results:\nApproved research results will appear here.\n\n")
        self.transcript.configure(state="disabled")

        self.progress_frame = ttk.Frame(
            self.chat_tab, style="Panel.TFrame", padding=(12, 9)
        )
        progress_header = ttk.Frame(self.progress_frame, style="Surface.TFrame")
        progress_header.pack(fill="x")
        self.progress_status = ttk.Label(
            progress_header,
            text="Research status: idle",
            style="ProgressTitle.TLabel",
        )
        self.progress_status.pack(side="left", anchor="w")
        self.progress_percent = ttk.Label(
            progress_header, text="0%", style="ProgressTitle.TLabel"
        )
        self.progress_percent.pack(side="right", anchor="e")
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self.progress_bar.pack(fill="x", pady=(6, 4))
        progress_footer = ttk.Frame(self.progress_frame, style="Surface.TFrame")
        progress_footer.pack(fill="x")
        self.progress_detail = ttk.Label(
            progress_footer,
            text="Progress updates will appear here during deep research.",
            style="ProgressDetail.TLabel",
        )
        self.progress_detail.pack(side="left", fill="x", expand=True, anchor="w")
        self.progress_elapsed = ttk.Label(progress_footer, text="")
        self.progress_elapsed.configure(style="ProgressDetail.TLabel")
        self.progress_elapsed.pack(side="right", anchor="e", padx=(12, 0))

        self.progress_frame.pack(side="bottom", fill="x", pady=(10, 0))
        transcript_frame.pack(side="top", fill="both", expand=True)

    def _build_holdings_tab(self) -> None:
        header = ttk.Frame(self.holdings_tab)
        header.pack(fill="x", pady=(0, 14))
        title_block = ttk.Frame(header)
        title_block.pack(side="left")
        ttk.Label(title_block, text="Portfolio", style="Title.TLabel").pack(anchor="w")
        self.portfolio_status = tk.StringVar(value="Live prices have not been refreshed")
        ttk.Label(title_block, textvariable=self.portfolio_status, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        toolbar = ttk.Frame(header)
        toolbar.pack(side="right", anchor="e")
        ttk.Button(
            toolbar, text="Record purchase", style="Toolbar.TButton", command=self.record_purchase
        ).pack(side="left")
        ttk.Button(
            toolbar,
            text="Review position risk",
            style="Toolbar.TButton",
            command=self.start_position_risk_review,
        ).pack(side="left", padx=8)
        ttk.Button(
            toolbar,
            text="Reset portfolio",
            style="Toolbar.TButton",
            command=self.reset_portfolio,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            toolbar,
            text="Model",
            style="Toolbar.TButton",
            command=self.show_portfolio_model,
        ).pack(side="left", padx=(0, 8))
        self.portfolio_refresh_button = ttk.Button(
            toolbar,
            text="Refresh prices",
            style="Accent.TButton",
            command=self.refresh_holdings,
        )
        self.portfolio_refresh_button.pack(side="left")

        self.portfolio_value = tk.StringVar(value="—")
        self.portfolio_cost = tk.StringVar(value="—")
        self.portfolio_gain = tk.StringVar(value="—")
        self.portfolio_return = tk.StringVar(value="—")
        summary = ttk.Frame(self.holdings_tab, style="Panel.TFrame", padding=(20, 15))
        summary.pack(fill="x", pady=(0, 14))
        for column in range(4):
            summary.columnconfigure(column, weight=1, uniform="metric")
        self._metric(summary, 0, "Portfolio value", self.portfolio_value)
        self._metric(summary, 1, "Invested", self.portfolio_cost)
        self.portfolio_gain_label = self._metric(summary, 2, "Total gain", self.portfolio_gain)
        self.portfolio_return_label = self._metric(summary, 3, "Total return", self.portfolio_return)

        performance = ttk.Frame(self.holdings_tab, style="Panel.TFrame", padding=(18, 13))
        performance.pack(fill="x", pady=(0, 14))
        performance_header = ttk.Frame(performance, style="Surface.TFrame")
        performance_header.pack(fill="x", pady=(0, 8))
        self.performance_title = tk.StringVar(value="Portfolio performance")
        ttk.Label(
            performance_header,
            textvariable=self.performance_title,
            style="SurfaceSection.TLabel",
        ).pack(side="left")
        self.period_change = tk.StringVar(value="History unavailable")
        self.period_change_label = ttk.Label(performance_header, textvariable=self.period_change, style="MetricValue.TLabel")
        self.period_change_label.pack(side="left", padx=(16, 0))
        segment = ttk.Frame(performance_header, style="Surface.TFrame")
        segment.pack(side="right")
        self.period_buttons: dict[int, ttk.Button] = {}
        for sessions, label in (
            (1, "1 day"),
            (3, "3 days"),
            (5, "1 week"),
            (10, "2 weeks"),
            (15, "3 weeks"),
            (20, "4 weeks"),
            (0, "All time"),
        ):
            button = ttk.Button(segment, text=label, style="Segment.TButton", command=lambda value=sessions: self._set_performance_period(value))
            button.pack(side="left", padx=(6, 0))
            self.period_buttons[sessions] = button
        self.performance_canvas = tk.Canvas(performance, height=118, background=self.SURFACE, highlightthickness=0)
        self.performance_canvas.pack(fill="x")
        self.performance_canvas.bind("<Configure>", lambda _event: self._draw_performance())
        self.performance_history = []
        self.performance_portfolio_history = []
        self.performance_position_histories: dict[str, list[Any]] = {}
        self.performance_intraday_portfolio_history = []
        self.performance_intraday_position_histories: dict[str, list[Any]] = {}
        self.performance_history_missing_tickers: tuple[str, ...] = ()
        self.selected_performance_ticker: str | None = None
        # Show the complete purchase-aware history on first load. A short
        # default window can misleadingly look like the position began recently.
        self.performance_sessions = 0
        self._set_performance_period(0)

        columns = (
            "ticker",
            "quantity",
            "average_cost",
            "total_cost",
            "current_price",
            "market_value",
            "gain_loss",
            "return_percent",
            "delete",
        )
        self.holdings_tree = ttk.Treeview(self.holdings_tab, columns=columns, show="headings")
        self.holdings_headings = {
            "ticker": "Ticker",
            "quantity": "Shares",
            "average_cost": "Avg. purchase price",
            "total_cost": "Total cost",
            "current_price": "Current price",
            "market_value": "Total value",
            "gain_loss": "Gain/loss",
            "return_percent": "Return",
            "delete": "",
        }
        widths = {
            "ticker": 110,
            "quantity": 100,
            "average_cost": 140,
            "total_cost": 140,
            "current_price": 140,
            "market_value": 140,
            "gain_loss": 140,
            "return_percent": 110,
            "delete": 44,
        }
        for column in columns:
            heading_options: dict[str, Any] = {"text": self.holdings_headings[column]}
            if column != "delete":
                heading_options["command"] = (
                    lambda selected_column=column: self._sort_holdings(selected_column)
                )
            self.holdings_tree.heading(column, **heading_options)
            self.holdings_tree.column(column, width=widths[column], anchor="center")
        self.holdings_scrollbar = ttk.Scrollbar(
            self.holdings_tab,
            orient="vertical",
            command=self._scroll_holdings,
        )
        self.holdings_tree.configure(yscrollcommand=self._update_holdings_scrollbar)
        self.holdings_tree.tag_configure("positive", foreground=self.POSITIVE)
        self.holdings_tree.tag_configure("negative", foreground=self.NEGATIVE)
        self.holdings_tree.bind("<Button-1>", self._toggle_holding_chart, add="+")
        self.holdings_tree.bind(
            "<Configure>", lambda _event: self.after_idle(self._position_delete_buttons), add="+"
        )
        self.holdings_tree.bind(
            "<MouseWheel>", lambda _event: self.after_idle(self._position_delete_buttons), add="+"
        )
        self.holdings_tree.pack(side="left", fill="both", expand=True)
        self.holdings_scrollbar.pack(side="right", fill="y")
        self.refresh_holdings(refresh_prices=False)

    def _metric(self, parent: ttk.Frame, column: int, title: str, variable: tk.StringVar) -> ttk.Label:
        cell = ttk.Frame(parent, style="Surface.TFrame", padding=(12, 0))
        cell.grid(row=0, column=column, sticky="nsew")
        ttk.Label(cell, text=title, style="MetricLabel.TLabel").pack(anchor="w")
        value = ttk.Label(cell, textvariable=variable, style="MetricValue.TLabel")
        value.pack(anchor="w", pady=(5, 0))
        return value

    def _build_market_tab(self) -> None:
        header = ttk.Frame(self.market_tab)
        header.pack(fill="x", pady=(0, 14))
        title_block = ttk.Frame(header)
        title_block.pack(side="left", fill="x", expand=True)
        ttk.Label(title_block, text="Market guide", style="Title.TLabel").pack(anchor="w")
        self.market_status = tk.StringVar(value="Waiting for current macro observations")
        ttk.Label(title_block, textvariable=self.market_status, style="Muted.TLabel").pack(
            anchor="w", pady=(2, 0)
        )
        self.market_refresh_button = ttk.Button(
            header,
            text="Refresh data",
            style="Accent.TButton",
            command=self.refresh_market_regime,
        )
        self.market_refresh_button.pack(side="right")
        ttk.Button(
            header,
            text="How signals work",
            command=self.show_macro_reference,
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            header,
            text="Start research",
            style="Accent.TButton",
            command=self.start_industry_research,
        ).pack(side="right", padx=(0, 8))

        overview = ttk.Frame(self.market_tab, style="Panel.TFrame", padding=(20, 15))
        overview.pack(fill="x", pady=(0, 14))
        self.market_regime_title = tk.StringVar(value="Regime not calculated")
        self.market_company_fit = tk.StringVar(
            value="Stock profile to prioritize: waiting for a complete market regime."
        )
        ttk.Label(
            overview, textvariable=self.market_regime_title, style="SurfaceSection.TLabel"
        ).pack(anchor="w")
        tk.Label(
            overview,
            textvariable=self.market_company_fit,
            background=self.SURFACE,
            foreground=self.TEXT,
            font=(self.ui_font, 10, "bold"),
            justify="left",
            anchor="w",
            wraplength=1030,
        ).pack(fill="x", pady=(9, 0))

        columns = ("indicator", "latest", "trend", "favored", "as_of")
        self.market_tree = ttk.Treeview(
            self.market_tab,
            columns=columns,
            show="headings",
            height=5,
            style="Market.Treeview",
        )
        headings = {
            "indicator": "Indicator",
            "latest": "Now",
            "trend": "Change",
            "favored": "Macro tilt",
            "as_of": "Data time",
        }
        widths = {
            "indicator": 185,
            "latest": 105,
            "trend": 260,
            "favored": 310,
            "as_of": 105,
        }
        for column in columns:
            self.market_tree.heading(column, text=headings[column])
            self.market_tree.column(
                column,
                width=widths[column],
                anchor="w" if column in {"indicator", "trend", "favored"} else "center",
            )
        self.market_tree.tag_configure("unavailable", foreground=self.MUTED)
        self.market_tree.tag_configure("safe", foreground=self.POSITIVE)
        self.market_tree.tag_configure("neutral", foreground=self.WARNING)
        self.market_tree.tag_configure("risk_tolerant", foreground=self.NEGATIVE)
        self.market_tree.pack(fill="x")

    def _build_account_tab(self) -> None:
        settings = self.controller.settings
        self.supabase_url = tk.StringVar(value=settings.supabase_url or "")
        self.supabase_key = tk.StringVar(value=settings.supabase_publishable_key or "")
        self.auth_email = tk.StringVar()
        self.auth_password = tk.StringVar()
        self.show_supabase_key = tk.BooleanVar(value=False)
        self.account_status = tk.StringVar(
            value="Enter your Supabase project settings, then create an account or sign in."
        )

        panel = ttk.Frame(self.account_tab, padding=20)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)

        ttk.Label(panel, text="Supabase account", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(
            panel,
            text=(
                "Use the Project URL and publishable key from Supabase Project Settings. "
                "The publishable key is safe for a desktop client; never paste a secret "
                "or service-role key here."
            ),
            wraplength=850,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 20))

        ttk.Label(panel, text="Project URL").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)
        self.supabase_url_entry = ttk.Entry(panel, textvariable=self.supabase_url)
        self.supabase_url_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(panel, text="Publishable key").grid(
            row=3, column=0, sticky="w", padx=(0, 12), pady=6
        )
        self.supabase_key_entry = ttk.Entry(panel, textvariable=self.supabase_key, show="•")
        self.supabase_key_entry.grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Checkbutton(
            panel,
            text="Show",
            variable=self.show_supabase_key,
            command=self._toggle_supabase_key,
        ).grid(row=3, column=2, sticky="w", padx=(10, 0))

        self.save_supabase_button = ttk.Button(
            panel, text="Save connection", command=self._save_supabase_connection
        )
        self.save_supabase_button.grid(row=4, column=1, sticky="w", pady=(8, 22))

        ttk.Separator(panel).grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 18))

        ttk.Label(panel, text="Email").grid(row=6, column=0, sticky="w", padx=(0, 12), pady=6)
        ttk.Entry(panel, textvariable=self.auth_email).grid(
            row=6, column=1, columnspan=2, sticky="ew", pady=6
        )
        ttk.Label(panel, text="Password").grid(row=7, column=0, sticky="w", padx=(0, 12), pady=6)
        password_entry = ttk.Entry(panel, textvariable=self.auth_password, show="•")
        password_entry.grid(row=7, column=1, columnspan=2, sticky="ew", pady=6)
        password_entry.bind("<Return>", lambda _event: self._start_auth("sign_in"))

        buttons = ttk.Frame(panel)
        buttons.grid(row=8, column=1, columnspan=2, sticky="w", pady=(12, 16))
        self.create_account_button = ttk.Button(
            buttons, text="Create account", command=lambda: self._start_auth("sign_up")
        )
        self.create_account_button.pack(side="left")
        self.sign_in_button = ttk.Button(
            buttons, text="Sign in", command=lambda: self._start_auth("sign_in")
        )
        self.sign_in_button.pack(side="left", padx=8)
        self.sign_out_button = ttk.Button(
            buttons, text="Sign out", command=lambda: self._start_auth("sign_out")
        )
        self.sign_out_button.pack(side="left")

        self.account_status_label = ttk.Label(
            panel,
            textvariable=self.account_status,
            wraplength=850,
            justify="left",
        )
        self.account_status_label.grid(row=9, column=0, columnspan=3, sticky="ew")

        if settings.supabase_url and settings.supabase_publishable_key:
            self.account_status.set(
                "Connection settings loaded. Create an account or sign in."
            )

    def _toggle_supabase_key(self) -> None:
        self.supabase_key_entry.configure(show="" if self.show_supabase_key.get() else "•")

    def _set_auth_busy(self, busy: bool) -> None:
        self.auth_busy = busy
        state = "disabled" if busy else "normal"
        for button in (
            self.save_supabase_button,
            self.create_account_button,
            self.sign_in_button,
            self.sign_out_button,
        ):
            button.configure(state=state)

    def _save_supabase_connection(self) -> bool:
        if self.auth_busy:
            return False
        try:
            url = self.supabase_url.get().strip()
            key = self.supabase_key.get().strip()
            save_supabase_settings(url, key)
        except Exception as exc:
            self.account_status.set(friendly_auth_error(exc))
            return False
        self.supabase_url.set(url.rstrip("/"))
        self.account_status.set(
            "Connection settings saved locally. Create an account or sign in to verify them."
        )
        return True

    def _start_auth(self, action: str) -> None:
        if self.auth_busy:
            return
        if action != "sign_out" and not self._save_supabase_connection():
            return
        email = self.auth_email.get().strip()
        password = self.auth_password.get()
        if action != "sign_out" and (not email or not password):
            self.account_status.set("Enter both an email address and password.")
            return

        labels = {
            "sign_up": "Creating account…",
            "sign_in": "Signing in…",
            "sign_out": "Signing out…",
        }
        self.account_status.set(labels[action])
        self._set_auth_busy(True)
        threading.Thread(
            target=self._auth_worker,
            args=(action, email, password),
            daemon=True,
        ).start()

    def _auth_worker(self, action: str, email: str, password: str) -> None:
        try:
            if action == "sign_up":
                result = self.controller.account_sign_up(email, password)
            elif action == "sign_in":
                result = self.controller.account_sign_in(email, password)
            else:
                result = self.controller.account_sign_out()
            self.results.put(("auth", result))
        except Exception as exc:
            if action in {"sign_in", "sign_up"}:
                try:
                    self.controller.account_sign_out()
                except Exception:
                    pass
            self.results.put(("auth_error", friendly_auth_error(exc)))

    def _request_stop(self) -> None:
        if not self.is_busy or not self.current_task_cancellable or self.stop_requested:
            return
        self.stop_requested = True
        if self.cancel_event is not None:
            self.cancel_event.set()
        timeout = getattr(self.controller.settings, "lseg_request_timeout", 20.0)
        self.current_stage = "Stopping research"
        self.current_detail = (
            "Stop requested. Waiting for the active LSEG request to return or reach "
            f"the {timeout:g}-second request timeout."
        )
        self.progress_status.configure(text="Research status: stopping")
        self.progress_detail.configure(text=self.current_detail)
        self._refresh_stop_button()
        self._replace_live_progress_message()

    def _start_position_risk_worker(
        self,
        tickers: list[str],
        user_context: str | None = None,
    ) -> None:
        if self.is_busy:
            messagebox.showinfo("Research in progress", "Wait for the current research to finish or stop it.", parent=self)
            return
        self.notebook.select(self.chat_tab)
        portfolio_tickers = {holding.ticker for holding in self.controller.holdings()}
        label = "entire portfolio" if set(tickers) == portfolio_tickers else ", ".join(tickers)
        self._append("Research request", f"Review position risk for {label}.")
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        self._set_busy(True, cancellable=True)

        def worker() -> None:
            def progress_callback(percent: int | None, stage: str, detail: str = "") -> None:
                self.results.put(("progress", {"percent": percent, "stage": stage, "detail": detail}))

            try:
                response = self.controller.review_position_risk(
                    tickers=tickers,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                    user_context=user_context,
                )
                result_kind = "message"
            except Exception as exc:
                response = friendly_research_error(exc)
                result_kind = "research_error"
            self.results.put((result_kind, response))

        threading.Thread(target=worker, daemon=True).start()

    def start_research_proposal(self) -> None:
        if self.is_busy:
            messagebox.showinfo(
                "Research in progress",
                "Wait for the current research to finish or stop it.",
                parent=self,
            )
            return
        question = self.research_question.get("1.0", "end").strip()
        if not question:
            messagebox.showerror(
                "Question required", "Enter a research question.", parent=self
            )
            return
        self._append("Research idea", question)
        self._set_busy(True, cancellable=False)
        self._update_progress(10, "Proposing research plan", "Matching the question to registered capabilities.")

        def worker() -> None:
            try:
                proposal = self.controller.propose_custom_research(question)
                self.results.put(("research_proposal", proposal))
            except Exception as exc:
                self.results.put(
                    (
                        "research_proposal_error",
                        "Could not prepare a research plan: "
                        + friendly_research_error(exc),
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

    def show_research_examples(self) -> None:
        dialog = ResearchExamplesDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        self.research_question.delete("1.0", "end")
        self.research_question.insert("1.0", dialog.result)
        self.research_question.focus_set()

    def _show_method_window(
        self,
        title: str,
        heading: str,
        sections: tuple[tuple[str, str], ...],
    ) -> None:
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry("760x560")
        window.minsize(680, 480)
        window.transient(self)
        content = ttk.Frame(window, padding=24)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text=heading, style="Title.TLabel").pack(anchor="w")
        for label, body in sections:
            ttk.Label(content, text=label, style="Section.TLabel").pack(
                anchor="w", pady=(18, 4)
            )
            ttk.Label(
                content,
                text=body,
                style="Muted.TLabel",
                wraplength=700,
                justify="left",
            ).pack(fill="x", anchor="w")
        ttk.Button(content, text="Close", command=window.destroy).pack(
            side="bottom", anchor="e", pady=(20, 0)
        )
        window.bind("<Escape>", lambda _event: window.destroy())
        self.after_idle(lambda: _center_dialog(window, self))

    def show_research_methodology(self) -> None:
        self._show_method_window(
            "Research methodology",
            "How candidate research is interpreted",
            (
                (
                    "Undervalued",
                    "A relative-value plan requires at least one usable positive valuation multiple below "
                    "the median of the approved LSEG sector or industry universe. It is a shortlist rule, "
                    "not an intrinsic-value estimate or return forecast.",
                ),
                (
                    "Different industries",
                    "Each universe is ranked independently. Cross-industry theme results are interleaved by "
                    "their within-universe order, so a software P/E is not treated as directly comparable to "
                    "a utility P/E.",
                ),
                (
                    "Freshness and history",
                    "Reports show their generation time and missing evidence. Screens use the current retrieval "
                    "snapshot. The app does not claim a point-in-time historical backtest because it does not "
                    "retain historical universe membership and estimate vintages.",
                ),
                (
                    "Incomplete runs",
                    "Optional missing data and failed discovery universes are reported as partial results when "
                    "usable evidence remains. Required identity or comparison evidence still stops the run.",
                ),
            ),
        )

    def show_portfolio_model(self) -> None:
        self._show_method_window(
            "Portfolio model",
            "What the portfolio currently measures",
            (
                (
                    "Open positions",
                    "The portfolio aggregates recorded purchase lots into current shares, cost, average purchase "
                    "price, market value, and unrealized gain or loss.",
                ),
                (
                    "Not a transaction ledger",
                    "Sales, dividends, stock splits, fees, cash balances, and taxes are not modeled yet. Total "
                    "return therefore means unrealized return on the recorded open purchases, not complete "
                    "account performance.",
                ),
                (
                    "Performance chart",
                    "The one-day chart uses five-minute observations from the latest market session. Longer "
                    "charts use daily values and the quantities owned after each recorded purchase date. Missing "
                    "ticker history is labeled partial rather than silently substituted.",
                ),
                (
                    "Decision context",
                    "Position review can accept an optional objective, horizon, and exit conditions for that run. "
                    "The context is labeled as user supplied and is not stored as an independently verified thesis.",
                ),
            ),
        )

    def _start_research_lab_worker(self, approved: ApprovedResearchPlan) -> None:
        if self.is_busy:
            return
        capability_labels = [
            item.label for item in CAPABILITIES if item.capability_id in approved.capability_ids
        ]
        analysis_labels = [
            item.label for item in ANALYSES if item.analysis_id in approved.analysis_ids
        ]
        summary_parts = []
        if approved.mode == "discovery":
            scopes = approved.discovery_scopes or (
                (approved.discovery_scope,) if approved.discovery_scope else ()
            )
            summary_parts.append("Universes: " + ", ".join(scopes))
            summary_parts.append(
                "Exchange market: " + (approved.exchange_geography or "All exchanges")
            )
            if approved.discovery_theme:
                summary_parts.append("Business theme: " + approved.discovery_theme)
            if approved.selection_objectives:
                objective_labels = {
                    "relative_value": "peer-relative value required",
                    "positive_signals": "multiple positive signal families required",
                }
                summary_parts.append(
                    "Candidate rules: "
                    + ", ".join(
                        objective_labels[item]
                        for item in approved.selection_objectives
                    )
                )
        summary_parts.append("Data: " + ", ".join(capability_labels))
        if analysis_labels:
            summary_parts.append("Optional analyses: " + ", ".join(analysis_labels))
        self._append("Approved plan", ". ".join(summary_parts) + ".")
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        self._set_busy(True, cancellable=True)

        def progress_callback(percent: int | None, stage: str, detail: str = "") -> None:
            self.results.put(
                ("progress", {"percent": percent, "stage": stage, "detail": detail})
            )

        def worker() -> None:
            try:
                result = self.controller.run_custom_research(
                    approved,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
                response = result.report
                result_kind = "message"
            except ResearchCancelled:
                response = "Research stopped. Partial results were discarded."
                result_kind = "message"
            except Exception as exc:
                response = friendly_research_error(exc)
                result_kind = "research_error"
            self.results.put((result_kind, response))

        threading.Thread(target=worker, daemon=True).start()

    def _start_industry_research_worker(self, industry: str, count: int) -> None:
        if self.is_busy:
            messagebox.showinfo(
                "Research in progress",
                "Wait for the current research to finish or stop it.",
                parent=self,
            )
            return
        self.notebook.select(self.chat_tab)
        self._append("Research request", f"Research {industry}; return {count} stocks.")
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        self._set_busy(True, cancellable=True)

        def progress_callback(percent: int | None, stage: str, detail: str = "") -> None:
            self.results.put(
                (
                    "progress",
                    {"percent": percent, "stage": stage, "detail": detail},
                )
            )

        def worker() -> None:
            try:
                response = self.controller.research_industry(
                    industry,
                    count,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
                result_kind = "message"
            except Exception as exc:
                response = friendly_research_error(exc)
                result_kind = "research_error"
            self.results.put((result_kind, response))

        threading.Thread(target=worker, daemon=True).start()

    def _run_direct(self, kind: str, function) -> None:
        self._set_busy(True, cancellable=False)

        def worker() -> None:
            try:
                response = function()
            except Exception as exc:
                response = f"Unexpected error: {type(exc).__name__}: {exc}"
            self.results.put((kind, response))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_results(self) -> None:
        try:
            while True:
                kind, payload = self.results.get_nowait()
                if kind == "progress":
                    self.research_progress_seen = True
                    stage = str(payload.get("stage") or "Researching")
                    if self.stop_requested and stage != "Research stopped":
                        continue
                    self._update_progress(
                        payload.get("percent"),
                        stage,
                        str(payload.get("detail") or ""),
                    )
                    continue
                if kind == "research_proposal":
                    self._finish_progress("Proposal ready.")
                    self._set_busy(False)
                    proposal = payload
                    if not isinstance(proposal, ResearchProposal):
                        self._append("Results", "The proposal model returned an invalid result.")
                        continue
                    dialog = ResearchApprovalDialog(self, proposal)
                    self.wait_window(dialog)
                    if dialog.result is not None:
                        self._start_research_lab_worker(dialog.result)
                    continue
                if kind == "research_proposal_error":
                    response = str(payload)
                    self._finish_progress(response, failed=True)
                    self._append("Results", response)
                    self._set_busy(False)
                    continue
                if kind == "auth":
                    result = payload
                    assert isinstance(result, AuthResult)
                    self.account_status.set(result.message)
                    self.auth_password.set("")
                    self._set_auth_busy(False)
                    self._invalidate_portfolio_refresh()
                    self.refresh_holdings(refresh_prices=False)
                    continue
                if kind == "auth_error":
                    self.account_status.set(str(payload))
                    self.auth_password.set("")
                    self._set_auth_busy(False)
                    self._invalidate_portfolio_refresh()
                    self.refresh_holdings(refresh_prices=False)
                    continue
                if kind == "market_regime":
                    self.market_refresh_busy = False
                    self.market_refresh_button.configure(state="normal")
                    if isinstance(payload, MarketRegimeSnapshot):
                        self._render_market_regime(payload)
                    else:
                        self.market_status.set(str(payload))
                    continue
                if kind == "portfolio_refresh":
                    if not isinstance(payload, dict):
                        continue
                    if payload["generation"] != self.portfolio_refresh_generation:
                        continue
                    self.portfolio_refresh_busy = False
                    self.portfolio_refresh_button.configure(state="normal")
                    if "error" in payload:
                        self.portfolio_status.set(payload["error"])
                    else:
                        self._render_holdings(
                            payload["holdings"],
                            refresh_prices=True,
                            history=payload["history"],
                            position_histories=payload["position_histories"],
                            intraday_history=payload["intraday_history"],
                            intraday_position_histories=payload[
                                "intraday_position_histories"
                            ],
                            missing_history_tickers=payload["missing_history_tickers"],
                        )
                    continue
                response = str(payload)
                self._finish_progress(response, failed=kind == "research_error")
                self._append("Results", response)
                self._set_busy(False)
                self.refresh_holdings(refresh_prices=False)
        except queue.Empty:
            pass
        self._refresh_elapsed()
        self.after(100, self._poll_results)

    def _append(self, speaker: str, text: str) -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"{speaker}:\n{text.strip()}\n\n")
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def _set_busy(self, busy: bool, *, cancellable: bool = True) -> None:
        self.is_busy = busy
        if hasattr(self, "propose_research_button"):
            self.propose_research_button.configure(state="disabled" if busy else "normal")
        self.current_task_cancellable = busy and cancellable
        if busy:
            self.stop_requested = False
            self.stop_button.configure(state="normal" if cancellable else "disabled")
            self.research_started_at = time.monotonic()
            self.current_step_started_at = self.research_started_at
            self._last_elapsed_second = -1
            self.current_progress = 0
            self.current_stage = "Thinking"
            self.current_detail = "Preparing a response."
            self.research_progress_seen = False
            self._update_progress(0, self.current_stage, self.current_detail)
        else:
            self.research_started_at = None
            self.current_step_started_at = None
            self.current_task_cancellable = False
            self.stop_requested = False
            self.cancel_event = None
            self.stop_button.configure(state="disabled", text="Stop")

    def _refresh_stop_button(self) -> None:
        if not self.is_busy:
            self.stop_button.configure(text="Stop", state="disabled")
            return
        if self.stop_requested:
            self.stop_button.configure(text="Stopping...", state="disabled")
        elif self.current_task_cancellable:
            self.stop_button.configure(text="Stop research", state="normal")
        else:
            self.stop_button.configure(text="Stop", state="disabled")

    def _update_progress(self, percent: int | None, stage: str, detail: str = "") -> None:
        if percent is not None:
            self.current_progress = max(self.current_progress, max(0, min(100, int(percent))))
        next_stage = stage.strip() or self.current_stage
        next_detail = detail.strip() or self.current_detail
        if next_stage != self.current_stage or next_detail != self.current_detail:
            self.current_step_started_at = time.monotonic()
        self.current_stage = next_stage
        self.current_detail = next_detail
        self.progress_bar.configure(value=self.current_progress)
        self.progress_percent.configure(text=f"{self.current_progress}%")
        status_kind = "Research" if self.research_progress_seen else "Response"
        self.progress_status.configure(text=f"{status_kind} status: {self.current_stage}")
        self.progress_detail.configure(text=self.current_detail or "Working...")
        self._refresh_stop_button()
        self._replace_live_progress_message()

    def _current_request_text(self) -> str:
        if self.current_step_started_at is None or self.current_stage != "Querying LSEG":
            return ""
        seconds = max(0, int(time.monotonic() - self.current_step_started_at))
        timeout = max(5, int(getattr(self.controller.settings, "lseg_request_timeout", 20)))
        return f"Current request: {seconds:02d}s / {timeout}s timeout"

    def _replace_live_progress_message(self) -> None:
        elapsed = self._elapsed_text()
        progress_kind = "Research" if self.research_progress_seen else "Response"
        block = (
            "Status:\n"
            f"{progress_kind} progress: {self.current_progress}%\n"
            f"Stage: {self.current_stage}\n"
            f"Current task: {self.current_detail or 'Working...'}\n"
            + (f"{self._current_request_text()}\n" if self._current_request_text() else "")
            + f"Elapsed: {elapsed}\n\n"
        )
        self.transcript.configure(state="normal")
        ranges = self.transcript.tag_ranges("live_progress")
        if ranges:
            start = ranges[0]
            self.transcript.delete(ranges[0], ranges[1])
            self.transcript.insert(start, block, ("live_progress",))
        else:
            self.transcript.insert("end", block, ("live_progress",))
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def _finish_progress(self, response: str, *, failed: bool = False) -> None:
        stopped = response.startswith("Research stopped.")
        failed = failed or response.startswith("LSEG research could not run") or response.startswith("Unexpected error")
        if stopped:
            self.current_stage = "Research stopped"
            self.progress_status.configure(text="Research status: stopped")
            self.progress_detail.configure(text="The research was stopped by the user.")
        elif failed:
            self.current_stage = "Research failed"
            self.progress_status.configure(text="Research status: failed")
            self.progress_detail.configure(text="The final error is shown below.")
        elif self.research_progress_seen:
            self.current_progress = 100
            self.current_stage = "Research complete"
            self.progress_bar.configure(value=100)
            self.progress_percent.configure(text="100%")
            self.progress_status.configure(text="Research status: complete")
            self.progress_detail.configure(text="The concise findings are shown below.")
        else:
            self.current_progress = 100
            self.current_stage = "Response ready"
            self.progress_bar.configure(value=100)
            self.progress_percent.configure(text="100%")
            self.progress_status.configure(text="Response status: complete")
            self.progress_detail.configure(text="The response is shown below.")
        self._replace_live_progress_message()
        self.transcript.configure(state="normal")
        self.transcript.tag_remove("live_progress", "1.0", "end")
        self.transcript.configure(state="disabled")

    def _elapsed_text(self) -> str:
        if self.research_started_at is None:
            return "00:00"
        elapsed = max(0, int(time.monotonic() - self.research_started_at))
        minutes, seconds = divmod(elapsed, 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _refresh_elapsed(self) -> None:
        if self.research_started_at is None:
            return
        elapsed_seconds = max(0, int(time.monotonic() - self.research_started_at))
        if elapsed_seconds == self._last_elapsed_second:
            return
        self._last_elapsed_second = elapsed_seconds
        minutes, seconds = divmod(elapsed_seconds, 60)
        elapsed = f"{minutes:02d}:{seconds:02d}"
        request_text = self._current_request_text()
        suffix = f"{request_text} • Total {elapsed}" if request_text else f"Elapsed {elapsed}"
        self.progress_elapsed.configure(text=suffix)
        self._replace_live_progress_message()

    def record_purchase(self) -> None:
        method = PurchaseMethodDialog(self)
        self.wait_window(method)
        if method.result == "manual":
            self._record_manual_purchase()
        elif method.result == "json":
            self._import_portfolio_json()

    def _record_manual_purchase(self) -> None:
        dialog = PurchaseDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        security, quantity, price, purchased_at, note = dialog.result
        try:
            purchase = self.controller.record_purchase(
                security=security,
                quantity=quantity,
                price=price,
                purchased_at=purchased_at,
                note=note,
            )
        except Exception as exc:
            messagebox.showerror("Could not save purchase", f"{type(exc).__name__}: {exc}", parent=self)
            return
        self._append(
            "Portfolio",
            f"Recorded {purchase.quantity:g} shares of {purchase.ticker} at ${purchase.price:,.2f}.",
        )
        self._invalidate_portfolio_refresh()
        self.refresh_holdings(refresh_prices=False)

    def _import_portfolio_json(self) -> None:
        dialog = PortfolioJsonDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        try:
            count = self.controller.import_portfolio_json(dialog.result)
        except Exception as exc:
            messagebox.showerror("Could not import portfolio", str(exc), parent=self)
            return
        messagebox.showinfo("Portfolio imported", f"Imported {count} purchase records.", parent=self)
        self._invalidate_portfolio_refresh()
        self.refresh_holdings(refresh_prices=False)

    def reset_portfolio(self) -> None:
        if not self.controller.holdings():
            messagebox.showinfo("Portfolio empty", "There are no positions to delete.", parent=self)
            return
        if not messagebox.askyesno(
            "Reset portfolio",
            "Are you sure you want to delete every position in this portfolio?",
            icon="warning",
            parent=self,
        ):
            return
        try:
            self.controller.clear_portfolio()
        except Exception as exc:
            messagebox.showerror("Could not reset portfolio", str(exc), parent=self)
            return
        self._invalidate_portfolio_refresh()
        self.refresh_holdings(refresh_prices=False)

    def delete_position(self, ticker: str) -> None:
        if not messagebox.askyesno(
            "Delete position",
            f"Delete {ticker} and all of its recorded purchase lots?",
            icon="warning",
            parent=self,
        ):
            return
        try:
            self.controller.delete_position(ticker)
        except Exception as exc:
            messagebox.showerror("Could not delete position", str(exc), parent=self)
            return
        self._invalidate_portfolio_refresh()
        self.refresh_holdings(refresh_prices=False)

    def start_position_risk_review(self) -> None:
        tickers = [holding.ticker for holding in self.controller.holdings()]
        if not tickers:
            messagebox.showinfo("Portfolio empty", "Add a position before reviewing risk.", parent=self)
            return
        dialog = PositionRiskDialog(self, tickers)
        self.wait_window(dialog)
        if dialog.result is not None:
            selected_tickers, user_context = dialog.result
            self._start_position_risk_worker(selected_tickers, user_context)

    def start_industry_research(self) -> None:
        dialog = IndustryResearchDialog(self, self.controller.industry_research_options())
        self.wait_window(dialog)
        if dialog.result is None:
            return
        industry, count = dialog.result
        self._start_industry_research_worker(industry, count)

    def refresh_market_regime(self) -> None:
        if self.market_refresh_busy:
            return
        self.market_refresh_busy = True
        self.market_status.set("Refreshing FRED and market observations...")
        self.market_refresh_button.configure(state="disabled")

        def worker() -> None:
            try:
                snapshot = self.controller.market_regime()
                self.results.put(("market_regime", snapshot))
            except Exception as exc:
                self.results.put(
                    ("market_regime", f"Refresh failed: {type(exc).__name__}: {exc}")
                )

        threading.Thread(target=worker, daemon=True).start()

    def show_macro_reference(self) -> None:
        window = tk.Toplevel(self)
        window.title("How macro signals affect stocks")
        window.geometry("1160x760")
        window.minsize(960, 620)
        window.transient(self)

        content = ttk.Frame(window, padding=20)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Core ideas to remember", style="Title.TLabel").pack(anchor="w")
        tk.Label(
            content,
            text=(
                '“Disproportionate” means the same market shock can cause a much larger percentage change '
                "in some companies because of when their cash flows arrive or how their capital is structured."
            ),
            background=self.BG,
            foreground=self.TEXT,
            font=(self.ui_font, 10),
            justify="left",
            anchor="w",
            wraplength=1100,
        ).pack(fill="x", pady=(10, 0))

        impact_examples = ttk.Frame(content)
        impact_examples.pack(fill="x", pady=(10, 16))
        impact_examples.columnconfigure((0, 1), weight=1, uniform="impact")

        examples = (
            (
                "HIGH GROWTH — DURATION",
                "PV = CF_t / (1 + r)^t",
                "If volatility makes investors demand a higher return (r), a cash flow 10 years away is "
                "discounted at that higher rate across 10 compounded periods; next year's cash flow is hit "
                "only once. High-growth companies have more value concentrated far in the future, so they "
                "have greater duration and a larger valuation response.",
            ),
            (
                "HIGH LEVERAGE — EQUITY MAGNIFICATION",
                "Equity value = Asset value − Debt",
                "If assets are worth $120 and debt is $100, equity is $20. A 10% decline in asset value to "
                "$108 reduces equity to $8—a 60% decline. Debt does not fall alongside asset value, so "
                "leverage mechanically magnifies the change. Higher rates can also make borrowing and "
                "refinancing more expensive.",
            ),
        )
        for column, (heading, formula, explanation) in enumerate(examples):
            panel = ttk.Frame(impact_examples, style="Panel.TFrame", padding=12)
            panel.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0, 5) if column == 0 else (5, 0),
            )
            tk.Label(
                panel,
                text=heading,
                background=self.SURFACE,
                foreground=self.MUTED,
                font=(self.ui_font, 9, "bold"),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                panel,
                text=formula,
                background=self.SURFACE,
                foreground=self.ACCENT,
                font=(self.ui_font, 12, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(6, 5))
            tk.Label(
                panel,
                text=explanation,
                background=self.SURFACE,
                foreground=self.TEXT,
                font=(self.ui_font, 10),
                justify="left",
                anchor="nw",
                wraplength=510,
            ).pack(fill="x")

        table = ttk.Frame(content, style="Panel.TFrame", padding=1)
        table.pack(fill="both", expand=True)
        column_widths = (170, 130, 235, 520)
        for column, width in enumerate(column_widths):
            table.columnconfigure(column, weight=1 if column == 3 else 0, minsize=width)
        for column, heading in enumerate(
            ("Metric", "Unfavorable signal", "What to favor", "Mechanical reason")
        ):
            tk.Label(
                table,
                text=heading,
                background=self.SURFACE_ALT,
                foreground=self.MUTED,
                font=(self.ui_font, 9, "bold"),
                anchor="w",
                padx=10,
                pady=9,
            ).grid(row=0, column=column, sticky="nsew", padx=(0, 1), pady=(0, 1))
        for row_index, row in enumerate(MACRO_REFERENCE_ROWS, start=1):
            for column, value in enumerate(row):
                tk.Label(
                    table,
                    text=value,
                    background=self.SURFACE,
                    foreground=self.TEXT if column != 1 else self.NEGATIVE,
                    font=(self.ui_font, 9, "bold" if column < 3 else "normal"),
                    justify="left",
                    anchor="nw",
                    wraplength=column_widths[column] - 20,
                    padx=10,
                    pady=9,
                ).grid(row=row_index, column=column, sticky="nsew", padx=(0, 1), pady=(0, 1))

        ttk.Label(
            content,
            text=(
                "Favorable signals are the opposite: low rates, expanding Fed assets, low high-yield "
                "spreads, low VIX, and low or falling inflation. These conditions are more tolerant of "
                "high-growth or leveraged companies; they do not make leverage or unprofitability desirable."
            ),
            style="Muted.TLabel",
            wraplength=1080,
        ).pack(fill="x", anchor="w", pady=(12, 0))
        window.bind("<Escape>", lambda _event: window.destroy())

    def _render_market_regime(self, snapshot: MarketRegimeSnapshot) -> None:
        self.market_regime_title.set(snapshot.regime)
        self.market_company_fit.set(f"Stock profile to prioritize: {snapshot.company_fit}")
        for item in self.market_tree.get_children():
            self.market_tree.delete(item)
        for indicator in snapshot.indicators:
            if indicator.status != "available":
                tags = ("unavailable",)
            else:
                tags = {
                    DEFENSIVE_MACRO_TILT: ("safe",),
                    NEUTRAL_MACRO_TILT: ("neutral",),
                    TOLERANT_MACRO_TILT: ("risk_tolerant",),
                }.get(indicator.macro_tilt, ())
            self.market_tree.insert(
                "",
                "end",
                tags=tags,
                values=(
                    indicator.label,
                    indicator.latest,
                    indicator.trend,
                    indicator.macro_tilt,
                    self._display_date(indicator.as_of),
                ),
            )
        timestamp = snapshot.generated_at.astimezone().strftime("%-I:%M %p")
        unavailable = sum(
            indicator.status != "available" for indicator in snapshot.indicators
        )
        suffix = f" | {unavailable} unavailable" if unavailable else ""
        self.market_status.set(f"Updated {timestamp}{suffix}")

    @staticmethod
    def _display_date(value: str) -> str:
        try:
            if "T" in value:
                parsed_time = datetime.fromisoformat(value)
                return parsed_time.strftime("%b %-d, %Y %-I:%M %p %Z").strip()
            parsed = date.fromisoformat(value)
            return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"
        except ValueError:
            return value

    def refresh_holdings(self, *, refresh_prices: bool = True) -> None:
        if refresh_prices:
            if self.portfolio_refresh_busy:
                return
            self.portfolio_refresh_busy = True
            self.portfolio_status.set("Refreshing prices and performance...")
            self.portfolio_refresh_button.configure(state="disabled")
            generation = self.portfolio_refresh_generation

            def worker() -> None:
                try:
                    holdings = self.controller.holding_snapshots()
                    history, position_histories, missing_tickers = (
                        self.controller.performance_histories()
                    )
                    intraday_history, intraday_position_histories = (
                        self.controller.intraday_performance_histories()
                    )
                    self.results.put(
                        (
                            "portfolio_refresh",
                            {
                                "generation": generation,
                                "holdings": holdings,
                                "history": history,
                                "position_histories": position_histories,
                                "intraday_history": intraday_history,
                                "intraday_position_histories": intraday_position_histories,
                                "missing_history_tickers": missing_tickers,
                            },
                        )
                    )
                except Exception as exc:
                    self.results.put(
                        (
                            "portfolio_refresh",
                            {
                                "generation": generation,
                                "error": (
                                    "Price refresh failed: "
                                    f"{type(exc).__name__}: {exc}"
                                ),
                            },
                        )
                    )

            threading.Thread(target=worker, daemon=True).start()
            return

        try:
            holdings = self.controller.holdings()
        except Exception as exc:
            self.portfolio_status.set(
                f"Could not load portfolio: {type(exc).__name__}: {exc}"
            )
            return
        self._render_holdings(holdings, refresh_prices=False)

    def _invalidate_portfolio_refresh(self) -> None:
        self.portfolio_refresh_generation += 1
        self.portfolio_refresh_busy = False
        self.performance_portfolio_history = []
        self.performance_position_histories = {}
        self.performance_intraday_portfolio_history = []
        self.performance_intraday_position_histories = {}
        self.performance_history_missing_tickers = ()
        self.selected_performance_ticker = None
        if hasattr(self, "portfolio_refresh_button"):
            self.portfolio_refresh_button.configure(state="normal")

    def _render_holdings(
        self,
        holdings: list[Any],
        *,
        refresh_prices: bool,
        history: list[Any] | None = None,
        position_histories: dict[str, list[Any]] | None = None,
        intraday_history: list[Any] | None = None,
        intraday_position_histories: dict[str, list[Any]] | None = None,
        missing_history_tickers: tuple[str, ...] = (),
    ) -> None:
        self.current_holdings = list(holdings)
        if refresh_prices:
            self.performance_portfolio_history = list(history or [])
            self.performance_position_histories = dict(position_histories or {})
            self.performance_intraday_portfolio_history = list(intraday_history or [])
            self.performance_intraday_position_histories = dict(
                intraday_position_histories or {}
            )
            self.performance_history_missing_tickers = tuple(missing_history_tickers)
        for item in self.holdings_tree.get_children():
            self.holdings_tree.delete(item)
        total_cost = sum(holding.total_cost for holding in holdings)
        priced = [
            holding
            for holding in holdings
            if getattr(holding, "market_value", None) is not None
        ]
        total_value = sum(holding.market_value for holding in priced)
        total_gain = sum(holding.gain_loss for holding in priced)
        priced_cost = sum(holding.total_cost for holding in priced)
        total_return = total_gain / priced_cost * 100 if priced_cost else None

        self.portfolio_cost.set(f"${total_cost:,.2f}")
        self.portfolio_value.set(f"${total_value:,.2f}" if priced else "—")
        self.portfolio_gain.set(f"${total_gain:+,.2f}" if priced else "—")
        self.portfolio_return.set(f"{total_return:+,.2f}%" if total_return is not None else "—")
        tone = "Positive.MetricValue.TLabel" if total_gain >= 0 else "Negative.MetricValue.TLabel"
        if not priced:
            tone = "MetricValue.TLabel"
        self.portfolio_gain_label.configure(style=tone)
        self.portfolio_return_label.configure(style=tone)

        self._render_holding_rows()

        if refresh_prices:
            if holdings and self.performance_history_missing_tickers:
                unavailable = ", ".join(self.performance_history_missing_tickers)
                self.portfolio_status.set(
                    f"Prices refreshed; history unavailable for {unavailable}"
                )
            else:
                self.portfolio_status.set(
                    f"Prices refreshed {datetime.now().strftime('%-I:%M %p')}"
                    if holdings
                    else "No holdings yet"
                )
        elif not holdings:
            self.performance_portfolio_history = []
            self.performance_position_histories = {}
            self.performance_intraday_portfolio_history = []
            self.performance_intraday_position_histories = {}
            self.performance_history_missing_tickers = ()
            self.selected_performance_ticker = None
            self.portfolio_status.set("No holdings yet")
        else:
            self.portfolio_status.set("Refresh prices to update market values")
        available_tickers = {holding.ticker for holding in holdings}
        if self.selected_performance_ticker not in available_tickers:
            self.selected_performance_ticker = None
        elif self.selected_performance_ticker:
            self.holdings_tree.selection_set(self.selected_performance_ticker)
        self._select_performance_history()

    def _period_label(self) -> str:
        return {
            0: "all time",
            1: "1 day",
            3: "3 days",
            5: "1 week",
            10: "2 weeks",
            15: "3 weeks",
            20: "4 weeks",
        }.get(self.performance_sessions, f"{self.performance_sessions} sessions")

    def _render_holding_rows(self) -> None:
        if not hasattr(self, "holdings_tree"):
            return
        for button in self.delete_buttons.values():
            button.destroy()
        self.delete_buttons.clear()
        for item in self.holdings_tree.get_children():
            self.holdings_tree.delete(item)
        period = self._period_label()
        self.holdings_headings["gain_loss"] = f"Gain/loss ({period})"
        self.holdings_headings["return_percent"] = f"Return ({period})"
        self._render_holding_headings()
        rows: list[dict[str, Any]] = []
        for holding in self.current_holdings:
            if self.performance_sessions == 0:
                performance = (
                    (holding.gain_loss, holding.return_percent)
                    if getattr(holding, "gain_loss", None) is not None
                    and getattr(holding, "return_percent", None) is not None
                    else None
                )
            else:
                performance = period_performance(
                    self.performance_position_histories.get(holding.ticker, []),
                    self.performance_sessions,
                )
            gain_loss, return_percent = performance if performance is not None else (None, None)
            rows.append(
                {
                    "holding": holding,
                    "ticker": holding.ticker,
                    "quantity": holding.quantity,
                    "average_cost": holding.average_cost,
                    "total_cost": holding.total_cost,
                    "current_price": getattr(holding, "current_price", None),
                    "market_value": getattr(holding, "market_value", None),
                    "gain_loss": gain_loss,
                    "return_percent": return_percent,
                }
            )
        if self.holdings_sort_column:
            rows = sort_portfolio_rows(
                rows,
                self.holdings_sort_column,
                descending=self.holdings_sort_descending,
            )
        for row in rows:
            holding = row["holding"]
            gain_loss = row["gain_loss"]
            return_percent = row["return_percent"]
            row_tag = ""
            if gain_loss is not None:
                row_tag = "positive" if gain_loss >= 0 else "negative"
            self.holdings_tree.insert(
                "",
                "end",
                iid=holding.ticker,
                tags=(row_tag,) if row_tag else (),
                values=(
                    holding.ticker,
                    f"{holding.quantity:g}",
                    f"${holding.average_cost:,.2f}",
                    f"${holding.total_cost:,.2f}",
                    f"${holding.current_price:,.2f}" if getattr(holding, "current_price", None) is not None else "N/A",
                    f"${holding.market_value:,.2f}" if getattr(holding, "market_value", None) is not None else "N/A",
                    f"${gain_loss:+,.2f}" if gain_loss is not None else "N/A",
                    f"{return_percent:+,.2f}%" if return_percent is not None else "N/A",
                    "",
                ),
            )
            self.delete_buttons[holding.ticker] = ttk.Button(
                self.holdings_tree,
                text="×",
                style="DeleteIcon.TButton",
                command=lambda ticker=holding.ticker: self.delete_position(ticker),
                takefocus=False,
            )
        if self.selected_performance_ticker in self.holdings_tree.get_children():
            self.holdings_tree.selection_set(self.selected_performance_ticker)
        self.after_idle(self._position_delete_buttons)

    def _sort_holdings(self, column: str) -> None:
        if column not in NUMERIC_PORTFOLIO_COLUMNS and column != "ticker":
            return
        if self.holdings_sort_column == column:
            self.holdings_sort_descending = not self.holdings_sort_descending
        else:
            self.holdings_sort_column = column
            self.holdings_sort_descending = False
        self._render_holding_rows()

    def _render_holding_headings(self) -> None:
        for column, label in self.holdings_headings.items():
            indicator = ""
            if column == self.holdings_sort_column:
                indicator = " ▼" if self.holdings_sort_descending else " ▲"
            self.holdings_tree.heading(column, text=f"{label}{indicator}")

    def _scroll_holdings(self, *args: str) -> None:
        self.holdings_tree.yview(*args)
        self.after_idle(self._position_delete_buttons)

    def _update_holdings_scrollbar(self, first: str, last: str) -> None:
        self.holdings_scrollbar.set(first, last)
        self.after_idle(self._position_delete_buttons)

    def _position_delete_buttons(self) -> None:
        if not hasattr(self, "holdings_tree") or not self.holdings_tree.winfo_exists():
            return
        for ticker, button in self.delete_buttons.items():
            if not button.winfo_exists():
                continue
            box = self.holdings_tree.bbox(ticker, "delete")
            if not box:
                button.place_forget()
                continue
            x, y, width, height = box
            button_width = min(26, max(20, width - 8))
            button_height = min(26, max(20, height - 8))
            button.place(
                x=x + (width - button_width) // 2,
                y=y + (height - button_height) // 2,
                width=button_width,
                height=button_height,
            )
            button.lift()

    def _toggle_holding_chart(self, event: tk.Event) -> str | None:
        row = self.holdings_tree.identify_row(event.y)
        if not row:
            return None
        ticker = str(self.holdings_tree.item(row, "values")[0])
        if self.selected_performance_ticker == ticker:
            self.selected_performance_ticker = None
            self.holdings_tree.selection_remove(row)
        else:
            self.selected_performance_ticker = ticker
            self.holdings_tree.selection_set(row)
            self.holdings_tree.focus(row)
        self._select_performance_history()
        return "break"

    def _select_performance_history(self) -> None:
        ticker = self.selected_performance_ticker
        if ticker:
            self.performance_title.set(f"{ticker} performance")
            self.performance_history = list(
                self.performance_position_histories.get(ticker, [])
            )
        else:
            title = "Portfolio performance"
            if self.performance_history_missing_tickers:
                title += " (partial)"
            self.performance_title.set(title)
            self.performance_history = list(self.performance_portfolio_history)
        self._draw_performance()

    def _set_performance_period(self, sessions: int) -> None:
        self.performance_sessions = sessions
        for value, button in self.period_buttons.items():
            button.configure(style="Selected.Segment.TButton" if value == sessions else "Segment.TButton")
        self._render_holding_rows()
        self._draw_performance()

    def _draw_performance(self) -> None:
        if not hasattr(self, "performance_canvas"):
            return
        canvas = self.performance_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 640)
        height = max(canvas.winfo_height(), 118)
        if self.performance_sessions == 0:
            holdings = [
                holding
                for holding in self.current_holdings
                if (
                    self.selected_performance_ticker is None
                    or holding.ticker == self.selected_performance_ticker
                )
                and getattr(holding, "market_value", None) is not None
            ]
            points = self.performance_history
            values = [point.market_value for point in points]
            start_label = _performance_time_label(points[0].as_of) if points else ""
            end_label = _performance_time_label(points[-1].as_of) if points else ""
            total_cost = sum(holding.total_cost for holding in holdings)
            current_value = sum(holding.market_value for holding in holdings)
            summary_values = (total_cost, current_value)
        elif self.performance_sessions == 1:
            if self.selected_performance_ticker:
                points = self.performance_intraday_position_histories.get(
                    self.selected_performance_ticker, []
                )
            else:
                points = self.performance_intraday_portfolio_history
            if len(points) < 2:
                points = self.performance_history[-2:]
            values = [point.market_value for point in points]
            start_label = _performance_time_label(points[0].as_of) if points else ""
            end_label = _performance_time_label(points[-1].as_of, end=True) if points else ""
            summary_values = (values[0], values[-1]) if len(values) >= 2 else None
        else:
            points = self.performance_history[-(self.performance_sessions + 1):]
            values = [point.market_value for point in points]
            start_label = points[0].as_of.strftime("%b %-d") if points else ""
            end_label = points[-1].as_of.strftime("%b %-d") if points else ""
            summary_values = (values[0], values[-1]) if len(values) >= 2 else None
        if len(values) < 2 or summary_values is None or summary_values[0] == 0:
            self.period_change.set("History unavailable")
            self.period_change_label.configure(style="MetricValue.TLabel")
            canvas.create_text(
                18,
                height / 2,
                anchor="w",
                text=(
                    f"Not enough market history for {self.selected_performance_ticker}"
                    if self.selected_performance_ticker
                    else "Not enough market history for this portfolio"
                ),
                fill=self.MUTED,
                font=(self.ui_font, 10),
            )
            return

        start_value, end_value = summary_values
        change = end_value - start_value
        percent = change / start_value * 100 if start_value else 0.0
        color = self.POSITIVE if change >= 0 else self.NEGATIVE
        self.period_change.set(f"${change:+,.2f}  {percent:+.2f}%")
        self.period_change_label.configure(
            style="Positive.MetricValue.TLabel" if change >= 0 else "Negative.MetricValue.TLabel"
        )

        left, right, top, bottom = 18, width - 18, 10, height - 24
        low, high = min(values), max(values)
        span = high - low or 1.0
        for fraction in (0.0, 0.5, 1.0):
            y = top + (bottom - top) * fraction
            canvas.create_line(left, y, right, y, fill=self.BORDER, width=1)
        coordinates: list[float] = []
        for index, value in enumerate(values):
            x = left + (right - left) * index / max(1, len(values) - 1)
            y = bottom - (value - low) / span * (bottom - top)
            coordinates.extend((x, y))
        canvas.create_line(*coordinates, fill=color, width=2, smooth=True)
        canvas.create_oval(coordinates[-2] - 3, coordinates[-1] - 3, coordinates[-2] + 3, coordinates[-1] + 3, fill=color, outline=color)
        canvas.create_text(left, height - 8, anchor="w", text=start_label, fill=self.MUTED, font=(self.ui_font, 8))
        canvas.create_text(right, height - 8, anchor="e", text=end_label, fill=self.MUTED, font=(self.ui_font, 8))


def main() -> None:
    app = StockAgentApp()
    app.mainloop()


if __name__ == "__main__":
    main()
