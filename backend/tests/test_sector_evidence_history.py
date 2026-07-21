from __future__ import annotations

from datetime import datetime
import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from app.models.trading import GlobalEvidenceSnapshot, SectorCrowdingDailySnapshot
from app.services import sector_evidence_history as history_service
from app.services.sector_evidence_history import (
    load_sector_history,
    persist_global_evidence_snapshot,
    persist_sector_temperature_snapshot,
)


def _sector_payload(
    trade_date: str,
    *,
    net_inflow: float = -18.0,
    risk_score: float = 82.0,
    updated_at: str | None = None,
    provider_updated_at: str | None = None,
    board_code: str = "BK1036",
    board_name: str = "半导体",
):
    return {
        "source": "东方财富板块订单流算法+东方财富两融T+1",
        "updated_at": updated_at or f"{trade_date}T15:01:00+08:00",
        "board_type": "行业",
        "items": [{
            "name": board_name,
            "board_code": board_code,
            "provider_trade_date": trade_date,
            "provider_updated_at": provider_updated_at or f"{trade_date}T14:59:00+08:00",
            "data_quality": "high",
            "heat_score": 84,
            "status": "过热兑现风险",
            "risk_level": "HIGH",
            "trend_score": 78.5,
            "flow_score": 31.2,
            "crowding_score": 91.0,
            "margin_score": 88.0,
            "attention_score": 96.0,
            "change_pct": -2.3,
            "change_pct_5d": 8.1,
            "change_pct_10d": 17.2,
            "net_inflow": net_inflow,
            "net_inflow_5d": 66.0,
            "net_inflow_10d": 142.0,
            "flow_speed": -1.25,
            "flow_acceleration": -0.16,
            "flow_turning": "INFLOW_FADING",
            "limit_up_count": 2,
            "financing_balance": 1288.0,
            "financing_net_buy": 9.8,
            "financing_balance_ratio": 7.2,
            "financing_net_buy_5d": 52.0,
            "financing_net_buy_10d": 106.0,
            "financing_net_buy_20d": 188.0,
            "margin_as_of": trade_date,
            # Even a bad upstream label must be persisted as T+1/non-realtime.
            "margin_realtime": True,
            "distribution_state": "高位派发警戒",
            "distribution_risk_level": "HIGH",
            "distribution_risk_score": risk_score,
            "order_flow_exhausted": True,
            "leverage_crowding": True,
            "price_response_weak": True,
            "distribution_confirmation_count": 3,
            "evidence": ["10日涨幅仍处高位"],
            "counter_evidence": ["两融为T+1慢变量"],
            "actions": ["停止追高"],
            "distribution_evidence": ["订单流转弱且价格负反馈"],
            "distribution_counter_evidence": ["尚未跌破长期趋势"],
            "distribution_actions": ["只作为联合预警，不机械清仓"],
        }],
    }


def test_sector_daily_snapshot_persists_full_contract_and_upserts(db_session):
    first = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload("2026-07-17"),
    )[0]

    assert first.board_key == "name:半导体"
    assert first.net_inflow == -18.0
    assert first.financing_net_buy_20d == 188.0
    assert first.margin_realtime is False
    assert first.distribution_state == "高位派发警戒"
    assert first.distribution_risk_score == 82.0
    assert first.order_flow_exhausted is True
    assert first.leverage_crowding is True
    assert first.price_response_weak is True
    assert json.loads(first.distribution_evidence_json) == ["订单流转弱且价格负反馈"]
    assert len(first.payload_hash) == 64

    first_hash = first.payload_hash
    second = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload("2026-07-17", net_inflow=-31.0, risk_score=91.0),
    )[0]

    assert second.id == first.id
    assert second.net_inflow == -31.0
    assert second.distribution_risk_score == 91.0
    assert second.payload_hash != first_hash
    assert db_session.query(SectorCrowdingDailySnapshot).count() == 1


def test_sector_upsert_rejects_stale_snapshot_and_preserves_newest_evidence(db_session):
    newest = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload(
            "2026-07-17",
            net_inflow=-31.0,
            risk_score=91.0,
            provider_updated_at="2026-07-17T15:00:00+08:00",
        ),
    )[0]
    newest_hash = newest.payload_hash

    stale = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload(
            "2026-07-17",
            net_inflow=99.0,
            risk_score=10.0,
            # A stale provider fact must not win merely because an old cache was
            # observed by this service at a later local collection time.
            updated_at="2026-07-17T16:00:00+08:00",
            provider_updated_at="2026-07-17T14:00:00+08:00",
        ),
    )[0]

    assert stale.id == newest.id
    assert stale.net_inflow == -31.0
    assert stale.distribution_risk_score == 91.0
    assert stale.payload_hash == newest_hash
    assert stale.provider_updated_at == datetime(2026, 7, 17, 15, 0)


