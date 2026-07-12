from __future__ import annotations

import json
import hashlib
import time as time_module
from datetime import datetime, time
from typing import Any

from sqlalchemy.orm import Session

from app.api.helpers.decision import current_expectation_stage, quote_for_code
from app.api.helpers.execution import build_position_execution_state
from app.api.helpers.quotes import _safe_float
from app.api.helpers.volume_price import build_volume_price_snapshot
from app.models.trading import DataCaptureSnapshot, Holding, IntradayEvidenceEvent
from app.schemas.trading import PositionExecutionStateOut, VolumePriceSnapshotOut


SCHEDULED_SAMPLE_TIMES = [
    time(9, 15),
    time(9, 20),
    time(9, 25),
    time(9, 30),
    time(9, 35),
    time(9, 40),
    time(9, 45),
    time(10, 0),
    time(10, 30),
    time(11, 30),
    time(13, 0),
    time(13, 30),
    time(14, 0),
    time(14, 30),
    time(14, 50),
    time(15, 0),
]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _trade_date(now: datetime | None = None) -> str:
    return (now or datetime.now()).date().isoformat()


def nearest_sample_label(now: datetime | None = None) -> str:
    now = now or datetime.now()
    current = now.time()
    nearest = min(
        SCHEDULED_SAMPLE_TIMES,
        key=lambda sample: abs(
            (sample.hour * 60 + sample.minute) - (current.hour * 60 + current.minute)
        ),
    )
    return nearest.strftime("%H:%M")


def _sample_evidence(
    quote: dict[str, Any],
    volume: VolumePriceSnapshotOut,
    state: PositionExecutionStateOut,
    sample_label: str,
) -> list[str]:
    price = _safe_float(quote.get("price")) or volume.price
    volume_value = _safe_float(quote.get("volume")) or volume.volume
    sector_state = state.sector_state or "板块证据缺口"
    return [
        f"采样点 {sample_label}，价格 {price:.2f}，成交量 {volume_value:.0f}。",
        f"VWAP {volume.vwap:.2f}，来源 {volume.vwap_source}，可靠={volume.vwap_reliable}。",
        f"预期状态 {state.expectation_state}，量价状态 {state.volume_price_state}，板块状态 {sector_state}。",
        f"当前动作建议：{state.recommended_action}。",
    ]


def save_intraday_sample_event(
    db: Session,
    holding: Holding,
    quote: dict[str, Any],
    volume: VolumePriceSnapshotOut,
    state: PositionExecutionStateOut,
    now: datetime | None = None,
) -> IntradayEvidenceEvent:
    now = now or datetime.now()
    sample_label = nearest_sample_label(now)
    row = IntradayEvidenceEvent(
        trade_date=_trade_date(now),
        captured_at=now,
        scope="stock",
        target_code=holding.code,
        target_name=holding.name,
        event_type="INTRADAY_EVIDENCE_SNAPSHOT",
        severity="info",
        value=round(_safe_float(quote.get("price")) or volume.price, 2),
        previous_value=round(volume.vwap or 0, 2),
        priority=10,
        group_key=f"stock:sample:{sample_label}",
        first_seen_at=now,
        last_seen_at=now,
        occurrence_count=1,
        confirmed=True,
        evidence_json=_json_dumps(_sample_evidence(quote, volume, state, sample_label)),
        recommendation_id=state.recommendation.id if state.recommendation else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def collect_holding_evidence(
    db: Session,
    holding: Holding,
    stage: str | None = None,
    now: datetime | None = None,
) -> tuple[VolumePriceSnapshotOut, PositionExecutionStateOut, IntradayEvidenceEvent]:
    now = now or datetime.now()
    fetch_started = time_module.perf_counter()
    quote = quote_for_code(holding.code)
    latency_ms = int((time_module.perf_counter() - fetch_started) * 1000)
    stage = stage or current_expectation_stage(now)
    volume = build_volume_price_snapshot(
        db,
        holding.code,
        name=holding.name,
        stage=stage,
        quote=quote,
    )
    state = build_position_execution_state(db, holding, quote=quote, volume_price=volume)
    raw_json = json.dumps(quote, ensure_ascii=False, sort_keys=True, default=str)
    normalized_json = json.dumps({"price": volume.price, "vwap": volume.vwap, "pattern": volume.pattern, "data_quality": volume.data_quality}, ensure_ascii=False, sort_keys=True)
    minute_status = str(quote.get("minute_bar_status") or "missing")
    capture = DataCaptureSnapshot(
        trade_date=_trade_date(now), captured_at=now, source=str(quote.get("minute_bar_source") or quote.get("note") or "unknown"),
        data_type="stock_minute", target_code=holding.code, target_name=holding.name,
        raw_value_json=raw_json, normalized_value_json=normalized_json, quality=volume.data_quality,
        latency_ms=latency_ms, is_stale=bool(quote.get("minute_bar_trade_date") and quote.get("minute_bar_trade_date") != _trade_date(now)),
        is_degraded=not volume.vwap_reliable, is_estimated=volume.vwap_source in {"quote_estimated", "range_estimated", "estimated"},
        is_complete=volume.vwap_reliable and volume.minute_bar_count >= 3, status=minute_status,
        error_message=str(quote.get("minute_fetch_error") or ""), raw_payload_hash=hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
    )
    db.add(capture)
    db.commit()
    sample = save_intraday_sample_event(db, holding, quote, volume, state, now=now)
    return volume, state, sample
