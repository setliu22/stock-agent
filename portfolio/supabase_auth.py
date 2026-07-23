"""Small, UI-independent wrapper around Supabase email authentication."""

from __future__ import annotations

from dataclasses import dataclass
import ssl
from typing import Any

from .certificates import configure_ssl_certificates

configure_ssl_certificates()

from supabase import Client, create_client


@dataclass(frozen=True)
class AuthResult:
    message: str
    signed_in: bool
    email: str | None = None


def is_certificate_error(error: BaseException) -> bool:
    """Recognize TLS verification errors even when an HTTP client wrapped them."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        text = str(current).lower()
        if "certificate_verify_failed" in text or "certificate verify failed" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def friendly_auth_error(error: BaseException) -> str:
    if is_certificate_error(error):
        return (
            "Python could not verify Supabase's security certificate. "
            "Run “Install Stock Agent.command” again so the trusted certificate "
            "bundle is installed, then retry. Do not disable SSL verification."
        )
    message = str(error).strip() or type(error).__name__
    return f"Supabase request failed: {message}"


class SupabaseAuth:
    """An in-memory authenticated Supabase client for the current app session."""

    def __init__(self, url: str, publishable_key: str) -> None:
        normalized_url = url.strip().rstrip("/")
        normalized_key = publishable_key.strip()
        if not normalized_url or not normalized_key:
            raise ValueError("Save a Supabase project URL and publishable key first.")
        self.client: Client = create_client(normalized_url, normalized_key)

    @staticmethod
    def _response_email(response: Any) -> str | None:
        user = getattr(response, "user", None)
        return getattr(user, "email", None) if user is not None else None

    def sign_up(self, email: str, password: str) -> AuthResult:
        response = self.client.auth.sign_up(
            {"email": email.strip(), "password": password}
        )
        response_email = self._response_email(response) or email.strip()
        if getattr(response, "session", None) is None:
            return AuthResult(
                "Account created. Check your email for the Supabase confirmation link, "
                "then return here and sign in.",
                signed_in=False,
                email=response_email,
            )
        return AuthResult("Account created and signed in.", True, response_email)

    def sign_in(self, email: str, password: str) -> AuthResult:
        response = self.client.auth.sign_in_with_password(
            {"email": email.strip(), "password": password}
        )
        response_email = self._response_email(response) or email.strip()
        return AuthResult("Signed in successfully.", True, response_email)

    def sign_out(self) -> AuthResult:
        self.client.auth.sign_out()
        return AuthResult("Signed out.", False)