def test_sector_identity_survives_missing_then_restored_board_code(db_session):
    without_code = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload("2026-07-17", board_code=""),
    )[0]
    with_code = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload(
            "2026-07-17",
            board_code="BK1036",
            provider_updated_at="2026-07-17T15:05:00+08:00",
        ),
    )[0]

    assert with_code.id == without_code.id
    assert with_code.board_key == "name:半导体"
    assert with_code.board_code == "BK1036"
    assert db_session.query(SectorCrowdingDailySnapshot).count() == 1


def test_sector_identity_promotes_code_only_row_when_name_arrives(db_session):
    code_only = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload("2026-07-17", board_name=""),
    )[0]
    named = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload(
            "2026-07-17",
            provider_updated_at="2026-07-17T15:05:00+08:00",
        ),
    )[0]

    assert code_only.id == named.id
    assert named.board_key == "name:半导体"
    assert named.board_name == "半导体"
    assert db_session.query(SectorCrowdingDailySnapshot).count() == 1


def test_sector_payload_rejects_anonymous_board_before_any_write(db_session):
    payload = _sector_payload("2026-07-17")
    payload["items"].append({"name": "", "board_code": ""})

    with pytest.raises(ValueError, match="缺少有效的板块名称和板块代码"):
        persist_sector_temperature_snapshot(db_session, payload)

    assert db_session.query(SectorCrowdingDailySnapshot).count() == 0


def test_missing_sector_metrics_remain_null_and_timestamps_are_shanghai_naive(db_session):
    row = persist_sector_temperature_snapshot(
        db_session,
        {
            "source": "只读测试源",
            "updated_at": "2026-07-16T16:30:00Z",
            "items": [{
                "name": "电力",
                "provider_updated_at": "2026-07-16T16:29:00Z",
            }],
        },
    )[0]

    assert row.trade_date == "2026-07-17"
    assert row.captured_at == datetime(2026, 7, 17, 0, 30)
    assert row.provider_updated_at == datetime(2026, 7, 17, 0, 29)
    assert row.captured_at.tzinfo is None
    assert row.created_at.tzinfo is None
    assert row.heat_score is None
    assert row.trend_score is None
    assert row.flow_score is None
    assert row.limit_up_count is None
    assert row.order_flow_exhausted is None
    assert row.leverage_crowding is None
    assert row.price_response_weak is None
    assert row.distribution_confirmation_count is None
    # This is an explicit domain invariant, not an imputed provider metric.
    assert row.margin_realtime is False


def test_sector_unique_race_retries_inside_savepoint(db_session, monkeypatch):
    original = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload("2026-07-17"),
    )[0]
    real_find = history_service._find_sector_row
    calls = 0

    def hide_first_lookup(db, values, *, lock):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Simulate another collector committing after our initial lookup.
            return None
        return real_find(db, values, lock=lock)

    monkeypatch.setattr(history_service, "_find_sector_row", hide_first_lookup)
    retried = persist_sector_temperature_snapshot(
        db_session,
        _sector_payload(
            "2026-07-17",
            net_inflow=-42.0,
            provider_updated_at="2026-07-17T15:05:00+08:00",
        ),
    )[0]

    assert calls >= 2
    assert retried.id == original.id
    assert retried.net_inflow == -42.0
    assert db_session.query(SectorCrowdingDailySnapshot).count() == 1


def test_load_sector_history_is_bounded_filtered_and_deterministic(db_session):
    for trade_date in ("2026-07-15", "2026-07-16", "2026-07-17"):
        persist_sector_temperature_snapshot(db_session, _sector_payload(trade_date))

    descending = load_sector_history(
        db_session,
        board_code="bk1036",
        start_date="2026-07-16",
        limit=2,
    )
    ascending = load_sector_history(
        db_session,
        board_type="行业",
        board_name="半导体",
        start_date="2026-07-15",
        end_date="2026-07-17",
        ascending=True,
    )

    assert [row.trade_date for row in descending] == ["2026-07-17", "2026-07-16"]
    assert [row.trade_date for row in ascending] == ["2026-07-15", "2026-07-16", "2026-07-17"]


