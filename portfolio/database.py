"""SQLite storage for purchases and aggregated holdings."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
from typing import Iterable

from .models import Holding, Purchase


class PortfolioDatabase:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    quantity REAL NOT NULL CHECK(quantity > 0),
                    price REAL NOT NULL CHECK(price >= 0),
                    purchased_at TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_purchases_ticker ON purchases(ticker)"
            )

    def record_purchase(self, purchase: Purchase) -> int:
        ticker = purchase.ticker.strip().upper()
        if not ticker:
            raise ValueError("Ticker cannot be empty.")
        if purchase.quantity <= 0:
            raise ValueError("Quantity must be greater than zero.")
        if purchase.price < 0:
            raise ValueError("Price cannot be negative.")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO purchases(ticker, quantity, price, purchased_at, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    float(purchase.quantity),
                    float(purchase.price),
                    purchase.purchased_at.isoformat(),
                    purchase.note.strip(),
                ),
            )
            return int(cursor.lastrowid)

    def record_purchases(self, purchases: Iterable[Purchase]) -> int:
        """Validate and append an import atomically, returning inserted rows."""
        purchases = list(purchases)
        for purchase in purchases:
            ticker = purchase.ticker.strip().upper()
            if not ticker:
                raise ValueError("Ticker cannot be empty.")
            if purchase.quantity <= 0:
                raise ValueError("Quantity must be greater than zero.")
            if purchase.price < 0:
                raise ValueError("Price cannot be negative.")
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO purchases(ticker, quantity, price, purchased_at, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        purchase.ticker.strip().upper(),
                        float(purchase.quantity),
                        float(purchase.price),
                        purchase.purchased_at.isoformat(),
                        purchase.note.strip(),
                    )
                    for purchase in purchases
                ],
            )
        return len(purchases)

    def list_purchases(self) -> list[Purchase]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT ticker, quantity, price, purchased_at, note
                FROM purchases
                ORDER BY purchased_at, id
                """
            ).fetchall()
        return [
            Purchase(
                ticker=row["ticker"],
                quantity=float(row["quantity"]),
                price=float(row["price"]),
                purchased_at=date.fromisoformat(row["purchased_at"]),
                note=row["note"],
            )
            for row in rows
        ]

    def holdings(self) -> list[Holding]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    ticker,
                    SUM(quantity) AS quantity,
                    SUM(quantity * price) AS total_cost
                FROM purchases
                GROUP BY ticker
                HAVING SUM(quantity) > 0
                ORDER BY ticker
                """
            ).fetchall()

        holdings: list[Holding] = []
        for row in rows:
            quantity = float(row["quantity"])
            total_cost = float(row["total_cost"])
            holdings.append(
                Holding(
                    ticker=row["ticker"],
                    quantity=quantity,
                    total_cost=total_cost,
                    average_cost=total_cost / quantity,
                )
            )
        return holdings

    def replace_with_snapshot(self, purchases: Iterable[Purchase]) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM purchases")
            connection.executemany(
                """
                INSERT INTO purchases(ticker, quantity, price, purchased_at, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        purchase.ticker.strip().upper(),
                        float(purchase.quantity),
                        float(purchase.price),
                        purchase.purchased_at.isoformat(),
                        purchase.note.strip(),
                    )
                    for purchase in purchases
                ],
            )
