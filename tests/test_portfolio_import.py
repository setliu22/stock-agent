from datetime import date

import pytest

from portfolio.database import PortfolioDatabase
from portfolio.agent import StockAgent
from portfolio.config import Settings
from portfolio.portfolio_import import PortfolioImportError, parse_portfolio_json_message


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


def test_agent_import_route_does_not_enter_research(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))
    monkeypatch.setattr(agent, "research", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("JSON must not be researched")))

    response = agent.handle('{"positions": [{"ticker": "AAPL", "shares": 2, "averageCost": 150}]}')

    assert "Imported 1 portfolio position" in response
    assert agent.database.holdings()[0].ticker == "AAPL"


def test_agent_remembers_pending_portfolio_import(tmp_path):
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    agent = StockAgent(settings, PortfolioDatabase(settings.database_path))

    prompt = agent.handle("I want to add my portfolio")
    response = agent.handle('{"positions": [{"ticker": "AAPL", "shares": 2, "averageCost": 150}]}')

    assert "Paste the portfolio JSON" in prompt
    assert "Imported 1 portfolio position" in response
    assert agent.database.holdings()[0].ticker == "AAPL"
