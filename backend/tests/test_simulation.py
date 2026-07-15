from datetime import datetime, timedelta
import json

from app.models.trading import (
    ExpectationSnapshot,
    Holding,
    MarketRegimeSnapshot,
    SimulationClosedTrade,
    SimulationEvidenceSnapshot,
    SimulationFill,
    SimulationPosition,
    SimulationTradeLot,
    TradeLog,
    VolumePriceSnapshot,
)
from app.schemas.simulation import SimulationAccountCreate, SimulationOrderCreate
from app.services.simulation import (
    create_account,
    mark_to_market,
    performance_report,
    process_open_orders,
    submit_order,
)


def _quote(price: float, *, previous_close: float = 10, when: datetime | None = None, **extra):
    payload = {
        "name": "测试股份",
        "price": price,
        "prev_close": previous_close,
        "open": price,
        "high": price,
        "low": price,
        "note": "东方财富实时行情",
    }
    if when is not None:
        payload["quote_time"] = when
    payload.update(extra)
    return payload


def _payload(side: str, *, strategy: str = "expectation_volume_price", order_type: str = "MARKET", limit_price: float = 0):
    return SimulationOrderCreate(
        strategy_source=strategy,
        code="600001",
        name="测试股份",
        side=side,
        order_type=order_type,
        limit_price=limit_price,
        quantity=100,
    )


def _process(db_session, account, when: datetime, quote):
    rows = process_open_orders(
        db_session,
        account,
        now=when,
        quote_loader=lambda _: quote,
    )
    assert rows
    return rows[-1]


def test_simulation_account_api(client):
    response = client.post("/api/simulation/accounts", json={"name": "纪律模拟盘", "initial_cash": 200000})
    assert response.status_code == 200
    body = response.json()
    assert body["cash"] == 200000
    listed = client.get("/api/simulation/accounts")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "纪律模拟盘"


