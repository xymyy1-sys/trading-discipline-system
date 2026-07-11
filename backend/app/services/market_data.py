import html
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from random import Random
from typing import Any

from app.schemas.trading import (
    BoardFlowPanelOut,
    DarkTradeItem,
    DarkTradeOut,
    HotThemeItem,
    HotThemesOut,
    InformationDifferentialOut,
    InformationItem,
    LimitUpClusterOut,
    LimitUpGroupOut,
    LimitUpLadderOut,
    LimitUpStockOut,
    SectorConstituentOut,
    SectorDetailOut,
    SectorFlowItem,
    SectorFlowOut,
    SectorFlowPoint,
    ThemeRadarItem,
    ThemeRadarOut,
    ThemeStockRole,
)
import requests

from app.services.cache import (
    _get_response_cache,
    _set_response_cache,
    _cache_good_flow,
    _get_cached_flow,
    _record_snapshot,
    _get_snapshots,
)
from app.services.mainline_classifier import (
    _MAINLINE_DEFS,
    _KNOWN_MAINLINE_NAMES,
    _BROAD_STYLE_LABELS,
    _SECTOR_TAXONOMY,
    _classify_sector_taxonomy,
)

def _last_trading_day() -> str:
    d = datetime.now()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")

def _is_trading_day() -> bool:
    return datetime.now().weekday() < 5

def _is_trading_time() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 555 <= t <= 905

def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

def _money_yuan_to_yi(value: Any) -> float:
    return round(_safe_float(value) / 1e8, 2)

def _synthetic_timeline_points(label_times: list[str], final_value: float) -> list[SectorFlowPoint]:
    if abs(final_value) < 0.01:
        return [SectorFlowPoint(time=label, value=0.0) for label in label_times]
    sign = 1 if final_value >= 0 else -1
    mag = abs(final_value)
    wave_profile = [0.0, 0.08, -0.04, 0.06, -0.09, 0.05, 0.11, -0.06, 0.0]
    progress = [0.08, 0.16, 0.28, 0.35, 0.48, 0.56, 0.72, 0.84, 1.0]
    amplitude = min(0.24, max(0.06, 2.8 / (mag + 8)))
    pts: list[SectorFlowPoint] = []
    rng = Random(int(abs(hash(str(final_value))) % 10000))
    for i, label in enumerate(label_times):
        jitter = rng.uniform(-0.028, 0.028)
        shaped = progress[i] + wave_profile[i] * amplitude + jitter
        shaped = max(0.02, min(1.08, shaped))
        pts.append(SectorFlowPoint(time=label, value=round(sign * mag * shaped, 2)))
    pts[-1].value = round(final_value, 2)
    return pts

def _current_timeline_point(final_value: float) -> list[SectorFlowPoint]:
    return [SectorFlowPoint(time="当前", value=round(float(final_value or 0), 2))]

def _sanitize_flow_timeline(points: list[SectorFlowPoint], final_value: float) -> list[SectorFlowPoint]:
    cleaned = [
        SectorFlowPoint(time=str(point.time), value=round(float(point.value or 0), 2))
        for point in points
        if str(point.time or "").strip()
    ]
    final = round(float(final_value or 0), 2)
    if not cleaned:
        return _current_timeline_point(final)
    if abs(cleaned[-1].value - final) > 0.01:
        cleaned.append(SectorFlowPoint(time="当前", value=final))
    else:
        cleaned[-1].value = final
    return cleaned

