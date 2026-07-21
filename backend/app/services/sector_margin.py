from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import math
import threading
from typing import Any

import requests

from app.services.cache import _get_response_cache, _set_response_cache
from app.services.trading_calendar import is_a_share_trading_day


_EASTMONEY_DATA_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_BOARD_TYPE_CODES = {"行业": "005", "概念": "006"}
_HISTORY_CACHE_LOCK = threading.Lock()
_HISTORY_CACHE: dict[tuple[str, str], tuple[dict[str, list[dict[str, Any]]], str | None]] = {}


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
    max_pages = 20
    # The interval report currently contains more than five 500-row pages.
    # Stopping at page five silently drops the tail of the board universe.
    while page <= max_pages:
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
        if page >= max_pages:
            raise ValueError(
                f"{report_name} 返回 {pages} 页，超过安全分页上限，拒绝截断后继续计算"
            )
        page += 1
    return rows


def _fetch_daily_history(
    board_codes: list[str],
    *,
    cutoff: date,
) -> list[dict[str, Any]]:
    """Fetch every page of exact daily financing observations for one cohort."""

    normalized = [str(code or "").strip() for code in board_codes if str(code or "").strip()]
    if not normalized:
        return []
    quoted = ",".join(f'"{code}"' for code in normalized)
    filter_text = (
        f"(BOARD_CODE in ({quoted}))"
        f"(TRADE_DATE>='{cutoff.isoformat()}')"
    )
    rows: list[dict[str, Any]] = []
    page = 1
    max_pages = 40
    while page <= max_pages:
        response = requests.get(
            _EASTMONEY_DATA_URL,
            params={
                "reportName": "RPTA_WEB_BKJYMX",
                "columns": (
                    "BOARD_CODE,BOARD_NAME,BOARD_TYPE_CODE,TRADE_DATE,"
                    "FIN_BALANCE,FIN_BALANCE_RATIO,FIN_BUY_AMT,"
                    "FIN_REPAY_AMT,FIN_NETBUY_AMT"
                ),
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "pageNumber": page,
                "pageSize": 500,
                "filter": filter_text,
                "source": "WEB",
                "client": "WEB",
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://data.eastmoney.com/rzrq/",
            },
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") or {}
        batch = [item for item in (result.get("data") or []) if isinstance(item, dict)]
        rows.extend(batch)
        pages = max(1, int(result.get("pages") or 1))
        if page >= pages or not batch:
            break
        if page >= max_pages:
            raise ValueError("板块融资历史分页超过安全上限，拒绝截断后计算斜率")
        page += 1
    return rows


def _linear_slope(values: list[float], window: int) -> float | None:
    """OLS slope of exact daily values, in value units per trading day."""

    if len(values) < window or window < 2:
        return None
    selected = values[-window:]
    if not all(math.isfinite(value) for value in selected):
        return None
    mean_x = (window - 1) / 2
    mean_y = sum(selected) / window
    denominator = sum((index - mean_x) ** 2 for index in range(window))
    if denominator <= 0:
        return None
    numerator = sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(selected)
    )
    return round(numerator / denominator, 4)


def _historical_percentile(values: list[float], window: int) -> float | None:
    """Empirical percentile of the latest value in an exact trading-day window."""

    if len(values) < window:
        return None
    selected = values[-window:]
    if not all(math.isfinite(value) for value in selected):
        return None
    latest = selected[-1]
    return round(sum(value <= latest for value in selected) / window * 100, 2)


def _history_candidate_codes(rows: list[dict[str, Any]], board_code: str) -> list[str]:
    """Return the complete disclosed board universe for historical metrics.

    A cross-sectional top-N shortcut would leave most boards without their own
    60/120-session percentile and could silently label an unqueried board as
    low risk.  The expensive history response is cached by disclosure date so
    the full-universe read happens at most once per process and trading day.
    """

    eligible = [
        row for row in rows
        if str(row.get("BOARD_TYPE_CODE") or "") == board_code
        and str(row.get("BOARD_CODE") or "").strip()
    ]
    return list(dict.fromkeys(
        str(row.get("BOARD_CODE") or "").strip()
        for row in eligible
        if str(row.get("BOARD_CODE") or "").strip()
    ))


