from __future__ import annotations

from datetime import date, datetime, timezone

from portfolio.config import Settings
from portfolio.controller import StockAgentController
from portfolio.cloud_portfolios import CloudPortfolio, CloudPurchase, CloudSession
from portfolio.models import Purchase
from portfolio.market_regime import MarketRegimeSnapshot
from portfolio.portfolio_import import parse_portfolio_update_json_message


def test_controller_records_explicit_ticker(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "portfolio.db",
        groq_api_key=None,
        groq_model="test-model",
        lseg_session_name="desktop.workspace",
    )
    controller = StockAgentController(settings=settings)
    purchase = controller.record_purchase("AAPL", 1, 100, date(2026, 1, 1))
    assert purchase.ticker == "AAPL"
    assert controller.holdings()[0].quantity == 1


def test_controller_holding_snapshots_calculate_gain(tmp_path, monkeypatch) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "portfolio.db",
        groq_api_key=None,
        groq_model="test-model",
        lseg_session_name="desktop.workspace",
    )
    controller = StockAgentController(settings=settings)
    controller.record_purchase("AAPL", 2, 100, date(2026, 1, 1))
    monkeypatch.setattr("portfolio.controller.current_price", lambda _ticker: 125.0)

    snapshots = controller.holding_snapshots()

    assert snapshots[0].current_price == 125.0
    assert snapshots[0].market_value == 250.0
    assert snapshots[0].gain_loss == 50.0
    assert snapshots[0].return_percent == 25.0


def test_controller_builds_common_session_portfolio_history(tmp_path, monkeypatch) -> None:
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    controller = StockAgentController(settings=settings)
    controller.database.record_purchases(
        [
            Purchase("AAPL", 2, 100, date(2026, 1, 1)),
            Purchase("MSFT", 1, 200, date(2026, 1, 1)),
        ]
    )
    histories = {
        "AAPL": [(date(2026, 8, 10), 110), (date(2026, 8, 11), 115)],
        "MSFT": [(date(2026, 8, 10), 210), (date(2026, 8, 11), 220)],
    }
    monkeypatch.setattr("portfolio.controller.recent_closes", lambda ticker: histories[ticker])

    points, positions = controller.performance_histories()

    assert [(point.as_of, point.market_value) for point in points] == [
        (date(2026, 8, 10), 430),
        (date(2026, 8, 11), 450),
    ]
    assert [(point.as_of, point.market_value) for point in positions["AAPL"]] == [
        (date(2026, 8, 10), 220),
        (date(2026, 8, 11), 230),
    ]


