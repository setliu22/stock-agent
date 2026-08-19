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
