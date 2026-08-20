from types import SimpleNamespace

from gui import StockAgentApp, tab_drag_target


def test_tab_drag_moves_between_adjacent_pages() -> None:
    assert tab_drag_target(1, 4, 80) == 2
    assert tab_drag_target(1, 4, -80) == 0


def test_tab_drag_ignores_small_movements_and_page_boundaries() -> None:
    assert tab_drag_target(1, 4, 20) is None
    assert tab_drag_target(0, 4, -80) is None
    assert tab_drag_target(3, 4, 80) is None


def test_selecting_portfolio_refreshes_prices() -> None:
    calls: list[str] = []
    app = SimpleNamespace(
        notebook=SimpleNamespace(select=lambda: "portfolio"),
        holdings_tab="portfolio",
        market_tab="market",
        refresh_holdings=lambda: calls.append("portfolio"),
        refresh_market_regime=lambda: calls.append("market"),
    )

    StockAgentApp._on_tab_changed(app, None)

    assert calls == ["portfolio"]


def test_selecting_market_refreshes_macro_data() -> None:
    calls: list[str] = []
    app = SimpleNamespace(
        notebook=SimpleNamespace(select=lambda: "market"),
        holdings_tab="portfolio",
        market_tab="market",
        refresh_holdings=lambda: calls.append("portfolio"),
        refresh_market_regime=lambda: calls.append("market"),
    )

    StockAgentApp._on_tab_changed(app, None)

    assert calls == ["market"]


def test_position_risk_button_opens_chat_with_unsent_draft() -> None:
    selected: list[str] = []
    drafts: list[str] = []
    app = SimpleNamespace(
        is_busy=False,
        chat_tab="chat",
        notebook=SimpleNamespace(select=selected.append),
        pending_chat_draft=None,
        _apply_chat_draft=drafts.append,
    )

    StockAgentApp.prepare_position_risk_prompt(app)

    assert selected == ["chat"]
    assert drafts == [
        "Review my portfolio positions for reasons to hold, review, trim, or consider exiting."
    ]
    assert app.pending_chat_draft is None


def test_position_risk_button_queues_draft_while_chat_is_busy() -> None:
    selected: list[str] = []
    app = SimpleNamespace(
        is_busy=True,
        chat_tab="chat",
        notebook=SimpleNamespace(select=selected.append),
        pending_chat_draft=None,
    )

    StockAgentApp.prepare_position_risk_prompt(app)

    assert selected == ["chat"]
    assert app.pending_chat_draft == (
        "Review my portfolio positions for reasons to hold, review, trim, or consider exiting."
    )


def test_market_research_button_opens_editable_request_without_policy_text() -> None:
    selected: list[str] = []
    drafts: list[str] = []
    app = SimpleNamespace(
        is_busy=False,
        chat_tab="chat",
        notebook=SimpleNamespace(select=selected.append),
        pending_chat_draft=None,
        _apply_chat_draft=drafts.append,
    )

    StockAgentApp.prepare_market_research_prompt(app)

    assert selected == ["chat"]
    assert drafts == ["Research promising technology stocks."]


class _InputBox:
    def __init__(self) -> None:
        self.selection = ("1.0", "3.4")
        self.insert = "3.4"

    def tag_ranges(self, _tag):
        return self.selection

    def tag_remove(self, _tag, _start, _end):
        self.selection = ()

    def tag_add(self, _tag, start, end):
        self.selection = (start, end)

    def mark_set(self, _mark, value):
        self.insert = value

    def see(self, _index):
        pass


def test_arrow_collapses_full_prompt_selection_to_requested_edge() -> None:
    box = _InputBox()
    app = SimpleNamespace(input_box=box)

    assert StockAgentApp._collapse_input_selection(app, None, "start") == "break"
    assert box.insert == "1.0"
    box.selection = ("1.0", "3.4")
    assert StockAgentApp._collapse_input_selection(app, None, "end") == "break"
    assert box.insert == "3.4"


def test_command_a_selects_the_entire_prompt() -> None:
    box = _InputBox()
    app = SimpleNamespace(input_box=box)

    assert StockAgentApp._select_all_input(app, None) == "break"
    assert box.selection == ("1.0", "end-1c")
    assert box.insert == "end-1c"


def test_clicking_same_holding_twice_returns_to_portfolio_chart() -> None:
    selected: list[str] = []
    removed: list[str] = []
    draws: list[str | None] = []
    tree = SimpleNamespace(
        identify_row=lambda _y: "AAPL",
        item=lambda _row, _option: ("AAPL",),
        selection_set=selected.append,
        selection_remove=removed.append,
        focus=lambda _row: None,
    )
    app = SimpleNamespace(
        holdings_tree=tree,
        selected_performance_ticker=None,
        _select_performance_history=lambda: draws.append(app.selected_performance_ticker),
    )
    event = SimpleNamespace(y=10)

    StockAgentApp._toggle_holding_chart(app, event)
    StockAgentApp._toggle_holding_chart(app, event)

    assert selected == ["AAPL"]
    assert removed == ["AAPL"]
    assert draws == ["AAPL", None]
