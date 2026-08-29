"""Supabase-backed portfolio persistence and email/password authentication.

This module deliberately uses the standard library HTTP client so the cloud
portfolio feature does not add another large SDK dependency. All database
access is made with the signed-in user's JWT and is constrained by the RLS
policies in ``supabase/schema.sql``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import ssl
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from .certificates import configure_ssl_certificates

configure_ssl_certificates()


class CloudPortfolioError(RuntimeError):
    """Base exception for cloud portfolio failures."""


class CloudConfigurationError(CloudPortfolioError):
    """Raised when Supabase environment settings are missing."""


class CloudAuthenticationError(CloudPortfolioError):
    """Raised when sign-in, sign-up, or session refresh fails."""


class CloudRequestError(CloudPortfolioError):
    """Raised when the Supabase REST API returns an error."""


@dataclass(frozen=True, slots=True)
class AuthResult:
    message: str
    signed_in: bool
    email: str | None = None


def is_certificate_error(error: BaseException) -> bool:
    """Recognize TLS verification errors even when a client wrapped them."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        text = str(current).casefold()
        if "certificate_verify_failed" in text or "certificate verify failed" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def friendly_cloud_error(error: BaseException) -> str:
    """Turn common first-time Supabase setup failures into actionable messages."""
    message = str(error).strip() or type(error).__name__
    lowered = message.casefold()
    if "public.portfolios" in lowered and "schema cache" in lowered:
        return (
            "Supabase login succeeded, but the cloud portfolio tables are not set up. "
            "Open the Supabase SQL Editor, run the complete contents of "
            "supabase/schema.sql from this project, then sign in again."
        )
    if "public.purchases" in lowered and "schema cache" in lowered:
        return (
            "Supabase login succeeded, but the cloud purchase table is not set up. "
            "Run the complete contents of supabase/schema.sql in the Supabase SQL Editor, "
            "then sign in again."
        )
    return message


def friendly_auth_error(error: BaseException) -> str:
    if is_certificate_error(error):
        return (
            "Python could not verify Supabase's security certificate. "
            "Run \u201cUpdate Stock Agent.command\u201d again so the trusted certificate "
            "bundle is installed, then retry. Do not disable SSL verification."
        )
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        reason = current.reason if isinstance(current, URLError) else current
        if isinstance(reason, socket.gaierror):
            return (
                "Could not resolve the Supabase project address. Check the internet connection "
                "and the Supabase project URL in Account settings, then retry. The request did "
                "not reach Supabase."
            )
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return (
                "Supabase did not respond before the connection timed out. Check the internet "
                "connection and Supabase service status, then retry."
            )
        if isinstance(reason, ConnectionRefusedError):
            return (
                "The connection to Supabase was refused before sign-in could complete. Check the "
                "internet connection and Supabase project status, then retry."
            )
        current = current.__cause__ or current.__context__
    message = friendly_cloud_error(error)
    if "could not reach supabase" in message.casefold():
        return (
            f"{message}. The request did not reach Supabase, so this is a network or project "
            "address problem rather than a password or portfolio-table error."
        )
    return f"Supabase request failed: {message}"


@dataclass(slots=True)
class CloudSession:
    access_token: str
    refresh_token: str
    expires_at: float
    user_id: str
    email: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CloudSession":
        user = payload.get("user") or {}
        expires_in = float(payload.get("expires_in") or 3600)
        expires_at = float(payload.get("expires_at") or 0)
        if expires_at <= 0:
            expires_at = datetime.now(tz=timezone.utc).timestamp() + expires_in
        return cls(
            access_token=str(payload.get("access_token") or ""),
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_at=expires_at,
            user_id=str(user.get("id") or payload.get("user_id") or ""),
            email=str(user.get("email") or payload.get("email") or ""),
        )

    def is_expiring(self, *, within_seconds: int = 90) -> bool:
        return self.expires_at <= datetime.now(tz=timezone.utc).timestamp() + within_seconds


@dataclass(slots=True)
class CloudPortfolio:
    id: str
    name: str
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "CloudPortfolio":
        return cls(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or "Unnamed"),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
        )


