from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from app.models.trading import SectorCrowdingDailySnapshot, SectorCrowdingSnapshotSample
from app.services.sector_evidence_history import (
    build_sector_state_evolution,
    load_sector_persistence_features,
    persist_sector_temperature_snapshot,
)
from app.services.trading_calendar import is_a_share_trading_day


def _payload(
    trade_date: str,
    time: str,
    *,
    state: str = "资金承载衰减",
    risk_level: str = "MEDIUM",
    net_inflow: float = -8.0,
) -> dict:
    observed_at = f"{trade_date}T{time}:00+08:00"
    return {
        "source": "provider-order-flow + margin-T+1",
        "updated_at": observed_at,
        "board_type": "行业",
        "items": [{
            "name": "半导体",
            "board_code": "BK1036",
            "provider_trade_date": trade_date,
            "provider_updated_at": observed_at,
            "data_quality": "high",
            "distribution_state": state,
            "distribution_risk_level": risk_level,
            "distribution_risk_score": 72,
            "distribution_confirmation_count": 3,
            "change_pct": -1.2,
            "net_inflow": net_inflow,
            "flow_speed": -0.6,
            "flow_acceleration": -0.2,
            "flow_turning": "OUTFLOW_ACCELERATING",
            "financing_balance": 120.0,
            "financing_net_buy": 3.0,
            "margin_as_of": trade_date,
            "distribution_evidence": ["资金进入后价格响应继续下降"],
            "distribution_counter_evidence": ["尚未跌破长期趋势"],
            "distribution_actions": ["停止追高，等待价格重新响应"],
        }],
    }


def test_intraday_samples_are_immutable_while_daily_summary_stays_compatible(db_session):
    first = _payload("2026-07-21", "10:00", net_inflow=-8.0)
    persist_sector_temperature_snapshot(db_session, first)
    persist_sector_temperature_snapshot(db_session, first)
    persist_sector_temperature_snapshot(
        db_session,
        _payload("2026-07-21", "10:05", net_inflow=-10.0),
    )

    assert db_session.query(SectorCrowdingDailySnapshot).count() == 1
    samples = (
        db_session.query(SectorCrowdingSnapshotSample)
        .order_by(SectorCrowdingSnapshotSample.captured_at.asc())
        .all()
    )
    assert len(samples) == 2
    assert [row.net_inflow for row in samples] == [-8.0, -10.0]
    assert samples[0].captured_at == datetime(2026, 7, 21, 10, 0)


def test_timestamp_only_refresh_does_not_create_fact_sample(db_session):
    first = _payload("2026-07-21", "10:00", net_inflow=-8.0)
    second = _payload("2026-07-21", "10:05", net_inflow=-8.0)

    persist_sector_temperature_snapshot(db_session, first)
    persist_sector_temperature_snapshot(db_session, second)

    samples = db_session.query(SectorCrowdingSnapshotSample).all()
    assert len(samples) == 1
    assert samples[0].provider_updated_at == datetime(2026, 7, 21, 10, 0)


def test_daily_turnover_completion_is_inferred_only_from_provider_close_time(db_session):
    intraday = _payload("2026-07-21", "14:59", net_inflow=-8.0)
    intraday["items"][0]["sector_turnover_amount"] = 100.0
    persisted = persist_sector_temperature_snapshot(db_session, intraday)[0]
    assert persisted.turnover_complete is False

    close = _payload("2026-07-21", "15:01", net_inflow=-9.0)
    close["items"][0]["sector_turnover_amount"] = 120.0
    persisted = persist_sector_temperature_snapshot(db_session, close)[0]
    assert persisted.turnover_complete is True

    # A late local replay of an old provider timestamp remains incomplete.
    replay = _payload("2026-07-22", "14:30", net_inflow=-7.0)
    replay["updated_at"] = "2026-07-22T18:00:00+08:00"
    replay["items"][0]["sector_turnover_amount"] = 90.0
    persisted = persist_sector_temperature_snapshot(db_session, replay)[0]
    assert persisted.turnover_complete is False


def test_history_enrichment_does_not_create_a_self_confirming_sample(db_session):
    original = _payload("2026-07-21", "10:00")
    original["items"][0]["instantaneous_distribution_state"] = "资金承载衰减"
    persist_sector_temperature_snapshot(db_session, original)

    enriched = deepcopy(original)
    enriched["updated_at"] = "2026-07-21T10:05:00+08:00"
    enriched["items"][0].update({
        "distribution_state": "高位派发风险",
        "strict_state": "高位派发风险",
        "sample_confirmation_count": 9,
        "trading_day_confirmation_count": 4,
        "persistence_confirmed": True,
        "financing_net_buy_slope_5d": 1.25,
        "financing_balance_ratio_percentile_60d": 98.0,
        "recent_state_samples": [{"state": "高位派发风险"}],
    })
    persist_sector_temperature_snapshot(db_session, enriched)

    assert db_session.query(SectorCrowdingSnapshotSample).count() == 1


