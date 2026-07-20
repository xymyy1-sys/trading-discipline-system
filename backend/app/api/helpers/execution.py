from __future__ import annotations

import json
import hashlib
import math
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.api.helpers.quotes import (
    _QUOTE_META_CACHE,
    _estimated_vwap,
    _is_realtime_note,
    _latest_a_share_quotes,
    _normalize_code,
    _quote_lookup_code,
    _safe_float,
)
from app.api.helpers.seesaw import _market_seesaw_monitor
from app.models.trading import (
    AccountState,
    ActionRecommendation,
    ActionRecommendationRevision,
    ExpectationSnapshot,
    ExitCard,
    Holding,
    IntradayEvidenceEvent,
    MarketRegimeSnapshot,
    NextDayPlan,
    PositionExecutionState,
    PositionStateHistory,
    ProfitProtectionSnapshot,
    RecommendationFeedback,
    TradeLog,
    TimeStopRule,
    VolumePriceSnapshot,
)
from app.schemas.trading import (
    ActionRecommendationOut,
    ExpectationSnapshotOut,
    HoldingExecutionSignalOut,
    IntradayEvidenceEventOut,
    PositionExecutionStateOut,
    PositionStateHistoryOut,
    ProfitProtectionSnapshotOut,
    VolumePriceSnapshotOut,
)
from app.services.flow_kinetics import FlowKinetics, classify_price_volume_flow_alerts
from app.services.global_market import global_market_service
from app.services.cache import _get_response_cache

WEAK_EXPECTATION_RESULTS = {"WEAKER", "INVALID", "SLIGHTLY_WEAKER"}
STRONG_EXPECTATION_RESULTS = {"STRONGER", "SLIGHTLY_STRONGER"}
EXECUTION_RULE_VERSION = "execution-v2"


DEFAULT_TIME_STOP_RULES: dict[str, dict[str, Any]] = {
    "default": {
        "display_name": "默认剧本",
        "confirmation_deadline": "10:00",
        "below_vwap_minutes": 5,
        "below_vwap_min_bars": 5,
        "recent_window_minutes": 15,
        "failed_limit_reseal_pct": 0.985,
        "enabled": True,
    },
    "breakout": {
        "display_name": "打板/冲板",
        "confirmation_deadline": "09:45",
        "below_vwap_minutes": 3,
        "below_vwap_min_bars": 3,
        "recent_window_minutes": 10,
        "failed_limit_reseal_pct": 0.99,
        "enabled": True,
    },
    "trend": {
        "display_name": "趋势/容量",
        "confirmation_deadline": "10:30",
        "below_vwap_minutes": 8,
        "below_vwap_min_bars": 6,
        "recent_window_minutes": 20,
        "failed_limit_reseal_pct": 0.985,
        "enabled": True,
    },
}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _recommendation_target_key(holding: Holding) -> str:
    return f"holding:{int(holding.id)}" if holding.id is not None else f"code:{_normalize_code(holding.code)}"


