import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import sessionmaker

from app.api.helpers.quotes import (
    _eastmoney_event_at,
    _provider_event_metadata,
    _sina_event_at,
)
from app.models.trading import Holding, IntradayEvidenceEvent, SimulationAccount, SimulationOrder
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


def test_shadow_scheduler_creates_one_dedicated_account_and_reuses_it(db_session, monkeypatch):
    testing_session = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    monkeypatch.setattr(intraday_collector, "SessionLocal", testing_session)
    calls: list[int] = []

    def fake_shadow(_db, account, *, now):
        calls.append(account.id)
        assert account.account_type == "shadow"
        assert account.automation_key == intraday_collector.SHADOW_ACCOUNT_AUTOMATION_KEY
        return SimpleNamespace(order_ids=[101], skipped=[], duplicate_signal_keys=[])

    monkeypatch.setattr(intraday_collector, "run_shadow_experiments", fake_shadow)
    now = datetime(2026, 7, 16, 10, 10)

    first = intraday_collector.run_simulation_shadow_once(now=now)
    second = intraday_collector.run_simulation_shadow_once(now=now)

    assert first["created_order_ids"] == [101]
    assert second["created_order_ids"] == [101]
    assert calls == [first["account_id"], first["account_id"]]
    check = testing_session()
    try:
        rows = check.query(SimulationAccount).filter(
            SimulationAccount.automation_key == intraday_collector.SHADOW_ACCOUNT_AUTOMATION_KEY,
        ).all()
        assert len(rows) == 1
    finally:
        check.close()


def test_shadow_scheduler_never_opens_database_outside_continuous_auction(monkeypatch):
    monkeypatch.setattr(
        intraday_collector,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("休市时不应创建影子账户")),
    )
    result = intraday_collector.run_simulation_shadow_once(
        now=datetime(2026, 7, 16, 12, 0),
    )
    assert result["skipped"] == "outside_continuous_auction"
    assert result["account_id"] is None


def test_shadow_close_equity_partial_run_does_not_mark_last_success(db_session, monkeypatch):
    testing_session = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    monkeypatch.setattr(intraday_collector, "SessionLocal", testing_session)
    monkeypatch.setattr(intraday_collector, "_simulation_shadow_equity_last_success_at", None)
    monkeypatch.setattr(
        intraday_collector,
        "mark_shadow_equity_after_close",
        lambda _db, *, now: SimpleNamespace(equity_ids=[1], skipped=[{"account_id": "2"}]),
    )

    result = intraday_collector.run_simulation_shadow_equity_once(
        now=datetime(2026, 7, 16, 15, 5)
    )

    assert result["equity_ids"] == [1]
    assert result["skipped_count"] == 1
    assert intraday_collector._simulation_shadow_equity_last_success_at is None


def test_shadow_close_equity_day_completes_only_without_errors_or_skips(monkeypatch):
    monkeypatch.setattr(intraday_collector, "_close_shadow_equity_date", None)

    assert not intraday_collector._record_close_shadow_equity_completion(
        {"errors": [], "skipped_count": 1},
        "2026-07-16",
    )
    assert intraday_collector._close_shadow_equity_date is None

    assert not intraday_collector._record_close_shadow_equity_completion(
        {"errors": ["provider unavailable"], "skipped_count": 0},
        "2026-07-16",
    )
    assert intraday_collector._close_shadow_equity_date is None

    assert intraday_collector._record_close_shadow_equity_completion(
        {"errors": [], "skipped_count": 0},
        "2026-07-16",
    )
    assert intraday_collector._close_shadow_equity_date == "2026-07-16"


