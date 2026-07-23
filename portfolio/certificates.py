"""Trusted certificate configuration for bundled and terminal launches."""

from __future__ import annotations

import os

import certifi


def configure_ssl_certificates() -> str:
    """Use certifi unless the user or administrator supplied a custom CA bundle."""
    ca_bundle = os.environ.get("SSL_CERT_FILE") or certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
    return ca_bundle
