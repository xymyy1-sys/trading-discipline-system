import json
import re
from collections import Counter, defaultdict
from types import SimpleNamespace
from typing import Any
import requests
from sqlalchemy.orm import Session
from app.core.trading_clock import shanghai_now_naive
from app.models.trading import Holding
from app.schemas.trading import MarketSeesawOut, SectorRotationItem, HoldingSeesawItem, SellPlanOut
from app.services.market_data import MarketDataProvider, _get_response_cache, _set_response_cache, _last_trading_day
from app.services.rules import profit_guard_price
from app.api.helpers.quotes import (
    _latest_a_share_quotes,
    _quote_lookup_code,
    _safe_float,
    _quote_code_candidates,
    _normalize_code,
    _code_hint,
    _estimated_vwap
)

market_provider = MarketDataProvider()

_THEME_RULES: list[dict[str, Any]] = [
    {
        "primary": "AI算力链",
        "tags": ["AI算力", "服务器", "数据中心", "液冷", "东数西算"],
        "keywords": ["算力", "服务器", "东数西算", "液冷", "云计算", "英伟达", "CPO", "光模块", "数据中心", "腾讯云", "人工智能", "AI", "铜连接", "高速连接器", "光通信"],
        "flow_aliases": ["AI算力", "算力概念", "服务器", "东数西算", "液冷概念", "云计算", "英伟达概念", "数据中心", "CPO", "光模块", "人工智能", "铜连接", "算力租赁"],
        "prefer_concept": True,
    },
    {
        "primary": "商业航天链",
        "tags": ["商业航天", "卫星互联网", "北斗导航", "航天军工"],
        "keywords": ["商业航天", "卫星互联网", "卫星导航", "北斗", "航天", "火箭", "空间站", "低空经济", "无人机", "军工航天", "国防军工", "军工信息化", "军民融合"],
        "flow_aliases": ["商业航天", "卫星互联网", "卫星导航", "北斗导航", "航天航空", "航天军工", "军工", "国防军工", "军工信息化", "低空经济", "无人机"],
        "prefer_concept": True,
    },
    {
        "primary": "半导体链",
        "tags": ["半导体", "芯片产业链", "集成电路"],
        "keywords": ["半导体", "芯片", "集成电路", "先进封装", "封测", "半导体设备", "光刻机", "存储芯片"],
        "flow_aliases": ["半导体", "芯片", "集成电路", "先进封装", "封装", "封测", "半导体设备", "光刻机", "存储芯片", "华为海思"],
        "preferred_industry_boards": [("90.BK1036", "半导体")],
    },
    {
        "primary": "机器人链",
        "tags": ["机器人", "人形机器人", "工业自动化"],
        "keywords": ["机器人", "人形机器人", "减速器", "工业母机", "机器视觉"],
        "flow_aliases": ["机器人", "人形机器人", "减速器", "工业母机", "机器视觉"],
        "prefer_concept": True,
    },
    {
        "primary": "医药链 / 创新药",
        "tags": ["医药", "创新药", "生物医药"],
        "keywords": ["创新药", "医药", "生物医药", "CRO", "医疗器械", "减肥药"],
        "flow_aliases": ["创新药", "医药", "生物医药", "CRO", "医疗器械", "减肥药"],
    },
    {
        "primary": "新能源链",
        "tags": ["新能源", "光伏", "锂电", "储能"],
        "keywords": ["新能源", "光伏", "锂电", "固态电池", "储能", "风电"],
        "flow_aliases": ["新能源", "光伏", "锂电池", "固态电池", "储能", "风电"],
    },
    {
        "primary": "化工材料链",
        "tags": ["化工", "新材料"],
        "keywords": ["化工", "塑料", "新材料", "化纤", "染料", "有机硅"],
        "flow_aliases": ["化工", "化工行业", "塑料", "新材料", "化纤", "有机硅"],
    },
    {
        "primary": "消费电子链",
        "tags": ["消费电子", "电子元件", "PCB"],
        "keywords": ["消费电子", "电子元件", "电子零部件", "PCB", "OLED", "华为概念", "小米概念"],
        "flow_aliases": ["消费电子", "电子元件", "电子零部件", "PCB", "OLED", "电子信息", "电子器件"],
    },
]


def _display_flow_signal(value: str | None) -> str:
    """Translate legacy provider-flow wording without changing stored/API keys."""
    return (
        str(value or "")
        .replace("主力净流入", "大单方向估算")
        .replace("主力资金", "大单方向估算")
        .replace("资金由净流出拐为净流入", "订单流方向由净流出拐为净流入")
        .replace("资金由净流入拐为净流出", "订单流方向由净流入拐为净流出")
        .replace("资金流入", "订单流方向流入")
        .replace("资金流出", "订单流方向流出")
        .replace("资金边际", "订单流方向边际")
        .replace("资金价格背离", "订单流与价格背离")
        .replace("资金转弱", "订单流方向转弱")
    )