class MarketDataProvider:
    def __init__(self) -> None:
        self.random = Random(20260704)

    def theme_radar(self, force_refresh: bool = False) -> ThemeRadarOut:
        cache_key = "theme-radar"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if cached is not None:
                return cached

        now = datetime.utcnow()
        notes: list[str] = []
        raw_items: list[dict[str, Any]] = []
        source_parts: list[str] = []

        for flow_type, theme_type in [("概念资金流", "概念"), ("行业资金流", "行业")]:
            try:
                rows = self._fetch_direct_eastmoney_sector_flow_raw(flow_type=flow_type, period="今日")
                for row in rows:
                    row["theme_type"] = theme_type
                raw_items.extend(rows)
                source_parts.append(f"eastmoney-{theme_type}")
                _cache_good_flow(f"{flow_type}|今日", rows, "eastmoney")
                if _is_trading_time():
                    _record_snapshot(flow_type, rows)
            except Exception as exc:
                notes.append(f"{theme_type}资金流暂不可用: {exc.__class__.__name__}")
                try:
                    rows = self._fetch_sina_sector_flow_raw(flow_type=flow_type, period="今日")
                    for row in rows:
                        row["theme_type"] = theme_type
                    raw_items.extend(rows)
                    source_parts.append(f"sina-{theme_type}")
                    _cache_good_flow(f"{flow_type}|今日", rows, "sina")
                    if _is_trading_time():
                        _record_snapshot(flow_type, rows)
                except Exception as sina_exc:
                    notes.append(f"{theme_type}新浪备用资金流暂不可用: {sina_exc.__class__.__name__}")
                    cached = _get_cached_flow(f"{flow_type}|今日")
                    if cached:
                        cached_rows, cached_source, _ = cached
                        for row in cached_rows:
                            row["theme_type"] = theme_type
                        raw_items.extend(cached_rows)
                        source_parts.append(f"{cached_source}-cached-{theme_type}")

        if not raw_items:
            raw_items = self._fallback_sector_flow_raw()
            for row in raw_items:
                row["theme_type"] = "诊断"
            source_parts.append("diagnostic")
            notes.append("外部板块数据不可用，显示诊断样例")

        deduped: dict[str, dict[str, Any]] = {}
        for row in raw_items:
            name = str(row.get("name") or "")
            if not name:
                continue
            row["mainline"] = self._classify_mainline(row)
            old = deduped.get(name)
            if old is None or self._raw_theme_score(row, []) > self._raw_theme_score(old, []):
                deduped[name] = row

        board_candidates = sorted(
            deduped.values(),
            key=lambda row: self._raw_theme_score(row, []),
            reverse=True,
        )
        candidates = self._aggregate_theme_mainlines(board_candidates)[:40]

        themes: list[ThemeRadarItem] = []
        for idx, raw in enumerate(candidates, start=1):
            constituents: list[dict[str, Any]] = []
            seed = raw.get("seed_board") if isinstance(raw.get("seed_board"), dict) else raw
            board_code = str(seed.get("board_code") or raw.get("board_code") or "").strip() or None
            if board_code:
                try:
                    if seed.get("provider") == "sina":
                        constituents = self._fetch_sina_sector_constituents_raw(board_code)
                    else:
                        constituents = self._fetch_sector_constituents_raw(board_code)
                except Exception as exc:
                    if idx <= 5:
                        notes.append(f"{raw.get('name')}成分股暂不可用: {exc.__class__.__name__}")
            themes.append(self._build_theme_item(raw, constituents, idx, include_timeline=idx <= 8))

        themes.sort(
            key=lambda item: (
                1 if item.name in _KNOWN_MAINLINE_NAMES else 0,
                item.score,
                item.net_inflow,
                len(item.related_boards),
                item.change_pct,
            ),
            reverse=True,
        )
        ranked: list[ThemeRadarItem] = []
        for idx, item in enumerate(themes, start=1):
            ranked.append(item.model_copy(update={"rank": idx}))

        resonance = [
            item for item in ranked
            if len(item.resonance_tags) >= 3 and item.stage not in {"退潮"}
        ][:6]
        strongest = ranked[0] if ranked else None
        avg_score = sum(item.score for item in ranked[:8]) / max(1, len(ranked[:8]))
        if avg_score >= 78:
            temperature = "强进攻"
        elif avg_score >= 66:
            temperature = "偏强"
        elif avg_score >= 54:
            temperature = "轮动"
        else:
            temperature = "低迷"

        result = ThemeRadarOut(
            source="+".join(dict.fromkeys(source_parts)) or "unknown",
            updated_at=now,
            market_temperature=temperature,
            strongest_theme=strongest,
            resonance=resonance,
            themes=ranked[:28],
            notes=notes or ["板块资金流已按交易主线聚合，原始板块保留为证据链"],
        )
        _set_response_cache(cache_key, result)
        return result

    def _classify_mainline(self, raw: dict[str, Any]) -> str:
        text_parts = [
            str(raw.get("name") or ""),
            str(raw.get("theme_type") or ""),
            " ".join(str(x) for x in raw.get("leaders", []) if str(x).strip()),
        ]
        text = " ".join(text_parts).lower()
        best_name = str(raw.get("name") or "其他题材")
        best_score = 0
        for line in _MAINLINE_DEFS:
            score = 0
            for kw in line["keywords"]:
                kw_text = str(kw).lower()
                if kw_text and kw_text in text:
                    score += 3 if kw_text in str(raw.get("name") or "").lower() else 1
            if score > best_score:
                best_score = score
                best_name = str(line["name"])
        return best_name

    def _is_broad_style_label(self, name: str) -> bool:
        upper_name = name.upper()
        return any(label.upper() in upper_name for label in _BROAD_STYLE_LABELS)

    def _aggregate_theme_mainlines(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            name = str(row.get("name") or "")
            mainline = str(row.get("mainline") or name or "其他题材")
            if self._is_broad_style_label(name):
                continue
            grouped[mainline].append(row)

        aggregates: list[dict[str, Any]] = []
        for line_name, items in grouped.items():
            ordered = sorted(items, key=lambda row: self._raw_theme_score(row, []), reverse=True)
            positive = [row for row in ordered if float(row.get("net_inflow") or 0) > 0]
            seed = ordered[0]
            net = sum(float(row.get("net_inflow") or 0) for row in items)
            main = sum(float(row.get("main_inflow") or 0) for row in items)
            weighted_base = sum(abs(float(row.get("net_inflow") or 0)) for row in items) or len(items)
            change = sum(float(row.get("change_pct") or 0) * (abs(float(row.get("net_inflow") or 0)) or 1) for row in items) / weighted_base
            related = [str(row.get("name") or "") for row in ordered if str(row.get("name") or "").strip()]
            leaders: list[str] = []
            for row in ordered:
                leaders.extend(str(x) for x in row.get("leaders", []) if str(x).strip())
            aggregates.append({
                "name": line_name,
                "board_code": str(seed.get("board_code") or ""),
                "provider": seed.get("provider"),
                "theme_type": "主线题材",
                "related_boards": list(dict.fromkeys(related))[:8],
                "component_boards": ordered[:6],
                "seed_board": seed,
                "change_pct": round(change, 2),
                "net_inflow": round(net, 2),
                "main_inflow": round(main, 2),
                "strength": max(0, min(100, int(50 + change * 7 + net * 0.9 + len(positive) * 2))),
                "leaders": list(dict.fromkeys(leaders))[:6] or ["待识别"],
                "limit_up_count": sum(int(row.get("limit_up_count") or 0) for row in items),
                "stock_count": sum(int(row.get("stock_count") or 0) for row in items),
                "avg_change": round(change, 2),
                "component_count": len(items),
            })
        return sorted(
            aggregates,
            key=lambda row: (
                1 if str(row.get("name") or "") in _KNOWN_MAINLINE_NAMES else 0,
                self._raw_theme_score(row, []),
                float(row.get("net_inflow") or 0),
                int(row.get("component_count") or 1),
                float(row.get("change_pct") or 0),
            ),
            reverse=True,
        )

    def _raw_theme_score(self, raw: dict[str, Any], constituents: list[dict[str, Any]]) -> int:
        change = float(raw.get("change_pct") or 0)
        net = float(raw.get("net_inflow") or 0)
        main = float(raw.get("main_inflow") or 0)
        limit_count = int(raw.get("limit_up_count") or 0)
        component_count = int(raw.get("component_count") or 1)
        positive_core = len([it for it in constituents[:12] if float(it.get("change_pct") or 0) >= 5])
        score = 45 + change * 5.2 + net * 1.45 + main * 0.95 + limit_count * 3 + positive_core * 2
        score += min(10, max(0, component_count - 1) * 2.5)
        return max(0, min(100, int(score)))

    def _build_theme_item(
        self,
        raw: dict[str, Any],
        constituents: list[dict[str, Any]],
        rank: int,
        include_timeline: bool = True,
    ) -> ThemeRadarItem:
        score = self._raw_theme_score(raw, constituents)
        roles = self._classify_theme_stocks(raw, constituents)
        stage, stage_reason = self._judge_theme_stage(raw, constituents, score)
        tags = self._theme_resonance_tags(raw, constituents, score)
        leader_names = [role.name for role in roles if role.role in {"情绪龙头", "连板标的"}][:3]
        for leader in raw.get("leaders", []):
            leader = str(leader).strip()
            if leader and leader != "待识别" and leader not in leader_names:
                leader_names.append(leader)

        if stage in {"启动", "发酵"}:
            action = "盯前排确认，不追后排；只在核心股分歧转强时考虑"
        elif stage in {"加速"}:
            action = "只看龙头和容量中军，后排补涨降低仓位"
        elif stage == "高潮":
            action = "防一致性兑现，优先做持仓去弱留强"
        elif stage == "分歧":
            action = "观察核心承接，弱转强再处理"
        else:
            action = "回避新开仓，等待新主线"

        risk = "核心股断板或容量中军放量回落，会触发题材退潮"
        if float(raw.get("net_inflow") or 0) < 0:
            risk = "资金净流出，题材可能只是消息脉冲"
        elif stage == "高潮":
            risk = "一致性过强，次日容易高开低走或强分歧"

        return ThemeRadarItem(
            name=str(raw.get("name") or "未知题材"),
            board_code=str(raw.get("board_code") or "") or None,
            theme_type=str(raw.get("theme_type") or "板块"),
            related_boards=[str(x) for x in raw.get("related_boards", []) if str(x).strip()][:8],
            stage=stage,
            stage_reason=stage_reason,
            score=score,
            rank=rank,
            change_pct=round(float(raw.get("change_pct") or 0), 2),
            net_inflow=round(float(raw.get("net_inflow") or 0), 2),
            main_inflow=round(float(raw.get("main_inflow") or 0), 2),
            limit_up_count=int(raw.get("limit_up_count") or 0),
            stock_count=int(raw.get("stock_count") or len(constituents) or 0),
            leader_names=leader_names[:4] or ["待识别"],
            core_stocks=roles[:8],
            timeline=self._theme_timeline(raw) if include_timeline else _synthetic_timeline_points(
                ["09:30", "10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"],
                float(raw.get("net_inflow") or 0),
            ),
            resonance_tags=tags,
            action=action,
            risk=risk,
        )

    def _classify_theme_stocks(
        self,
        raw: dict[str, Any],
        constituents: list[dict[str, Any]],
    ) -> list[ThemeStockRole]:
        roles: list[ThemeStockRole] = []
        used: set[str] = set()

        def add(stock: dict[str, Any], role: str, reason: str) -> None:
            code = str(stock.get("code") or "")
            name = str(stock.get("name") or stock.get("code") or "")
            key = code or name
            if not key or key in used:
                return
            used.add(key)
            roles.append(ThemeStockRole(
                code=code,
                name=name,
                role=role,
                change_pct=round(float(stock.get("change_pct") or 0), 2),
                amount=round(float(stock.get("amount") or 0), 2),
                reason=reason,
            ))

        ordered = sorted(
            constituents,
            key=lambda item: (float(item.get("change_pct") or 0), float(item.get("amount") or 0)),
            reverse=True,
        )
        limit_like = [it for it in ordered if float(it.get("change_pct") or 0) >= 9.5]
        if limit_like:
            add(limit_like[0], "情绪龙头", "板块内涨幅最强，接近或达到涨停")
        elif ordered:
            add(ordered[0], "情绪龙头", "板块内涨幅居前")

        for stock in limit_like[1:4]:
            add(stock, "连板标的", "涨停队列成员，需要结合连板高度确认")

        liquid = sorted(
            [it for it in constituents if float(it.get("change_pct") or 0) > 0],
            key=lambda item: float(item.get("amount") or 0),
            reverse=True,
        )
        if liquid:
            add(liquid[0], "容量中军", "成交额靠前，代表大资金承接")

        trend = [
            it for it in liquid
            if 2.0 <= float(it.get("change_pct") or 0) < 9.5
        ]
        if trend:
            add(trend[0], "趋势核心", "非涨停但量价强，适合观察趋势延续")

        for stock in ordered:
            if len(roles) >= 8:
                break
            add(stock, "前排强势", "板块涨幅前排")

        if not roles:
            fallback_roles = ["情绪龙头", "容量中军", "前排强势", "趋势核心"]
            for i, leader in enumerate(raw.get("leaders", [])):
                name = str(leader).strip()
                if name and name != "待识别":
                    role = fallback_roles[min(i, len(fallback_roles) - 1)]
                    add({"code": "", "name": name, "change_pct": raw.get("change_pct", 0), "amount": 0}, role, "板块领涨线索，待成分股行情确认")
        return roles

    def _judge_theme_stage(
        self,
        raw: dict[str, Any],
        constituents: list[dict[str, Any]],
        score: int,
    ) -> tuple[str, str]:
        change = float(raw.get("change_pct") or 0)
        net = float(raw.get("net_inflow") or 0)
        limit_count = int(raw.get("limit_up_count") or 0)
        hot_count = len([it for it in constituents if float(it.get("change_pct") or 0) >= 5])
        weak_count = len([it for it in constituents if float(it.get("change_pct") or 0) <= -3])

        if net < -1 and change < 0:
            return "退潮", "板块下跌且资金净流出"
        if score >= 86 and (limit_count >= 5 or hot_count >= 8):
            return "高潮", "涨停/大涨扩散较充分，情绪一致性偏高"
        if score >= 78 and net > 5 and change > 2.5:
            return "加速", "资金与涨幅同步放大"
        if weak_count >= 5 and hot_count >= 3:
            return "分歧", "内部强弱分化，需要看核心承接"
        if score >= 64 and net > 1:
            return "发酵", "资金开始集中，前排个股已出现"
        return "启动", "资金或涨幅刚开始显现，仍需次日确认"

    def _theme_resonance_tags(
        self,
        raw: dict[str, Any],
        constituents: list[dict[str, Any]],
        score: int,
    ) -> list[str]:
        tags: list[str] = []
        if float(raw.get("net_inflow") or 0) >= 3:
            tags.append("资金净流入放大")
        if float(raw.get("main_inflow") or 0) >= 1.5:
            tags.append("主力资金确认")
        if float(raw.get("change_pct") or 0) >= 2:
            tags.append("板块涨幅共振")
        if int(raw.get("limit_up_count") or 0) >= 3:
            tags.append("涨停扩散")
        if any(float(it.get("change_pct") or 0) >= 9.5 for it in constituents):
            tags.append("核心股涨停")
        if any(float(it.get("amount") or 0) >= 8 for it in constituents):
            tags.append("容量承接")
        if int(raw.get("component_count") or 0) >= 2:
            tags.append("多板块共振")
        if score >= 75:
            tags.append("主线候选")
        return tags or ["待资金确认"]

    def _fetch_sector_constituents_raw(self, board_code: str) -> list[dict[str, Any]]:
        params = {
            "pn": "1",
            "pz": "80",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": f"b:{board_code}",
            "fields": "f12,f14,f2,f3,f5,f6,f20,f21,f62",
        }
        resp = requests.get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/center/boardlist.html",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=4,
        )
        resp.raise_for_status()
        rows = resp.json().get("data", {}).get("diff", [])
        if not rows:
            raise ValueError("empty sector constituents")
        return [
            {
                "code": str(row.get("f12") or ""),
                "name": str(row.get("f14") or ""),
                "price": float(row.get("f2") or 0),
                "change_pct": float(row.get("f3") or 0),
                "amount": round(float(row.get("f6") or 0) / 1e8, 2),
                "float_cap": round(float(row.get("f21") or 0) / 1e8, 2),
                "main_inflow": round(float(row.get("f62") or 0) / 1e8, 2),
            }
            for row in rows
        ]

    def _theme_timeline(self, raw: dict[str, Any]) -> list[SectorFlowPoint]:
        component_boards = raw.get("component_boards")
        if isinstance(component_boards, list) and component_boards:
            points_by_time: dict[str, float] = defaultdict(float)
            used = 0
            for component in component_boards[:2]:
                if not isinstance(component, dict):
                    continue
                board_code = str(component.get("board_code") or "").strip()
                if component.get("provider") != "sina" or not board_code:
                    continue
                try:
                    for point in self._fetch_sina_sector_intraday_flow(board_code):
                        points_by_time[point.time] += point.value
                    used += 1
                except Exception:
                    continue
            if used and points_by_time:
                return [
                    SectorFlowPoint(time=key, value=round(points_by_time[key], 2))
                    for key in sorted(points_by_time)
                ]

        flow_type = "概念资金流" if str(raw.get("theme_type")) == "概念" else "行业资金流"
        board_code = str(raw.get("board_code") or "").strip()
        if raw.get("provider") == "sina" and board_code:
            try:
                return self._fetch_sina_sector_intraday_flow(board_code)
            except Exception:
                pass

        name = str(raw.get("name") or "")
        snaps = _get_snapshots(flow_type)
        if len(snaps) >= 2:
            points: list[SectorFlowPoint] = []
            for snap in snaps:
                snap_time = str(snap.get("time") or "")
                for snap_item in snap.get("items", []):
                    if str(snap_item.get("name")) == name:
                        points.append(SectorFlowPoint(
                            time=snap_time,
                            value=round(float(snap_item.get("net_inflow") or 0), 2),
                        ))
                        break
            if len(points) >= 2:
                return points

        label_times = ["09:30", "10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
        return _synthetic_timeline_points(label_times, float(raw.get("net_inflow") or 0))

    def _fetch_sina_sector_flow_raw(self, flow_type: str, period: str) -> list[dict[str, Any]]:
        if period != "今日":
            raise ValueError("sina sector flow only supports intraday ranking")
        fenlei_map = {"行业资金流": "0", "概念资金流": "1", "地域资金流": "2"}
        fenlei = fenlei_map.get(flow_type, "0")
        resp = requests.get(
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_bk",
            params={"fenlei": fenlei, "sort": "netamount", "page": "1", "num": "300", "asc": "0"},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://vip.stock.finance.sina.com.cn/moneyflow/",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=4,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise ValueError("empty sina sector flow")

        results: list[dict[str, Any]] = []
        for row in rows:
            category = str(row.get("category") or "")
            cate_type = str(row.get("cate_type") or fenlei)
            leader = str(row.get("ts_name") or "").strip() or "待识别"
            change_pct = float(row.get("avg_changeratio") or 0) * 100
            net_inflow = float(row.get("netamount") or 0) / 1e8
            main_ratio = float(row.get("r0_ratio") or row.get("ratioamount") or 0)
            main_inflow = net_inflow * max(0.35, min(0.95, abs(main_ratio) * 4))
            results.append({
                "name": str(row.get("name") or "未知板块"),
                "board_code": f"{cate_type}/{category}" if category else "",
                "provider": "sina",
                "change_pct": round(change_pct, 2),
                "net_inflow": round(net_inflow, 2),
                "main_inflow": round(main_inflow, 2),
                "strength": max(0, min(100, int(50 + change_pct * 8 + net_inflow * 1.2))),
                "leaders": [leader],
                "leader_symbol": str(row.get("ts_symbol") or ""),
                "leader_change_pct": round(float(row.get("ts_changeratio") or 0) * 100, 2),
                "limit_up_count": 1 if float(row.get("ts_changeratio") or 0) >= 0.095 else 0,
                "stock_count": 0,
                "avg_change": round(change_pct, 2),
            })
        return results

    def _fetch_sina_sector_intraday_flow(self, board_code: str) -> list[SectorFlowPoint]:
        resp = requests.get(
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssx_bkzj_fszs",
            params={"bankuai": board_code, "page": "1", "num": "260", "sort": "time"},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://vip.stock.finance.sina.com.cn/moneyflow/",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=3,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        if not rows:
            raise ValueError("empty sina sector intraday flow")

        dedup: dict[str, SectorFlowPoint] = {}
        for row in rows:
            tick = str(row.get("ticktime") or "")[:5]
            if not tick:
                continue
            value = round(float(row.get("netamount") or 0) / 1e8, 2)
            dedup[tick] = SectorFlowPoint(time=tick, value=value)

        points = [dedup[key] for key in sorted(dedup)]
        if len(points) > 90:
            sampled = [pt for i, pt in enumerate(points) if i % 3 == 0]
            if sampled[-1].time != points[-1].time:
                sampled.append(points[-1])
            return sampled
        return points

    def _fetch_sina_sector_constituents_raw(self, board_code: str) -> list[dict[str, Any]]:
        resp = requests.get(
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj",
            params={"bankuai": board_code, "page": "1", "num": "80", "sort": "netamount", "asc": "0"},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://vip.stock.finance.sina.com.cn/moneyflow/",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=4,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise ValueError("empty sina sector constituents")
        return [
            {
                "code": str(row.get("symbol") or "")[-6:],
                "name": str(row.get("name") or ""),
                "price": float(row.get("trade") or 0),
                "change_pct": round(float(row.get("changeratio") or 0) * 100, 2),
                "amount": round(float(row.get("amount") or 0) / 1e8, 2),
                "main_inflow": round(float(row.get("r0_net") or row.get("netamount") or 0) / 1e8, 2),
                "net_inflow": round(float(row.get("netamount") or 0) / 1e8, 2),
                "turnover": round(float(row.get("turnover") or 0), 2),
            }
            for row in rows
        ]

    def sector_flow(
        self,
        flow_type: str = "行业资金流",
        period: str = "今日",
        force_refresh: bool = False,
    ) -> SectorFlowOut:
        response_cache_key = f"sector-flow|{flow_type}|{period}"
        if not force_refresh:
            cached = _get_response_cache(response_cache_key)
            if cached is not None:
                return cached

        now = datetime.utcnow()
        raw_items: list[dict[str, Any]] = []
        source = "eastmoney"
        cache_key = f"{flow_type}|{period}"
        data_date: str | None = None

        try:
            raw_items = self._fetch_direct_eastmoney_sector_flow_raw(flow_type=flow_type, period=period)
            _cache_good_flow(cache_key, raw_items, "eastmoney")
        except Exception:
            try:
                raw_items = self._fetch_akshare_sector_flow_raw(flow_type=flow_type, period=period)
                source = "akshare/eastmoney"
                _cache_good_flow(cache_key, raw_items, "akshare/eastmoney")
            except Exception:
                try:
                    raw_items = self._fetch_sina_sector_flow_raw(flow_type=flow_type, period="今日")
                    source = "sina"
                    _cache_good_flow(cache_key, raw_items, "sina")
                except Exception:
                    cached = _get_cached_flow(cache_key)
                    if cached:
                        raw_items, source, data_date = cached
                        if not _is_trading_day():
                            data_date = _last_trading_day()
                        source = f"{source}+cached"
                    else:
                        raw_items = self._fallback_sector_flow_raw()
                        source = "diagnostic-fallback"

        if _is_trading_time():
            try:
                _record_snapshot(flow_type, raw_items)
            except Exception:
                pass

        snaps = _get_snapshots(flow_type)
        has_real_curve = len(snaps) >= 3
        label_times = ["09:30", "10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]

        def _net_of(raw: dict[str, Any]) -> float:
            return float(raw.get("net_inflow") or 0)

        items: list[SectorFlowItem] = []
        visible_raw_items = raw_items
        if flow_type == "概念资金流":
            theme_like = [
                raw for raw in raw_items
                if not self._is_broad_style_label(str(raw.get("name") or ""))
            ]
            if len(theme_like) >= 20:
                visible_raw_items = theme_like
        raw_ordered = sorted(visible_raw_items, key=_net_of, reverse=True)
        chart_codes = {
            str(raw.get("board_code") or "")
            for raw in raw_ordered[:10]
            if str(raw.get("board_code") or "").strip()
        }
        chart_codes.update(
            str(raw.get("board_code") or "")
            for raw in sorted([raw for raw in raw_ordered if _net_of(raw) < 0], key=_net_of)[:10]
            if str(raw.get("board_code") or "").strip()
        )
        has_eastmoney_intraday_curve = False
        for idx, raw in enumerate(raw_ordered, start=1):
            name = str(raw.get("name") or "未知板块")
            taxonomy = _classify_sector_taxonomy(raw)
            net = _net_of(raw)
            board_code = str(raw.get("board_code") or "")
            if raw.get("provider") == "eastmoney" and period == "今日" and board_code in chart_codes:
                try:
                    timeline = self._fetch_eastmoney_board_intraday_flow(board_code)
                    has_eastmoney_intraday_curve = len([point for point in timeline if point.time != "当前"]) >= 2
                except Exception:
                    timeline = _current_timeline_point(net)
            elif raw.get("provider") == "sina" and raw.get("board_code") and idx <= 8:
                try:
                    timeline = self._fetch_sina_sector_intraday_flow(str(raw.get("board_code")))
                except Exception:
                    timeline = _current_timeline_point(net)
            elif has_real_curve:
                timeline: list[SectorFlowPoint] = []
                for snap in snaps:
                    snap_time = str(snap.get("time") or "")
                    snap_val: float | None = None
                    for snap_item in snap.get("items", []):
                        if str(snap_item.get("name")) == name:
                            snap_val = float(snap_item.get("net_inflow") or 0)
                            break
                    if snap_val is not None:
                        timeline.append(SectorFlowPoint(time=snap_time, value=round(snap_val, 2)))
            else:
                timeline = _current_timeline_point(net)
            timeline = _sanitize_flow_timeline(timeline, net)

            items.append(
                SectorFlowItem(
                    name=name,
                    display_name=taxonomy["display_name"],
                    raw_name=taxonomy["raw_name"],
                    board_code=str(raw.get("board_code") or "") or None,
                    provider=str(raw.get("provider") or source).split("+")[0],
                    theme_line=taxonomy["mainline"],
                    mainline=taxonomy["mainline"],
                    subline=taxonomy["subline"],
                    category=taxonomy["category"],
                    change_pct=round(float(raw.get("change_pct") or 0), 2),
                    net_inflow=round(net, 2),
                    main_inflow=round(float(raw.get("main_inflow") or 0), 2),
                    strength=max(0, min(100, int(raw.get("strength") or 50))),
                    leaders=[str(l) for l in raw.get("leaders", []) if str(l).strip()][:4],
                    timeline=timeline,
                    flow_breakdown=[
                        {
                            "name": str(part.get("name") or ""),
                            "net": round(float(part.get("net") or 0), 2),
                            "ratio": round(float(part.get("ratio") or 0), 2),
                        }
                        for part in raw.get("flow_breakdown", [])
                        if str(part.get("name") or "").strip()
                    ],
                )
            )

        ordered = sorted(items, key=lambda x: x.net_inflow, reverse=True)
        inflow_items = [item for item in ordered if item.net_inflow > 0]
        outflow_items = sorted(
            [item for item in ordered if item.net_inflow < 0],
            key=lambda x: x.net_inflow,
        )
        base_source = source
        if has_eastmoney_intraday_curve:
            base_source = f"{source}+eastmoney-fflow"
        elif has_real_curve:
            base_source = f"{source}+snapshots:{len(snaps)}"
        elif len(snaps) > 0:
            base_source = f"{source}+estimates"
        if data_date:
            base_source = f"{base_source}|date:{data_date}"
        if not _is_trading_day() and "diagnostic" not in base_source:
            base_source += f"|最近交易日"
        result = SectorFlowOut(
            source=base_source,
            updated_at=now,
            inflow=inflow_items[:20],
            outflow=outflow_items[:20],
        )
        _set_response_cache(response_cache_key, result)
        return result

    def board_flow_panel(
        self,
        board_type: str = "行业",
        period: str = "今日",
        force_refresh: bool = False,
    ) -> BoardFlowPanelOut:
        normalized = self._normalize_board_type(board_type)
        flow_type = self._board_type_to_flow_type(normalized)
        cache_key = f"board-flow-panel|{normalized}|{period}"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if cached is not None:
                return cached

        notes: list[str] = []
        flow = self.sector_flow(
            flow_type=flow_type,
            period=period,
            force_refresh=force_refresh,
        )
        source = flow.source
        if normalized in {"风格", "港股"}:
            notes.append(f"东方财富公开板块资金接口未稳定提供{normalized}资金榜，当前按{flow_type.replace('资金流', '')}资金口径展示")
        if "sina" in source:
            notes.append("东方财富 push2 当前不可用，已回落到新浪资金流；分层资金可能缺失")
        if "diagnostic" in source:
            notes.append("外部资金流不可用，当前为诊断数据")
        if not any(item.timeline and len([p for p in item.timeline if p.time != "当前"]) >= 2 for item in flow.inflow + flow.outflow):
            notes.append("当前只有资金快照，主图不会绘制伪曲线；盘中多次刷新后可形成连续曲线")

        result = BoardFlowPanelOut(
            source=source,
            updated_at=flow.updated_at,
            board_type=normalized,
            period=period,
            inflow=flow.inflow[:20],
            outflow=flow.outflow[:20],
            notes=notes or ["东方财富板块资金流已同步"],
        )
        _set_response_cache(cache_key, result)
        return result

    def hot_themes(self, force_refresh: bool = False) -> HotThemesOut:
        cache_key = "hot-themes"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if cached is not None:
                return cached

        notes: list[str] = []
        items: list[HotThemeItem] = []
        try:
            hot_rows = self._fetch_eastmoney_hot_market_raw()
            items = self._build_hot_theme_items(hot_rows)
            if items and not any(abs(item.net_inflow) > 0.01 or abs(item.main_inflow) > 0.01 for item in items):
                notes.append("热点题材涨幅榜可用，但东方财富板块资金补充当前不可用")
        except Exception as exc:
            notes.append(f"东方财富市场热点页暂不可用: {exc.__class__.__name__}")
            radar = self.theme_radar(force_refresh=force_refresh)
            for item in radar.themes[:30]:
                items.append(HotThemeItem(
                    name=item.name,
                    board_code=item.board_code,
                    period="今日",
                    rank=item.rank,
                    change_pct=item.change_pct,
                    net_inflow=item.net_inflow,
                    main_inflow=item.main_inflow,
                    source=radar.source,
                    reason=item.stage_reason,
                    leaders=item.leader_names,
                ))

        if not items:
            fallback = self._fallback_sector_flow_raw()
            items = [
                HotThemeItem(
                    name=str(row.get("name") or "未知题材"),
                    board_code=str(row.get("board_code") or "") or None,
                    period="今日",
                    rank=idx,
                    change_pct=round(float(row.get("change_pct") or 0), 2),
                    net_inflow=round(float(row.get("net_inflow") or 0), 2),
                    main_inflow=round(float(row.get("main_inflow") or 0), 2),
                    source="diagnostic-fallback",
                    reason="外部热点接口不可用，使用诊断样例",
                    leaders=[str(x) for x in row.get("leaders", []) if str(x).strip()],
                )
                for idx, row in enumerate(fallback[:12], start=1)
            ]
            notes.append("热点题材外部源不可用，显示诊断样例")

        result = HotThemesOut(
            source="eastmoney-hotmarket" if not notes else "eastmoney-hotmarket+fallback",
            updated_at=datetime.utcnow(),
            items=items[:45],
            notes=notes or ["东方财富市场热点榜已同步；资金字段按板块资金榜补充"],
        )
        _set_response_cache(cache_key, result)
        return result

    def dark_trade(
        self,
        scope: str = "个股",
        trade_date: str | None = None,
        force_refresh: bool = False,
    ) -> DarkTradeOut:
        normalized = self._normalize_dark_scope(scope)
        date_text = (trade_date or _last_trading_day()).replace("-", "")
        cache_key = f"dark-trade|{normalized}|{date_text}"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if cached is not None:
                return cached

        notes = [
            "东方财富暗盘资金为算法统计口径，A股无官方交易所暗盘；用于观察潜在资金行为，不等同真实暗盘成交",
        ]
        try:
            rows, remote_date = self._fetch_eastmoney_dark_trade_raw(normalized, date_text)
            items = [self._build_dark_trade_item(row, normalized) for row in rows]
        except Exception as exc:
            notes.append(f"东方财富暗盘资金接口暂不可用: {exc.__class__.__name__}")
            rows = []
            remote_date = date_text
            items = []

        result = DarkTradeOut(
            source="eastmoney-darktrade" if items else "eastmoney-darktrade-unavailable",
            trade_date=str(remote_date or date_text),
            updated_at=datetime.utcnow(),
            scope=normalized,
            items=items[:60],
            notes=notes,
        )
        _set_response_cache(cache_key, result)
        return result

    def sector_detail(
        self,
        name: str,
        flow_type: str = "行业资金流",
        period: str = "今日",
        board_code: str | None = None,
        provider: str | None = None,
        force_refresh: bool = False,
    ) -> SectorDetailOut:
        cache_key = f"sector-detail|{flow_type}|{period}|{name}|{board_code or ''}|{provider or ''}"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if cached is not None:
                return cached

        notes: list[str] = []
        raw_items: list[dict[str, Any]] = []
        source = "eastmoney"
        try:
            raw_items = self._fetch_direct_eastmoney_sector_flow_raw(flow_type=flow_type, period=period)
        except Exception as exc:
            notes.append(f"东方财富板块排行暂不可用: {exc.__class__.__name__}")
            try:
                raw_items = self._fetch_sina_sector_flow_raw(flow_type=flow_type, period="今日")
                source = "sina"
            except Exception as sina_exc:
                notes.append(f"新浪板块排行暂不可用: {sina_exc.__class__.__name__}")
                cached = _get_cached_flow(f"{flow_type}|{period}")
                if cached:
                    raw_items, source, _ = cached
                    source = f"{source}+cached"
                else:
                    raw_items = self._fallback_sector_flow_raw()
                    source = "diagnostic-fallback"

        target = self._match_sector_raw(raw_items, name=name, board_code=board_code)
        if target is None:
            target = {
                "name": name,
                "board_code": board_code or "",
                "provider": provider or source,
                "change_pct": 0,
                "net_inflow": 0,
                "main_inflow": 0,
                "strength": 0,
                "leaders": [],
            }
            notes.append("未在当前资金榜匹配到板块，仅按传入名称展示")

        taxonomy = _classify_sector_taxonomy(target)
        target_provider = str(provider or target.get("provider") or source).split("+")[0]
        target_code = str(board_code or target.get("board_code") or "").strip()
        constituents_raw: list[dict[str, Any]] = []
        if target_code:
            try:
                if target_provider == "sina":
                    constituents_raw = self._fetch_sina_sector_constituents_raw(target_code)
                else:
                    constituents_raw = self._fetch_sector_constituents_raw(target_code)
            except Exception as exc:
                notes.append(f"成分股暂不可用: {exc.__class__.__name__}")

        if not constituents_raw:
            constituents_raw = self._fallback_sector_constituents_raw(str(target.get("name") or name))
            if "diagnostic" not in source:
                notes.append("使用诊断成分股样例，等待外部成分股接口恢复")

        constituents = [self._build_constituent(item, target) for item in constituents_raw]
        constituents.sort(key=lambda item: (item.change_pct, item.amount), reverse=True)
        limit_ups = [item for item in constituents if item.is_limit_up]

        result = SectorDetailOut(
            source=source,
            updated_at=datetime.utcnow(),
            name=str(target.get("name") or name),
            display_name=taxonomy["display_name"],
            raw_name=taxonomy["raw_name"],
            board_code=target_code or None,
            provider=target_provider or None,
            theme_line=taxonomy["mainline"],
            mainline=taxonomy["mainline"],
            subline=taxonomy["subline"],
            category=taxonomy["category"],
            change_pct=round(float(target.get("change_pct") or 0), 2),
            net_inflow=round(float(target.get("net_inflow") or 0), 2),
            main_inflow=round(float(target.get("main_inflow") or 0), 2),
            strength=max(0, min(100, int(target.get("strength") or 0))),
            leaders=[str(l) for l in target.get("leaders", []) if str(l).strip()][:6],
            constituents=constituents[:80],
            limit_up_stocks=limit_ups[:20],
            flow_breakdown=[
                {
                    "name": str(part.get("name") or ""),
                    "net": round(float(part.get("net") or 0), 2),
                    "ratio": round(float(part.get("ratio") or 0), 2),
                }
                for part in target.get("flow_breakdown", [])
                if str(part.get("name") or "").strip()
            ],
            notes=notes or ["板块成分股已同步"],
        )
        _set_response_cache(cache_key, result)
        return result

    def limit_up_ladder(self, trade_date: str | None = None, force_refresh: bool = False) -> LimitUpLadderOut:
        target_date = trade_date or _last_trading_day()
        cache_key = f"limit-up-ladder|{target_date}"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if cached is not None:
                return cached

        notes: list[str] = []
        source = "akshare/eastmoney"
        raw_items: list[dict[str, Any]] = []
        if not _is_trading_day() and trade_date is None:
            notes.append(f"非交易日，展示最近交易日 {target_date} 的涨停池")
        try:
            raw_items = self._fetch_limit_up_pool_raw(target_date)
        except Exception as exc:
            notes.append(f"涨停池暂不可用: {exc.__class__.__name__}")
            raw_items = self._fallback_limit_up_pool_raw()
            source = "diagnostic-fallback"

        stocks = [self._build_limit_up_stock(item) for item in raw_items]
        stocks.sort(key=lambda item: (item.consecutive_limit_days, item.sealed_amount, item.amount), reverse=True)

        by_level: dict[int, list[LimitUpStockOut]] = defaultdict(list)
        for stock in stocks:
            by_level[max(1, stock.consecutive_limit_days)].append(stock)

        groups: list[LimitUpGroupOut] = []
        for level in sorted(by_level.keys(), reverse=True):
            groups.append(LimitUpGroupOut(
                level=level,
                label=f"{level}板" if level < 6 else "6板及以上",
                stocks=by_level[level],
            ))

        clusters = self._build_limit_up_clusters(stocks)
        summary = self._limit_up_summary(stocks, clusters)
        result = LimitUpLadderOut(
            source=source,
            trade_date=target_date,
            updated_at=datetime.utcnow(),
            groups=groups,
            clusters=clusters,
            summary=summary,
            notes=notes or ["涨停天梯已按连板高度和题材聚类"],
        )
        _set_response_cache(cache_key, result)
        return result

    def _match_sector_raw(
        self,
        raw_items: list[dict[str, Any]],
        name: str,
        board_code: str | None = None,
    ) -> dict[str, Any] | None:
        if board_code:
            for item in raw_items:
                if str(item.get("board_code") or "") == board_code:
                    return item
        for item in raw_items:
            if str(item.get("name") or "") == name:
                return item
        for item in raw_items:
            item_name = str(item.get("name") or "")
            if name in item_name or item_name in name:
                return item
        return None

    def _build_constituent(self, raw: dict[str, Any], sector: dict[str, Any]) -> SectorConstituentOut:
        change_pct = round(float(raw.get("change_pct") or 0), 2)
        mainline = self._classify_mainline({
            "name": str(sector.get("name") or ""),
            "leaders": [raw.get("name")],
            "theme_type": sector.get("theme_type", ""),
        })
        return SectorConstituentOut(
            code=str(raw.get("code") or ""),
            name=str(raw.get("name") or ""),
            price=round(float(raw.get("price") or 0), 2),
            change_pct=change_pct,
            amount=round(float(raw.get("amount") or 0), 2),
            turnover=round(float(raw.get("turnover") or 0), 2),
            main_inflow=round(float(raw.get("main_inflow") or 0), 2),
            net_inflow=round(float(raw.get("net_inflow") or 0), 2),
            float_cap=round(float(raw.get("float_cap") or 0), 2),
            is_limit_up=change_pct >= 9.5,
            consecutive_limit_days=max(1, int(raw.get("consecutive_limit_days") or 1)) if change_pct >= 9.5 else 0,
            concepts=list(dict.fromkeys([
                str(sector.get("name") or ""),
                mainline,
            ]))[:4],
        )

    def _fallback_sector_constituents_raw(self, sector_name: str) -> list[dict[str, Any]]:
        bases = ["龙头股份", "容量核心", "趋势前排", "补涨一号", "补涨二号", "中军标的", "辨识度股", "低位首板"]
        rows: list[dict[str, Any]] = []
        for idx, label in enumerate(bases, start=1):
            change = round(10.02 - idx * 0.72 + self.random.uniform(-0.25, 0.35), 2)
            rows.append({
                "code": f"60{idx:04d}",
                "name": f"{sector_name[:2]}{label}",
                "price": round(8 + idx * 1.7, 2),
                "change_pct": change,
                "amount": round(12 - idx * 0.9, 2),
                "turnover": round(4 + idx * 0.8, 2),
                "main_inflow": round(1.8 - idx * 0.12, 2),
                "net_inflow": round(1.2 - idx * 0.1, 2),
                "float_cap": round(80 + idx * 15, 2),
            })
        return rows

    def _fetch_limit_up_pool_raw(self, trade_date: str) -> list[dict[str, Any]]:
        date_text = trade_date.replace("-", "")
        try:
            rows = self._fetch_direct_limit_up_pool_raw(date_text)
            if rows:
                return rows
        except Exception:
            pass

        import akshare as ak
        frame = ak.stock_zt_pool_em(date=date_text)
        if frame.empty:
            raise ValueError("empty limit-up pool")
        results: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            results.append({str(k): row.get(k) for k in frame.columns})
        return results

    def _fetch_direct_limit_up_pool_raw(self, date_text: str) -> list[dict[str, Any]]:
        resp = requests.get(
            "https://push2ex.eastmoney.com/getTopicZTPool",
            params={
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": 0,
                "pagesize": 10000,
                "sort": "fbt:asc",
                "date": date_text,
                "_": int(datetime.now().timestamp() * 1000),
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/ztb/detail",
            },
            timeout=6,
        )
        resp.raise_for_status()
        payload = resp.json()
        pool = (payload.get("data") or {}).get("pool") or []
        if not pool:
            raise ValueError("empty direct limit-up pool")

        def format_time(value: Any) -> str:
            text = str(value or "").zfill(6)
            if len(text) < 6:
                return ""
            return f"{text[:2]}:{text[2:4]}:{text[4:6]}"

        results: list[dict[str, Any]] = []
        for row in pool:
            if not isinstance(row, dict):
                continue
            zttj = row.get("zttj") if isinstance(row.get("zttj"), dict) else {}
            level = int(row.get("lbc") or zttj.get("ct") or zttj.get("days") or 1)
            results.append({
                "代码": str(row.get("c") or ""),
                "名称": str(row.get("n") or ""),
                "最新价": round(float(row.get("p") or 0) / 1000, 3),
                "涨跌幅": float(row.get("zdp") or 0),
                "成交额": float(row.get("amount") or 0),
                "换手率": float(row.get("hs") or 0),
                "封板资金": float(row.get("fund") or 0),
                "首次封板时间": format_time(row.get("fbt")),
                "最后封板时间": format_time(row.get("lbt")),
                "炸板次数": int(row.get("zbc") or 0),
                "连板数": level,
                "连续涨停天数": level,
                "所属行业": str(row.get("hybk") or ""),
            })
        return results

    def _build_limit_up_stock(self, raw: dict[str, Any]) -> LimitUpStockOut:
        name = str(raw.get("名称") or raw.get("name") or "")
        industry = str(raw.get("所属行业") or raw.get("行业") or "")
        level = self._parse_limit_days(raw.get("连板数") or raw.get("涨停统计") or raw.get("连续涨停天数"))
        sealed = self._money_to_yi(raw.get("封板资金") or raw.get("封单资金") or 0)
        amount = self._money_to_yi(raw.get("成交额") or raw.get("amount") or 0)
        concepts = list(dict.fromkeys([x for x in [industry, self._classify_mainline({"name": industry or name})] if x]))[:4]
        expectation = self._stock_expectation(level, sealed, int(float(raw.get("炸板次数") or 0)))
        return LimitUpStockOut(
            code=str(raw.get("代码") or raw.get("code") or ""),
            name=name,
            price=round(float(raw.get("最新价") or raw.get("price") or 0), 2),
            change_pct=round(float(raw.get("涨跌幅") or raw.get("change_pct") or 10), 2),
            amount=round(amount, 2),
            turnover=round(float(raw.get("换手率") or raw.get("turnover") or 0), 2),
            sealed_amount=round(sealed, 2),
            first_limit_time=self._format_limit_time(raw.get("首次封板时间") or raw.get("first_limit_time") or ""),
            last_limit_time=self._format_limit_time(raw.get("最后封板时间") or raw.get("last_limit_time") or ""),
            break_count=int(float(raw.get("炸板次数") or raw.get("break_count") or 0)),
            consecutive_limit_days=level,
            industry=industry,
            concepts=concepts,
            expectation=expectation,
        )

    def _build_limit_up_clusters(self, stocks: list[LimitUpStockOut]) -> list[LimitUpClusterOut]:
        grouped: dict[str, list[LimitUpStockOut]] = defaultdict(list)
        for stock in stocks:
            for concept in stock.concepts:
                if concept and concept != "其他题材":
                    grouped[concept].append(stock)

        clusters: list[LimitUpClusterOut] = []
        for name, items in grouped.items():
            unique = list({item.code or item.name: item for item in items}.values())
            if len(unique) < 2:
                continue
            highest = max(item.consecutive_limit_days for item in unique)
            expectation = "高标带动明确，明日看前排竞价强度"
            if highest <= 1:
                expectation = "低位首板扩散，明日先看能否晋级二板"
            elif len(unique) >= 5:
                expectation = "题材涨停扩散充分，关注分歧后核心承接"
            clusters.append(LimitUpClusterOut(
                name=name,
                count=len(unique),
                highest_level=highest,
                stocks=[item.name for item in unique[:8]],
                expectation=expectation,
            ))
        return sorted(clusters, key=lambda item: (item.highest_level, item.count), reverse=True)[:16]

    def _limit_up_summary(
        self,
        stocks: list[LimitUpStockOut],
        clusters: list[LimitUpClusterOut],
    ) -> list[str]:
        if not stocks:
            return ["暂无涨停数据，先等待交易日数据同步"]
        highest = max(stock.consecutive_limit_days for stock in stocks)
        strong_clusters = [item for item in clusters if item.count >= 3]
        summary = [
            f"涨停家数 {len(stocks)} 只，最高 {highest} 板",
            f"题材聚类 {len(clusters)} 条，{len(strong_clusters)} 条达到 3 只以上共振",
        ]
        if clusters:
            top = clusters[0]
            summary.append(f"最强聚类：{top.name}，{top.count} 只涨停，最高 {top.highest_level} 板")
        if highest >= 4:
            summary.append("情绪高度打开，明日重点看高标是否继续正反馈")
        elif highest <= 2:
            summary.append("连板高度偏低，明日更需要观察首板到二板的晋级率")
        return summary

    def _parse_limit_days(self, value: Any) -> int:
        if value is None:
            return 1
        text = str(value)
        nums = [int(x) for x in re.findall(r"\d+", text)]
        if not nums:
            return 1
        return max(1, max(nums))

    def _money_to_yi(self, value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            raw = float(value)
            return raw / 1e8 if abs(raw) > 10000 else raw
        text = str(value).replace(",", "").strip()
        if not text or text == "-":
            return 0.0
        number_match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not number_match:
            return 0.0
        number = float(number_match.group(0))
        if "万" in text:
            return number / 10000
        return number

    def _format_limit_time(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text or text == "-":
            return ""
        if len(text) == 6 and text.isdigit():
            return f"{text[:2]}:{text[2:4]}:{text[4:]}"
        if len(text) == 4 and text.isdigit():
            return f"{text[:2]}:{text[2:]}"
        return text

    def _stock_expectation(self, level: int, sealed_amount: float, break_count: int) -> str:
        if level >= 4 and break_count == 0:
            return "高标核心，明日看竞价承接和封单延续"
        if level >= 2 and sealed_amount >= 1:
            return "梯队前排，具备继续带动同题材的条件"
        if break_count >= 2:
            return "封板反复，明日先看弱转强确认"
        return "首板观察，重点看是否带出同板块补涨"

    def _fallback_limit_up_pool_raw(self) -> list[dict[str, Any]]:
        themes = [
            ("机器人", "机器人 / 物理AI", 3),
            ("PCB", "PCB / 玻璃基板", 2),
            ("创新药", "创新药", 2),
            ("黄金", "贵金属 / 黄金", 1),
            ("算力", "AI应用 / 算力", 1),
        ]
        rows: list[dict[str, Any]] = []
        seq = 1
        for industry, _, count in themes:
            for i in range(count + 1):
                level = max(1, count - i + 1)
                rows.append({
                    "代码": f"00{seq:04d}",
                    "名称": f"{industry[:2]}涨停{seq}",
                    "最新价": round(7.5 + seq * 1.3, 2),
                    "涨跌幅": 10.0,
                    "成交额": (6 + seq * 0.7) * 1e8,
                    "换手率": round(5 + i * 1.8, 2),
                    "封板资金": (1.8 - i * 0.18) * 1e8,
                    "首次封板时间": f"09{35 + i:02d}00",
                    "最后封板时间": f"14{20 + i:02d}00",
                    "炸板次数": i % 2,
                    "连板数": level,
                    "所属行业": industry,
                })
                seq += 1
        return rows

    def _normalize_board_type(self, board_type: str) -> str:
        text = str(board_type or "").strip()
        mapping = {
            "行业资金流": "行业",
            "概念资金流": "概念",
            "地域资金流": "地域",
            "地区": "地域",
            "地域": "地域",
            "行业": "行业",
            "概念": "概念",
            "风格": "风格",
            "港股": "港股",
        }
        return mapping.get(text, "行业")

    def _board_type_to_flow_type(self, board_type: str) -> str:
        mapping = {
            "行业": "行业资金流",
            "概念": "概念资金流",
            "地域": "地域资金流",
            "风格": "概念资金流",
            "港股": "行业资金流",
        }
        return mapping.get(board_type, "行业资金流")

    def _normalize_dark_scope(self, scope: str) -> str:
        text = str(scope or "").strip()
        if text in {"行业", "行业板块", "板块"}:
            return "行业"
        if text in {"概念", "概念板块", "题材"}:
            return "概念"
        return "个股"

    def _fetch_eastmoney_hot_market_raw(self) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        for period, rank_type, field in [
            ("今日", "40001", "prcPcnt_dr"),
            ("5日", "40002", "prcPcnt_5r"),
            ("20日", "40003", "prcPcnt_20r"),
        ]:
            params = {
                "type": "spo_rank_hot",
                "plat": "2",
                "ver": "web20",
                "utToken": "",
                "ctToken": "",
                "rankType": rank_type,
                "recIdx": 1,
                "recCnt": 15,
            }
            resp = requests.get(
                "https://simqry2.eastmoney.com/qry_tzzh_v2",
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://group.eastmoney.com/HotMarket.html",
                    "Accept": "application/json,text/plain,*/*",
                },
                timeout=6,
            )
            resp.raise_for_status()
            data = self._decode_eastmoney_json_payload(resp.text)
            rows = data.get("data") or []
            for rank, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    continue
                all_rows.append({
                    "period": period,
                    "rank": rank,
                    "name": str(row.get("plateName") or ""),
                    "board_code": str(row.get("plateCode") or ""),
                    "change_pct": round(_safe_float(row.get(field)), 2),
                })
        if not all_rows:
            raise ValueError("empty eastmoney hot market")
        return all_rows

    def _decode_eastmoney_json_payload(self, text: str) -> dict[str, Any]:
        raw = text.strip()
        if raw.startswith("{"):
            return requests.models.complexjson.loads(raw)
        match = re.search(r"\((\{.*\})\)\s*;?$", raw, re.S)
        if not match:
            raise ValueError("invalid eastmoney jsonp")
        return requests.models.complexjson.loads(match.group(1))

    def _build_hot_theme_items(self, rows: list[dict[str, Any]]) -> list[HotThemeItem]:
        flow_lookup: dict[str, dict[str, Any]] = {}
        try:
            for row in self._fetch_direct_eastmoney_sector_flow_raw("概念资金流", "今日"):
                for key in [str(row.get("name") or ""), str(row.get("board_code") or "")]:
                    if key:
                        flow_lookup[key] = row
        except Exception:
            pass
        try:
            for row in self._fetch_direct_eastmoney_sector_flow_raw("行业资金流", "今日"):
                for key in [str(row.get("name") or ""), str(row.get("board_code") or "")]:
                    if key and key not in flow_lookup:
                        flow_lookup[key] = row
        except Exception:
            pass

        items: list[HotThemeItem] = []
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            board_code = str(row.get("board_code") or "").strip()
            flow = flow_lookup.get(board_code) or flow_lookup.get(name) or {}
            leaders = [str(x) for x in flow.get("leaders", []) if str(x).strip()]
            items.append(HotThemeItem(
                name=name,
                board_code=board_code or None,
                period=str(row.get("period") or "今日"),
                rank=_safe_int(row.get("rank"), 0),
                change_pct=round(_safe_float(row.get("change_pct")), 2),
                net_inflow=round(_safe_float(flow.get("net_inflow")), 2),
                main_inflow=round(_safe_float(flow.get("main_inflow")), 2),
                source="eastmoney-hotmarket",
                reason=f"{row.get('period') or '今日'}热点涨幅榜第{row.get('rank') or '-'}名",
                leaders=leaders[:4],
            ))
        return items

    def _fetch_eastmoney_dark_trade_raw(
        self,
        scope: str,
        date_text: str,
    ) -> tuple[list[dict[str, Any]], str]:
        if scope == "行业":
            market = "90"
            datetype = "2"
        elif scope == "概念":
            market = "90"
            datetype = "3"
        else:
            market = ""
            datetype = ""
        params = {
            "version": 100,
            "cver": 100,
            "date": date_text,
            "StartPage": 1,
            "NumPerPage": 80,
            "sortflag": 6,
            "desc": 1,
            "market": market,
            "datetype": datetype,
        }
        resp = requests.get(
            "https://quotederivates.eastmoney.com/datacenter/darktrade",
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://emrnweb.eastmoney.com/graymarket/home?appfenxiang=1",
                "Accept": "application/json,text/plain,*/*",
                "gtoken": "",
                "rnProjectId": "emrn.GrayMarketRank",
            },
            timeout=8,
        )
        resp.raise_for_status()
        payload = resp.json()
        if int(payload.get("errid") or 0) != 0:
            raise ValueError(str(payload.get("errmsg") or "darktrade error"))
        rows = payload.get("data") or []
        if not rows:
            raise ValueError("empty dark trade")
        return rows, str(payload.get("1") or date_text)

    def _build_dark_trade_item(self, row: dict[str, Any], scope: str) -> DarkTradeItem:
        market_raw = str(row.get("3") or "")
        market = ""
        if scope == "个股":
            market = "沪市" if market_raw == "1" else "深市"
        elif market_raw == "90":
            market = "板块"
        code = str(row.get("4") or "")
        name = str(row.get("16") or row.get("4") or "")
        latest_raw = _safe_float(row.get("13"))
        latest = latest_raw / 1000 if scope == "个股" and latest_raw > 1000 else latest_raw
        industry = str(row.get("17") or "")
        concept = str(row.get("18") or "")
        if scope != "个股":
            name = str(row.get("16") or name)
        return DarkTradeItem(
            code=code,
            name=name,
            market=market,
            board_type=scope,
            rank=_safe_int(row.get("21")),
            latest=round(latest, 3),
            change_pct=round(_safe_float(row.get("14")) * 100, 2),
            dark_amount=_money_yuan_to_yi(row.get("6")),
            lit_amount=_money_yuan_to_yi(row.get("7")),
            main_net_inflow_with_dark=_money_yuan_to_yi(row.get("8")),
            dark_activity=round(_safe_float(row.get("11")) * 100, 2),
            inflow_stock_ratio=round(_safe_float(row.get("12")) * 100, 2),
            inflow_count=_safe_int(row.get("10")),
            stock_count=_safe_int(row.get("9")),
            leading_stock=str(row.get("15") or ""),
            leading_stock_code=str(row.get("20") or ""),
            industry=industry,
            concept=concept,
        )

    def _fetch_direct_eastmoney_sector_flow_raw(
        self, flow_type: str, period: str
    ) -> list[dict[str, Any]]:
        sector_type_map = {"行业资金流": "m:90 s:4", "概念资金流": "m:90 t:3", "地域资金流": "m:90 t:1"}
        indicator_map = {
            "今日": ("f62", "1", "f62", "f66", "f204", "f3",
                     "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124,f1,f13"),
            "5日": ("f164", "5", "f164", "f166", "f257", "f109",
                     "f12,f14,f2,f109,f164,f165,f166,f167,f168,f169,f170,f171,f172,f173,f257,f258,f124,f1,f13"),
            "10日": ("f174", "10", "f174", "f176", "f260", "f160",
                     "f12,f14,f2,f160,f174,f175,f176,f177,f178,f179,f180,f181,f182,f183,f260,f261,f124,f1,f13"),
        }
        fid, stat, net_key, main_key, leader_key, change_key, fields = indicator_map.get(
            period, indicator_map["今日"]
        )
        params = {
            "pn": "1",
            "pz": "100",
            "po": "1",
            "np": "1",
            "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
            "fltt": "2",
            "invt": "2",
            "fid": fid,
            "fs": sector_type_map.get(flow_type, "m:90 s:4"),
            "stat": stat,
            "fields": fields,
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/bkzj/hy.html",
            "Accept": "application/json,text/plain,*/*",
        }
        rows = None
        last_exc: Exception | None = None
        for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"):
            try:
                resp = requests.get(
                    f"{host}/api/qt/clist/get",
                    params=params,
                    headers=headers,
                    timeout=6,
                )
                resp.raise_for_status()
                rows = resp.json()["data"]["diff"]
                if rows:
                    break
            except Exception as exc:
                last_exc = exc
        if not rows and last_exc:
            raise last_exc
        if not rows:
            raise ValueError("empty eastmoney sector flow")
        return [
            {
                "name": str(row.get("f14", "未知板块")),
                "board_code": str(row.get("f12") or ""),
                "provider": "eastmoney",
                "change_pct": float(row.get("f3") or 0),
                "net_inflow": round(float(row.get(net_key) or 0) / 1e8, 2),
                "main_inflow": round(float(row.get(main_key) or 0) / 1e8, 2),
                "flow_breakdown": [
                    {
                        "name": "超大单",
                        "net": round(float(row.get("f66") or 0) / 1e8, 2),
                        "ratio": round(float(row.get("f69") or 0), 2),
                    },
                    {
                        "name": "大单",
                        "net": round(float(row.get("f72") or 0) / 1e8, 2),
                        "ratio": round(float(row.get("f75") or 0), 2),
                    },
                    {
                        "name": "中单",
                        "net": round(float(row.get("f78") or 0) / 1e8, 2),
                        "ratio": round(float(row.get("f81") or 0), 2),
                    },
                    {
                        "name": "小单",
                        "net": round(float(row.get("f84") or 0) / 1e8, 2),
                        "ratio": round(float(row.get("f87") or 0), 2),
                    },
                ],
                "strength": max(0, min(100, int(
                    50 + float(row.get(change_key) or 0) * 8 + float(row.get(net_key) or 0) / 2e7
                ))),
                "leaders": [str(row.get(leader_key) or "待识别")],
                "change_pct_5": float(row.get("f109") or row.get("f160") or 0),
                "net_5d": round(float(row.get("f164") or row.get("f174") or 0) / 1e8, 2),
                "limit_up_count": _safe_int(row.get("f100")),
                "stock_count": _safe_int(row.get("f104")),
                "avg_change": _safe_float(row.get("f102")),
            }
            for row in rows
        ]

    def _fetch_eastmoney_board_intraday_flow(self, board_code: str) -> list[SectorFlowPoint]:
        if not board_code:
            raise ValueError("missing eastmoney board code")
        params = {
            "lmt": "0",
            "klt": "1",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "secid": f"90.{board_code}",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/bkzj/",
            "Accept": "application/json,text/plain,*/*",
        }
        rows = None
        last_exc: Exception | None = None
        for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"):
            try:
                resp = requests.get(
                    f"{host}/api/qt/stock/fflow/kline/get",
                    params=params,
                    headers=headers,
                    timeout=6,
                )
                resp.raise_for_status()
                rows = resp.json().get("data", {}).get("klines") or []
                if rows:
                    break
            except Exception as exc:
                last_exc = exc
        if not rows and last_exc:
            raise last_exc
        points: list[SectorFlowPoint] = []
        for row in rows or []:
            parts = str(row).split(",")
            if len(parts) < 2:
                continue
            time_label = parts[0][-5:]
            if not re.match(r"^\d{2}:\d{2}$", time_label):
                continue
            points.append(SectorFlowPoint(time=time_label, value=round(_safe_float(parts[1]) / 1e8, 2)))
        if not points:
            raise ValueError("empty eastmoney board intraday flow")
        return points

    def _fetch_akshare_sector_flow_raw(
        self, flow_type: str, period: str
    ) -> list[dict[str, Any]]:
        import akshare as ak
        sym = {"行业资金流": "行业资金流", "概念资金流": "概念资金流", "地域资金流": "地域资金流"}
        ind = {"今日": "今日", "5日": "5日", "10日": "10日"}
        frame = ak.stock_sector_fund_flow_rank(
            indicator=ind.get(period, "今日"),
            sector_type=sym.get(flow_type, "行业资金流"),
        )
        if frame.empty:
            raise ValueError("empty sector flow")
        results: list[dict[str, Any]] = []
        for _, row in frame.head(80).iterrows():
            name = str(row.get("名称", row.get("板块名称", "未知板块")))
            chg = float(row.get("涨跌幅", row.get("今日涨跌幅", 0)) or 0)
            net = float(row.get("今日主力净流入-净额", row.get("主力净流入-净额", 0)) or 0)
            main = float(row.get("今日超大单净流入-净额", row.get("超大单净流入-净额", net)) or net)
            leaders_raw = str(row.get("领涨股票", row.get("涨幅最大股票", "")))
            leaders = [x for x in leaders_raw.replace("，", ",").split(",") if x.strip()][:4]
            results.append({
                "name": name,
                "board_code": "",
                "change_pct": round(chg, 2),
                "net_inflow": round(net / 1e8, 2),
                "main_inflow": round(main / 1e8, 2),
                "strength": max(0, min(100, int(50 + chg * 8 + net / 1e7))),
                "leaders": leaders or ["待识别"],
            })
        return results

    def _fallback_sector_flow_raw(self) -> list[dict[str, Any]]:
        names = [
            "机器人", "半导体", "证券", "算力", "电力设备", "创新药", "低空经济",
            "消费电子", "有色金属", "煤炭", "房地产", "传媒", "白酒", "光伏",
            "国防军工", "汽车零部件", "中药", "化学制药", "银行", "保险",
        ]
        items: list[dict[str, Any]] = []
        for i, name in enumerate(names):
            net = round(18 - i * 1.8 + self.random.uniform(-2.5, 2.5), 2)
            chg = round(net / 8 + self.random.uniform(-1.5, 1.5), 2)
            items.append({
                "name": name,
                "board_code": "",
                "change_pct": chg,
                "net_inflow": net,
                "main_inflow": round(net * self.random.uniform(0.45, 0.8), 2),
                "strength": max(0, min(100, int(55 + net * 2))),
                "leaders": ["龙头候选", "容量核心", "前排强势"],
            })
        return items

    def information_differential(self, date: str | None = None) -> InformationDifferentialOut:
        if date:
            target_date = date
        elif _is_trading_day():
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            target_date = _last_trading_day()
        raw_items: list[dict[str, object]] = []
        notes: list[str] = []
        sources: list[str] = []

        try:
            raw_items.extend(self._fetch_eastmoney_fast_news())
            sources.append("eastmoney")
        except Exception as exc:
            notes.append(f"东方财富快讯暂不可用: {exc.__class__.__name__}")

        try:
            raw_items.extend(self._fetch_cctv_news(target_date))
            sources.append("cctv")
        except Exception as exc:
            notes.append(f"新闻联播暂不可用: {exc.__class__.__name__}")

        if not raw_items:
            raw_items = self._fallback_information_items(target_date)
            sources.append("diagnostic")
            notes.append("外部资讯源不可用，显示诊断样例")

        try:
            flow = self.sector_flow()
        except Exception:
            flow = None

        scored = [self._score_information_item(item, flow) for item in raw_items]
        scored = [item for item in scored if item.sectors or item.keywords]
        if not scored:
            scored = [
                self._score_information_item(item, flow)
                for item in self._fallback_information_items(target_date)
            ]
            notes.append("本批外部资讯暂未命中 A 股信息差关键词，显示诊断样例")

        scored.sort(key=lambda item: item.strength_score, reverse=True)
        watchlist: list[str] = []
        for item in scored:
            if item.fund_status == "资金已验证":
                watchlist.extend(item.sectors[:2])
        return InformationDifferentialOut(
            source="+".join(dict.fromkeys(sources)),
            date=target_date,
            updated_at=datetime.utcnow(),
            items=scored[:24],
            watchlist=list(dict.fromkeys(watchlist))[:8],
            data_notes=notes or ["东方财富快讯与新闻联播已同步"],
        )

    def _fetch_eastmoney_fast_news(self) -> list[dict[str, object]]:
        resp = requests.get(
            "https://np-listapi.eastmoney.com/comm/web/getFastNewsList",
            params={
                "client": "web",
                "biz": "web_724",
                "fastColumn": "102",
                "sortEnd": "",
                "pageSize": "40",
                "req_trace": str(uuid.uuid4()),
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://kuaixun.eastmoney.com/",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json().get("data", {}).get("fastNewsList", [])
        if not rows:
            raise ValueError("empty eastmoney fast news")
        items: list[dict[str, object]] = []
        for row in rows:
            title = str(row.get("title") or "").strip()
            summary = str(row.get("summary") or title).strip()
            if not title:
                continue
            code = str(row.get("code") or "")
            url = f"https://kuaixun.eastmoney.com/{code}.html" if code else None
            items.append({
                "title": title,
                "summary": summary,
                "source": "东方财富快讯",
                "published_at": str(row.get("showTime") or ""),
                "url": url,
                "related_stocks": [
                    str(s) for s in row.get("stockList", []) if str(s).strip()
                ],
            })
        return items

    def _fetch_cctv_news(self, target_date: str) -> list[dict[str, object]]:
        date_key = target_date.replace("-", "")
        resp = requests.get(
            f"https://tv.cctv.com/lm/xwlb/day/{date_key}.shtml",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        resp.raise_for_status()
        page = resp.content.decode("utf-8", "ignore")
        items: list[dict[str, object]] = []
        for href, raw_title in re.findall(
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, flags=re.S
        ):
            title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
            title = title.replace("完整版", "").replace("[视频]", "").strip()
            if not title or title.startswith("《新闻联播》"):
                continue
            url = href if href.startswith("http") else f"https://tv.cctv.com{href}" if href.startswith("/") else None
            items.append({
                "title": title,
                "summary": title,
                "source": "新闻联播",
                "published_at": target_date,
                "url": url,
                "related_stocks": [],
            })
        if not items:
            raise ValueError("empty cctv news")
        return items

    def _score_information_item(
        self, item: dict[str, object], flow: SectorFlowOut | None
    ) -> InformationItem:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        sector_map: dict[str, tuple[str, ...]] = {
            "量子": ("量子科技", "通信设备", "半导体"),
            "半导体": ("半导体", "芯片", "存储芯片"),
            "算力": ("算力", "数据中心", "液冷服务器"),
            "人工智能": ("人工智能", "算力", "软件开发"),
            "AI": ("人工智能", "算力", "传媒"),
            "新能源": ("新能源车", "电力设备", "储能"),
            "汽车": ("汽车整车", "汽车零部件", "智能驾驶"),
            "机器人": ("机器人", "减速器", "工业母机"),
            "低空": ("低空经济", "无人机", "航空装备"),
            "卫星": ("卫星导航", "商业航天", "军工电子"),
            "航天": ("商业航天", "航空装备", "军工"),
            "网络安全": ("网络安全", "信创", "金融科技"),
            "数据": ("数据要素", "信创", "云计算"),
            "医药": ("创新药", "医疗服务", "中药"),
            "金融": ("证券", "金融科技", "银行"),
            "电力": ("电力", "智能电网", "储能"),
            "煤电": ("电力", "煤炭", "电力设备"),
            "环保": ("环保", "固废处理", "循环经济"),
            "循环经济": ("环保", "循环经济", "再生资源"),
            "消费": ("食品饮料", "商业零售", "旅游酒店"),
            "电影": ("影视院线", "传媒", "游戏"),
            "两岸": ("福建自贸区", "航运港口", "旅游酒店"),
        }
        keywords: list[str] = []
        sectors: list[str] = []
        for kw, mapped in sector_map.items():
            if kw.lower() in text.lower():
                keywords.append(kw)
                sectors.extend(mapped)
        sectors = list(dict.fromkeys(sectors))

        flow_names = set()
        outflow_names = set()
        if flow:
            flow_names = {it.name for it in flow.inflow[:16]}
            outflow_names = {it.name for it in flow.outflow[:16]}

        has_verified = bool(set(sectors) & flow_names)
        has_outflow = bool(set(sectors) & outflow_names)
        if has_verified:
            fund_status = "资金已验证"
            action = "值得跟踪，仍需通过买入检查器确认个股地位"
        elif has_outflow:
            fund_status = "资金流出"
            action = "谨慎，防止利好兑现或板块退潮"
        else:
            fund_status = "等资金确认"
            action = "只记录信息差，不因消息单独开仓"

        source = str(item.get("source") or "资讯")
        base = 52 if source == "新闻联播" else 46
        score = base + len(keywords) * 8 + len(sectors[:3]) * 3
        if has_verified:
            score += 18
        if has_outflow:
            score -= 12

        return InformationItem(
            id=str(abs(hash((item.get("title"), item.get("published_at"))))),
            title=str(item.get("title") or "").strip(),
            summary=str(item.get("summary") or item.get("title") or "").strip(),
            source=source,
            published_at=str(item.get("published_at") or ""),
            keywords=keywords[:6],
            sectors=sectors[:6],
            related_stocks=[str(s) for s in item.get("related_stocks", [])][:6],
            strength_score=max(0, min(100, score)),
            credibility="高" if source == "新闻联播" else "中高",
            fund_status=fund_status,
            action=action,
            url=str(item.get("url")) if item.get("url") else None,
        )

    def _fallback_information_items(self, target_date: str) -> list[dict[str, object]]:
        return [
            {
                "title": "工信部推进量子信息标准化工作",
                "summary": "围绕量子计算、量子通信、量子精密测量等方向完善行业标准。",
                "source": "诊断样例",
                "published_at": target_date,
                "url": None,
                "related_stocks": [],
            },
            {
                "title": "国务院部署循环经济与能源结构优化",
                "summary": "到 2030 年资源循环利用产业规模提升，新能源电力消费比重提高。",
                "source": "诊断样例",
                "published_at": target_date,
                "url": None,
                "related_stocks": [],
            },
            {
                "title": "金融关键基础设施安全保护征求意见",
                "summary": "金融、数据安全、网络安全基础设施获得政策关注。",
                "source": "诊断样例",
                "published_at": target_date,
                "url": None,
                "related_stocks": [],
            },
        ]
