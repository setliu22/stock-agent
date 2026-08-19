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