def _market_seesaw_monitor(
    holdings: list[Holding],
    force_refresh: bool = False,
    *,
    cache_only: bool = False,
) -> MarketSeesawOut:
    if not holdings:
        return MarketSeesawOut(
            source="empty",
            updated_at=shanghai_now_naive(),
            market_mode="暂无持仓",
            summary="暂无持仓可监控。",
            inflow_targets=[],
            outflow_targets=[],
            holding_alerts=[],
            notes=["暂无持仓，跳过板块订单流算法外部抓取。"],
        )
    notes: list[str] = []
    industry_flows: list[Any] = []
    concept_flows: list[Any] = []
    sources: list[str] = []
    cached_concept = None if force_refresh else _get_response_cache(
        "sector-flow|概念资金流|今日",
        allow_stale=cache_only,
    )
    cached_industry = None if force_refresh else _get_response_cache(
        "sector-flow|行业资金流|今日",
        allow_stale=cache_only,
    )
    try:
        concept_flow = cached_concept or (None if cache_only else market_provider.sector_flow(
            flow_type="概念资金流",
            period="今日",
            force_refresh=force_refresh,
        ))
        if concept_flow is not None:
            concept_flows.extend(concept_flow.inflow + concept_flow.outflow)
            sources.append(f"概念订单流算法/{concept_flow.source}")
    except Exception as exc:
        notes.append(f"概念订单流算法不可用：{exc}")
    try:
        industry_flow = cached_industry or (None if cache_only else market_provider.sector_flow(
            flow_type="行业资金流",
            period="今日",
            force_refresh=force_refresh,
        ))
        if industry_flow is not None:
            industry_flows.extend(industry_flow.inflow + industry_flow.outflow)
            sources.append(f"行业订单流算法/{industry_flow.source}")
    except Exception as exc:
        notes.append(f"行业订单流算法不可用：{exc}")

    if cache_only and cached_concept is None and cached_industry is None:
        notes.append("尚无板块订单流缓存，请点击刷新或等待后台采集。")

    unique_industry_flows = _dedupe_sector_flows(industry_flows)
    unique_concept_flows = _dedupe_sector_flows(concept_flows)
    ranked_industry = sorted(unique_industry_flows, key=lambda item: (item.net_inflow, item.main_inflow, item.change_pct), reverse=True)
    ranked_concept = sorted(unique_concept_flows, key=lambda item: (item.net_inflow, item.main_inflow, item.change_pct), reverse=True)
    industry_rank_map = {item.name: idx for idx, item in enumerate(ranked_industry, start=1)}
    concept_rank_map = {item.name: idx for idx, item in enumerate(ranked_concept, start=1)}
    limit_counts = {} if cache_only else _sector_limit_up_counts()

    inflow_targets = [
        _sector_rotation_item(item, industry_rank_map.get(item.name, 0), limit_counts)
        for item in ranked_industry[:10]
    ]
    outflow_ranked = sorted(unique_industry_flows, key=lambda item: (item.net_inflow, item.main_inflow))
    outflow_targets = [
        _sector_rotation_item(item, industry_rank_map.get(item.name, 0), limit_counts)
        for item in outflow_ranked[:8]
        if item.net_inflow < 0 or item.main_inflow < 0
    ]

    quotes = {} if cache_only else _latest_quotes_for_holdings(holdings)
    holding_alerts = [
        _holding_seesaw_item(
            holding,
            quotes.get(_quote_lookup_code(holding.code, quotes), {}),
            ranked_industry,
            industry_rank_map,
            ranked_concept,
            concept_rank_map,
            inflow_targets,
            allow_network=not cache_only,
        )
        for holding in holdings
    ]
    severe_count = sum(1 for item in holding_alerts if item.risk_level in {"高", "中高"})
    strong_targets = [item for item in inflow_targets if item.net_inflow > 0 and (item.acceleration > 0 or item.limit_up_count >= 2)]
    market_mode = "存量跷跷板明显" if strong_targets and severe_count else "轮动观察"
    if len(strong_targets) >= 2 and severe_count >= 2:
        market_mode = "存量资金快速迁移"
    top_target = strong_targets[0].name if strong_targets else (inflow_targets[0].name if inflow_targets else "暂无")
    summary = (
        f"行业订单流方向估算当前最强方向：{top_target}；"
        f"{'已有持仓出现冲高回落/板块失血，需要保护利润。' if severe_count else '暂未触发持仓级强告警，继续观察板块排名和个股分时均价。'}"
    )
    return MarketSeesawOut(
        source="+".join(dict.fromkeys(sources)) or "数据源不可用",
        updated_at=shanghai_now_naive(),
        market_mode=market_mode,
        summary=summary,
        inflow_targets=inflow_targets[:8],
        outflow_targets=outflow_targets[:6],
        holding_alerts=sorted(
            holding_alerts,
            key=lambda item: ({"高": 4, "中高": 3, "中": 2, "观察": 1}.get(item.risk_level, 0), item.pullback_from_high_pct),
            reverse=True,
        ),
        notes=notes or ["主判定口径为行业订单流供应商算法；概念订单流算法仅作为辅助证据，不代表账户真实流水。"],
    )

def _dedupe_sector_flows(flows: list[Any]) -> list[Any]:
    best: dict[str, Any] = {}
    for item in flows:
        name = str(getattr(item, "name", "") or getattr(item, "display_name", "") or "")
        if not name:
            continue
        previous = best.get(name)
        if previous is None or abs(float(getattr(item, "net_inflow", 0) or 0)) > abs(float(getattr(previous, "net_inflow", 0) or 0)):
            best[name] = item
    return list(best.values())

def _sector_rotation_item(item: Any, rank: int, limit_counts: dict[str, int]) -> SectorRotationItem:
    acceleration = _sector_acceleration(item)
    flow_speed = getattr(item, "flow_speed", None)
    flow_acceleration = getattr(item, "flow_acceleration", None)
    flow_direction = str(getattr(item, "flow_direction", "") or "") or None
    flow_turning = str(getattr(item, "flow_turning", "") or "") or None
    flow_signal = _display_flow_signal(str(getattr(item, "flow_signal", "") or "")) or None
    flow_as_of = str(getattr(item, "flow_as_of", "") or "") or None
    names = _sector_aliases(item)
    limit_count = max(limit_counts.get(name, 0) for name in names) if names else 0
    direction = "订单流方向加速流入" if acceleration > 0 else "订单流方向流入减速" if acceleration < 0 else "订单流方向平稳"
    evidence = (
        f"排名第{rank}，涨跌{float(item.change_pct):+.2f}%，订单流方向净额{float(item.net_inflow):.2f}亿，"
        f"大单方向估算{float(item.main_inflow):.2f}亿，盘中变化{acceleration:+.2f}亿，涨停{limit_count}只，{direction}（供应商算法，非账户真实流水）。"
    )
    return SectorRotationItem(
        name=str(item.name),
        rank=rank,
        change_pct=round(float(item.change_pct or 0), 2),
        net_inflow=round(float(item.net_inflow or 0), 2),
        main_inflow=round(float(item.main_inflow or 0), 2),
        acceleration=round(acceleration, 2),
        flow_speed=round(float(flow_speed), 4) if flow_speed is not None else None,
        flow_acceleration=round(float(flow_acceleration), 6) if flow_acceleration is not None else None,
        flow_direction=flow_direction,
        flow_turning=flow_turning,
        flow_signal=flow_signal,
        flow_as_of=flow_as_of,
        flow_window_minutes=getattr(item, "flow_window_minutes", None),
        flow_kinetics_reliable=bool(getattr(item, "flow_kinetics_reliable", False)),
        limit_up_count=limit_count,
        leaders=[str(leader) for leader in getattr(item, "leaders", [])[:4]],
        evidence=evidence,
    )

def _sector_acceleration(item: Any) -> float:
    points = list(getattr(item, "timeline", []) or [])
    values = [float(getattr(point, "value", 0) or 0) for point in points]
    if len(values) >= 2:
        return values[-1] - values[max(0, len(values) - 4)]
    return float(getattr(item, "net_inflow", 0) or 0)

def _sector_aliases(item: Any) -> list[str]:
    raw = [
        getattr(item, "name", ""),
        getattr(item, "display_name", ""),
        getattr(item, "raw_name", ""),
        getattr(item, "theme_line", ""),
        getattr(item, "mainline", ""),
        getattr(item, "subline", ""),
        getattr(item, "category", ""),
    ]
    return [str(value) for value in raw if str(value or "").strip()]

def _sector_limit_up_counts() -> dict[str, int]:
    counts: Counter[str] = Counter()
    try:
        ladder = market_provider.limit_up_ladder(force_refresh=False)
    except Exception:
        ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is None:
        return {}
    for cluster in getattr(ladder, "clusters", []) or []:
        counts[str(cluster.name)] += int(cluster.count or 0)
    for group in getattr(ladder, "groups", []) or []:
        for stock in getattr(group, "stocks", []) or []:
            if stock.industry:
                counts[str(stock.industry)] += 1
            for concept in stock.concepts[:4]:
                counts[str(concept)] += 1
    return dict(counts)

def _latest_quotes_for_holdings(holdings: list[Holding]) -> dict[str, dict[str, Any]]:
    try:
        return _latest_a_share_quotes([holding.code for holding in holdings if holding.code])
    except Exception:
        return {}

