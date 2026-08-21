from __future__ import annotations

import os
import ssl

import pytest

from portfolio.certificates import configure_ssl_certificates
from portfolio.cloud_portfolios import friendly_auth_error, is_certificate_error
from portfolio.config import DEFAULT_GROQ_MODEL, get_settings, save_supabase_settings


def test_settings_use_current_groq_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    settings = get_settings(tmp_path / "portfolio.db")

    assert settings.groq_model == DEFAULT_GROQ_MODEL


def test_settings_migrate_retired_groq_model_without_rewriting_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    settings = get_settings(tmp_path / "portfolio.db")

    assert settings.groq_model == DEFAULT_GROQ_MODEL
    assert os.environ["GROQ_MODEL"] == "llama-3.3-70b-versatile"


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
