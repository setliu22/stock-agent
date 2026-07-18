"""Facade used by the GUI."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from .agent import StockAgent
from .company_resolver import company_name_to_ticker, extract_security_reference, is_explicit_ticker
from .config import Settings, get_settings
from .database import PortfolioDatabase
from .models import Holding, Purchase


class StockAgentController:
    def __init__(self, settings: Settings | None = None, database_path: Path | None = None) -> None:
        self.settings = settings or get_settings(database_path)
        self.database = PortfolioDatabase(self.settings.database_path)
        self.agent = StockAgent(self.settings, self.database)

    def handle_message(
        self,
        message: str,
        progress_callback: Callable[[int | None, str, str], None] | None = None,
        cancel_event: object | None = None,
    ) -> str:
        return self.agent.handle(
            message, progress_callback=progress_callback, cancel_event=cancel_event
        )

    def record_purchase(
        self,
        security: str,
        quantity: float,
        price: float,
        purchased_at: date,
        note: str = "",
    ) -> Purchase:
        reference = extract_security_reference(security)
        if is_explicit_ticker(reference):
            ticker = reference.upper()
        else:
            ticker, _company, _exchange = company_name_to_ticker(reference)
        purchase = Purchase(
            ticker=ticker,
            quantity=float(quantity),
            price=float(price),
            purchased_at=purchased_at,
            note=note,
        )
        self.database.record_purchase(purchase)
        return purchase

    def holdings(self) -> list[Holding]:
        return self.database.holdings()

    def holdings_text(self) -> str:
        return self.agent.show_holdings()

    def return_text(self) -> str:
        return self.agent.calculate_return()
