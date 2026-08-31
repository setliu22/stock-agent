from types import SimpleNamespace
from datetime import date, datetime

from gui import (
    RESEARCH_EXAMPLE_QUESTIONS,
    ResearchApprovalDialog,
    StockAgentApp,
    _performance_time_label,
    friendly_research_error,
    period_performance,
    sort_portfolio_rows,
    tab_drag_target,
)
from portfolio.company_resolver import InstrumentResolutionError
from portfolio.lseg_research import LSEGWorkspaceUnavailable
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


def test_portfolio_rows_sort_numbers_by_raw_value_and_keep_missing_last() -> None:
    rows = [
        {"ticker": "HIGH", "market_value": 100.0},
        {"ticker": "MISSING", "market_value": None},
        {"ticker": "LOW", "market_value": 20.0},
    ]

    ascending = sort_portfolio_rows(rows, "market_value", descending=False)
    descending = sort_portfolio_rows(rows, "market_value", descending=True)

    assert [row["ticker"] for row in ascending] == ["LOW", "HIGH", "MISSING"]
    assert [row["ticker"] for row in descending] == ["HIGH", "LOW", "MISSING"]


def test_portfolio_rows_sort_tickers_alphabetically_without_case_bias() -> None:
    rows = [
        {"ticker": "zbra"},
        {"ticker": "AAPL"},
        {"ticker": "msft"},
    ]

    result = sort_portfolio_rows(rows, "ticker", descending=False)

    assert [row["ticker"] for row in result] == ["AAPL", "msft", "zbra"]


def test_portfolio_period_labels_include_one_day_and_all_time() -> None:
    assert StockAgentApp._period_label(SimpleNamespace(performance_sessions=1)) == "1 day"
    assert StockAgentApp._period_label(SimpleNamespace(performance_sessions=0)) == "all time"


def test_performance_time_labels_distinguish_daily_and_intraday_points() -> None:
    assert _performance_time_label(date(2026, 8, 31)) == "Aug 31, 2026"
    assert _performance_time_label(
        datetime(2026, 8, 31, 13, 35),
        end=True,
    ) == "1:35 PM"


def test_clicking_a_portfolio_heading_toggles_sort_direction() -> None:
    renders: list[bool] = []
    app = SimpleNamespace(
        holdings_sort_column=None,
        holdings_sort_descending=False,
        _render_holding_rows=lambda: renders.append(True),
    )

    StockAgentApp._sort_holdings(app, "market_value")
    assert (app.holdings_sort_column, app.holdings_sort_descending) == (
        "market_value",
        False,
    )

    StockAgentApp._sort_holdings(app, "market_value")
    assert (app.holdings_sort_column, app.holdings_sort_descending) == (
        "market_value",
        True,
    )
    assert len(renders) == 2


def test_research_approval_keeps_only_one_rate_measure_selected() -> None:
    class Variable:
        def __init__(self, value: bool) -> None:
            self.value = value

        def get(self) -> bool:
            return self.value

        def set(self, value: bool) -> None:
            self.value = value

    app = SimpleNamespace(
        capability_vars={
            "fed_funds_history": Variable(True),
            "treasury_yield_history": Variable(True),
        }
    )

    ResearchApprovalDialog._enforce_exclusive_capability(app, "fed_funds_history")

    assert app.capability_vars["fed_funds_history"].get()
    assert not app.capability_vars["treasury_yield_history"].get()


def test_research_examples_only_fill_the_input(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class Input:
        def delete(self, start: str, end: str) -> None:
            calls.append(("delete", f"{start}:{end}"))

        def insert(self, start: str, value: str) -> None:
            calls.append(("insert", f"{start}:{value}"))

        def focus_set(self) -> None:
            calls.append(("focus", ""))

    class Dialog:
        def __init__(self, _parent) -> None:
            self.result = RESEARCH_EXAMPLE_QUESTIONS[0]

    monkeypatch.setattr("gui.ResearchExamplesDialog", Dialog)
    app = SimpleNamespace(
        research_question=Input(),
        wait_window=lambda _dialog: None,
    )

    StockAgentApp.show_research_examples(app)

    assert calls == [
        ("delete", "1.0:end"),
        ("insert", f"1.0:{RESEARCH_EXAMPLE_QUESTIONS[0]}"),
        ("focus", ""),
    ]


def test_expected_research_errors_do_not_leak_internal_type_names() -> None:
    resolution = friendly_research_error(
        InstrumentResolutionError("No listed security matched 'Unknown'.")
    )
    workspace = friendly_research_error(
        LSEGWorkspaceUnavailable("Open LSEG Workspace and retry.")
    )

    assert resolution == "No listed security matched 'Unknown'."
    assert workspace == "Open LSEG Workspace and retry."


def test_unexpected_research_errors_keep_diagnostic_type() -> None:
    assert friendly_research_error(RuntimeError("broken")) == (
        "Unexpected error: RuntimeError: broken"
    )


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
