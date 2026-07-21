from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import requests


_SHANGHAI_TZ = timezone(timedelta(hours=8))
_ACCEPTED_QUALITIES = {"audited", "official", "official_audited"}
_MAX_SOURCE_AGE = timedelta(hours=36)
_NON_LEVERAGED_UNIT = "亿元"
_ETF_SHARE_UNIT = "份"


def _shanghai_now() -> datetime:
    """Return a patchable clock for source-freshness validation."""

    return datetime.now(_SHANGHAI_TZ)


@dataclass(slots=True)
class SectorAuditedFlowConfiguration:
    endpoint: str = ""
    bearer_token: str = ""
    configuration_error: str = ""

    @classmethod
    def from_environment(cls) -> "SectorAuditedFlowConfiguration":
        endpoint = os.getenv("SECTOR_AUDITED_FLOW_URL", "").strip()
        direct_token = os.getenv("SECTOR_AUDITED_FLOW_TOKEN", "").strip()
        token_file = os.getenv("SECTOR_AUDITED_FLOW_TOKEN_FILE", "").strip()
        if direct_token:
            return cls(endpoint=endpoint, bearer_token=direct_token)
        if not token_file:
            return cls(endpoint=endpoint)
        try:
            file_token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError:
            return cls(
                endpoint=endpoint,
                configuration_error="授权令牌文件不可读",
            )
        return cls(endpoint=endpoint, bearer_token=file_token)

    @property
    def configured(self) -> bool:
        return bool(
            self.endpoint
            and self.bearer_token
            and not self.configuration_error
            and _is_https_url(self.endpoint)
        )


def _is_https_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
    )


def _iso_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    # An auditable provider timestamp must identify an unambiguous instant.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.isoformat()


