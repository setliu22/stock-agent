from types import SimpleNamespace
from datetime import date

from gui import StockAgentApp, period_performance, tab_drag_target
from portfolio.models import PortfolioHistoryPoint


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


def test_period_performance_uses_selected_session_window() -> None:
    history = [
        PortfolioHistoryPoint(date(2026, 8, day), value)
        for day, value in ((10, 90), (11, 100), (12, 102), (13, 105), (14, 110))
    ]

    assert period_performance(history, 3) == (10, 10.0)


def test_clicking_same_holding_twice_returns_to_portfolio_chart() -> None:
    selected: list[str] = []
    removed: list[str] = []
    draws: list[str | None] = []
    tree = SimpleNamespace(
        identify_row=lambda _y: "AAPL",
        identify_column=lambda _x: "#1",
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
    event = SimpleNamespace(x=10, y=10)

    StockAgentApp._toggle_holding_chart(app, event)
    StockAgentApp._toggle_holding_chart(app, event)

    assert selected == ["AAPL"]
    assert removed == ["AAPL"]
    assert draws == ["AAPL", None]


def test_delete_position_requires_confirmation(monkeypatch) -> None:
    deleted: list[str] = []
    refreshed: list[bool] = []
    app = SimpleNamespace(
        controller=SimpleNamespace(delete_position=deleted.append),
        _invalidate_portfolio_refresh=lambda: None,
        refresh_holdings=lambda **kwargs: refreshed.append(kwargs["refresh_prices"]),
    )

    monkeypatch.setattr("gui.messagebox.askyesno", lambda *args, **kwargs: False)
    StockAgentApp.delete_position(app, "AAPL")
    assert deleted == []

    monkeypatch.setattr("gui.messagebox.askyesno", lambda *args, **kwargs: True)
    StockAgentApp.delete_position(app, "AAPL")
    assert deleted == ["AAPL"]
    assert refreshed == [False]
