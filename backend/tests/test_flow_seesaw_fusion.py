import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.api.helpers.execution import _dedupe_events, _trade_date
from app.api.helpers.seesaw import _intraday_sell_triggers, _sector_rotation_item
from app.models.trading import IntradayEvidenceEvent


def test_sector_rotation_carries_causal_flow_kinetics():
    item = SimpleNamespace(
        name="半导体",
        change_pct=1.2,
        net_inflow=-8.0,
        main_inflow=-6.0,
        timeline=[],
        leaders=["长电科技"],
        flow_speed=-1.25,
        flow_acceleration=-0.08,
        flow_turning="TURN_TO_OUTFLOW",
        flow_signal="价格上涨但资金转弱，形成资金价格背离，警惕诱多",
        flow_as_of="2026-07-15 10:05:00",
    )

    result = _sector_rotation_item(item, rank=3, limit_counts={"半导体": 2})

    assert result.flow_speed == -1.25
    assert result.flow_acceleration == -0.08
    assert result.flow_turning == "TURN_TO_OUTFLOW"
    assert "诱多" in (result.flow_signal or "")
    assert result.flow_as_of == "2026-07-15 10:05:00"


def test_intraday_sell_triggers_add_turn_out_and_recovery_conditions():
    holding = SimpleNamespace(cost_price=10.0)
    risk = _intraday_sell_triggers(
        holding=holding,
        current=10.5,
        high=11.0,
        high_change_pct=10.0,
        change_pct=5.0,
        pullback=5.0,
        below_vwap=True,
        sector="半导体",
        sector_rank=5,
        sector_net=-3.0,
        sector_main=-2.0,
        sector_acc=-1.5,
        sector_flow_speed=-0.8,
        sector_flow_acceleration=-0.05,
        sector_flow_turning="TURN_TO_OUTFLOW",
        sector_flow_signal="资金由净流入拐为净流出",
    )
    recovery = _intraday_sell_triggers(
        holding=holding,
        current=9.6,
        high=10.0,
        high_change_pct=0.0,
        change_pct=-4.0,
        pullback=4.0,
        below_vwap=True,
        sector="半导体",
        sector_rank=8,
        sector_net=1.0,
        sector_main=0.5,
        sector_acc=2.0,
        sector_flow_speed=0.9,
        sector_flow_acceleration=0.04,
        sector_flow_turning="TURN_TO_INFLOW",
        sector_flow_signal="资金由净流出拐为净流入",
    )

    assert any("由净流入拐为净流出" in item for item in risk["sector_ebb_trigger"])
    assert any("流速-0.800亿/分钟" in item for item in risk["sector_ebb_trigger"])
    assert "板块退潮" in risk["trigger_action"]
    assert "资金由净流出拐为净流入" in recovery["buyback_trigger"][0]
    assert "仍需个股站回真实VWAP" in recovery["buyback_trigger"][0]


def test_dedupe_event_overwrites_latest_value_time_and_evidence(db_session):
    old_time = datetime.now() - timedelta(minutes=2)
    existing = IntradayEvidenceEvent(
        trade_date=_trade_date(),
        captured_at=old_time,
        scope="sector",
        target_code="600584",
        target_name="半导体",
        event_type="SECTOR_FLOW_WEAKENING",
        severity="warning",
        value=-1.0,
        previous_value=0.0,
        priority=70,
        group_key="sector:flow-direction:weak",
        first_seen_at=old_time,
        last_seen_at=old_time,
        occurrence_count=1,
        confirmed=False,
        evidence_json='["旧资金值"]',
    )
    db_session.add(existing)
    db_session.commit()
    observed_at = datetime.now()

    created = _dedupe_events(db_session, [{
        "captured_at": observed_at,
        "scope": "sector",
        "target_code": "600584",
        "target_name": "半导体",
        "event_type": "SECTOR_FLOW_WEAKENING",
        "severity": "critical",
        "value": -8.5,
        "previous_value": 0.0,
        "priority": 82,
        "group_key": "sector:flow-direction:weak",
        "evidence": ["截至10:05，资金流出正在加速。"],
    }])

    assert created == []
    assert existing.captured_at == observed_at
    assert existing.last_seen_at == observed_at
    assert existing.value == -8.5
    assert existing.priority == 82
    assert existing.severity == "critical"
    assert existing.occurrence_count == 2
    assert existing.confirmed is True
    assert json.loads(existing.evidence_json) == ["截至10:05，资金流出正在加速。"]
