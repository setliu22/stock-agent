"""Tkinter desktop interface for Stock Agent."""

from __future__ import annotations

from datetime import date
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from portfolio.config import save_supabase_settings
from portfolio.controller import StockAgentController
from portfolio.supabase_auth import AuthResult, SupabaseAuth, friendly_auth_error


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
    def __init__(self) -> None:
        super().__init__()
        self.title("Stock Agent")
        self.geometry("1180x760")
        self.minsize(900, 620)
        self.controller = StockAgentController()
        self.supabase_auth: SupabaseAuth | None = None
        self.auth_busy = False
        self.results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.research_started_at: float | None = None
        self.current_progress = 0
        self.current_stage = "Idle"
        self.current_detail = ""
        self._last_elapsed_second = -1
        self.current_step_started_at: float | None = None
        self.is_busy = False
        self.current_task_cancellable = False
        self.stop_requested = False
        self.cancel_event: threading.Event | None = None
        self._send_button_hovered = False
        self._configure_style()
        self._build_ui()
        self.after(100, self._poll_results)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("aqua")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Helvetica", 20, "bold"))
        style.configure("ProgressTitle.TLabel", font=("Helvetica", 12, "bold"))
        style.configure("ProgressDetail.TLabel", font=("Helvetica", 10))
        style.configure("Treeview", rowheight=27)
        style.configure("Treeview.Heading", font=("Helvetica", 12, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=(18, 12, 18, 18))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Stock Agent", style="Title.TLabel").pack(anchor="w", pady=(0, 10))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        self.chat_tab = ttk.Frame(notebook, padding=16)
        self.holdings_tab = ttk.Frame(notebook, padding=16)
        self.account_tab = ttk.Frame(notebook, padding=16)
        notebook.add(self.chat_tab, text="Chat")
        notebook.add(self.holdings_tab, text="Portfolio Snapshot")
        notebook.add(self.account_tab, text="Account")
        self._build_chat_tab()
        self._build_holdings_tab()
        self._build_account_tab()

    def _build_chat_tab(self) -> None:
        actions = ttk.Frame(self.chat_tab)
        actions.pack(fill="x", pady=(0, 12))
        for text, command in (
            ("Record purchase", self.record_purchase),
            ("Show holdings", lambda: self._run_direct("holdings", self.controller.holdings_text)),
            ("Calculate return", lambda: self._run_direct("return", self.controller.return_text)),
            ("Deep research", self.research_stock),
            ("LSEG capabilities", lambda: self._submit_prompt("What can LSEG do?")),
        ):
            ttk.Button(actions, text=text, command=command).pack(side="left", expand=True, fill="x", padx=5)

        transcript_frame = ttk.Frame(self.chat_tab)
        transcript_frame.pack(fill="both", expand=True)
        self.transcript = ScrolledText(
            transcript_frame,
            wrap="word",
            font=("Menlo", 13),
            background="#171717",
            foreground="#f2f2f2",
            insertbackground="#ffffff",
            padx=14,
            pady=12,
        )
        self.transcript.pack(fill="both", expand=True)
        self.transcript.insert("end", "Agent:\nReady. Enter a request below.\n\n")
        self.transcript.configure(state="disabled")

        self.progress_frame = ttk.Frame(self.chat_tab, padding=(10, 8))
        self.progress_frame.pack(fill="x", pady=(10, 0))
        progress_header = ttk.Frame(self.progress_frame)
        progress_header.pack(fill="x")
        self.progress_status = ttk.Label(
            progress_header,
            text="Research status: idle",
            style="ProgressTitle.TLabel",
        )
        self.progress_status.pack(side="left", anchor="w")
        self.progress_percent = ttk.Label(progress_header, text="0%")
        self.progress_percent.pack(side="right", anchor="e")
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self.progress_bar.pack(fill="x", pady=(6, 4))
        progress_footer = ttk.Frame(self.progress_frame)
        progress_footer.pack(fill="x")
        self.progress_detail = ttk.Label(
            progress_footer,
            text="Progress updates will appear here during deep research.",
            style="ProgressDetail.TLabel",
        )
        self.progress_detail.pack(side="left", fill="x", expand=True, anchor="w")
        self.progress_elapsed = ttk.Label(progress_footer, text="")
        self.progress_elapsed.pack(side="right", anchor="e", padx=(12, 0))

        input_frame = ttk.Frame(self.chat_tab)
        input_frame.pack(fill="x", pady=(12, 0))
        self.input_box = tk.Text(
            input_frame,
            height=4,
            wrap="word",
            font=("Menlo", 13),
            background="#171717",
            foreground="#f2f2f2",
            insertbackground="#ffffff",
            padx=10,
            pady=8,
        )
        self.input_box.pack(side="left", fill="both", expand=True)
        self.send_button = ttk.Button(input_frame, text="Send", command=self._send_or_stop, width=16)
        self.send_button.pack(side="left", padx=(12, 0), fill="y")
        self.send_button.bind("<Enter>", self._on_send_button_enter)
        self.send_button.bind("<Leave>", self._on_send_button_leave)
        self.input_box.bind("<Command-Return>", self._send_event)
        self.input_box.bind("<Control-Return>", self._send_event)
        self.input_box.focus_set()

    def _build_holdings_tab(self) -> None:
        toolbar = ttk.Frame(self.holdings_tab)
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Button(toolbar, text="Refresh", command=self.refresh_holdings).pack(side="left")
        ttk.Button(toolbar, text="Record purchase", command=self.record_purchase).pack(side="left", padx=8)

        columns = ("ticker", "quantity", "average_cost", "total_cost")
        self.holdings_tree = ttk.Treeview(self.holdings_tab, columns=columns, show="headings")
        headings = {
            "ticker": "Ticker",
            "quantity": "Shares",
            "average_cost": "Average cost",
            "total_cost": "Total cost",
        }
        widths = {"ticker": 130, "quantity": 150, "average_cost": 180, "total_cost": 180}
        for column in columns:
            self.holdings_tree.heading(column, text=headings[column])
            self.holdings_tree.column(column, width=widths[column], anchor="center")
        scrollbar = ttk.Scrollbar(self.holdings_tab, orient="vertical", command=self.holdings_tree.yview)
        self.holdings_tree.configure(yscrollcommand=scrollbar.set)
        self.holdings_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.refresh_holdings()

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
            try:
                self.supabase_auth = SupabaseAuth(
                    settings.supabase_url, settings.supabase_publishable_key
                )
                self.account_status.set(
                    "Connection settings loaded. Create an account or sign in."
                )
            except Exception as exc:
                self.account_status.set(friendly_auth_error(exc))

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
            self.supabase_auth = SupabaseAuth(url, key)
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
        if self.supabase_auth is None:
            self.account_status.set("Save your Supabase connection settings first.")
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
        assert self.supabase_auth is not None
        try:
            if action == "sign_up":
                result = self.supabase_auth.sign_up(email, password)
            elif action == "sign_in":
                result = self.supabase_auth.sign_in(email, password)
            else:
                result = self.supabase_auth.sign_out()
            self.results.put(("auth", result))
        except Exception as exc:
            self.results.put(("auth_error", friendly_auth_error(exc)))

    def _send_event(self, _event: tk.Event) -> str:
        self._send_or_stop()
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
                    continue
                if kind == "auth_error":
                    self.account_status.set(str(payload))
                    self.auth_password.set("")
                    self._set_auth_busy(False)
                    continue
                response = str(payload)
                self._finish_progress(response)
                self._append("Agent", response)
                self._set_busy(False)
                self.refresh_holdings()
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
            self.current_stage = "Starting"
            self.current_detail = "Preparing the research worker."
            self._update_progress(0, self.current_stage, self.current_detail)
        else:
            self.research_started_at = None
            self.current_step_started_at = None
            self.current_task_cancellable = False
            self.stop_requested = False
            self.cancel_event = None
            self._send_button_hovered = False
            self.send_button.configure(state="normal", text="Send")
            self.input_box.focus_set()

    def _refresh_send_button_text(self) -> None:
        if not self.is_busy:
            self.send_button.configure(text="Send")
            return
        if self.stop_requested:
            self.send_button.configure(text="Stopping...", state="disabled")
        elif self.current_task_cancellable and self._send_button_hovered:
            self.send_button.configure(text="Stop research", state="normal")
        elif self.current_task_cancellable:
            self.send_button.configure(text=f"Researching... {self.current_progress}%", state="normal")
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
        self.progress_status.configure(text=f"Research status: {self.current_stage}")
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
        block = (
            "Agent:\n"
            f"Research progress: {self.current_progress}%\n"
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
        else:
            self.current_progress = 100
            self.current_stage = "Research complete"
            self.progress_bar.configure(value=100)
            self.progress_percent.configure(text="100%")
            self.progress_status.configure(text="Research status: complete")
            self.progress_detail.configure(text="The concise findings are shown below.")
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
        self.refresh_holdings()

    def _submit_prompt(self, prompt: str) -> None:
        self.input_box.delete("1.0", "end")
        self.input_box.insert("1.0", prompt)
        self.send_message()

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

    def refresh_holdings(self) -> None:
        for item in self.holdings_tree.get_children():
            self.holdings_tree.delete(item)
        try:
            holdings = self.controller.holdings()
        except Exception:
            return
        for holding in holdings:
            self.holdings_tree.insert(
                "",
                "end",
                values=(
                    holding.ticker,
                    f"{holding.quantity:g}",
                    f"${holding.average_cost:,.2f}",
                    f"${holding.total_cost:,.2f}",
                ),
            )


def main() -> None:
    app = StockAgentApp()
    app.mainloop()


if __name__ == "__main__":
    main()
