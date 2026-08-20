"""Tkinter desktop interface for Stock Agent."""

from __future__ import annotations

from datetime import date, datetime
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from portfolio.config import save_supabase_settings
from portfolio.controller import StockAgentController
from portfolio.cloud_portfolios import AuthResult, friendly_auth_error
from portfolio.market_regime import (
    MACRO_REFERENCE_ROWS,
    MacroResearchPolicy,
    MarketRegimeSnapshot,
)


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
        self._send_button_hovered = False
        self.market_refresh_busy = False
        self.portfolio_refresh_busy = False
        self.portfolio_refresh_generation = 0
        self.pending_chat_draft: str | None = None
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
        style.configure("TCheckbutton", background=self.BG, foreground=self.TEXT, font=(self.ui_font, 10))
        style.map("TCheckbutton", background=[("active", self.BG)], foreground=[("active", self.TEXT)])
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
        notebook.add(self.chat_tab, text="Chat")
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
        ttk.Label(header, text="Chat", style="Title.TLabel").pack(side="left")

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
        self.transcript.insert("end", "Agent:\nReady. Enter a request below.\n\n")
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

        input_frame = ttk.Frame(self.chat_tab)
        input_frame.pack(side="bottom", fill="x", pady=(12, 0))
        self.input_box = tk.Text(
            input_frame,
            height=4,
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
            highlightcolor=self.ACCENT,
            padx=12,
            pady=10,
        )
        self.input_box.pack(side="left", fill="both", expand=True)
        self.send_button = ttk.Button(
            input_frame,
            text="Send",
            style="Accent.TButton",
            command=self._send_or_stop,
            width=14,
        )
        self.send_button.pack(side="left", padx=(12, 0), fill="y")
        self.send_button.bind("<Enter>", self._on_send_button_enter)
        self.send_button.bind("<Leave>", self._on_send_button_leave)
        self.input_box.bind("<Command-Return>", self._send_event)
        self.input_box.bind("<Control-Return>", self._send_event)
        self.input_box.bind("<Command-a>", self._select_all_input)
        self.input_box.bind("<Left>", lambda event: self._collapse_input_selection(event, "start"))
        self.input_box.bind("<Right>", lambda event: self._collapse_input_selection(event, "end"))
        self.progress_frame.pack(side="bottom", fill="x", pady=(10, 0))
        transcript_frame.pack(side="top", fill="both", expand=True)
        self.input_box.focus_set()

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
            command=self.prepare_position_risk_prompt,
        ).pack(side="left", padx=8)
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
            (3, "3 days"),
            (5, "1 week"),
            (10, "2 weeks"),
            (15, "3 weeks"),
            (20, "4 weeks"),
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
        self.selected_performance_ticker: str | None = None
        self.performance_sessions = 3
        self._set_performance_period(3)

        columns = (
            "ticker",
            "quantity",
            "average_cost",
            "total_cost",
            "current_price",
            "market_value",
            "gain_loss",
            "return_percent",
        )
        self.holdings_tree = ttk.Treeview(self.holdings_tab, columns=columns, show="headings")
        headings = {
            "ticker": "Ticker",
            "quantity": "Shares",
            "average_cost": "Avg. purchase price",
            "total_cost": "Total cost",
            "current_price": "Current price",
            "market_value": "Total value",
            "gain_loss": "Gain/loss",
            "return_percent": "Return",
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
        }
        for column in columns:
            self.holdings_tree.heading(column, text=headings[column])
            self.holdings_tree.column(column, width=widths[column], anchor="center")
        scrollbar = ttk.Scrollbar(self.holdings_tab, orient="vertical", command=self.holdings_tree.yview)
        self.holdings_tree.configure(yscrollcommand=scrollbar.set)
        self.holdings_tree.tag_configure("positive", foreground=self.POSITIVE)
        self.holdings_tree.tag_configure("negative", foreground=self.NEGATIVE)
        self.holdings_tree.bind("<Button-1>", self._toggle_holding_chart, add="+")
        self.holdings_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
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

        overview = ttk.Frame(self.market_tab, style="Panel.TFrame", padding=(20, 15))
        overview.pack(fill="x", pady=(0, 14))
        self.market_regime_title = tk.StringVar(value="Regime not calculated")
        self.market_regime_summary = tk.StringVar(
            value="The Fed policy-rate and balance-sheet directions determine the framework quadrant."
        )
        self.market_company_fit = tk.StringVar(
            value="Stock profile to prioritize: waiting for a complete market regime."
        )
        ttk.Label(
            overview, textvariable=self.market_regime_title, style="SurfaceSection.TLabel"
        ).pack(anchor="w")
        tk.Label(
            overview,
            textvariable=self.market_regime_summary,
            background=self.SURFACE,
            foreground=self.MUTED,
            font=(self.ui_font, 10),
            justify="left",
            anchor="w",
            wraplength=1030,
        ).pack(fill="x", pady=(6, 0))
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

        policy = ttk.Frame(self.market_tab, style="Panel.TFrame", padding=(18, 12))
        policy.pack(fill="x", pady=(0, 14))
        policy_header = ttk.Frame(policy, style="Surface.TFrame")
        policy_header.pack(fill="x", pady=(0, 6))
        ttk.Label(
            policy_header,
            text="How research uses this market",
            style="SurfaceSection.TLabel",
        ).pack(side="left")
        instruction_row = ttk.Frame(policy, style="Surface.TFrame")
        instruction_row.pack(fill="x")
        self.research_instruction = tk.StringVar(
            value="Waiting for market data."
        )
        tk.Label(
            instruction_row,
            textvariable=self.research_instruction,
            background=self.SURFACE,
            foreground=self.MUTED,
            font=(self.ui_font, 9),
            justify="left",
            anchor="w",
            wraplength=930,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            instruction_row,
            text="Start research",
            style="Accent.TButton",
            command=self.prepare_market_research_prompt,
        ).pack(side="right", padx=(12, 0))

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
            "favored": "Favored company type",
            "as_of": "Data date",
        }
        widths = {
            "indicator": 185,
            "latest": 105,
            "trend": 200,
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
        self.market_tree.pack(fill="x", pady=(0, 7))

        ttk.Label(
            self.market_tab,
            text=(
                "Each row translates the current signal into the same two company profiles. The app combines "
                "all five rows before setting research priorities; no single indicator is a buy or sell signal."
            ),
            style="Muted.TLabel",
            wraplength=1060,
            justify="left",
        ).pack(fill="x", anchor="w", pady=(0, 12))

        ttk.Label(self.market_tab, text="What this market favors", style="Section.TLabel").pack(
            anchor="w", pady=(2, 8)
        )
        self.market_emphasis = tk.Text(
            self.market_tab,
            height=3,
            wrap="word",
            background=self.BG,
            foreground=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=(self.ui_font, 10),
            padx=0,
            pady=0,
        )
        self.market_emphasis.pack(fill="both", expand=True)
        self.market_emphasis.insert(
            "1.0", "Refresh the data to generate evidence-based portfolio considerations."
        )
        self.market_emphasis.configure(state="disabled")
        self.market_missing = tk.StringVar(
            value="This view is a market-condition checklist, not a buy or sell signal."
        )
        ttk.Label(
            self.market_tab,
            textvariable=self.market_missing,
            style="Muted.TLabel",
            wraplength=1050,
            justify="left",
        ).pack(fill="x", anchor="w", pady=(10, 0))

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

    def _send_event(self, _event: tk.Event) -> str:
        self._send_or_stop()
        return "break"

    def _select_all_input(self, _event: tk.Event) -> str:
        self.input_box.tag_add("sel", "1.0", "end-1c")
        self.input_box.mark_set("insert", "end-1c")
        self.input_box.see("insert")
        return "break"

    def _collapse_input_selection(self, _event: tk.Event, edge: str) -> str | None:
        selection = self.input_box.tag_ranges("sel")
        if len(selection) != 2:
            return None
        target = selection[0] if edge == "start" else selection[1]
        self.input_box.tag_remove("sel", "1.0", "end")
        self.input_box.mark_set("insert", target)
        self.input_box.see("insert")
        return "break"

    def _send_or_stop(self) -> None:
        if self.is_busy:
            self._request_stop()
            return
        self.send_message()

    def _on_send_button_enter(self, _event: tk.Event) -> None:
        self._send_button_hovered = True
        self._refresh_send_button_text()

    def _on_send_button_leave(self, _event: tk.Event) -> None:
        self._send_button_hovered = False
        self._refresh_send_button_text()

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
        self._refresh_send_button_text()
        self._replace_live_progress_message()

    def send_message(self) -> None:
        if self.is_busy:
            return
        message = self.input_box.get("1.0", "end").strip()
        if not message:
            return
        self.input_box.delete("1.0", "end")
        self._append("You", message)
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        self._set_busy(True, cancellable=True)
        threading.Thread(
            target=self._message_worker, args=(message, cancel_event), daemon=True
        ).start()

    def _message_worker(self, message: str, cancel_event: threading.Event) -> None:
        def progress_callback(percent: int | None, stage: str, detail: str = "") -> None:
            self.results.put(
                (
                    "progress",
                    {"percent": percent, "stage": stage, "detail": detail},
                )
            )

        try:
            response = self.controller.handle_message(
                message,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            response = f"Unexpected error: {type(exc).__name__}: {exc}"
        self.results.put(("message", response))

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
                        )
                    continue
                response = str(payload)
                self._finish_progress(response)
                self._append("Agent", response)
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
        self.current_task_cancellable = busy and cancellable
        self.input_box.configure(state="disabled" if busy else "normal")
        if busy:
            self.stop_requested = False
            self._send_button_hovered = False
            self.send_button.configure(state="normal" if cancellable else "disabled")
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
            self._send_button_hovered = False
            self.send_button.configure(state="normal", text="Send")
            if self.pending_chat_draft is not None:
                draft = self.pending_chat_draft
                self.pending_chat_draft = None
                self._apply_chat_draft(draft)
            self.input_box.focus_set()

    def _refresh_send_button_text(self) -> None:
        if not self.is_busy:
            self.send_button.configure(text="Send")
            return
        if self.stop_requested:
            self.send_button.configure(text="Stopping...", state="disabled")
        elif self.current_task_cancellable and self._send_button_hovered:
            self.send_button.configure(
                text="Stop research" if self.research_progress_seen else "Stop",
                state="normal",
            )
        elif self.current_task_cancellable and self.research_progress_seen:
            self.send_button.configure(text=f"Researching... {self.current_progress}%", state="normal")
        elif self.current_task_cancellable:
            self.send_button.configure(text="Working...", state="normal")
        else:
            self.send_button.configure(text="Working...", state="disabled")

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
        self._refresh_send_button_text()
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
            "Agent:\n"
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

    def _finish_progress(self, response: str) -> None:
        stopped = response.startswith("Research stopped.")
        failed = response.startswith("LSEG research could not run") or response.startswith("Unexpected error")
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
            "Agent",
            f"Recorded {purchase.quantity:g} shares of {purchase.ticker} at ${purchase.price:,.2f}.",
        )
        self.refresh_holdings(refresh_prices=False)

    def _submit_prompt(self, prompt: str) -> None:
        self.input_box.delete("1.0", "end")
        self.input_box.insert("1.0", prompt)
        self.send_message()

    def prepare_position_risk_prompt(self) -> None:
        prompt = (
            "Review my portfolio positions for reasons to hold, review, trim, "
            "or consider exiting."
        )
        self.notebook.select(self.chat_tab)
        if self.is_busy:
            self.pending_chat_draft = prompt
            return
        self._apply_chat_draft(prompt)

    def _apply_chat_draft(self, prompt: str) -> None:
        self.input_box.delete("1.0", "end")
        self.input_box.insert("1.0", prompt)
        self.input_box.focus_set()

    def research_stock(self) -> None:
        query = simpledialog.askstring(
            "Deep LSEG research",
            "Enter a complete request, company name, comparison, or stock screen:",
            parent=self,
        )
        if not query:
            return
        prompt = query if len(query.split()) > 2 else f"Analyze {query} using LSEG"
        self._submit_prompt(prompt)

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

    def _render_research_policy(self, policy: MacroResearchPolicy) -> None:
        self.research_instruction.set(
            policy.focus_summary() + " " + " ".join(policy.rules)
        )

    def prepare_market_research_prompt(self) -> None:
        prompt = "Research promising technology stocks."
        self.notebook.select(self.chat_tab)
        if self.is_busy:
            self.pending_chat_draft = prompt
            return
        self._apply_chat_draft(prompt)

    def show_macro_reference(self) -> None:
        window = tk.Toplevel(self)
        window.title("How macro signals affect stocks")
        window.geometry("1160x690")
        window.minsize(960, 620)
        window.transient(self)

        content = ttk.Frame(window, padding=20)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Core ideas to remember", style="Title.TLabel").pack(anchor="w")
        tk.Label(
            content,
            text=(
                "Valuation effect: A higher discount rate lowers the present value of future cash flows. "
                "When Treasury bonds already offer a high safe return, investors require more return from "
                "risky stocks. This disproportionately hurts high-growth companies whose expected cash flows "
                "are further in the future."
            ),
            background=self.BG,
            foreground=self.TEXT,
            font=(self.ui_font, 10),
            justify="left",
            anchor="w",
            wraplength=1100,
        ).pack(fill="x", pady=(10, 0))
        tk.Label(
            content,
            text=(
                "Borrowing effect: Higher market rates make bank loans and bond financing more expensive. "
                "This disproportionately hurts high-leverage companies that depend on borrowing or refinancing."
            ),
            background=self.BG,
            foreground=self.TEXT,
            font=(self.ui_font, 10),
            justify="left",
            anchor="w",
            wraplength=1100,
        ).pack(fill="x", pady=(8, 16))

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
                "spreads, low VIX, and low or falling inflation."
            ),
            style="Muted.TLabel",
            wraplength=1080,
        ).pack(fill="x", anchor="w", pady=(12, 0))
        window.bind("<Escape>", lambda _event: window.destroy())

    def _render_market_regime(self, snapshot: MarketRegimeSnapshot) -> None:
        self.market_regime_title.set(snapshot.regime)
        self.market_regime_summary.set(snapshot.summary)
        self.market_company_fit.set(f"Stock profile to prioritize: {snapshot.company_fit}")
        self._render_research_policy(self.controller.research_policy())
        for item in self.market_tree.get_children():
            self.market_tree.delete(item)
        for indicator in snapshot.indicators:
            tags = ("unavailable",) if indicator.status != "available" else ()
            self.market_tree.insert(
                "",
                "end",
                tags=tags,
                values=(
                    indicator.label,
                    indicator.latest,
                    indicator.trend,
                    indicator.favored_company_type,
                    self._display_date(indicator.as_of),
                ),
            )
        self.market_emphasis.configure(state="normal")
        self.market_emphasis.delete("1.0", "end")
        self.market_emphasis.insert(
            "1.0", "\n".join(f"- {item}" for item in snapshot.emphasis)
        )
        self.market_emphasis.configure(state="disabled")
        missing = " ".join(snapshot.missing_evidence)
        self.market_missing.set(
            f"Not yet measured: {missing} This is a market-condition checklist, not a buy or sell signal."
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
                    history, position_histories = self.controller.performance_histories()
                    self.results.put(
                        (
                            "portfolio_refresh",
                            {
                                "generation": generation,
                                "holdings": holdings,
                                "history": history,
                                "position_histories": position_histories,
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
        if hasattr(self, "portfolio_refresh_button"):
            self.portfolio_refresh_button.configure(state="normal")

    def _render_holdings(
        self,
        holdings: list[Any],
        *,
        refresh_prices: bool,
        history: list[Any] | None = None,
        position_histories: dict[str, list[Any]] | None = None,
    ) -> None:
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

        for holding in holdings:
            gain_loss = getattr(holding, "gain_loss", None)
            return_percent = getattr(holding, "return_percent", None)
            row_tag = "positive" if gain_loss is not None and gain_loss >= 0 else "negative"
            if gain_loss is None:
                row_tag = ""
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
                    (
                        f"${holding.current_price:,.2f}"
                        if getattr(holding, "current_price", None) is not None
                        else "N/A"
                    ),
                    (
                        f"${holding.market_value:,.2f}"
                        if getattr(holding, "market_value", None) is not None
                        else "N/A"
                    ),
                    (
                        f"${holding.gain_loss:+,.2f}"
                        if gain_loss is not None
                        else "N/A"
                    ),
                    f"{return_percent:+,.2f}%" if return_percent is not None else "N/A",
                ),
            )

        if refresh_prices:
            self.performance_portfolio_history = list(history or [])
            self.performance_position_histories = dict(position_histories or {})
            self.portfolio_status.set(
                f"Prices refreshed {datetime.now().strftime('%-I:%M %p')}"
                if holdings
                else "No holdings yet"
            )
        elif not holdings:
            self.performance_portfolio_history = []
            self.performance_position_histories = {}
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
            self.performance_title.set("Portfolio performance")
            self.performance_history = list(self.performance_portfolio_history)
        self._draw_performance()

    def _set_performance_period(self, sessions: int) -> None:
        self.performance_sessions = sessions
        for value, button in self.period_buttons.items():
            button.configure(style="Selected.Segment.TButton" if value == sessions else "Segment.TButton")
        self._draw_performance()

    def _draw_performance(self) -> None:
        if not hasattr(self, "performance_canvas"):
            return
        canvas = self.performance_canvas
        canvas.delete("all")
        points = self.performance_history[-(self.performance_sessions + 1):]
        width = max(canvas.winfo_width(), 640)
        height = max(canvas.winfo_height(), 118)
        if len(points) < 2:
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

        start_value = points[0].market_value
        end_value = points[-1].market_value
        change = end_value - start_value
        percent = change / start_value * 100 if start_value else 0.0
        color = self.POSITIVE if change >= 0 else self.NEGATIVE
        self.period_change.set(f"${change:+,.2f}  {percent:+.2f}%")
        self.period_change_label.configure(
            style="Positive.MetricValue.TLabel" if change >= 0 else "Negative.MetricValue.TLabel"
        )

        left, right, top, bottom = 18, width - 18, 10, height - 24
        values = [point.market_value for point in points]
        low, high = min(values), max(values)
        span = high - low or 1.0
        for fraction in (0.0, 0.5, 1.0):
            y = top + (bottom - top) * fraction
            canvas.create_line(left, y, right, y, fill=self.BORDER, width=1)
        coordinates: list[float] = []
        for index, point in enumerate(points):
            x = left + (right - left) * index / max(1, len(points) - 1)
            y = bottom - (point.market_value - low) / span * (bottom - top)
            coordinates.extend((x, y))
        canvas.create_line(*coordinates, fill=color, width=2, smooth=True)
        canvas.create_oval(coordinates[-2] - 3, coordinates[-1] - 3, coordinates[-2] + 3, coordinates[-1] + 3, fill=color, outline=color)
        canvas.create_text(left, height - 8, anchor="w", text=points[0].as_of.strftime("%b %-d"), fill=self.MUTED, font=(self.ui_font, 8))
        canvas.create_text(right, height - 8, anchor="e", text=points[-1].as_of.strftime("%b %-d"), fill=self.MUTED, font=(self.ui_font, 8))


def main() -> None:
    app = StockAgentApp()
    app.mainloop()


if __name__ == "__main__":
    main()