def _load_candidate_history(
    rows: list[dict[str, Any]],
    *,
    board_code: str,
) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    candidates = _history_candidate_codes(rows, board_code)
    if not candidates:
        return {}, None
    disclosure_dates = sorted({
        str(row.get("TRADE_DATE") or "")[:10]
        for row in rows
        if str(row.get("BOARD_TYPE_CODE") or "") == board_code
        and str(row.get("TRADE_DATE") or "")[:10]
    })
    disclosure_date = disclosure_dates[-1] if disclosure_dates else date.today().isoformat()
    cache_key = (board_code, disclosure_date)
    with _HISTORY_CACHE_LOCK:
        cached = _HISTORY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        cutoff_anchor = date.fromisoformat(disclosure_date)
    except ValueError:
        cutoff_anchor = date.today()
    cutoff = cutoff_anchor - timedelta(days=220)
    fetched: list[dict[str, Any]] = []
    batches = [
        candidates[index:index + 8]
        for index in range(0, len(candidates), 8)
    ]
    try:
        # Deep history is the slow T+1 family.  Bounded concurrency avoids the
        # former minutes-long three-board serial loop without flooding the
        # provider or weakening completeness checks.
        with ThreadPoolExecutor(max_workers=min(6, len(batches))) as executor:
            futures = {
                executor.submit(_fetch_daily_history, batch, cutoff=cutoff): batch
                for batch in batches
            }
            for future in as_completed(futures):
                fetched.extend(future.result())
    except Exception as exc:
        with _HISTORY_CACHE_LOCK:
            stale_candidates = [
                (key, value)
                for key, value in _HISTORY_CACHE.items()
                if key[0] == board_code and key != cache_key and value[0]
            ]
        if stale_candidates:
            stale_key, stale_value = max(stale_candidates, key=lambda item: item[0][1])
            return stale_value[0], f"{exc.__class__.__name__}（回退披露日{stale_key[1]}缓存）"
        return {}, exc.__class__.__name__
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in fetched:
        code = str(row.get("BOARD_CODE") or "").strip()
        trade_date = str(row.get("TRADE_DATE") or "")[:10]
        if not code or not trade_date:
            continue
        grouped.setdefault(code, []).append(row)
    for history in grouped.values():
        history.sort(key=lambda row: str(row.get("TRADE_DATE") or "")[:10])
    result = (grouped, None)
    with _HISTORY_CACHE_LOCK:
        # Bound the cache to the current and immediately previous disclosure
        # day for both board types.
        _HISTORY_CACHE[cache_key] = result
        if len(_HISTORY_CACHE) > 4:
            for key in sorted(_HISTORY_CACHE)[:-4]:
                _HISTORY_CACHE.pop(key, None)
    return result


def _has_complete_trading_sequence(rows: list[dict[str, Any]]) -> bool:
    raw_dates = [str(row.get("TRADE_DATE") or "")[:10] for row in rows]
    if not raw_dates or any(not value for value in raw_dates):
        return False
    try:
        parsed_dates = [date.fromisoformat(value) for value in raw_dates]
    except ValueError:
        return False
    if len(set(parsed_dates)) != len(parsed_dates):
        return False
    observed = set(parsed_dates)
    cursor = min(parsed_dates)
    end = max(parsed_dates)
    while cursor <= end:
        if is_a_share_trading_day(cursor) and cursor not in observed:
            return False
        cursor += timedelta(days=1)
    return True


