"""Content-addressed cache for validated semantic research classifications."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3


CLASSIFIER_CONTRACT_VERSION = 1
VALID_RELEVANCE = {"direct", "meaningful", "adjacent", "unsupported"}


class ResearchClassificationCache:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_profile_classifications (
                    cache_key TEXT PRIMARY KEY,
                    relevance TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    @staticmethod
    def key(theme: str, candidate: dict[str, str]) -> str:
        evidence = {
            "version": CLASSIFIER_CONTRACT_VERSION,
            "theme": " ".join(theme.casefold().split()),
            "sector": candidate.get("sector", "").strip(),
            "industry": candidate.get("industry", "").strip(),
            "business_summary": candidate.get("business_summary", "").strip(),
        }
        encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def get(self, theme: str, candidate: dict[str, str]) -> tuple[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT relevance, reason
                FROM research_profile_classifications
                WHERE cache_key = ?
                """,
                (self.key(theme, candidate),),
            ).fetchone()
        if row is None or row[0] not in VALID_RELEVANCE or not str(row[1]).strip():
            return None
        return str(row[0]), str(row[1])

    def put(
        self,
        theme: str,
        candidate: dict[str, str],
        relevance: str,
        reason: str,
    ) -> None:
        if relevance not in VALID_RELEVANCE or not reason.strip():
            raise ValueError("Only complete validated classifications can be cached.")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_profile_classifications(cache_key, relevance, reason)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    relevance = excluded.relevance,
                    reason = excluded.reason,
                    created_at = CURRENT_TIMESTAMP
                """,
                (self.key(theme, candidate), relevance, reason.strip()),
            )
