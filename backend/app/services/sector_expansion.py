"""Causal intraday sector-expansion radar.

The service combines the *current* limit-up ladder with same-day sector fund
flow evidence.  It deliberately treats a burst of newly sealed stocks as an
observable hypothesis, not as an entry signal.  A sector is confirmed only
when the burst, fund-flow kinetics and price strength agree at ``as_of``.

The module is intentionally independent from FastAPI and Pydantic so it can be
reused by the opportunity radar, the intraday collector and forward-only
simulation without introducing route-level coupling.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

STATUS_CONFIRMED = "增量已确认"
STATUS_WATCH = "增量待确认"

_POSITIVE_TURNS = {
    "TURN_TO_INFLOW",
    "INFLOW_ACCELERATING",
    "FLOW_IMPROVING",
    "OUTFLOW_NARROWING",
}
_NEGATIVE_TURNS = {
    "TURN_TO_OUTFLOW",
    "INFLOW_FADING",
    "OUTFLOW_ACCELERATING",
    "FLOW_WEAKENING",
}
_IGNORED_THEMES = {
    "",
    "其他",
    "其他题材",
    "未知",
    "未分类",
    "沪深A股",
}
_TRUSTED_SOURCE_MARKERS = ("eastmoney", "东方财富", "sina", "新浪")
_UNTRUSTED_SOURCE_MARKERS = (
    "unavailable",
    "mock",
    "demo",
    "synthetic",
    "fallback",
    "diagnostic",
    "模拟",
    "测试",
)


@dataclass(slots=True)
class SectorExpansionItem:
    sector: str
    status: str
    confirmation_score: int
    window_minutes: int
    total_limit_up_count: int
    new_limit_up_count: int
    highest_board: int
    change_pct: float | None
    net_inflow: float | None
    flow_speed: float | None
    flow_acceleration: float | None
    flow_turning: str | None
    leaders: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    counter_evidence: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    risk: list[str] = field(default_factory=list)
    action: str = ""
    invalidation: list[str] = field(default_factory=list)
    source: list[str] = field(default_factory=list)
    as_of: str = ""
    buy_signal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _FlowEvidence:
    name: str
    source: str
    observed_at: datetime | None
    change_pct: float | None
    net_inflow: float | None
    speed: float | None
    acceleration: float | None
    turning: str | None
    signal: str | None
    kinetics_reliable: bool
    below_vwap: bool | None
    vwap_reliable: bool
    intraday_price_change_pct: float | None


@dataclass(slots=True)
class _ThemeBucket:
    stocks: dict[str, Any] = field(default_factory=dict)
    recent: dict[str, tuple[Any, datetime]] = field(default_factory=dict)


class SectorExpansionRadarService:
    """Identify causal, intraday sector expansion without inventing signals."""

    def __init__(
        self,
        *,
        window_minutes: int = 15,
        min_new_limit_ups: int = 2,
        min_price_strength_pct: float = 0.8,
        max_flow_age_minutes: int = 20,
        max_ladder_age_minutes: int = 6,
    ) -> None:
        self.window_minutes = max(5, min(30, int(window_minutes)))
        self.min_new_limit_ups = max(2, int(min_new_limit_ups))
        self.min_price_strength_pct = float(min_price_strength_pct)
        self.max_flow_age_minutes = max(5, int(max_flow_age_minutes))
        self.max_ladder_age_minutes = max(1, int(max_ladder_age_minutes))

    def assess(
        self,
        ladder: Any,
        sector_flows: Any,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        evaluated_at = _ensure_shanghai(as_of or datetime.now(SHANGHAI_TZ))
        notes = [
            "盘中增量只生成观察结论；禁止追后排，个股仍须通过量价、位置和风险收益比检查。",
        ]

        if evaluated_at.weekday() >= 5:
            notes.append("当前为非交易日，不生成盘中增量方向。")
            return _empty_result(evaluated_at, notes, "missing", self.window_minutes)
        if _trading_minute_index(evaluated_at) is None:
            notes.append("当前不在集合竞价或连续交易时段，不刷新盘中增量方向。")
            return _empty_result(evaluated_at, notes, "missing", self.window_minutes)

        ladder_date = str(_value(ladder, "trade_date", "") or "")
        ladder_source = str(_value(ladder, "source", "") or "").strip()
        ladder_updated_at = _parse_provider_datetime(_value(ladder, "updated_at"), evaluated_at)
        if ladder is None or ladder_date != evaluated_at.date().isoformat():
            notes.append("涨停梯队不是当前交易日数据，不生成盘中增量结论。")
            return _empty_result(evaluated_at, notes, "missing", self.window_minutes)
        if not _is_trusted_source(ladder_source):
            notes.append("涨停梯队不是可核验的东方财富/新浪真实源，不生成盘中增量结论。")
            return _empty_result(evaluated_at, notes, "missing", self.window_minutes)
        if ladder_updated_at is None:
            notes.append("涨停梯队缺少可核验更新时间，不生成盘中增量结论。")
            return _empty_result(evaluated_at, notes, "degraded", self.window_minutes)
        if ladder_updated_at is not None and ladder_updated_at > evaluated_at:
            notes.append("涨停梯队更新时间晚于评估时点，已按因果约束拒绝使用。")
            return _empty_result(evaluated_at, notes, "degraded", self.window_minutes)
        ladder_age = _trading_age_minutes(ladder_updated_at, evaluated_at)
        if ladder_age is None or ladder_age > self.max_ladder_age_minutes:
            notes.append("涨停梯队已过期，不使用旧梯队推断当前增量方向。")
            return _empty_result(evaluated_at, notes, "degraded", self.window_minutes)

        flow_index = _build_flow_index(
            sector_flows,
            as_of=evaluated_at,
            max_age_minutes=self.max_flow_age_minutes,
        )
        buckets, invalid_time_count = _build_theme_buckets(
            ladder,
            as_of=evaluated_at,
            window_minutes=self.window_minutes,
        )
        if invalid_time_count:
            notes.append(f"{invalid_time_count} 只涨停股缺少可用首封时点或首封晚于评估时点，未计入新增封板。")

        items: list[SectorExpansionItem] = []
        for sector, bucket in buckets.items():
            flow = _find_flow(sector, flow_index)
            # A theme with neither a multi-stock burst nor a strong one-stock
            # burst plus supportive real flow is not an intraday expansion.
            recent_count = len(bucket.recent)
            improving_flow = _flow_improving(flow)
            if recent_count < self.min_new_limit_ups and not (recent_count >= 1 and improving_flow):
                continue
            items.append(
                self._assess_theme(
                    sector,
                    bucket,
                    flow,
                    evaluated_at=evaluated_at,
                )
            )

        items.sort(
            key=lambda item: (
                item.status == STATUS_CONFIRMED,
                item.confirmation_score,
                item.new_limit_up_count,
                item.highest_board,
            ),
            reverse=True,
        )
        counts = {
            STATUS_CONFIRMED: sum(item.status == STATUS_CONFIRMED for item in items),
            STATUS_WATCH: sum(item.status == STATUS_WATCH for item in items),
        }
        if items and flow_index:
            quality = "ok"
        elif items or flow_index:
            quality = "degraded"
        else:
            quality = "missing"
        sources = sorted(
            {
                str(_value(ladder, "source", "") or "").strip(),
                *(flow.source for flow in flow_index.values() if flow.source),
            }
            - {""}
        )
        return {
            "updated_at": evaluated_at.isoformat(),
            "as_of": evaluated_at.isoformat(),
            "window_minutes": self.window_minutes,
            "data_quality": quality,
            "source": sources,
            "items": [item.to_dict() for item in items],
            "counts": counts,
            "notes": notes,
        }

    def _assess_theme(
        self,
        sector: str,
        bucket: _ThemeBucket,
        flow: _FlowEvidence | None,
        *,
        evaluated_at: datetime,
    ) -> SectorExpansionItem:
        stocks = list(bucket.stocks.values())
        recent_stocks = [item[0] for item in bucket.recent.values()]
        recent_stocks.sort(
            key=lambda stock: _parse_limit_time(_value(stock, "first_limit_time"), evaluated_at)
            or evaluated_at
        )
        recent_count = len(recent_stocks)
        total_count = len(stocks)
        highest_board = max((_int(_value(stock, "consecutive_limit_days"), 1) for stock in stocks), default=1)
        leaders = [str(_value(stock, "name", "") or _value(stock, "code", "")) for stock in recent_stocks[:6]]

        evidence: list[str] = []
        counter: list[str] = []
        missing: list[str] = []
        risk = ["后排个股可能只是瞬时跟风，禁止追后排或追逐已大幅脱离分时均价的标的。"]
        score = 0

        if recent_count >= self.min_new_limit_ups:
            score += 35 + min(15, (recent_count - self.min_new_limit_ups) * 5)
            seal_facts = []
            for stock in recent_stocks[:5]:
                seal_facts.append(
                    f"{_value(stock, 'name', '')} {_display_time(_value(stock, 'first_limit_time', ''))}首封"
                )
            evidence.append(
                f"最近 {self.window_minutes} 个交易分钟新增 {recent_count} 只涨停：" + "、".join(seal_facts) + "。"
            )
        else:
            missing.append(f"最近 {self.window_minutes} 个交易分钟至少 {self.min_new_limit_ups} 只新增涨停")
            evidence.append(f"最近 {self.window_minutes} 个交易分钟仅新增 {recent_count} 只涨停。")

        change_pct = flow.change_pct if flow else None
        net_inflow = flow.net_inflow if flow else None
        flow_speed = flow.speed if flow else None
        flow_acceleration = flow.acceleration if flow else None
        flow_turning = flow.turning if flow else None

        if flow is None:
            missing.extend(["同名板块真实资金流", "板块涨幅"])
        else:
            if flow.kinetics_reliable:
                if _flow_supportive(flow):
                    score += 25
                    if flow.turning in {"TURN_TO_INFLOW", "INFLOW_ACCELERATING"}:
                        score += 10
                    evidence.append(_flow_evidence_text(flow))
                elif _flow_improving(flow):
                    score += 10
                    evidence.append(_flow_evidence_text(flow))
                    missing.append("板块资金由净流出转为净流入")
                    risk.append("资金只是边际改善但仍为净流出，只能观察反抽，不能确认趋势反转。")
                elif flow.turning in _NEGATIVE_TURNS or (flow.speed is not None and flow.speed < 0):
                    counter.append(_flow_evidence_text(flow))
                    risk.append("板块资金正在转弱，新增涨停可能只是存量脉冲。")
                else:
                    missing.append("资金由流出转流入或加速流入")
            else:
                missing.append("至少两个因果资金快照形成的流速/拐点")

            price_strength = _price_strength_confirmed(flow, self.min_price_strength_pct)
            if price_strength:
                score += 20
                price_text = f"板块涨幅 {flow.change_pct:+.2f}%"
                if flow.intraday_price_change_pct is not None:
                    price_text += f"，观察窗口价格继续走强 {flow.intraday_price_change_pct:+.2f}%"
                if flow.vwap_reliable and flow.below_vwap is False:
                    price_text += "，且位于真实分时均价上方"
                evidence.append(price_text + "。")
            elif flow.change_pct is None:
                missing.append("板块实时涨幅")
            else:
                counter.append(f"板块当前涨幅 {flow.change_pct:+.2f}%，尚未形成同步价格强度。")

            if flow.vwap_reliable and flow.below_vwap is True:
                counter.append("板块价格仍位于真实分时均价下方，不能确认强势扩散。")
                risk.append("价格未站稳分时均价，追涨容易遭遇脉冲回落。")

        break_count = sum(max(0, _int(_value(stock, "break_count"), 0)) for stock in recent_stocks)
        if recent_stocks and break_count >= max(2, recent_count):
            counter.append(f"新增涨停合计炸板 {break_count} 次，封板稳定性偏弱。")
            risk.append("炸板反复，板块扩散质量不足。")
            score -= 10

        confirmed = bool(
            recent_count >= self.min_new_limit_ups
            and flow is not None
            and flow.kinetics_reliable
            and _flow_supportive(flow)
            and _price_strength_confirmed(flow, self.min_price_strength_pct)
            and not (flow.vwap_reliable and flow.below_vwap is True)
        )
        status = STATUS_CONFIRMED if confirmed else STATUS_WATCH
        score = max(0, min(100, score))

        if confirmed:
            action = (
                "仅加入盘中机会观察；优先跟踪最早封板、最高板或板块核心。"
                "等待首次回踩真实分时均价不破、资金未拐出且涨停扩散未退潮后再评估，禁止追后排。"
            )
        else:
            action = (
                "证据尚未闭环，不开仓、不追高；等待新增封板达到门槛，并由真实资金流速和板块价格共同确认。"
            )

        invalidation = [
            "板块资金由流入拐为流出，或流入速度连续转负。",
            "板块跌破真实分时均价且不能在一个观察窗口内收回。",
            "新增涨停集中炸板、核心开板，板块扩散数量明显回落。",
        ]
        sources: list[str] = []
        if flow and flow.source:
            sources.append(flow.source)
        return SectorExpansionItem(
            sector=sector,
            status=status,
            confirmation_score=score,
            window_minutes=self.window_minutes,
            total_limit_up_count=total_count,
            new_limit_up_count=recent_count,
            highest_board=highest_board,
            change_pct=change_pct,
            net_inflow=net_inflow,
            flow_speed=flow_speed,
            flow_acceleration=flow_acceleration,
            flow_turning=flow_turning,
            leaders=leaders,
            evidence=evidence,
            counter_evidence=counter,
            missing=list(dict.fromkeys(missing)),
            risk=list(dict.fromkeys(risk)),
            action=action,
            invalidation=invalidation,
            source=list(dict.fromkeys(sources)),
            as_of=evaluated_at.isoformat(),
            buy_signal=False,
        )


def _empty_result(
    evaluated_at: datetime,
    notes: list[str],
    quality: str,
    window_minutes: int,
) -> dict[str, Any]:
    return {
        "updated_at": evaluated_at.isoformat(),
        "as_of": evaluated_at.isoformat(),
        "window_minutes": window_minutes,
        "data_quality": quality,
        "source": [],
        "items": [],
        "counts": {STATUS_CONFIRMED: 0, STATUS_WATCH: 0},
        "notes": notes,
    }


def _value(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ensure_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def _parse_datetime(value: Any, reference: datetime) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_shanghai(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=reference.tzinfo or SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def _parse_provider_datetime(value: Any, reference: datetime) -> datetime | None:
    """Parse provider timestamps, including the app's legacy naive UTC values.

    ``MarketDataProvider`` currently serializes several ``updated_at`` fields
    with ``datetime.utcnow()`` (naive), while tests and newer services use
    Shanghai-local or timezone-aware values.  For a naive value, evaluate both
    interpretations and choose the one causally closest to ``reference``.
    """

    if isinstance(value, datetime):
        raw = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            raw = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if raw.tzinfo is not None:
        return raw.astimezone(SHANGHAI_TZ)
    local_candidate = raw.replace(tzinfo=SHANGHAI_TZ)
    utc_candidate = raw.replace(tzinfo=timezone.utc).astimezone(SHANGHAI_TZ)
    candidates = [candidate for candidate in (local_candidate, utc_candidate) if candidate <= reference]
    if not candidates:
        return min((local_candidate, utc_candidate), key=lambda candidate: abs((candidate - reference).total_seconds()))
    return min(candidates, key=lambda candidate: abs((reference - candidate).total_seconds()))


def _trading_minute_index(value: datetime) -> int | None:
    current = value.time()
    minutes = current.hour * 60 + current.minute
    if time(9, 15) <= current < time(9, 30):
        return minutes - (9 * 60 + 30)
    if time(9, 30) <= current <= time(11, 30):
        return minutes - (9 * 60 + 30)
    if time(13, 0) <= current <= time(15, 0):
        return 121 + minutes - (13 * 60)
    return None


def _trading_minutes_between(earlier: datetime, later: datetime) -> int | None:
    if earlier.date() != later.date():
        return None
    earlier_index = _trading_minute_index(earlier)
    later_index = _trading_minute_index(later)
    if earlier_index is None or later_index is None:
        return None
    return later_index - earlier_index


def _trading_age_minutes(earlier: datetime, later: datetime) -> int | None:
    """Return same-session age while treating lunch as zero trading minutes."""

    if earlier.date() != later.date() or earlier > later:
        return None
    earlier_index = _trading_minute_index(earlier)
    later_index = _trading_minute_index(later)
    if later_index is None:
        return None
    if earlier_index is None and time(11, 30) < earlier.time() < time(13, 0):
        earlier_index = 120
    if earlier_index is None:
        return None
    return later_index - earlier_index


def _parse_limit_time(value: Any, reference: datetime) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    if re.fullmatch(r"\d{6}", text):
        text = f"{text[:2]}:{text[2:4]}:{text[4:]}"
    elif re.fullmatch(r"\d{4}", text):
        text = f"{text[:2]}:{text[2:]}"
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed_time = datetime.strptime(text, fmt).time()
            return datetime.combine(reference.date(), parsed_time, tzinfo=reference.tzinfo or SHANGHAI_TZ)
        except ValueError:
            continue
    parsed = _parse_datetime(text, reference)
    if parsed is None or parsed.date() != reference.date():
        return None
    return parsed


def _display_time(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{6}", text):
        return f"{text[:2]}:{text[2:4]}"
    match = re.search(r"(\d{2}:\d{2})", text)
    return match.group(1) if match else "未知时点"


def _normalize_sector(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip())
    text = re.sub(r"[（(][^)）]*[)）]$", "", text)
    text = text.replace("Ⅱ", "").replace("Ⅲ", "")
    text = re.sub(r"(?:II|III)$", "", text, flags=re.I)
    text = re.sub(r"^(?:申万|东方财富)", "", text)
    text = re.sub(r"(?:行业|板块|概念)+$", "", text)
    return text


def _stock_key(stock: Any) -> str:
    return str(_value(stock, "code", "") or _value(stock, "name", "")).strip()


def _stock_themes(stock: Any) -> list[str]:
    raw = [str(_value(stock, "industry", "") or "")]
    raw.extend(str(item or "") for item in list(_value(stock, "concepts", []) or []))
    themes: list[str] = []
    for item in raw:
        normalized = _normalize_sector(item)
        if normalized not in _IGNORED_THEMES and normalized not in themes:
            themes.append(normalized)
    return themes


def _ladder_stocks(ladder: Any) -> list[Any]:
    stocks: dict[str, Any] = {}
    for group in list(_value(ladder, "groups", []) or []):
        for stock in list(_value(group, "stocks", []) or []):
            key = _stock_key(stock)
            if key:
                stocks[key] = stock
    return list(stocks.values())


def _build_theme_buckets(
    ladder: Any,
    *,
    as_of: datetime,
    window_minutes: int,
) -> tuple[dict[str, _ThemeBucket], int]:
    buckets: dict[str, _ThemeBucket] = {}
    invalid_time_count = 0
    for stock in _ladder_stocks(ladder):
        key = _stock_key(stock)
        first_limit = _parse_limit_time(_value(stock, "first_limit_time"), as_of)
        elapsed = _trading_minutes_between(first_limit, as_of) if first_limit else None
        if first_limit is None or elapsed is None or elapsed < 0:
            invalid_time_count += 1
        for theme in _stock_themes(stock):
            bucket = buckets.setdefault(theme, _ThemeBucket())
            bucket.stocks[key] = stock
            if elapsed is not None and 0 <= elapsed <= window_minutes:
                bucket.recent[key] = (stock, first_limit)
    return buckets, invalid_time_count


def _flatten_flow_values(value: Any, inherited_updated_at: Any = None) -> Iterable[tuple[Any, Any]]:
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _flatten_flow_values(item, inherited_updated_at)
        return
    inflow = _value(value, "inflow")
    outflow = _value(value, "outflow")
    if inflow is not None or outflow is not None:
        observed = _value(value, "updated_at", inherited_updated_at)
        for item in list(inflow or []) + list(outflow or []):
            yield item, observed
        return
    yield value, inherited_updated_at


def _build_flow_index(
    values: Any,
    *,
    as_of: datetime,
    max_age_minutes: int,
) -> dict[str, _FlowEvidence]:
    result: dict[str, _FlowEvidence] = {}
    for raw, inherited_updated_at in _flatten_flow_values(values):
        name = str(_value(raw, "display_name", "") or _value(raw, "name", "") or "").strip()
        normalized = _normalize_sector(name)
        if not normalized:
            continue
        source = str(_value(raw, "provider", "") or _value(raw, "source", "") or "").strip()
        if not _is_trusted_source(source):
            continue
        observed_at = _parse_datetime(
            _value(raw, "flow_as_of") or _value(raw, "updated_at") or inherited_updated_at,
            as_of,
        )
        if observed_at is not None:
            elapsed = _trading_minutes_between(observed_at, as_of)
            if elapsed is None or elapsed < 0 or elapsed > max_age_minutes:
                continue
        price_change = _intraday_price_change_pct(
            _value(raw, "index_timeline", []),
            as_of=as_of,
            window_minutes=15,
        )
        candidate = _FlowEvidence(
            name=name,
            source=source,
            observed_at=observed_at,
            change_pct=_float(_value(raw, "change_pct")),
            net_inflow=_float(_value(raw, "net_inflow")),
            speed=_float(_value(raw, "flow_speed")),
            acceleration=_float(_value(raw, "flow_acceleration")),
            turning=str(_value(raw, "flow_turning", "") or "") or None,
            signal=str(_value(raw, "flow_signal", "") or "") or None,
            kinetics_reliable=bool(_value(raw, "flow_kinetics_reliable", False) and observed_at is not None),
            below_vwap=_value(raw, "sector_below_vwap"),
            vwap_reliable=bool(_value(raw, "sector_vwap_reliable", False)),
            intraday_price_change_pct=price_change,
        )
        previous = result.get(normalized)
        # Prefer reliable kinetics, then the freshest timestamp.  This avoids
        # an industry/concept alias overwriting a richer real-time curve.
        if previous is None or (
            candidate.kinetics_reliable,
            candidate.observed_at or datetime.min.replace(tzinfo=SHANGHAI_TZ),
        ) > (
            previous.kinetics_reliable,
            previous.observed_at or datetime.min.replace(tzinfo=SHANGHAI_TZ),
        ):
            result[normalized] = candidate
    return result


def _is_trusted_source(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text or any(marker in text for marker in _UNTRUSTED_SOURCE_MARKERS):
        return False
    return any(marker in text for marker in _TRUSTED_SOURCE_MARKERS)


def _find_flow(sector: str, index: Mapping[str, _FlowEvidence]) -> _FlowEvidence | None:
    normalized = _normalize_sector(sector)
    exact = index.get(normalized)
    if exact is not None:
        return exact
    candidates = [
        value
        for key, value in index.items()
        if len(normalized) >= 2 and (normalized in key or key in normalized)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item.kinetics_reliable,
            item.observed_at or datetime.min.replace(tzinfo=SHANGHAI_TZ),
        ),
        reverse=True,
    )
    return candidates[0]


def _flow_supportive(flow: _FlowEvidence | None) -> bool:
    if flow is None or not flow.kinetics_reliable:
        return False
    turning_supportive = flow.turning in _POSITIVE_TURNS
    speed_supportive = flow.speed is not None and flow.speed > 0
    net_supportive = flow.net_inflow is not None and flow.net_inflow > 0
    # OUTFLOW_NARROWING is an improvement but cannot by itself confirm a new
    # trend while the sector still has net outflow.
    if flow.turning == "OUTFLOW_NARROWING" and not net_supportive:
        return False
    return bool(turning_supportive and (speed_supportive or net_supportive) and net_supportive)


def _flow_improving(flow: _FlowEvidence | None) -> bool:
    """Return observable marginal improvement, even before net flow turns positive."""

    if flow is None or not flow.kinetics_reliable:
        return False
    return bool(flow.turning in _POSITIVE_TURNS and flow.speed is not None and flow.speed > 0)


def _price_strength_confirmed(flow: _FlowEvidence, threshold: float) -> bool:
    current_strong = flow.change_pct is not None and flow.change_pct >= threshold
    intraday_strong = flow.intraday_price_change_pct is not None and flow.intraday_price_change_pct >= 0.25
    above_vwap = not flow.vwap_reliable or flow.below_vwap is False
    return bool(current_strong and above_vwap and (intraday_strong or flow.intraday_price_change_pct is None))


def _flow_evidence_text(flow: _FlowEvidence) -> str:
    parts: list[str] = []
    if flow.net_inflow is not None:
        parts.append(f"净流入 {flow.net_inflow:+.2f} 亿")
    if flow.speed is not None:
        parts.append(f"流速 {flow.speed:+.3f} 亿/分钟")
    if flow.acceleration is not None:
        parts.append(f"加速度 {flow.acceleration:+.4f} 亿/分钟²")
    if flow.signal:
        parts.append(flow.signal)
    elif flow.turning:
        parts.append(flow.turning)
    return "资金证据：" + "，".join(parts) + "。"


def _intraday_price_change_pct(
    points: Any,
    *,
    as_of: datetime,
    window_minutes: int,
) -> float | None:
    causal: list[tuple[int, float]] = []
    for point in list(points or []):
        observed_at = _parse_limit_time(_value(point, "time"), as_of)
        if observed_at is None or observed_at > as_of:
            continue
        minute_index = _trading_minute_index(observed_at)
        price = _float(_value(point, "price"))
        if minute_index is not None and price is not None and price > 0:
            causal.append((minute_index, price))
    if len(causal) < 2:
        return None
    causal.sort(key=lambda item: item[0])
    latest_index, latest_price = causal[-1]
    eligible = [item for item in causal[:-1] if latest_index - item[0] <= window_minutes]
    if not eligible:
        return None
    reference_price = eligible[0][1]
    return round((latest_price - reference_price) / reference_price * 100, 3)


__all__ = [
    "STATUS_CONFIRMED",
    "STATUS_WATCH",
    "SectorExpansionItem",
    "SectorExpansionRadarService",
]