def test_server_scheduler_persists_opportunity_events_without_browser(db_session, monkeypatch):
    testing_session = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    monkeypatch.setattr(intraday_collector, "SessionLocal", testing_session)
    db_session.add(Holding(
        code="600584",
        name="长电科技",
        quantity=200,
        cost_price=100,
        current_price=101,
        total_asset=200_000,
    ))
    db_session.commit()
    now = datetime(2026, 7, 16, 10, 30)
    provider_calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        intraday_collector.opportunity_market_provider,
        "information_differential",
        lambda **kwargs: provider_calls.append(("news", kwargs["force_refresh"])) or {"items": []},
    )
    monkeypatch.setattr(
        intraday_collector.opportunity_market_provider,
        "sector_flow",
        lambda **kwargs: provider_calls.append((kwargs["flow_type"], kwargs["force_refresh"]))
        or {"inflow": [], "outflow": []},
    )
    monkeypatch.setattr(
        intraday_collector.opportunity_market_provider,
        "sector_opening_breadth",
        lambda **kwargs: {"trade_date": kwargs["trade_date"], "data_quality": "missing", "sample_count": 0},
    )
    monkeypatch.setattr(
        intraday_collector.opportunity_market_provider,
        "limit_up_ladder",
        lambda *_args, **_kwargs: {"groups": []},
    )
    monkeypatch.setattr(
        intraday_collector.opportunity_radar_service,
        "assess",
        lambda *_args, **_kwargs: {
            "as_of": now.isoformat(),
            "updated_at": now.isoformat(),
            "source": ["测试真实资讯源"],
            "data_quality": "ok",
            "items": [{
                "id": "server-news-600584",
                "title": "长电科技发布风险事项公告",
                "source": "测试真实资讯源",
                "published_at": datetime(2026, 7, 16, 10, 20).isoformat(),
                "url": "https://example.test/news/600584",
                "related_stocks": ["600584"],
                "primary_sector": "半导体",
                "sectors": ["半导体"],
                "status": "已确认",
                "confirmation_score": 82,
                "evidence": ["公告发布后价格与资金同步转弱。"],
                "counter_evidence": ["尚未跌破计划硬止损。"],
                "missing": [],
                "action": "按计划等待承接确认。",
                "trade_constraint": "消息不自动触发卖出。",
                "claim_level": "OFFICIAL",
                "news_impact_status": "IMPACT_CONFIRMED",
                "market_validation": "CONFIRMED",
                "sentiment": "利空",
                "buy_signal": False,
            }],
            "counts": {"已确认": 1},
            "notes": [],
        },
    )
    monkeypatch.setattr(
        intraday_collector.opportunity_sector_expansion_service,
        "assess",
        lambda *_args, **_kwargs: {
            "as_of": now.isoformat(),
            "source": ["测试真实涨停源"],
            "items": [{
                "sector": "商业航天",
                "status": "增量已确认",
                "confirmation_score": 90,
                "new_limit_up_count": 3,
                "total_limit_up_count": 5,
                "leaders": ["航天甲"],
                "evidence": ["最近15分钟新增3只涨停。"],
                "counter_evidence": [],
                "risk": ["禁止追后排。"],
                "source": ["测试真实涨停源"],
            }],
        },
    )

    result = intraday_collector.run_opportunity_radar_collection_once(now=now)

    assert result["errors"] == []
    assert result["persisted_count"] == 2
    assert {name for name, _force in provider_calls} == {"news", "行业资金流", "概念资金流"}
    assert all(force is False for _name, force in provider_calls)
    rows = db_session.query(IntradayEvidenceEvent).all()
    assert {row.event_type for row in rows} == {
        "SECTOR_INCREMENT_CONFIRMED",
        "HOLDING_NEWS_NEGATIVE_IMPACT_CONFIRMED",
    }


def test_server_opportunity_job_never_persists_a_historical_request(monkeypatch):
    monkeypatch.setattr(
        intraday_collector,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("历史回看不应打开实时写入事务")),
    )
    result = intraday_collector.run_opportunity_radar_collection_once(
        now=datetime(2026, 7, 16, 10, 30),
        target_trade_date="2000-01-04",
    )
    assert result["skipped"] == "historical_read_only"
    assert result["persisted_count"] == 0