def test_controller_delegates_market_regime(tmp_path, monkeypatch) -> None:
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    controller = StockAgentController(settings=settings)
    expected = MarketRegimeSnapshot(
        regime="Test regime",
        summary="Test summary",
        emphasis=(),
        indicators=(),
        missing_evidence=(),
        generated_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr("portfolio.controller.build_market_regime", lambda: expected)

    assert controller.market_regime() is expected
    assert controller.research_policy().regime == "Test regime"
    assert controller.research_policy().rules


def test_controller_replaces_policy_when_macro_regime_refreshes(tmp_path, monkeypatch) -> None:
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    controller = StockAgentController(settings=settings)
    snapshot = MarketRegimeSnapshot(
        regime="Tightening and contracting liquidity",
        summary="Test",
        emphasis=(),
        indicators=(),
        missing_evidence=(),
        generated_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr("portfolio.controller.build_market_regime", lambda: snapshot)

    controller.market_regime()

    assert controller.research_policy().regime == snapshot.regime
    assert any("cash flow" in rule for rule in controller.research_policy().rules)


class _FakeCloudClient:
    def __init__(self, remote_purchases=None):
        self.remote_purchases = list(remote_purchases or [])
        self.created = []
        self.signed_out = False
        self.signed_in = False
        self.updated = []

    def sign_up(self, email, _password):
        self.signed_in = True
        self.session = CloudSession("a", "r", 9999999999, "user-1", email)
        return "Account created and signed in."

    @property
    def current_email(self):
        return self.session.email if hasattr(self, "session") else ""

    def sign_in(self, _email, _password):
        self.signed_in = True
        return CloudSession("a", "r", 9999999999, "user-1", "person@example.com")

    def list_portfolios(self):
        return [CloudPortfolio("portfolio-1", "Main")]

    def create_portfolio(self, name):
        return CloudPortfolio("portfolio-1", name)

    def list_purchases(self, _portfolio_id):
        return self.remote_purchases

    def create_purchase(self, **kwargs):
        self.created.append(kwargs)

    def update_purchase(self, purchase_id, **kwargs):
        self.updated.append((purchase_id, kwargs))

    def sign_out(self):
        self.signed_out = True


def test_account_sign_in_restores_cloud_holdings_and_sign_out_clears_cache(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    controller = StockAgentController(settings=settings)
    fake = _FakeCloudClient([
        CloudPurchase("purchase-1", "portfolio-1", "AAPL", "AAPL", 2, 100, "2026-01-01", "", "complete")
    ])
    monkeypatch.setattr("portfolio.controller.SupabasePortfolioClient.from_project", lambda _root: fake)

    controller.account_sign_in("person@example.com", "password")
    assert controller.holdings()[0].ticker == "AAPL"
    controller.account_sign_out()
    assert controller.holdings() == []
    assert fake.signed_out


def test_account_sign_in_replaces_stale_local_holdings_when_cloud_is_empty(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    controller = StockAgentController(settings=settings)
    controller.database.record_purchase(Purchase("MSFT", 3, 200, date(2026, 1, 1)))
    fake = _FakeCloudClient()
    monkeypatch.setattr("portfolio.controller.SupabasePortfolioClient.from_project", lambda _root: fake)

    controller.account_sign_in("person@example.com", "password")

    assert controller.holdings() == []
    assert fake.created == []


def test_account_sign_up_reuses_new_session_without_second_login(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    controller = StockAgentController(settings=settings)
    fake = _FakeCloudClient()
    monkeypatch.setattr("portfolio.controller.SupabasePortfolioClient.from_project", lambda _root: fake)

    result = controller.account_sign_up("person@example.com", "password")

    assert result.signed_in
    assert result.email == "person@example.com"
    assert controller.cloud_client is fake


def test_failed_cloud_hydration_signs_out_and_clears_local_cache(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    controller = StockAgentController(settings=settings)
    controller.database.record_purchase(Purchase("MSFT", 1, 200, date(2026, 1, 1)))
    fake = _FakeCloudClient()
    monkeypatch.setattr("portfolio.controller.SupabasePortfolioClient.from_project", lambda _root: fake)
    monkeypatch.setattr(
        controller,
        "_hydrate_local_portfolio",
        lambda _client: (_ for _ in ()).throw(RuntimeError("schema unavailable")),
    )

    try:
        controller.account_sign_in("person@example.com", "password")
    except RuntimeError:
        pass

    assert fake.signed_out
    assert controller.cloud_client is None
    assert controller.holdings() == []


def test_post_login_purchase_syncs_before_logout(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    controller = StockAgentController(settings=settings)
    fake = _FakeCloudClient()
    monkeypatch.setattr("portfolio.controller.SupabasePortfolioClient.from_project", lambda _root: fake)
    controller.account_sign_in("person@example.com", "password")

    controller.record_purchase("AAPL", 2, 100, date(2026, 1, 1))

    assert len(fake.created) == 1
    assert fake.created[0]["ticker"] == "AAPL"


def test_post_login_update_syncs_existing_cloud_purchase(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "portfolio.db", None, "test-model", "desktop.workspace")
    controller = StockAgentController(settings=settings)
    fake = _FakeCloudClient([
        CloudPurchase("purchase-1", "portfolio-1", "AAPL", "AAPL", 2, 100, "2026-01-01", "", "complete")
    ])
    monkeypatch.setattr("portfolio.controller.SupabasePortfolioClient.from_project", lambda _root: fake)
    controller.account_sign_in("person@example.com", "password")
    controller.database.apply_portfolio_updates(parse_portfolio_update_json_message('{"ticker": "AAPL", "purchase_date": "2026-08-01"}'))
    controller.sync_local_portfolio()

    assert fake.updated[0][0] == "purchase-1"
    assert fake.updated[0][1]["purchased_at"] == "2026-08-01"
