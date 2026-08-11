from __future__ import annotations

from datetime import date

from portfolio.config import Settings
from portfolio.controller import StockAgentController


def test_controller_records_explicit_ticker(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "portfolio.db",
        groq_api_key=None,
        groq_model="test-model",
        lseg_session_name="desktop.workspace",
    )
    controller = StockAgentController(settings=settings)
    purchase = controller.record_purchase("AAPL", 1, 100, date(2026, 1, 1))
    assert purchase.ticker == "AAPL"
    assert controller.holdings()[0].quantity == 1


def test_controller_holding_snapshots_calculate_gain(tmp_path, monkeypatch) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "portfolio.db",
        groq_api_key=None,
        groq_model="test-model",
        lseg_session_name="desktop.workspace",
    )
    controller = StockAgentController(settings=settings)
    controller.record_purchase("AAPL", 2, 100, date(2026, 1, 1))
    monkeypatch.setattr("portfolio.controller.current_price", lambda _ticker: 125.0)

    snapshots = controller.holding_snapshots()

    assert snapshots[0].current_price == 125.0
    assert snapshots[0].gain_loss == 50.0
