from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from app.api.helpers.quotes import (
    _eastmoney_event_at,
    _provider_event_metadata,
    _sina_event_at,
)
from app.models.trading import SimulationAccount, SimulationOrder
from app.schemas.simulation import SimulationAccountCreate, SimulationOrderCreate
from app.services import intraday_collector
from app.services.simulation import create_account, submit_order


def _order_payload(code: str) -> SimulationOrderCreate:
    return SimulationOrderCreate(
        strategy_source="expectation_volume_price",
        code=code,
        name=f"测试{code}",
        side="BUY",
        order_type="MARKET",
        quantity=100,
    )


def _quote(when: datetime) -> dict[str, object]:
    return {
        "name": "测试股份",
        "price": 10.0,
        "prev_close": 9.8,
        "open": 9.9,
        "high": 10.1,
        "low": 9.8,
        "quote_time": when,
        "note": "东方财富实时行情",
    }


def test_simulation_scheduler_discovers_open_orders_and_isolates_accounts(db_session, monkeypatch):
    first = create_account(db_session, SimulationAccountCreate(name="账户一", initial_cash=100000))
    second = create_account(db_session, SimulationAccountCreate(name="账户二", initial_cash=100000))
    submitted_at = datetime(2026, 7, 15, 10, 0)
    for account, code in ((first, "600001"), (second, "600002")):
        order = submit_order(
            db_session,
            account,
            _order_payload(code),
            now=submitted_at,
            quote_loader=lambda _: _quote(submitted_at),
        )
        assert order.status == "OPEN"

    testing_session = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    monkeypatch.setattr(intraday_collector, "SessionLocal", testing_session)

    called: list[int] = []

    def fake_process(_db, account: SimulationAccount, *, now):
        called.append(account.id)
        if account.id == first.id:
            raise RuntimeError("单账户行情失败")
        return [object()]

    monkeypatch.setattr(intraday_collector, "process_open_orders", fake_process)
    result = intraday_collector.run_simulation_matching_once(
        now=datetime(2026, 7, 15, 10, 1)
    )

    assert result["account_count"] == 2
    assert result["processed_count"] == 1
    assert set(called) == {first.id, second.id}
    assert result["errors"] == [f"account:{first.id}:RuntimeError"]
    assert db_session.query(SimulationOrder).filter_by(status="OPEN").count() == 2


def test_simulation_scheduler_skips_outside_continuous_auction(monkeypatch):
    monkeypatch.setattr(
        intraday_collector,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("休市时不应打开数据库")),
    )
    result = intraday_collector.run_simulation_matching_once(
        now=datetime(2026, 7, 15, 12, 0)
    )
    assert result["account_count"] == 0
    assert result["processed_count"] == 0
    assert result["skipped"] == "outside_continuous_auction"


def test_quote_provider_timestamps_preserve_exchange_event_time():
    event = _eastmoney_event_at(1784080860)
    assert event is not None
    assert event.tzinfo is None
    assert _eastmoney_event_at(1784080860000) == event
    assert _eastmoney_event_at(None) is None

    sina_event = _sina_event_at("2026-07-15", "10:01:00")
    assert sina_event == datetime(2026, 7, 15, 10, 1)
    assert _sina_event_at("", "10:01:00") is None

    received = datetime(2026, 7, 15, 10, 1, 30)
    metadata = _provider_event_metadata(sina_event, received_at=received)
    assert metadata["provider_event_at"] == sina_event
    assert metadata["received_at"] == received
    assert metadata["age_seconds"] == 30
    assert metadata["timestamp_quality"] == "exchange"

    aware_event = datetime(2026, 7, 15, 2, 1, tzinfo=timezone.utc)
    aware_metadata = _provider_event_metadata(aware_event, received_at=received)
    assert aware_metadata["provider_event_at"] == sina_event