def test_global_evidence_deduplicates_collection_time_but_not_market_content(db_session):
    payload = {
        "generated_at": "2026-07-17T08:20:00+08:00",
        "as_of": "2026-07-17T08:20:00+08:00",
        "source": ["Yahoo Finance只读延迟源", "东方财富全球指数"],
        "data_quality": "good",
        "us_sector_rank": [{
            "symbol": "SMH",
            "name": "半导体ETF代理",
            "change_pct": -3.2,
            "as_of": "2026-07-16T16:00:00-04:00",
            "source": "Yahoo Finance",
        }],
    }
    first = persist_global_evidence_snapshot(db_session, payload)

    repeated = {
        "data_quality": "good",
        "source": ["Yahoo Finance只读延迟源", "东方财富全球指数"],
        "as_of": "2026-07-17T08:25:00+08:00",
        "generated_at": "2026-07-17T08:25:00+08:00",
        "us_sector_rank": [dict(payload["us_sector_rank"][0])],
    }
    duplicate = persist_global_evidence_snapshot(db_session, repeated)

    assert duplicate.id == first.id
    assert len(first.payload_hash) == 64
    assert first.trade_date == "2026-07-17"
    assert first.data_quality == "good"
    assert db_session.query(GlobalEvidenceSnapshot).count() == 1

    next_day = dict(repeated)
    next_day["as_of"] = "2026-07-18T08:25:00+08:00"
    next_day["generated_at"] = "2026-07-18T08:25:00+08:00"
    next_day_snapshot = persist_global_evidence_snapshot(db_session, next_day)

    assert next_day_snapshot.id != first.id
    assert next_day_snapshot.payload_hash == first.payload_hash
    assert next_day_snapshot.trade_date == "2026-07-18"
    assert next_day_snapshot.captured_at.tzinfo is None
    assert db_session.query(GlobalEvidenceSnapshot).count() == 2

    changed = dict(repeated)
    changed["us_sector_rank"] = [{**payload["us_sector_rank"][0], "change_pct": -4.1}]
    second_market_state = persist_global_evidence_snapshot(db_session, changed)

    assert second_market_state.id != first.id
    assert second_market_state.payload_hash != first.payload_hash
    assert db_session.query(GlobalEvidenceSnapshot).count() == 3


def test_global_evidence_hash_recursively_ignores_collector_time_only(db_session):
    payload = {
        "generated_at": "2026-07-20T08:20:00+08:00",
        "as_of": "2026-07-20T08:20:00+08:00",
        "source": ["licensed-adapter"],
        "data_quality": "official",
        "etf_flows": [{
            "metric_id": "EWY_SHARES",
            "value": -100.0,
            "published_at": "2026-07-19T20:00:00-04:00",
            "as_of": "2026-07-19T16:00:00-04:00",
            "observed_at": "2026-07-20T08:19:58+08:00",
            "adapter_trace": {
                "received_at": "2026-07-20T08:19:59+08:00",
                "observed_at": "2026-07-20T08:20:00+08:00",
            },
        }],
    }
    first = persist_global_evidence_snapshot(db_session, payload)

    recollected = json.loads(json.dumps(payload))
    recollected["generated_at"] = "2026-07-20T08:25:00+08:00"
    recollected["as_of"] = "2026-07-20T08:25:00+08:00"
    recollected["etf_flows"][0]["observed_at"] = "2026-07-20T08:25:00+08:00"
    recollected["etf_flows"][0]["adapter_trace"] = {
        "received_at": "2026-07-20T08:25:01+08:00",
        "observed_at": "2026-07-20T08:25:02+08:00",
    }
    duplicate = persist_global_evidence_snapshot(db_session, recollected)

    provider_time_changed = json.loads(json.dumps(recollected))
    provider_time_changed["etf_flows"][0]["as_of"] = "2026-07-19T16:05:00-04:00"
    second = persist_global_evidence_snapshot(db_session, provider_time_changed)

    publication_changed = json.loads(json.dumps(recollected))
    publication_changed["etf_flows"][0]["published_at"] = "2026-07-19T20:05:00-04:00"
    third = persist_global_evidence_snapshot(db_session, publication_changed)

    assert duplicate.id == first.id
    assert second.id != first.id
    assert third.id not in {first.id, second.id}
    assert db_session.query(GlobalEvidenceSnapshot).count() == 3


def test_empty_sector_payload_is_a_noop(db_session):
    assert persist_sector_temperature_snapshot(db_session, {"items": []}) == []
    assert db_session.query(SectorCrowdingDailySnapshot).count() == 0


def test_sector_evidence_migration_upgrade_and_downgrade(monkeypatch):
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w3g7h8i9j0k1_sector_evidence_history.py"
    )
    spec = importlib.util.spec_from_file_location("sector_evidence_migration_test", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration, "op", Operations(context))
        migration.upgrade()

        inspector = inspect(connection)
        assert {
            "sector_crowding_daily_snapshots",
            "global_evidence_snapshots",
        }.issubset(set(inspector.get_table_names()))
        sector_columns = {
            column["name"]: column
            for column in inspector.get_columns("sector_crowding_daily_snapshots")
        }
        for column_name in (
            "heat_score",
            "trend_score",
            "flow_score",
            "limit_up_count",
            "order_flow_exhausted",
            "leverage_crowding",
            "price_response_weak",
            "distribution_confirmation_count",
        ):
            assert sector_columns[column_name]["nullable"] is True
        assert sector_columns["created_at"]["default"] is None
        assert sector_columns["updated_at"]["default"] is None

        global_columns = {
            column["name"]: column
            for column in inspector.get_columns("global_evidence_snapshots")
        }
        assert global_columns["captured_at"]["default"] is None
        unique_sets = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("global_evidence_snapshots")
        }
        assert ("trade_date", "payload_hash") in unique_sets
        assert ("payload_hash",) not in unique_sets

        migration.downgrade()
        assert "sector_crowding_daily_snapshots" not in inspect(connection).get_table_names()
        assert "global_evidence_snapshots" not in inspect(connection).get_table_names()
