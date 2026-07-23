from portfolio.cloud_portfolios import CloudPurchase
from portfolio.input_normalization import normalize_research_request
from portfolio.portfolio_chat import is_purchase_statement, parse_follow_up, parse_purchase_statement
from portfolio.portfolio_metrics import calculate_portfolio_metrics


def _purchase(
    *,
    ticker="PLTR",
    quantity=5.0,
    price=20.0,
    purchased_at="2026-07-21",
):
    return CloudPurchase(
        id="purchase-1",
        portfolio_id="portfolio-1",
        security_name=ticker,
        ticker=ticker,
        quantity=quantity,
        purchase_price=price,
        purchased_at=purchased_at,
        note="",
        status="complete",
    )


def test_lowercase_us_is_normalized_only_as_geography():
    assert normalize_research_request("analyze us stocks") == "analyze US stocks"
    assert normalize_research_request("screen stocks in us") == "screen stocks in US"
    assert normalize_research_request("tell us about PLTR") == "tell us about PLTR"


def test_purchase_chat_asks_then_merges_one_follow_up(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    message = "I bought 5 shares of pltr"
    assert is_purchase_statement(message)
    draft = parse_purchase_statement(message)
    assert draft.ticker == "PLTR"
    assert draft.quantity == 5
    assert "purchase price per share" in draft.missing_fields()
    completed = parse_follow_up(draft, "Portfolio A at $21.50 today")
    assert completed.portfolio_name == "Portfolio A"
    assert completed.purchase_price == 21.50
    assert completed.purchased_at is not None


def test_returns_exclude_missing_initial_price():
    complete = _purchase(quantity=5, price=20)
    incomplete = _purchase(ticker="AAPL", quantity=2, price=None)
    metrics = calculate_portfolio_metrics([complete, incomplete], {"PLTR": 25, "AAPL": 200})
    assert metrics.total_cost == 100
    assert metrics.priced_cost == 100
    assert metrics.market_value == 125
    assert metrics.gain_loss == 25
    assert metrics.return_percent == 25
    assert metrics.excluded_purchase_count == 1
