import html
import re
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

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
    LimitUpAtmosphereMetrics,
    LimitUpAtmosphereOut,
    LimitUpIdentityRoleOut,
    LimitUpLadderOut,
    LimitUpStockOut,
    LimitUpThemeLadderOut,
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

_DARK_TRADE_LAST_GOOD: dict[str, DarkTradeOut] = {}

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
from app.services.flow_kinetics import analyze_flow_kinetics


def _shanghai_now_naive() -> datetime:
    """Return the A-share market clock without depending on host timezone."""
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _last_trading_day() -> str:
    d = _shanghai_now_naive()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _limit_up_default_candidate_dates(
    now: datetime | None = None,
    lookback: int = 10,
) -> list[str]:
    """Return the dates that may back the default limit-up view.

    Before the continuous auction starts, today's pool is necessarily
    incomplete, so the search begins with the preceding weekday.  From 09:30
    onward today may be used, but only after the provider has returned a
    non-empty, correctly dated pool (validated by
    :func:`_is_valid_limit_up_ladder`).  Looking back across multiple weekdays
    also covers exchange holidays without pretending that a weekday was a
    trading day.
    """
    current = now or _shanghai_now_naive()
    cursor = current.date()
    minutes = current.hour * 60 + current.minute
    if cursor.weekday() >= 5 or minutes < 570:
        cursor -= timedelta(days=1)

    candidates: list[str] = []
    while len(candidates) < max(1, lookback):
        if cursor.weekday() < 5:
            candidates.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return candidates


def _is_valid_limit_up_ladder(value: LimitUpLadderOut | None) -> bool:
    """Only a real, non-empty dated pool can advance trading-day state."""
    if value is None or not str(value.trade_date or "").strip():
        return False
    source = str(value.source or "").strip().lower()
    if not source or "unavailable" in source or "不可用" in source:
        return False
    return any(
        str(stock.code or "").strip() and str(stock.name or "").strip()
        for group in value.groups
        for stock in group.stocks
    )

def _is_trading_day() -> bool:
    return _shanghai_now_naive().weekday() < 5

def _is_trading_time() -> bool:
    now = _shanghai_now_naive()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 555 <= t <= 690 or 780 <= t <= 900

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

