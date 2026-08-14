"""Facade used by the GUI."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from .agent import StockAgent
from .company_resolver import company_name_to_ticker, extract_security_reference, is_explicit_ticker
from .config import Settings, get_settings
from .cloud_portfolios import CloudPurchase, SupabasePortfolioClient
from .database import PortfolioDatabase
from .event_risk import run_portfolio_event_risk_review
from .market_data import current_price, recent_closes
from .models import Holding, HoldingSnapshot, PortfolioHistoryPoint, Purchase


class StockAgentController:
    def __init__(self, settings: Settings | None = None, database_path: Path | None = None) -> None:
        self.settings = settings or get_settings(database_path)
        self.database = PortfolioDatabase(self.settings.database_path)
        self.agent = StockAgent(self.settings, self.database)
        self.cloud_client: SupabasePortfolioClient | None = None

    def handle_message(
        self,
        message: str,
        progress_callback: Callable[[int | None, str, str], None] | None = None,
        cancel_event: object | None = None,
    ) -> str:
        response = self.agent.handle(
            message, progress_callback=progress_callback, cancel_event=cancel_event
        )
        self.sync_local_portfolio()
        return response

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
        self.sync_local_portfolio()
        return purchase

    def holdings(self) -> list[Holding]:
        return self.database.holdings()

    def holding_snapshots(self) -> list[HoldingSnapshot]:
        snapshots: list[HoldingSnapshot] = []
        for holding in self.holdings():
            try:
                price = current_price(holding.ticker)
            except Exception:
                price = None
            gain_loss = (
                (price - holding.average_cost) * holding.quantity
                if price is not None
                else None
            )
            market_value = price * holding.quantity if price is not None else None
            return_percent = (
                gain_loss / holding.total_cost * 100
                if gain_loss is not None and holding.total_cost
                else None
            )
            snapshots.append(
                HoldingSnapshot(
                    ticker=holding.ticker,
                    quantity=holding.quantity,
                    average_cost=holding.average_cost,
                    total_cost=holding.total_cost,
                    current_price=price,
                    market_value=market_value,
                    gain_loss=gain_loss,
                    return_percent=return_percent,
                )
            )
        return snapshots

    def portfolio_history(self) -> list[PortfolioHistoryPoint]:
        """Value current holdings across recent common market sessions."""
        holdings = self.holdings()
        if not holdings:
            return []
        closes_by_ticker: dict[str, dict[date, float]] = {}
        for holding in holdings:
            try:
                closes = recent_closes(holding.ticker)
            except Exception:
                return []
            if not closes:
                return []
            closes_by_ticker[holding.ticker] = dict(closes)

        common_dates = set.intersection(
            *(set(closes) for closes in closes_by_ticker.values())
        )
        return [
            PortfolioHistoryPoint(
                as_of=as_of,
                market_value=sum(
                    closes_by_ticker[holding.ticker][as_of] * holding.quantity
                    for holding in holdings
                ),
            )
            for as_of in sorted(common_dates)
        ]

    def holdings_text(self) -> str:
        return self.agent.show_holdings()

    def return_text(self) -> str:
        return self.agent.calculate_return()

    def review_event_risk(self, progress_callback=None, cancel_event=None) -> str:
        review = run_portfolio_event_risk_review(
            self.settings,
            self.holdings(),
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        return review.to_text()

    def account_sign_in(self, email: str, password: str) -> str:
        """Authenticate and hydrate the disposable local portfolio cache."""
        client = SupabasePortfolioClient.from_project(self.settings.project_root)
        session = client.sign_in(email, password)
        portfolio = self._account_portfolio(client)
        remote = client.list_purchases(portfolio.id)
        local = self.database.list_purchases()
        if remote:
            self.database.replace_with_snapshot(_local_purchases(remote))
        elif local:
            for purchase in local:
                client.create_purchase(
                    portfolio_id=portfolio.id,
                    security_name=purchase.ticker,
                    ticker=purchase.ticker,
                    quantity=purchase.quantity,
                    purchase_price=purchase.price,
                    purchased_at=purchase.purchased_at.isoformat(),
                    note=purchase.note,
                )
        self.cloud_client = client
        return session.email

    def account_sign_out(self) -> None:
        """Clear the local cache only after the cloud session is available."""
        if self.cloud_client is not None:
            self.cloud_client.sign_out()
        self.cloud_client = None
        self.database.replace_with_snapshot([])

    def sync_local_portfolio(self) -> int:
        """Upload local purchases not yet present in the signed-in account."""
        if self.cloud_client is None or not self.cloud_client.signed_in:
            return 0
        portfolio = self._account_portfolio(self.cloud_client)
        remote = self.cloud_client.list_purchases(portfolio.id)
        local = self.database.list_purchases()
        remote_by_ticker: dict[str, list[CloudPurchase]] = {}
        local_by_ticker: dict[str, list[Purchase]] = {}
        for item in remote:
            remote_by_ticker.setdefault((item.ticker or "").upper(), []).append(item)
        for item in local:
            local_by_ticker.setdefault(item.ticker.upper(), []).append(item)
        created = 0
        for ticker, purchases in local_by_ticker.items():
            remote_rows = remote_by_ticker.get(ticker, [])
            for index, purchase in enumerate(purchases):
                if index < len(remote_rows):
                    remote_row = remote_rows[index]
                    if _purchase_fingerprint(remote_row) != _local_purchase_fingerprint(purchase):
                        self.cloud_client.update_purchase(
                            remote_row.id,
                            portfolio_id=portfolio.id,
                            security_name=purchase.ticker,
                            ticker=purchase.ticker,
                            quantity=purchase.quantity,
                            purchase_price=purchase.price,
                            purchased_at=purchase.purchased_at.isoformat(),
                            note=purchase.note,
                        )
                else:
                    self.cloud_client.create_purchase(
                        portfolio_id=portfolio.id,
                        security_name=purchase.ticker,
                        ticker=purchase.ticker,
                        quantity=purchase.quantity,
                        purchase_price=purchase.price,
                        purchased_at=purchase.purchased_at.isoformat(),
                        note=purchase.note,
                    )
                    created += 1
        return created

    @staticmethod
    def _account_portfolio(client: SupabasePortfolioClient):
        portfolios = client.list_portfolios()
        if portfolios:
            return portfolios[0]
        return client.create_portfolio("Main")


def _local_purchases(rows: list[CloudPurchase]) -> list[Purchase]:
    purchases: list[Purchase] = []
    for row in rows:
        if row.ticker is None or row.quantity is None or row.purchase_price is None:
            continue
        try:
            purchased_at = date.fromisoformat(row.purchased_at or date.today().isoformat())
        except ValueError:
            purchased_at = date.today()
        purchases.append(
            Purchase(
                ticker=row.ticker,
                quantity=row.quantity,
                price=row.purchase_price,
                purchased_at=purchased_at,
                note=row.note,
            )
        )
    return purchases


def _local_purchase_fingerprint(purchase: Purchase) -> tuple[object, ...]:
    return (
        purchase.ticker.upper(),
        float(purchase.quantity),
        float(purchase.price),
        purchase.purchased_at.isoformat(),
        purchase.note.strip(),
    )


def _purchase_fingerprint(purchase: CloudPurchase) -> tuple[object, ...]:
    return (
        (purchase.ticker or "").upper(),
        float(purchase.quantity or 0),
        float(purchase.purchase_price or 0),
        purchase.purchased_at or "",
        purchase.note.strip(),
    )