def _holding_seesaw_item(
    holding: Holding,
    quote: dict[str, Any],
    ranked_industry_flows: list[Any],
    industry_rank_map: dict[str, int],
    ranked_concept_flows: list[Any],
    concept_rank_map: dict[str, int],
    inflow_targets: list[SectorRotationItem],
    *,
    allow_network: bool = True,
) -> HoldingSeesawItem:
    current = _safe_float(quote.get("price")) or holding.current_price
    prev_close = _safe_float(quote.get("prev_close"))
    high = _safe_float(quote.get("high"))
    change_pct = _safe_float(quote.get("change_pct"))
    high_change_pct = ((high - prev_close) / prev_close * 100) if high and prev_close else max(change_pct, 0.0)
    pullback = max(0.0, high_change_pct - change_pct)
    estimated_vwap = _estimated_vwap(quote)
    below_vwap = bool(estimated_vwap and current < estimated_vwap)
    theme_profile = _holding_theme_profile(holding, allow_network=allow_network)
    holding_theme = str(theme_profile["primary"])
    theme_tags = list(theme_profile["tags"])
    theme_flow = _holding_theme_flow_profile(
        holding,
        ranked_industry_flows,
        industry_rank_map,
        ranked_concept_flows,
        concept_rank_map,
        allow_network=allow_network,
    )
    matched_flow = theme_flow["primary_flow"]
    theme_flow_sectors = list(theme_flow["sectors"])
    concept_flow_sectors = list(theme_flow["concept_sectors"])
    matched_flow_sector = str(getattr(matched_flow, "name", "") or "") or "、".join(theme_flow_sectors[:3])
    sector = holding_theme
    sector_rank = int(theme_flow["rank"])
    sector_net = float(theme_flow["current"])
    sector_main = float(theme_flow["main"])
    sector_acc = float(theme_flow["acceleration"])
    sector_flow_speed = getattr(matched_flow, "flow_speed", None) if matched_flow is not None else None
    sector_flow_acceleration = getattr(matched_flow, "flow_acceleration", None) if matched_flow is not None else None
    sector_flow_direction = str(getattr(matched_flow, "flow_direction", "") or "") if matched_flow is not None else ""
    sector_flow_turning = str(getattr(matched_flow, "flow_turning", "") or "") if matched_flow is not None else ""
    sector_flow_signal = str(getattr(matched_flow, "flow_signal", "") or "") if matched_flow is not None else ""
    sector_flow_as_of = str(getattr(matched_flow, "flow_as_of", "") or "") if matched_flow is not None else ""
    theme_flow_peak = float(theme_flow["peak"])
    theme_flow_pullback = float(theme_flow["pullback"])
    theme_flow_pullback_pct = float(theme_flow["pullback_pct"])
    theme_flow_summary = str(theme_flow["summary"])
    concept_flow_summary = str(theme_flow["concept_summary"])
    strongest = inflow_targets[0] if inflow_targets else None
    strongest_name = strongest.name if strongest else "暂无强吸金方向"
    strongest_is_other = bool(
        strongest
        and holding_theme
        and strongest.name not in theme_flow_sectors
        and _sector_family(strongest.name) != _sector_family(holding_theme)
    )
    external_inflow_target = strongest_name if strongest_is_other else ""
    evidence: list[str] = [
        f"所属主线：{holding_theme}；标签：{' / '.join(theme_tags) or '待补充'}。",
        (
            f"个股板块画像：行业={theme_profile.get('industry') or '未抓到'}；"
            f"概念={ '、'.join(list(theme_profile.get('concepts') or [])[:8]) or '未抓到'}；"
            f"来源={theme_profile.get('source') or 'fallback'}。"
        ),
        f"{holding.name}最高涨幅{high_change_pct:.2f}%，当前涨幅{change_pct:.2f}%，高点回撤{pullback:.2f}%。",
    ]
    if estimated_vwap:
        evidence.append(f"估算VWAP {estimated_vwap:.2f}，当前{'跌破' if below_vwap else '仍在'}VWAP。")
    if theme_flow_sectors:
        evidence.append(theme_flow_summary)
    else:
        evidence.append("板块订单流方向曲线：未在行业/概念供应商算法中精确匹配；仅展示个股画像，不强行替代。")
    if sector_flow_signal:
        flow_time = f"（截至{sector_flow_as_of}）" if sector_flow_as_of else ""
        speed_text = f"，流速{float(sector_flow_speed):+.3f}亿/分钟" if sector_flow_speed is not None else ""
        evidence.append(f"订单流方向拐点证据{flow_time}：{_display_flow_signal(sector_flow_signal)}{speed_text}。")
    if concept_flow_sectors:
        evidence.append(concept_flow_summary)
    if strongest:
        evidence.append(f"外部吸金方向：{strongest.name}，净流入{strongest.net_inflow:.2f}亿，涨停{strongest.limit_up_count}只。")

    sell_triggers = _intraday_sell_triggers(
        holding=holding,
        current=current,
        high=high,
        high_change_pct=high_change_pct,
        change_pct=change_pct,
        pullback=pullback,
        below_vwap=below_vwap,
        sector=sector,
        sector_rank=sector_rank,
        sector_net=sector_net,
        sector_main=sector_main,
        sector_acc=sector_acc,
        sector_flow_speed=float(sector_flow_speed) if sector_flow_speed is not None else None,
        sector_flow_acceleration=float(sector_flow_acceleration) if sector_flow_acceleration is not None else None,
        sector_flow_turning=sector_flow_turning,
        sector_flow_signal=sector_flow_signal,
        sector_flow_peak=theme_flow_peak,
        sector_flow_current=sector_net,
        sector_flow_pullback=theme_flow_pullback,
        sector_flow_pullback_pct=theme_flow_pullback_pct,
        strongest_name=strongest_name,
        strongest_is_other=strongest_is_other,
    )

    score = 0
    if strongest_is_other and strongest.net_inflow > max(0, sector_net):
        score += 2
    if sector_net < 0 or sector_main < 0 or sector_acc < -1:
        score += 2
    if sector_flow_turning in {"TURN_TO_OUTFLOW", "OUTFLOW_ACCELERATING", "INFLOW_FADING", "FLOW_WEAKENING"}:
        score += 2
    if theme_flow_pullback >= 20 or theme_flow_pullback_pct >= 20:
        score += 2
    if pullback >= 4:
        score += 2
    elif pullback >= 2.5:
        score += 1
    if below_vwap:
        score += 2
    if high_change_pct >= 7 and pullback >= 3:
        score += 1
    if sell_triggers["profit_drawdown_trigger"]:
        score += 2
    if sell_triggers["stock_weakening_trigger"] and sell_triggers["sector_ebb_trigger"]:
        score += 1

    if score >= 6:
        risk_level = "高"
        signal = (
            f"{holding_theme}承压，外部吸金方向为{external_inflow_target}，个股冲高回落弱于预期"
            if external_inflow_target
            else f"{holding_theme}承压，个股冲高回落弱于预期"
        )
        advice = sell_triggers["trigger_action"] or "优先保护利润：跌破/反抽不过VWAP继续减仓；若板块订单流方向估算仍恶化，不再加仓或买回。"
    elif score >= 4:
        risk_level = "中高"
        signal = "订单流跷跷板风险升高"
        advice = sell_triggers["trigger_action"] or "持有降为观察：不加仓；若高点回撤扩大或跌破VWAP，先减一部分风险。"
    elif score >= 2:
        risk_level = "中"
        signal = "板块轮动分流"
        advice = sell_triggers["trigger_action"] or "继续观察板块排名和个股承接，只有重新站稳VWAP且板块订单流方向估算转强才提高预期。"
    else:
        risk_level = "观察"
        signal = "暂未触发跷跷板风险"
        advice = "按原计划持有观察，重点看所属板块是否继续在订单流方向榜前列。"

    return HoldingSeesawItem(
        code=holding.code,
        name=holding.name,
        sector=sector,
        holding_theme=holding_theme,
        theme_tags=theme_tags,
        stock_industry=str(theme_profile.get("industry") or ""),
        stock_concepts=[str(item) for item in theme_profile.get("concepts", [])],
        theme_source=str(theme_profile.get("source") or ""),
        flow_basis=str(theme_flow.get("basis") or "行业资金流"),
        primary_industry_sector=matched_flow_sector,
        concept_flow_sectors=concept_flow_sectors,
        concept_flow_summary=concept_flow_summary,
        matched_flow_sector_redundant=matched_flow_sector, # matched_flow_sector is output twice in model
        theme_flow_sectors=theme_flow_sectors,
        theme_flow_summary=theme_flow_summary,
        theme_flow_current=round(sector_net, 2),
        theme_flow_peak=round(theme_flow_peak, 2),
        theme_flow_pullback=round(theme_flow_pullback, 2),
        theme_flow_pullback_pct=round(theme_flow_pullback_pct, 2),
        external_inflow_target=external_inflow_target,
        current_price=round(current, 2),
        change_pct=round(change_pct, 2),
        high_change_pct=round(high_change_pct, 2),
        pullback_from_high_pct=round(pullback, 2),
        estimated_vwap=round(estimated_vwap, 2),
        below_vwap=below_vwap,
        sector_rank=sector_rank,
        sector_net_inflow=round(sector_net, 2),
        sector_main_inflow=round(sector_main, 2),
        sector_acceleration=round(sector_acc, 2),
        sector_flow_speed=round(float(sector_flow_speed), 4) if sector_flow_speed is not None else None,
        sector_flow_acceleration=round(float(sector_flow_acceleration), 6) if sector_flow_acceleration is not None else None,
        sector_flow_direction=sector_flow_direction or None,
        sector_flow_turning=sector_flow_turning or None,
        sector_flow_signal=sector_flow_signal or None,
        sector_flow_as_of=sector_flow_as_of or None,
        sector_flow_window_minutes=getattr(matched_flow, "flow_window_minutes", None) if matched_flow is not None else None,
        sector_flow_kinetics_reliable=bool(getattr(matched_flow, "flow_kinetics_reliable", False)) if matched_flow is not None else False,
        risk_level=risk_level,
        signal=signal,
        advice=advice,
        profit_protection_state=sell_triggers["profit_protection_state"],
        trigger_action=sell_triggers["trigger_action"],
        sector_ebb_trigger=sell_triggers["sector_ebb_trigger"],
        stock_weakening_trigger=sell_triggers["stock_weakening_trigger"],
        profit_drawdown_trigger=sell_triggers["profit_drawdown_trigger"],
        buyback_trigger=sell_triggers["buyback_trigger"],
        evidence=evidence,
        theme_flow_timeline=list(theme_flow.get("timeline_points", [])),
    )