def _snapshot_only_timeline(final_value: float) -> list[SectorFlowPoint]:
    # A single real snapshot must stay a single point.  Never fabricate an
    # intraday curve for a trading decision UI.
    return _current_timeline_point(final_value)

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
        pass

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
            notes.append("外部板块数据不可用；不生成模拟题材、模拟核心股或模拟资金")

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
            timeline=self._theme_timeline(raw) if include_timeline else _snapshot_only_timeline(float(raw.get("net_inflow") or 0)),
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
            "fields": "f12,f14,f2,f3,f5,f6,f8,f20,f21,f62",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/center/boardlist.html",
            "Accept": "application/json,text/plain,*/*",
        }
        rows = []
        last_exc: Exception | None = None
        for host in ("https://push2delay.eastmoney.com", "https://push2.eastmoney.com"):
            try:
                resp = requests.get(f"{host}/api/qt/clist/get", params=params, headers=headers, timeout=8)
                resp.raise_for_status()
                rows = (resp.json().get("data") or {}).get("diff") or []
                if rows:
                    break
            except Exception as exc:
                last_exc = exc
        if not rows and last_exc:
            raise last_exc
        if not rows:
            raise ValueError("empty sector constituents")
        return [
            {
                "code": str(row.get("f12") or ""),
                "name": str(row.get("f14") or ""),
                "price": _safe_float(row.get("f2")),
                "change_pct": _safe_float(row.get("f3")),
                "amount": round(_safe_float(row.get("f6")) / 1e8, 2),
                "float_cap": round(_safe_float(row.get("f21")) / 1e8, 2),
                "main_inflow": round(_safe_float(row.get("f62")) / 1e8, 2),
                "turnover": round(_safe_float(row.get("f8")), 2),
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

        return _snapshot_only_timeline(float(raw.get("net_inflow") or 0))

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

        now = _shanghai_now_naive()
        observed_at = now
        raw_items: list[dict[str, Any]] = []
        source = "eastmoney"
        cache_key = f"{flow_type}|{period}"
        data_date: str | None = None

        try:
            raw_items = self._fetch_direct_eastmoney_sector_flow_raw(flow_type=flow_type, period=period)
            _cache_good_flow(cache_key, raw_items, "eastmoney")
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
                    raw_items = []
                    source = "unavailable"

        snapshot_recorded = _is_trading_time()
        if snapshot_recorded:
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
        index_curves: dict[str, list[dict[str, Any]]] = {}
        eastmoney_chart_codes = [
            str(raw.get("board_code") or "")
            for raw in raw_ordered
            if raw.get("provider") == "eastmoney"
            and period == "今日"
            and str(raw.get("board_code") or "") in chart_codes
        ]
        if eastmoney_chart_codes:
            with ThreadPoolExecutor(max_workers=min(8, len(eastmoney_chart_codes))) as executor:
                futures = {
                    executor.submit(self._fetch_eastmoney_board_intraday_index, code): code
                    for code in eastmoney_chart_codes
                }
                for future in as_completed(futures):
                    try:
                        index_curves[futures[future]] = future.result()
                    except Exception:
                        continue
        has_eastmoney_intraday_curve = False
        previous_rank: dict[str, int] = {}
        rank_snapshot = snaps[-2] if snapshot_recorded and len(snaps) >= 2 else (snaps[-1] if snaps else None)
        if rank_snapshot:
            previous_items = sorted(
                rank_snapshot.get("items", []),
                key=lambda item: float(item.get("net_inflow") or 0),
                reverse=True,
            )
            previous_rank = {
                str(item.get("name") or ""): rank
                for rank, item in enumerate(previous_items, start=1)
            }
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
            historical_points = [point for point in timeline if point.time != "当前"]
            timeline_reliable = len(historical_points) >= 2
            flow_peak = None
            flow_peak_time = None
            flow_pullback = None
            flow_pullback_pct = None
            flow_event = None
            if timeline_reliable:
                peak_point = max(timeline, key=lambda point: point.value)
                flow_peak = round(peak_point.value, 2)
                flow_peak_time = peak_point.time
                flow_pullback = round(net - peak_point.value, 2)
                if peak_point.value > 0:
                    flow_pullback_pct = round((net - peak_point.value) / peak_point.value * 100, 2)
                values = [point.value for point in timeline]
                if net < 0 <= values[-2]:
                    flow_event = "FLOW_TURN_NEGATIVE"
                elif peak_point.time != timeline[-1].time and peak_point.value > 0 and net <= peak_point.value * 0.7:
                    flow_event = "FLOW_PEAK_REVERSAL"
                elif peak_point.time == timeline[-1].time and net > 0:
                    flow_event = "FLOW_NEW_HIGH"

            kinetics_available = bool(
                period == "今日"
                and _is_trading_day()
                and source != "unavailable"
                and "cached" not in source
            )
            kinetics = analyze_flow_kinetics(
                timeline if kinetics_available else [],
                current_value=net if kinetics_available else None,
                change_pct=round(float(raw.get("change_pct") or 0), 2),
                as_of=observed_at,
            )

            old_rank = previous_rank.get(name)
            index_timeline = index_curves.get(board_code, [])
            latest_index = index_timeline[-1] if index_timeline else None
            sector_price = float(latest_index["price"]) if latest_index else None
            sector_vwap = float(latest_index["vwap"]) if latest_index else None
            sector_vwap_reliable = len(index_timeline) >= 3 and bool(sector_vwap and sector_vwap > 0)

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
                    rank=idx,
                    rank_change=(old_rank - idx) if old_rank is not None else None,
                    leaders=[str(l) for l in raw.get("leaders", []) if str(l).strip()][:4],
                    timeline=timeline,
                    timeline_reliable=timeline_reliable,
                    flow_peak=flow_peak,
                    flow_peak_time=flow_peak_time,
                    flow_pullback=flow_pullback,
                    flow_pullback_pct=flow_pullback_pct,
                    flow_event=flow_event,
                    flow_direction=kinetics.direction,
                    flow_speed=kinetics.speed,
                    flow_acceleration=kinetics.acceleration,
                    flow_turning=kinetics.turning,
                    flow_signal=kinetics.signal,
                    flow_signal_level=kinetics.severity,
                    flow_as_of=kinetics.as_of,
                    flow_window_minutes=kinetics.window_minutes,
                    flow_kinetics_reliable=kinetics.reliable,
                    index_timeline=index_timeline,
                    sector_price=sector_price,
                    sector_vwap=sector_vwap,
                    sector_vwap_reliable=sector_vwap_reliable,
                    sector_below_vwap=(sector_price < sector_vwap) if sector_vwap_reliable and sector_price is not None and sector_vwap is not None else None,
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
            notes.append("热点题材外部源不可用；不展示模拟热点")

        result = HotThemesOut(
            source="东方财富市场热点" if items else "数据源不可用",
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
        rows: list[dict[str, Any]] = []
        remote_date = date_text
        items: list[DarkTradeItem] = []
        last_error: Exception | None = None
        attempted_dates: list[str] = []
        candidate = datetime.strptime(date_text, "%Y%m%d")
        # 盘前东方财富会先生成当日行业/概念空壳行，个股接口则直接报错。
        # 最多回看六个交易日，只接受确实带有成交拆单金额的完整榜单。
        while len(attempted_dates) < 6:
            if candidate.weekday() < 5:
                candidate_text = candidate.strftime("%Y%m%d")
                attempted_dates.append(candidate_text)
                try:
                    candidate_rows, candidate_remote_date = self._fetch_eastmoney_dark_trade_raw(
                        normalized, candidate_text
                    )
                    if not self._has_real_dark_trade_values(candidate_rows):
                        raise ValueError("darktrade placeholder rows")
                    rows = candidate_rows
                    remote_date = candidate_remote_date or candidate_text
                    break
                except Exception as exc:
                    last_error = exc
            candidate -= timedelta(days=1)

        if rows:
            items = [self._build_dark_trade_item(row, normalized) for row in rows]
            if str(remote_date) != date_text:
                notes.append(
                    f"当日完整榜单尚未生成，已自动回退至最近有效交易日 {remote_date}。"
                )
            notes.append(f"接口本次返回 {len(items)} 条；仅代表东方财富该算法榜单覆盖范围，不代表全市场股票总数")
        else:
            cached_good = _DARK_TRADE_LAST_GOOD.get(normalized)
            if cached_good is not None and cached_good.items:
                result = cached_good.model_copy(deep=True)
                result.updated_at = datetime.utcnow()
                result.source = f"{cached_good.source}+last-good-cache"
                result.notes = [
                    *cached_good.notes,
                    "实时接口暂不可用，当前展示本进程最后一次成功获取的榜单。",
                ]
                _set_response_cache(cache_key, result)
                return result
            notes.append(
                f"东方财富成交拆单估算接口暂不可用: {(last_error or ValueError()).__class__.__name__}"
            )

        result = DarkTradeOut(
            source="eastmoney-darktrade" if items else "eastmoney-darktrade-unavailable",
            trade_date=str(remote_date or date_text),
            updated_at=datetime.utcnow(),
            scope=normalized,
            items=items,
            notes=notes,
        )
        if items:
            _DARK_TRADE_LAST_GOOD[normalized] = result.model_copy(deep=True)
        _set_response_cache(cache_key, result)
        return result

    def sector_opening_breadth(
        self,
        trade_date: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Return real, same-day industry-board opening breadth.

        Eastmoney's current board list exposes open and previous-close fields
        as ``f17`` and ``f18``.  Historical requests, missing provider dates,
        and thin samples stay ``missing``; they are never backfilled or
        simulated.
        """

        shanghai_tz = ZoneInfo("Asia/Shanghai")
        now = datetime.now(shanghai_tz)
        target_trade_date = trade_date or now.date().isoformat()
        cache_key = f"sector-opening-breadth|{target_trade_date}"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if cached is not None:
                return cached

        result: dict[str, Any] = {
            "trade_date": target_trade_date,
            "updated_at": now.isoformat(),
            "source": "东方财富行业板块开盘价(f17/f18)",
            "data_quality": "missing",
            "threshold_pct": 0.5,
            "sample_count": 0,
            "sector_high_open_count": None,
            "sector_component_count": None,
            "sector_open_breadth_ratio": None,
            "notes": [],
        }
        if target_trade_date != now.date().isoformat():
            result["notes"] = ["东方财富板块列表仅提供当前交易日开盘字段，历史日期不回填、不模拟。"]
            _set_response_cache(cache_key, result)
            return result

        try:
            rows = self._fetch_direct_eastmoney_sector_flow_raw(
                flow_type="行业资金流",
                period="今日",
            )
        except Exception as exc:
            result["notes"] = [f"东方财富行业板块开盘数据暂不可用：{type(exc).__name__}"]
            _set_response_cache(cache_key, result)
            return result

        samples: list[float] = []
        timestamps: list[str] = []
        for row in rows:
            if str(row.get("provider_trade_date") or "") != target_trade_date:
                continue
            open_price = _safe_float(row.get("open_price"))
            prev_close = _safe_float(row.get("prev_close"))
            if open_price <= 0 or prev_close <= 0:
                continue
            samples.append((open_price - prev_close) / prev_close * 100)
            observed_at = str(row.get("provider_updated_at") or "").strip()
            if observed_at:
                timestamps.append(observed_at)

        sample_count = len(samples)
        result["sample_count"] = sample_count
        if sample_count < 10:
            result["notes"] = [
                f"同交易日且含真实开盘/昨收的行业板块仅{sample_count}个，低于10个的最低覆盖要求。"
            ]
            _set_response_cache(cache_key, result)
            return result

        high_open_count = sum(value >= float(result["threshold_pct"]) for value in samples)
        result.update({
            "updated_at": max(timestamps) if timestamps else now.isoformat(),
            "data_quality": "ok",
            "sector_high_open_count": high_open_count,
            "sector_component_count": sample_count,
            "sector_open_breadth_ratio": round(high_open_count / sample_count, 4),
            "notes": ["仅统计具有同交易日提供方时间戳、真实开盘价和昨收价的行业板块。"],
        })
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
                    raw_items = []
                    source = "unavailable"

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
            notes.append("外部成分股接口不可用；不展示模拟成分股")

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
        """Return a dated real limit-up pool.

        An explicit date is never silently changed.  The default view searches
        from the most recent eligible date backwards and only advances to the
        current session when a non-empty, correctly sourced pool is available.
        This keeps pre-market, holiday and transient-empty responses anchored
        to the last valid trading session.
        """
        if trade_date:
            return self._limit_up_ladder_for_date(trade_date, force_refresh=force_refresh)

        first_unavailable: LimitUpLadderOut | None = None
        for candidate in _limit_up_default_candidate_dates():
            result = self._limit_up_ladder_for_date(candidate, force_refresh=force_refresh)
            if first_unavailable is None:
                first_unavailable = result
            if _is_valid_limit_up_ladder(result):
                return result
        # Preserve an explicit data-gap payload when no valid pool exists.  It
        # is deliberately not cached, so a newly published current pool can be
        # adopted on the next refresh.
        assert first_unavailable is not None
        return first_unavailable

    def _limit_up_ladder_for_date(
        self,
        target_date: str,
        *,
        force_refresh: bool = False,
    ) -> LimitUpLadderOut:
        cache_key = f"limit-up-ladder|{target_date}"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if _is_valid_limit_up_ladder(cached):
                return cached

        notes: list[str] = []
        source = "东方财富涨停池"
        raw_items: list[dict[str, Any]] = []
        if not _is_trading_day():
            notes.append(f"非交易日，展示最近交易日 {target_date} 的涨停池")
        try:
            raw_items = self._fetch_limit_up_pool_raw(target_date)
        except Exception as exc:
            notes.append(f"涨停池暂不可用: {exc.__class__.__name__}")
            raw_items = []
            source = "unavailable"
            notes.append("不生成模拟涨停股票")

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
        # Never cache an unavailable/empty pool.  During the session the real
        # current-day pool can appear minutes later and must then be eligible
        # to replace the prior completed session immediately.
        if _is_valid_limit_up_ladder(result):
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

    def _fetch_limit_up_pool_raw(self, trade_date: str) -> list[dict[str, Any]]:
        date_text = trade_date.replace("-", "")
        rows = self._fetch_direct_limit_up_pool_raw(date_text)
        if not rows:
            raise ValueError("empty limit-up pool")
        return rows

    def broken_limit_pool(self, trade_date: str) -> list[LimitUpStockOut]:
        """Return real Eastmoney failed-limit rows for post-breakout support analysis."""
        return [self._build_limit_up_stock(item) for item in self._fetch_broken_limit_pool_raw(trade_date.replace("-", ""))]

    def _fetch_broken_limit_pool_raw(self, date_text: str) -> list[dict[str, Any]]:
        resp = requests.get(
            "https://push2ex.eastmoney.com/getTopicZBPool",
            params={
                "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
                "Pageindex": 0, "pagesize": 10000, "sort": "fbt:asc", "date": date_text,
                "_": int(datetime.now().timestamp() * 1000),
            },
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/ztb/detail"},
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json().get("data")
        if not isinstance(data, dict):
            raise ValueError("broken-limit pool returned no dated payload")
        query_date = str(data.get("qdate") or "")
        if query_date and query_date != date_text:
            raise ValueError(f"broken-limit pool returned mismatched date {query_date}")
        pool = data.get("pool") or []
        results: list[dict[str, Any]] = []
        for row in pool:
            if not isinstance(row, dict):
                continue
            stats = row.get("zttj") if isinstance(row.get("zttj"), dict) else {}
            results.append({
                "代码": str(row.get("c") or ""), "名称": str(row.get("n") or ""),
                "最新价": round(float(row.get("p") or 0) / 1000, 3),
                "涨跌幅": float(row.get("zdp") or 0), "成交额": float(row.get("amount") or 0),
                "换手率": float(row.get("hs") or 0), "封板资金": 0,
                "首次封板时间": str(row.get("fbt") or ""), "最后封板时间": "",
                "炸板次数": max(1, int(row.get("zbc") or 1)),
                "连板数": max(1, int(stats.get("ct") or 1)), "所属行业": str(row.get("hybk") or ""),
            })
        return results

    def _fetch_dated_pool_total(self, endpoint: str, date_text: str) -> int:
        resp = requests.get(
            f"https://push2ex.eastmoney.com/{endpoint}",
            params={
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": 0,
                "pagesize": 100,
                "sort": "fbt:asc",
                "date": date_text,
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/ztb/detail",
            },
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json().get("data")
        if not isinstance(data, dict):
            raise ValueError(f"{endpoint} returned no dated payload")
        query_date = str(data.get("qdate") or "")
        if query_date and query_date != date_text:
            raise ValueError(f"{endpoint} returned mismatched date {query_date}")
        total = data.get("tc")
        if total is None:
            raise ValueError(f"{endpoint} returned no total")
        return max(0, int(float(total)))

    def _find_previous_limit_up_pool(self, target_date: str) -> tuple[str, list[dict[str, Any]]]:
        candidate = datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)
        checked = 0
        last_error: Exception | None = None
        while checked < 10:
            if candidate.weekday() < 5:
                checked += 1
                candidate_text = candidate.strftime("%Y-%m-%d")
                try:
                    rows = self._fetch_limit_up_pool_raw(candidate_text)
                    if rows:
                        return candidate_text, rows
                except Exception as exc:
                    last_error = exc
            candidate -= timedelta(days=1)
        if last_error is not None:
            raise last_error
        raise ValueError("previous limit-up pool unavailable")

    def _fetch_current_stock_quotes(
        self,
        codes: list[str],
    ) -> tuple[dict[str, dict[str, Any]], str]:
        unique_codes = list(dict.fromkeys(code for code in codes if code))
        if not unique_codes:
            return {}, "eastmoney-stock-quotes"
        rows: dict[str, dict[str, Any]] = {}
        hosts: list[str] = []
        for offset in range(0, len(unique_codes), 80):
            batch = unique_codes[offset:offset + 80]
            secids = ",".join(
                f"{'1' if code.startswith(('5', '6', '9')) else '0'}.{code}"
                for code in batch
            )
            payload, host = _get_json_from_hosts(
                "/api/qt/ulist.np/get",
                {
                    "fltt": "2",
                    "invt": "2",
                    "fields": "f12,f14,f2,f3,f17,f18,f124",
                    "secids": secids,
                },
                timeout=8,
            )
            hosts.append(host.split("//")[-1])
            for raw in list((payload.get("data") or {}).get("diff") or []):
                code = str(raw.get("f12") or "")
                stamp = _safe_int(raw.get("f124"))
                quote_date = (
                    (datetime.utcfromtimestamp(stamp) + timedelta(hours=8)).strftime("%Y%m%d")
                    if stamp > 0
                    else ""
                )
                rows[code] = {
                    "trade_date": quote_date,
                    "open": _safe_float(raw.get("f17")),
                    "prev_close": _safe_float(raw.get("f18")),
                    "change_pct": (
                        _safe_float(raw.get("f3"))
                        if raw.get("f3") not in (None, "", "-", "--")
                        else None
                    ),
                }
        return rows, f"eastmoney-stock-quotes@{','.join(dict.fromkeys(hosts))}"

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
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("limit-up pool returned no dated payload")
        query_date = str(data.get("qdate") or "")
        if query_date and query_date != date_text:
            raise ValueError(f"limit-up pool returned mismatched date {query_date}")
        pool = data.get("pool") or []
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
            # 东方财富该接口单页上限为 80，超过会返回“单页数过大”。
            "NumPerPage": 80,
            "sortflag": 6,
            "desc": 1,
            "market": market,
            "datetype": datetype,
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://emrnweb.eastmoney.com/graymarket/home?appfenxiang=1",
            "Accept": "application/json,text/plain,*/*",
            "gtoken": "",
            "rnProjectId": "emrn.GrayMarketRank",
        }
        rows: list[dict[str, Any]] = []
        remote_date = date_text
        seen: set[tuple[str, str]] = set()
        for page in range(1, 51):
            params["StartPage"] = page
            resp = requests.get(
                "https://quotederivates.eastmoney.com/datacenter/darktrade",
                params=params,
                headers=headers,
                timeout=8,
            )
            resp.raise_for_status()
            payload = resp.json()
            if int(payload.get("errid") or 0) != 0:
                raise ValueError(str(payload.get("errmsg") or "darktrade error"))
            remote_date = str(payload.get("1") or remote_date)
            page_rows = payload.get("data") or []
            if not page_rows:
                break
            added = 0
            for row in page_rows:
                key = (str(row.get("3") or ""), str(row.get("4") or ""))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                added += 1
            if len(page_rows) < int(params["NumPerPage"]) or added == 0:
                break
        if not rows:
            raise ValueError("empty dark trade")
        return rows, remote_date

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
                     "f12,f14,f2,f3,f15,f17,f18,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f100,f102,f104,f204,f205,f124,f1,f13"),
            "5日": ("f164", "5", "f164", "f166", "f257", "f109",
                     "f12,f14,f2,f100,f102,f104,f109,f164,f165,f166,f167,f168,f169,f170,f171,f172,f173,f257,f258,f124,f1,f13"),
            "10日": ("f174", "10", "f174", "f176", "f260", "f160",
                     "f12,f14,f2,f100,f102,f104,f160,f174,f175,f176,f177,f178,f179,f180,f181,f182,f183,f260,f261,f124,f1,f13"),
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
        rows: list[dict[str, Any]] | None = None
        last_exc: Exception | None = None
        for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"):
            try:
                fetched: list[dict[str, Any]] = []
                total = 0
                for page in range(1, 11):
                    page_params = {**params, "pn": str(page)}
                    resp = requests.get(
                        f"{host}/api/qt/clist/get",
                        params=page_params,
                        headers=headers,
                        timeout=6,
                    )
                    resp.raise_for_status()
                    data = resp.json().get("data") or {}
                    page_rows = data.get("diff") or []
                    if not page_rows:
                        break
                    fetched.extend(page_rows)
                    total = _safe_int(data.get("total"), len(fetched))
                    if total and len(fetched) >= total:
                        break
                if fetched:
                    rows = fetched[:total] if total else fetched
                    break
            except Exception as exc:
                last_exc = exc
        if not rows and last_exc:
            raise last_exc
        if not rows:
            raise ValueError("empty eastmoney sector flow")
        result: list[dict[str, Any]] = []
        for row in rows:
            provider_timestamp = _safe_int(row.get("f124"))
            provider_updated_at = None
            provider_trade_date = None
            if provider_timestamp > 0:
                try:
                    provider_dt = datetime.fromtimestamp(
                        provider_timestamp,
                        ZoneInfo("Asia/Shanghai"),
                    )
                    provider_updated_at = provider_dt.isoformat()
                    provider_trade_date = provider_dt.date().isoformat()
                except (OverflowError, OSError, ValueError):
                    provider_timestamp = 0
            result.append({
                "name": str(row.get("f14", "未知板块")),
                "board_code": str(row.get("f12") or ""),
                "provider": "eastmoney",
                "provider_timestamp": provider_timestamp or None,
                "provider_updated_at": provider_updated_at,
                "provider_trade_date": provider_trade_date,
                "latest": _safe_float(row.get("f2")),
                "high_price": _safe_float(row.get("f15")),
                "open_price": _safe_float(row.get("f17")),
                "prev_close": _safe_float(row.get("f18")),
                "change_pct": _safe_float(row.get("f3")),
                "net_inflow": round(_safe_float(row.get(net_key)) / 1e8, 2),
                "main_inflow": round(_safe_float(row.get(main_key)) / 1e8, 2),
                "flow_breakdown": [
                    {
                        "name": "超大单",
                        "net": round(_safe_float(row.get("f66")) / 1e8, 2),
                        "ratio": round(_safe_float(row.get("f69")), 2),
                    },
                    {
                        "name": "大单",
                        "net": round(_safe_float(row.get("f72")) / 1e8, 2),
                        "ratio": round(_safe_float(row.get("f75")), 2),
                    },
                    {
                        "name": "中单",
                        "net": round(_safe_float(row.get("f78")) / 1e8, 2),
                        "ratio": round(_safe_float(row.get("f81")), 2),
                    },
                    {
                        "name": "小单",
                        "net": round(_safe_float(row.get("f84")) / 1e8, 2),
                        "ratio": round(_safe_float(row.get("f87")), 2),
                    },
                ],
                "strength": max(0, min(100, int(
                    50 + _safe_float(row.get(change_key)) * 8 + _safe_float(row.get(net_key)) / 2e7
                ))),
                "leaders": [str(row.get(leader_key) or "待识别")],
                "change_pct_5": _safe_float(row.get("f109") or row.get("f160")),
                "net_5d": round(_safe_float(row.get("f164") or row.get("f174")) / 1e8, 2),
                "limit_up_count": _safe_int(row.get("f100")),
                "stock_count": _safe_int(row.get("f104")),
                "avg_change": _safe_float(row.get("f102")),
            })
        return result

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

    def _fetch_eastmoney_board_intraday_index(self, board_code: str) -> list[dict[str, Any]]:
        if not board_code:
            raise ValueError("missing eastmoney board code")
        resp = requests.get(
            "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
            params={
                "secid": f"90.{board_code}", "ndays": "1", "iscr": "0", "iscca": "0",
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            },
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
            timeout=6,
        )
        resp.raise_for_status()
        rows = resp.json().get("data", {}).get("trends") or []
        points: list[dict[str, Any]] = []
        for row in rows:
            parts = str(row).split(",")
            if len(parts) < 8:
                continue
            time_label = parts[0][-5:]
            price = _safe_float(parts[2])
            average_price = _safe_float(parts[7])
            if re.match(r"^\d{2}:\d{2}$", time_label) and price > 0 and average_price > 0:
                points.append({"time": time_label, "price": round(price, 4), "vwap": round(average_price, 4)})
        if not points:
            raise ValueError("empty eastmoney board intraday index")
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

    def information_differential(self, date: str | None = None, force_refresh: bool = False, related_stocks: dict[str, str] | None = None) -> InformationDifferentialOut:
        if date:
            target_date = date
        elif _is_trading_day():
            target_date = (_shanghai_now_naive() - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            target_date = _last_trading_day()
        related_stocks = related_stocks or {}
        cache_key = f"information-differential|{target_date}|{','.join(sorted(related_stocks))}"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if cached is not None:
                return cached
        raw_items: list[dict[str, object]] = []
        notes: list[str] = []
        sources: list[str] = []

        with ThreadPoolExecutor(max_workers=3) as executor:
            tasks = {
                executor.submit(self._fetch_eastmoney_fast_news): "东方财富快讯",
                executor.submit(self._fetch_cctv_news, target_date): "央视新闻",
            }
            if related_stocks:
                tasks[executor.submit(self._fetch_eastmoney_announcements, list(related_stocks))] = "东方财富持仓公告"
            for future, source_name in tasks.items():
                try:
                    raw_items.extend(future.result())
                    sources.append(source_name)
                except Exception as exc:
                    notes.append(f"{source_name}暂不可用: {exc.__class__.__name__}")

        if not raw_items:
            notes.append("外部资讯源不可用；不展示诊断样例或虚构要闻")

        # News must return quickly.  Use a previously verified market cache for
        # confirmation instead of launching another full external request.
        flow = _get_response_cache("sector-flow|行业资金流|今日")

        scored = [self._score_information_item(item, flow, related_stocks) for item in raw_items]
        scored = [item for item in scored if item.sectors or item.keywords]
        if raw_items and not scored:
            notes.append("本批真实资讯暂未命中 A 股行业关键词")

        scored.sort(key=lambda item: item.strength_score, reverse=True)
        watchlist: list[str] = []
        for item in scored:
            if item.fund_status == "资金已验证":
                watchlist.extend(item.sectors[:2])
        result = InformationDifferentialOut(
            source="+".join(dict.fromkeys(sources)),
            date=target_date,
            updated_at=datetime.utcnow(),
            items=scored[:24],
            watchlist=list(dict.fromkeys(watchlist))[:8],
            data_notes=notes or ["东方财富快讯与新闻联播已同步"],
        )
        _set_response_cache(cache_key, result)
        return result

    def limit_up_atmosphere(
        self,
        trade_date: str | None = None,
        force_refresh: bool = False,
    ) -> LimitUpAtmosphereOut:
        """Measure board-trading breadth and next-session payoff with real dated pools.

        A missing historical pool or quote sample is never filled with a synthetic
        value.  In that case the decision is capped at ``CAUTION`` (or
        ``DATA_GAP`` when even the current limit-up pool is unavailable).
        """
        ladder = self.limit_up_ladder(
            trade_date=trade_date,
            force_refresh=force_refresh,
        )
        # The ladder resolves the default date through the real-pool validity
        # gate.  Reuse that exact date for every atmosphere component so a
        # holiday/current empty shell cannot mix with the prior session.
        target_date = ladder.trade_date
        cache_key = f"limit-up-atmosphere|{target_date}"
        if not force_refresh:
            cached = _get_response_cache(cache_key)
            if cached is not None:
                return cached
        current_stocks = [stock for group in ladder.groups for stock in group.stocks]
        metrics = LimitUpAtmosphereMetrics(
            limit_up_count=len(current_stocks),
            highest_board=max((stock.consecutive_limit_days for stock in current_stocks), default=0),
        )
        sources = [ladder.source]
        missing: list[str] = []
        notes = list(ladder.notes)
        broken_stocks: list[LimitUpStockOut] | None = None

        if not _is_valid_limit_up_ladder(ladder):
            result = LimitUpAtmosphereOut(
                source=ladder.source,
                trade_date=target_date,
                updated_at=datetime.utcnow(),
                decision="DATA_GAP",
                decision_label="数据不足，禁止打板",
                score=0,
                data_quality="缺失",
                metrics=metrics,
                evidence=[],
                risks=["当日真实涨停池不可用，无法判断封板率、晋级率和次日溢价。"],
                missing_data=["当日真实涨停池"],
                notes=notes + ["不使用模拟涨停家数，不生成打板许可。"],
            )
            _set_response_cache(cache_key, result)
            return result

        try:
            broken_rows = self._fetch_broken_limit_pool_raw(target_date.replace("-", ""))
            broken_stocks = [self._build_limit_up_stock(row) for row in broken_rows]
            metrics.broken_count = len(broken_rows)
            attempted = metrics.limit_up_count + metrics.broken_count
            if attempted > 0:
                metrics.seal_rate = round(metrics.limit_up_count / attempted * 100, 1)
                metrics.break_rate = round(metrics.broken_count / attempted * 100, 1)
            sources.append("eastmoney-broken-limit-pool")
        except Exception as exc:
            missing.append("炸板池")
            notes.append(f"东方财富炸板池不可用：{exc.__class__.__name__}。")

        try:
            metrics.limit_down_count = self._fetch_dated_pool_total(
                "getTopicDTPool", target_date.replace("-", "")
            )
            sources.append("eastmoney-limit-down-pool")
        except Exception as exc:
            missing.append("跌停池")
            notes.append(f"东方财富跌停池不可用：{exc.__class__.__name__}。")

        top_cluster = max(ladder.clusters, key=lambda row: row.count, default=None)
        if top_cluster is not None:
            metrics.top_theme = top_cluster.name
            metrics.top_theme_count = top_cluster.count
            metrics.theme_concentration_pct = round(
                top_cluster.count / max(1, metrics.limit_up_count) * 100, 1
            )
        else:
            missing.append("题材聚类")

        previous_date: str | None = None
        previous_stocks: list[LimitUpStockOut] = []
        try:
            previous_date, previous_rows = self._find_previous_limit_up_pool(target_date)
            previous_stocks = [self._build_limit_up_stock(row) for row in previous_rows]
            metrics.previous_limit_up_count = len(previous_stocks)
            current_by_code = {stock.code: stock for stock in current_stocks if stock.code}
            promoted = sum(
                1
                for stock in previous_stocks
                if stock.code in current_by_code
                and current_by_code[stock.code].consecutive_limit_days > stock.consecutive_limit_days
            )
            metrics.promoted_count = promoted
            metrics.promotion_rate = round(
                promoted / max(1, len(previous_stocks)) * 100, 1
            )
            sources.append("eastmoney-previous-limit-up-pool")
        except Exception as exc:
            missing.append("前一交易日涨停池/晋级率")
            notes.append(f"前一交易日涨停池不可用：{exc.__class__.__name__}。")

        if previous_stocks:
            try:
                quotes, quote_source = self._fetch_current_stock_quotes(
                    [stock.code for stock in previous_stocks if stock.code]
                )
                target_compact = target_date.replace("-", "")
                open_premiums: list[float] = []
                current_premiums: list[float] = []
                for stock in previous_stocks:
                    quote = quotes.get(stock.code)
                    if not quote or quote.get("trade_date") != target_compact:
                        continue
                    prev_close = _safe_float(quote.get("prev_close"))
                    open_price = _safe_float(quote.get("open"))
                    change_pct = quote.get("change_pct")
                    if prev_close > 0 and open_price > 0:
                        open_premiums.append((open_price - prev_close) / prev_close * 100)
                    if change_pct is not None:
                        current_premiums.append(float(change_pct))
                metrics.next_day_open_sample_count = len(open_premiums)
                metrics.next_day_premium_sample_count = len(current_premiums)
                if open_premiums:
                    metrics.next_day_average_open_pct = round(
                        sum(open_premiums) / len(open_premiums), 2
                    )
                    metrics.next_day_low_open_ratio = round(
                        sum(value < 0 for value in open_premiums) / len(open_premiums) * 100,
                        1,
                    )
                else:
                    missing.append("昨日涨停次日开盘样本")
                if current_premiums:
                    metrics.next_day_average_premium_pct = round(
                        sum(current_premiums) / len(current_premiums), 2
                    )
                else:
                    missing.append("昨日涨停次日溢价样本")
                sources.append(quote_source)
            except Exception as exc:
                missing.append("昨日涨停次日开盘/溢价行情")
                notes.append(f"昨日涨停次日行情不可用：{exc.__class__.__name__}。")

        score, decision, decision_label, evidence, risks = self._score_limit_up_atmosphere(
            metrics, missing
        )
        theme_ladders = self._build_limit_up_theme_ladders(
            ladder=ladder,
            stocks=current_stocks,
            broken_stocks=broken_stocks,
            atmosphere_decision=decision,
        )
        data_quality = "完整" if not missing else "部分"
        result = LimitUpAtmosphereOut(
            source="+".join(dict.fromkeys(sources)),
            trade_date=target_date,
            previous_trade_date=previous_date,
            updated_at=datetime.utcnow(),
            decision=decision,
            decision_label=decision_label,
            score=score,
            data_quality=data_quality,
            metrics=metrics,
            evidence=evidence,
            risks=risks,
            missing_data=list(dict.fromkeys(missing)),
            theme_ladders=theme_ladders,
            notes=notes,
        )
        _set_response_cache(cache_key, result)
        return result

    def _score_limit_up_atmosphere(
        self,
        metrics: LimitUpAtmosphereMetrics,
        missing: list[str],
    ) -> tuple[int, str, str, list[str], list[str]]:
        score = 0
        evidence: list[str] = []
        risks: list[str] = []

        if metrics.seal_rate is not None:
            evidence.append(
                f"封板率 {metrics.seal_rate:.1f}%（涨停{metrics.limit_up_count}只、炸板{metrics.broken_count or 0}只）。"
            )
            if metrics.seal_rate >= 75:
                score += 2
            elif metrics.seal_rate >= 60:
                score += 1
            elif metrics.seal_rate < 45:
                score -= 2
                risks.append("炸板占比过高，追板后当日无法卖出的风险显著。")
            else:
                score -= 1

        if metrics.limit_down_count is not None:
            evidence.append(
                f"涨停/跌停为 {metrics.limit_up_count}/{metrics.limit_down_count}。"
            )
            if metrics.limit_down_count >= max(10, metrics.limit_up_count):
                score -= 2
                risks.append("跌停家数不低于涨停家数，亏钱效应压制打板容错。")
            elif metrics.limit_up_count >= max(10, metrics.limit_down_count * 2):
                score += 1

        evidence.append(f"最高连板 {metrics.highest_board} 板。")
        if metrics.highest_board >= 4:
            score += 1
        elif metrics.highest_board <= 2:
            score -= 1
            risks.append("连板高度未打开，首板次日晋级需要更严格确认。")

        if metrics.promotion_rate is not None:
            evidence.append(
                f"昨日涨停晋级 {metrics.promoted_count or 0}/{metrics.previous_limit_up_count or 0}，晋级率 {metrics.promotion_rate:.1f}%。"
            )
            if metrics.promotion_rate >= 30:
                score += 2
            elif metrics.promotion_rate >= 18:
                score += 1
            elif metrics.promotion_rate < 10:
                score -= 2
                risks.append("昨日涨停晋级率低，接力资金兑现意愿偏强。")
            else:
                score -= 1

        if metrics.next_day_average_open_pct is not None:
            evidence.append(
                f"昨日涨停次日平均开盘 {metrics.next_day_average_open_pct:+.2f}%，低开比例 {metrics.next_day_low_open_ratio or 0:.1f}%（{metrics.next_day_open_sample_count}只）。"
            )
            if (
                metrics.next_day_average_open_pct >= 1
                and metrics.next_day_low_open_ratio is not None
                and metrics.next_day_low_open_ratio <= 35
            ):
                score += 2
            elif (
                metrics.next_day_average_open_pct < 0
                or (metrics.next_day_low_open_ratio or 0) >= 60
            ):
                score -= 2
                risks.append("昨日涨停多数低开或平均开盘无溢价，次日被套风险偏高。")

        if metrics.next_day_average_premium_pct is not None:
            evidence.append(
                f"昨日涨停次日当前/收盘平均溢价 {metrics.next_day_average_premium_pct:+.2f}%（{metrics.next_day_premium_sample_count}只）。"
            )
            if metrics.next_day_average_premium_pct >= 2:
                score += 1
            elif metrics.next_day_average_premium_pct < 0:
                score -= 1
                risks.append("昨日涨停次日平均溢价为负，板上接力的正反馈不足。")

        if metrics.theme_concentration_pct is not None:
            evidence.append(
                f"最大题材“{metrics.top_theme}”占涨停池 {metrics.theme_concentration_pct:.1f}%（{metrics.top_theme_count}只）。"
            )
            if 15 <= metrics.theme_concentration_pct <= 45:
                score += 1
            elif metrics.theme_concentration_pct > 55:
                risks.append("涨停过度集中于单一题材，后排跟风股分化风险较高。")
            elif metrics.theme_concentration_pct < 8:
                score -= 1

        score = max(-10, min(10, score))
        historical_complete = (
            metrics.promotion_rate is not None
            and metrics.next_day_average_open_pct is not None
            and metrics.seal_rate is not None
            and metrics.limit_down_count is not None
        )
        if score <= -3:
            return score, "FORBID", "禁止打板", evidence, risks
        if score >= 4 and historical_complete and not missing:
            return score, "ALLOW", "允许评估打板（仅限前排确认）", evidence, risks
        if missing:
            risks.append("关键数据存在缺口，结论已降级；缺失项恢复前不开放无条件打板。")
        return score, "CAUTION", "谨慎打板", evidence, risks

    def _build_limit_up_theme_ladders(
        self,
        *,
        ladder: LimitUpLadderOut,
        stocks: list[LimitUpStockOut],
        broken_stocks: list[LimitUpStockOut] | None,
        atmosphere_decision: str,
    ) -> list[LimitUpThemeLadderOut]:
        """Build rule-based theme ladders and identity competition from real pools.

        These are observable role labels, not claims about a participant's intent.
        A theme must already be present in the real limit-up clustering; this helper
        never invents a theme or fills missing failed-limit rows.
        """
        global_highest = max((stock.consecutive_limit_days for stock in stocks), default=0)
        results: list[LimitUpThemeLadderOut] = []
        for cluster in ladder.clusters:
            members = list({
                stock.code or stock.name: stock
                for stock in stocks
                if cluster.name == stock.industry or cluster.name in stock.concepts
            }.values())
            if not members:
                continue
            members.sort(
                key=lambda stock: (
                    stock.consecutive_limit_days,
                    self._limit_up_role_score(stock),
                    stock.amount,
                ),
                reverse=True,
            )
            first_count = sum(stock.consecutive_limit_days == 1 for stock in members)
            second_count = sum(stock.consecutive_limit_days == 2 for stock in members)
            high_count = sum(stock.consecutive_limit_days >= 3 for stock in members)
            highest = max(stock.consecutive_limit_days for stock in members)
            layer_count = len({stock.consecutive_limit_days for stock in members})

            theme_broken: list[LimitUpStockOut] | None = None
            if broken_stocks is not None:
                theme_broken = [
                    stock for stock in broken_stocks
                    if cluster.name == stock.industry or cluster.name in stock.concepts
                ]
            broken_count = None if theme_broken is None else len(theme_broken)
            attempted = len(members) + (broken_count or 0)
            seal_rate = (
                round(len(members) / attempted * 100, 1)
                if broken_count is not None and attempted > 0
                else None
            )

            completeness_score = min(25, len(members) * 5)
            completeness_score += min(15, first_count * 5)
            completeness_score += 20 if second_count else 0
            completeness_score += 25 if high_count else 0
            completeness_score += min(15, layer_count * 5)
            completeness_score = max(0, min(100, completeness_score))
            if first_count >= 2 and second_count >= 1 and high_count >= 1:
                completeness_label = "高标、二板、首板梯队完整"
            elif layer_count >= 3:
                completeness_label = "多层梯队已成形"
            elif second_count and first_count:
                completeness_label = "中低位梯队有承接"
            elif high_count and not (first_count or second_count):
                completeness_label = "高标孤军，低位助攻缺失"
            elif first_count == len(members):
                completeness_label = "首板扩散，尚未形成晋级梯队"
            else:
                completeness_label = "梯队存在断层"

            if atmosphere_decision in {"FORBID", "DATA_GAP"}:
                action = "禁止打板"
            elif atmosphere_decision == "ALLOW" and completeness_score >= 70:
                action = "允许观察前排，竞价与封单确认后才可执行"
            elif completeness_score >= 60:
                action = "谨慎接力，只看前排确认"
            else:
                action = "谨慎观望，不追后排跟风"

            if atmosphere_decision in {"FORBID", "DATA_GAP"}:
                continuation = "全市场接力闸门未通过，题材即使有高度也按分化/退潮预期处理。"
            elif completeness_score >= 75 and first_count >= 2 and second_count and high_count:
                continuation = "梯队较完整，次日有延续基础；仍需最高标、二板与首板扩散同时正反馈。"
            elif high_count and first_count == 0:
                continuation = "高标缺少低位助攻，次日更偏向分歧，不能把高标强势外推给后排。"
            elif first_count == len(members):
                continuation = "只有首板扩散，次日先验证一进二，尚不能定义为持续主线。"
            else:
                continuation = "梯队有局部承接但存在断层，次日先看前排卡位结果和后排是否补齐。"

            invalidation = [
                f"题材涨停家数由 {len(members)} 只明显收缩，或炸板率快速上升。",
                f"最高 {highest} 板竞价/开盘弱于同梯队，且开盘后不能回封。",
            ]
            if second_count:
                invalidation.append("二板晋级失败且首板没有新增助攻，梯队承接证伪。")
            else:
                invalidation.append("首板未出现一进二，梯队断层继续扩大。")

            roles = self._build_limit_up_identity_roles(
                members,
                theme_highest=highest,
                global_highest=global_highest,
            )
            results.append(LimitUpThemeLadderOut(
                name=cluster.name,
                limit_up_count=len(members),
                broken_count=broken_count,
                seal_rate=seal_rate,
                first_board_count=first_count,
                second_board_count=second_count,
                high_board_count=high_count,
                highest_level=highest,
                layer_count=layer_count,
                completeness_score=completeness_score,
                completeness_label=completeness_label,
                action=action,
                continuation_expectation=continuation,
                invalidation_conditions=invalidation,
                identity_roles=roles,
            ))
        return sorted(
            results,
            key=lambda item: (
                item.completeness_score,
                item.highest_level,
                item.limit_up_count,
            ),
            reverse=True,
        )[:12]

    def _build_limit_up_identity_roles(
        self,
        stocks: list[LimitUpStockOut],
        *,
        theme_highest: int,
        global_highest: int,
    ) -> list[LimitUpIdentityRoleOut]:
        ranked = sorted(
            stocks,
            key=lambda stock: (self._limit_up_role_score(stock), stock.amount),
            reverse=True,
        )
        if not ranked:
            return []
        capacity = max(ranked, key=lambda stock: stock.amount)
        top_score = self._limit_up_role_score(ranked[0])
        same_height = [stock for stock in ranked if stock.consecutive_limit_days == theme_highest]
        capacity_enabled = len(ranked) >= 3 and capacity.amount > 0
        roles: list[LimitUpIdentityRoleOut] = []
        for index, stock in enumerate(ranked):
            score = self._limit_up_role_score(stock)
            tags: list[str] = []
            if stock.consecutive_limit_days == global_highest and global_highest >= 2:
                tags.append("全场最高标")
            elif stock.consecutive_limit_days == theme_highest and theme_highest >= 2:
                tags.append("题材最高标")
            if index == 0:
                tags.append("龙头候选")
            if (
                len(same_height) >= 2
                and stock in same_height
                and top_score - score <= 12
            ):
                tags.append("同身位卡位竞争")
            if capacity_enabled and stock.code == capacity.code:
                tags.append("容量中军")
            if stock.consecutive_limit_days == 1 and theme_highest >= 2:
                tags.append("补涨候选")
            if not tags:
                tags.append("助攻" if stock.first_limit_time and stock.break_count == 0 else "跟风")
            facts = [
                f"{stock.consecutive_limit_days}板",
                f"成交{stock.amount:.2f}亿",
                f"封单{stock.sealed_amount:.2f}亿",
                f"炸板{stock.break_count}次",
            ]
            if stock.first_limit_time:
                facts.append(f"{stock.first_limit_time}首封")
            roles.append(LimitUpIdentityRoleOut(
                code=stock.code,
                name=stock.name,
                level=stock.consecutive_limit_days,
                roles=list(dict.fromkeys(tags)),
                role_score=score,
                amount=stock.amount,
                sealed_amount=stock.sealed_amount,
                break_count=stock.break_count,
                reason="、".join(facts) + "；角色由梯队身位、封单/成交、首封时间和炸板次数规则计算。",
            ))
        return roles[:8]

    @staticmethod
    def _limit_up_role_score(stock: LimitUpStockOut) -> int:
        score = min(55, max(1, stock.consecutive_limit_days) * 14)
        if stock.amount > 0 and stock.sealed_amount >= 0:
            score += min(20, int(stock.sealed_amount / stock.amount * 100))
        if stock.first_limit_time:
            match = re.match(r"(\d{2}):(\d{2})", stock.first_limit_time)
            if match:
                minutes = int(match.group(1)) * 60 + int(match.group(2))
                if minutes <= 570:
                    score += 15
                elif minutes <= 630:
                    score += 10
                elif minutes <= 810:
                    score += 5
        if stock.amount >= 20:
            score += 8
        elif stock.amount >= 8:
            score += 4
        score -= min(24, stock.break_count * 6)
        return max(0, min(100, score))

    @staticmethod
    def _has_real_dark_trade_values(rows: list[dict[str, Any]]) -> bool:
        """Reject the pre-market placeholder board rows returned with all amounts zero."""
        return any(
            abs(_safe_float(row.get(field))) > 0
            for row in rows
            for field in ("6", "7", "8")
        )

    def _fetch_eastmoney_announcements(self, codes: list[str]) -> list[dict[str, object]]:
        resp = requests.get(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            params={"sr": "-1", "page_size": "50", "page_index": "1", "ann_type": "A", "client_source": "web", "stock_list": ",".join(codes)},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/notices/"}, timeout=10,
        )
        resp.raise_for_status()
        rows = (resp.json().get("data") or {}).get("list") or []
        items: list[dict[str, object]] = []
        for row in rows:
            title = str(row.get("title") or "").strip()
            art_code = str(row.get("art_code") or "")
            stock_codes = [str(item.get("stock_code") or "") for item in (row.get("codes") or []) if isinstance(item, dict)]
            if not title:
                continue
            primary = stock_codes[0] if stock_codes else ""
            items.append({
                "title": title, "summary": title, "source": "东方财富持仓公告",
                "published_at": str(row.get("display_time") or row.get("notice_date") or ""),
                "url": f"https://data.eastmoney.com/notices/detail/{primary}/{art_code}.html" if primary and art_code else None,
                "related_stocks": stock_codes,
                "verification_level": "FORMAL_ANNOUNCEMENT",
                "attribution": "上市公司公告原文",
            })
        return items

    def _fetch_eastmoney_fast_news(self) -> list[dict[str, object]]:
        resp = requests.get(
            "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
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
            related_stocks: list[str] = []
            for stock in row.get("stockList", []) or []:
                if isinstance(stock, dict):
                    raw_stock = stock.get("stockCode") or stock.get("securityCode") or stock.get("code") or ""
                else:
                    raw_stock = stock
                stock_text = str(raw_stock).strip().split(".")[-1]
                if re.fullmatch(r"\d{6}", stock_text):
                    related_stocks.append(stock_text)
            items.append({
                "title": title,
                "summary": summary,
                "source": "东方财富快讯",
                "published_at": str(row.get("showTime") or ""),
                "url": url,
                "related_stocks": list(dict.fromkeys(related_stocks)),
                "verification_level": "MEDIA_ATTRIBUTION",
                "attribution": url or "东方财富快讯原文",
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
                "verification_level": "MEDIA_ATTRIBUTION",
                "attribution": url or "央视新闻联播原文",
            })
        if not items:
            raise ValueError("empty cctv news")
        return items

    def _score_information_item(
        self, item: dict[str, object], flow: SectorFlowOut | None, holding_map: dict[str, str] | None = None
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

        positive_words = ("中标", "增持", "回购", "预增", "突破", "获批", "签订", "上调", "扶持", "利好")
        negative_words = ("减持", "立案", "处罚", "亏损", "下调", "终止", "退市", "风险", "诉讼", "利空")
        positive_hits = [word for word in positive_words if word in text]
        negative_hits = [word for word in negative_words if word in text]
        sentiment = "利好" if len(positive_hits) > len(negative_hits) else "利空" if len(negative_hits) > len(positive_hits) else "中性"
        sentiment_reason = "命中：" + "、".join((positive_hits if sentiment == "利好" else negative_hits)[:3]) if sentiment != "中性" else "未命中明确利好/利空词，等待资金与价格验证"
        holding_map = holding_map or {}
        item_codes = [str(s) for s in item.get("related_stocks", [])]
        related_holding_names = [holding_map[code] for code in item_codes if code in holding_map]
        for code, holding_name in holding_map.items():
            if holding_name and holding_name in text and holding_name not in related_holding_names:
                related_holding_names.append(holding_name)
                item_codes.append(code)
        return InformationItem(
            id=str(abs(hash((item.get("title"), item.get("published_at"))))),
            title=str(item.get("title") or "").strip(),
            summary=str(item.get("summary") or item.get("title") or "").strip(),
            source=source,
            published_at=str(item.get("published_at") or ""),
            keywords=keywords[:6],
            sectors=sectors[:6],
            related_stocks=list(dict.fromkeys(item_codes))[:6],
            strength_score=max(0, min(100, score)),
            credibility="高" if source == "新闻联播" else "中高",
            fund_status=fund_status,
            action=action,
            url=str(item.get("url")) if item.get("url") else None,
            sentiment=sentiment, sentiment_reason=sentiment_reason,
            related_holdings=related_holding_names[:6],
            verification_level=str(item.get("verification_level") or "RUMOR"),
            attribution=str(item.get("attribution") or ""),
        )

