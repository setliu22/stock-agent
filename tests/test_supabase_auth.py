from __future__ import annotations

import os
import ssl

import pytest

from portfolio.certificates import configure_ssl_certificates
from portfolio.config import save_supabase_settings
from portfolio.supabase_auth import SupabaseAuth, friendly_auth_error, is_certificate_error


class _User:
    email = "person@example.com"


class _Response:
    def __init__(self, *, session) -> None:
        self.user = _User()
        self.session = session


class _Auth:
    def __init__(self) -> None:
        self.signed_up_with = None
        self.signed_in_with = None
        self.signed_out = False
        self.signup_session = object()

    def sign_up(self, credentials):
        self.signed_up_with = credentials
        return _Response(session=self.signup_session)

    def sign_in_with_password(self, credentials):
        self.signed_in_with = credentials
        return _Response(session=object())

    def sign_out(self):
        self.signed_out = True


def _service() -> tuple[SupabaseAuth, _Auth]:
    service = SupabaseAuth.__new__(SupabaseAuth)
    auth = _Auth()
    service.client = type("Client", (), {"auth": auth})()
    return service, auth


def test_save_supabase_settings_persists_connection_without_password(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)

    save_supabase_settings(
        "https://example.supabase.co/",
        "sb_publishable_example",
        env_path=env_path,
    )

    text = env_path.read_text()
    assert "SUPABASE_URL='https://example.supabase.co'" in text
    assert "SUPABASE_PUBLISHABLE_KEY='sb_publishable_example'" in text
    assert "PASSWORD" not in text
    assert os.environ["SUPABASE_URL"] == "https://example.supabase.co"


@pytest.mark.parametrize(
    "url",
    ["", "http://example.supabase.co", "https://example.com"],
)
def test_save_supabase_settings_rejects_invalid_project_url(tmp_path, url) -> None:
    with pytest.raises(ValueError):
        save_supabase_settings(url, "key", env_path=tmp_path / ".env")


def test_certificate_configuration_respects_custom_bundle(monkeypatch, tmp_path) -> None:
    custom_bundle = str(tmp_path / "company-ca.pem")
    monkeypatch.setenv("SSL_CERT_FILE", custom_bundle)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

    assert configure_ssl_certificates() == custom_bundle
    assert os.environ["REQUESTS_CA_BUNDLE"] == custom_bundle


def test_certificate_error_is_recognized_through_wrapper() -> None:
    try:
        try:
            raise ssl.SSLCertVerificationError("unable to get local issuer certificate")
        except ssl.SSLCertVerificationError as error:
            raise RuntimeError("connection failed") from error
    except RuntimeError as wrapped:
        assert is_certificate_error(wrapped)
        assert "Install Stock Agent.command" in friendly_auth_error(wrapped)


def test_signup_reports_immediate_session() -> None:
    service, auth = _service()

    result = service.sign_up(" person@example.com ", "password")

    assert result.signed_in
    assert result.email == "person@example.com"
    assert auth.signed_up_with == {
        "email": "person@example.com",
        "password": "password",
    }


def test_signup_explains_email_confirmation() -> None:
    service, auth = _service()
    auth.signup_session = None

    result = service.sign_up("person@example.com", "password")

    assert not result.signed_in
    assert "Check your email" in result.message


def test_signin_and_signout() -> None:
    service, auth = _service()

    signed_in = service.sign_in("person@example.com", "password")
    signed_out = service.sign_out()

    assert signed_in.signed_in
    assert not signed_out.signed_in
    assert auth.signed_in_with == {
        "email": "person@example.com",
        "password": "password",
    }
    assert auth.signed_out