def _intraday_sell_triggers(
    holding: Holding,
    current: float,
    high: float,
    high_change_pct: float,
    change_pct: float,
    pullback: float,
    below_vwap: bool,
    sector: str,
    sector_rank: int,
    sector_net: float,
    sector_main: float,
    sector_acc: float,
    sector_flow_speed: float | None = None,
    sector_flow_acceleration: float | None = None,
    sector_flow_turning: str = "",
    sector_flow_signal: str = "",
    sector_flow_peak: float = 0.0,
    sector_flow_current: float = 0.0,
    sector_flow_pullback: float = 0.0,
    sector_flow_pullback_pct: float = 0.0,
    strongest_name: str = "",
    strongest_is_other: bool = False,
) -> dict[str, Any]:
    current_profit_pct = ((current - holding.cost_price) / holding.cost_price * 100) if holding.cost_price else 0.0
    high_profit_pct = ((high - holding.cost_price) / holding.cost_price * 100) if high and holding.cost_price else max(current_profit_pct, high_change_pct)
    profit_drawdown = max(0.0, high_profit_pct - current_profit_pct)
    sector_triggers: list[str] = []
    stock_triggers: list[str] = []
    profit_triggers: list[str] = []

    if sector_net < 0:
        sector_triggers.append(f"{sector or '所属板块'}订单流方向净额转负：{sector_net:.2f}亿（供应商算法）。")
    if sector_main < 0:
        sector_triggers.append(f"{sector or '所属板块'}大单方向估算转负：{sector_main:.2f}亿（供应商算法）。")
    if sector_acc < -1:
        sector_triggers.append(f"{sector or '所属板块'}盘中订单流方向变化{sector_acc:+.2f}亿，出现退潮。")
    if sector_flow_turning == "TURN_TO_OUTFLOW":
        speed_text = f"，流速{sector_flow_speed:+.3f}亿/分钟" if sector_flow_speed is not None else ""
        sector_triggers.append(f"{sector or '所属板块'}订单流方向由净流入拐为净流出{speed_text}。")
    elif sector_flow_turning in {"OUTFLOW_ACCELERATING", "INFLOW_FADING", "FLOW_WEAKENING"}:
        speed_text = f"，流速{sector_flow_speed:+.3f}亿/分钟" if sector_flow_speed is not None else ""
        acceleration_text = (
            f"、加速度{sector_flow_acceleration:+.4f}亿/分钟²"
            if sector_flow_acceleration is not None else ""
        )
        sector_triggers.append(
            f"{sector or '所属板块'}{_display_flow_signal(sector_flow_signal) or '订单流方向边际转弱'}{speed_text}{acceleration_text}。"
        )
    if sector_flow_pullback >= 20 or sector_flow_pullback_pct >= 20:
        sector_triggers.append(
            f"{sector or '所属板块'}订单流方向净额从高点{sector_flow_peak:.2f}亿回落到{sector_flow_current:.2f}亿，"
            f"回落{sector_flow_pullback:.2f}亿（{sector_flow_pullback_pct:.1f}%），即使当前仍净流入也按退潮处理。"
        )
    if sector_rank and sector_rank > 10:
        sector_triggers.append(f"{sector or '所属板块'}订单流方向排名降至第{sector_rank}，不在前排。")
    if strongest_is_other:
        sector_triggers.append(f"订单流方向排名切向{strongest_name}，形成跷跷板分流。")

    if high_change_pct >= 9 and pullback >= 3:
        stock_triggers.append(f"盘中接近涨停/强冲高后回撤{pullback:.2f}%，冲板失败风险升高。")
    elif high_change_pct >= 5 and pullback >= 3:
        stock_triggers.append(f"强势冲高后回撤{pullback:.2f}%，由强转分歧。")
    if pullback >= 5:
        stock_triggers.append("从日内高点回撤超过5%，利润回吐速度偏快。")
    elif pullback >= 3:
        stock_triggers.append("从日内高点回撤超过3%，触发减仓观察。")
    if below_vwap:
        stock_triggers.append("当前跌破估算分时均价/VWAP，且反抽未确认前不提高预期。")
    if change_pct < 0 and high_change_pct >= 5:
        stock_triggers.append("从高浮盈杀到翻绿/收跌区间，属于强转弱而非普通震荡。")

    if high_profit_pct >= 8:
        protection_state = f"最高浮盈约{high_profit_pct:.2f}%，进入8%-10%分批兑现区。"
    elif high_profit_pct >= 5:
        protection_state = f"最高浮盈约{high_profit_pct:.2f}%，进入5%以上利润保护区。"
    else:
        protection_state = "尚未进入5%利润保护区，按原止损/预期管理。"

    if high_profit_pct >= 5 and profit_drawdown >= 3:
        profit_triggers.append(f"浮盈5%以上后回撤{profit_drawdown:.2f}%，先减一部分观察。")
    if high_profit_pct >= 5 and profit_drawdown >= 5 and below_vwap:
        profit_triggers.append("浮盈保护区内回撤超过5%且跌破VWAP，优先兑现至少一半。")
    if high_profit_pct >= 8 and (sector_triggers or below_vwap or pullback >= 3):
        profit_triggers.append("浮盈8%-10%区间未能封住强势，不能再按亏损票逻辑死等。")

    if profit_triggers and stock_triggers and sector_triggers:
        action = "三类卖出信号共振：板块退潮、个股转弱、利润回撤同时出现，按卖出/减仓信号处理，先保护利润。"
    elif profit_triggers and stock_triggers:
        action = "利润保护与个股弱化同时触发：先减仓观察，跌破/反抽不过VWAP继续降风险。"
    elif sector_triggers and stock_triggers:
        action = "板块退潮叠加个股弱化：持有降级，不加仓，反抽不过VWAP优先减仓。"
    elif profit_triggers:
        action = "进入利润保护状态：回撤达到规则阈值，先兑现一部分，不把盈利票拿成被动票。"
    elif sector_triggers:
        action = "板块订单流方向出现分流：继续观察个股是否跌破VWAP，未转强前不接回。"
    else:
        action = ""

    buyback = [
        "板块止跌或订单流方向重新转强。",
        "个股不再创新低，并重新站回分时均价/VWAP。",
        "下跌缩量、反弹放量；买回后设置失败位，跌破日内低点或VWAP不继续补。",
    ]
    if sector_flow_turning in {"TURN_TO_INFLOW", "OUTFLOW_NARROWING", "INFLOW_ACCELERATING", "FLOW_IMPROVING"}:
        buyback.insert(
            0,
            f"{sector or '所属板块'}{_display_flow_signal(sector_flow_signal) or '订单流方向边际改善'}；仍需个股站回真实VWAP后确认。",
        )
    return {
        "profit_protection_state": protection_state,
        "trigger_action": action,
        "sector_ebb_trigger": sector_triggers,
        "stock_weakening_trigger": stock_triggers,
        "profit_drawdown_trigger": profit_triggers,
        "buyback_trigger": buyback,
    }

