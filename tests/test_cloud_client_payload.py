import pytest

from portfolio.cloud_portfolios import _purchase_payload


def test_purchase_payload_uses_nulls_and_marks_incomplete_records_draft():
    payload = _purchase_payload(
        portfolio_id="portfolio-1",
        security_name=None,
        ticker="n/a",
        quantity=5,
        purchase_price=None,
        purchased_at=None,
        note="test",
    )
    assert payload["ticker"] is None
    assert payload["purchase_price"] is None
    assert payload["status"] == "draft"


def test_purchase_payload_allows_missing_date_for_return_complete_record():
    payload = _purchase_payload(
        portfolio_id="portfolio-1",
        security_name="Palantir",
        ticker="pltr",
        quantity=5,
        purchase_price=20,
        purchased_at=None,
        note="",
    )
    assert payload["ticker"] == "PLTR"
    assert payload["status"] == "complete"


def test_purchase_payload_rejects_invalid_numbers():
    with pytest.raises(ValueError):
        _purchase_payload(
            portfolio_id="portfolio-1",
            security_name=None,
            ticker="PLTR",
            quantity=0,
            purchase_price=20,
            purchased_at=None,
            note="",
        )
