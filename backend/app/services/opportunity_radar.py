"""News-to-market confirmation radar.

News is treated as a hypothesis, never as a buy signal.  A sector needs real
fund flow, price strength and a reliable price/VWAP relationship before an
item can become ``已确认``.  Even a confirmed item is only eligible for the
watchlist; stock-level entry rules remain mandatory.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.services.reflexivity import analyze_news_impact


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

STATUS_PENDING = "待确认"
STATUS_CONFIRMED = "已确认"
STATUS_DECAYED = "衰减"
STATUS_INVALIDATED = "证伪"


SECTOR_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "商业航天": ("商业航天", "卫星互联网", "火箭", "卫星", "航天"),
    "半导体": ("半导体", "芯片", "光刻机", "存储", "先进封装", "SK海力士", "三星电子"),
    "人工智能": ("人工智能", "AI", "大模型", "算力", "服务器"),
    "机器人": ("机器人", "人形机器人", "减速器", "机器视觉"),
    "创新药": ("创新药", "医药", "生物医药", "临床", "获批"),
    "低空经济": ("低空经济", "无人机", "通航", "飞行汽车"),
    "汽车": ("汽车", "智能驾驶", "新能源车", "零部件"),
    "电力设备": ("电网", "电力设备", "储能", "特高压"),
    "证券": ("券商", "证券", "资本市场"),
    "军工": ("军工", "国防", "军贸", "航空装备"),
}


@dataclass(slots=True)
class SectorEvidence:
    name: str
    source: str = ""
    captured_at: datetime | None = None
    change_pct: float | None = None
    market_change_pct: float | None = None
    relative_change_pct: float | None = None
    net_inflow: float | None = None
    main_inflow: float | None = None
    acceleration: float | None = None
    flow_direction: str | None = None
    flow_speed: float | None = None
    flow_acceleration: float | None = None
    flow_turning: str | None = None
    flow_kinetics_reliable: bool = False
    price: float | None = None
    vwap: float | None = None
    vwap_reliable: bool = False
    breadth_up_ratio: float | None = None
    leaders: list[str] = field(default_factory=list)

    @classmethod
    def from_value(cls, value: Any, market_change_pct: float | None = None) -> "SectorEvidence":
        change = _optional_float(_value(value, "change_pct"))
        market_change = _optional_float(_value(value, "market_change_pct", market_change_pct))
        relative = _optional_float(_value(value, "relative_change_pct"))
        if relative is None and change is not None and market_change is not None:
            relative = change - market_change
        timeline = list(_value(value, "timeline", []) or [])
        acceleration = _optional_float(_value(value, "acceleration"))
        if acceleration is None and len(timeline) >= 2:
            latest = _optional_float(_value(timeline[-1], "value"))
            earlier = _optional_float(_value(timeline[max(0, len(timeline) - 4)], "value"))
            if latest is not None and earlier is not None:
                acceleration = latest - earlier
        return cls(
            name=str(_value(value, "name", "") or _value(value, "display_name", "") or ""),
            source=str(_value(value, "source", "") or _value(value, "provider", "") or ""),
            captured_at=_parse_datetime(
                _value(value, "flow_as_of") or _value(value, "captured_at") or _value(value, "updated_at")
            ),
            change_pct=change,
            market_change_pct=market_change,
            relative_change_pct=relative,
            net_inflow=_optional_float(_value(value, "net_inflow")),
            main_inflow=_optional_float(_value(value, "main_inflow")),
            acceleration=acceleration,
            flow_direction=str(_value(value, "flow_direction", "") or "") or None,
            flow_speed=_optional_float(_value(value, "flow_speed")),
            flow_acceleration=_optional_float(_value(value, "flow_acceleration")),
            flow_turning=str(_value(value, "flow_turning", "") or "") or None,
            flow_kinetics_reliable=bool(_value(value, "flow_kinetics_reliable", False)),
            price=_optional_float(_value(value, "sector_price") or _value(value, "price")),
            vwap=_optional_float(_value(value, "sector_vwap") or _value(value, "vwap")),
            vwap_reliable=bool(_value(value, "sector_vwap_reliable", _value(value, "vwap_reliable", False))),
            breadth_up_ratio=_optional_float(_value(value, "breadth_up_ratio")),
            leaders=[str(item) for item in list(_value(value, "leaders", []) or []) if str(item).strip()][:6],
        )


@dataclass(slots=True)
class SectorAssessment:
    sector: str
    status: str
    confirmation_score: int
    funds_confirmed: bool
    price_confirmed: bool
    vwap_confirmed: bool
    evidence: list[str]
    counter_evidence: list[str]
    missing: list[str]
    source: str = ""
    captured_at: str | None = None


@dataclass(slots=True)
class OpportunityAssessment:
    id: str
    title: str
    source: str
    published_at: str
    age_minutes: int | None
    sectors: list[str]
    related_stocks: list[str]
    status: str
    confirmation_score: int
    primary_sector: str | None
    evidence: list[str]
    counter_evidence: list[str]
    missing: list[str]
    sector_assessments: list[SectorAssessment]
    action: str
    trade_constraint: str = "资讯不得单独触发买入；仍需个股量价、地位、风险收益比和仓位检查。"
    buy_signal: bool = False
    url: str | None = None
    expires_at: str | None = None
    claim_level: str = "RUMOR"
    news_impact_status: str = "UNVERIFIED"
    market_validation: str = "PENDING"
    sentiment: str = "待验证"
    sentiment_reason: str = ""
    escalate_to_holding_risk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OpportunityRadarService:
    def __init__(
        self,
        *,
        max_age_minutes: int = 180,
        min_relative_strength_pct: float = 0.3,
        min_net_inflow: float = 0.0,
        now_provider=None,
    ) -> None:
        self.max_age_minutes = max(1, int(max_age_minutes))
        self.min_relative_strength_pct = float(min_relative_strength_pct)
        self.min_net_inflow = float(min_net_inflow)
        self.now_provider = now_provider or (lambda: datetime.now(SHANGHAI_TZ))

    def assess(
        self,
        information: Any,
        sector_flows: Any,
        *,
        market_change_pct: float | None = None,
        previous_statuses: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        now = _ensure_timezone(self.now_provider())
        news_items = _information_items(information)
        evidence_index = build_sector_evidence_index(sector_flows, market_change_pct=market_change_pct)
        previous_statuses = previous_statuses or {}
        assessments = [
            self.assess_item(
                item,
                evidence_index,
                now=now,
                previous_status=previous_statuses.get(_news_id(item)),
            )
            for item in news_items
        ]
        status_priority = {
            STATUS_CONFIRMED: 4,
            STATUS_PENDING: 3,
            STATUS_DECAYED: 2,
            STATUS_INVALIDATED: 1,
        }
        assessments.sort(
            key=lambda item: (status_priority.get(item.status, 0), item.confirmation_score),
            reverse=True,
        )
        counts = {status: 0 for status in (STATUS_CONFIRMED, STATUS_PENDING, STATUS_DECAYED, STATUS_INVALIDATED)}
        for item in assessments:
            counts[item.status] = counts.get(item.status, 0) + 1
        source_names = sorted(
            {
                value
                for value in (
                    *(str(_value(item, "source") or "").strip() for item in news_items),
                    *(evidence.source for evidence in evidence_index.values()),
                )
                if value
            }
        )
        if news_items and evidence_index:
            data_quality = "ok"
        elif news_items or evidence_index:
            data_quality = "degraded"
        else:
            data_quality = "missing"
        discipline = "新闻只生成观察假设；未经板块资金、价格和可靠VWAP共同确认，不得升级。即使已确认也不是买入信号。"
        return {
            "updated_at": now.isoformat(),
            "as_of": now.isoformat(),
            "source": source_names,
            "data_quality": data_quality,
            "items": [item.to_dict() for item in assessments],
            "counts": counts,
            "discipline": discipline,
            "notes": [discipline],
            "available_sector_evidence": len(evidence_index),
        }

    def assess_item(
        self,
        item: Any,
        evidence_index: Mapping[str, SectorEvidence],
        *,
        now: datetime | None = None,
        previous_status: str | None = None,
    ) -> OpportunityAssessment:
        now = _ensure_timezone(now or self.now_provider())
        title = str(_value(item, "title", "") or "")
        summary = str(_value(item, "summary", "") or "")
        sectors = _mapped_sectors(item, f"{title} {summary}")
        published = _parse_datetime(_value(item, "published_at"), reference=now)
        age_minutes = max(0, int((now - published).total_seconds() // 60)) if published else None
        expires_at = published + timedelta(minutes=self.max_age_minutes) if published else None

        sector_assessments: list[SectorAssessment] = []
        for sector in sectors:
            evidence = _find_sector_evidence(sector, evidence_index)
            sector_assessments.append(self._assess_sector(sector, evidence))

        sector_assessments.sort(key=lambda row: row.confirmation_score, reverse=True)
        primary = sector_assessments[0] if sector_assessments else None
        status = primary.status if primary else STATUS_PENDING
        if age_minutes is not None and age_minutes > self.max_age_minutes and status != STATUS_INVALIDATED:
            status = STATUS_DECAYED
        elif previous_status == STATUS_CONFIRMED and status == STATUS_PENDING:
            status = STATUS_DECAYED

        if status == STATUS_CONFIRMED:
            action = "板块证据已确认，可加入机会观察池；等待核心个股量价买点，禁止追后排。"
        elif status == STATUS_INVALIDATED:
            action = "消息方向已被板块资金与量价证伪，停止据此开仓。"
        elif status == STATUS_DECAYED:
            action = "消息时效或市场承接已衰减，不再作为新开仓依据。"
        else:
            action = "仅记录信息差，等待板块资金、价格和分时均价共同确认。"

        evidence = list(primary.evidence) if primary else []
        counter = list(primary.counter_evidence) if primary else []
        missing = list(primary.missing) if primary else ["未映射到可验证板块"]
        market_evidence: dict[str, Any] = {}
        if primary:
            primary_value = _find_sector_evidence(primary.sector, evidence_index)
            if primary_value:
                market_evidence = {
                    "fund_direction": primary_value.flow_direction
                    or (
                        "NET_INFLOW" if primary_value.net_inflow is not None and primary_value.net_inflow > 0
                        else "NET_OUTFLOW" if primary_value.net_inflow is not None and primary_value.net_inflow < 0
                        else "NEUTRAL"
                    ),
                    "flow_turning": primary_value.flow_turning or "",
                    "price_direction": (
                        "UP" if (primary_value.change_pct or 0) > 0
                        else "DOWN" if (primary_value.change_pct or 0) < 0 else "FLAT"
                    ),
                    "vwap_position": (
                        "ABOVE" if primary_value.vwap_reliable and primary_value.price is not None
                        and primary_value.vwap is not None and primary_value.price >= primary_value.vwap
                        else "BELOW" if primary_value.vwap_reliable and primary_value.price is not None
                        and primary_value.vwap is not None else "UNKNOWN"
                    ),
                    "captured_at": primary_value.captured_at.isoformat() if primary_value.captured_at else None,
                    "fund_reliable": primary_value.flow_kinetics_reliable,
                    "price_reliable": bool(primary_value.vwap_reliable or primary_value.change_pct is not None),
                    "holding_related": bool(list(_value(item, "related_holdings", []) or [])),
                    # Sector-wide high-open crowding is evaluated separately;
                    # absence must never be silently treated as confirmation.
                    "consensus_high_open_fade": False,
                }
        impact = analyze_news_impact(
            {
                "title": title,
                "source": str(_value(item, "source", "") or ""),
                "url": _value(item, "url"),
                "published_at": _value(item, "published_at"),
                "verification_level": _value(item, "verification_level", "RUMOR"),
                "attribution": _value(item, "attribution", ""),
                "sentiment": _value(item, "sentiment", "待验证"),
                "sentiment_reason": _value(item, "sentiment_reason", ""),
                "sectors": sectors,
                "related_stocks": list(_value(item, "related_stocks", []) or []),
                "holding_related": bool(list(_value(item, "related_holdings", []) or [])),
            },
            market_evidence,
            now=now,
            max_age_minutes=self.max_age_minutes,
        )
        if impact["claim_level"] == "RUMOR":
            action = f"传闻/未核验线索不得当作事实；{action}"
        elif impact["market_validation"] == "CONFIRMED":
            action = f"消息方向获得后续资金量价验证，但不等于因果已证实；{action}"
        return OpportunityAssessment(
            id=_news_id(item),
            title=title,
            source=str(_value(item, "source", "资讯") or "资讯"),
            published_at=published.isoformat() if published else str(_value(item, "published_at", "") or ""),
            age_minutes=age_minutes,
            sectors=sectors,
            related_stocks=[str(value) for value in list(_value(item, "related_stocks", []) or [])][:12],
            status=status,
            confirmation_score=primary.confirmation_score if primary else 0,
            primary_sector=primary.sector if primary else None,
            evidence=evidence,
            counter_evidence=counter,
            missing=missing,
            sector_assessments=sector_assessments,
            action=action,
            url=str(_value(item, "url")) if _value(item, "url") else None,
            expires_at=expires_at.isoformat() if expires_at else None,
            claim_level=str(impact["claim_level"]),
            news_impact_status=str(impact["status"]),
            market_validation=str(impact["market_validation"]),
            sentiment=str(impact["sentiment"]),
            sentiment_reason=str(impact["sentiment_reason"]),
            escalate_to_holding_risk=bool(impact["escalate_to_holding_risk"]),
        )

    def _assess_sector(self, sector: str, evidence: SectorEvidence | None) -> SectorAssessment:
        if evidence is None:
            return SectorAssessment(
                sector=sector,
                status=STATUS_PENDING,
                confirmation_score=0,
                funds_confirmed=False,
                price_confirmed=False,
                vwap_confirmed=False,
                evidence=[],
                counter_evidence=[],
                missing=["板块资金", "板块涨幅/相对强度", "可靠板块VWAP"],
            )

        evidence_text: list[str] = []
        counter: list[str] = []
        missing: list[str] = []
        net = evidence.net_inflow
        main = evidence.main_inflow
        funds_available = net is not None or main is not None
        funds_confirmed = bool(
            (net is not None and net > self.min_net_inflow)
            and (main is None or main > 0)
        )
        funds_invalid = bool(
            (net is not None and net < 0)
            and (main is None or main < 0)
        )
        if not funds_available:
            missing.append("板块资金")
        elif funds_confirmed:
            evidence_text.append(f"板块净流入{net:+.2f}亿" + (f"，主力{main:+.2f}亿" if main is not None else ""))
        elif funds_invalid:
            counter.append(f"板块净流出{net:.2f}亿" + (f"，主力{main:.2f}亿" if main is not None else ""))
        else:
            counter.append("板块资金尚未形成同向净流入")

        relative = evidence.relative_change_pct
        change = evidence.change_pct
        price_available = relative is not None or change is not None
        price_confirmed = bool(
            relative is not None and relative >= self.min_relative_strength_pct
            or relative is None and change is not None and change > 0
        )
        price_invalid = bool(
            relative is not None and relative <= -self.min_relative_strength_pct
            or relative is None and change is not None and change < 0
        )
        if not price_available:
            missing.append("板块涨幅/相对强度")
        elif price_confirmed:
            if relative is not None:
                evidence_text.append(f"板块相对大盘超额{relative:+.2f}%")
            else:
                evidence_text.append(f"板块涨幅{change:+.2f}%")
        elif price_invalid:
            counter.append(
                f"板块相对大盘落后{relative:.2f}%" if relative is not None else f"板块下跌{change:.2f}%"
            )
        else:
            counter.append("板块价格尚未形成明显相对强度")

        vwap_available = evidence.vwap_reliable and evidence.price is not None and evidence.vwap is not None and evidence.vwap > 0
        vwap_confirmed = bool(vwap_available and evidence.price >= evidence.vwap)
        vwap_invalid = bool(vwap_available and evidence.price < evidence.vwap)
        if not vwap_available:
            missing.append("可靠板块VWAP")
        elif vwap_confirmed:
            evidence_text.append(f"板块指数{evidence.price:.2f}站上分时均价{evidence.vwap:.2f}")
        elif vwap_invalid:
            counter.append(f"板块指数{evidence.price:.2f}低于分时均价{evidence.vwap:.2f}")

        if evidence.acceleration is not None:
            if evidence.acceleration > 0:
                evidence_text.append(f"资金较近期采样加速{evidence.acceleration:+.2f}亿")
            elif evidence.acceleration < 0:
                counter.append(f"资金较近期采样减速{evidence.acceleration:.2f}亿")
        if evidence.breadth_up_ratio is not None:
            if evidence.breadth_up_ratio >= 0.55:
                evidence_text.append(f"板块上涨宽度{evidence.breadth_up_ratio:.0%}")
            elif evidence.breadth_up_ratio < 0.35:
                counter.append(f"板块上涨宽度仅{evidence.breadth_up_ratio:.0%}")

        mandatory_count = sum((funds_confirmed, price_confirmed, vwap_confirmed))
        score = mandatory_count * 25
        if evidence.acceleration is not None and evidence.acceleration > 0:
            score += 10
        if evidence.breadth_up_ratio is not None and evidence.breadth_up_ratio >= 0.55:
            score += 10
        if evidence.leaders:
            score += 5
        score = min(100, score)

        if funds_invalid and price_invalid and vwap_invalid:
            status = STATUS_INVALIDATED
        elif funds_confirmed and price_confirmed and vwap_confirmed:
            status = STATUS_CONFIRMED
        else:
            status = STATUS_PENDING

        return SectorAssessment(
            sector=sector,
            status=status,
            confirmation_score=score,
            funds_confirmed=funds_confirmed,
            price_confirmed=price_confirmed,
            vwap_confirmed=vwap_confirmed,
            evidence=evidence_text,
            counter_evidence=counter,
            missing=missing,
            source=evidence.source,
            captured_at=evidence.captured_at.isoformat() if evidence.captured_at else None,
        )


def build_sector_evidence_index(
    sector_flows: Any,
    *,
    market_change_pct: float | None = None,
) -> dict[str, SectorEvidence]:
    """Normalize one/many SectorFlowOut instances or raw evidence records."""
    values: list[Any] = []
    if sector_flows is None:
        return {}
    if isinstance(sector_flows, (list, tuple, set)):
        roots = list(sector_flows)
    else:
        roots = [sector_flows]
    for root in roots:
        inflow = _value(root, "inflow")
        outflow = _value(root, "outflow")
        if inflow is not None or outflow is not None:
            values.extend(list(inflow or []))
            values.extend(list(outflow or []))
        elif isinstance(root, Mapping) and not _value(root, "name"):
            values.extend(root.values())
        else:
            values.append(root)

    index: dict[str, SectorEvidence] = {}
    for value in values:
        evidence = value if isinstance(value, SectorEvidence) else SectorEvidence.from_value(value, market_change_pct)
        if not evidence.name:
            continue
        aliases = {
            evidence.name,
            str(_value(value, "display_name", "") or ""),
            str(_value(value, "raw_name", "") or ""),
            str(_value(value, "theme_line", "") or ""),
            str(_value(value, "mainline", "") or ""),
            str(_value(value, "subline", "") or ""),
            str(_value(value, "category", "") or ""),
        }
        for alias in aliases:
            normalized = _normalize_sector(alias)
            if normalized:
                previous = index.get(normalized)
                if previous is None or _evidence_completeness(evidence) > _evidence_completeness(previous):
                    index[normalized] = evidence
    return index


def _information_items(information: Any) -> list[Any]:
    if information is None:
        return []
    items = _value(information, "items")
    if items is not None:
        return list(items or [])
    if isinstance(information, Mapping):
        return [information]
    if isinstance(information, Iterable) and not isinstance(information, (str, bytes)):
        return list(information)
    return [information]


def _mapped_sectors(item: Any, text: str) -> list[str]:
    explicit = [str(value).strip() for value in list(_value(item, "sectors", []) or []) if str(value).strip()]
    mapped = list(explicit)
    lowered = text.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            mapped.append(sector)
    return list(dict.fromkeys(mapped))[:12]


def _find_sector_evidence(sector: str, index: Mapping[str, SectorEvidence]) -> SectorEvidence | None:
    normalized = _normalize_sector(sector)
    if normalized in index:
        return index[normalized]
    candidates = [
        evidence for alias, evidence in index.items()
        if normalized and (normalized in alias or alias in normalized)
    ]
    return max(candidates, key=_evidence_completeness) if candidates else None


def _evidence_completeness(value: SectorEvidence) -> int:
    return sum(
        item is not None
        for item in (
            value.change_pct, value.net_inflow, value.main_inflow, value.price,
            value.vwap, value.acceleration, value.flow_speed, value.flow_acceleration,
        )
    ) + int(value.vwap_reliable) + int(value.flow_kinetics_reliable)


def _normalize_sector(value: str) -> str:
    text = str(value or "").strip().lower()
    for token in ("概念", "行业", "板块", "产业链", "主题"):
        text = text.replace(token, "")
    return "".join(text.split())


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any, *, reference: datetime | None = None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _ensure_timezone(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return _ensure_timezone(datetime.fromisoformat(text))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return _ensure_timezone(datetime.strptime(text, fmt))
        except ValueError:
            continue
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        base = _ensure_timezone(reference or datetime.now(SHANGHAI_TZ))
        return base.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second, microsecond=0)
    return None


def _ensure_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def _news_id(item: Any) -> str:
    existing = str(_value(item, "id", "") or "")
    if existing:
        return existing
    raw = "|".join(
        str(_value(item, key, "") or "")
        for key in ("source", "title", "published_at", "url")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


__all__ = [
    "OpportunityRadarService",
    "OpportunityAssessment",
    "SectorAssessment",
    "SectorEvidence",
    "build_sector_evidence_index",
    "STATUS_PENDING",
    "STATUS_CONFIRMED",
    "STATUS_DECAYED",
    "STATUS_INVALIDATED",
]
