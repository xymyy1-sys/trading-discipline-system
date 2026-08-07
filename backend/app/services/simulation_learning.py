from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.trading import (
    DataCaptureSnapshot,
    SimulationAccount,
    SimulationEvidenceSnapshot,
    SimulationFill,
    SimulationOrder,
    VolumePriceSnapshot,
)
from app.services.simulation_calibration import simulation_calibration_proposal


SAMPLE_TYPE = "ai_trade_learning"
DAILY_TYPE = "ai_daily_learning"


def _json(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _upsert_snapshot(
    db: Session,
    *,
    trade_date: str,
    data_type: str,
    target_code: str,
    target_name: str,
    payload: dict[str, Any],
    captured_at: datetime,
) -> DataCaptureSnapshot:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    row = db.query(DataCaptureSnapshot).filter(
        DataCaptureSnapshot.trade_date == trade_date,
        DataCaptureSnapshot.data_type == data_type,
        DataCaptureSnapshot.target_code == target_code,
    ).order_by(DataCaptureSnapshot.id.desc()).first()
    if row is None:
        row = DataCaptureSnapshot(
            trade_date=trade_date,
            source="simulation-forward-learning",
            data_type=data_type,
            target_code=target_code,
            target_name=target_name[:64],
        )
    row.captured_at = captured_at
    row.raw_value_json = encoded
    row.normalized_value_json = encoded
    row.quality = "complete"
    row.is_complete = True
    row.is_stale = False
    row.is_degraded = False
    row.status = "evaluated"
    row.error_message = ""
    row.raw_payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    db.add(row)
    db.flush()
    return row


def build_ai_trader_daily_learning(
    db: Session,
    account: SimulationAccount,
    trade_date: str,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Turn every fill into a numeric, forward-only learning sample.

    BUY samples preserve MFE/MAE and entry-to-close outcome.  SELL samples
    preserve the counterfactual close and rebound after exit.  These rows are
    the machine-readable feedback layer used by later calibration; the daily
    text is only a view of this data, never the learning record itself.
    """
    fills = db.query(SimulationFill).filter(
        SimulationFill.account_id == account.id,
        SimulationFill.trade_date == trade_date,
    ).order_by(SimulationFill.filled_at.asc(), SimulationFill.id.asc()).all()
    samples: list[dict[str, Any]] = []
    corrections: list[str] = []
    for fill in fills:
        rows = db.query(VolumePriceSnapshot).filter(
            VolumePriceSnapshot.trade_date == trade_date,
            VolumePriceSnapshot.code == fill.code,
            VolumePriceSnapshot.captured_at >= fill.filled_at,
            VolumePriceSnapshot.price > 0,
        ).order_by(VolumePriceSnapshot.captured_at.asc(), VolumePriceSnapshot.id.asc()).all()
        if not rows:
            samples.append({
                "fill_id": fill.id,
                "code": fill.code,
                "name": fill.name,
                "side": fill.side,
                "status": "data_gap",
                "reason": "成交后缺少同日量价快照，暂不进入参数校准",
            })
            continue
        prices = [float(row.price or 0) for row in rows if float(row.price or 0) > 0]
        close_price = prices[-1]
        maximum = max(prices)
        minimum = min(prices)
        basis = float(fill.price or 0)
        close_return = (close_price / basis - 1) * 100 if basis > 0 else 0.0
        max_up = (maximum / basis - 1) * 100 if basis > 0 else 0.0
        max_down = (minimum / basis - 1) * 100 if basis > 0 else 0.0
        order = db.get(SimulationOrder, fill.order_id)
        evidence = db.get(
            SimulationEvidenceSnapshot,
            int(getattr(order, "decision_evidence_snapshot_id", 0) or 0),
        ) if order else None
        volume_at_decision = _json(getattr(evidence, "volume_price_json", "{}"))
        tags: list[str] = []
        if fill.side == "BUY":
            if close_return <= -2.0 and max_up < 1.5:
                tags.append("买入后未形成有效上攻")
                corrections.append("收紧买点：必须经过回踩承接或放量突破的二次确认，短时站回VWAP不再直接买入。")
            if float(volume_at_decision.get("volume_acceleration") or 0) < -35:
                tags.append("买入时量能明显衰减")
                corrections.append("屏蔽量能加速度低于-35%的追强信号。")
            if float(volume_at_decision.get("price_vs_vwap") or 0) > 2:
                tags.append("买入偏离VWAP过远")
                corrections.append("非涨停策略价格高于VWAP 2%时禁止追入，等待回踩。")
        else:
            if close_return >= 1.5 or max_up >= 2.5:
                tags.append("卖出后出现明显修复")
                corrections.append("优化卖点：开盘负预期先等待观察窗及VWAP修复验证，避免单次低开触发连续卖出。")
            elif close_return <= -1.5:
                tags.append("卖出后继续走弱")
            else:
                tags.append("卖点结果中性")
        sample = {
            "fill_id": fill.id,
            "order_id": fill.order_id,
            "trade_date": trade_date,
            "code": fill.code,
            "name": fill.name,
            "side": fill.side,
            "quantity": fill.quantity,
            "fill_price": round(basis, 4),
            "fill_time": fill.filled_at.isoformat(),
            "close_price": round(close_price, 4),
            "return_to_close_pct": round(close_return, 4),
            "max_favorable_pct": round(max_up if fill.side == "BUY" else -max_down, 4),
            "max_adverse_pct": round(max_down if fill.side == "BUY" else -max_up, 4),
            "counterfactual_no_action_close_pct": round(close_return, 4) if fill.side == "SELL" else None,
            "decision_volume_price": {
                key: volume_at_decision.get(key)
                for key in ("pattern", "price_vs_vwap", "volume_acceleration", "active_buy_amount", "active_sell_amount")
            },
            "diagnostic_tags": tags,
            "status": "evaluated",
        }
        _upsert_snapshot(
            db,
            trade_date=trade_date,
            data_type=SAMPLE_TYPE,
            target_code=f"fill:{fill.id}",
            target_name=f"{fill.name}{fill.side}",
            payload=sample,
            captured_at=now,
        )
        samples.append(sample)

    calibration = simulation_calibration_proposal(db, account)
    summary = {
        "trade_date": trade_date,
        "account_id": account.id,
        "fill_sample_count": len(samples),
        "evaluated_sample_count": sum(item.get("status") == "evaluated" for item in samples),
        "corrections": list(dict.fromkeys(corrections)),
        "samples": samples,
        "formal_closed_sample_count": int((calibration.get("overall") or {}).get("sample_count") or 0),
        "minimum_formal_samples": 30,
        "calibration_candidates": calibration.get("candidates") or [],
        "rule_application": "每日生成诊断；规则只有达到样本门槛并通过前向对照后才升级，AI文字不能直接改交易参数。",
    }
    _upsert_snapshot(
        db,
        trade_date=trade_date,
        data_type=DAILY_TYPE,
        target_code=f"account:{account.id}",
        target_name="AI模拟交易员每日学习",
        payload=summary,
        captured_at=now,
    )
    db.commit()
    return summary