def test_server_opportunity_job_degrades_provider_failures_without_fake_rows(db_session, monkeypatch):
    testing_session = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    monkeypatch.setattr(intraday_collector, "SessionLocal", testing_session)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(intraday_collector.opportunity_market_provider, "information_differential", unavailable)
    monkeypatch.setattr(intraday_collector.opportunity_market_provider, "sector_flow", unavailable)
    monkeypatch.setattr(intraday_collector.opportunity_market_provider, "sector_opening_breadth", unavailable)
    monkeypatch.setattr(intraday_collector.opportunity_market_provider, "limit_up_ladder", unavailable)
    monkeypatch.setattr(
        intraday_collector.opportunity_radar_service,
        "assess",
        lambda *_args, **_kwargs: {
            "as_of": "2026-07-16T10:30:00",
            "data_quality": "missing",
            "items": [],
            "counts": {},
            "notes": [],
        },
    )

    result = intraday_collector.run_opportunity_radar_collection_once(
        now=datetime(2026, 7, 16, 10, 30),
    )

    assert result["errors"] == []
    assert result["data_quality"] == "missing"
    assert result["persisted_count"] == 0
    assert db_session.query(IntradayEvidenceEvent).count() == 0
    assert any("未生成替代消息" in note for note in result["notes"])
    assert any("未生成模拟资金证据" in note for note in result["notes"])


def test_opportunity_failure_in_minute_loop_does_not_undo_holding_collection(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(intraday_collector, "COLLECTOR_ENABLED", True)
    monkeypatch.setattr(intraday_collector, "_is_market_watch_time", lambda: True)
    monkeypatch.setattr(intraday_collector, "_is_simulation_match_time", lambda: False)
    monkeypatch.setattr(intraday_collector, "_shanghai_now_naive", lambda: datetime(2026, 7, 16, 10, 30))
    monkeypatch.setattr(
        intraday_collector,
        "run_intraday_collection_once",
        lambda *_args, **_kwargs: calls.append("holding"),
    )

    def fail_opportunity(*_args, **_kwargs):
        calls.append("opportunity")
        raise RuntimeError("radar failed")

    monkeypatch.setattr(intraday_collector, "run_opportunity_radar_collection_once", fail_opportunity)

    async def stop_after_one_iteration(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(intraday_collector.asyncio, "sleep", stop_after_one_iteration)
    try:
        asyncio.run(intraday_collector._collector_loop())
    except asyncio.CancelledError:
        pass

    assert calls == ["holding", "opportunity"]
    assert intraday_collector._opportunity_radar_last_error.startswith("RuntimeError:")


def test_holding_collection_exception_resets_running_and_next_iteration_continues(monkeypatch):
    calls: list[str] = []
    holding_attempts = 0
    monkeypatch.setattr(intraday_collector, "COLLECTOR_ENABLED", True)
    monkeypatch.setattr(intraday_collector, "_is_market_watch_time", lambda: True)
    monkeypatch.setattr(intraday_collector, "_is_simulation_match_time", lambda: False)
    monkeypatch.setattr(intraday_collector, "_shanghai_now_naive", lambda: datetime(2026, 7, 16, 10, 30))
    monkeypatch.setattr(intraday_collector, "_collector_running", False)
    monkeypatch.setattr(intraday_collector, "_collector_last_success_at", None)
    monkeypatch.setattr(intraday_collector, "_collector_last_error", "")

    def flaky_holding(*_args, **_kwargs):
        nonlocal holding_attempts
        holding_attempts += 1
        calls.append(f"holding:{holding_attempts}")
        if holding_attempts == 1:
            raise RuntimeError("database finalize failed")

    monkeypatch.setattr(intraday_collector, "run_intraday_collection_once", flaky_holding)
    monkeypatch.setattr(
        intraday_collector,
        "run_opportunity_radar_collection_once",
        lambda *_args, **_kwargs: calls.append("opportunity"),
    )

    async def run_two_iterations():
        await intraday_collector._collector_iteration()
        assert intraday_collector._collector_running is False
        assert intraday_collector._collector_last_error.startswith("RuntimeError:")
        assert intraday_collector._collector_last_success_at is None

        await intraday_collector._collector_iteration()
        assert intraday_collector._collector_running is False
        assert intraday_collector._collector_last_error == ""
        assert intraday_collector._collector_last_success_at == datetime(2026, 7, 16, 10, 30)

    asyncio.run(run_two_iterations())

    assert calls == ["holding:1", "opportunity", "holding:2", "opportunity"]


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