def test_evolution_confirms_same_state_from_distinct_samples_or_trading_days(db_session):
    for time, flow in (("10:00", -8.0), ("10:05", -10.0), ("10:10", -12.0)):
        persist_sector_temperature_snapshot(
            db_session,
            _payload("2026-07-21", time, net_inflow=flow),
        )

    intraday = build_sector_state_evolution(
        db_session,
        board_code="BK1036",
    )[0]
    assert intraday["strict_state"] == "资金承载衰减"
    assert intraday["sample_confirmation_count"] == 3
    assert intraday["sample_confirmed"] is True
    assert intraday["trading_day_confirmation_count"] == 1
    assert intraday["persistence_confirmed"] is True
    assert len(intraday["samples"]) == 3
    assert intraday["samples"][0]["provider_updated_at"] == datetime(2026, 7, 21, 10, 0)

    persist_sector_temperature_snapshot(
        db_session,
        _payload("2026-07-22", "10:00", net_inflow=-6.0),
    )
    cross_day = build_sector_state_evolution(db_session, board_name="半导体")[0]
    assert cross_day["sample_confirmation_count"] == 1
    assert cross_day["trading_day_confirmation_count"] == 2
    assert cross_day["trading_day_confirmed"] is True


def test_missing_trading_day_breaks_cross_day_confirmation(db_session):
    persist_sector_temperature_snapshot(
        db_session,
        _payload("2026-07-21", "10:00", net_inflow=-8.0),
    )
    persist_sector_temperature_snapshot(
        db_session,
        _payload("2026-07-23", "10:00", net_inflow=-9.0),
    )

    result = build_sector_state_evolution(db_session, board_code="BK1036")[0]

    assert result["trading_day_confirmation_count"] == 1
    assert result["trading_day_confirmed"] is False


def test_intraday_confirmation_enforces_default_five_minute_gap_and_allows_injection(
    db_session,
):
    for time, flow in (("10:00", -8.0), ("10:01", -9.0), ("10:05", -10.0)):
        persist_sector_temperature_snapshot(
            db_session,
            _payload("2026-07-21", time, net_inflow=flow),
        )

    conservative = build_sector_state_evolution(db_session, board_code="BK1036")[0]
    assert conservative["sample_confirmation_count"] == 2
    assert conservative["sample_confirmation_min_interval_seconds"] == 300

    injected = build_sector_state_evolution(
        db_session,
        board_code="BK1036",
        min_sample_interval_seconds=60,
    )[0]
    assert injected["sample_confirmation_count"] == 3


def test_missing_or_changed_state_breaks_intraday_confirmation(db_session):
    persist_sector_temperature_snapshot(db_session, _payload("2026-07-21", "10:00"))
    persist_sector_temperature_snapshot(
        db_session,
        _payload(
            "2026-07-21",
            "10:05",
            state="高位派发风险",
            risk_level="HIGH",
        ),
    )

    result = build_sector_state_evolution(db_session, board_code="BK1036")[0]
    assert result["strict_state"] == "高位派发风险"
    assert result["sample_confirmation_count"] == 1
    assert result["persistence_confirmed"] is False


def test_persistence_confirms_instantaneous_state_not_gated_display_state(db_session):
    """A pending display downgrade must not erase the state being confirmed."""

    for time, flow in (("10:00", -8.0), ("10:05", -10.0)):
        payload = _payload(
            "2026-07-21",
            time,
            state="资金承载衰减",
            risk_level="HIGH",
            net_inflow=flow,
        )
        payload["items"][0]["instantaneous_distribution_state"] = "高位派发风险"
        persist_sector_temperature_snapshot(db_session, payload)

    samples = (
        db_session.query(SectorCrowdingSnapshotSample)
        .order_by(SectorCrowdingSnapshotSample.captured_at.asc())
        .all()
    )
    assert [row.instantaneous_distribution_state for row in samples] == [
        "高位派发风险",
        "高位派发风险",
    ]
    assert [row.distribution_state for row in samples] == [
        "资金承载衰减",
        "资金承载衰减",
    ]

    result = build_sector_state_evolution(db_session, board_code="BK1036")[0]
    assert result["strict_state"] == "高位派发风险"
    assert result["sample_confirmation_count"] == 2
    assert result["persistence_confirmed"] is True
    assert result["samples"][-1]["resolved_state"] == "资金承载衰减"