def _match_holding_sector_flow(holding: Holding, ranked_flows: list[Any]) -> Any | None:
    best: tuple[int, Any] | None = None
    for flow in ranked_flows:
        score = _holding_flow_match_score(holding, flow)
        if score and (best is None or score > best[0]):
            best = (score, flow)
    return best[1] if best else None

def _holding_theme_flow_profile(
    holding: Holding,
    ranked_industry_flows: list[Any],
    industry_rank_map: dict[str, int],
    ranked_concept_flows: list[Any] | None = None,
    concept_rank_map: dict[str, int] | None = None,
    *,
    allow_network: bool = True,
) -> dict[str, Any]:
    theme_profile = _holding_theme_profile(holding, allow_network=allow_network)
    preferred_flow = _preferred_industry_board_flow(
        holding,
        allow_network=allow_network,
    )
    matched_industry: list[tuple[int, Any]] = []
    for flow in ranked_industry_flows:
        score = _holding_flow_match_score(holding, flow, allow_network=allow_network)
        if score > 0:
            matched_industry.append((score, flow))
    matched_industry.sort(
        key=lambda pair: (
            pair[0],
            abs(float(getattr(pair[1], "net_inflow", 0) or 0)),
            abs(_sector_acceleration(pair[1])),
        ),
        reverse=True,
    )
    selected = [preferred_flow] if preferred_flow is not None else [flow for _, flow in matched_industry[:1]]
    primary_flow = selected[0] if selected else None
    sectors = [str(getattr(flow, "name", "") or "") for flow in selected if str(getattr(flow, "name", "") or "")]
    current = sum(float(getattr(flow, "net_inflow", 0) or 0) for flow in selected)
    main = sum(float(getattr(flow, "main_inflow", 0) or 0) for flow in selected)
    acceleration = sum(_sector_acceleration(flow) for flow in selected)
    rank = min((industry_rank_map.get(name, 999) for name in sectors), default=0)
    timeline = _aggregate_flow_timeline(selected)
    peak = max(timeline.values()) if timeline else current
    if current > peak:
        peak = current
    pullback = max(0.0, peak - current)
    pullback_pct = pullback / abs(peak) * 100 if peak else 0.0
    concept_matched: list[tuple[int, Any]] = []
    for flow in ranked_concept_flows or []:
        score = _holding_flow_match_score(holding, flow, allow_network=allow_network)
        if score > 0:
            concept_matched.append((score, flow))
    concept_matched.sort(
        key=lambda pair: (
            pair[0],
            abs(float(getattr(pair[1], "net_inflow", 0) or 0)),
            abs(_sector_acceleration(pair[1])),
        ),
        reverse=True,
    )
    priority_aliases = [str(item) for item in theme_profile.get("priority_flow_aliases", []) if str(item).strip()]
    if priority_aliases:
        concept_selected = [
            flow
            for _, flow in concept_matched
            if _flow_matches_aliases(flow, priority_aliases)
        ][:4]
    else:
        concept_selected = [flow for _, flow in concept_matched[:4]]
    best_industry_score = matched_industry[0][0] if matched_industry else 0
    best_concept_score = concept_matched[0][0] if concept_matched else 0
    priority_concept = next(
        (
            flow
            for _, flow in concept_matched
            if _flow_matches_aliases(flow, priority_aliases)
        ),
        None,
    )
    use_concept_primary = bool(
        priority_concept is not None
        and best_concept_score >= best_industry_score
        and _holding_theme_prefers_concept_flow(theme_profile)
    )
    basis = "概念资金流" if use_concept_primary else "行业资金流"
    if (
        _holding_theme_prefers_concept_flow(theme_profile)
        and priority_concept is None
        and selected
        and not any(_flow_matches_aliases(flow, priority_aliases) for flow in selected)
    ):
        selected = []
        primary_flow = None
        sectors = []
        current = 0.0
        main = 0.0
        acceleration = 0.0
        rank = 0
        peak = 0.0
        pullback = 0.0
        pullback_pct = 0.0
        basis = "订单流算法缺口"
    if use_concept_primary:
        selected = [priority_concept]
        primary_flow = selected[0]
        sectors = [str(getattr(primary_flow, "name", "") or "")]
        rank = concept_rank_map.get(sectors[0], 999) if concept_rank_map and sectors and sectors[0] else 0
        concept_selected = [flow for flow in concept_selected if flow is not primary_flow]
        current = sum(float(getattr(flow, "net_inflow", 0) or 0) for flow in selected)
        main = sum(float(getattr(flow, "main_inflow", 0) or 0) for flow in selected)
        acceleration = sum(_sector_acceleration(flow) for flow in selected)
        timeline = _aggregate_flow_timeline(selected)
        peak = max(timeline.values()) if timeline else current
        if current > peak:
            peak = current
        pullback = max(0.0, peak - current)
        pullback_pct = pullback / abs(peak) * 100 if peak else 0.0
    concept_sectors = [
        str(getattr(flow, "name", "") or "")
        for flow in concept_selected
        if str(getattr(flow, "name", "") or "")
    ]
    concept_current = sum(float(getattr(flow, "net_inflow", 0) or 0) for flow in concept_selected)
    concept_main = sum(float(getattr(flow, "main_inflow", 0) or 0) for flow in concept_selected)
    concept_rank = min((concept_rank_map.get(name, 999) for name in concept_sectors), default=0) if concept_rank_map else 0
    if sectors:
        sector_text = "、".join(sectors[:3])
        rank_name = "概念排名" if basis == "概念资金流" else "行业排名"
        rank_text = f"{rank_name}第{rank}" if rank and rank != 999 else "优先细分板块"
        basis_label = "概念订单流算法" if basis == "概念资金流" else "行业订单流算法" if basis == "行业资金流" else basis
        summary = (
            f"板块订单流方向曲线：{sector_text}（{basis_label}）；{rank_text}，当前方向净额{current:.2f}亿，"
            f"大单方向估算{main:.2f}亿，盘中变化{acceleration:+.2f}亿（供应商算法，非账户真实流水）。"
        )
        if pullback > 0:
            summary += f" 高点{peak:.2f}亿回落至当前，回落{pullback:.2f}亿（{pullback_pct:.1f}%）。"
    else:
        summary = ""
    if concept_sectors:
        concept_summary = (
            f"概念辅助证据：{'、'.join(concept_sectors[:4])}；"
            f"最佳概念排名第{0 if concept_rank == 999 else concept_rank}，"
            f"合计订单流方向净额{concept_current:.2f}亿，大单方向估算{concept_main:.2f}亿（供应商算法）。"
        )
    else:
        concept_summary = ""
    _tl = _aggregate_flow_timeline(selected)
    timeline_points = _timeline_points_with_current(_tl, current)

    return {
        "primary_flow": primary_flow,
        "basis": basis,
        "sectors": sectors,
        "rank": 0 if rank == 999 else rank,
        "current": current,
        "main": main,
        "acceleration": acceleration,
        "peak": peak,
        "pullback": pullback,
        "pullback_pct": pullback_pct,
        "summary": summary,
        "concept_sectors": concept_sectors,
        "concept_summary": concept_summary,
        "concept_current": concept_current,
        "concept_main": concept_main,
        "timeline_points": timeline_points,
    }