def _history_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: str(row.get("TRADE_DATE") or "")[:10])
    net_values = [
        _optional_float(row.get("FIN_NETBUY_AMT"))
        for row in ordered
    ]
    balance_ratios = [
        _optional_float(row.get("FIN_BALANCE_RATIO"))
        for row in ordered
    ]

    def exact_window_values(
        raw_values: list[float | None],
        window: int,
        *,
        scale: float = 1.0,
    ) -> list[float] | None:
        if len(ordered) < window or len(raw_values) != len(ordered):
            return None
        selected_rows = ordered[-window:]
        selected_values = raw_values[-window:]
        if not _has_complete_trading_sequence(selected_rows):
            return None
        if any(value is None or not math.isfinite(value) for value in selected_values):
            return None
        return [float(value) / scale for value in selected_values if value is not None]

    net_5 = exact_window_values(net_values, 5, scale=1e8)
    net_10 = exact_window_values(net_values, 10, scale=1e8)
    net_20 = exact_window_values(net_values, 20, scale=1e8)
    ratio_60 = exact_window_values(balance_ratios, 60)
    ratio_120 = exact_window_values(balance_ratios, 120)
    metric_windows = {
        "5日斜率": net_5 is not None,
        "10日斜率": net_10 is not None,
        "20日斜率": net_20 is not None,
        "60日分位": ratio_60 is not None,
        "120日分位": ratio_120 is not None,
    }
    missing_windows = [label for label, available in metric_windows.items() if not available]
    complete_sequence = ratio_120 is not None and net_20 is not None
    sample_count = min(
        sum(value is not None for value in net_values),
        sum(value is not None for value in balance_ratios),
    )
    return {
        "financing_net_buy_slope_5d": _linear_slope(net_5, 5) if net_5 is not None else None,
        "financing_net_buy_slope_10d": _linear_slope(net_10, 10) if net_10 is not None else None,
        "financing_net_buy_slope_20d": _linear_slope(net_20, 20) if net_20 is not None else None,
        "financing_balance_ratio_percentile_60d": _historical_percentile(ratio_60, 60) if ratio_60 is not None else None,
        "financing_balance_ratio_percentile_120d": _historical_percentile(ratio_120, 120) if ratio_120 is not None else None,
        "margin_history_sample_count": sample_count,
        "margin_history_degraded": bool(missing_windows),
        "margin_history_sequence_complete": complete_sequence,
        "margin_history_method": (
            (
                "东方财富逐日融资净买入OLS斜率（亿元/交易日）；"
                "融资余额占比为含当日样本的经验历史分位"
            )
            if not missing_windows
            else (
                "各指标按自身最新交易日窗口独立校验；不可用窗口保持空值："
                + "、".join(missing_windows)
            )
        ),
    }


def fetch_sector_margin(board_type: str = "行业", force_refresh: bool = False) -> dict[str, Any]:
    """Return T+1 sector financing crowding inputs keyed by board name.

    Financing disclosure is a slow variable.  The return shape explicitly says
    ``realtime=False`` so callers cannot accidentally present it as an intraday
    flow signal.
    """

    normalized = "概念" if board_type == "概念" else "行业"
    cache_key = f"sector-margin|{normalized}"
    last_good = _get_response_cache(cache_key, allow_stale=True)
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
        if isinstance(last_good, dict) and last_good.get("items"):
            fallback = dict(last_good)
            fallback["source"] = f"{fallback.get('source') or '板块融资'}（上次成功缓存）"
            fallback["notes"] = list(dict.fromkeys([
                *list(fallback.get("notes") or []),
                f"本轮板块融资采集失败：{exc.__class__.__name__}；继续显示上次成功快照，"
                "不把旧披露日冒充实时数据。",
            ]))
            return fallback
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

    history_by_code, history_error = _load_candidate_history(
        daily_rows,
        board_code=code,
    )
    if history_error:
        notes.append(
            f"全板块逐日融资历史暂不可用：{history_error}；"
            "5/10/20日斜率和60/120日历史分位保持空值。"
        )
    elif history_by_code:
        notes.append(
            "5/10/20日融资斜率来自逐日融资净买入OLS；"
            "60/120日拥挤度来自融资余额占比的自身历史分位。"
        )
        notes.append(
            "深历史按当日披露的完整行业/概念板块集合采集，并按披露日缓存；"
            "单板块历史缺失或交易日断档时保持空值，不把横截面排名冒充自身历史分位。"
        )

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
        history = history_by_code.get(str(row.get("BOARD_CODE") or "").strip(), [])
        history_metrics = _history_metrics(history) if history else {
            "financing_net_buy_slope_5d": None,
            "financing_net_buy_slope_10d": None,
            "financing_net_buy_slope_20d": None,
            "financing_balance_ratio_percentile_60d": None,
            "financing_balance_ratio_percentile_120d": None,
            "margin_history_sample_count": 0,
            "margin_history_degraded": True,
            "margin_history_sequence_complete": False,
            "margin_history_method": "未取得完整逐日融资历史；不输出伪历史分位，也不输出伪斜率",
        }
        if history_error:
            history_metrics["margin_history_degraded"] = True
            history_metrics["margin_history_method"] = (
                f"{history_metrics.get('margin_history_method') or ''}；"
                f"本轮深历史采集降级：{history_error}"
            ).strip("；")
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
            **history_metrics,
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
