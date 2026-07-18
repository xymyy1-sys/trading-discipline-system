from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.services.cache import _get_response_cache, _set_response_cache


_EASTMONEY_DATA_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_BOARD_TYPE_CODES = {"行业": "005", "概念": "006"}


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_report(report_name: str, page_size: int = 500) -> list[dict[str, Any]]:
    """Read Eastmoney's public financing board report without inventing gaps."""

    rows: list[dict[str, Any]] = []
    page = 1
    while page <= 5:
        response = requests.get(
            _EASTMONEY_DATA_URL,
            params={
                "reportName": report_name,
                "columns": "ALL",
                "sortColumns": "FIN_NETBUY_AMT",
                "sortTypes": "-1",
                "pageNumber": page,
                "pageSize": page_size,
                "source": "WEB",
                "client": "WEB",
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://data.eastmoney.com/rzrq/",
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") or {}
        batch = [item for item in (result.get("data") or []) if isinstance(item, dict)]
        rows.extend(batch)
        pages = int(result.get("pages") or 1)
        if page >= pages or not batch:
            break
        page += 1
    return rows


def fetch_sector_margin(board_type: str = "行业", force_refresh: bool = False) -> dict[str, Any]:
    """Return T+1 sector financing crowding inputs keyed by board name.

    Financing disclosure is a slow variable.  The return shape explicitly says
    ``realtime=False`` so callers cannot accidentally present it as an intraday
    flow signal.
    """

    normalized = "概念" if board_type == "概念" else "行业"
    cache_key = f"sector-margin|{normalized}"
    if not force_refresh:
        cached = _get_response_cache(cache_key)
        if cached is not None:
            return cached

    code = _BOARD_TYPE_CODES[normalized]
    notes = [
        "融资融券按交易所及东方财富公开日终口径披露，为T+1慢变量，不代表盘中实时融资变化。",
    ]
    try:
        daily_rows = _fetch_report("RPTA_WEB_BKJYMXN")
        interval_rows = _fetch_report("RPTA_WEB_BKQJYMXN")
    except Exception as exc:
        result = {
            "source": "东方财富融资融券（暂不可用）",
            "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
            "as_of": "",
            "realtime": False,
            "items": {},
            "notes": notes + [f"板块融资接口暂不可用：{exc.__class__.__name__}"],
        }
        _set_response_cache(cache_key, result)
        return result

    intervals: dict[str, dict[str, float]] = {}
    for row in interval_rows:
        if str(row.get("BOARD_TYPE_CODE") or "") != code:
            continue
        name = str(row.get("BOARD_NAME") or "").strip()
        period = str(row.get("INTERVAL_TYPE") or "").strip()
        if not name or not period:
            continue
        bucket = intervals.setdefault(name, {})
        bucket[f"net_buy_{period}"] = round(_safe_float(row.get("FIN_NETBUY_AMT")) / 1e8, 2)
        bucket[f"buy_{period}"] = round(_safe_float(row.get("FIN_BUY_AMT")) / 1e8, 2)

    items: dict[str, dict[str, Any]] = {}
    dates: list[str] = []
    for row in daily_rows:
        if str(row.get("BOARD_TYPE_CODE") or "") != code:
            continue
        name = str(row.get("BOARD_NAME") or "").strip()
        if not name:
            continue
        trade_date = str(row.get("TRADE_DATE") or "")[:10]
        if trade_date:
            dates.append(trade_date)
        values = intervals.get(name, {})
        items[name] = {
            "board_code": str(row.get("BOARD_CODE") or ""),
            "as_of": trade_date,
            "financing_balance": round(_safe_float(row.get("FIN_BALANCE")) / 1e8, 2),
            "financing_buy": round(_safe_float(row.get("FIN_BUY_AMT")) / 1e8, 2),
            "financing_repay": round(_safe_float(row.get("FIN_REPAY_AMT")) / 1e8, 2),
            "financing_net_buy": round(_safe_float(row.get("FIN_NETBUY_AMT")) / 1e8, 2),
            "financing_balance_ratio": round(_safe_float(row.get("FIN_BALANCE_RATIO")), 3),
            "net_buy_5d": _optional_float(values.get("net_buy_5日")),
            "net_buy_10d": _optional_float(values.get("net_buy_10日")),
            "net_buy_20d": _optional_float(values.get("net_buy_20日")),
            "realtime": False,
        }

    result = {
        "source": "东方财富融资融券板块榜",
        "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
        "as_of": max(dates, default=""),
        "realtime": False,
        "items": items,
        "notes": notes if items else notes + ["本次未取得可验证的板块融资数据。"],
    }
    _set_response_cache(cache_key, result)
    return result