def _timeline_points_with_current(timeline: dict[str, float], current: float) -> list[dict[str, Any]]:
    def _sort_key(item: tuple[str, float]) -> int:
        label = item[0]
        match = re.match(r"^(\d{1,2}):(\d{2})$", label)
        if match:
            return int(match.group(1)) * 60 + int(match.group(2))
        return 2400 if label == "当前" else 9999

    points = [
        {"time": t, "value": round(v, 2)}
        for t, v in sorted(timeline.items(), key=_sort_key)
        if t and (v or v == 0)
    ]
    final = round(float(current or 0), 2)
    if not points:
        return [{"time": "当前", "value": final}]
    if abs(float(points[-1]["value"]) - final) > 0.01:
        points.append({"time": "当前", "value": final})
    else:
        points[-1]["value"] = final
    return points

def _cached_holding_theme_flow_profile(
    holding: Holding,
    *,
    allow_network: bool = False,
) -> dict[str, Any]:
    industry_flows = []
    concept_flows = []
    cached_industry = _get_response_cache("sector-flow|行业资金流|今日")
    cached_concept = _get_response_cache("sector-flow|概念资金流|今日")
    if allow_network and cached_industry is None:
        try:
            cached_industry = market_provider.sector_flow(flow_type="行业资金流", period="今日")
        except Exception:
            pass
    if allow_network and cached_concept is None:
        try:
            cached_concept = market_provider.sector_flow(flow_type="概念资金流", period="今日")
        except Exception:
            pass
    if cached_industry:
        industry_flows.extend(cached_industry.inflow + cached_industry.outflow)
    if cached_concept:
        concept_flows.extend(cached_concept.inflow + cached_concept.outflow)
    unique_industry = _dedupe_sector_flows(industry_flows)
    unique_concept = _dedupe_sector_flows(concept_flows)
    ranked_industry = sorted(unique_industry, key=lambda item: (item.net_inflow, item.main_inflow, item.change_pct), reverse=True)
    ranked_concept = sorted(unique_concept, key=lambda item: (item.net_inflow, item.main_inflow, item.change_pct), reverse=True)
    industry_rank_map = {item.name: idx for idx, item in enumerate(ranked_industry, start=1)}
    concept_rank_map = {item.name: idx for idx, item in enumerate(ranked_concept, start=1)}
    profile = _holding_theme_flow_profile(
        holding,
        ranked_industry,
        industry_rank_map,
        ranked_concept,
        concept_rank_map,
        allow_network=allow_network,
    )
    primary_flow = profile.get("primary_flow")
    # Expose the *matched* board observation rather than asking downstream
    # callers to infer a sector move from the fund-flow amount.  A zero change
    # or zero net flow is a valid observation; only a missing matched board is
    # treated as a data gap.
    profile.update({
        "matched": primary_flow is not None,
        "sector_change_pct": (
            float(getattr(primary_flow, "change_pct"))
            if primary_flow is not None and getattr(primary_flow, "change_pct", None) is not None
            else None
        ),
        "flow_speed": (
            float(getattr(primary_flow, "flow_speed"))
            if primary_flow is not None and getattr(primary_flow, "flow_speed", None) is not None
            else None
        ),
        "flow_acceleration": (
            float(getattr(primary_flow, "flow_acceleration"))
            if primary_flow is not None and getattr(primary_flow, "flow_acceleration", None) is not None
            else None
        ),
        "flow_direction": str(getattr(primary_flow, "flow_direction", "") or "") or None,
        "flow_turning": str(getattr(primary_flow, "flow_turning", "") or "") or None,
        "flow_signal": str(getattr(primary_flow, "flow_signal", "") or "") or None,
        "flow_as_of": str(getattr(primary_flow, "flow_as_of", "") or "") or None,
        "flow_window_minutes": getattr(primary_flow, "flow_window_minutes", None),
        "flow_kinetics_reliable": bool(
            primary_flow is not None
            and getattr(primary_flow, "flow_kinetics_reliable", False)
        ),
    })
    # Preserve the actual cache/source timestamps.  Callers such as the
    # evidence-grounded AI assistant must never label an older sector-flow
    # cache with the current request time.
    if profile.get("basis") == "概念资金流":
        flow_snapshots = [item for item in (cached_concept,) if item]
    elif profile.get("basis") == "行业资金流":
        flow_snapshots = [item for item in (cached_industry,) if item]
    else:
        flow_snapshots = []
    source_times = [getattr(primary_flow, "updated_at", None)] if primary_flow is not None else []
    source_times.extend(getattr(item, "updated_at", None) for item in flow_snapshots)
    source_times = [item for item in source_times if item is not None]
    sources = [str(getattr(primary_flow, "provider", "") or "")] if primary_flow is not None else []
    sources.extend(str(getattr(item, "source", "") or "") for item in flow_snapshots)
    sources = list(dict.fromkeys(item for item in sources if item))
    profile["as_of"] = min(source_times).isoformat() if source_times else "未知"
    profile["source"] = "+".join(sources) or "板块订单流估算缓存不可用"
    profile["data_quality"] = "cached_source_timestamped" if source_times else "missing"
    return profile

