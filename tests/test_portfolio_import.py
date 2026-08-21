from datetime import date

import pytest

from portfolio.database import PortfolioDatabase
from portfolio.config import Settings
from portfolio.portfolio_import import PortfolioImportError, parse_portfolio_json_message, parse_portfolio_update_json_message


def test_imports_portfolio_json_without_research_fields(tmp_path):
    imported = parse_portfolio_json_message(
        'Add these positions: {"holdings": [{"symbol": "AAPL", "shares": 2, "averageCost": 150}, '
        '{"ticker": "MSFT", "quantity": 3, "purchase_price": 200, "date": "2026-01-05"}]}'
    )

    assert imported is not None
    assert [purchase.ticker for purchase in imported.purchases] == ["AAPL", "MSFT"]
    assert imported.purchases[0].purchased_at == date.today()
    assert imported.purchases[1].purchased_at == date(2026, 1, 5)


def test_imports_brokerage_export_with_stocks_container():
    imported = parse_portfolio_json_message(
        '{"account": {"institution": "Chase"}, "summary": {"winner_count": 1}, '
        '"stocks": [{"ticker": "ZBRA", "quantity": 0.12, '
        '"average_cost_per_share": 271.5833, "purchase_date": null, '
        '"current_price": 378.08}]}'
    )

    assert imported is not None
    assert imported.source_position_count == 1
    assert imported.purchases[0].ticker == "ZBRA"
    assert imported.purchases[0].quantity == 0.12
    assert imported.purchases[0].price == 271.5833


def test_import_requires_cost_basis(tmp_path):
    with pytest.raises(PortfolioImportError, match="purchase price/average cost"):
        parse_portfolio_json_message('{"positions": [{"ticker": "AAPL", "shares": 2}]}')


def test_import_is_atomic(tmp_path):
    database = PortfolioDatabase(tmp_path / "portfolio.db")
    imported = parse_portfolio_json_message(
        '[{"ticker": "AAPL", "shares": 2, "average_cost": 150}, '
        '{"ticker": "MSFT", "shares": 3, "average_cost": 200}]'
    )
    assert imported is not None
    assert database.record_purchases(imported.purchases) == 2
    assert [(item.ticker, item.quantity) for item in database.holdings()] == [("AAPL", 2), ("MSFT", 3)]


def test_update_parser_preserves_omitted_fields():
    updates = parse_portfolio_update_json_message(
        '{"stocks": [{"ticker": "AAPL", "purchase_date": "2026-08-01"}, '
        '{"ticker": "NEW", "quantity": 2, "average_cost": 40}]}'
    )

    assert updates is not None
    assert updates[0].fields == frozenset({"purchased_at"})
    assert updates[1].fields == frozenset({"quantity", "price"})


def test_update_mode_can_apply_a_field_to_all_holdings(tmp_path):
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    database = PortfolioDatabase(settings.database_path)
    database.record_purchases([
        __import__("portfolio.models", fromlist=["Purchase"]).Purchase("AAPL", 1, 100, date(2026, 1, 1)),
        __import__("portfolio.models", fromlist=["Purchase"]).Purchase("MSFT", 1, 200, date(2026, 2, 1)),
    ])
    updates = parse_portfolio_update_json_message('{"ticker": "*", "purchase_date": "2026-08-12"}')

    assert updates is not None
    assert database.apply_portfolio_updates(updates) == (2, 0)
    assert {item.purchased_at for item in database.list_purchases()} == {date(2026, 8, 12)}



def test_key_normalization_handles_close_typos_but_not_current_price():
    updates = parse_portfolio_update_json_message(
        '{"stocks": [{"tiker": "AAPL", "purchse_dat": "2026-08-12", "current_price": 250}]}'
    )

    assert updates is not None
    assert updates[0].ticker == "AAPL"
    assert updates[0].purchased_at == date(2026, 8, 12)
    assert updates[0].fields == frozenset({"purchased_at"})
