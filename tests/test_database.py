from __future__ import annotations

from datetime import date

from portfolio.database import PortfolioDatabase
from portfolio.models import Purchase


def test_records_and_aggregates_purchases(tmp_path) -> None:
    database = PortfolioDatabase(tmp_path / "portfolio.db")
    database.record_purchase(Purchase("AAPL", 2, 100, date(2026, 1, 1)))
    database.record_purchase(Purchase("AAPL", 1, 130, date(2026, 2, 1)))

    holdings = database.holdings()
    assert len(holdings) == 1
    assert holdings[0].ticker == "AAPL"
    assert holdings[0].quantity == 3
    assert holdings[0].total_cost == 330
    assert holdings[0].average_cost == 110


def test_deletes_one_ticker_or_clears_everything(tmp_path) -> None:
    database = PortfolioDatabase(tmp_path / "portfolio.db")
    database.record_purchase(Purchase("AAPL", 2, 100, date(2026, 1, 1)))
    database.record_purchase(Purchase("MSFT", 1, 200, date(2026, 1, 1)))

    assert database.delete_ticker("aapl") == 1
    assert [holding.ticker for holding in database.holdings()] == ["MSFT"]
    assert database.clear() == 1
    assert database.holdings() == []