def test_submission_outside_continuous_auction_is_rejected_but_keeps_evidence(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    before_open = datetime(2026, 7, 15, 9, 20)
    order = submit_order(
        db_session, account, _payload("BUY"), now=before_open,
        quote_loader=lambda _: _quote(10, when=before_open),
    )
    assert order.status == "REJECTED"
    assert "连续竞价" in order.reject_reason
    assert db_session.get(SimulationEvidenceSnapshot, order.decision_evidence_snapshot_id) is not None


def test_day_order_expires_instead_of_carrying_to_next_trade_date(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    submitted = datetime(2026, 7, 15, 14, 59)
    order = submit_order(
        db_session, account, _payload("BUY"), now=submitted,
        quote_loader=lambda _: _quote(10, when=submitted),
    )
    assert order.status == "OPEN"
    next_day = datetime(2026, 7, 16, 9, 31)
    order = _process(db_session, account, next_day, _quote(10, when=next_day))
    assert order.status == "EXPIRED"
    assert "仅当日有效" in order.reject_reason
    assert db_session.query(SimulationFill).count() == 0


def test_future_quote_is_removed_from_decision_snapshot(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    submitted = datetime(2026, 7, 15, 10, 0)
    order = submit_order(
        db_session, account, _payload("BUY"), now=submitted,
        quote_loader=lambda _: _quote(10, when=submitted + timedelta(seconds=30)),
    )
    assert order.status == "OPEN"
    snapshot = db_session.get(SimulationEvidenceSnapshot, order.decision_evidence_snapshot_id)
    assert snapshot.data_quality == "timestamp_missing"
    assert "price" not in json.loads(snapshot.quote_json)


def test_t_plus_one_costs_and_performance(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    day_one = datetime(2026, 7, 15, 10, 0)
    buy = submit_order(
        db_session,
        account,
        _payload("BUY", strategy="holding_execution"),
        now=day_one,
        quote_loader=lambda _: _quote(10, when=day_one),
    )
    assert buy.status == "OPEN"
    buy = _process(db_session, account, day_one + timedelta(minutes=1), _quote(10, when=day_one + timedelta(minutes=1)))
    assert buy.status == "FILLED"
    position = db_session.query(SimulationPosition).filter_by(account_id=account.id, code="600001").one()
    assert position.quantity == 100
    assert position.available_quantity == 0
    assert position.today_buy_quantity == 100
    assert account.cash == 98994.99  # 1000 gross + 5 commission + 0.01 transfer fee
    assert db_session.query(Holding).count() == 0
    assert db_session.query(TradeLog).count() == 0

    same_day_sell = submit_order(
        db_session,
        account,
        _payload("SELL", strategy="holding_execution"),
        now=day_one.replace(hour=14),
        quote_loader=lambda _: _quote(10.5, when=day_one.replace(hour=14)),
    )
    assert same_day_sell.status == "OPEN"
    same_day_sell = _process(
        db_session, account, day_one.replace(hour=14, minute=1),
        _quote(10.5, when=day_one.replace(hour=14, minute=1)),
    )
    assert same_day_sell.status == "REJECTED"
    assert "T+1" in same_day_sell.reject_reason

    day_two = day_one + timedelta(days=1)
    sell = submit_order(
        db_session,
        account,
        _payload("SELL", strategy="holding_execution"),
        now=day_two,
        quote_loader=lambda _: _quote(10.5, when=day_two),
    )
    assert sell.status == "OPEN"
    sell = _process(db_session, account, day_two + timedelta(minutes=1), _quote(10.5, when=day_two + timedelta(minutes=1)))
    assert sell.status == "FILLED"
    fill = db_session.query(SimulationFill).filter_by(order_id=sell.id).one()
    assert fill.stamp_tax == 0.53
    assert fill.commission == 5
    assert fill.realized_pnl == 39.45  # net of both-side commission/transfer fee and sell-side stamp tax
    assert db_session.query(Holding).count() == 0
    assert db_session.query(TradeLog).count() == 0
    report = performance_report(db_session, account)
    assert report["sell_count"] == 1
    assert report["win_rate"] == 100
    assert report["by_strategy"][0]["key"] == "holding_execution"


def test_rejects_limit_suspension_missing_and_future_quote(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0)
    limit_order = submit_order(
        db_session,
        account,
        _payload("BUY"),
        now=now,
        quote_loader=lambda _: _quote(11, when=now),
    )
    assert limit_order.status == "OPEN"
    limit_order = _process(db_session, account, now + timedelta(minutes=1), _quote(11, when=now + timedelta(minutes=1)))
    assert limit_order.status == "REJECTED"
    assert "涨停" in limit_order.reject_reason

    suspended = submit_order(
        db_session,
        account,
        _payload("BUY"),
        now=now,
        quote_loader=lambda _: _quote(10, when=now, suspended=True),
    )
    assert suspended.status == "OPEN"
    suspended = _process(
        db_session, account, now + timedelta(minutes=1),
        _quote(10, when=now + timedelta(minutes=1), suspended=True),
    )
    assert suspended.status == "REJECTED"
    assert "停牌" in suspended.reject_reason

    missing = submit_order(
        db_session,
        account,
        _payload("BUY"),
        now=now,
        quote_loader=lambda _: {},
    )
    assert missing.status == "OPEN"
    missing = _process(db_session, account, now + timedelta(minutes=1), {})
    assert missing.status == "OPEN"
    assert "provider_event_at" in missing.reject_reason

    future = submit_order(
        db_session,
        account,
        _payload("BUY"),
        now=now,
        quote_loader=lambda _: _quote(10, when=now),
    )
    assert future.status == "OPEN"
    future = _process(
        db_session, account, now + timedelta(minutes=1),
        _quote(10, when=now + timedelta(minutes=1, seconds=30)),
    )
    assert future.status == "REJECTED"
    assert "未来数据" in future.reject_reason

    same_minute = submit_order(
        db_session,
        account,
        _payload("BUY"),
        now=now,
        quote_loader=lambda _: _quote(10, when=now),
    )
    same_minute = _process(
        db_session, account, now + timedelta(seconds=30),
        _quote(10, when=now + timedelta(seconds=30)),
    )
    assert same_minute.status == "OPEN"
    assert "禁止同K线成交" in same_minute.reject_reason

    decision_preceding_quote = submit_order(
        db_session,
        account,
        _payload("BUY"),
        now=now,
        quote_loader=lambda _: _quote(10, when=now),
    )
    decision_preceding_quote = _process(
        db_session, account, now + timedelta(minutes=1),
        _quote(10, when=now - timedelta(seconds=30)),
    )
    assert decision_preceding_quote.status == "REJECTED"
    assert "早于委托决策时点" in decision_preceding_quote.reject_reason


def test_limit_down_without_buyers_is_conservatively_rejected(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0)
    order = submit_order(
        db_session,
        account,
        _payload("SELL"),
        now=now,
        quote_loader=lambda _: _quote(9, when=now, bid1_volume=0),
    )
    assert order.status == "OPEN"
    order = _process(
        db_session, account, now + timedelta(minutes=1),
        _quote(9, when=now + timedelta(minutes=1), bid1_volume=0),
    )
    assert order.status == "REJECTED"
    assert "跌停且无可见买盘" in order.reject_reason


def test_limit_restriction_is_side_specific(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0)
    position = SimulationPosition(
        account_id=account.id,
        code="600001",
        name="测试股份",
        quantity=100,
        available_quantity=100,
        today_buy_quantity=0,
        average_cost=10,
        last_rollover_date=now.date().isoformat(),
    )
    db_session.add(position)
    db_session.commit()

    sell_at_limit_up = submit_order(
        db_session, account, _payload("SELL"), now=now,
        quote_loader=lambda _: _quote(11, when=now),
    )
    sell_at_limit_up = _process(
        db_session, account, now + timedelta(minutes=1),
        _quote(11, when=now + timedelta(minutes=1)),
    )
    assert sell_at_limit_up.status == "FILLED"

    buy_at_limit_down = submit_order(
        db_session, account, _payload("BUY"), now=now + timedelta(minutes=2),
        quote_loader=lambda _: _quote(9, when=now + timedelta(minutes=2)),
    )
    buy_at_limit_down = _process(
        db_session, account, now + timedelta(minutes=3),
        _quote(9, when=now + timedelta(minutes=3)),
    )
    assert buy_at_limit_down.status == "FILLED"


def test_cross_border_etf_t0_differs_from_stock_t1(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0)
    buy_payload = SimulationOrderCreate(
        strategy_source="expectation_volume_price",
        code="513100",
        name="纳指ETF",
        side="BUY",
        quantity=100,
    )
    buy = submit_order(
        db_session,
        account,
        buy_payload,
        now=now,
        quote_loader=lambda _: _quote(5, previous_close=5, when=now, name="纳指ETF"),
    )
    assert buy.status == "OPEN"
    buy = _process(
        db_session, account, now + timedelta(minutes=1),
        _quote(5, previous_close=5, when=now + timedelta(minutes=1), name="纳指ETF"),
    )
    assert buy.status == "FILLED"
    position = db_session.query(SimulationPosition).filter_by(account_id=account.id, code="513100").one()
    assert position.available_quantity == 100
    sell_payload = buy_payload.model_copy(update={"side": "SELL"})
    sell_time = now + timedelta(minutes=5)
    sell = submit_order(
        db_session,
        account,
        sell_payload,
        now=sell_time,
        quote_loader=lambda _: _quote(5.1, previous_close=5, when=sell_time, name="纳指ETF"),
    )
    assert sell.status == "OPEN"
    sell = _process(
        db_session, account, sell_time + timedelta(minutes=1),
        _quote(5.1, previous_close=5, when=sell_time + timedelta(minutes=1), name="纳指ETF"),
    )
    assert sell.status == "FILLED"


def test_non_marketable_limit_order_stays_open(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0)
    order = submit_order(
        db_session,
        account,
        _payload("BUY", order_type="LIMIT", limit_price=9.8),
        now=now,
        quote_loader=lambda _: _quote(10, when=now),
    )
    assert order.status == "OPEN"
    order = _process(
        db_session, account, now + timedelta(minutes=1),
        _quote(10, when=now + timedelta(minutes=1)),
    )
    assert order.status == "OPEN"
    assert db_session.query(SimulationFill).count() == 0


def test_same_minute_never_fills_and_next_minute_can_fill(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0, 5)
    order = submit_order(
        db_session, account, _payload("BUY"), now=now,
        quote_loader=lambda _: _quote(10, when=now),
    )
    assert order.status == "OPEN"
    order = _process(
        db_session, account, now + timedelta(seconds=30),
        _quote(10, when=now + timedelta(seconds=30)),
    )
    assert order.status == "OPEN"
    assert db_session.query(SimulationFill).count() == 0
    next_minute = now.replace(minute=1, second=1)
    order = _process(db_session, account, next_minute, _quote(10, when=next_minute))
    assert order.status == "FILLED"
    assert db_session.query(SimulationFill).count() == 1


def test_stale_provider_event_keeps_order_open_and_records_age(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    submitted = datetime(2026, 7, 15, 10, 0)
    order = submit_order(
        db_session, account, _payload("BUY"), now=submitted,
        quote_loader=lambda _: _quote(10, when=submitted),
    )
    evaluated_at = submitted + timedelta(minutes=5)
    stale_event_at = submitted + timedelta(minutes=1)
    order = _process(
        db_session,
        account,
        evaluated_at,
        _quote(10.1, when=stale_event_at),
    )
    assert order.status == "OPEN"
    assert "陈旧" in order.reject_reason
    assert db_session.query(SimulationFill).filter_by(order_id=order.id).count() == 0
    latest = (
        db_session.query(SimulationEvidenceSnapshot)
        .filter_by(account_id=account.id, code=order.code)
        .order_by(SimulationEvidenceSnapshot.version.desc())
        .first()
    )
    assert latest.data_quality == "stale"
    quote_payload = json.loads(latest.quote_json)
    assert quote_payload["provider_event_at"].startswith("2026-07-15 10:01")
    assert quote_payload["received_at"].startswith("2026-07-15 10:05")
    assert quote_payload["age_seconds"] == 240.0


def test_processing_a_filled_order_again_is_idempotent(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0)
    order = submit_order(
        db_session, account, _payload("BUY"), now=now,
        quote_loader=lambda _: _quote(10, when=now),
    )
    first = process_open_orders(
        db_session,
        account,
        now=now + timedelta(minutes=1),
        quote_loader=lambda _: _quote(10, when=now + timedelta(minutes=1)),
    )
    second = process_open_orders(
        db_session,
        account,
        now=now + timedelta(minutes=2),
        quote_loader=lambda _: _quote(10.1, when=now + timedelta(minutes=2)),
    )
    assert [row.id for row in first] == [order.id]
    assert second == []
    assert db_session.query(SimulationFill).filter_by(order_id=order.id).count() == 1
    unique_names = {
        constraint.name for constraint in SimulationFill.__table__.constraints
        if constraint.name
    }
    assert "uq_sim_fill_order" in unique_names


def test_fill_keeps_decision_evidence_and_performance_uses_it(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0)
    decision_market = MarketRegimeSnapshot(
        trade_date=now.date().isoformat(),
        captured_at=now - timedelta(seconds=30),
        source="decision",
        data_quality="realtime",
        regime_code="DECISION_REGIME",
        regime_name="决策时环境",
    )
    db_session.add(decision_market)
    db_session.commit()

    order = submit_order(
        db_session, account, _payload("BUY"), now=now,
        quote_loader=lambda _: _quote(10, when=now),
    )
    decision_snapshot_id = order.decision_evidence_snapshot_id
    fill_market = MarketRegimeSnapshot(
        trade_date=now.date().isoformat(),
        captured_at=now + timedelta(seconds=30),
        source="fill",
        data_quality="realtime",
        regime_code="FILL_REGIME",
        regime_name="成交时环境",
    )
    db_session.add(fill_market)
    db_session.commit()

    processed = _process(
        db_session,
        account,
        now + timedelta(minutes=1),
        _quote(10, when=now + timedelta(minutes=1)),
    )
    fill = db_session.query(SimulationFill).filter_by(order_id=processed.id).one()
    db_session.refresh(processed)
    assert processed.decision_evidence_snapshot_id == decision_snapshot_id
    assert fill.fill_evidence_snapshot_id != decision_snapshot_id
    decision_snapshot = db_session.get(SimulationEvidenceSnapshot, decision_snapshot_id)
    fill_snapshot = db_session.get(SimulationEvidenceSnapshot, fill.fill_evidence_snapshot_id)
    assert decision_snapshot.market_regime == "DECISION_REGIME"
    assert fill_snapshot.market_regime == "FILL_REGIME"

    next_day = now + timedelta(days=1)
    sell = submit_order(
        db_session, account, _payload("SELL"), now=next_day,
        quote_loader=lambda _: _quote(10.5, when=next_day),
    )
    sell = _process(
        db_session,
        account,
        next_day + timedelta(minutes=1),
        _quote(10.5, when=next_day + timedelta(minutes=1)),
    )
    assert sell.status == "FILLED"
    report = performance_report(db_session, account)
    assert [row["key"] for row in report["by_market_regime"]] == ["DECISION_REGIME"]


def test_partial_exits_create_one_closed_trade_attributed_to_entry_strategy(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    entry_at = datetime(2026, 7, 15, 10, 0)
    entry_payload = _payload("BUY", strategy="limit_up").model_copy(update={"quantity": 200})
    buy = submit_order(
        db_session, account, entry_payload, now=entry_at,
        quote_loader=lambda _: _quote(10, when=entry_at),
    )
    buy = _process(
        db_session, account, entry_at + timedelta(minutes=1),
        _quote(10, when=entry_at + timedelta(minutes=1)),
    )
    lot = db_session.query(SimulationTradeLot).filter_by(entry_order_id=buy.id).one()
    assert lot.initial_quantity == 200
    assert lot.strategy_source == "limit_up"

    exit_at = entry_at + timedelta(days=1)
    first_exit = submit_order(
        db_session, account, _payload("SELL", strategy="holding_execution"), now=exit_at,
        quote_loader=lambda _: _quote(10.5, when=exit_at),
    )
    first_exit = _process(
        db_session, account, exit_at + timedelta(minutes=1),
        _quote(10.5, when=exit_at + timedelta(minutes=1)),
    )
    assert first_exit.status == "FILLED"
    db_session.refresh(lot)
    assert lot.remaining_quantity == 100
    assert db_session.query(SimulationClosedTrade).count() == 0
    assert performance_report(db_session, account)["closed_trade_count"] == 0

    second_at = exit_at + timedelta(minutes=2)
    second_exit = submit_order(
        db_session, account, _payload("SELL", strategy="expectation_volume_price"), now=second_at,
        quote_loader=lambda _: _quote(10.6, when=second_at),
    )
    second_exit = _process(
        db_session, account, second_at + timedelta(minutes=1),
        _quote(10.6, when=second_at + timedelta(minutes=1)),
    )
    assert second_exit.status == "FILLED"
    closed = db_session.query(SimulationClosedTrade).one()
    assert closed.quantity == 200
    assert closed.strategy_source == "limit_up"
    assert closed.entry_decision_evidence_snapshot_id == buy.decision_evidence_snapshot_id
    report = performance_report(db_session, account)
    assert report["closed_trade_count"] == 1
    assert report["sell_count"] == 1
    assert report["by_strategy"][0]["key"] == "limit_up"
    assert report["by_strategy"][0]["closed_trade_count"] == 1


def test_duplicate_evidence_content_hash_is_reproducible(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0)
    payload = _payload("BUY", order_type="LIMIT", limit_price=9.8)
    first = submit_order(
        db_session, account, payload, now=now,
        quote_loader=lambda _: _quote(10, when=now),
    )
    second = submit_order(
        db_session, account, payload, now=now + timedelta(seconds=10),
        quote_loader=lambda _: _quote(10, when=now + timedelta(seconds=10)),
    )
    first_snapshot = db_session.get(SimulationEvidenceSnapshot, first.decision_evidence_snapshot_id)
    second_snapshot = db_session.get(SimulationEvidenceSnapshot, second.decision_evidence_snapshot_id)
    assert first_snapshot.version == 1
    assert second_snapshot.version == 2
    assert first_snapshot.content_hash == second_snapshot.content_hash


def test_evidence_snapshot_excludes_future_source_rows(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 15, 10, 0)
    past_market = MarketRegimeSnapshot(
        trade_date="2026-07-15",
        captured_at=now - timedelta(minutes=1),
        source="test",
        data_quality="realtime",
        regime_code="RISK_ON",
        regime_name="赚钱效应较强",
    )
    future_market = MarketRegimeSnapshot(
        trade_date="2026-07-15",
        captured_at=now + timedelta(minutes=1),
        source="future",
        data_quality="realtime",
        regime_code="FUTURE_FORBIDDEN",
        regime_name="未来状态",
    )
    expectation = ExpectationSnapshot(
        trade_date="2026-07-15",
        code="600001",
        name="测试股份",
        stage="第一阶段确认",
        expectation_gap_score=-8,
        expectation_result="WEAKER",
        created_at=now - timedelta(seconds=30),
    )
    volume = VolumePriceSnapshot(
        trade_date="2026-07-15",
        code="600001",
        name="测试股份",
        captured_at=now - timedelta(seconds=20),
        pattern="跌破VWAP",
        data_quality="realtime",
        data_source="test",
    )
    db_session.add_all([past_market, future_market, expectation, volume])
    db_session.commit()

    order = submit_order(
        db_session,
        account,
        _payload("BUY"),
        now=now,
        quote_loader=lambda _: _quote(10, when=now),
    )
    snapshot = db_session.get(SimulationEvidenceSnapshot, order.decision_evidence_snapshot_id)
    assert snapshot.market_regime == "RISK_ON"
    assert snapshot.expectation_gap_score == -8
    assert snapshot.expectation_gap_band == "negative"
    sources = json.loads(snapshot.source_versions_json)
    assert sources["market_regime_snapshot_id"] == past_market.id
    assert snapshot.captured_at >= snapshot.quote_time


def test_daily_equity_and_maximum_drawdown(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    day_one = datetime(2026, 7, 15, 10, 0)
    buy = submit_order(
        db_session,
        account,
        _payload("BUY", strategy="limit_up"),
        now=day_one,
        quote_loader=lambda _: _quote(10, when=day_one),
    )
    assert buy.status == "OPEN"
    buy = _process(
        db_session, account, day_one + timedelta(minutes=1),
        _quote(10, when=day_one + timedelta(minutes=1)),
    )
    assert buy.status == "FILLED"
    first = mark_to_market(
        db_session,
        account,
        now=day_one.replace(hour=15),
        quote_loader=lambda _: _quote(10, when=day_one.replace(hour=15)),
    )
    second_day = day_one + timedelta(days=1)
    second = mark_to_market(
        db_session,
        account,
        now=second_day.replace(hour=15),
        quote_loader=lambda _: _quote(9, previous_close=10, when=second_day.replace(hour=15)),
    )
    assert second.total_equity < first.total_equity
    assert second.drawdown_pct < 0
    report = performance_report(db_session, account)
    assert report["maximum_drawdown_pct"] == abs(second.drawdown_pct)
