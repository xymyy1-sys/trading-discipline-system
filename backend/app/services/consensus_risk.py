from typing import Any

from app.schemas.trading import ConsensusRiskOut, ExpectationSnapshotOut, VolumePriceSnapshotOut


def build_consensus_risk(
    quote: dict[str, Any],
    expectation: ExpectationSnapshotOut,
    volume: VolumePriceSnapshotOut,
    history: dict[str, float],
) -> ConsensusRiskOut:
    factors: list[str] = []
    counter: list[str] = []
    score = 0
    return_2d = float(history.get("return_2d") or 0)
    return_5d = float(history.get("return_5d") or 0)
    open_pct = float(expectation.actual_open_pct or 0)

    if return_2d >= 12:
        score += 25
        factors.append(f"近2日累计涨幅 {return_2d:.1f}%，获利盘集中度较高。")
    elif return_5d >= 15:
        score += 15
        factors.append(f"近5日累计涨幅 {return_5d:.1f}%，存在短期兑现压力。")
    else:
        counter.append("近期累计涨幅未达到一致性过热阈值。")

    if open_pct > expectation.expected_open_high:
        score += 20
        factors.append(f"开盘涨幅 {open_pct:.1f}% 超过合理上沿 {expectation.expected_open_high:.1f}%。")
    else:
        counter.append("开盘未显著超过盘前合理区间。")

    if volume.high_drawdown >= 5:
        score += 20
        factors.append(f"日内高点回撤 {volume.high_drawdown:.1f}%，一致性承接减弱。")
    if volume.attack_amount > 0 and volume.pullback_amount_ratio >= 80:
        score += 20
        factors.append(f"回落段成交额达到上攻段的 {volume.pullback_amount_ratio:.0f}%。")
    if volume.vwap_reliable and volume.price_vs_vwap < 0:
        score += 15
        factors.append(f"价格位于真实 VWAP 下方 {abs(volume.price_vs_vwap):.1f}%。")
    elif volume.vwap_reliable:
        counter.append("价格仍在真实 VWAP 上方。")
    else:
        counter.append("真实分钟成交额不足，VWAP 证据不参与一致性定级。")

    available = bool(history) and bool(quote)
    if not available:
        return ConsensusRiskOut(level="UNKNOWN", score=0, data_complete=False, factors=[], counter_evidence=counter, actions=["等待真实日线和盘中量价数据，不追涨。"])
    level = "HIGH" if score >= 60 else "MEDIUM" if score >= 35 else "LOW"
    actions = {
        "HIGH": ["禁止追涨和加仓。", "已有持仓进入利润保护，观察能否放量收回 VWAP。"],
        "MEDIUM": ["等待换手和量价确认，不在开盘一致阶段追涨。"],
        "LOW": ["一致性风险暂低，仍按原交易剧本和结构止损执行。"],
    }[level]
    return ConsensusRiskOut(level=level, score=min(score, 100), data_complete=True, factors=factors, counter_evidence=counter, actions=actions)
