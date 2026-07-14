from __future__ import annotations

"""Observable expectation-crowding and reflexivity scenario engine.

The engine deliberately does not infer an operator's or a group of investors'
intent.  It converts objective market/price/volume observations into competing,
falsifiable scenarios.  The returned objects are plain dictionaries so they can
be passed directly to a future Pydantic response model and cached as JSON.
"""

from collections.abc import Mapping
from typing import Any


METHODOLOGY_NOTE = (
    "本模块识别的是价格、量能、市场宽度和资金行为所呈现的预期拥挤代理，"
    "不是对主力、散户或任何参与者真实意图的判断；场景必须由后续数据验证或证伪。"
)


MARKET_REQUIRED_FIELDS = {
    "advance_ratio": "上涨家数占比",
    "index_change_pct": "指数涨跌幅",
    "index_vwap_deviation_pct": "指数相对分时均价偏离",
    "market_main_net_inflow_yi": "全市场资金净流入",
    "positive_sector_ratio": "上涨/净流入板块占比",
    "low_rebound_pct": "指数距日内低点反弹",
    "high_drawdown_pct": "指数距日内高点回撤",
    "volume_ratio_5d": "成交额/5日均额",
}

STOCK_REQUIRED_FIELDS = {
    "expectation_gap_score": "预期差",
    "vwap_deviation_pct": "股价相对分时均价偏离",
    "change_pct": "个股涨跌幅",
    "low_rebound_pct": "个股距日内低点反弹",
    "high_drawdown_pct": "个股距日内高点回撤",
    "volume_ratio": "个股量比/量能比",
    "sector_relative_strength_pct": "个股相对板块强弱",
}

# A scenario may only become the current deterministic conclusion when its own
# defining observations are present.  The broader field sets above still drive
# completeness reporting, while these per-path gates prevent a high base score
# from winning on unrelated evidence.
MARKET_SCENARIO_REQUIRED_FIELDS = {
    "REBOUND_ABSORPTION": (
        "low_rebound_pct",
        "index_vwap_deviation_pct",
        "advance_ratio",
        "main_net_inflow_change_yi",
    ),
    "NO_REBOUND_LIQUIDATION": (
        "advance_ratio",
        "index_change_pct",
        "index_vwap_deviation_pct",
        "low_rebound_pct",
    ),
    "REBOUND_FAILURE_SUPPLY": (
        "low_rebound_pct",
        "index_vwap_deviation_pct",
        "high_drawdown_pct",
    ),
    "UPSIDE_SURPRISE_REPAIR": (
        "index_change_pct",
        "index_vwap_deviation_pct",
        "advance_ratio",
        "volume_ratio_5d",
    ),
}

STOCK_SCENARIO_REQUIRED_FIELDS = {
    "REBOUND_ABSORPTION": (
        "low_rebound_pct",
        "vwap_deviation_pct",
        "volume_ratio",
    ),
    "NO_REBOUND_LIQUIDATION": (
        "expectation_gap_score",
        "vwap_deviation_pct",
        "low_rebound_pct",
    ),
    "REBOUND_FAILURE_SUPPLY": (
        "low_rebound_pct",
        "vwap_deviation_pct",
        "high_drawdown_pct",
        "volume_ratio",
    ),
    "UPSIDE_SURPRISE_REPAIR": (
        "expectation_gap_score",
        "vwap_deviation_pct",
        "change_pct",
        "volume_ratio",
    ),
}

MARKET_FIELD_LABELS = {
    **MARKET_REQUIRED_FIELDS,
    "main_net_inflow_change_yi": "资金净流向较前一快照变化",
}