def test_persistence_features_use_true_daily_financing_series(db_session):
    rows = []
    first_day = date(2026, 1, 1)
    current = first_day
    while len(rows) < 60:
        if not is_a_share_trading_day(current):
            current += timedelta(days=1)
            continue
        index = len(rows)
        day = current.isoformat()
        rows.append(SectorCrowdingDailySnapshot(
            trade_date=day,
            board_type="行业",
            board_key="name:电力",
            board_code="BK0428",
            board_name="电力",
            captured_at=datetime.combine(current, datetime.min.time()).replace(hour=15),
            data_quality="high",
            distribution_state="健康增量",
            distribution_risk_level="LOW",
            financing_net_buy=float(index),
            # Cumulative fields deliberately disagree; the helper must not use
            # them as daily points when calculating a slope.
            financing_net_buy_5d=9999.0,
            financing_net_buy_10d=9999.0,
            financing_net_buy_20d=9999.0,
            financing_balance_ratio=float(index),
            margin_as_of=day,
            raw_payload_json="{}",
            payload_hash=f"{index:064d}",
        ))
        current += timedelta(days=1)
    db_session.add_all(rows)
    db_session.commit()

    features = load_sector_persistence_features(db_session, "行业")
    by_name = features["电力"]
    by_code = features["BK0428"]
    assert by_code is by_name
    assert by_name["last_state"] == "健康增量"
    assert by_name["last_trade_date"] == rows[-1].trade_date
    assert by_name["financing_net_buy_slope_5d"] == 1.0
    assert by_name["financing_net_buy_slope_10d"] == 1.0
    assert by_name["financing_net_buy_slope_20d"] == 1.0
    assert by_name["financing_balance_ratio_percentile_60d"] == 100.0
    assert by_name["financing_balance_ratio_percentile_120d"] is None
    assert by_name["trading_day_confirmation_count"] == 60


def test_persistence_features_expose_only_real_same_day_turnover(db_session):
    trading_days = [
        date(2026, 7, 14),
        date(2026, 7, 15),
        date(2026, 7, 16),
        date(2026, 7, 17),
        date(2026, 7, 20),
        date(2026, 7, 21),
        date(2026, 7, 22),
    ]
    rows = []
    for index, (current, turnover) in enumerate(zip(
        trading_days,
        (100.0, 110.0, 0.0, None, 140.0, 150.0, 160.0),
    )):
        day = current.isoformat()
        raw_item = {}
        if turnover is not None:
            raw_item["sector_turnover_amount"] = turnover
        # A tempting but unrelated alias must not be used when the auditable
        # sector turnover is missing.
        raw_item["turnover_amount"] = 9999.0
        rows.append(SectorCrowdingDailySnapshot(
            trade_date=day,
            provider_trade_date=day,
            board_type="行业",
            board_key="name:半导体",
            board_code="BK1036",
            board_name="半导体",
            captured_at=datetime.combine(current, datetime.min.time()).replace(hour=15),
            data_quality="high",
            turnover_complete=turnover is not None and turnover > 0,
            distribution_state="健康增量",
            distribution_risk_level="LOW",
            margin_as_of=day,
            raw_payload_json=json.dumps({"item": raw_item}, ensure_ascii=False),
            payload_hash=f"turnover-{index}",
        ))
    db_session.add_all(rows)
    db_session.commit()

    feature = load_sector_persistence_features(db_session, "行业")["半导体"]
    assert feature["daily_turnover_by_trade_date"] == {
        "2026-07-14": 100.0,
        "2026-07-15": 110.0,
        "2026-07-20": 140.0,
        "2026-07-21": 150.0,
        "2026-07-22": 160.0,
    }
    assert feature["daily_turnover_observations"] == 5
    assert feature["incomplete_turnover_dates"] == []
    assert feature["confirmation_basis"] == ["连续 7 个交易日保持同一状态"]


def test_incomplete_intraday_turnover_is_never_a_t1_denominator(db_session):
    day = "2026-07-20"
    db_session.add(SectorCrowdingDailySnapshot(
        trade_date=day,
        provider_trade_date=day,
        board_type="行业",
        board_key="name:半导体",
        board_code="BK1036",
        board_name="半导体",
        captured_at=datetime(2026, 7, 20, 14, 55),
        data_quality="high",
        turnover_complete=False,
        distribution_state="健康增量",
        distribution_risk_level="LOW",
        margin_as_of=day,
        raw_payload_json=json.dumps({
            "item": {"sector_turnover_amount": 160.0}
        }, ensure_ascii=False),
        payload_hash="intraday-turnover",
    ))
    db_session.commit()

    feature = load_sector_persistence_features(db_session, "行业")["半导体"]
    assert feature["daily_turnover_by_trade_date"] == {}
    assert feature["incomplete_turnover_dates"] == [day]