def _material_decision_hash(
    *,
    level: str,
    state: str,
    action: str,
    recommended_ratio: float,
    trigger_events: list[str],
    invalid_conditions: list[str],
    recovery_conditions: list[str],
) -> str:
    """Hash only material decision semantics, never minute-by-minute prose.

    Evidence text contains current prices and therefore changes on virtually
    every sample.  Including it in the identity would recreate the old
    recommendation-inflation problem.  Trigger classes, action size and the
    conditions that invalidate/recover a decision are the stable semantics.
    """

    payload = {
        "level": str(level or "INFO"),
        "state": str(state or ""),
        "action": str(action or ""),
        "recommended_ratio": round(float(recommended_ratio or 0), 4),
        "trigger_events": sorted({str(item) for item in trigger_events if str(item)}),
        "invalid_conditions": sorted({str(item) for item in invalid_conditions if str(item)}),
        "recovery_conditions": sorted({str(item) for item in recovery_conditions if str(item)}),
        "rule_version": EXECUTION_RULE_VERSION,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _sector_name_key(value: Any) -> str:
    return re.sub(r"[\s·・/\\（）()]+", "", str(value or "").strip()).lower()


def _cached_sector_distribution_context(seesaw: Any | None) -> dict[str, Any]:
    """Match a holding's sector to the latest persisted-in-cache risk snapshot.

    This helper is intentionally read-only.  A holding calculation must never
    trigger an external board-flow or margin request merely because a page was
    opened.
    """

    if seesaw is None:
        return {}
    candidates = [
        getattr(seesaw, "primary_industry_sector", ""),
        getattr(seesaw, "matched_flow_sector", ""),
        getattr(seesaw, "holding_theme", ""),
        getattr(seesaw, "sector", ""),
        *list(getattr(seesaw, "concept_flow_sectors", []) or []),
    ]
    candidate_keys = {_sector_name_key(item) for item in candidates if _sector_name_key(item)}
    if not candidate_keys:
        return {}
    matches: list[dict[str, Any]] = []
    for board_type in ("行业", "概念"):
        cached = _get_response_cache(f"sector-temperature|{board_type}", allow_stale=True)
        items = cached.get("items") if isinstance(cached, dict) else getattr(cached, "items", None)
        for item in items or []:
            data = item.model_dump() if hasattr(item, "model_dump") else item
            if not isinstance(data, dict):
                continue
            item_key = _sector_name_key(data.get("name"))
            if not item_key:
                continue
            exact = item_key in candidate_keys
            related = any(
                len(key) >= 2 and (key in item_key or item_key in key)
                for key in candidate_keys
            )
            if exact or related:
                matches.append({**data, "board_type": board_type, "_match_exact": exact})
    if not matches:
        return {}
    return max(
        matches,
        key=lambda item: (
            bool(item.get("_match_exact")),
            int(item.get("distribution_risk_score") or 0),
            int(item.get("heat_score") or 0),
        ),
    )


def _shanghai_now_naive() -> datetime:
    """Return the current Shanghai wall clock as a timezone-naive value.

    Trading stages and deadlines are defined in China Standard Time.  Using the
    host's local timezone made the same evidence produce different actions on
    UTC-configured servers and developer machines.
    """
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _trade_date() -> str:
    return _shanghai_now_naive().date().isoformat()


def _utc_now_naive() -> datetime:
    """Return the UTC clock value used by timezone-naive database columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_storage_bounds_for_local_trade_day() -> tuple[datetime, datetime]:
    """Return naive UTC bounds for timestamps persisted with UTC model defaults."""
    shanghai_date = _shanghai_now_naive().date()
    local_start = datetime.combine(shanghai_date, time.min)
    local_end = datetime.combine(shanghai_date, time.max)
    china_offset = timedelta(hours=8)
    return local_start - china_offset, local_end - china_offset


def _shanghai_naive_to_utc_naive(value: datetime) -> datetime:
    """Convert a persisted Shanghai wall-clock value to UTC-naive storage.

    ``PositionExecutionState.updated_at`` and recommendation revisions are
    intentionally recorded as China-market wall time, while profit snapshots
    use UTC-naive storage.  Comparing the two values directly can select a
    snapshot created up to eight hours *after* the state being rendered.
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value - timedelta(hours=8)


def _latest_market_regime(db: Session) -> MarketRegimeSnapshot | None:
    """Return today's latest persisted full-market state without triggering I/O.

    Execution decisions must stay deterministic and fast.  Market collection is
    handled elsewhere; this helper only consumes the latest evidence snapshot.
    """
    return (
        db.query(MarketRegimeSnapshot)
        .filter(MarketRegimeSnapshot.trade_date == _trade_date())
        .order_by(MarketRegimeSnapshot.captured_at.desc(), MarketRegimeSnapshot.id.desc())
        .first()
    )


def _market_regime_gate(
    db: Session,
    now: datetime,
) -> tuple[MarketRegimeSnapshot | None, bool, bool, int]:
    """Describe the global expansion gate.

    The boolean values are ``freeze_expansion`` and ``data_limited``.  A weak
    market can forbid adding/bottom-fishing, but it is deliberately *not* fed
    into the holding sell score: direction risk and execution price are two
    different decisions.
    """
    row = _latest_market_regime(db)
    if row is None:
        return None, False, True, 0
    captured_at = row.captured_at
    if captured_at.tzinfo is not None:
        captured_at = captured_at.replace(tzinfo=None)
    age_minutes = max(0, int((now - captured_at).total_seconds() // 60))
    during_market = now.weekday() < 5 and time(9, 15) <= now.time() <= time(15, 0)
    stale = during_market and age_minutes > 10
    data_limited = bool(
        stale
        or str(row.data_quality or "").lower() in {"missing", "unavailable", "degraded", "partial"}
        or float(row.coverage_ratio or 0) < 0.90
        or str(row.regime_code or "").upper() == "UNKNOWN"
        or _json_list(row.missing_fields_json)
    )
    freeze_expansion = bool(
        str(row.risk_level or "") in {"极高", "高"}
        or str(row.regime_code or "") in {"EXTREME_SHRINK_DECLINE", "VOLUME_SELL_OFF"}
        or str(row.regime_code or "").upper() == "UNKNOWN"
        or stale
    )
    return row, freeze_expansion, data_limited, age_minutes


def _global_value(item: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def _global_evidence_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    return parsed


def _valid_global_quote(
    item: Mapping[str, Any] | Any,
    *,
    now: datetime,
    max_age: timedelta,
) -> tuple[float, str] | None:
    status = str(_global_value(item, "status") or "").lower()
    source = str(_global_value(item, "source") or "").lower()
    if status not in {"ok", "delayed"} or any(
        marker in source for marker in ("mock", "manual", "simulat")
    ):
        return None
    raw_change = _global_value(item, "change_pct")
    if raw_change is None or isinstance(raw_change, bool):
        return None
    try:
        change_pct = float(raw_change)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(change_pct):
        return None
    captured_at = _global_evidence_time(_global_value(item, "as_of"))
    if change_pct is None or captured_at is None:
        return None
    if captured_at > now + timedelta(minutes=5) or now - captured_at > max_age:
        return None
    return change_pct, str(_global_value(item, "name") or _global_value(item, "symbol") or "外围标的")


def global_market_execution_gate(
    snapshot: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Score fresh overseas evidence without turning it into a sell trigger.

    The output can freeze *new* exposure when several independent overseas
    observations are materially weak.  It is intentionally absent from the
    holding risk-family score and therefore can never, on its own, create a
    reduction or liquidation instruction.
    """

    current = now or _shanghai_now_naive()
    if current.tzinfo is not None:
        current = current.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    base: dict[str, Any] = {
        "score": 0,
        "freeze_expansion": False,
        "data_limited": True,
        "valid_quote_count": 0,
        "evidence": [],
        "counter_evidence": [],
    }
    if not isinstance(snapshot, Mapping):
        base["evidence"] = ["外围市场证据缺口：不加分、不扣分，也不据此生成卖出结论。"]
        return base
    quality = str(snapshot.get("data_quality") or snapshot.get("quality") or "").lower()
    if quality not in {"ok", "complete", "realtime", "degraded"}:
        base["evidence"] = ["外围市场数据质量不足：不加分、不扣分，也不据此生成卖出结论。"]
        return base

    def valid_group(key: str, max_age: timedelta) -> list[tuple[float, str]]:
        values: list[tuple[float, str]] = []
        for item in list(snapshot.get(key) or []):
            normalized = _valid_global_quote(item, now=current, max_age=max_age)
            if normalized is not None:
                values.append(normalized)
        return values

    # US quotes are an overnight close context and tolerate weekends/holidays;
    # Korean quotes participate in the same Asian session and must be fresher.
    us = valid_group("us_indices", timedelta(hours=96))
    korea_age = timedelta(minutes=90) if current.weekday() < 5 and time(9, 15) <= current.time() <= time(15, 0) else timedelta(hours=30)
    korea = valid_group("korea_indices", korea_age)
    korea_equities = valid_group("korea_equities", korea_age)
    us_sectors = valid_group("us_sector_rank", timedelta(hours=96))
    base["valid_quote_count"] = len(us) + len(korea) + len(korea_equities) + len(us_sectors)

    score = 0
    evidence: list[str] = []
    counter: list[str] = []

    def broad_score(label: str, values: list[tuple[float, str]]) -> int:
        if len(values) < 2:
            return 0
        changes = [value for value, _ in values]
        average = sum(changes) / len(changes)
        negative_ratio = sum(value < 0 for value in changes) / len(changes)
        positive_ratio = sum(value > 0 for value in changes) / len(changes)
        if average <= -1.25 and negative_ratio >= 2 / 3:
            evidence.append(f"{label}广度偏弱：有效样本{len(changes)}个，平均{average:+.2f}%，仅冻结新增风险。")
            return -2
        if average >= 1.25 and positive_ratio >= 2 / 3:
            counter.append(f"{label}广度偏强：有效样本{len(changes)}个，平均{average:+.2f}%，作为扩仓环境加分但不替代A股确认。")
            return 2
        counter.append(f"{label}方向未形成一致极端：有效样本{len(changes)}个，平均{average:+.2f}%。")
        return 0

    score += broad_score("隔夜美股主要指数", us)
    score += broad_score("韩国主要指数", korea)

    semiconductor_symbols = {"SOX", "SMH", "SOXX", "005930", "000660"}
    semiconductor: list[tuple[float, str]] = []
    for key, values in (
        ("us_indices", us),
        ("us_sector_rank", us_sectors),
        ("korea_equities", korea_equities),
    ):
        rows = list(snapshot.get(key) or [])
        by_name = {name: value for value, name in values}
        for item in rows:
            symbol = str(_global_value(item, "symbol") or "").upper()
            name = str(_global_value(item, "name") or symbol)
            if symbol in semiconductor_symbols and name in by_name:
                semiconductor.append((by_name[name], name))
    if len(semiconductor) >= 2:
        average = sum(value for value, _ in semiconductor) / len(semiconductor)
        consistency = sum(value < 0 for value, _ in semiconductor) / len(semiconductor)
        if average <= -2 and consistency >= 2 / 3:
            score -= 1
            evidence.append(f"海外半导体代理共振偏弱：有效样本{len(semiconductor)}个，平均{average:+.2f}%。")
        elif average >= 2 and sum(value > 0 for value, _ in semiconductor) / len(semiconductor) >= 2 / 3:
            score += 1
            counter.append(f"海外半导体代理共振偏强：有效样本{len(semiconductor)}个，平均{average:+.2f}%。")

    score = max(-3, min(3, score))
    base.update({
        "score": score,
        "freeze_expansion": score <= -2,
        "data_limited": not (len(us) >= 2 or len(korea) >= 2),
        "evidence": evidence,
        "counter_evidence": counter,
    })
    if base["valid_quote_count"] == 0:
        base["evidence"] = ["外围行情均缺少有效时间戳、已过期或不可用：本次不参与执行门控。"]
    return base


def _load_global_market_snapshot(*, force_refresh: bool = False) -> Mapping[str, Any] | None:
    try:
        return global_market_service.snapshot(force_refresh=force_refresh)
    except Exception:
        return None


def _quote_for_holding(holding: Holding, quotes: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    quotes = quotes or {}
    lookup_code = _quote_lookup_code(holding.code, quotes)
    quote = quotes.get(lookup_code)
    if quote:
        return quote
    return _QUOTE_META_CACHE.get(str(holding.code)) or _QUOTE_META_CACHE.get(_normalize_code(holding.code)) or {}


def _latest_quotes(holdings: list[Holding]) -> dict[str, dict[str, Any]]:
    try:
        return _latest_a_share_quotes([holding.code for holding in holdings if holding.code])
    except Exception:
        return {}


def _latest_profit_snapshot(db: Session, holding_id: int) -> ProfitProtectionSnapshot | None:
    day_start, day_end = _utc_storage_bounds_for_local_trade_day()
    return (
        db.query(ProfitProtectionSnapshot)
        .filter(
            ProfitProtectionSnapshot.holding_id == holding_id,
            ProfitProtectionSnapshot.captured_at >= day_start,
            ProfitProtectionSnapshot.captured_at <= day_end,
        )
        .order_by(ProfitProtectionSnapshot.captured_at.desc(), ProfitProtectionSnapshot.id.desc())
        .first()
    )


def _latest_expectation_snapshot(db: Session, code: str) -> ExpectationSnapshot | None:
    normalized = _normalize_code(code)
    candidates = {code, normalized, normalized.lstrip("0")}
    return (
        db.query(ExpectationSnapshot)
        .filter(
            ExpectationSnapshot.code.in_(list(candidates)),
            ExpectationSnapshot.trade_date == _trade_date(),
        )
        .order_by(ExpectationSnapshot.created_at.desc(), ExpectationSnapshot.id.desc())
        .first()
    )


def _latest_volume_price_snapshot(db: Session, code: str) -> VolumePriceSnapshot | None:
    normalized = _normalize_code(code)
    candidates = {code, normalized, normalized.lstrip("0")}
    return (
        db.query(VolumePriceSnapshot)
        .filter(
            VolumePriceSnapshot.code.in_(list(candidates)),
            VolumePriceSnapshot.trade_date == _trade_date(),
        )
        .order_by(VolumePriceSnapshot.captured_at.desc(), VolumePriceSnapshot.id.desc())
        .first()
    )


def _expectation_result(row: ExpectationSnapshot | ExpectationSnapshotOut | None) -> str:
    if row is None:
        return ""
    return str(getattr(row, "expectation_result", "") or "")


def _expectation_score(row: ExpectationSnapshot | ExpectationSnapshotOut | None) -> int:
    if row is None:
        return 0
    try:
        return int(getattr(row, "expectation_gap_score", 0) or 0)
    except Exception:
        return 0


def _volume_pattern(row: VolumePriceSnapshot | VolumePriceSnapshotOut | None) -> str:
    if row is None:
        return ""
    return str(getattr(row, "pattern", "") or "")


def _volume_vwap_reliable(row: VolumePriceSnapshot | VolumePriceSnapshotOut | None, quote: dict[str, Any]) -> bool:
    if row is not None:
        return bool(getattr(row, "vwap_reliable", False))
    minute_rows = quote.get("minute_bars") or quote.get("minutes") or []
    return isinstance(minute_rows, list) and len(minute_rows) >= 3


def _volume_price_state(pattern: str, current: float, vwap: float, high_drawdown_pct: float) -> str:
    if any(label in pattern for label in ("跌停开板V形修复", "深水开板V形修复", "水下V形反转站回VWAP", "水下V形修复站回VWAP", "重新站回VWAP且低点抬高")):
        return "REVERSAL_CONFIRMED"
    if "深水V形反抽待确认" in pattern:
        return "REVERSAL_PENDING"
    if "冲高回落跌破VWAP" in pattern:
        return "VOLUME_PRICE_WEAKENING"
    if "跌破VWAP" in pattern or (vwap and current < vwap):
        return "VWAP_BREAKDOWN"
    if "冲高回落" in pattern or high_drawdown_pct >= 4:
        return "HIGH_DRAWDOWN"
    if "VWAP上方强势" in pattern:
        return "REPAIR_CONFIRMED"
    if vwap and current >= vwap:
        return "VWAP_STRONG"
    return "VOLUME_PRICE_NEUTRAL"


def _position_quantities(db: Session, holding: Holding, trade_date: str) -> tuple[int, int, int]:
    normalized = _normalize_code(holding.code)
    candidates = {holding.code, normalized, normalized.lstrip("0")}
    shanghai_date = _shanghai_now_naive().date()
    day_start = datetime.combine(shanghai_date, time.min)
    day_end = datetime.combine(shanghai_date, time.max)
    rows = (
        db.query(TradeLog)
        .filter(TradeLog.code.in_(list(candidates)), TradeLog.traded_at >= day_start, TradeLog.traded_at <= day_end)
        .all()
    )
    buy_sides = {"买入", "加仓", "做T买回", "T买回"}
    today_buy = sum(int(row.quantity or 0) for row in rows if row.side in buy_sides)
    current_quantity = int(holding.quantity or 0)
    sellable = max(0, current_quantity - today_buy)
    return sellable, today_buy, sellable


def _protection_level(max_profit_pct: float) -> tuple[str, float]:
    if max_profit_pct >= 10:
        return "LEVEL_4", 0.30
    if max_profit_pct >= 8:
        return "LEVEL_3", 0.35
    if max_profit_pct >= 5:
        return "LEVEL_2", 0.40
    if max_profit_pct >= 3:
        return "LEVEL_1", 0.60
    return "NONE", 1.0


def _script_hard_stop_ratio(position_type: str) -> float:
    text = position_type or ""
    if "趋势" in text or "容量" in text:
        return 0.92
    if "低吸" in text:
        return 0.95
    return 0.96


def _stop_source_label(source: str) -> str:
    labels = {
        "next_day_plan": "次日计划",
        "sell_card": "卖出卡",
        "text_script": "交易剧本文本",
        "frozen_state": "当日冻结执行状态",
        "cost_reference": "持仓成本防守参考",
        "fallback_candidate": "候选价兜底",
    }
    parts = [labels.get(part, part) for part in str(source or "").split("+") if part]
    return " + ".join(parts) or labels["fallback_candidate"]


def _script_stop_levels(holding: Holding, current_stop: float, current_hard_stop: float) -> tuple[float, float, list[str], list[str]]:
    text = f"{holding.position_type or ''} {holding.next_discipline or ''}"
    evidence: list[str] = []
    sources: list[str] = []
    # A cost-derived percentage is only a risk reference.  It must not silently
    # become an intraday hard stop; hard stops require an explicit frozen source.
    hard_stop = current_hard_stop
    structure_stop = current_stop
    price_patterns = [
        (r"(?:结构止损|结构位|失败位|防守位|跌破|破位|止损)\D{0,8}(\d+(?:\.\d+)?)", "structure"),
        (r"(?:硬止损|最终止损|绝对止损)\D{0,8}(\d+(?:\.\d+)?)", "hard"),
    ]
    for pattern, target in price_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = float(match.group(1))
        if value <= 0:
            continue
        if target == "hard":
            hard_stop = round(value, 2)
            evidence.append(f"按交易剧本解析硬止损 {hard_stop:.2f}。")
            sources.append("text_script")
        else:
            structure_stop = round(value, 2)
            evidence.append(f"按交易剧本解析结构止损 {structure_stop:.2f}。")
            sources.append("text_script")

    pct_match = re.search(r"(?:止损|破位|亏损)\D{0,8}(\d+(?:\.\d+)?)\s*%", text)
    if pct_match and holding.cost_price:
        pct_stop = round(holding.cost_price * (1 - float(pct_match.group(1)) / 100), 2)
        if not evidence:
            evidence.append(f"按交易剧本解析百分比止损 {pct_stop:.2f}。")
        sources.append("text_script")
        structure_stop = max(structure_stop, pct_stop) if structure_stop else pct_stop

    return structure_stop, hard_stop, evidence, sources


def _structured_stop_levels(db: Session, holding: Holding, current_stop: float, hard_stop: float) -> tuple[float, float, list[str], list[str]]:
    normalized = _normalize_code(holding.code)
    candidates = {holding.code, normalized, normalized.lstrip("0")}
    evidence: list[str] = []
    sources: list[str] = []
    structure_stop = current_stop
    hard_stop_price = hard_stop
    frozen_state = (
        db.query(PositionExecutionState)
        .filter(
            PositionExecutionState.holding_id == int(holding.id or 0),
            PositionExecutionState.trade_date == _trade_date(),
        )
        .order_by(PositionExecutionState.updated_at.desc(), PositionExecutionState.id.desc())
        .first()
    )
    if frozen_state and str(frozen_state.stop_source or "") not in {"", "fallback_candidate", "cost_reference"}:
        structure_stop = float(frozen_state.structure_stop_price or 0) or structure_stop
        hard_stop_price = float(frozen_state.hard_stop_price or 0) or hard_stop_price
        evidence.append("沿用当日首次生成的执行止损，不随盘中最低价、VWAP或开盘价漂移。")
        sources.extend([part for part in str(frozen_state.stop_source or "").split("+") if part])
        sources.append("frozen_state")
        return structure_stop, hard_stop_price, evidence, list(dict.fromkeys(sources))

    plan = (
        db.query(NextDayPlan)
        .filter(
            NextDayPlan.code.in_(list(candidates)),
            NextDayPlan.plan_date == _trade_date(),
        )
        .order_by(NextDayPlan.plan_date.desc(), NextDayPlan.updated_at.desc(), NextDayPlan.id.desc())
        .first()
    )
    if plan:
        if float(plan.final_risk_price or 0) > 0:
            hard_stop_price = round(float(plan.final_risk_price), 2)
            evidence.append(f"按次日计划最终风险价设定硬止损 {hard_stop_price:.2f}。")
            sources.append("next_day_plan")
        plan_candidates = [
            float(plan.reduce_price or 0),
            float(plan.trim_price or 0),
            float(plan.confirm_price or 0),
            float(plan.stop_loss_4pct or 0),
        ]
        plan_stop = max([value for value in plan_candidates if value > 0], default=0.0)
        if plan_stop > 0:
            structure_stop = round(plan_stop, 2)
            evidence.append(f"按次日计划结构位设定结构止损 {structure_stop:.2f}。")
            sources.append("next_day_plan")
    day_start, day_end = _utc_storage_bounds_for_local_trade_day()
    exit_card = (
        db.query(ExitCard)
        .filter(
            ExitCard.code.in_(list(candidates)),
            ExitCard.created_at >= day_start,
            ExitCard.created_at <= day_end,
        )
        .order_by(ExitCard.created_at.desc(), ExitCard.id.desc())
        .first()
    )
    if exit_card:
        if float(exit_card.failure_price or 0) > 0:
            hard_stop_price = round(float(exit_card.failure_price), 2)
            evidence.append(f"按卖出卡失败价设定硬止损 {hard_stop_price:.2f}。")
            sources.append("sell_card")
        card_candidates = [float(exit_card.trim_price or 0), float(exit_card.confirm_price or 0)]
        card_stop = max([value for value in card_candidates if value > 0], default=0.0)
        if card_stop > 0 and (not structure_stop or card_stop > structure_stop):
            structure_stop = round(card_stop, 2)
            evidence.append(f"按卖出卡减仓/确认价设定结构止损 {structure_stop:.2f}。")
            sources.append("sell_card")
    return structure_stop, hard_stop_price, evidence, sources


def _script_type_for_holding(holding: Holding) -> str:
    text = f"{holding.position_type or ''} {holding.next_discipline or ''}"
    if "打板" in text or "冲板" in text or "连板" in text:
        return "breakout"
    if "趋势" in text or "容量" in text:
        return "trend"
    return "default"


def _time_stop_rule_for(db: Session | None, holding: Holding) -> dict[str, Any]:
    script_type = _script_type_for_holding(holding)
    base = dict(DEFAULT_TIME_STOP_RULES.get(script_type) or DEFAULT_TIME_STOP_RULES["default"])
    base["script_type"] = script_type
    if db is None:
        return base
    row = db.query(TimeStopRule).filter(TimeStopRule.script_type == script_type, TimeStopRule.enabled.is_(True)).first()
    if row is None and script_type != "default":
        row = db.query(TimeStopRule).filter(TimeStopRule.script_type == "default", TimeStopRule.enabled.is_(True)).first()
    if row is None:
        return base
    return {
        "script_type": row.script_type,
        "display_name": row.display_name,
        "confirmation_deadline": row.confirmation_deadline,
        "below_vwap_minutes": row.below_vwap_minutes,
        "below_vwap_min_bars": row.below_vwap_min_bars,
        "recent_window_minutes": row.recent_window_minutes,
        "failed_limit_reseal_pct": row.failed_limit_reseal_pct,
        "enabled": row.enabled,
    }


def _parse_deadline(value: str, fallback: time) -> time:
    match = re.match(r"^(\d{1,2})[:：](\d{2})$", str(value or "").strip())
    if not match:
        return fallback
    hour = min(14, max(9, int(match.group(1))))
    minute = min(59, max(0, int(match.group(2))))
    return time(hour, minute)


def _confirmation_deadline(holding: Holding, rule: dict[str, Any] | None = None) -> time:
    text = f"{holding.position_type or ''} {holding.next_discipline or ''}"
    match = re.search(r"(\d{1,2})[:：](\d{2})\s*(?:确认|截止|不修复|未修复)", text)
    if match:
        hour = min(14, max(9, int(match.group(1))))
        minute = min(59, max(0, int(match.group(2))))
        return time(hour, minute)
    if rule:
        return _parse_deadline(str(rule.get("confirmation_deadline") or ""), time(10, 0))
    if "打板" in text or "冲板" in text or "连板" in text:
        return time(9, 45)
    if "趋势" in text or "容量" in text:
        return time(10, 30)
    return time(10, 0)


def _action_from_score(score: int, hard_exit: bool, has_profit: bool) -> tuple[str, str, float, str]:
    if hard_exit:
        return "EXIT_REQUIRED", "全部退出", 1.0, "EXIT"
    if score >= 5:
        return "EXIT_REQUIRED", "只留观察仓", 0.75, "EXIT"
    if score >= 4:
        return "REDUCE_REQUIRED", "减仓50%", 0.50, "REDUCE"
    if score >= 2:
        return "PROFIT_PROTECTION" if has_profit else "DIVERGENCE_HOLD", "减仓25%", 0.25, "PROTECT"
    if score >= 1:
        return "DIVERGENCE_HOLD", "观察但禁止加仓", 0.0, "WATCH"
    return "PROFIT_EXPANSION" if has_profit else "NORMAL_HOLD", "继续持有", 0.0, "INFO"


def _near_intraday_extreme_low(
    current: float,
    low: float,
    high: float,
    prev_close: float,
    limit_down_price: float,
) -> bool:
    """Return whether selling now would likely be chasing an intraday extreme.

    The guard deliberately requires both proximity to the low and a meaningful
    adverse move.  A quiet stock trading at the bottom of a tiny range should
    not be classified as an extreme merely because current == low.
    """
    if current <= 0 or low <= 0:
        return False
    intraday_range = max(0.0, high - low)
    range_position = (current - low) / intraday_range if intraday_range > 0 else 1.0
    near_low = current <= low * 1.005 or range_position <= 0.12
    drawdown_from_high = ((high - current) / high * 100) if high > 0 else 0.0
    change_pct = ((current - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
    near_limit_down = bool(limit_down_price > 0 and current <= limit_down_price * 1.01)
    meaningful_decline = drawdown_from_high >= 3.0 or change_pct <= -3.0 or near_limit_down
    return near_low and meaningful_decline


def _latest_next_day_plan(db: Session, holding: Holding) -> NextDayPlan | None:
    normalized = _normalize_code(holding.code)
    candidates = {holding.code, normalized, normalized.lstrip("0")}
    return (
        db.query(NextDayPlan)
        .filter(
            NextDayPlan.code.in_(list(candidates)),
            NextDayPlan.plan_type == "holding",
            NextDayPlan.plan_date == _trade_date(),
        )
        .order_by(NextDayPlan.updated_at.desc(), NextDayPlan.id.desc())
        .first()
    )


def _holding_execution_signals(
    db: Session,
    holding: Holding,
    *,
    now: datetime,
    current: float,
    high: float,
    low: float,
    prev_close: float,
    vwap: float,
    vwap_reliable: bool,
    volume_state: str,
    volume_price: VolumePriceSnapshot | VolumePriceSnapshotOut | None,
    seesaw: Any | None,
    market_regime: MarketRegimeSnapshot | None,
    market_expansion_frozen: bool,
    market_data_limited: bool,
    hard_exit: bool,
    hard_stop_price: float,
    structure_stop_price: float,
    near_extreme_low: bool,
    expectation_result: str,
    protection_level: str,
    profit_drawdown_pct: float,
) -> tuple[HoldingExecutionSignalOut, HoldingExecutionSignalOut, HoldingExecutionSignalOut]:
    """Build three independent gates for sell timing, panic control and adds.

    A signal is evidence-family based.  Floating P&L is never sufficient on its
    own, and a panic-sell guard never opens the add-risk gate.
    """
    plan = _latest_next_day_plan(db, holding)
    trim_price = float(getattr(plan, "trim_price", 0) or 0)
    high_drawdown_pct = ((high - current) / high * 100) if high > 0 and current > 0 else 0.0
    high_change_pct = ((high - prev_close) / prev_close * 100) if prev_close > 0 and high > 0 else 0.0

    target_evidence: list[str] = []
    plan_target_reached = bool(trim_price > 0 and high >= trim_price * 0.995)
    morning_peak_reached = bool(
        now.time() <= time(11, 30)
        and high_change_pct >= 3.0
        and high_drawdown_pct >= 1.5
    )
    if plan_target_reached:
        target_evidence.append(f"盘中最高 {high:.2f} 已到达次日计划兑现位 {trim_price:.2f}。")
    if morning_peak_reached:
        target_evidence.append(
            f"早盘最高涨幅 {high_change_pct:.2f}%，随后从高点回撤 {high_drawdown_pct:.2f}%，进入兑现观察窗。"
        )

    weakening_families: dict[str, str] = {}
    if vwap_reliable and vwap > 0 and current < vwap:
        weakening_families["vwap"] = f"当前价 {current:.2f} 已跌破真实分钟VWAP {vwap:.2f}。"
    minute_count = int(getattr(volume_price, "minute_bar_count", 0) or 0)
    attack_efficiency = float(getattr(volume_price, "attack_efficiency", 0) or 0)
    pullback_sell_ratio = float(getattr(volume_price, "pullback_sell_ratio", 0) or 0)
    if minute_count >= 5 and (pullback_sell_ratio >= 55 or (attack_efficiency <= 0.18 and high_drawdown_pct >= 2)):
        weakening_families["stall"] = (
            f"放量滞涨/回落卖压确认：上攻效率 {attack_efficiency:.2f}，"
            f"回落段卖出占比 {pullback_sell_ratio:.1f}%。"
        )
    sector_risk = str(getattr(seesaw, "risk_level", "") or "") if seesaw else ""
    sector_net = float(getattr(seesaw, "sector_net_inflow", 0) or 0) if seesaw else 0.0
    sector_pullback = float(getattr(seesaw, "theme_flow_pullback_pct", 0) or 0) if seesaw else 0.0
    if seesaw and (sector_risk in {"高", "中高"} or sector_net < 0 or sector_pullback >= 20):
        weakening_families["sector"] = (
            f"板块承接走弱：订单流方向净额 {sector_net:.2f} 亿，方向净额峰值回撤 {sector_pullback:.1f}%（供应商算法），风险{sector_risk or '观察'}。"
        )
    if high_drawdown_pct >= 2.5:
        weakening_families["drawdown"] = f"个股从日内高点回撤 {high_drawdown_pct:.2f}%，冲高延续性下降。"
    if protection_level != "NONE" and profit_drawdown_pct >= 2:
        weakening_families["profit_protection"] = (
            f"已进入{protection_level}利润保护且利润回撤 {profit_drawdown_pct:.2f} 个百分点。"
        )

    target_reached = plan_target_reached or morning_peak_reached
    has_structural_weakening = any(
        family in weakening_families for family in ("vwap", "stall", "sector")
    )
    high_sell_active = (
        target_reached
        and len(weakening_families) >= 2
        and has_structural_weakening
        and not hard_exit
    )
    high_sell_ratio = 0.50 if len(weakening_families) >= 4 else 0.25
    if high_sell_active and near_extreme_low:
        high_sell = HoldingExecutionSignalOut(
            code="HIGH_SELL_WINDOW",
            status="EXPIRED",
            level="WATCH",
            title="冲高兑现窗口已错过",
            action="禁止在日内极低位补卖；等待反抽后重新评估",
            evidence=target_evidence + list(weakening_families.values()),
            cancel_conditions=["当前价已回到日内极低位，原冲高兑现信号不再作为即时卖点。"],
            recovery_conditions=["反抽真实VWAP/固定压力位时，若板块仍弱且量价滞涨，再分批兑现。"],
        )
    elif high_sell_active:
        high_sell = HoldingExecutionSignalOut(
            code="HIGH_SELL_WINDOW",
            status="ACTIVE",
            level="HIGH" if high_sell_ratio >= 0.50 else "MEDIUM",
            title="冲高兑现窗口",
            action=f"分批兑现 {high_sell_ratio * 100:.0f}%，保留仓位继续验证",
            recommended_ratio=high_sell_ratio,
            evidence=target_evidence + list(weakening_families.values()),
            cancel_conditions=[
                "连续两个观察窗口重新站稳VWAP并有效突破日内高点/计划兑现位。",
                "板块订单流方向估算重新回到前排，且上攻效率、主动买盘同步恢复。",
            ],
            recovery_conditions=["首批兑现后只在重新站稳VWAP、板块回流时保留剩余仓；否则继续按计划降风险。"],
        )
    elif target_reached:
        high_sell_missing = []
        if len(weakening_families) < 2:
            high_sell_missing.append("尚未形成至少两类独立走弱证据。")
        if not has_structural_weakening:
            high_sell_missing.append("尚缺VWAP、放量滞涨或板块走弱中的至少一类结构证据。")
        high_sell = HoldingExecutionSignalOut(
            code="HIGH_SELL_WINDOW",
            status="WATCH",
            level="WATCH",
            title="到达兑现区，等待走弱确认",
            action="不盲目看多，也不因单一冲高立即卖出",
            evidence=target_evidence + list(weakening_families.values()),
            missing_conditions=high_sell_missing,
            cancel_conditions=["突破兑现位并持续站稳VWAP，取消本轮兑现观察。"],
            recovery_conditions=["出现跌破VWAP、放量滞涨、板块走弱或高点回撤中的至少两项后再执行。"],
        )
    else:
        high_sell = HoldingExecutionSignalOut(
            code="HIGH_SELL_WINDOW",
            status="INACTIVE",
            level="NEUTRAL",
            title="尚未进入冲高兑现区",
            action="按计划观察，不提前猜顶",
            missing_conditions=["尚未到达次日计划兑现位或有效早盘高点。"],
            cancel_conditions=["未触发。"],
            recovery_conditions=["到达计划压力位后，再等待量价、板块和回撤证据。"],
        )

    reversal_confirmed = volume_state == "REVERSAL_CONFIRMED"
    reversal_pending = volume_state == "REVERSAL_PENDING"
    panic_context = bool((near_extreme_low or reversal_confirmed or reversal_pending) and not hard_exit)
    if panic_context and high_sell.status != "ACTIVE":
        support_evidence: list[str] = []
        if near_extreme_low:
            support_evidence.append(f"当前价 {current:.2f} 接近日内极低位 {low:.2f}，此处追卖赔率不利。")
        if reversal_confirmed:
            support_evidence.append("已确认V形修复/开板承接、低点抬高或重新站回真实VWAP。")
        elif reversal_pending:
            support_evidence.append("价格已脱离低点进入反转观察，但尚未完成VWAP与低点抬高双确认。")
        panic_guard = HoldingExecutionSignalOut(
            code="PANIC_SELL_GUARD",
            status="ACTIVE",
            level="PROTECT",
            title="禁止恐慌卖出" if not reversal_confirmed else "反转确认，禁止沿用低点卖出结论",
            action="不在极低位割肉；等待反抽/VWAP确认后再决定",
            evidence=support_evidence,
            missing_conditions=[] if reversal_confirmed else ["尚需重新站稳VWAP或形成更高低点。"],
            cancel_conditions=[
                f"明确固定硬止损 {hard_stop_price:.2f} 被触发。" if hard_stop_price > 0 else "盘前尚未冻结明确硬止损；不能用盘中最低价临时制造止损。",
                "反抽失败后再创新低，并连续5-15分钟无法收回VWAP/固定结构位。",
            ],
            recovery_conditions=["重新站稳VWAP、低点抬高且板块停止流出后，恢复为正常持有观察。"],
        )
    else:
        panic_guard = HoldingExecutionSignalOut(
            code="PANIC_SELL_GUARD",
            status="BLOCKED" if hard_exit else "INACTIVE",
            level="HIGH" if hard_exit else "NEUTRAL",
            title="明确硬止损优先" if hard_exit else "未触发恐慌保护",
            action="硬止损已触发，恐慌保护不得覆盖退出纪律" if hard_exit else "按正常证据链执行",
            evidence=[f"当前价 {current:.2f} 已触发固定硬止损 {hard_stop_price:.2f}。"] if hard_exit else [],
            cancel_conditions=["未触发。"],
            recovery_conditions=["只有极低位且未触发明确硬止损时，才启用禁止恐慌卖保护。"],
        )

    market_gate_open = bool(market_regime is not None and not market_expansion_frozen and not market_data_limited)
    sector_ebb = list(getattr(seesaw, "sector_ebb_trigger", []) or []) if seesaw else []
    sector_acceleration = float(getattr(seesaw, "sector_acceleration", 0) or 0) if seesaw else 0.0
    sector_turning_strong = bool(
        seesaw
        and sector_risk not in {"高", "中高"}
        and sector_net > 0
        and sector_acceleration >= 0
        and not sector_ebb
    )
    stock_reversal_ready = bool(reversal_confirmed and vwap_reliable and vwap > 0 and current >= vwap)
    expectation_allows = expectation_result not in WEAK_EXPECTATION_RESULTS
    reward_target = trim_price if trim_price > current else 0.0
    risk_floor = hard_stop_price if hard_stop_price > 0 else structure_stop_price
    downside = current - risk_floor if current > risk_floor > 0 else 0.0
    upside = reward_target - current if reward_target > current else 0.0
    risk_reward = upside / downside if upside > 0 and downside > 0 else 0.0
    risk_reward_ok = risk_reward >= 1.5
    add_missing: list[str] = []
    if not market_gate_open:
        add_missing.append("全市场扩仓闸门未开放或市场数据质量不足。")
    if not sector_turning_strong:
        add_missing.append("所属板块订单流方向估算尚未转强并形成正向加速。")
    if not stock_reversal_ready:
        add_missing.append("个股尚未完成V形/低点抬高并重新站稳真实VWAP。")
    if not expectation_allows:
        add_missing.append("当前预期仍弱于阈值，禁止用补仓掩盖证伪。")
    if not risk_reward_ok:
        add_missing.append("从当前价到计划兑现位的风险收益比尚未达到1.5。")
    add_eligible = not add_missing
    contrarian_add = HoldingExecutionSignalOut(
        code="CONTRARIAN_ADD_EVALUATION",
        status="ELIGIBLE" if add_eligible else "BLOCKED",
        level="OPPORTUNITY" if add_eligible else "NEUTRAL",
        title="允许评估逆势试错" if add_eligible else "禁止逆势补仓",
        action=(
            "仅允许评估小仓试错；下单前再次核对VWAP、板块订单流方向估算和固定止损"
            if add_eligible
            else "不恐慌卖出不等于允许抄底，四道闸门未齐前禁止补仓"
        ),
        evidence=(
            [
                f"全市场扩仓闸门开放：{market_regime.regime_name if market_regime else '未知'}。",
                f"板块订单流方向净额 {sector_net:.2f} 亿、加速度 {sector_acceleration:.2f}（供应商算法，非账户真实流水）。",
                f"个股反转确认并站上VWAP {vwap:.2f}。",
                f"计划兑现位 {reward_target:.2f} / 风险位 {risk_floor:.2f}，风险收益比 {risk_reward:.2f}。",
            ]
            if add_eligible
            else []
        ),
        missing_conditions=add_missing,
        cancel_conditions=[
            "重新跌破VWAP或抬高后的次低点。",
            "板块订单流方向估算再次转负/扩仓闸门关闭。",
            "风险收益比跌破1.5或预期再次证伪。",
        ],
        recovery_conditions=["四道闸门必须同时满足；这里只给出评估资格，不自动生成买入指令。"],
    )
    return high_sell, panic_guard, contrarian_add


def _high_open_failed_breakout_event(
    holding: Holding,
    quote: dict[str, Any],
    current: float,
    vwap: float,
    high_drawdown_pct: float,
    seesaw: Any | None,
    vwap_reliable: bool,
    volume_price: VolumePriceSnapshot | VolumePriceSnapshotOut | None,
) -> dict[str, Any] | None:
    prev_close = _safe_float(quote.get("prev_close")) or _safe_float(getattr(volume_price, "prev_close", 0))
    open_price = _safe_float(quote.get("open")) or _safe_float(getattr(volume_price, "open_price", 0))
    high_price = _safe_float(quote.get("high")) or _safe_float(getattr(volume_price, "high_price", 0))
    if not prev_close or not open_price or not high_price or not current:
        return None

    open_pct = (open_price - prev_close) / prev_close * 100
    high_pct = (high_price - prev_close) / prev_close * 100
    active_sell = _safe_float(getattr(volume_price, "active_sell_amount", 0))
    active_buy = _safe_float(getattr(volume_price, "active_buy_amount", 0))
    attack_efficiency = _safe_float(getattr(volume_price, "attack_efficiency", 0))
    sector_pullback = float(getattr(seesaw, "theme_flow_pullback_pct", 0) or 0) if seesaw else 0.0
    sector_net = float(getattr(seesaw, "sector_net_inflow", 0) or 0) if seesaw else 0.0

    evidence: list[str] = []
    matched = 0
    if open_pct >= 3:
        matched += 1
        evidence.append(f"高开 {open_pct:.2f}%，存在一致性兑现压力。")
    if high_pct >= 7 or high_price >= prev_close * 1.095:
        matched += 1
        evidence.append(f"盘中最高涨幅 {high_pct:.2f}%，接近/冲击涨停区。")
    if high_drawdown_pct >= 4:
        matched += 1
        evidence.append(f"从日内高点回撤 {high_drawdown_pct:.2f}%，冲高延续性不足。")
    if vwap_reliable and vwap and current < vwap:
        matched += 1
        evidence.append(f"当前价 {current:.2f} 跌破真实分钟VWAP {vwap:.2f}。")
    if active_sell > active_buy and active_sell > 0:
        matched += 1
        evidence.append(f"主动卖出额 {active_sell:.2f} 亿高于主动买入额 {active_buy:.2f} 亿。")
    if attack_efficiency > 0 and attack_efficiency < 0.2:
        matched += 1
        evidence.append(f"上攻效率仅 {attack_efficiency:.2f}，新增成交对价格推动减弱。")
    if sector_pullback >= 20 or sector_net < 0:
        matched += 1
        evidence.append("所属板块订单流方向估算从峰值回落或转负。")

    if matched < 3:
        return None

    level = "YELLOW"
    severity = "warning"
    priority = 72
    if (vwap_reliable and vwap and current < vwap and high_drawdown_pct >= 4) or matched >= 5:
        level = "ORANGE"
        severity = "critical"
        priority = 88
    if (vwap_reliable and vwap and current < vwap and high_drawdown_pct >= 7 and (active_sell > active_buy or sector_pullback >= 20 or sector_net < 0)):
        level = "RED"
        severity = "critical"
        priority = 98

    action = {
        "YELLOW": "停止加仓，提高止盈保护，观察VWAP。",
        "ORANGE": "冲板失败/跌破VWAP风险，建议减仓30%-50%。",
        "RED": "交易逻辑证伪风险，建议退出，禁止补仓和摊低成本。",
    }[level]
    evidence.append(action)
    return {
        "captured_at": _shanghai_now_naive(),
        "scope": "stock",
        "target_code": holding.code,
        "target_name": holding.name,
        "event_type": "HIGH_OPEN_FAILED_BREAKOUT",
        "severity": severity,
        "value": round(current, 2),
        "previous_value": round(vwap or prev_close, 2),
        "priority": priority,
        "group_key": "stock:high-open-failed-breakout",
        "evidence": [f"{level} 风险："] + evidence,
    }


def _sector_migration_signal(seesaw: Any | None) -> tuple[bool, int, list[str], float, float]:
    if not seesaw:
        return False, 0, [], 0.0, 0.0
    target = str(getattr(seesaw, "external_inflow_target", "") or "")
    if not target:
        return False, 0, [], 0.0, 0.0

    evidence: list[str] = []
    criteria = 0
    score = 0
    sector_net = float(getattr(seesaw, "sector_net_inflow", 0) or 0)
    flow_peak = float(getattr(seesaw, "theme_flow_peak", 0) or 0)
    pullback = float(getattr(seesaw, "theme_flow_pullback_pct", 0) or 0)
    rank = int(getattr(seesaw, "sector_rank", 0) or 0)
    risk_level = str(getattr(seesaw, "risk_level", "") or "")

    if target:
        criteria += 1
        score += 15
        evidence.append(f"新题材/外部吸金方向为 {target}。")
    if sector_net < 0 or pullback >= 20:
        criteria += 1
        score += 25 if sector_net < 0 and pullback >= 20 else 18
        evidence.append(f"原主线订单流方向弱化：方向净额 {sector_net:.2f} 亿，峰值回落 {pullback:.2f}%（供应商算法）。")
    if rank > 10:
        criteria += 1
        score += 12
        evidence.append(f"原主线订单流方向排名降至第 {rank}，不在前排。")
    if risk_level in {"高", "中高", "中"}:
        criteria += 1
        score += 12
        evidence.append(f"持仓/主线风险等级为 {risk_level}。")
    stock_triggers = [str(item) for item in list(getattr(seesaw, "stock_weakening_trigger", []) or [])]
    sector_triggers = [str(item) for item in list(getattr(seesaw, "sector_ebb_trigger", []) or [])]
    if stock_triggers:
        criteria += 1
        score += 18
        evidence.extend(stock_triggers[:2])
    if sector_triggers:
        criteria += 1
        score += 15
        evidence.extend(sector_triggers[:2])

    leader_text = " ".join(stock_triggers + sector_triggers)
    if any(keyword in leader_text for keyword in ("龙头", "核心股", "领涨", "切换")):
        criteria += 1
        score += 15
        evidence.append("龙头/核心强弱证据支持订单流方向切换。")

    confidence = min(95, score)
    confirmed = criteria >= 3 and score >= 55 and (sector_net < 0 or pullback >= 20) and bool(stock_triggers or sector_triggers)
    if confirmed:
        evidence.insert(0, f"疑似跨板块订单流方向迁移，可信度 {confidence}%；该结论来自供应商算法，不代表账户真实划转。")
    return confirmed, confidence, evidence[:7], sector_net, flow_peak


def _build_events(
    holding: Holding,
    current: float,
    vwap: float,
    current_profit_pct: float,
    max_profit_pct: float,
    profit_drawdown_pct: float,
    seesaw: Any | None,
    evidence: list[str],
    volume_price_state: str = "",
    expectation_result: str = "",
    vwap_reliable: bool = False,
    time_stop_reasons: list[str] | None = None,
    quote: dict[str, Any] | None = None,
    volume_price: VolumePriceSnapshot | VolumePriceSnapshotOut | None = None,
    high_drawdown_pct: float = 0,
) -> list[dict[str, Any]]:
    now = _shanghai_now_naive()
    events: list[dict[str, Any]] = []
    if expectation_result == "INVALID":
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "EXPECTATION_INVALIDATED",
            "severity": "critical",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 100,
            "group_key": "stock:expectation-invalidated",
            "evidence": [next((item for item in evidence if item.startswith("预期证伪：")), "实际竞价/开盘显著低于合理预期区间，预期已经证伪。")],
        })
    if vwap and current < vwap and vwap_reliable:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "VWAP_BROKEN",
            "severity": "warning",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 60,
            "group_key": "stock:vwap",
            "evidence": [f"当前价 {current:.2f} 跌破真实分钟VWAP {vwap:.2f}。"],
        })
    if volume_price_state in {"VOLUME_PRICE_WEAKENING", "HIGH_DRAWDOWN"}:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": volume_price_state,
            "severity": "critical" if volume_price_state == "VOLUME_PRICE_WEAKENING" and vwap_reliable else "warning",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 90 if volume_price_state == "VOLUME_PRICE_WEAKENING" else 55,
            "group_key": "stock:volume-price",
            "evidence": (evidence[:3] or ["量价结构转弱。"]) + ([] if vwap_reliable else ["VWAP缺少真实1分钟成交确认，该事件仅作观察。"]),
        })
    if vwap_reliable and expectation_result in WEAK_EXPECTATION_RESULTS and volume_price_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"}:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "EXPECTATION_VOLUME_BREAKDOWN",
            "severity": "critical",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 95,
            "group_key": "stock:expectation-volume",
            "evidence": ["预期低于阈值，同时量价跌破关键承接，执行上优先降风险。"],
        })
    if max_profit_pct >= 5 and profit_drawdown_pct >= 3:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "PROFIT_DRAWDOWN_WARNING",
            "severity": "warning",
            "value": round(current_profit_pct, 2),
            "previous_value": round(max_profit_pct, 2),
            "priority": 50,
            "group_key": "stock:profit",
            "evidence": [f"最大浮盈 {max_profit_pct:.2f}%，当前 {current_profit_pct:.2f}%，利润回撤 {profit_drawdown_pct:.2f} 个百分点。"],
        })
    if seesaw and float(getattr(seesaw, "theme_flow_pullback_pct", 0) or 0) >= 20:
        events.append({
            "captured_at": now,
            "scope": "sector",
            "target_code": holding.code,
            "target_name": str(getattr(seesaw, "holding_theme", "") or holding.name),
            "event_type": "SECTOR_FLOW_PEAK_REVERSAL",
            "severity": "warning",
            "value": round(float(getattr(seesaw, "theme_flow_current", 0) or 0), 2),
            "previous_value": round(float(getattr(seesaw, "theme_flow_peak", 0) or 0), 2),
            "priority": 55,
            "group_key": "sector:flow",
            "evidence": [str(getattr(seesaw, "theme_flow_summary", "") or "板块订单流方向估算从峰值回落。")],
        })
    if seesaw:
        flow_turning = str(getattr(seesaw, "sector_flow_turning", "") or "")
        flow_signal = str(getattr(seesaw, "sector_flow_signal", "") or "")
        flow_as_of = str(getattr(seesaw, "sector_flow_as_of", "") or "")
        flow_captured_at: datetime | None = None
        if flow_as_of:
            try:
                candidate = datetime.fromisoformat(flow_as_of)
                if candidate.tzinfo is not None:
                    candidate = candidate.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
                if candidate.date() == now.date() and candidate <= now:
                    flow_captured_at = candidate
            except (TypeError, ValueError):
                flow_captured_at = None
        flow_event_map = {
            "TURN_TO_OUTFLOW": ("SECTOR_FLOW_TURN_OUT", "warning", 86, "sector:flow-direction:out"),
            "INFLOW_FADING": ("SECTOR_FLOW_WEAKENING", "warning", 74, "sector:flow-direction:weak"),
            "OUTFLOW_ACCELERATING": ("SECTOR_FLOW_WEAKENING", "critical", 82, "sector:flow-direction:weak"),
            "FLOW_WEAKENING": ("SECTOR_FLOW_WEAKENING", "warning", 70, "sector:flow-direction:weak"),
            "TURN_TO_INFLOW": ("SECTOR_FLOW_TURN_IN", "info", 62, "sector:flow-direction:in"),
            "OUTFLOW_NARROWING": ("SECTOR_FLOW_RECOVERY", "info", 48, "sector:flow-direction:recovery"),
            "INFLOW_ACCELERATING": ("SECTOR_FLOW_RECOVERY", "info", 52, "sector:flow-direction:recovery"),
            "FLOW_IMPROVING": ("SECTOR_FLOW_RECOVERY", "info", 42, "sector:flow-direction:recovery"),
        }
        mapped = flow_event_map.get(flow_turning)
        # A flow event without a provider observation timestamp is not causal
        # evidence and must not be stamped with the application clock.
        if mapped and flow_captured_at is not None:
            event_type, severity, priority, group_key = mapped
            speed = getattr(seesaw, "sector_flow_speed", None)
            acceleration = getattr(seesaw, "sector_flow_acceleration", None)
            metrics = []
            if speed is not None:
                metrics.append(f"流速 {float(speed):+.3f} 亿/分钟")
            if acceleration is not None:
                metrics.append(f"加速度 {float(acceleration):+.4f} 亿/分钟²")
            events.append({
                "captured_at": flow_captured_at,
                "scope": "sector",
                "target_code": holding.code,
                "target_name": str(getattr(seesaw, "holding_theme", "") or holding.name),
                "event_type": event_type,
                "severity": severity,
                "value": round(float(getattr(seesaw, "sector_net_inflow", 0) or 0), 2),
                "previous_value": 0.0,
                "priority": priority,
                "group_key": group_key,
                "evidence": [
                    f"截至 {flow_captured_at.strftime('%H:%M:%S')}，{flow_signal or flow_turning}。",
                    "，".join(metrics) if metrics else "订单流方向曲线已有至少两个带时点的真实观察。",
                ],
            })
        flow_reliable = bool(getattr(seesaw, "sector_flow_kinetics_reliable", False))
        if flow_reliable and flow_captured_at is not None and volume_price is not None:
            direction = str(getattr(seesaw, "sector_flow_direction", "") or "")
            if not direction:
                sector_net = float(getattr(seesaw, "sector_net_inflow", 0) or 0)
                direction = "NET_INFLOW" if sector_net > 0 else "NET_OUTFLOW" if sector_net < 0 else "NEUTRAL"
            flow_state = FlowKinetics(
                direction=direction,
                speed=(
                    float(getattr(seesaw, "sector_flow_speed"))
                    if getattr(seesaw, "sector_flow_speed", None) is not None else None
                ),
                acceleration=(
                    float(getattr(seesaw, "sector_flow_acceleration"))
                    if getattr(seesaw, "sector_flow_acceleration", None) is not None else None
                ),
                turning=flow_turning or None,
                signal=flow_signal or None,
                severity="warning" if flow_turning in {
                    "TURN_TO_OUTFLOW", "INFLOW_FADING", "OUTFLOW_ACCELERATING", "FLOW_WEAKENING",
                } else "info",
                as_of=flow_as_of or None,
                window_minutes=getattr(seesaw, "sector_flow_window_minutes", None),
                reliable=True,
            )
            snapshot_low = float(getattr(volume_price, "low_price", 0) or 0)
            low_rebound_pct = (
                (current - snapshot_low) / snapshot_low * 100
                if snapshot_low > 0 and current >= snapshot_low else 0.0
            )
            price_vs_vwap_pct = (
                float(getattr(volume_price, "price_vs_vwap", 0) or 0)
                if vwap_reliable else None
            )
            composite_alerts = classify_price_volume_flow_alerts(
                change_pct=float(getattr(volume_price, "change_pct", 0) or 0),
                volume_ratio=float(getattr(volume_price, "volume_ratio", 0) or 0),
                price_vs_vwap_pct=price_vs_vwap_pct,
                vwap_reliable=vwap_reliable,
                flow=flow_state,
                low_rebound_pct=low_rebound_pct,
                high_drawdown_pct=high_drawdown_pct,
            )
            for alert in composite_alerts:
                # The execution engine already emits a richer, hard-stop-aware
                # PANIC_SELL_GUARD below; keep this classifier focused on the
                # remaining price-volume-flow semantics.
                if alert.event_type == "LOW_PANIC_SELL_GUARD":
                    continue
                events.append({
                    "captured_at": flow_captured_at or now,
                    "scope": "stock",
                    "target_code": holding.code,
                    "target_name": holding.name,
                    "event_type": alert.event_type,
                    "severity": alert.severity,
                    "value": round(current, 2),
                    "previous_value": round(vwap, 2),
                    "priority": 88 if alert.severity == "critical" else 72 if alert.severity == "warning" else 42,
                    "group_key": f"stock:price-volume-flow:{alert.event_type.lower()}",
                    "evidence": [*alert.evidence, alert.action],
                })
    migration_confirmed, migration_confidence, migration_evidence, migration_value, migration_previous = _sector_migration_signal(seesaw)
    if migration_confirmed:
        events.append({
            "captured_at": now,
            "scope": "sector",
            "target_code": holding.code,
            "target_name": str(getattr(seesaw, "holding_theme", "") or holding.name),
            "event_type": "SECTOR_MIGRATION_CONFIRMED",
            "severity": "warning",
            "value": migration_value,
            "previous_value": migration_previous,
            "priority": 75 + min(20, max(0, migration_confidence - 70)),
            "group_key": "sector:migration",
            "evidence": migration_evidence,
        })
    if seesaw and str(getattr(seesaw, "risk_level", "")) in {"高", "中高"}:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "EXPECTATION_DOWNGRADE",
            "severity": "critical" if getattr(seesaw, "risk_level", "") == "高" else "warning",
            "value": float(getattr(seesaw, "pullback_from_high_pct", 0) or 0),
            "previous_value": 0,
            "priority": 70,
            "group_key": "stock:risk",
            "evidence": evidence[:3] or [str(getattr(seesaw, "signal", "") or "持仓风险升高。")],
        })
    for reason in time_stop_reasons or []:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "TIME_STOP_TRIGGERED",
            "severity": "critical" if vwap_reliable else "warning",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 92,
            "group_key": "stock:time-stop",
            "evidence": [reason],
        })
    high_open_event = _high_open_failed_breakout_event(
        holding,
        quote or {},
        current,
        vwap,
        high_drawdown_pct,
        seesaw,
        vwap_reliable,
        volume_price,
    )
    if high_open_event:
        events.append(high_open_event)
    return events


def _time_stop_reasons(
    db: Session,
    holding: Holding,
    current: float,
    vwap: float,
    vwap_reliable: bool,
    quote: dict[str, Any],
    volume_state: str,
    expectation_result: str,
    now: datetime,
) -> list[str]:
    reasons: list[str] = []
    rule = _time_stop_rule_for(db, holding)
    deadline = _confirmation_deadline(holding, rule)
    below_vwap_min_bars = max(1, int(rule.get("below_vwap_min_bars") or 5))
    below_vwap_minutes = max(1, int(rule.get("below_vwap_minutes") or 5))
    recent_window_minutes = max(below_vwap_minutes, int(rule.get("recent_window_minutes") or 15))
    failed_limit_reseal_pct = min(1.0, max(0.9, float(rule.get("failed_limit_reseal_pct") or 0.985)))
    if vwap_reliable and vwap > 0 and current < vwap:
        rows = quote.get("minute_bars") or quote.get("minutes") or []
        below_rows = []
        if isinstance(rows, list):
            for row in rows[-max(8, below_vwap_min_bars + 2):]:
                if not isinstance(row, dict):
                    continue
                price = _safe_float(row.get("price") or row.get("close"))
                if price > 0 and price < vwap:
                    below_rows.append(row)
        if len(below_rows) >= below_vwap_min_bars:
            reasons.append(
                f"{rule.get('display_name')}规则：真实分钟数据连续 {len(below_rows)} 根低于VWAP {vwap:.2f}，"
                f"满足{below_vwap_minutes}分钟时间止损观察。"
            )
        else:
            normalized = _normalize_code(holding.code)
            candidates = {holding.code, normalized, normalized.lstrip("0")}
            recent = (
                db.query(VolumePriceSnapshot)
                .filter(
                    VolumePriceSnapshot.code.in_(list(candidates)),
                    VolumePriceSnapshot.trade_date == _trade_date(),
                    VolumePriceSnapshot.captured_at >= now - timedelta(minutes=recent_window_minutes),
                    VolumePriceSnapshot.vwap_reliable.is_(True),
                )
                .order_by(VolumePriceSnapshot.captured_at.asc(), VolumePriceSnapshot.id.asc())
                .all()
            )
            below = [row for row in recent if float(row.price or 0) < float(row.vwap or 0)]
            if len(below) >= 3 and (below[-1].captured_at - below[0].captured_at) >= timedelta(minutes=below_vwap_minutes):
                reasons.append(f"{recent_window_minutes}分钟窗口内多次低于真实VWAP且持续超过{below_vwap_minutes}分钟，确认持续低于VWAP。")

    prev_close = _safe_float(quote.get("prev_close"))
    high = _safe_float(quote.get("high"))
    limit_up_price = _safe_float(quote.get("limit_up_price")) or (round(prev_close * 1.1, 2) if prev_close else 0)
    if limit_up_price and high >= limit_up_price * 0.995 and current < limit_up_price * failed_limit_reseal_pct:
        reasons.append(
            f"{rule.get('display_name')}规则：盘中冲击涨停价 {limit_up_price:.2f} 后未回封，"
            f"当前 {current:.2f} 低于回封阈值 {failed_limit_reseal_pct:.3f}。"
        )

    if now.time() >= deadline and expectation_result in WEAK_EXPECTATION_RESULTS and volume_state not in {"REPAIR_CONFIRMED", "VWAP_STRONG", "REVERSAL_CONFIRMED", "REVERSAL_PENDING"}:
        reasons.append(f"{deadline.strftime('%H:%M')}确认截止后预期仍偏弱且量价未修复，触发时间止损确认。")
    return reasons


def _recovery_events(db: Session, holding: Holding, volume_state: str, current: float, vwap: float) -> list[dict[str, Any]]:
    if volume_state not in {"REPAIR_CONFIRMED", "VWAP_STRONG", "REVERSAL_CONFIRMED"}:
        return []
    normalized = _normalize_code(holding.code)
    candidates = {holding.code, normalized, normalized.lstrip("0")}
    since = _shanghai_now_naive() - timedelta(minutes=45)
    risk_events = (
        db.query(IntradayEvidenceEvent)
        .filter(
            IntradayEvidenceEvent.target_code.in_(list(candidates)),
            IntradayEvidenceEvent.trade_date == _trade_date(),
            IntradayEvidenceEvent.captured_at >= since,
            IntradayEvidenceEvent.event_type.in_([
                "VWAP_BROKEN",
                "VOLUME_PRICE_WEAKENING",
                "EXPECTATION_VOLUME_BREAKDOWN",
                "TIME_STOP_TRIGGERED",
            ]),
        )
        .order_by(IntradayEvidenceEvent.captured_at.desc(), IntradayEvidenceEvent.id.desc())
        .limit(3)
        .all()
    )
    if not risk_events:
        return []
    return [{
        "captured_at": _shanghai_now_naive(),
        "scope": "stock",
        "target_code": holding.code,
        "target_name": holding.name,
        "event_type": "INTRADAY_REVERSAL_CONFIRMED" if volume_state == "REVERSAL_CONFIRMED" else "RISK_RECOVERY_CONFIRMED",
        "severity": "info",
        "value": round(current, 2),
        "previous_value": round(vwap, 2),
        "priority": 35,
        "group_key": "stock:recovery",
        "evidence": ["前序风险事件后重新站回真实VWAP/量价修复，风险状态恢复为观察。"],
    }]


def _confirmation_policy(event_type: str) -> tuple[int, int]:
    policy = {
        "VWAP_BROKEN": (5, 2),
        "VOLUME_PRICE_WEAKENING": (5, 2),
        "HIGH_DRAWDOWN": (10, 2),
        "EXPECTATION_VOLUME_BREAKDOWN": (3, 1),
        "TIME_STOP_TRIGGERED": (3, 1),
        "SECTOR_MIGRATION_CONFIRMED": (10, 2),
        "SECTOR_FLOW_PEAK_REVERSAL": (10, 2),
        "SECTOR_DISTRIBUTION_RISK": (10, 2),
        "RISK_RECOVERY_CONFIRMED": (5, 1),
        "PROFIT_DRAWDOWN_WARNING": (15, 2),
        "HIGH_SELL_WINDOW": (5, 2),
        "PANIC_SELL_GUARD": (5, 1),
        "CONTRARIAN_ADD_EVALUATION": (5, 2),
        "SECTOR_FLOW_TURN_OUT": (10, 1),
        "SECTOR_FLOW_TURN_IN": (10, 1),
        "SECTOR_FLOW_WEAKENING": (10, 2),
        "SECTOR_FLOW_RECOVERY": (10, 2),
        "SHRINKING_RISE_DIVERGENCE": (5, 2),
        "SHRINKING_REBOUND_UNCONFIRMED": (5, 2),
        "SHRINKING_DECLINE_EXHAUSTION_WATCH": (5, 2),
        "SHRINKING_DECLINE_WEAKNESS": (5, 2),
        "SHRINKING_PULLBACK_SUPPORT_WATCH": (5, 2),
        "VOLUME_DOWN_FLOW_ACCELERATION": (3, 2),
        "FLOW_TURN_OUT_DISTRIBUTION_WARNING": (5, 1),
        "FLOW_TURN_IN_REBOUND_WATCH": (5, 1),
        "VOLUME_FLOW_STRENGTH_CONFIRMED": (5, 2),
        "VOLUME_REBOUND_CONFIRMED": (5, 2),
    }
    return policy.get(event_type, (5, 2))


def _dedupe_events(db: Session, events: list[dict[str, Any]], cooldown_minutes: int = 5) -> list[dict[str, Any]]:
    persisted: list[dict[str, Any]] = []
    now = _shanghai_now_naive()
    seen: set[tuple[str, str]] = set()
    for event in events:
        key = (str(event.get("target_code") or ""), str(event.get("event_type") or ""))
        if key in seen:
            continue
        seen.add(key)
        event_type = str(event.get("event_type") or "")
        event_cooldown, required_occurrences = _confirmation_policy(event_type)
        window_minutes = event_cooldown or cooldown_minutes
        recent = (
            db.query(IntradayEvidenceEvent)
            .filter(
                IntradayEvidenceEvent.target_code == key[0],
                IntradayEvidenceEvent.event_type == key[1],
                IntradayEvidenceEvent.trade_date == _trade_date(),
                IntradayEvidenceEvent.captured_at >= now - timedelta(minutes=window_minutes),
            )
            .order_by(IntradayEvidenceEvent.captured_at.desc(), IntradayEvidenceEvent.id.desc())
            .first()
        )
        if recent is not None:
            observed_at = event.get("captured_at") or now
            recent.captured_at = observed_at
            recent.last_seen_at = observed_at
            recent.occurrence_count = int(recent.occurrence_count or 1) + 1
            recent.confirmed = recent.occurrence_count >= required_occurrences
            recent.value = float(event.get("value") or 0)
            recent.previous_value = float(event.get("previous_value") or 0)
            recent.priority = max(int(recent.priority or 0), int(event.get("priority") or 0))
            recent.severity = str(event.get("severity") or recent.severity)
            recent.group_key = str(event.get("group_key") or recent.group_key or "")
            recent.evidence_json = _json_dumps(event.get("evidence") or [])
            continue
        event["first_seen_at"] = event.get("captured_at")
        event["last_seen_at"] = event.get("captured_at")
        event["occurrence_count"] = 1
        event["confirmed"] = required_occurrences <= 1
        persisted.append(event)
    return persisted


def _state_history_out(row: PositionStateHistory) -> PositionStateHistoryOut:
    return PositionStateHistoryOut(
        id=row.id,
        holding_id=row.holding_id,
        code=row.code,
        name=row.name,
        trade_date=row.trade_date,
        old_state=row.old_state,
        new_state=row.new_state,
        captured_at=row.captured_at,
        reason=row.reason,
        evidence=_json_list(row.evidence_json),
    )


def _recent_state_history(db: Session, holding_id: int, limit: int = 20) -> list[PositionStateHistoryOut]:
    rows = (
        db.query(PositionStateHistory)
        .filter(PositionStateHistory.holding_id == holding_id)
        .order_by(PositionStateHistory.captured_at.desc(), PositionStateHistory.id.desc())
        .limit(limit)
        .all()
    )
    return [_state_history_out(row) for row in rows]


def build_position_execution_state(
    db: Session,
    holding: Holding,
    quote: dict[str, Any] | None = None,
    seesaw: Any | None = None,
    expectation: ExpectationSnapshot | ExpectationSnapshotOut | None = None,
    volume_price: VolumePriceSnapshot | VolumePriceSnapshotOut | None = None,
    global_cues: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> PositionExecutionStateOut:
    quote = quote or {}
    now = _shanghai_now_naive()
    current = _safe_float(quote.get("price")) or float(holding.current_price or 0)
    high = _safe_float(quote.get("high")) or max(current, float(holding.current_price or 0))
    low = _safe_float(quote.get("low"))
    vwap = _estimated_vwap(quote)
    # Execution calculation is also used by read-only decision cards.  Do not
    # seed AccountState merely because a page was opened; use the persisted
    # account value when it exists and otherwise fall back to the holding's
    # imported total-asset reference.
    account_state = db.get(AccountState, 1)
    total_asset = (
        float(account_state.total_asset or 0)
        if account_state is not None
        else float(holding.total_asset or 0)
    )
    market_value = current * int(holding.quantity or 0)
    position_ratio = market_value / total_asset if total_asset else 0.0
    current_profit_pct = ((current - holding.cost_price) / holding.cost_price * 100) if holding.cost_price else 0.0
    high_profit_pct = ((high - holding.cost_price) / holding.cost_price * 100) if high and holding.cost_price else current_profit_pct
    previous_snapshot = _latest_profit_snapshot(db, int(holding.id or 0))
    expectation = expectation or _latest_expectation_snapshot(db, holding.code)
    volume_price = volume_price or _latest_volume_price_snapshot(db, holding.code)
    vwap_reliable = _volume_vwap_reliable(volume_price, quote)
    previous_max_profit = float(previous_snapshot.maximum_profit_pct or 0) if previous_snapshot else 0.0
    previous_max_price = float(previous_snapshot.maximum_price or 0) if previous_snapshot else 0.0
    previous_max_at = previous_snapshot.maximum_profit_at if previous_snapshot else None
    previous_day_max = float(getattr(previous_snapshot, "day_max_profit_pct", 0) or 0) if previous_snapshot else 0.0
    previous_day_max_at = getattr(previous_snapshot, "day_max_profit_at", None) if previous_snapshot else None
    max_profit_pct = max(previous_max_profit, high_profit_pct, current_profit_pct)
    maximum_price = max(previous_max_price, high, current)
    maximum_profit_at = previous_max_at if previous_max_profit >= max(high_profit_pct, current_profit_pct) else now
    day_max_profit_pct = max(previous_day_max, high_profit_pct, current_profit_pct)
    day_max_profit_at = previous_day_max_at if previous_day_max >= max(high_profit_pct, current_profit_pct) else now
    profit_drawdown_pct = max(0.0, max_profit_pct - current_profit_pct)
    protection_level, allowed_drawdown = _protection_level(max_profit_pct)
    floor_profit_pct = max_profit_pct * (1 - allowed_drawdown) if protection_level != "NONE" else 0.0
    profit_protection_price = round(holding.cost_price * (1 + floor_profit_pct / 100), 2) if floor_profit_pct and holding.cost_price else 0.0
    # Stops must be stable across the session.  VWAP, the opening price and in
    # particular today's low are observations, not stop sources: using the
    # current low here makes every new low trigger a self-referential sell.
    hard_stop_price = 0.0
    structure_stop_price = round(holding.cost_price * 0.97, 2) if holding.cost_price else 0.0
    structure_stop_price, hard_stop_price, structured_stop_evidence, structured_stop_sources = _structured_stop_levels(
        db, holding, structure_stop_price, hard_stop_price
    )
    if "frozen_state" in structured_stop_sources:
        # The state is already frozen for this trade date.  Re-parsing mutable
        # intraday text would allow the stop to drift after the first decision.
        script_stop_evidence, script_stop_sources = [], []
    else:
        structure_stop_price, hard_stop_price, script_stop_evidence, script_stop_sources = _script_stop_levels(
            holding, structure_stop_price, hard_stop_price
        )
    stop_sources = list(dict.fromkeys(structured_stop_sources + script_stop_sources))
    stop_source = "+".join(stop_sources) if stop_sources else "cost_reference"
    stop_source_detail = "；".join((structured_stop_evidence + script_stop_evidence)[:4])
    if not stop_source_detail:
        stop_source_detail = "仅以持仓成本防守位作为稳定结构参考；尚未冻结明确硬止损，不使用盘中最低价、VWAP或开盘价即时生成止损。"
    trailing_stop_price = round(maximum_price * 0.95, 2) if maximum_price and protection_level in {"LEVEL_2", "LEVEL_3", "LEVEL_4"} else 0.0

    evidence: list[str] = [
        f"当前盈亏 {current_profit_pct:+.2f}%，最大浮盈 {max_profit_pct:+.2f}%，利润回撤 {profit_drawdown_pct:.2f} 个百分点。",
        f"结构止损 {structure_stop_price:.2f}，硬止损 {hard_stop_price:.2f}，利润保护线 {profit_protection_price:.2f}。",
        f"止损来源：{_stop_source_label(stop_source)}。{stop_source_detail}",
    ]
    evidence.extend(structured_stop_evidence)
    evidence.extend(script_stop_evidence)
    counter_evidence: list[str] = []
    invalid_conditions: list[str] = [
        f"放量跌破结构止损 {structure_stop_price:.2f} 且 5-15 分钟不能收回。",
        "板块订单流方向估算继续回落且个股反抽 VWAP 失败。",
    ]
    recovery_conditions: list[str] = [
        "重新站回 VWAP 并维持至少一个观察窗口。",
        "所属板块订单流方向估算停止恶化或重新回到前排。",
    ]
    market_regime, market_expansion_frozen, market_data_limited, market_age_minutes = _market_regime_gate(db, now)
    if market_regime is None:
        evidence.append("全市场状态数据缺口：尚无当日市场环境快照；本次不会把未知市场环境当成卖出依据。")
    else:
        market_summary = (
            f"全市场状态：{market_regime.regime_name or '未分类'}"
            f"（风险{market_regime.risk_level or '未知'}、数据质量{market_regime.data_quality or '未知'}、"
            f"快照{market_age_minutes}分钟前）。"
        )
        evidence.append(market_summary)
        evidence.extend([f"全市场证据：{item}" for item in _json_list(market_regime.evidence_json)[:3]])
        missing_market_fields = _json_list(market_regime.missing_fields_json)
        if market_data_limited:
            missing_detail = f"；缺失字段：{'、'.join(missing_market_fields[:5])}" if missing_market_fields else ""
            evidence.append(
                f"全市场数据质量不足：当前状态仅用于限制仓位扩张，不据此机械卖出{missing_detail}。"
            )
        if market_expansion_frozen:
            invalid_conditions.append("全市场高风险或关键数据不足期间，禁止加仓、抄底和做T买回。")
            recovery_conditions.append("全市场闸门解除后，仍需个股重新站稳VWAP且预期修复，才允许恢复加仓评估。")
            forbidden = _json_list(market_regime.forbidden_actions_json)
            if forbidden:
                evidence.append(f"全市场禁止动作：{'；'.join(forbidden[:3])}。")
        else:
            counter_evidence.append(
                f"全市场状态{market_regime.regime_name or '未分类'}未触发全局冻结闸门；个股仍须独立通过预期和量价验证。"
            )
    global_gate = global_market_execution_gate(global_cues, now=now) if global_cues is not None else None
    if global_gate is not None:
        evidence.extend([f"外围证据：{item}" for item in global_gate["evidence"]])
        counter_evidence.extend([f"外围反证：{item}" for item in global_gate["counter_evidence"]])
        evidence.append(
            f"外围环境扩仓分 {int(global_gate['score']):+d}；该分数只影响新增风险权限，绝不单独触发减仓或清仓。"
        )
        if global_gate["freeze_expansion"]:
            market_expansion_frozen = True
            invalid_conditions.append("外围主要指数出现新鲜且一致的显著弱势时，暂停加仓、抄底和做T买回。")
            recovery_conditions.append("外围冻结解除后，仍需A股市场、所属板块与个股量价共同确认，才恢复扩仓评估。")
    sector_distribution = _cached_sector_distribution_context(seesaw)
    distribution_state = str(sector_distribution.get("distribution_state") or "")
    distribution_level = str(
        sector_distribution.get("distribution_risk_level") or "UNKNOWN"
    ).upper()
    distribution_confirmations = int(
        sector_distribution.get("distribution_confirmation_count") or 0
    )
    distribution_high = bool(
        distribution_state == "高位派发风险"
        and distribution_level in {"HIGH", "CRITICAL"}
        and distribution_confirmations >= 3
        and sector_distribution.get("order_flow_exhausted") is True
        and sector_distribution.get("price_response_weak") is True
    )
    distribution_watch = bool(
        not distribution_high
        and distribution_state in {"资金承载衰减", "杠杆追涨观察", "去杠杆踩踏"}
    )
    if distribution_high:
        market_expansion_frozen = True
        sector_name = str(sector_distribution.get("name") or "所属板块")
        leverage_note = (
            "，并得到T+1两融拥挤确认"
            if sector_distribution.get("leverage_crowding") is True
            else "；两融未拥挤或数据缺口，不把两融当作必要条件"
        )
        evidence.append(
            f"板块派发联合证据：{sector_name}同时出现资金承载衰减与价格负反馈（{distribution_confirmations}项确认）{leverage_note}；禁止追涨、加仓和做T买回，但不据此单独机械卖出。"
        )
        evidence.extend(
            [f"板块证据：{item}" for item in list(sector_distribution.get("distribution_evidence") or [])[:3]]
        )
        invalid_conditions.append("板块派发联合证据未解除前，禁止新增风险；已有持仓须再叠加个股预期证伪、量价破位或固定止损，才升级减仓。")
        recovery_conditions.append("板块订单流重新转强、价格恢复正反馈且杠杆拥挤不再恶化后，再由个股站回VWAP确认是否解除派发观察。")
    elif distribution_watch:
        evidence.append(
            f"板块观察信号：{sector_distribution.get('name') or '所属板块'}处于{distribution_state}；证据尚未闭环，暂停追高并等待资金和价格响应复核。"
        )
    elif distribution_state in {"健康", "资金承载健康"}:
        counter_evidence.append(
            f"板块反证：{sector_distribution.get('name') or '所属板块'}资金与价格响应仍属健康，尚未形成派发联合证据。"
        )
    risk_family_scores: dict[str, int] = {}
    positive_family_scores: dict[str, int] = {}

    def add_risk(family: str, score: int) -> None:
        # Correlated observations in the same family corroborate one another,
        # but must not be counted repeatedly as independent risks.
        risk_family_scores[family] = max(risk_family_scores.get(family, 0), max(0, int(score)))

    def add_positive(family: str, score: int = 1) -> None:
        positive_family_scores[family] = max(positive_family_scores.get(family, 0), max(0, int(score)))

    if distribution_high:
        # One corroborating family: enough to freeze expansion, never enough
        # by itself to produce a sell action (score 1 => WATCH only).
        add_risk("sector_distribution", 1)

    negative_score = 0
    hard_exit = False
    expectation_result = _expectation_result(expectation)
    expectation_gap_score = _expectation_score(expectation)
    hard_expectation_invalidation = expectation_result == "INVALID" or expectation_gap_score <= -18
    volume_pattern = _volume_pattern(volume_price)
    high_drawdown_pct = ((high - current) / high * 100) if high and current else 0.0
    if volume_price is not None:
        high = max(high, _safe_float(getattr(volume_price, "high_price", 0)))
        low = low or _safe_float(getattr(volume_price, "low_price", 0))
        high_drawdown_pct = max(high_drawdown_pct, _safe_float(getattr(volume_price, "high_drawdown", 0)))
        vwap = _safe_float(getattr(volume_price, "vwap", 0)) or vwap
    volume_state = _volume_price_state(volume_pattern, current, vwap if vwap_reliable else 0, high_drawdown_pct)
    time_stop_reasons = _time_stop_reasons(db, holding, current, vwap, vwap_reliable, quote, volume_state, expectation_result, now)

    if protection_level != "NONE":
        evidence.append(f"已进入{protection_level}利润保护，不能无条件放任盈利大幅回吐。")
    if expectation_result in WEAK_EXPECTATION_RESULTS:
        score_add = 2 if expectation_result in {"WEAKER", "INVALID"} or expectation_gap_score <= -18 else 1
        add_risk("expectation", score_add)
        evidence.append(f"阶段预期结果 {expectation_result}，预期差 {expectation_gap_score}，执行侧不允许补仓摊低。")
        invalid_conditions.append("预期低于阈值且未出现量价修复前，禁止加仓或做T接回。")
    elif expectation_result in STRONG_EXPECTATION_RESULTS:
        counter_evidence.append(f"阶段预期结果 {expectation_result}，暂未构成预期证伪。")
        add_positive("expectation", 1)
    if volume_state == "VOLUME_PRICE_WEAKENING" and vwap_reliable:
        add_risk("volume_price", 2)
        evidence.append("量价形态为冲高回落跌破VWAP，优先按风险信号处理。")
        invalid_conditions.append("冲高回落跌破VWAP后，不能用主观预期继续扛单。")
    elif volume_state == "VWAP_BREAKDOWN" and vwap_reliable:
        add_risk("volume_price", 1)
        evidence.append("量价状态为跌破VWAP，等待重新站回后才允许恢复观察。")
    elif volume_state == "HIGH_DRAWDOWN":
        add_risk("volume_price", 1)
        evidence.append(f"相对日内高点回撤 {high_drawdown_pct:.2f}%，进入高位回落观察。")
    elif volume_state == "REPAIR_CONFIRMED":
        counter_evidence.append("量价状态为VWAP上方强势，暂不按走弱处理。")
        add_positive("volume_price", 1)
    elif volume_state == "REVERSAL_CONFIRMED":
        counter_evidence.append("盘中已形成V形修复、重新站回VWAP或低点抬高的反转证据，暂停沿用低点时的卖出结论。")
        add_positive("intraday_reversal", 3)
        recovery_conditions.insert(0, "反转确认后继续观察VWAP与抬高后的次低点；再次放量跌破两者才恢复减仓评估。")
        invalid_conditions.append("反转后的次低点失守且再次跌破真实VWAP，视为V形修复失败。")
    elif volume_state == "REVERSAL_PENDING":
        counter_evidence.append("价格已脱离深水/跌停附近低点，但尚未同时站稳VWAP并形成低点抬高，进入反转观察窗口。")
        add_positive("intraday_reversal", 1)
        recovery_conditions.insert(0, "等待连续站稳真实VWAP或形成更高低点后，再确认V形反转。")
    elif volume_state == "VWAP_STRONG":
        counter_evidence.append("当前价格位于可靠VWAP上方，量价尚未证实走弱。")
        add_positive("volume_price", 1)
    structure_reference_breached = bool(current <= structure_stop_price and structure_stop_price)
    structure_has_confirmation = bool(
        stop_sources
        or hard_expectation_invalidation
        or expectation_result in WEAK_EXPECTATION_RESULTS
        or (vwap_reliable and volume_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"})
    )
    if current <= hard_stop_price and hard_stop_price:
        add_risk("hard_stop", 5)
        hard_exit = True
        evidence.append(f"当前价 {current:.2f} 已触发硬止损 {hard_stop_price:.2f}。")
    elif structure_reference_breached and structure_has_confirmation:
        add_risk("structure", 2)
        if stop_sources:
            evidence.append(f"当前价 {current:.2f} 已接近/跌破明确计划结构位 {structure_stop_price:.2f}。")
        else:
            evidence.append(
                f"当前价 {current:.2f} 跌破成本防守参考 {structure_stop_price:.2f}，"
                "并与预期证伪或可靠量价破位共振。"
            )
    elif structure_reference_breached:
        counter_evidence.append(
            f"当前价虽跌破成本防守参考 {structure_stop_price:.2f}，但没有预期证伪、"
            "可靠量价破位或明确盘前计划共振；该参考不单独计入卖出分。"
        )
    if time_stop_reasons and vwap_reliable:
        add_risk("volume_price", 2)
        evidence.extend(time_stop_reasons)
        invalid_conditions.append("时间止损触发后，必须看到真实VWAP修复或重新回封才允许恢复计划。")
    if vwap and vwap_reliable:
        if current < vwap:
            add_risk("volume_price", 1)
            evidence.append(f"当前价 {current:.2f} 跌破真实分钟VWAP {vwap:.2f}。")
        else:
            counter_evidence.append(f"当前仍在真实分钟VWAP {vwap:.2f} 上方。")
            add_positive("volume_price", 1)
    else:
        counter_evidence.append("缺少真实1分钟成交数据，VWAP 为估算缺口，不把该项作为确定性卖点。")
    if max_profit_pct >= 8 and profit_drawdown_pct >= 3:
        add_risk("profit_protection", 2)
        evidence.append("浮盈超过 8% 后出现明显回撤，优先保护利润。")
    elif max_profit_pct >= 5 and profit_drawdown_pct >= 3:
        add_risk("profit_protection", 1)
        evidence.append("浮盈超过 5% 后回撤超过 3 个百分点，进入减仓观察。")
    if current_profit_pct < 0 and max_profit_pct >= 5:
        add_risk("profit_protection", 3)
        evidence.append("曾有 5% 以上浮盈但当前转亏，触发 PROFIT_TO_LOSS_RISK。")
    if seesaw:
        risk_level = str(getattr(seesaw, "risk_level", "观察") or "观察")
        sector_state = str(getattr(seesaw, "signal", "") or risk_level)
        if risk_level == "高":
            add_risk("sector", 2)
        elif risk_level == "中高":
            add_risk("sector", 1)
        elif risk_level in {"低", "安全"}:
            counter_evidence.append("板块/订单流方向环境风险较低，暂未形成外部共振杀跌。")
            add_positive("sector", 1)
        sector_triggers = list(getattr(seesaw, "sector_ebb_trigger", []) or [])
        stock_triggers = list(getattr(seesaw, "stock_weakening_trigger", []) or [])
        profit_triggers = list(getattr(seesaw, "profit_drawdown_trigger", []) or [])
        evidence.extend([str(item) for item in (sector_triggers + stock_triggers + profit_triggers)[:5]])
        if not sector_triggers:
            counter_evidence.append("暂未确认所属板块进入持续退潮。")
    else:
        sector_state = "订单流跷跷板数据缺口"
        counter_evidence.append("未取得板块跷跷板数据，本次建议主要依据个股价格和利润保护。")
    if distribution_state:
        sector_state = f"{sector_state} · {distribution_state}"

    raw_negative_score = sum(risk_family_scores.values())
    protected_risk = risk_family_scores.get("hard_stop", 0) + risk_family_scores.get("structure", 0)
    offsettable_risk = max(0, raw_negative_score - protected_risk)
    positive_offset = min(2, sum(positive_family_scores.values()), offsettable_risk)
    negative_score = protected_risk + max(0, offsettable_risk - positive_offset)
    if positive_offset:
        counter_evidence.append(
            f"明确正向反证抵扣 {positive_offset} 分：原始风险 {raw_negative_score} 分，去重降级后 {negative_score} 分。"
        )

    state, action, reduce_ratio, level = _action_from_score(negative_score, hard_exit, current_profit_pct > 0)
    if hard_expectation_invalidation and not hard_exit:
        expected_low = _safe_float(getattr(expectation, "expected_open_low", 0))
        expected_high = _safe_float(getattr(expectation, "expected_open_high", 0))
        actual_open = _safe_float(getattr(expectation, "actual_open_pct", 0))
        state = "EXPECTATION_INVALIDATED"
        repair_confirmed = vwap_reliable and current >= vwap and volume_state in {"REPAIR_CONFIRMED", "VWAP_STRONG", "REVERSAL_CONFIRMED"}
        if now.time() < time(9, 35):
            action, reduce_ratio, level = "减仓25%", max(reduce_ratio, 0.25), "PROTECT"
            recovery_conditions.insert(0, "观察至09:35：若重新站回真实VWAP且量价修复，可保留观察仓；否则继续降仓。")
        elif repair_confirmed:
            action, reduce_ratio, level = "减仓25%", max(reduce_ratio, 0.25), "PROTECT"
            recovery_conditions.insert(0, "当前已出现VWAP修复，只保留验证仓；再次跌破则升级为减仓50%或退出。")
        else:
            action, reduce_ratio, level = "减仓50%", max(reduce_ratio, 0.50), "REDUCE"
        evidence.insert(0, f"预期证伪：合理开盘 {expected_low:+.2f}%～{expected_high:+.2f}%，实际竞价/开盘 {actual_open:+.2f}%，预期差 {expectation_gap_score}。")
        invalid_conditions.insert(0, "真实竞价/开盘显著低于合理区间，未出现明确修复前必须先降低持仓风险。")
    if expectation_result in WEAK_EXPECTATION_RESULTS and volume_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"} and not hard_exit:
        state = "EXPECTATION_VOLUME_BREAKDOWN"
        # The same evidence must lead to the same action regardless of whether
        # floating P&L happens to be slightly positive or negative.
        action = "减仓50%"
        reduce_ratio = max(reduce_ratio, 0.50)
        level = "REDUCE"
    if volume_state == "REVERSAL_CONFIRMED" and not hard_exit:
        # A verified intraday reversal is new evidence.  It does not erase the
        # failed opening expectation and never authorises averaging down, but
        # it can supersede a stale low-point liquidation instruction.  Opening
        # invalidation or multiple independent non-price risks still require a
        # staged reduction into the rebound instead of being erased.
        persistent_reversal_risks = {
            family
            for family in ("expectation", "structure", "sector", "profit_protection")
            if risk_family_scores.get(family, 0) > 0
        }
        reversal_risk_persists = hard_expectation_invalidation or len(persistent_reversal_risks) >= 2
        if reversal_risk_persists:
            state = "REVERSAL_CONFIRMED_RISK_REDUCTION"
            action = "反转确认，利用反抽分批减仓25%"
            reduce_ratio = 0.25
            level = "PROTECT"
            evidence.append(
                "执行修正：V形修复已通过真实VWAP确认，禁止在低点清仓；但开盘预期证伪或多类风险仍在，"
                "利用反抽分批降低25%风险，而不是撤销全部风险结论。"
            )
        else:
            state = "REVERSAL_CONFIRMED_HOLD"
            action = "反转确认，暂缓减仓并禁止追高"
            reduce_ratio = 0.0
            level = "WATCH"
            evidence.append("执行修正：低点后的V形修复已经通过真实VWAP确认；未触发明确硬止损且无多类持续风险时，不在修复途中清仓。")
    if not vwap_reliable and action in {"减仓25%", "减仓50%", "只留观察仓", "全部退出"} and not hard_exit and not hard_expectation_invalidation:
        state = "DEGRADED_DATA_OBSERVATION"
        action = "观察但禁止加仓"
        reduce_ratio = 0.0
        level = "WATCH"
        evidence.append("数据降级：缺少真实1分钟VWAP，不输出确定性减仓、清仓或做T信号。")
        invalid_conditions.append("未恢复真实分钟成交数据前，系统建议只能作为观察提醒。")
    if reduce_ratio >= 0.75 and action == "减仓50%":
        action = "只留观察仓"
        level = "EXIT"

    prev_close = _safe_float(quote.get("prev_close")) or _safe_float(getattr(volume_price, "prev_close", 0))
    limit_down_price = _safe_float(quote.get("limit_down_price")) or (round(prev_close * 0.9, 2) if prev_close else 0.0)
    near_extreme_low = _near_intraday_extreme_low(current, low, high, prev_close, limit_down_price)
    if near_extreme_low and not hard_exit and reduce_ratio > 0:
        original_action = action
        original_ratio = reduce_ratio
        decisive_low_risk = bool(
            hard_expectation_invalidation
            or risk_family_scores.get("structure", 0) > 0
            or (
                risk_family_scores.get("expectation", 0) > 0
                and risk_family_scores.get("volume_price", 0) > 0
            )
            or len({family for family, score in risk_family_scores.items() if score > 0 and family != "profit_protection"}) >= 3
        )
        if decisive_low_risk:
            state = "EXTREME_LOW_STAGED_RISK_REDUCTION"
            action = "禁止低位追卖，首次有效反抽分批减仓25%"
            reduce_ratio = min(original_ratio, 0.25)
            level = "PROTECT"
            stop_status = (
                f"明确硬止损尚未实际触发（{hard_stop_price:.2f}）；"
                if hard_stop_price > 0
                else "盘前未冻结明确硬止损；"
            )
            evidence.append(
                f"执行时机门控：当前价接近日内极端低点，{stop_status}禁止直接按原建议{original_action}追卖；"
                f"但预期、量价或结构风险已形成独立共振，风险结论不撤销，改为首次有效反抽分批减仓{reduce_ratio * 100:.0f}%。"
            )
        else:
            state = "EXTREME_LOW_WAIT_REBOUND"
            action = "禁止追卖，等待反抽确认"
            reduce_ratio = 0.0
            level = "WATCH"
            evidence.append(
                f"执行时机门控：当前价接近日内极端低点，且明确硬止损尚未实际触发；"
                f"保留风险结论（原建议{original_action}、{original_ratio * 100:.0f}%），但禁止在低位追卖。"
            )
        recovery_conditions.insert(0, "等待价格脱离日内低点并反抽VWAP/固定结构位后，再按承接强弱执行；若新低持续且固定硬止损被触发则立即退出。")
    high_sell_signal, panic_sell_guard, contrarian_add_signal = _holding_execution_signals(
        db,
        holding,
        now=now,
        current=current,
        high=high,
        low=low,
        prev_close=prev_close,
        vwap=vwap,
        vwap_reliable=vwap_reliable,
        volume_state=volume_state,
        volume_price=volume_price,
        seesaw=seesaw,
        market_regime=market_regime,
        market_expansion_frozen=market_expansion_frozen,
        market_data_limited=market_data_limited,
        hard_exit=hard_exit,
        hard_stop_price=hard_stop_price,
        structure_stop_price=structure_stop_price,
        near_extreme_low=near_extreme_low,
        expectation_result=expectation_result,
        protection_level=protection_level,
        profit_drawdown_pct=profit_drawdown_pct,
    )
    if (
        high_sell_signal.status == "ACTIVE"
        and high_sell_signal.recommended_ratio > reduce_ratio
        and not hard_exit
    ):
        state = "HIGH_SELL_WINDOW"
        action = high_sell_signal.action
        reduce_ratio = high_sell_signal.recommended_ratio
        level = "REDUCE" if reduce_ratio >= 0.50 else "PROTECT"
        evidence.insert(0, "冲高兑现信号：" + "；".join(high_sell_signal.evidence[:3]))
        invalid_conditions.insert(0, "冲高兑现必须由计划压力位/早盘高点与至少两类走弱证据共同触发。")
        recovery_conditions.insert(0, high_sell_signal.recovery_conditions[0])
    evidence.append(
        f"动态决策依据：风险族 {risk_family_scores}，正向反证 {positive_family_scores}，"
        f"去重后风险积分 {negative_score}；预期差 {expectation_gap_score}；"
        f"量价状态 {volume_state}；当前价 {'已' if current <= structure_stop_price and structure_stop_price else '未'}破结构止损，"
        f"{'已' if hard_exit else '未'}触发硬止损。"
    )
    if hard_exit:
        evidence.append("全部退出并非由浮亏百分比单独触发，而是当前价已经触发硬止损。")
    elif reduce_ratio >= 0.75:
        evidence.append("只留观察仓：多项风险共振但尚未触发硬止损；仅在重新站回VWAP且预期修复时保留。")
    elif reduce_ratio > 0:
        evidence.append(f"分阶段降低风险：当前建议先降低 {reduce_ratio * 100:.0f}% 仓位，再按恢复/失效条件动态复核。")
    if current_profit_pct < 0 and state == "NORMAL_HOLD":
        state = "LOSS_OBSERVATION"
        action = "观察但禁止加仓"
    if market_expansion_frozen and reduce_ratio <= 0 and action == "继续持有":
        # The global gate changes permission to expand risk, not the holding's
        # sell conclusion.  Existing positions remain governed by their own
        # expectation, volume/price and frozen-stop evidence.
        state = "MARKET_GATE_HOLD" if state == "NORMAL_HOLD" else state
        action = "持有但禁止加仓/抄底"
        evidence.append("全市场闸门只冻结新增风险；未出现个股证伪或固定止损时，不在低位机械卖出。")
    t_forbidden = bool(
        hard_exit
        or state in {"EXIT_REQUIRED", "REDUCE_REQUIRED", "EXPECTATION_VOLUME_BREAKDOWN", "REVERSAL_CONFIRMED_HOLD", "REVERSAL_PENDING_HOLD"}
        or volume_state in {"REVERSAL_CONFIRMED", "REVERSAL_PENDING"}
        or current < structure_stop_price
        or (vwap_reliable and volume_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"})
        or expectation_result in WEAK_EXPECTATION_RESULTS
        or (seesaw and getattr(seesaw, "risk_level", "") in {"高", "中高"})
        or market_expansion_frozen
    )
    t_eligible = not t_forbidden and int(holding.quantity or 0) > 0 and current_profit_pct >= 0 and protection_level != "NONE"
    t_type = "POSITIVE_T" if t_eligible else "NO_T"
    if t_forbidden:
        if market_expansion_frozen:
            evidence.append("当前禁止做T：全市场扩仓闸门生效，即使个股暂未走弱，也禁止借做T实施抄底或变相加仓。")
        else:
            evidence.append("当前禁止做T：做T不能用于挽救已经证伪或需要降风险的交易。")
        t_type = "NO_T"
    recommended_position_ratio = max(0.0, position_ratio * (1 - reduce_ratio))
    sellable_quantity, today_buy_quantity, yesterday_quantity = _position_quantities(db, holding, _trade_date())
    evidence.append(f"T+1口径：当前持仓 {int(holding.quantity or 0)} 股，今日买入 {today_buy_quantity} 股，昨日可卖 {sellable_quantity} 股。")
    volume_price_state = volume_state
    data_quality = "realtime" if _is_realtime_note(str(quote.get("note") or "")) else "degraded" if quote else "manual"
    if quote and not vwap_reliable:
        data_quality = "degraded_vwap"
    data_time = str(quote.get("time") or quote.get("updated_at") or now.strftime("%H:%M:%S"))
    events = _build_events(
        holding,
        current,
        vwap,
        current_profit_pct,
        max_profit_pct,
        profit_drawdown_pct,
        seesaw,
        evidence,
        volume_price_state=volume_price_state,
        expectation_result=expectation_result,
        vwap_reliable=vwap_reliable,
        time_stop_reasons=time_stop_reasons,
        quote=quote,
        volume_price=volume_price,
        high_drawdown_pct=high_drawdown_pct,
    )
    if distribution_high:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "SECTOR_DISTRIBUTION_RISK",
            "severity": "critical",
            "value": float(sector_distribution.get("distribution_risk_score") or 0),
            "previous_value": float(sector_distribution.get("heat_score") or 0),
            "priority": 86,
            "group_key": "stock:sector-distribution-risk",
            "evidence": [
                f"{sector_distribution.get('name') or '所属板块'}：{distribution_state}",
                *list(sector_distribution.get("distribution_evidence") or [])[:3],
                "只冻结追涨、加仓和做T买回；必须叠加个股预期/量价/止损证据才执行卖出。",
            ],
        })
    if high_sell_signal.status == "ACTIVE":
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "HIGH_SELL_WINDOW",
            "severity": "critical" if high_sell_signal.recommended_ratio >= 0.50 else "warning",
            "value": round(current, 2),
            "previous_value": round(high, 2),
            "priority": 94 if high_sell_signal.recommended_ratio >= 0.50 else 82,
            "group_key": "stock:high-sell-window",
            "evidence": high_sell_signal.evidence + [high_sell_signal.action],
        })
    if panic_sell_guard.status == "ACTIVE":
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "PANIC_SELL_GUARD",
            "severity": "info",
            "value": round(current, 2),
            "previous_value": round(low, 2),
            "priority": 78,
            "group_key": "stock:panic-sell-guard",
            "evidence": panic_sell_guard.evidence + [panic_sell_guard.action],
        })
    if contrarian_add_signal.status == "ELIGIBLE":
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "CONTRARIAN_ADD_EVALUATION",
            "severity": "info",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 45,
            "group_key": "stock:contrarian-add-evaluation",
            "evidence": contrarian_add_signal.evidence + [contrarian_add_signal.action],
        })
    events.extend(_recovery_events(db, holding, volume_price_state, current, vwap))

    storage_now = _utc_now_naive()
    if previous_max_profit < max(high_profit_pct, current_profit_pct):
        maximum_profit_at = storage_now
    if previous_day_max < max(high_profit_pct, current_profit_pct):
        day_max_profit_at = storage_now
    snapshot = ProfitProtectionSnapshot(
        holding_id=int(holding.id),
        code=holding.code,
        captured_at=storage_now,
        current_profit_pct=round(current_profit_pct, 2),
        maximum_profit_pct=round(max_profit_pct, 2),
        profit_drawdown_pct=round(profit_drawdown_pct, 2),
        maximum_price=round(maximum_price, 2),
        maximum_profit_at=maximum_profit_at,
        day_max_profit_pct=round(day_max_profit_pct, 2),
        day_max_profit_at=day_max_profit_at,
        protection_level=protection_level,
        protection_floor=profit_protection_price,
        triggered=protection_level != "NONE",
        recommended_action=action,
    )
    target_key = _recommendation_target_key(holding)
    persisted_recommendation = (
        db.query(ActionRecommendation)
        .filter(
            ActionRecommendation.trade_date == _trade_date(),
            ActionRecommendation.target_key == target_key,
        )
        .order_by(ActionRecommendation.id.desc())
        .first()
    )
    if persisted_recommendation is None:
        # Compatibility for rows created before ``target_key`` existed.  The
        # first persisted refresh promotes the latest legacy row to the
        # canonical key instead of creating another recommendation episode.
        persisted_recommendation = (
            db.query(ActionRecommendation)
            .filter(
                ActionRecommendation.holding_id == int(holding.id),
                ActionRecommendation.trade_date == _trade_date(),
            )
            .order_by(ActionRecommendation.id.desc())
            .first()
        )
    if persisted_recommendation is None:
        recommendation = ActionRecommendation(
            trade_date=_trade_date(), target_key=target_key,
            holding_id=int(holding.id), code=holding.code, name=holding.name,
            created_at=now, updated_at=now,
        )
    elif persist:
        recommendation = persisted_recommendation
    else:
        # Pure calculations must not dirty the ORM identity already attached
        # to this request's session.  A transient copy keeps the current
        # recommendation/revision identity available to the response while
        # making every field update below side-effect free.
        recommendation = ActionRecommendation(
            id=persisted_recommendation.id,
            trade_date=persisted_recommendation.trade_date,
            target_key=persisted_recommendation.target_key,
            holding_id=persisted_recommendation.holding_id,
            code=persisted_recommendation.code,
            name=persisted_recommendation.name,
            created_at=persisted_recommendation.created_at,
            updated_at=persisted_recommendation.updated_at,
            current_revision_id=persisted_recommendation.current_revision_id,
            current_decision_hash=persisted_recommendation.current_decision_hash,
            acknowledged_at=persisted_recommendation.acknowledged_at,
            expires_at=persisted_recommendation.expires_at,
        )
    trigger_event_types = [str(event["event_type"]) for event in events]
    decision_hash = _material_decision_hash(
        level=level,
        state=state,
        action=action,
        recommended_ratio=reduce_ratio,
        trigger_events=trigger_event_types,
        invalid_conditions=invalid_conditions,
        recovery_conditions=recovery_conditions,
    )
    changed = str(recommendation.current_decision_hash or "") != decision_hash
    recommendation.target_key = target_key
    recommendation.updated_at = now
    recommendation.level = level
    recommendation.state = state
    recommendation.action = action
    recommendation.recommended_ratio = reduce_ratio
    recommendation.trigger_events_json = _json_dumps(trigger_event_types)
    recommendation.evidence_json = _json_dumps(evidence)
    recommendation.counter_evidence_json = _json_dumps(counter_evidence)
    recommendation.invalid_conditions_json = _json_dumps(invalid_conditions)
    recommendation.recovery_conditions_json = _json_dumps(recovery_conditions)
    recommendation.current_decision_hash = decision_hash
    recommendation.expires_at = now + timedelta(minutes=15)
    if changed:
        recommendation.acknowledged_at = None
    persisted_state = (
        db.query(PositionExecutionState)
        .filter(PositionExecutionState.holding_id == int(holding.id), PositionExecutionState.trade_date == _trade_date())
        .first()
    )
    created_state = persisted_state is None
    previous_state = "" if created_state else str(persisted_state.state or "")
    if persisted_state is None or not persist:
        state_row = PositionExecutionState(holding_id=int(holding.id), code=holding.code, name=holding.name, trade_date=_trade_date())
        if persisted_state is not None:
            state_row.id = persisted_state.id
    else:
        state_row = persisted_state
    state_row.state = state
    state_row.expectation_state = (
        expectation_result
        or ("EXPECTATION_INVALIDATED" if state == "EXIT_REQUIRED" else "SLIGHTLY_WEAKER" if reduce_ratio else "MATCHED")
    )
    state_row.volume_price_state = volume_price_state
    state_row.sector_state = sector_state
    state_row.current_quantity = int(holding.quantity or 0)
    state_row.sellable_quantity = sellable_quantity
    state_row.today_buy_quantity = today_buy_quantity
    state_row.yesterday_quantity = yesterday_quantity
    state_row.current_position_ratio = round(position_ratio, 4)
    state_row.recommended_position_ratio = round(recommended_position_ratio, 4)
    state_row.recommended_action = action
    state_row.recommended_reduce_ratio = reduce_ratio
    state_row.structure_stop_price = structure_stop_price
    state_row.hard_stop_price = hard_stop_price
    state_row.stop_source = stop_source
    state_row.stop_source_detail = stop_source_detail
    state_row.trailing_stop_price = trailing_stop_price
    state_row.profit_protection_price = profit_protection_price
    state_row.t_eligible = t_eligible
    state_row.t_type = t_type
    state_row.evidence_json = _json_dumps(evidence)
    state_row.counter_evidence_json = _json_dumps(counter_evidence)
    state_row.invalid_conditions_json = _json_dumps(invalid_conditions)
    state_row.recovery_conditions_json = _json_dumps(recovery_conditions)
    state_row.data_quality = data_quality
    state_row.data_time = data_time
    state_row.updated_at = now

    persisted_events: list[IntradayEvidenceEvent] = []
    if persist:
        db.add(snapshot)
        db.add(recommendation)
        db.flush()
        if changed:
            latest_revision = (
                db.query(ActionRecommendationRevision)
                .filter(ActionRecommendationRevision.recommendation_id == recommendation.id)
                .order_by(ActionRecommendationRevision.version.desc(), ActionRecommendationRevision.id.desc())
                .first()
            )
            if latest_revision is not None and latest_revision.effective_until is None:
                latest_revision.effective_until = now
                db.add(latest_revision)
            context = {
                "holding_id": int(holding.id),
                # Freeze the executable position facts with the immutable
                # decision.  Reading the mutable Holding row later cannot tell
                # us whether a 25% reduction was actually completed.
                "current_quantity": int(holding.quantity or 0),
                "sellable_quantity": int(sellable_quantity or 0),
                "recommended_sell_quantity": min(
                    int(sellable_quantity or 0),
                    max(0, int(round(float(sellable_quantity or 0) * float(reduce_ratio or 0)))),
                ),
                "expectation_snapshot_id": getattr(expectation, "id", None),
                "volume_price_snapshot_id": getattr(volume_price, "id", None),
                "expectation_result": expectation_result,
                "volume_price_state": volume_price_state,
                "sector_state": sector_state,
                "data_quality": data_quality,
                "data_time": data_time,
                "price": round(current, 4),
                "vwap": round(vwap, 4),
            }
            revision = ActionRecommendationRevision(
                recommendation_id=recommendation.id,
                previous_revision_id=latest_revision.id if latest_revision else None,
                version=(latest_revision.version + 1) if latest_revision else 1,
                decision_hash=decision_hash,
                level=recommendation.level, state=recommendation.state,
                action=recommendation.action, recommended_ratio=recommendation.recommended_ratio,
                trigger_events_json=recommendation.trigger_events_json,
                evidence_json=recommendation.evidence_json,
                counter_evidence_json=recommendation.counter_evidence_json,
                invalid_conditions_json=recommendation.invalid_conditions_json,
                recovery_conditions_json=recommendation.recovery_conditions_json,
                decision_context_json=_json_dumps(context),
                rule_version=EXECUTION_RULE_VERSION,
                created_at=now,
            )
            db.add(revision)
            db.flush()
            recommendation.current_revision_id = revision.id
            db.add(recommendation)
        if previous_state != state:
            history = PositionStateHistory(
                holding_id=int(holding.id),
                code=holding.code,
                name=holding.name,
                trade_date=_trade_date(),
                old_state=previous_state,
                new_state=state,
                captured_at=now,
                reason=action,
                evidence_json=_json_dumps((evidence + invalid_conditions)[:10]),
            )
            db.add(history)
        for event in _dedupe_events(db, events):
            row = IntradayEvidenceEvent(
                trade_date=_trade_date(),
                captured_at=event["captured_at"],
                scope=event["scope"],
                target_code=event["target_code"],
                target_name=event["target_name"],
                event_type=event["event_type"],
                severity=event["severity"],
                value=event["value"],
                previous_value=event["previous_value"],
                priority=int(event.get("priority") or 0),
                group_key=str(event.get("group_key") or ""),
                first_seen_at=event.get("first_seen_at"),
                last_seen_at=event.get("last_seen_at"),
                occurrence_count=int(event.get("occurrence_count") or 1),
                confirmed=bool(event.get("confirmed") or False),
                evidence_json=_json_dumps(event["evidence"]),
                recommendation_id=recommendation.id,
            )
            db.add(row)
            persisted_events.append(row)
        db.add(state_row)
        db.commit()
        db.refresh(state_row)
        db.refresh(snapshot)
        db.refresh(recommendation)
        for row in persisted_events:
            db.refresh(row)

    return _execution_state_out(
        state_row,
        snapshot,
        recommendation,
        persisted_events or events,
        db=db,
        execution_signals=(high_sell_signal, panic_sell_guard, contrarian_add_signal),
    )


def _execution_state_out(
    state: PositionExecutionState,
    snapshot: ProfitProtectionSnapshot,
    recommendation: ActionRecommendation,
    events: list[IntradayEvidenceEvent | dict[str, Any]],
    db: Session | None = None,
    execution_signals: tuple[
        HoldingExecutionSignalOut,
        HoldingExecutionSignalOut,
        HoldingExecutionSignalOut,
    ] | None = None,
) -> PositionExecutionStateOut:
    current_revision = None
    latest_feedback = None
    if db is not None and recommendation.id:
        if recommendation.current_revision_id:
            current_revision = db.get(ActionRecommendationRevision, recommendation.current_revision_id)
        if current_revision is None:
            current_revision = (
                db.query(ActionRecommendationRevision)
                .filter(ActionRecommendationRevision.recommendation_id == recommendation.id)
                .order_by(ActionRecommendationRevision.version.desc(), ActionRecommendationRevision.id.desc())
                .first()
            )
        feedback_query = db.query(RecommendationFeedback).filter(
            RecommendationFeedback.recommendation_id == recommendation.id,
        )
        if current_revision is not None:
            feedback_query = feedback_query.filter(
                RecommendationFeedback.recommendation_revision_id == current_revision.id,
            )
        latest_feedback = feedback_query.order_by(
            RecommendationFeedback.created_at.desc(),
            RecommendationFeedback.id.desc(),
        ).first()
    event_out: list[IntradayEvidenceEventOut] = []
    for event in events:
        if isinstance(event, dict):
            event_out.append(IntradayEvidenceEventOut(**event))
        else:
            event_out.append(
                IntradayEvidenceEventOut(
                    id=event.id,
                    captured_at=event.captured_at,
                    scope=event.scope,
                    target_code=event.target_code,
                    target_name=event.target_name,
                    event_type=event.event_type,
                    severity=event.severity,
                    value=event.value,
                    previous_value=event.previous_value,
                    priority=getattr(event, "priority", 0),
                    group_key=getattr(event, "group_key", ""),
                    first_seen_at=getattr(event, "first_seen_at", None),
                    last_seen_at=getattr(event, "last_seen_at", None),
                    occurrence_count=getattr(event, "occurrence_count", 1),
                    confirmed=bool(getattr(event, "confirmed", False)),
                    evidence=_json_list(event.evidence_json),
                )
            )
    high_sell_signal, panic_sell_guard, contrarian_add_signal = execution_signals or (None, None, None)
    return PositionExecutionStateOut(
        id=state.id,
        holding_id=state.holding_id,
        code=state.code,
        name=state.name,
        trade_date=state.trade_date,
        state=state.state,
        expectation_state=state.expectation_state,
        volume_price_state=state.volume_price_state,
        sector_state=state.sector_state,
        current_quantity=state.current_quantity,
        sellable_quantity=state.sellable_quantity,
        today_buy_quantity=state.today_buy_quantity,
        yesterday_quantity=getattr(state, "yesterday_quantity", state.sellable_quantity),
        current_position_ratio=state.current_position_ratio,
        recommended_position_ratio=state.recommended_position_ratio,
        recommended_action=state.recommended_action,
        recommended_reduce_ratio=state.recommended_reduce_ratio,
        structure_stop_price=state.structure_stop_price,
        hard_stop_price=state.hard_stop_price,
        stop_source=getattr(state, "stop_source", "fallback_candidate") or "fallback_candidate",
        stop_source_detail=getattr(state, "stop_source_detail", "") or "",
        trailing_stop_price=state.trailing_stop_price,
        profit_protection_price=state.profit_protection_price,
        t_eligible=state.t_eligible,
        t_type=state.t_type,
        evidence=_json_list(state.evidence_json),
        counter_evidence=_json_list(state.counter_evidence_json),
        invalid_conditions=_json_list(state.invalid_conditions_json),
        recovery_conditions=_json_list(state.recovery_conditions_json),
        events=event_out,
        recommendation=ActionRecommendationOut(
            id=recommendation.id,
            revision_id=current_revision.id if current_revision else None,
            revision_version=int(current_revision.version or 0) if current_revision else 0,
            decision_hash=str(current_revision.decision_hash or recommendation.current_decision_hash or "") if current_revision else str(recommendation.current_decision_hash or ""),
            trade_date=recommendation.trade_date,
            target_key=recommendation.target_key or "",
            holding_id=recommendation.holding_id,
            code=recommendation.code,
            name=recommendation.name,
            level=recommendation.level,
            state=recommendation.state,
            action=recommendation.action,
            recommended_ratio=recommendation.recommended_ratio,
            evidence=_json_list(recommendation.evidence_json),
            counter_evidence=_json_list(recommendation.counter_evidence_json),
            invalid_conditions=_json_list(recommendation.invalid_conditions_json),
            recovery_conditions=_json_list(recommendation.recovery_conditions_json),
            created_at=recommendation.created_at,
            updated_at=recommendation.updated_at,
            expires_at=recommendation.expires_at,
            acknowledged_at=recommendation.acknowledged_at,
            feedback_status=latest_feedback.status if latest_feedback else "",
        ),
        profit_snapshot=ProfitProtectionSnapshotOut(
            id=snapshot.id,
            holding_id=snapshot.holding_id,
            code=snapshot.code,
            captured_at=snapshot.captured_at,
            current_profit_pct=snapshot.current_profit_pct,
            maximum_profit_pct=snapshot.maximum_profit_pct,
            profit_drawdown_pct=snapshot.profit_drawdown_pct,
            maximum_price=snapshot.maximum_price,
            maximum_profit_at=getattr(snapshot, "maximum_profit_at", None),
            day_max_profit_pct=getattr(snapshot, "day_max_profit_pct", 0),
            day_max_profit_at=getattr(snapshot, "day_max_profit_at", None),
            protection_level=snapshot.protection_level,
            protection_floor=snapshot.protection_floor,
            triggered=snapshot.triggered,
            recommended_action=snapshot.recommended_action,
        ),
        state_history=_recent_state_history(db, state.holding_id) if db is not None and state.holding_id else [],
        high_sell_signal=high_sell_signal,
        panic_sell_guard=panic_sell_guard,
        contrarian_add_signal=contrarian_add_signal,
        data_quality=state.data_quality,
        data_time=state.data_time,
        updated_at=state.updated_at,
    )


def read_persisted_execution_state(
    db: Session,
    holding: Holding,
) -> PositionExecutionStateOut:
    """Return the collector's latest committed state without recomputation.

    HTTP GET routes use this function so page navigation cannot create profit
    snapshots, recommendation revisions or evidence events.  The scheduler and
    explicit collection POST remain the only normal writers.
    """

    today = _trade_date()
    state = (
        db.query(PositionExecutionState)
        .filter(
            PositionExecutionState.holding_id == int(holding.id),
            PositionExecutionState.trade_date == today,
        )
        .order_by(PositionExecutionState.updated_at.desc(), PositionExecutionState.id.desc())
        .first()
    )
    is_historical = False
    if state is None:
        # Weekends, pre-market and collector outages must not make a holding
        # disappear from the UI.  Fall back to the last committed session and
        # mark it stale in the response; never recompute or persist from GET.
        state = (
            db.query(PositionExecutionState)
            .filter(PositionExecutionState.holding_id == int(holding.id))
            .order_by(
                PositionExecutionState.trade_date.desc(),
                PositionExecutionState.updated_at.desc(),
                PositionExecutionState.id.desc(),
            )
            .first()
        )
        is_historical = state is not None

    if state is None:
        now = _shanghai_now_naive()
        quantity = int(holding.quantity or 0)
        current = float(holding.current_price or 0)
        cost = float(holding.cost_price or 0)
        profit_pct = ((current / cost - 1) * 100) if current > 0 and cost > 0 else 0.0
        placeholder_state = PositionExecutionState(
            holding_id=int(holding.id),
            code=holding.code,
            name=holding.name,
            trade_date=today,
            state="NO_SNAPSHOT",
            expectation_state="PENDING",
            volume_price_state="尚无采样",
            sector_state="尚无采样",
            current_quantity=quantity,
            sellable_quantity=quantity,
            today_buy_quantity=0,
            yesterday_quantity=quantity,
            current_position_ratio=0,
            recommended_position_ratio=0,
            recommended_action="等待首次盘中采样",
            recommended_reduce_ratio=0,
            structure_stop_price=0,
            hard_stop_price=0,
            stop_source="missing_snapshot",
            stop_source_detail="尚无持久化采样，GET 不会自动生成交易结论。",
            trailing_stop_price=0,
            profit_protection_price=0,
            t_eligible=False,
            t_type="NO_T",
            evidence_json=_json_dumps(["尚无盘中采样；请运行盘中采集器后刷新。"]),
            counter_evidence_json="[]",
            invalid_conditions_json=_json_dumps(["缺少真实盘中量价证据，不生成确定性操作建议。"]),
            recovery_conditions_json=_json_dumps(["采集器成功生成首个持久化快照。"]),
            data_quality="missing",
            data_time="尚无持久化盘中采样",
            updated_at=holding.updated_at or now,
        )
        placeholder_recommendation = ActionRecommendation(
            trade_date=today,
            target_key=_recommendation_target_key(holding),
            holding_id=int(holding.id),
            code=holding.code,
            name=holding.name,
            created_at=holding.updated_at or now,
            updated_at=holding.updated_at or now,
            level="INFO",
            state="NO_SNAPSHOT",
            action="等待首次盘中采样",
            recommended_ratio=0,
            trigger_events_json="[]",
            evidence_json=placeholder_state.evidence_json,
            counter_evidence_json="[]",
            invalid_conditions_json=placeholder_state.invalid_conditions_json,
            recovery_conditions_json=placeholder_state.recovery_conditions_json,
        )
        placeholder_snapshot = ProfitProtectionSnapshot(
            holding_id=int(holding.id),
            code=holding.code,
            captured_at=holding.updated_at or now,
            current_profit_pct=round(profit_pct, 2),
            maximum_profit_pct=round(profit_pct, 2),
            profit_drawdown_pct=0,
            maximum_price=current,
            protection_level="NONE",
            protection_floor=0,
            triggered=False,
            recommended_action="等待首次盘中采样",
        )
        return _execution_state_out(
            placeholder_state,
            placeholder_snapshot,
            placeholder_recommendation,
            [],
            db=db,
        )

    trade_date = state.trade_date
    recommendation = (
        db.query(ActionRecommendation)
        .filter(
            ActionRecommendation.trade_date == trade_date,
            ActionRecommendation.target_key == _recommendation_target_key(holding),
        )
        .order_by(ActionRecommendation.updated_at.desc(), ActionRecommendation.id.desc())
        .first()
    )
    if recommendation is None:
        recommendation = (
            db.query(ActionRecommendation)
            .filter(
                ActionRecommendation.trade_date == trade_date,
                ActionRecommendation.holding_id == int(holding.id),
            )
            .order_by(ActionRecommendation.updated_at.desc(), ActionRecommendation.id.desc())
            .first()
        )
    state_snapshot_cutoff = _shanghai_naive_to_utc_naive(state.updated_at)
    snapshot = (
        db.query(ProfitProtectionSnapshot)
        .filter(
            ProfitProtectionSnapshot.holding_id == int(holding.id),
            ProfitProtectionSnapshot.captured_at <= state_snapshot_cutoff,
        )
        .order_by(ProfitProtectionSnapshot.captured_at.desc(), ProfitProtectionSnapshot.id.desc())
        .first()
    )
    if snapshot is None:
        snapshot = (
            db.query(ProfitProtectionSnapshot)
            .filter(ProfitProtectionSnapshot.holding_id == int(holding.id))
            .order_by(ProfitProtectionSnapshot.captured_at.desc(), ProfitProtectionSnapshot.id.desc())
            .first()
        )
    if recommendation is None:
        recommendation = ActionRecommendation(
            trade_date=trade_date,
            target_key=_recommendation_target_key(holding),
            holding_id=int(holding.id),
            code=holding.code,
            name=holding.name,
            created_at=state.updated_at,
            updated_at=state.updated_at,
            level="INFO",
            state=state.state,
            action=state.recommended_action,
            recommended_ratio=state.recommended_reduce_ratio,
            trigger_events_json="[]",
            evidence_json=state.evidence_json,
            counter_evidence_json=state.counter_evidence_json,
            invalid_conditions_json=state.invalid_conditions_json,
            recovery_conditions_json=state.recovery_conditions_json,
        )
    if snapshot is None:
        snapshot = ProfitProtectionSnapshot(
            holding_id=int(holding.id),
            code=holding.code,
            captured_at=state.updated_at,
            current_profit_pct=0,
            maximum_profit_pct=0,
            profit_drawdown_pct=0,
            maximum_price=float(holding.current_price or 0),
            protection_level="NONE",
            protection_floor=0,
            triggered=False,
            recommended_action=state.recommended_action,
        )
    events = (
        db.query(IntradayEvidenceEvent)
        .filter(
            IntradayEvidenceEvent.trade_date == trade_date,
            IntradayEvidenceEvent.target_code == holding.code,
        )
        .order_by(IntradayEvidenceEvent.captured_at.desc(), IntradayEvidenceEvent.id.desc())
        .limit(20)
        .all()
    )
    output = _execution_state_out(state, snapshot, recommendation, events, db=db)
    if is_historical:
        history_label = f"历史快照 {trade_date}"
        output = output.model_copy(update={
            "data_quality": "stale",
            "data_time": f"{history_label} · {state.data_time or '非当前交易日'}",
        })
    return output


def read_persisted_execution_states(
    db: Session,
    holdings: list[Holding],
) -> list[PositionExecutionStateOut]:
    return [read_persisted_execution_state(db, holding) for holding in holdings]


def build_execution_states(db: Session, holdings: list[Holding], force_refresh: bool = False) -> list[PositionExecutionStateOut]:
    quotes = _latest_quotes(holdings)
    global_cues = _load_global_market_snapshot(force_refresh=force_refresh) if holdings else None
    seesaw_by_code: dict[str, Any] = {}
    try:
        seesaw = _market_seesaw_monitor(holdings, force_refresh=force_refresh)
        seesaw_by_code = {_normalize_code(item.code): item for item in seesaw.holding_alerts}
    except Exception:
        seesaw_by_code = {}
    return [
        build_position_execution_state(
            db,
            holding,
            quote=_quote_for_holding(holding, quotes),
            seesaw=seesaw_by_code.get(_normalize_code(holding.code)),
            global_cues=global_cues,
        )
        for holding in holdings
    ]