def _source_timestamp(
    value: Any,
    *,
    requested_trade_date: str,
    now: datetime,
) -> tuple[str | None, str]:
    normalized = _iso_timestamp(value)
    if normalized is None:
        return None, "来源时间缺失、格式无效或未携带时区"
    parsed = datetime.fromisoformat(normalized)
    shanghai_time = parsed.astimezone(_SHANGHAI_TZ)
    if shanghai_time.date().isoformat() != requested_trade_date:
        return None, "来源时间换算为上海时间后与请求交易日不一致"
    if parsed > now.astimezone(parsed.tzinfo):
        return None, "来源时间晚于服务器当前时间"
    if now.astimezone(parsed.tzinfo) - parsed > _MAX_SOURCE_AGE:
        return None, "来源时间已过期"
    return normalized, ""


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _non_negative_int(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _valid_trade_date(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _payload_records(body: Any) -> tuple[list[Any], Mapping[str, Any]]:
    if isinstance(body, list):
        return list(body), {}
    if not isinstance(body, Mapping):
        return [], {}
    for key in ("items", "data", "records", "results"):
        rows = body.get(key)
        if isinstance(rows, list):
            return list(rows), body
    # A single explicitly identified board record is also valid JSON input.
    if any(body.get(key) not in (None, "") for key in ("board_name", "name", "board_code")):
        return [body], {}
    return [], body


def _envelope_value(
    row: Mapping[str, Any],
    envelope: Mapping[str, Any],
    key: str,
) -> Any:
    value = row.get(key)
    return value if value not in (None, "") else envelope.get(key)


def _normalize_item(
    row: Mapping[str, Any],
    envelope: Mapping[str, Any],
    *,
    requested_trade_date: str,
    now: datetime,
) -> tuple[dict[str, Any] | None, str]:
    board_name = str(row.get("board_name") or row.get("name") or "").strip()
    board_code = str(row.get("board_code") or "").strip().upper()
    if not board_name and not board_code:
        return None, "缺少板块名称或代码"

    trade_date = _valid_trade_date(_envelope_value(row, envelope, "trade_date"))
    if trade_date != requested_trade_date:
        return None, "交易日缺失或与请求交易日不一致"

    net_inflow = _number(row.get("non_leveraged_net_inflow"))
    if net_inflow is None:
        return None, "缺少可验证的非杠杆净流入数值"
    net_inflow_unit = str(
        _envelope_value(row, envelope, "non_leveraged_net_inflow_unit") or ""
    ).strip()
    if net_inflow_unit != _NON_LEVERAGED_UNIT:
        return None, "非杠杆净流入必须显式声明单位为亿元"
    methodology_id = str(
        _envelope_value(row, envelope, "methodology_id") or ""
    ).strip()
    if not methodology_id:
        return None, "非杠杆净流入缺少methodology_id"

    source_url = str(_envelope_value(row, envelope, "source_url") or "").strip()
    if not _is_https_url(source_url):
        return None, "来源链接缺失或不是HTTPS"

    raw_published_at = _envelope_value(row, envelope, "published_at")
    raw_observed_at = _envelope_value(row, envelope, "observed_at")
    if raw_published_at in (None, "") and raw_observed_at in (None, ""):
        return None, "缺少带时区的发布时间或观测时间"
    published_at: str | None = None
    observed_at: str | None = None
    for field_name, raw_value in (
        ("发布时间", raw_published_at),
        ("观测时间", raw_observed_at),
    ):
        if raw_value in (None, ""):
            continue
        normalized, error = _source_timestamp(
            raw_value,
            requested_trade_date=requested_trade_date,
            now=now,
        )
        if normalized is None:
            return None, f"{field_name}{error}"
        if field_name == "发布时间":
            published_at = normalized
        else:
            observed_at = normalized

    quality = str(_envelope_value(row, envelope, "data_quality") or "").strip().lower()
    if quality not in _ACCEPTED_QUALITIES:
        return None, "数据质量未明确标记为audited或official"

    raw_new_high_count = row.get("new_high_count")
    raw_constituent_count = row.get("constituent_count")
    new_high_count = _non_negative_int(raw_new_high_count)
    constituent_count = _non_negative_int(raw_constituent_count)
    if raw_new_high_count not in (None, "") or raw_constituent_count not in (None, ""):
        if (
            new_high_count is None
            or constituent_count is None
            or constituent_count <= 0
            or new_high_count > constituent_count
        ):
            return None, "20日创新高家数或成分股总数不合法"
        new_high_window = _non_negative_int(row.get("new_high_window") or 20)
        if new_high_window != 20:
            return None, "创新高口径必须明确为20个交易日"

    etf_share_net_change = _number(row.get("etf_share_net_change"))
    etf_share_change_pct = _number(row.get("etf_share_change_pct"))
    etf_fields_present = any(
        row.get(key) not in (None, "")
        for key in (
            "etf_share_net_change",
            "etf_share_change_pct",
            "etf_id",
            "etf_share_unit",
            "etf_share_base",
            "etf_methodology_id",
        )
    )
    etf_id: str | None = None
    etf_share_base: float | None = None
    etf_methodology_id: str | None = None
    if etf_fields_present:
        if etf_share_net_change is None or etf_share_change_pct is None:
            return None, "ETF真实份额净变化与变化率必须同时提供"
        etf_id = str(row.get("etf_id") or "").strip().upper()
        if not etf_id:
            return None, "ETF真实份额证据缺少ETF标识"
        if str(row.get("etf_share_unit") or "").strip() != _ETF_SHARE_UNIT:
            return None, "ETF真实份额必须显式声明单位为份"
        etf_share_base = _number(row.get("etf_share_base"))
        if etf_share_base is None or etf_share_base <= 0:
            return None, "ETF真实份额证据缺少正数基准份额"
        etf_methodology_id = str(row.get("etf_methodology_id") or "").strip()
        if not etf_methodology_id:
            return None, "ETF真实份额证据缺少etf_methodology_id"
        expected_pct = etf_share_net_change / etf_share_base * 100
        tolerance = max(0.01, abs(expected_pct) * 0.01)
        if (
            etf_share_change_pct < -100
            or abs(etf_share_change_pct - expected_pct) > tolerance
        ):
            return None, "ETF份额变化率与净变化及基准份额不一致"

    source = str(_envelope_value(row, envelope, "source") or "").strip()
    return {
        "board_name": board_name,
        "board_code": board_code,
        "trade_date": trade_date,
        "non_leveraged_net_inflow": round(net_inflow, 4),
        "non_leveraged_net_inflow_unit": net_inflow_unit,
        "methodology_id": methodology_id,
        "non_leveraged_flow_audited": True,
        "data_quality": quality,
        "source": source,
        "source_url": source_url,
        "published_at": published_at,
        "observed_at": observed_at,
        "new_high_count": new_high_count,
        "constituent_count": constituent_count,
        "new_high_window": 20 if new_high_count is not None else None,
        "etf_share_net_change": (
            round(etf_share_net_change, 4)
            if etf_share_net_change is not None else None
        ),
        "etf_share_change_pct": (
            round(etf_share_change_pct, 4)
            if etf_share_change_pct is not None else None
        ),
        "etf_id": etf_id,
        "etf_share_unit": _ETF_SHARE_UNIT if etf_fields_present else None,
        "etf_share_base": (
            round(etf_share_base, 4) if etf_share_base is not None else None
        ),
        "etf_methodology_id": etf_methodology_id,
        "etf_flow_audited": etf_share_net_change is not None,
    }, ""


def _unavailable(
    *,
    trade_date: str,
    configured: bool,
    note: str,
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "configured": configured,
        "source": "授权板块非杠杆资金适配器",
        "trade_date": trade_date,
        "updated_at": _shanghai_now().isoformat(),
        "items": [],
        "by_name": {},
        "by_code": {},
        "merge_map": {},
        "notes": [
            note,
            "未取得合格授权数据时保持空值，绝不使用东方财富订单流估算冒充非杠杆资金。",
        ],
    }


def fetch_sector_audited_flow(
    trade_date: str,
    *,
    configuration: SectorAuditedFlowConfiguration | None = None,
) -> dict[str, Any]:
    """Fetch a licensed/official non-leveraged sector-flow snapshot.

    The adapter is deliberately strict: one malformed or cross-date record
    invalidates the whole snapshot.  This prevents a partly stale response from
    being merged into the six-state model as if it were a same-day audited fact.
    """

    requested_trade_date = _valid_trade_date(trade_date)
    if requested_trade_date is None:
        return _unavailable(
            trade_date=str(trade_date or "")[:10],
            configured=False,
            note="请求交易日格式无效。",
        )

    config = configuration or SectorAuditedFlowConfiguration.from_environment()
    if config.configuration_error:
        return _unavailable(
            trade_date=requested_trade_date,
            configured=False,
            note=config.configuration_error,
        )
    if not config.endpoint or not config.bearer_token:
        return _unavailable(
            trade_date=requested_trade_date,
            configured=False,
            note="未配置HTTPS适配器地址或服务端授权令牌。",
        )
    if not _is_https_url(config.endpoint):
        return _unavailable(
            trade_date=requested_trade_date,
            configured=False,
            note="适配器地址必须使用HTTPS，且不得在URL中携带账号密码。",
        )

    try:
        response = requests.get(
            config.endpoint,
            params={"trade_date": requested_trade_date},
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {config.bearer_token}",
            },
            timeout=10,
            allow_redirects=False,
        )
        response.raise_for_status()
        status_code = int(getattr(response, "status_code", 200) or 200)
        if not 200 <= status_code < 300:
            raise ValueError("授权适配器拒绝请求或发生重定向")
        body = response.json()
    except Exception as exc:
        return _unavailable(
            trade_date=requested_trade_date,
            configured=True,
            note=f"授权适配器暂不可用：{exc.__class__.__name__}。",
        )

    rows, envelope = _payload_records(body)
    if not rows or len(rows) > 1000:
        return _unavailable(
            trade_date=requested_trade_date,
            configured=True,
            note="授权适配器未返回板块记录。",
        )

    items: list[dict[str, Any]] = []
    now = _shanghai_now()
    for row in rows:
        if not isinstance(row, Mapping):
            return _unavailable(
                trade_date=requested_trade_date,
                configured=True,
                note="授权适配器数据未通过审计契约：记录不是JSON对象。",
            )
        item, error = _normalize_item(
            row,
            envelope,
            requested_trade_date=requested_trade_date,
            now=now,
        )
        if item is None:
            return _unavailable(
                trade_date=requested_trade_date,
                configured=True,
                note=f"授权适配器数据未通过审计契约：{error}。",
            )
        items.append(item)

    by_name: dict[str, dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}
    for item in items:
        name = item["board_name"]
        code = item["board_code"]
        if (name and name in by_name) or (code and code in by_code):
            return _unavailable(
                trade_date=requested_trade_date,
                configured=True,
                note="授权适配器返回重复板块身份，拒绝不确定覆盖。",
            )
        if name:
            by_name[name] = item
        if code:
            by_code[code] = item

    merge_map = {**by_name, **by_code}
    return {
        "status": "ok",
        "configured": True,
        "source": "授权板块非杠杆资金适配器",
        "trade_date": requested_trade_date,
        "updated_at": now.isoformat(),
        "items": items,
        "by_name": by_name,
        "by_code": by_code,
        "merge_map": merge_map,
        "notes": [
            "仅合并明确标记为audited/official且与请求交易日一致的授权数据。",
            "该数据用于独立证据家族，不替代价格、订单流方向、板块结构或融资慢变量。",
        ],
    }


__all__ = [
    "SectorAuditedFlowConfiguration",
    "fetch_sector_audited_flow",
]