def test_carrying_efficiency_uses_time_spaced_immutable_samples(db_session):
    observations = (
        ("10:00", -1.0, 1.0),
        ("10:05", -0.5, 2.0),
        ("10:10", 0.0, 3.0),
        ("10:15", 0.5, 4.0),
    )
    for observed_time, price_change, flow_ratio in observations:
        payload = _payload("2026-07-21", observed_time, net_inflow=flow_ratio * 10)
        payload["items"][0]["change_pct"] = price_change
        payload["items"][0]["flow_ratio"] = flow_ratio
        persist_sector_temperature_snapshot(db_session, payload)

    feature = load_sector_persistence_features(db_session, "行业")["半导体"]
    assert feature["capital_price_carrying_sample_count"] == 3
    assert feature["capital_price_carrying_span_minutes"] == 15.0
    assert feature["capital_price_carrying_efficiency"] is not None
    assert feature["capital_price_carrying_efficiency"] > 50
    assert feature["capital_price_carrying_slope"] is not None
    assert feature["capital_price_carrying_method"] == "immutable_intraday_delta_rolling"

    strict = load_sector_persistence_features(
        db_session,
        "行业",
        carrying_min_span_seconds=20 * 60,
    )["半导体"]
    assert strict["capital_price_carrying_efficiency"] is None


def test_financing_history_missing_disclosure_metric_fails_closed(db_session):
    first_day = date(2026, 7, 1)
    for index in range(10):
        current = first_day + timedelta(days=index)
        day = current.isoformat()
        db_session.add(SectorCrowdingDailySnapshot(
            trade_date=day,
            board_type="行业",
            board_key="name:电力",
            board_code="BK0428",
            board_name="电力",
            captured_at=datetime.combine(current, datetime.min.time()).replace(hour=15),
            data_quality="high",
            distribution_state="健康增量",
            distribution_risk_level="LOW",
            financing_net_buy=None if index == 8 else float(index),
            financing_balance_ratio=float(index),
            margin_as_of=day,
            raw_payload_json="{}",
            payload_hash=f"gap-{index}",
        ))
    db_session.commit()

    feature = load_sector_persistence_features(db_session, "行业")["电力"]
    assert feature["financing_net_buy_slope_5d"] is None
    assert feature["financing_net_buy_slope_10d"] is None
    assert feature["margin_history_degraded"] is True
    assert feature["margin_history_missing_net_buy_dates"] == ["2026-07-09"]


def test_financing_history_missing_whole_trading_day_fails_closed(db_session):
    trading_days = [
        date(2026, 7, 6),
        date(2026, 7, 7),
        date(2026, 7, 8),
        # 7月9日整日披露缺失，不能跨越缺口计算五日斜率。
        date(2026, 7, 10),
        date(2026, 7, 13),
    ]
    for index, current in enumerate(trading_days):
        day = current.isoformat()
        db_session.add(SectorCrowdingDailySnapshot(
            trade_date=day,
            board_type="行业",
            board_key="name:电力",
            board_code="BK0428",
            board_name="电力",
            captured_at=datetime.combine(current, datetime.min.time()).replace(hour=15),
            data_quality="high",
            distribution_state="健康增量",
            distribution_risk_level="LOW",
            financing_net_buy=float(index),
            financing_balance_ratio=float(index),
            margin_as_of=day,
            raw_payload_json="{}",
            payload_hash=f"whole-gap-{index}",
        ))
    db_session.commit()

    feature = load_sector_persistence_features(db_session, "行业")["电力"]

    assert feature["financing_net_buy_slope_5d"] is None
    assert feature["margin_history_sequence_complete"] is False
    assert feature["margin_history_degraded"] is True


def test_sector_sample_migration_upgrade_and_downgrade(monkeypatch):
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "x4h8i9j0k1l2_sector_intraday_samples.py"
    )
    spec = importlib.util.spec_from_file_location("sector_sample_migration_test", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration, "op", Operations(context))
        migration.upgrade()
        inspector = inspect(connection)
        assert "sector_crowding_snapshot_samples" in inspector.get_table_names()
        unique_sets = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("sector_crowding_snapshot_samples")
        }
        assert ("trade_date", "board_type", "board_key", "payload_hash") in unique_sets
        migration.downgrade()
        assert "sector_crowding_snapshot_samples" not in inspect(connection).get_table_names()


def test_turnover_completion_migration_upgrade_and_downgrade(monkeypatch):
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "y6i9j0k1l2m3_sector_turnover_complete.py"
    )
    spec = importlib.util.spec_from_file_location(
        "sector_turnover_completion_migration_test",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE sector_crowding_daily_snapshots (id INTEGER PRIMARY KEY)"
        )
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration, "op", Operations(context))
        migration.upgrade()
        columns = {
            column["name"]
            for column in inspect(connection).get_columns(
                "sector_crowding_daily_snapshots"
            )
        }
        assert "turnover_complete" in columns
        migration.downgrade()
        columns = {
            column["name"]
            for column in inspect(connection).get_columns(
                "sector_crowding_daily_snapshots"
            )
        }
        assert "turnover_complete" not in columns