def _broader_industry_timeline(
    ranked_industry_flows: list[Any],
    holding_sectors: list[str],
    theme_profile: dict[str, Any],
) -> dict[str, float] | None:
    best: tuple[int, dict[str, float]] | None = None
    _SECTOR_TO_INDUSTRY_FLOW: dict[str, list[str]] = {
        "半导体": ["电子信息", "电子器件", "半导体设备"],
        "AI算力": ["电子信息", "电子器件", "计算机行业", "通信行业"],
        "商业航天": ["飞机制造", "航天航空", "军工航天"],
        "医药": ["生物制药", "化学制药", "医疗器械"],
        "机器人": ["机械行业", "电器行业"],
        "新能源": ["发电设备", "新能源车"],
        "化工": ["化工行业", "化纤行业"],
    }
    for flow in ranked_industry_flows:
        flow_names = _sector_aliases(flow)
        match_score = 0
        for s in holding_sectors:
            if not s:
                continue
            for name in flow_names:
                if not name:
                    continue
                if s in name or name in s:
                    match_score += 4
                    break
            mapped = _SECTOR_TO_INDUSTRY_FLOW.get(s, [])
            for m in mapped:
                for name in flow_names:
                    if m in name or name in m:
                        match_score += 3
                        break
        if match_score > 0:
            tl = _aggregate_flow_timeline([flow])
            if tl and (best is None or match_score > best[0] or len(tl) > len(best[1])):
                best = (match_score, tl)
    return best[1] if best else None

def _preferred_industry_board_flow(
    holding: Holding,
    *,
    allow_network: bool = True,
) -> Any | None:
    theme_profile = _holding_theme_profile(holding, allow_network=allow_network)
    if not allow_network:
        return None
    for secid, display_name in theme_profile.get("preferred_industry_boards", []) or []:
        try:
            flow = _fetch_eastmoney_h5_board_flow(str(secid), str(display_name))
        except Exception:
            continue
        if flow is not None:
            return flow
    return None