@dataclass(slots=True)
class CloudPurchase:
    id: str
    portfolio_id: str
    security_name: str | None
    ticker: str | None
    quantity: float | None
    purchase_price: float | None
    purchased_at: str | None
    note: str
    status: str
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "CloudPurchase":
        return cls(
            id=str(row.get("id") or ""),
            portfolio_id=str(row.get("portfolio_id") or ""),
            security_name=_optional_text(row.get("security_name")),
            ticker=_optional_text(row.get("ticker"), uppercase=True),
            quantity=_optional_float(row.get("quantity")),
            purchase_price=_optional_float(row.get("purchase_price")),
            purchased_at=_optional_text(row.get("purchased_at")),
            note=str(row.get("note") or ""),
            status=str(row.get("status") or "draft"),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
        )


class SupabasePortfolioClient:
    """Small authenticated Supabase Auth + PostgREST client."""

    def __init__(
        self,
        *,
        project_url: str,
        publishable_key: str,
        session_path: Path,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.project_url = project_url.rstrip("/")
        self.publishable_key = publishable_key.strip()
        self.session_path = session_path
        self.timeout_seconds = max(5.0, float(timeout_seconds))
        self.session: CloudSession | None = None
        self._lock = threading.RLock()
        self._load_session()

    @classmethod
    def from_project(cls, project_root: Path | None = None) -> "SupabasePortfolioClient":
        root = (project_root or Path(__file__).resolve().parents[1]).resolve()
        load_dotenv(root / ".env", override=True)
        project_url = os.getenv("SUPABASE_URL", "").strip()
        publishable_key = (
            os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
            or os.getenv("SUPABASE_ANON_KEY", "").strip()
        )
        timeout = float(os.getenv("SUPABASE_REQUEST_TIMEOUT", "20") or 20)
        return cls(
            project_url=project_url,
            publishable_key=publishable_key,
            session_path=root / "data" / "cloud_session.json",
            timeout_seconds=timeout,
        )

    @property
    def configured(self) -> bool:
        return self.project_url.startswith("https://") and bool(self.publishable_key)

    @property
    def signed_in(self) -> bool:
        return self.session is not None and bool(self.session.access_token)

    @property
    def current_email(self) -> str:
        return self.session.email if self.session else ""

    def require_configured(self) -> None:
        if not self.configured:
            raise CloudConfigurationError(
                "Cloud portfolios are not configured. Add SUPABASE_URL and "
                "SUPABASE_PUBLISHABLE_KEY to .env after running supabase/schema.sql."
            )

    def sign_up(self, email: str, password: str) -> str:
        self.require_configured()
        email = email.strip()
        if not email or not password:
            raise CloudAuthenticationError("Email and password are required.")
        # Email confirmation may return no new token, so discard any cached
        # session before creating a different account.
        self.sign_out()
        payload = self._raw_request(
            "POST",
            "/auth/v1/signup",
            body={"email": email, "password": password},
            auth=False,
        )
        if payload.get("access_token"):
            self.session = CloudSession.from_payload(payload)
            self._save_session()
            return f"Signed in as {self.session.email}."
        return "Account created. Check your email if confirmation is enabled, then sign in."

    def sign_in(self, email: str, password: str) -> CloudSession:
        self.require_configured()
        payload = self._raw_request(
            "POST",
            "/auth/v1/token?grant_type=password",
            body={"email": email.strip(), "password": password},
            auth=False,
        )
        session = CloudSession.from_payload(payload)
        if not session.access_token or not session.user_id:
            raise CloudAuthenticationError("Supabase did not return a valid user session.")
        self.session = session
        self._save_session()
        return session

    def sign_out(self) -> None:
        if self.signed_in:
            try:
                self._raw_request("POST", "/auth/v1/logout", body={}, auth=True, retry_auth=False)
            except CloudPortfolioError:
                pass
        self.session = None
        try:
            self.session_path.unlink(missing_ok=True)
        except OSError:
            pass

    def ensure_session(self) -> CloudSession:
        self.require_configured()
        with self._lock:
            if self.session is None:
                raise CloudAuthenticationError("Sign in to access cloud portfolios.")
            if self.session.is_expiring():
                self._refresh_session()
            assert self.session is not None
            return self.session

    def list_portfolios(self) -> list[CloudPortfolio]:
        rows = self._rest(
            "GET",
            "portfolios",
            query={
                "select": "id,name,created_at,updated_at",
                "order": "created_at.asc",
            },
        )
        return [CloudPortfolio.from_row(row) for row in _require_rows(rows)]

    def create_portfolio(self, name: str) -> CloudPortfolio:
        clean_name = _clean_portfolio_name(name)
        rows = self._rest(
            "POST",
            "portfolios",
            body={"name": clean_name},
            prefer="return=representation",
        )
        result = _require_rows(rows)
        if not result:
            raise CloudRequestError("Portfolio creation returned no row.")
        return CloudPortfolio.from_row(result[0])

    def rename_portfolio(self, portfolio_id: str, name: str) -> CloudPortfolio:
        rows = self._rest(
            "PATCH",
            "portfolios",
            query={"id": f"eq.{portfolio_id}"},
            body={"name": _clean_portfolio_name(name)},
            prefer="return=representation",
        )
        result = _require_rows(rows)
        if not result:
            raise CloudRequestError("Portfolio was not found or could not be renamed.")
        return CloudPortfolio.from_row(result[0])

    def delete_portfolio(self, portfolio_id: str) -> None:
        self._rest("DELETE", "portfolios", query={"id": f"eq.{portfolio_id}"})

    def find_or_create_portfolio(self, name: str) -> CloudPortfolio:
        clean_name = _clean_portfolio_name(name)
        for portfolio in self.list_portfolios():
            if portfolio.name.casefold() == clean_name.casefold():
                return portfolio
        return self.create_portfolio(clean_name)

    def list_purchases(self, portfolio_id: str) -> list[CloudPurchase]:
        rows = self._rest(
            "GET",
            "purchases",
            query={
                "select": (
                    "id,portfolio_id,security_name,ticker,quantity,purchase_price,"
                    "purchased_at,note,status,created_at,updated_at"
                ),
                "portfolio_id": f"eq.{portfolio_id}",
                "order": "purchased_at.desc.nullslast,created_at.desc",
            },
        )
        return [CloudPurchase.from_row(row) for row in _require_rows(rows)]

    def create_purchase(
        self,
        *,
        portfolio_id: str,
        security_name: str | None,
        ticker: str | None,
        quantity: float | None,
        purchase_price: float | None,
        purchased_at: str | None,
        note: str = "",
    ) -> CloudPurchase:
        row = _purchase_payload(
            portfolio_id=portfolio_id,
            security_name=security_name,
            ticker=ticker,
            quantity=quantity,
            purchase_price=purchase_price,
            purchased_at=purchased_at,
            note=note,
        )
        rows = self._rest(
            "POST",
            "purchases",
            body=row,
            prefer="return=representation",
        )
        result = _require_rows(rows)
        if not result:
            raise CloudRequestError("Purchase creation returned no row.")
        return CloudPurchase.from_row(result[0])

    def update_purchase(
        self,
        purchase_id: str,
        *,
        portfolio_id: str,
        security_name: str | None,
        ticker: str | None,
        quantity: float | None,
        purchase_price: float | None,
        purchased_at: str | None,
        note: str = "",
    ) -> CloudPurchase:
        row = _purchase_payload(
            portfolio_id=portfolio_id,
            security_name=security_name,
            ticker=ticker,
            quantity=quantity,
            purchase_price=purchase_price,
            purchased_at=purchased_at,
            note=note,
        )
        rows = self._rest(
            "PATCH",
            "purchases",
            query={"id": f"eq.{purchase_id}"},
            body=row,
            prefer="return=representation",
        )
        result = _require_rows(rows)
        if not result:
            raise CloudRequestError("Purchase was not found or could not be updated.")
        return CloudPurchase.from_row(result[0])

    def delete_purchase(self, purchase_id: str) -> None:
        self._rest("DELETE", "purchases", query={"id": f"eq.{purchase_id}"})

    def _rest(
        self,
        method: str,
        table: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | list[dict[str, Any]] | None = None,
        prefer: str | None = None,
    ) -> Any:
        suffix = ""
        if query:
            suffix = "?" + urlencode(query, safe=".,()*:-")
        return self._raw_request(
            method,
            f"/rest/v1/{table}{suffix}",
            body=body,
            auth=True,
            prefer=prefer,
        )

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[dict[str, Any]] | None = None,
        auth: bool,
        prefer: str | None = None,
        retry_auth: bool = True,
    ) -> Any:
        self.require_configured()
        if auth:
            self.ensure_session()
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "apikey": self.publishable_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if auth and self.session:
            headers["Authorization"] = f"Bearer {self.session.access_token}"
        if prefer:
            headers["Prefer"] = prefer
        request = Request(
            self.project_url + path,
            data=data,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                if not raw:
                    return []
                return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401 and auth and retry_auth and self.session and self.session.refresh_token:
                self._refresh_session()
                return self._raw_request(
                    method,
                    path,
                    body=body,
                    auth=auth,
                    prefer=prefer,
                    retry_auth=False,
                )
            message = _extract_error_message(raw) or f"Supabase returned HTTP {exc.code}."
            if path.startswith("/auth/"):
                raise CloudAuthenticationError(message) from exc
            raise CloudRequestError(message) from exc
        except URLError as exc:
            raise CloudRequestError(f"Could not reach Supabase: {exc.reason}") from exc
        except TimeoutError as exc:
            raise CloudRequestError("The Supabase request timed out.") from exc
        except json.JSONDecodeError as exc:
            raise CloudRequestError("Supabase returned an unreadable response.") from exc

    def _refresh_session(self) -> None:
        if self.session is None or not self.session.refresh_token:
            self.sign_out()
            raise CloudAuthenticationError("Your cloud session expired. Sign in again.")
        payload = self._raw_request(
            "POST",
            "/auth/v1/token?grant_type=refresh_token",
            body={"refresh_token": self.session.refresh_token},
            auth=False,
            retry_auth=False,
        )
        self.session = CloudSession.from_payload(payload)
        if not self.session.access_token:
            self.sign_out()
            raise CloudAuthenticationError("Your cloud session could not be refreshed.")
        self._save_session()

    def _load_session(self) -> None:
        try:
            payload = json.loads(self.session_path.read_text(encoding="utf-8"))
            self.session = CloudSession(**payload)
        except (OSError, ValueError, TypeError):
            self.session = None

    def _save_session(self) -> None:
        if self.session is None:
            return
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(json.dumps(asdict(self.session), indent=2), encoding="utf-8")
        try:
            os.chmod(self.session_path, 0o600)
        except OSError:
            pass
def _purchase_payload(
    *,
    portfolio_id: str,
    security_name: str | None,
    ticker: str | None,
    quantity: float | None,
    purchase_price: float | None,
    purchased_at: str | None,
    note: str,
) -> dict[str, Any]:
    ticker_clean = _optional_text(ticker, uppercase=True)
    quantity_clean = _optional_float(quantity)
    price_clean = _optional_float(purchase_price)
    date_clean = _optional_text(purchased_at)
    if quantity_clean is not None and quantity_clean <= 0:
        raise ValueError("Shares must be greater than zero when provided.")
    if price_clean is not None and price_clean < 0:
        raise ValueError("Purchase price cannot be negative.")
    complete = bool(ticker_clean and quantity_clean is not None and price_clean is not None)
    return {
        "portfolio_id": portfolio_id,
        "security_name": _optional_text(security_name),
        "ticker": ticker_clean,
        "quantity": quantity_clean,
        "purchase_price": price_clean,
        "purchased_at": date_clean,
        "note": note.strip(),
        "status": "complete" if complete else "draft",
    }


def _clean_portfolio_name(name: str) -> str:
    clean = " ".join(name.strip().split())
    if not clean:
        raise ValueError("Portfolio name cannot be blank.")
    if len(clean) > 80:
        raise ValueError("Portfolio name must be 80 characters or fewer.")
    return clean


def _optional_text(value: Any, *, uppercase: bool = False) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in {"n/a", "na", "unknown", "none", "null"}:
        return None
    return text.upper() if uppercase else text


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _require_rows(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise CloudRequestError("Supabase returned an unexpected database response.")
    return [row for row in payload if isinstance(row, dict)]


def _extract_error_message(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()[:500]
    for key in ("msg", "message", "error_description", "error", "details", "hint"):
        value = payload.get(key)
        if value:
            return str(value)
    return raw.strip()[:500]