def _number(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value in (None, "", "-", "--") or isinstance(value, bool):
            continue
        try:
            result = float(value)
        except (TypeError, ValueError):
            continue
        if result == result and abs(result) != float("inf"):
            return result
    return None


def _boolean(payload: Mapping[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    return None


def _ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100 if abs(value) > 1 else value


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 1)


def _fmt(
    value: float | None,
    digits: int = 2,
    *,
    signed: bool = False,
    scale: float = 1.0,
    absolute: bool = False,
) -> str:
    """Format optional observations without evaluating absent values."""
    if value is None:
        return "--"
    number = value * scale
    if absolute:
        number = abs(number)
    sign = "+" if signed else ""
    return format(number, f"{sign}.{digits}f")


def _add(condition: bool, score: float, evidence: list[str], text: str) -> float:
    if condition:
        evidence.append(text)
        return score
    return 0.0


def _scenario(
    code: str,
    label: str,
    score: float,
    evidence: list[str],
    counter_evidence: list[str],
    allowed_actions: list[str],
    forbidden_actions: list[str],
    next_validation_points: list[str],
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        # This is a deterministic rule-match score, not a probability.
        "match_score": _clip(score),
        "evidence": evidence,
        "counter_evidence": counter_evidence,
        "allowed_actions": allowed_actions,
        "forbidden_actions": forbidden_actions,
        "next_validation_points": next_validation_points,
    }


def _market_opening_gap(metrics: Mapping[str, Any]) -> float | None:
    direct = _number(metrics, "opening_expectation_gap", "expectation_gap_score")
    if direct is not None:
        # Existing expectation-gap scores commonly use roughly +/-20 as one
        # strong regime step.  Preserve the sign while normalising the impact.
        return max(-1.5, min(1.5, direct / 20))
    actual = _number(metrics, "actual_open_pct", "opening_change_pct")
    low = _number(metrics, "expected_open_low")
    high = _number(metrics, "expected_open_high")
    if actual is None or low is None or high is None:
        return None
    if actual < low:
        return max(-1.5, (actual - low) / 3)
    if actual > high:
        return min(1.5, (actual - high) / 3)
    return 0.0


def _crowding_label(score: float, bullish: bool = False) -> str:
    if score < 30:
        return "拥挤不明显"
    if score < 55:
        return "中度追涨拥挤" if bullish else "中度抛压拥挤"
    if score < 75:
        return "较高追涨拥挤" if bullish else "较高抛压拥挤"
    return "极高追涨拥挤" if bullish else "极高抛压拥挤"


def _confidence(available: int, required: int, first: float, second: float) -> float:
    completeness = available / max(1, required)
    separation = max(0.0, min(1.0, (first - second) / 35))
    return round(min(0.96, 0.35 + completeness * 0.45 + separation * 0.16), 2)


def _current_fields(scenario: Mapping[str, Any], enough_data: bool) -> dict[str, Any]:
    if not enough_data:
        return {
            "current_evidence": [],
            "current_counter_evidence": [],
            "allowed_actions": ["补齐关键行情证据后再判断"],
            "forbidden_actions": ["在数据缺口下生成确定性操作结论"],
            "next_validation_points": ["补齐缺失字段", "等待下一有效行情快照"],
        }
    return {
        "current_evidence": list(scenario["evidence"]),
        "current_counter_evidence": list(scenario["counter_evidence"]),
        "allowed_actions": list(scenario["allowed_actions"]),
        "forbidden_actions": list(scenario["forbidden_actions"]),
        "next_validation_points": list(scenario["next_validation_points"]),
    }


def _required_missing(
    scenario_code: str,
    normalized: Mapping[str, Any],
    requirements: Mapping[str, tuple[str, ...]],
    labels: Mapping[str, str],
) -> list[str]:
    """Return human-readable missing core evidence for the leading scenario."""
    return [
        labels.get(key, key)
        for key in requirements.get(scenario_code, ())
        if normalized.get(key) is None
    ]


def analyze_market_reflexivity(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Build competing market-level scenarios from objective observations."""
    advance = _ratio(_number(metrics, "advance_ratio", "up_ratio"))
    index_change = _number(metrics, "index_change_pct", "index_composite_change_pct")
    vwap = _number(metrics, "index_vwap_deviation_pct", "vwap_deviation_pct")
    if vwap is None:
        above = _number(metrics, "index_above_vwap_ratio")
        if above is None:
            count = _number(metrics, "index_above_vwap_count")
            valid = _number(metrics, "index_valid_count")
            above = count / valid if count is not None and valid else None
        above = _ratio(above)
        vwap = None if above is None else (above - 0.5) * 2
    main_flow = _number(metrics, "market_main_net_inflow_yi", "main_net_inflow_yi")
    flow_change = _number(metrics, "main_net_inflow_change_yi", "market_flow_change_yi")
    sectors = _ratio(_number(metrics, "positive_sector_ratio"))
    rebound = _number(metrics, "low_rebound_pct", "index_low_rebound_pct")
    drawdown = _number(metrics, "high_drawdown_pct", "index_high_drawdown_pct")
    volume_ratio = _number(metrics, "volume_ratio_5d", "turnover_ratio_5d")
    limit_up = _number(metrics, "limit_up_count")
    limit_down = _number(metrics, "limit_down_count")
    opening_gap = _market_opening_gap(metrics)
    index_signal_count = _number(metrics, "index_signal_count")
    index_consistency = _ratio(_number(metrics, "index_signal_consistency_ratio"))

    normalized = {
        "advance_ratio": advance,
        "index_change_pct": index_change,
        "index_vwap_deviation_pct": vwap,
        "market_main_net_inflow_yi": main_flow,
        "positive_sector_ratio": sectors,
        "low_rebound_pct": rebound,
        "high_drawdown_pct": drawdown,
        "volume_ratio_5d": volume_ratio,
        "main_net_inflow_change_yi": flow_change,
    }
    missing = [label for key, label in MARKET_REQUIRED_FIELDS.items() if normalized[key] is None]

    sell_crowding = 0.0
    sell_crowding += 24 if advance is not None and advance <= 0.20 else 12 if advance is not None and advance <= 0.35 else 0
    sell_crowding += 18 if index_change is not None and index_change <= -2 else 9 if index_change is not None and index_change <= -1 else 0
    sell_crowding += 16 if main_flow is not None and main_flow <= -500 else 8 if main_flow is not None and main_flow < 0 else 0
    sell_crowding += 14 if sectors is not None and sectors <= 0.2 else 7 if sectors is not None and sectors <= 0.35 else 0
    sell_crowding += 14 if limit_down is not None and limit_up is not None and limit_down >= max(20, limit_up * 2) else 0
    sell_crowding += 8 if vwap is not None and vwap <= -0.5 else 0
    long_crowding = 0.0
    long_crowding += 24 if advance is not None and advance >= 0.75 else 10 if advance is not None and advance >= 0.62 else 0
    long_crowding += 18 if index_change is not None and index_change >= 2 else 8 if index_change is not None and index_change >= 1 else 0
    long_crowding += 16 if main_flow is not None and main_flow >= 500 else 8 if main_flow is not None and main_flow > 0 else 0
    long_crowding += 14 if sectors is not None and sectors >= 0.75 else 7 if sectors is not None and sectors >= 0.6 else 0
    long_crowding += 12 if vwap is not None and vwap >= 0.8 else 0
    long_crowding += 10 if opening_gap is not None and opening_gap >= 0.5 else 0

    scenarios: list[dict[str, Any]] = []

    evidence: list[str] = []
    counter: list[str] = []
    score = 18.0
    score += _add(rebound is not None and rebound >= 1.0, 20, evidence, f"指数从日内低点反弹{_fmt(rebound)}%")
    score += _add(vwap is not None and vwap >= 0, 18, evidence, f"指数回到分时均价上方{_fmt(vwap, signed=True)}%")
    score += _add(flow_change is not None and flow_change > 0, 14, evidence, f"资金净流向较前一快照改善{_fmt(flow_change, 1, signed=True)}亿元")
    score += _add(advance is not None and advance >= 0.45, 14, evidence, f"上涨家数占比修复至{_fmt(advance, 1, scale=100)}%")
    score += _add(sectors is not None and sectors >= 0.45, 12, evidence, f"正向板块占比修复至{_fmt(sectors, 1, scale=100)}%")
    if vwap is not None and vwap < 0:
        counter.append(f"指数仍在分时均价下方{abs(vwap):.2f}%")
    if main_flow is not None and main_flow < 0:
        counter.append(f"全市场资金仍净流出{abs(main_flow):.1f}亿元")
    scenarios.append(_scenario(
        "REBOUND_ABSORPTION", "反弹出现有效承接", score, evidence, counter,
        ["等待指数回踩分时均价不破后再提高持仓容忍度", "只跟踪与指数、板块共振且强于板块的标的"],
        ["首次脉冲即追高", "仅凭一根反弹K线认定反转"],
        ["下一次回踩能否守住指数分时均价", "上涨家数与正向板块占比能否连续两个快照改善", "资金净流出是否继续收窄或翻正"],
    ))

    evidence, counter = [], []
    score = 18.0
    score += _add(advance is not None and advance <= 0.3, 20, evidence, f"上涨家数占比仅{_fmt(advance, 1, scale=100)}%")
    score += _add(index_change is not None and index_change <= -1, 16, evidence, f"指数下跌{_fmt(index_change, absolute=True)}%")
    score += _add(vwap is not None and vwap < 0, 16, evidence, f"指数位于分时均价下方{_fmt(vwap, absolute=True)}%")
    score += _add(rebound is not None and rebound < 0.6, 13, evidence, f"距日内低点反弹仅{_fmt(rebound)}%")
    score += _add(main_flow is not None and main_flow < 0, 10, evidence, f"全市场资金净流出{_fmt(main_flow, 1, absolute=True)}亿元")
    score += _add(sectors is not None and sectors <= 0.3, 10, evidence, f"正向板块占比仅{_fmt(sectors, 1, scale=100)}%")
    if rebound is not None and rebound >= 1:
        score -= 20
        counter.append(f"指数已从低点反弹{rebound:.2f}%，不再符合“无反弹”的严格定义")
    if flow_change is not None and flow_change > 0:
        counter.append(f"资金净流向已改善{flow_change:+.1f}亿元")
    scenarios.append(_scenario(
        "NO_REBOUND_LIQUIDATION", "无有效反弹、抛压继续释放", score, evidence, counter,
        ["暂停新开仓与补仓", "只执行已预先定义且被真实价格触发的硬止损", "等待首次放量回收分时均价"],
        ["下跌途中凭主观估值抄底", "没有硬止损依据时在日内低点附近恐慌追卖", "用单一个股反弹替代全市场确认"],
        ["指数能否从低点反弹并回收分时均价", "上涨家数占比能否脱离极低区", "跌停家数是否下降且资金净流出收窄"],
    ))

    evidence, counter = [], []
    score = 16.0
    score += _add(rebound is not None and rebound >= 0.7, 15, evidence, f"盘中曾从低点反弹{_fmt(rebound)}%")
    score += _add(vwap is not None and vwap < 0, 18, evidence, f"反弹后仍未站回分时均价，偏离{_fmt(vwap, signed=True)}%")
    score += _add(drawdown is not None and drawdown >= 1.2, 17, evidence, f"距日内高点回撤{_fmt(drawdown)}%")
    score += _add(flow_change is not None and flow_change <= 0, 12, evidence, "反弹过程中资金净流向未改善")
    score += _add(advance is not None and advance < 0.4, 12, evidence, f"上涨家数占比仍仅{_fmt(advance, 1, scale=100)}%")
    if vwap is not None and vwap >= 0:
        counter.append("指数已站回分时均价")
    if sectors is not None and sectors >= 0.5:
        counter.append(f"正向板块占比达到{sectors:.1%}")
    scenarios.append(_scenario(
        "REBOUND_FAILURE_SUPPLY", "反弹失败、上方抛压重新占优", score, evidence, counter,
        ["反抽不能回收分时均价时分批降低风险", "优先处理弱于指数和所属板块的持仓"],
        ["反抽时无差别追涨", "把缩量反抽视为趋势反转", "在首次回落前一次性做满仓位"],
        ["二次回升能否放量站回分时均价", "回落是否跌破前低", "板块相对强度和资金流能否转正"],
    ))

    evidence, counter = [], []
    score = 14.0
    score += _add(opening_gap is not None and opening_gap >= 0.35, 18, evidence, "开盘/竞价表现高于基准预期")
    score += _add(index_change is not None and index_change > 0, 14, evidence, f"指数上涨{_fmt(index_change)}%")
    score += _add(vwap is not None and vwap >= 0.3, 17, evidence, f"指数站上分时均价{_fmt(vwap, signed=True)}%")
    score += _add(advance is not None and advance >= 0.6, 16, evidence, f"上涨家数占比扩散至{_fmt(advance, 1, scale=100)}%")
    score += _add(main_flow is not None and main_flow > 0, 12, evidence, f"全市场资金净流入{_fmt(main_flow, 1)}亿元")
    score += _add(sectors is not None and sectors >= 0.6, 11, evidence, f"正向板块占比{_fmt(sectors, 1, scale=100)}%")
    if volume_ratio is not None and volume_ratio < 0.9:
        counter.append(f"成交额仅为5日均额的{volume_ratio:.2f}倍，修复量能不足")
    if drawdown is not None and drawdown >= 1.5:
        counter.append(f"距日内高点已回撤{drawdown:.2f}%")
    scenarios.append(_scenario(
        "UPSIDE_SURPRISE_REPAIR", "超预期修复并扩散", score, evidence, counter,
        ["持有已确认的强势标的并用分时均价跟踪", "回踩不破且板块继续扩散时维持计划仓位"],
        ["高开脉冲时无量追涨", "把外围上涨直接当作A股买入信号", "忽略高位拥挤和盈亏比"],
        ["首次回踩是否缩量且不破分时均价", "成交额和上涨家数能否同步扩张", "领涨板块是否出现后排跟随而非仅少数权重拉升"],
    ))

    scenarios.sort(key=lambda item: item["match_score"], reverse=True)
    if index_signal_count is not None and index_signal_count >= 2 and index_consistency is not None:
        consistency_text = (
            f"{int(index_signal_count)}个主要指数涨跌方向一致率"
            f"{_fmt(index_consistency, 0, scale=100)}%"
        )
        if index_consistency >= 2 / 3:
            scenarios[0]["evidence"].append(consistency_text)
        else:
            scenarios[0]["counter_evidence"].append(f"{consistency_text}，指数信号仍分化")
    available = len(MARKET_REQUIRED_FIELDS) - len(missing)
    confidence = _confidence(available, len(MARKET_REQUIRED_FIELDS), scenarios[0]["match_score"], scenarios[1]["match_score"])
    dominant_bullish = long_crowding > sell_crowding
    crowding_score = max(long_crowding, sell_crowding)
    scenario_missing = _required_missing(
        scenarios[0]["code"],
        normalized,
        MARKET_SCENARIO_REQUIRED_FIELDS,
        MARKET_FIELD_LABELS,
    )
    missing = list(dict.fromkeys([*missing, *scenario_missing]))
    enough_data = not scenario_missing
    result = {
        "level": "MARKET",
        "current_scenario": scenarios[0]["code"] if enough_data else "DATA_GAP",
        "current_scenario_label": scenarios[0]["label"] if enough_data else "证据不足，等待验证",
        "scenario_match_score": scenarios[0]["match_score"] if enough_data else None,
        "crowding": {
            "side": "LONG_CHASING" if dominant_bullish else "SELL_PRESSURE",
            "label": _crowding_label(crowding_score, dominant_bullish),
            "score": _clip(crowding_score),
        },
        "confidence": confidence,
        "missing_fields": missing,
        "scenarios": scenarios,
        "methodology_note": METHODOLOGY_NOTE,
    }
    result.update(_current_fields(scenarios[0], enough_data))
    return result


def _expectation_gap(metrics: Mapping[str, Any]) -> float | None:
    direct = _number(metrics, "expectation_gap_score", "opening_expectation_gap")
    if direct is not None:
        return direct
    actual = _number(metrics, "actual_open_pct", "opening_change_pct")
    low = _number(metrics, "expected_open_low")
    high = _number(metrics, "expected_open_high")
    if actual is None or low is None or high is None:
        return None
    if actual < low:
        return round((actual - low) * 5, 2)
    if actual > high:
        return round((actual - high) * 5, 2)
    return 0.0


def analyze_stock_reflexivity(
    metrics: Mapping[str, Any],
    market_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build stock-level scenarios and inherit the market execution gate."""
    market_context = market_context or {}
    gap = _expectation_gap(metrics)
    vwap = _number(metrics, "vwap_deviation_pct", "price_vs_vwap_pct", "price_vs_vwap")
    change = _number(metrics, "change_pct")
    rebound = _number(metrics, "low_rebound_pct")
    drawdown = _number(metrics, "high_drawdown_pct")
    volume = _number(metrics, "volume_ratio", "relative_volume")
    sector_rs = _number(metrics, "sector_relative_strength_pct", "relative_strength_pct")
    sector_flow = _number(metrics, "sector_net_inflow_yi")
    support_distance = _number(metrics, "support_distance_pct")
    hard_stop = _boolean(metrics, "hard_stop_triggered") is True
    market_scenario = str(market_context.get("current_scenario") or metrics.get("market_regime_code") or "")
    market_risk = market_scenario in {
        "NO_REBOUND_LIQUIDATION", "REBOUND_FAILURE_SUPPLY", "EXTREME_SHRINK_DECLINE",
        "VOLUME_SELL_OFF", "SHRINK_ROTATION", "UNKNOWN", "DATA_GAP",
    }
    normalized = {
        "expectation_gap_score": gap,
        "vwap_deviation_pct": vwap,
        "change_pct": change,
        "low_rebound_pct": rebound,
        "high_drawdown_pct": drawdown,
        "volume_ratio": volume,
        "sector_relative_strength_pct": sector_rs,
    }
    missing = [label for key, label in STOCK_REQUIRED_FIELDS.items() if normalized[key] is None]

    sell_crowding = 0.0
    sell_crowding += 22 if gap is not None and gap <= -15 else 10 if gap is not None and gap < 0 else 0
    sell_crowding += 18 if change is not None and change <= -7 else 9 if change is not None and change <= -3 else 0
    sell_crowding += 16 if vwap is not None and vwap <= -2 else 8 if vwap is not None and vwap < 0 else 0
    sell_crowding += 15 if drawdown is not None and drawdown >= 7 else 7 if drawdown is not None and drawdown >= 3 else 0
    sell_crowding += 12 if sector_rs is not None and sector_rs <= -2 else 0
    sell_crowding += 10 if market_risk else 0
    long_crowding = 0.0
    long_crowding += 22 if gap is not None and gap >= 15 else 10 if gap is not None and gap > 0 else 0
    long_crowding += 18 if change is not None and change >= 7 else 9 if change is not None and change >= 3 else 0
    long_crowding += 16 if vwap is not None and vwap >= 2 else 8 if vwap is not None and vwap > 0 else 0
    long_crowding += 12 if sector_rs is not None and sector_rs >= 2 else 0
    long_crowding += 10 if volume is not None and volume >= 1.8 else 0

    scenarios: list[dict[str, Any]] = []

    evidence: list[str] = []
    counter: list[str] = []
    score = 18.0
    score += _add(rebound is not None and rebound >= 2, 20, evidence, f"股价从日内低点反弹{_fmt(rebound)}%")
    score += _add(vwap is not None and vwap >= 0, 18, evidence, f"股价回到分时均价上方{_fmt(vwap, signed=True)}%")
    score += _add(sector_rs is not None and sector_rs >= 0, 13, evidence, f"相对所属板块强度{_fmt(sector_rs, signed=True)}%")
    score += _add(volume is not None and volume >= 1, 12, evidence, f"量能比{_fmt(volume)}，承接有量")
    score += _add(gap is not None and gap >= 0, 10, evidence, f"当前预期差{_fmt(gap, 1, signed=True)}")
    if market_risk:
        counter.append(f"全市场仍处于{market_scenario}风险状态")
    if sector_flow is not None and sector_flow < 0:
        counter.append(f"所属板块资金净流出{abs(sector_flow):.1f}亿元")
    scenarios.append(_scenario(
        "REBOUND_ABSORPTION", "反弹获得承接", score, evidence, counter,
        ["保留计划内仓位，观察回踩分时均价", "回踩缩量不破且板块同步转强时继续持有"],
        ["首次拉升追涨加仓", "尚未回收分时均价时把反抽认定为反转"],
        ["回踩分时均价是否缩量不破", "下一高点能否高于前一高点", "个股相对板块强度能否保持为正"],
    ))

    evidence, counter = [], []
    score = 18.0
    score += _add(gap is not None and gap < 0, 18, evidence, f"预期差为{_fmt(gap, 1, signed=True)}")
    score += _add(vwap is not None and vwap < 0, 17, evidence, f"股价位于分时均价下方{_fmt(vwap, absolute=True)}%")
    score += _add(rebound is not None and rebound < 1, 14, evidence, f"距日内低点反弹仅{_fmt(rebound)}%")
    score += _add(sector_rs is not None and sector_rs < 0, 12, evidence, f"弱于所属板块{_fmt(sector_rs, absolute=True)}%")
    score += _add(market_risk, 10, evidence, "全市场风险闸门处于防守状态")
    score += _add(support_distance is not None and support_distance < 0, 10, evidence, f"已跌破预定义支撑{_fmt(support_distance, absolute=True)}%")
    if volume is not None and volume < 0.8:
        counter.append(f"量能比仅{volume:.2f}，尚不能确认主动放量抛压")
    if hard_stop:
        counter.append("已触发预先定义的硬止损，执行纪律优先于等待反弹")
    allowed = ["若未触发硬止损，在日内低点附近等待一个反抽验证窗口", "反抽仍不能回收分时均价时再分批降风险"]
    forbidden = ["逆全市场风险闸门补仓", "仅因跌幅扩大而摊低成本"]
    if hard_stop:
        allowed.insert(0, "执行盘前已定义且被真实价格触发的硬止损")
    else:
        forbidden.append("没有硬止损依据时在日内低点附近情绪化清仓")
    scenarios.append(_scenario(
        "NO_REBOUND_LIQUIDATION", "无有效反弹、抛压延续", score, evidence, counter,
        allowed, forbidden,
        ["首次反抽能否站回分时均价", "日内低点是否被放量跌破", "所属板块和大盘是否先出现止跌承接"],
    ))

    evidence, counter = [], []
    score = 16.0
    score += _add(rebound is not None and rebound >= 1.5, 16, evidence, f"盘中曾从低点反弹{_fmt(rebound)}%")
    score += _add(vwap is not None and vwap < 0, 17, evidence, f"反弹后仍低于分时均价{_fmt(vwap, absolute=True)}%")
    score += _add(drawdown is not None and drawdown >= 4, 18, evidence, f"距日内高点回撤{_fmt(drawdown)}%")
    score += _add(volume is not None and volume >= 1.2, 12, evidence, f"量能比{_fmt(volume)}但价格未能维持")
    score += _add(sector_rs is not None and sector_rs < 0, 10, evidence, f"反弹后仍弱于板块{_fmt(sector_rs, absolute=True)}%")
    if vwap is not None and vwap >= 0:
        counter.append("股价已经回收分时均价")
    scenarios.append(_scenario(
        "REBOUND_FAILURE_SUPPLY", "反弹失败、上方抛压占优", score, evidence, counter,
        ["反抽失败且量价转弱时分批减仓", "先处理弱于板块且预期差为负的仓位"],
        ["反抽失败后立即补仓", "只因成本较高而拒绝执行风险计划"],
        ["二次反弹能否放量站回分时均价", "回落是否跌破前低", "板块转强时个股能否同步而非继续落后"],
    ))

    evidence, counter = [], []
    score = 14.0
    score += _add(gap is not None and gap >= 8, 20, evidence, f"实际表现高于基准预期，预期差{_fmt(gap, 1, signed=True)}")
    score += _add(vwap is not None and vwap >= 0.5, 17, evidence, f"股价站上分时均价{_fmt(vwap, signed=True)}%")
    score += _add(change is not None and change > 0, 13, evidence, f"股价上涨{_fmt(change)}%")
    score += _add(sector_rs is not None and sector_rs >= 1, 14, evidence, f"强于所属板块{_fmt(sector_rs, signed=True)}%")
    score += _add(volume is not None and 1 <= volume <= 2.5, 11, evidence, f"量能比{_fmt(volume)}，量价尚未极端")
    if market_risk:
        counter.append(f"全市场{market_scenario}仍限制个股仓位上限")
    if long_crowding >= 70:
        counter.append("追涨拥挤代理已偏高，强势不等于适合追价")
    scenarios.append(_scenario(
        "UPSIDE_SURPRISE_REPAIR", "超预期修复/弱转强候选", score, evidence, counter,
        ["已有仓位可持有并以分时均价作为动态验证线", "等待回踩不破、板块共振后再评估风险收益比"],
        ["在大盘风险闸门关闭时新增仓位", "高开或急拉时直接追价", "没有失效条件的主观格局"],
        ["回踩分时均价是否缩量不破", "所属板块资金流与相对强度能否同步改善", "下一高点突破时是否有成交量确认"],
    ))

    scenarios.sort(key=lambda item: item["match_score"], reverse=True)
    available = len(STOCK_REQUIRED_FIELDS) - len(missing)
    confidence = _confidence(available, len(STOCK_REQUIRED_FIELDS), scenarios[0]["match_score"], scenarios[1]["match_score"])
    dominant_bullish = long_crowding > sell_crowding
    crowding_score = max(long_crowding, sell_crowding)
    scenario_missing = _required_missing(
        scenarios[0]["code"],
        normalized,
        STOCK_SCENARIO_REQUIRED_FIELDS,
        STOCK_REQUIRED_FIELDS,
    )
    missing = list(dict.fromkeys([*missing, *scenario_missing]))
    enough_data = not scenario_missing
    result = {
        "level": "STOCK",
        "code": str(metrics.get("code") or ""),
        "name": str(metrics.get("name") or ""),
        "current_scenario": scenarios[0]["code"] if enough_data else "DATA_GAP",
        "current_scenario_label": scenarios[0]["label"] if enough_data else "证据不足，等待验证",
        "scenario_match_score": scenarios[0]["match_score"] if enough_data else None,
        "crowding": {
            "side": "LONG_CHASING" if dominant_bullish else "SELL_PRESSURE",
            "label": _crowding_label(crowding_score, dominant_bullish),
            "score": _clip(crowding_score),
        },
        "confidence": confidence,
        "market_gate": {
            "scenario": market_scenario or "UNKNOWN",
            "risk_off": market_risk,
            "new_position_allowed": not market_risk,
        },
        "hard_stop_triggered": hard_stop,
        "missing_fields": missing,
        "scenarios": scenarios,
        "methodology_note": METHODOLOGY_NOTE,
    }
    result.update(_current_fields(scenarios[0], enough_data))
    return result


class ReflexivityService:
    """Small facade for future route/background-job integration."""

    @staticmethod
    def analyze_market(metrics: Mapping[str, Any]) -> dict[str, Any]:
        return analyze_market_reflexivity(metrics)

    @staticmethod
    def analyze_stock(
        metrics: Mapping[str, Any],
        market_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return analyze_stock_reflexivity(metrics, market_context)