def _fetch_eastmoney_h5_board_flow(secid: str, display_name: str) -> Any | None:
    cache_key = f"em-h5-board-flow|{_last_trading_day()}|{secid}"
    cached = _get_response_cache(cache_key)
    if cached is not None:
        return cached
    resp = requests.get(
        "https://emdatah5.eastmoney.com/dc/ZJLX/getZJLXData",
        params={
            "secid": secid,
            "fields": "f3,f57,f58,f135,f136,f137,f138,f139,f140,f141,f142,f143,f144,f145,f146,f147,f148,f149,f86",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://emdatah5.eastmoney.com/"},
        timeout=6,
    )
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or {}
    if not data:
        return None
    name = str(data.get("f58") or display_name)
    main_net = round(float(data.get("f137") or 0) / 1e8, 2)
    super_large_net = round(float(data.get("f140") or 0) / 1e8, 2)
    large_net = round(float(data.get("f143") or 0) / 1e8, 2)
    main_inflow = round(super_large_net + large_net, 2) if (super_large_net or large_net) else main_net
    raw_change = data.get("f3")
    try:
        board_change_pct = (
            round(float(raw_change) / 100, 4)
            if raw_change not in (None, "", "-")
            else None
        )
    except (TypeError, ValueError):
        board_change_pct = None
    flow = SimpleNamespace(
        name=name,
        display_name=name,
        raw_name=name,
        board_code=secid.split(".")[-1],
        provider="eastmoney-h5",
        theme_line=name,
        mainline=name,
        subline="",
        category="行业资金流",
        # Eastmoney f3 is scaled by 100.  Missing f3 remains missing; a zero
        # must never be manufactured merely because this endpoint primarily
        # serves fund-flow fields.
        change_pct=board_change_pct,
        net_inflow=main_net,
        main_inflow=main_inflow,
        strength=max(0, min(100, int(50 + main_net / 2))),
        leaders=[],
        timeline=_eastmoney_h5_board_timeline(secid, main_net),
        updated_at=shanghai_now_naive(),
    )
    _set_response_cache(cache_key, flow)
    return flow

def _eastmoney_h5_board_timeline(secid: str, current: float) -> list[Any]:
    now_label = shanghai_now_naive().strftime("%H:%M")
    return [SimpleNamespace(time=now_label, value=current)]

def _holding_flow_match_score(
    holding: Holding,
    flow: Any,
    *,
    allow_network: bool = True,
) -> int:
    theme_profile = _holding_theme_profile(holding, allow_network=allow_network)
    target_text = (
        f"{holding.name} {holding.code} {holding.position_type} {holding.next_discipline} "
        f"{theme_profile['primary']} {' '.join(theme_profile['tags'])} "
        f"{theme_profile.get('industry', '')} {' '.join(theme_profile.get('concepts', []))}"
    )
    aliases = list(dict.fromkeys(list(theme_profile["flow_aliases"]) + _holding_sector_keywords(holding)))
    names = _sector_aliases(flow)
    score = 0
    for name in names:
        if name and name in target_text:
            score += 4
    for alias in aliases:
        if any(alias in name or name in alias for name in names if name):
            score += 3
    for alias in theme_profile.get("priority_flow_aliases", []) or []:
        if any(alias in name or name in alias for name in names if name):
            score += 6
    return score

def _flow_matches_aliases(flow: Any, aliases: list[str]) -> bool:
    names = _sector_aliases(flow)
    return any(
        alias and any(alias in name or name in alias for name in names if name)
        for alias in aliases
    )

def _aggregate_flow_timeline(flows: list[Any]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for flow in flows:
        for point in getattr(flow, "timeline", []) or []:
            time_key = str(getattr(point, "time", "") or "")
            if not time_key:
                continue
            totals[time_key] += float(getattr(point, "value", 0) or 0)
    return dict(totals)

def _holding_stock_board_profile(
    holding: Holding,
    *,
    allow_network: bool = True,
) -> dict[str, Any]:
    code = str(holding.code or "").strip()
    cache_key = f"stock-board-profile|{code}"
    cached = _get_response_cache(cache_key)
    if cached is not None:
        return cached

    profile = {
        "industry": "",
        "concepts": [],
        "source": "",
    }
    if not re.fullmatch(r"\d{6}", code):
        return profile
    if not allow_network:
        return profile

    try:
        resp = requests.get(
            f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpOtherInfo/stockid/{code}.phtml",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        resp.raise_for_status()
        resp.encoding = "gb2312"
        html = resp.text
        industry = _extract_sina_stock_industry(html)
        concepts = _extract_sina_stock_concepts(html)
        if industry or concepts:
            profile = {
                "industry": industry,
                "concepts": concepts,
                "source": "sina-stock-board",
            }
            _set_response_cache(cache_key, profile)
            return profile
    except Exception:
        pass

    try:
        em = _fetch_em_stock_board(code)
        if em.get("industry") or em.get("concepts"):
            profile = {
                "industry": em.get("industry") or "",
                "concepts": em.get("concepts") or [],
                "source": "eastmoney-stock-detail",
            }
            _set_response_cache(cache_key, profile)
            return profile
    except Exception:
        pass

    try:
        ladder = market_provider.limit_up_ladder(force_refresh=False)
    except Exception:
        ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is not None:
        for group in getattr(ladder, "groups", []) or []:
            for stock in getattr(group, "stocks", []) or []:
                if str(getattr(stock, "code", "") or "") == code or str(getattr(stock, "name", "") or "") == str(holding.name or ""):
                    profile = {
                        "industry": str(getattr(stock, "industry", "") or ""),
                        "concepts": [str(item) for item in getattr(stock, "concepts", []) if str(item).strip()],
                        "source": "limit-up-ladder-cache",
                    }
                    _set_response_cache(cache_key, profile)
                    return profile

    _set_response_cache(cache_key, profile)
    return profile

def _extract_sina_stock_industry(html: str) -> str:
    match = re.search(r"所属行业板块.*?<tr>\s*<td[^>]*>(.*?)</td>", html, re.S)
    return _strip_html(match.group(1)) if match else ""

def _extract_sina_stock_concepts(html: str) -> list[str]:
    start = html.find("所属概念板块")
    if start < 0:
        return []
    section = html[start:]
    rows = re.findall(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>", section, re.S)
    concepts: list[str] = []
    skip = {"所属行业板块", "所属概念板块", "概念板块", "同概念个股"}
    for raw in rows:
        value = _strip_html(raw)
        if not value or value in skip or "备注" in value or "点击查看" in value or "对不起" in value:
            continue
        if len(value) > 30:
            continue
        if value not in concepts:
            concepts.append(value)
    return concepts

def _strip_html(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()

def _fetch_em_stock_board(code: str) -> dict[str, Any]:
    try:
        market = "1" if code.startswith("6") else "0"
        resp = requests.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={
                "secid": f"{market}.{code}",
                "fields": "f57,f58,f127,f55,f100,f102,f103",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        data = resp.json().get("data") or {}
        industry = str(data.get("f100") or "")
        concept_str = str(data.get("f103") or "")
        concepts: list[str] = []
        if concept_str and concept_str != "-":
            concepts = [c.strip() for c in concept_str.split(",") if c.strip()]
        return {"industry": industry, "concepts": concepts}
    except Exception:
        return {}

def _holding_theme_prefers_concept_flow(theme_profile: dict[str, Any]) -> bool:
    return bool(theme_profile.get("prefer_concept"))

def _holding_theme_profile(
    holding: Holding,
    *,
    allow_network: bool = True,
) -> dict[str, Any]:
    code = str(holding.code or "").strip()
    name = str(holding.name or "")
    board_profile = _holding_stock_board_profile(holding, allow_network=allow_network)
    industry = str(board_profile.get("industry") or "")
    concepts = [
        str(item)
        for item in board_profile.get("concepts", [])
        if str(item).strip() and str(item) != industry
    ]
    text = f"{code} {name} {industry} {' '.join(concepts)} {holding.position_type or ''} {holding.next_discipline or ''}"

    scored: list[tuple[int, dict[str, Any]]] = []
    for rule in _THEME_RULES:
        score = 0
        for keyword in rule["keywords"]:
            if keyword and keyword in text:
                score += 3 if keyword in " ".join(concepts) else 2
        if rule["primary"] in text:
            score += 4
        if score:
            scored.append((score, rule))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        rule = scored[0][1]
        tags = list(dict.fromkeys([*rule["tags"], *[c for c in concepts if any(k in c for k in rule["keywords"])][:4]]))
        aliases = list(dict.fromkeys([*rule["flow_aliases"], industry, *concepts]))
        primary = str(rule["primary"])
        if industry and industry not in primary and any(key in industry for key in ("半导体", "航天", "电子", "医药", "机器人", "新能源", "化工")):
            primary = f"{primary} / {industry}"
        return {
            "primary": primary,
            "tags": tags,
            "flow_aliases": aliases,
            "priority_flow_aliases": list(dict.fromkeys([*rule["flow_aliases"], *rule["tags"]])),
            "preferred_industry_boards": rule.get("preferred_industry_boards", []),
            "prefer_concept": bool(rule.get("prefer_concept")),
            "industry": industry,
            "concepts": concepts,
            "source": board_profile.get("source") or "theme-rules",
        }

    fallback = _holding_sector_keywords(holding)
    aliases = list(dict.fromkeys([*fallback, industry, *concepts]))
    return {
        "primary": fallback[0] if fallback else (industry or "待确认主线"),
        "tags": aliases[:8],
        "flow_aliases": aliases,
        "priority_flow_aliases": fallback,
        "preferred_industry_boards": [],
        "prefer_concept": False,
        "industry": industry,
        "concepts": concepts,
        "source": board_profile.get("source") or "fallback",
    }

def _holding_sector_keywords(holding: Holding) -> list[str]:
    text = f"{holding.name} {holding.position_type} {holding.next_discipline}"
    mapping = {
        "半导体": ("长电", "半导体", "芯片", "封测", "科创半导体"),
        "先进封装": ("长电", "封装", "封测"),
        "电子信息": ("半导体", "芯片", "电子", "PCB", "消费电子"),
        "AI算力": ("浪潮", "算力", "服务器", "AI", "人工智能", "CPO", "利通", "英伟达", "云计算", "液冷", "东数西算"),
        "商业航天": ("航天", "卫星", "火箭", "军工", "军民融合"),
        "创新药": ("海正", "医药", "创新药", "药业", "生物"),
        "电子元件": ("PCB", "消费电子", "OLED"),
        "机器人": ("机器人", "减速器", "人形"),
        "新能源": ("新能源", "光伏", "锂电", "固态电池", "储能"),
        "化工材料": ("化工", "塑料", "材料"),
    }
    hits: list[str] = []
    for sector, keywords in mapping.items():
        if any(keyword in text for keyword in keywords):
            hits.append(sector)
    return hits

def _fallback_holding_sector(holding: Holding) -> str:
    return str(_holding_theme_profile(holding)["primary"])

def _sector_family(name: str) -> str:
    if any(key in name for key in ("半导体", "芯片", "封装", "电子", "AI", "算力", "服务器", "CPO", "光模块")):
        return "科技"
    if any(key in name for key in ("航天", "卫星", "火箭", "军工")):
        return "商业航天"
    if any(key in name for key in ("医药", "创新药", "药")):
        return "医药"
    if any(key in name for key in ("机器人", "减速器", "人形")):
        return "机器人"
    if any(key in name for key in ("新能源", "光伏", "锂电", "固态电池", "储能")):
        return "新能源"
    if any(key in name for key in ("化工", "塑料", "材料")):
        return "化工材料"
    return name

def _sell_plan(holding: Holding) -> SellPlanOut:
    guard = profit_guard_price(holding.cost_price, holding.current_price)
    return SellPlanOut(
        code=holding.code,
        name=holding.name,
        first_trim_price=round(max(holding.current_price * 1.04, holding.cost_price * 1.05), 2),
        second_exit_price=round(max(holding.current_price * 1.075, holding.cost_price * 1.1), 2),
        failure_price=round(guard or holding.cost_price * 0.96, 2),
        sell_ratios=["第一层卖 1/3", "第二层再卖 1/3", "尾仓按封板与 VWAP 决定"],
        allow_buyback=False,
        buyback_condition="重新站上确认位且板块仍为前排，买回不超过原仓 1/2",
        condition_orders=[
            "较前收盘涨 4%-5% 或达到压力位：卖 1/3",
            "涨 7%-8% 或接近涨停但封板弱：再卖 1/3",
            "从盘中高点回落 5%：剩余进攻仓至少减半",
        ],
    )
