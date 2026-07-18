from __future__ import annotations

import json
import math
import statistics
import threading
import time as clock
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_from_timestamp, shanghai_now_naive
from app.models.trading import MarketRegimeSnapshot
from app.schemas.trading import (
    MarketIndexStateOut,
    MarketRegimeClassificationOut,
    MarketRegimeMetrics,
    MarketRegimeOut,
    MarketSectorEvidenceOut,
)
from app.services.market_data import MarketDataProvider


EASTMONEY_HOSTS = (
    "https://push2.eastmoney.com",
    "https://push2ex.eastmoney.com",
    "https://push2delay.eastmoney.com",
)
INDEX_DEFINITIONS = {
    "000001": ("上证指数", "1.000001"),
    "399001": ("深证成指", "0.399001"),
    "399006": ("创业板指", "0.399006"),
    "000688": ("科创50", "1.000688"),
}
REGIME_CACHE_SECONDS = 120
_CACHE_LOCK = threading.Lock()
_REGIME_CACHE: tuple[float, MarketRegimeOut] | None = None


@dataclass
class MarketRegimeCollection:
    metrics: MarketRegimeMetrics
    trade_date: str
    captured_at: datetime
    source: str
    indices: list[MarketIndexStateOut] = field(default_factory=list)
    strongest_sectors: list[MarketSectorEvidenceOut] = field(default_factory=list)
    weakest_sectors: list[MarketSectorEvidenceOut] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _optional_float(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: Any) -> int | None:
    number = _optional_float(value)
    return int(number) if number is not None else None


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _json_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _market_progress(now: datetime) -> float | None:
    """Elapsed A-share continuous-auction fraction; no pre-open projection."""
    if now.weekday() >= 5:
        return 1.0
    minute = now.hour * 60 + now.minute
    if minute < 9 * 60 + 35:
        return None
    if minute <= 11 * 60 + 30:
        return min(0.5, max(0.0, (minute - (9 * 60 + 30)) / 240))
    if minute < 13 * 60:
        return 0.5
    if minute <= 15 * 60:
        return min(1.0, 0.5 + (minute - 13 * 60) / 240)
    return 1.0


def _daily_limit_pct(code: str, name: str) -> float:
    upper_name = str(name or "").upper()
    if "ST" in upper_name:
        return 5.0
    if str(code).startswith(("300", "301", "688", "689")):
        return 20.0
    if str(code).startswith(("4", "8", "92")):
        return 30.0
    return 10.0


def summarize_all_a_rows(
    rows: list[dict[str, Any]],
    *,
    expected_total: int | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Aggregate real full-market quote rows without filling absent values."""
    now = shanghai_now_naive(now)
    active: list[tuple[dict[str, Any], float]] = []
    source_timestamps: list[int] = []
    for row in rows:
        price = _optional_float(row.get("f2"))
        change = _optional_float(row.get("f3"))
        if price is None or price <= 0 or change is None:
            continue
        active.append((row, change))
        stamp = _optional_int(row.get("f124"))
        if stamp and stamp > 0:
            source_timestamps.append(stamp)

    notes: list[str] = []
    if expected_total and len(rows) < expected_total:
        notes.append(f"全A行情仅返回{len(rows)}/{expected_total}条，广度和订单流方向统计标记为缺口。")
        if len(rows) / max(1, expected_total) < 0.90:
            return {}, notes + ["全A覆盖率不足90%，拒绝用涨幅排序的局部榜单推断全市场。"]
    if not active:
        return {}, notes + ["全A行情未返回可用交易股票，不生成市场广度。"]

    changes = [change for _, change in active]
    up_count = sum(change > 0.01 for change in changes)
    down_count = sum(change < -0.01 for change in changes)
    flat_count = len(changes) - up_count - down_count
    up_5pct_count = sum(change >= 5 for change in changes)
    down_5pct_count = sum(change <= -5 for change in changes)
    limit_up_count = 0
    limit_down_count = 0
    for row, change in active:
        limit_pct = _daily_limit_pct(str(row.get("f12") or ""), str(row.get("f14") or ""))
        tolerance = 0.25 if limit_pct <= 10 else 0.45
        limit_up_count += int(change >= limit_pct - tolerance)
        limit_down_count += int(change <= -limit_pct + tolerance)

    amount_values = [
        value for row, _ in active
        if (value := _optional_float(row.get("f6"))) is not None
    ]
    main_flow_values = [
        value for row, _ in active
        if (value := _optional_float(row.get("f62"))) is not None
    ]
    minimum_coverage = max(1, int(len(active) * 0.9))
    turnover_yi = round(sum(amount_values) / 1e8, 2) if len(amount_values) >= minimum_coverage else None
    main_net_yi = round(sum(main_flow_values) / 1e8, 2) if len(main_flow_values) >= minimum_coverage else None
    if turnover_yi is None:
        notes.append(f"成交额字段覆盖{len(amount_values)}/{len(active)}，不足90%，不输出市场成交额。")
    if main_net_yi is None:
        notes.append(f"供应商大单方向字段覆盖{len(main_flow_values)}/{len(active)}，不足90%，不输出全市场大单方向估算。")

    source_time = shanghai_from_timestamp(max(source_timestamps)) if source_timestamps else None
    # Before the next session opens, quote endpoints still expose the previous
    # completed trading day. Treat that dated snapshot as a full session rather
    # than suppressing the volume ratio merely because wall-clock time is early.
    progress = (
        1.0
        if source_time is not None and source_time.date() < now.date()
        else _market_progress(now)
    )
    projected = round(turnover_yi / progress, 2) if turnover_yi is not None and progress and progress >= 0.02 else None
    if turnover_yi is not None and progress is None:
        notes.append("09:35前不线性外推全天成交额，等待连续竞价形成有效进度。")
    elif projected is not None and progress < 1:
        notes.append(f"预计全天成交额按真实累计成交额/交易进度({progress:.1%})计算，属于显式估算值。")

    return {
        "active_stock_count": len(active),
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "up_5pct_count": up_5pct_count,
        "down_5pct_count": down_5pct_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "median_change_pct": round(statistics.median(changes), 3),
        "advance_ratio": round(up_count / max(1, up_count + down_count), 4),
        "turnover_yi": turnover_yi,
        "projected_turnover_yi": projected,
        "market_main_net_inflow_yi": main_net_yi,
        "source_time": source_time,
    }, notes + ["涨跌停家数先按证券名称、代码所属板块及实际涨跌幅推断，仅作为涨跌停池接口失败时的备用值。"]


def _get_json_from_hosts(path: str, params: dict[str, str], timeout: int = 8) -> tuple[dict[str, Any], str]:
    last_error: Exception | None = None
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json,text/plain,*/*",
    }
    for host in EASTMONEY_HOSTS:
        try:
            response = requests.get(f"{host}{path}", params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if payload.get("data") is not None:
                return payload, host
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise ValueError("empty Eastmoney response")


def _fetch_limit_pool_count(path: str, trade_date: str) -> int:
    """Read the dated Eastmoney limit pool total; never infer an empty pool as zero."""
    response = requests.get(
        f"https://push2ex.eastmoney.com/{path}",
        params={
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": "0",
            "pagesize": "500",
            "sort": "fbt:asc",
            "date": trade_date,
        },
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{path} returned no dated pool")
    query_date = str(data.get("qdate") or "")
    if query_date and query_date != trade_date:
        raise ValueError(f"{path} returned mismatched trade date {query_date}")
    count = _optional_int(data.get("tc"))
    if count is None or count < 0:
        raise ValueError(f"{path} returned no pool total")
    return count


def _fetch_all_a_market(now: datetime) -> tuple[dict[str, Any], str, list[str]]:
    params = {
        "pn": "1",
        # Eastmoney currently caps this endpoint near 100 rows even when a
        # larger page size is requested.  Keep the requested and actual page
        # size aligned so a rising-sort first page can never masquerade as the
        # whole market.
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f12,f13,f14,f2,f3,f6,f62,f15,f16,f17,f18,f124",
    }
    payload, host = _get_json_from_hosts("/api/qt/clist/get", params)
    data = payload.get("data") or {}
    first_page = list(data.get("diff") or [])
    total = _optional_int(data.get("total")) or len(first_page)
    actual_page_size = len(first_page)
    rows = list(first_page)
    if total > len(rows) and actual_page_size:
        page_count = math.ceil(total / actual_page_size)

        def _fetch_page(page: int) -> list[dict[str, Any]]:
            page_payload, _ = _get_json_from_hosts(
                "/api/qt/clist/get",
                {**params, "pn": str(page), "pz": str(actual_page_size)},
            )
            return list((page_payload.get("data") or {}).get("diff") or [])

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_fetch_page, page): page
                for page in range(2, page_count + 1)
            }
            pages: dict[int, list[dict[str, Any]]] = {}
            for future in as_completed(futures):
                page = futures[future]
                try:
                    pages[page] = future.result()
                except Exception:
                    pages[page] = []
            for page in range(2, page_count + 1):
                rows.extend(pages.get(page, []))

    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("f13") or ""), str(row.get("f12") or ""))
        if key[1]:
            deduplicated[key] = row
    rows = list(deduplicated.values())
    summary, notes = summarize_all_a_rows(rows, expected_total=total, now=now)
    source = f"eastmoney-all-a@{host.split('//')[-1]}"
    source_time = summary.get("source_time")
    trade_date = (
        source_time.strftime("%Y%m%d")
        if isinstance(source_time, datetime)
        else now.strftime("%Y%m%d")
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            up_future = executor.submit(_fetch_limit_pool_count, "getTopicZTPool", trade_date)
            down_future = executor.submit(_fetch_limit_pool_count, "getTopicDTPool", trade_date)
            summary["limit_up_count"] = up_future.result()
            summary["limit_down_count"] = down_future.result()
        source += "+eastmoney-dated-limit-pools"
        notes.append(
            f"涨停/跌停家数取东方财富{trade_date}日期池总数；全A涨幅推断仅作接口失败备用。"
        )
    except Exception as exc:
        notes.append(
            f"日期涨跌停池采集失败（{exc.__class__.__name__}），暂使用全A真实涨幅规则推断值并明确降级。"
        )
    return summary, source, notes


def _fetch_index_intraday_vwap(secid: str) -> tuple[float | None, int]:
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "ndays": "1",
        "iscr": "0",
        "secid": secid,
    }
    payload, _ = _get_json_from_hosts("/api/qt/stock/trends2/get", params, timeout=6)
    trends = list((payload.get("data") or {}).get("trends") or [])
    valid: list[float] = []
    for row in trends:
        parts = str(row).split(",")
        if len(parts) < 8:
            continue
        average = _optional_float(parts[7])
        if average and average > 0:
            valid.append(average)
    return (valid[-1] if valid else None), len(valid)


def _fetch_indices() -> tuple[list[MarketIndexStateOut], str, list[str]]:
    secids = ",".join(secid for _, secid in INDEX_DEFINITIONS.values())
    payload, host = _get_json_from_hosts(
        "/api/qt/ulist.np/get",
        {
            "fltt": "2",
            "invt": "2",
            "fields": "f12,f14,f2,f3,f6,f15,f16,f17,f18,f124",
            "secids": secids,
        },
    )
    rows = list((payload.get("data") or {}).get("diff") or [])
    by_code = {str(row.get("f12") or "").zfill(6): row for row in rows}
    vwap_by_code: dict[str, tuple[float | None, int]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_fetch_index_intraday_vwap, secid): code
            for code, (_, secid) in INDEX_DEFINITIONS.items()
        }
        for future in as_completed(futures):
            try:
                vwap_by_code[futures[future]] = future.result()
            except Exception:
                vwap_by_code[futures[future]] = (None, 0)

    notes: list[str] = []
    result: list[MarketIndexStateOut] = []
    for code, (name, _) in INDEX_DEFINITIONS.items():
        row = by_code.get(code)
        if row is None:
            result.append(MarketIndexStateOut(code=code, name=name, source="eastmoney", data_quality="missing"))
            notes.append(f"{name}行情缺失。")
            continue
        current = _optional_float(row.get("f2"))
        high = _optional_float(row.get("f15"))
        low = _optional_float(row.get("f16"))
        vwap, point_count = vwap_by_code.get(code, (None, 0))
        result.append(MarketIndexStateOut(
            code=code,
            name=name,
            current=current,
            change_pct=_optional_float(row.get("f3")),
            amount_yi=(round(value / 1e8, 2) if (value := _optional_float(row.get("f6"))) is not None else None),
            open_price=_optional_float(row.get("f17")),
            high_price=high,
            low_price=low,
            prev_close=_optional_float(row.get("f18")),
            intraday_vwap=vwap,
            above_vwap=(current >= vwap) if current is not None and vwap is not None else None,
            high_drawdown_pct=round((high - current) / high * 100, 3) if high and current is not None else None,
            low_rebound_pct=round((current - low) / low * 100, 3) if low and current is not None else None,
            data_quality="realtime" if point_count >= 2 else "partial",
            source="eastmoney-index",
        ))
    return result, f"eastmoney-index@{host.split('//')[-1]}", notes


def _fetch_index_daily_amount(secid: str) -> dict[str, float]:
    params = {
        "secid": secid,
        "klt": "101",
        "fqt": "0",
        "lmt": "12",
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json,text/plain,*/*",
    }
    rows: list[str] = []
    last_error: Exception | None = None
    for host in ("https://push2his.eastmoney.com", "https://push2delay.eastmoney.com"):
        try:
            response = requests.get(
                f"{host}/api/qt/stock/kline/get",
                params=params,
                headers=headers,
                timeout=8,
            )
            response.raise_for_status()
            rows = list((response.json().get("data") or {}).get("klines") or [])
            if rows:
                break
        except Exception as exc:
            last_error = exc
    if not rows and last_error:
        raise last_error
    result: dict[str, float] = {}
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 7:
            continue
        amount = _optional_float(parts[6])
        if amount is not None and amount >= 0:
            result[parts[0]] = amount / 1e8
    return result


def _fetch_sse_stock_turnover_yi(trade_date: str) -> float:
    """Read the SSE official daily *stock* turnover total, whose unit is 亿元."""
    compact_date = trade_date.replace("-", "")
    response = requests.get(
        "https://query.sse.com.cn/commonQuery.do",
        params={
            "sqlId": "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C",
            "PRODUCT_CODE": "01,02,03,11,17",
            "type": "inParams",
            "SEARCH_DATE": trade_date,
        },
        headers={
            "Referer": "https://www.sse.com.cn/",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=10,
    )
    response.raise_for_status()
    rows = list(response.json().get("result") or [])
    stock_total = next(
        (
            row for row in rows
            if str(row.get("PRODUCT_CODE") or "") == "17"
            and str(row.get("TRADE_DATE") or "") == compact_date
        ),
        None,
    )
    amount_yi = _optional_float((stock_total or {}).get("TRADE_AMT"))
    if amount_yi is None or amount_yi <= 0:
        raise ValueError(f"SSE official stock turnover is absent for {trade_date}")
    return amount_yi


def _fetch_szse_stock_turnover_yi(trade_date: str) -> float:
    """Read the SZSE official daily *stock* turnover total and normalise 元 to 亿元."""
    # Keep the parser local to the official fallback.  pandas/openpyxl are
    # already backend dependencies, while importing them for every market
    # regime request would needlessly add startup cost.
    import warnings

    import pandas as pd

    response = requests.get(
        "https://www.szse.cn/api/report/ShowReport",
        params={
            "SHOWTYPE": "xlsx",
            "CATALOGID": "1803_sczm",
            "TABKEY": "tab1",
            "txtQueryDate": trade_date,
            "random": "0.39339437497296137",
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()
    content_type = str(response.headers.get("content-type") or "").lower()
    if "spreadsheet" not in content_type and "excel" not in content_type:
        raise ValueError(f"SZSE official response is not a workbook for {trade_date}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        frame = pd.read_excel(BytesIO(response.content), engine="openpyxl")
    category_column = next(
        (column for column in frame.columns if str(column).strip().startswith("证券类别")),
        None,
    )
    amount_column = next(
        (column for column in frame.columns if str(column).strip().startswith("成交金额")),
        None,
    )
    if category_column is None or amount_column is None:
        raise ValueError(f"SZSE official workbook schema changed for {trade_date}")
    stock_rows = frame[
        frame[category_column].astype(str).str.strip().eq("股票")
    ]
    if stock_rows.empty:
        raise ValueError(f"SZSE official stock turnover is absent for {trade_date}")
    raw_amount = str(stock_rows.iloc[0][amount_column]).replace(",", "").strip()
    amount_yuan = _optional_float(raw_amount)
    if amount_yuan is None or amount_yuan <= 0:
        raise ValueError(f"SZSE official stock turnover is invalid for {trade_date}")
    return amount_yuan / 1e8


def _fetch_official_turnover_day(trade_date: str) -> float:
    """Return one paired SSE+SZSE official stock turnover total in 亿元."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        sse_future = executor.submit(_fetch_sse_stock_turnover_yi, trade_date)
        szse_future = executor.submit(_fetch_szse_stock_turnover_yi, trade_date)
        sse_yi = sse_future.result()
        szse_yi = szse_future.result()
    return round(sse_yi + szse_yi, 2)


def _fetch_official_turnover_history(trade_date: str) -> dict[str, float]:
    """Collect five prior paired exchange days; incomplete days are never estimated."""
    current = datetime.strptime(trade_date, "%Y-%m-%d")
    candidates = [
        (current - timedelta(days=offset)).date().isoformat()
        for offset in range(1, 22)
        if (current - timedelta(days=offset)).weekday() < 5
    ]
    totals: dict[str, float] = {}
    # Query recent dates in small batches and stop as soon as five complete
    # paired days exist.  This bounds normal fallback to 6-8 dates rather than
    # requesting the whole 21-day holiday safety window every time.
    for start in range(0, len(candidates), 4):
        batch = candidates[start:start + 4]
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(_fetch_official_turnover_day, date): date
                for date in batch
            }
            for future in as_completed(futures):
                try:
                    total = future.result()
                except Exception:
                    continue
                if total > 0:
                    totals[futures[future]] = total
        if len(totals) >= 5:
            break
    return dict(sorted(totals.items())[-5:])


def _summarize_turnover_series(
    totals: list[tuple[str, float]],
    *,
    source: str,
    note: str,
) -> tuple[float | None, float | None, str, list[str]]:
    if not totals:
        return None, None, "unavailable", ["未取得早于当前交易日的两市成交额。"]
    previous = round(totals[-1][1], 2)
    if len(totals) < 5:
        return previous, None, source, [
            f"仅取得{len(totals)}个上深两市完整交易日，前日成交额可用，但拒绝生成5日均值。"
        ]
    last5 = [value for _, value in totals[-5:]]
    avg5 = round(sum(last5) / 5, 2)
    return previous, avg5, source, [note]


def _fetch_turnover_history(trade_date: str) -> tuple[float | None, float | None, str, list[str]]:
    eastmoney_error: Exception | None = None
    try:
        series: list[dict[str, float]] = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(_fetch_index_daily_amount, "1.000001"),
                executor.submit(_fetch_index_daily_amount, "0.399001"),
            ]
            for future in futures:
                series.append(future.result())
        dates = sorted(set(series[0]) & set(series[1])) if len(series) == 2 else []
        totals = [(date, series[0][date] + series[1][date]) for date in dates if date < trade_date]
        if len(totals) >= 5:
            return _summarize_turnover_series(
                totals,
                source="eastmoney-index-daily-amount",
                note="前日/5日均成交额取上证与深证综合指数日K真实成交额之和。",
            )
    except Exception as exc:
        eastmoney_error = exc

    official_series = _fetch_official_turnover_history(trade_date)
    official_result = _summarize_turnover_series(
        sorted(official_series.items()),
        source="sse-szse-official-stock-turnover",
        note="东方财富指数日K不可用，已回退上交所、深交所官方每日股票成交额；仅使用两市同日完整数据并统一为亿元。",
    )
    if official_result[0] is not None:
        return official_result
    failure = eastmoney_error.__class__.__name__ if eastmoney_error else "insufficient-history"
    return None, None, "unavailable", [
        f"东方财富历史成交额不可用（{failure}），上交所/深交所官方回退也未取得完整配对交易日；量能字段保持为空。"
    ]


def _fetch_sector_evidence(force_refresh: bool) -> tuple[dict[str, Any], str, list[str]]:
    provider = MarketDataProvider()
    flow = provider.sector_flow(
        flow_type="行业资金流",
        period="今日",
        force_refresh=force_refresh,
    )
    visible_positive = list(flow.inflow)
    visible_negative = list(flow.outflow)
    notes: list[str] = []
    try:
        # sector_flow intentionally exposes only the top 20 rows on each side.
        # Fetch the complete Eastmoney industry universe separately so breadth and
        # concentration are never calculated from that truncated display list.
        raw_rows = provider._fetch_direct_eastmoney_sector_flow_raw("行业资金流", "今日")
    except Exception as exc:
        raw_rows = []
        notes.append(
            f"全量行业订单流方向采集失败：{exc.__class__.__name__}；行业正负比例和集中度保持为空。"
        )

    directional_rows = [
        row for row in raw_rows
        if str(row.get("name") or "").strip()
        and _optional_float(row.get("net_inflow")) is not None
        and abs(float(row.get("net_inflow") or 0)) > 1e-9
    ]
    raw_positive = sorted(
        [row for row in directional_rows if float(row.get("net_inflow") or 0) > 0],
        key=lambda row: float(row.get("net_inflow") or 0),
        reverse=True,
    )
    raw_negative = sorted(
        [row for row in directional_rows if float(row.get("net_inflow") or 0) < 0],
        key=lambda row: float(row.get("net_inflow") or 0),
    )
    positive_total = sum(float(row.get("net_inflow") or 0) for row in raw_positive)
    top3_total = sum(float(row.get("net_inflow") or 0) for row in raw_positive[:3])
    visible_by_name = {
        item.name: item for item in visible_positive + visible_negative
    }

    def _sector_output(row: dict[str, Any], rank: int) -> MarketSectorEvidenceOut:
        visible = visible_by_name.get(str(row.get("name") or ""))
        return MarketSectorEvidenceOut(
            name=str(row.get("name") or "未知行业"),
            change_pct=float(row.get("change_pct") or 0),
            net_inflow=float(row.get("net_inflow") or 0),
            main_inflow=float(row.get("main_inflow") or 0),
            rank=rank,
            above_vwap=(
                not visible.sector_below_vwap
                if visible is not None and visible.sector_below_vwap is not None
                else None
            ),
        )

    vwap_items = [
        item for item in visible_positive + visible_negative
        if item.sector_below_vwap is not None
    ]
    strongest = [
        _sector_output(row, rank)
        for rank, row in enumerate(raw_positive[:5], start=1)
    ]
    weakest = [
        _sector_output(row, rank)
        for rank, row in enumerate(raw_negative[:5], start=1)
    ]
    if not strongest and not weakest and (visible_positive or visible_negative):
        notes.append("全量行业列表不可用；仅保留榜单证据，不据此计算行业扩散比例。")
        strongest = [
            MarketSectorEvidenceOut(
                name=item.name,
                change_pct=item.change_pct,
                net_inflow=item.net_inflow,
                main_inflow=item.main_inflow,
                rank=item.rank,
                above_vwap=(not item.sector_below_vwap) if item.sector_below_vwap is not None else None,
            )
            for item in visible_positive[:5]
        ]
        weakest = [
            MarketSectorEvidenceOut(
                name=item.name,
                change_pct=item.change_pct,
                net_inflow=item.net_inflow,
                main_inflow=item.main_inflow,
                rank=item.rank,
                above_vwap=(not item.sector_below_vwap) if item.sector_below_vwap is not None else None,
            )
            for item in visible_negative[:5]
        ]
    if not raw_rows and not strongest and not weakest:
        notes.append("行业订单流方向榜为空，不生成板块扩散结论。")
    if not vwap_items:
        notes.append("行业指数分钟均价缺失，板块站上VWAP比例留空。")
    else:
        notes.append(f"行业站上VWAP比例基于榜单中{len(vwap_items)}个具备真实分钟曲线的行业样本。")
    directional_total = len(raw_positive) + len(raw_negative)
    return {
        "positive_sector_count": len(raw_positive) if raw_rows else None,
        "negative_sector_count": len(raw_negative) if raw_rows else None,
        "positive_sector_ratio": (
            round(len(raw_positive) / directional_total, 4)
            if directional_total else None
        ),
        "sector_above_vwap_ratio": (
            round(sum(not item.sector_below_vwap for item in vwap_items) / len(vwap_items), 4)
            if vwap_items else None
        ),
        "top3_inflow_share": round(top3_total / positive_total, 4) if positive_total > 0 else None,
        "strongest_sectors": strongest,
        "weakest_sectors": weakest,
    }, f"{flow.source}+eastmoney-sector-full" if raw_rows else flow.source, notes


def _missing_metric_fields(metrics: MarketRegimeMetrics) -> list[str]:
    labels = {
        "advance_ratio": "上涨家数占比",
        "volume_ratio_previous": "预计全天成交额/前日成交额",
        "volume_ratio_5d": "预计全天成交额/5日均额",
        "index_composite_change_pct": "主要指数合成涨跌幅",
        "limit_up_count": "涨停家数",
        "limit_down_count": "跌停家数",
        "market_main_net_inflow_yi": "全市场大单方向估算",
        "positive_sector_ratio": "行业上涨/正流入扩散比例",
        "top3_inflow_share": "行业流入集中度",
        "sector_above_vwap_ratio": "行业指数站上VWAP比例",
    }
    return [label for field_name, label in labels.items() if getattr(metrics, field_name) is None]


def _score_metrics(metrics: MarketRegimeMetrics) -> tuple[int, int, int]:
    advance = float(metrics.advance_ratio or 0)
    up_limit = int(metrics.limit_up_count or 0)
    down_limit = int(metrics.limit_down_count or 0)
    limit_score = (up_limit + 1) / (up_limit + down_limit + 2) * 100
    index_score = _clip(50 + float(metrics.index_composite_change_pct or 0) * 20)
    sector_score = float(metrics.positive_sector_ratio or 0) * 100
    turnover = float(metrics.turnover_yi or metrics.projected_turnover_yi or 0)
    main_flow = float(metrics.market_main_net_inflow_yi or 0)
    flow_ratio = main_flow / turnover if turnover > 0 else 0
    flow_score = _clip(50 + flow_ratio * 1000)
    opportunity = round(
        advance * 100 * 0.30
        + limit_score * 0.15
        + index_score * 0.20
        + sector_score * 0.20
        + flow_score * 0.15
    )
    volume_baselines = [
        float(value)
        for value in (metrics.volume_ratio_previous, metrics.volume_ratio_5d)
        if value is not None
    ]
    representative_volume = min(volume_baselines) if volume_baselines else 0.0
    liquidity = round(_clip(50 + (representative_volume - 1) * 100))
    return opportunity, 100 - opportunity, liquidity


def classify_market_regime(
    metrics: MarketRegimeMetrics,
    previous: MarketRegimeMetrics | None = None,
) -> MarketRegimeClassificationOut:
    """Pure, deterministic six-state classifier; UNKNOWN is explicit degradation."""
    missing = _missing_metric_fields(metrics)
    required = {
        "上涨家数占比",
        "预计全天成交额/5日均额",
        "主要指数合成涨跌幅",
        "涨停家数",
        "跌停家数",
        "全市场大单方向估算",
        "行业上涨/正流入扩散比例",
    }
    missing_required = [item for item in missing if item in required]
    opportunity, loss, liquidity = _score_metrics(metrics)
    total_quality_fields = 10
    populated = total_quality_fields - len(missing)
    confidence = round(max(0.0, min(0.98, populated / total_quality_fields)), 2)
    if missing_required:
        return MarketRegimeClassificationOut(
            regime_code="UNKNOWN",
            regime_name="数据不足",
            risk_level="未知",
            opportunity_score=opportunity,
            loss_score=loss,
            liquidity_score=liquidity,
            confidence=confidence,
            allowed_actions=["仅查看已有真实证据，等待缺失字段恢复"],
            forbidden_actions=["禁止依据不完整市场状态主动扩大仓位"],
            evidence=[f"关键数据缺口：{'、'.join(missing_required)}。"],
            missing_fields=missing,
        )

    volume_ratio = float(metrics.volume_ratio_5d)
    volume_ratio_previous = (
        float(metrics.volume_ratio_previous)
        if metrics.volume_ratio_previous is not None
        else None
    )
    advance_ratio = float(metrics.advance_ratio)
    index_change = float(metrics.index_composite_change_pct)
    main_flow = float(metrics.market_main_net_inflow_yi)
    sector_ratio = float(metrics.positive_sector_ratio)
    limit_up = int(metrics.limit_up_count)
    limit_down = int(metrics.limit_down_count)
    index_above = metrics.index_above_vwap_count
    evidence = [
        f"上涨占比{advance_ratio:.1%}，涨停{limit_up}只、跌停{limit_down}只。",
        (
            f"预计全天成交额为前日的{volume_ratio_previous:.2f}倍、5日均额的{volume_ratio:.2f}倍，"
            f"主要指数合成涨跌{index_change:+.2f}%。"
            if volume_ratio_previous is not None
            else f"预计全天成交额为5日均额的{volume_ratio:.2f}倍，主要指数合成涨跌{index_change:+.2f}%。"
        ),
        f"全市场大单方向估算{main_flow:+.2f}亿，行业正向比例{sector_ratio:.1%}；该值来自供应商算法，不是账户真实流水。",
    ]

    repair = False
    if previous is not None and all(
        value is not None for value in (
            previous.advance_ratio,
            previous.index_composite_change_pct,
            previous.positive_sector_ratio,
            previous.market_main_net_inflow_yi,
        )
    ):
        repair = bool(
            advance_ratio >= 0.35
            and advance_ratio - float(previous.advance_ratio) >= 0.08
            and index_change - float(previous.index_composite_change_pct) >= 0.60
            and sector_ratio - float(previous.positive_sector_ratio) >= 0.10
            and main_flow > float(previous.market_main_net_inflow_yi)
            and (index_above is None or index_above >= 3)
        )

    shrink_signal = bool(
        volume_ratio <= 0.78
        or (volume_ratio_previous is not None and volume_ratio_previous <= 0.85)
    )
    expansion_signal = bool(
        volume_ratio >= 1.05
        or (volume_ratio_previous is not None and volume_ratio_previous >= 1.08)
    )

    if repair:
        code, name, risk = "STABILIZING_REPAIR", "恐慌企稳修复", "中"
        allowed = ["仅允许10%-20%计划仓位试错", "等待板块和个股重新站稳VWAP后再执行"]
        forbidden = ["禁止把单次反抽当作趋势反转", "禁止一次性补满仓"]
        evidence.append("市场广度、指数、行业扩散和供应商订单流方向估算较上一快照同步修复。")
    elif (
        shrink_signal
        and advance_ratio <= 0.30
        and index_change <= -0.80
        and limit_down >= max(8, limit_up)
        and main_flow < 0
    ):
        code, name, risk = "EXTREME_SHRINK_DECLINE", "极致缩量普跌", "极高"
        allowed = ["仅处理已证伪持仓风险", "卖出方向与执行价格分开，避免极低位情绪化追卖"]
        forbidden = ["禁止新开仓", "禁止补仓摊低", "禁止弱势做T或预判反弹"]
    elif (
        expansion_signal
        and advance_ratio <= 0.32
        and index_change <= -1.00
        and main_flow < 0
        and (index_above is None or index_above <= 1)
    ):
        code, name, risk = "VOLUME_SELL_OFF", "放量杀跌", "极高"
        allowed = ["优先降低已确认的结构性风险", "只在反抽失败时按计划分批退出"]
        forbidden = ["禁止接下跌中的反弹", "禁止逆势补仓或做T"]
    elif (
        expansion_signal
        and advance_ratio >= 0.62
        and limit_up / max(1, limit_down + 1) >= 4
        and index_change >= 0.80
        and sector_ratio >= 0.60
        and main_flow > 0
    ):
        code, name, risk = "VOLUME_BROAD_RALLY", "放量普涨", "低"
        allowed = ["允许按策略正常开仓", "优先选择与指数、板块共振的前排核心"]
        forbidden = ["禁止追逐远离VWAP的后排加速", "禁止因普涨取消个股止损"]
    elif (
        (volume_ratio < 0.95 or (volume_ratio_previous is not None and volume_ratio_previous < 0.95))
        and 0.35 <= advance_ratio <= 0.60
        and metrics.top3_inflow_share is not None
        and float(metrics.top3_inflow_share) >= 0.50
        and sector_ratio <= 0.60
    ):
        code, name, risk = "SHRINK_ROTATION", "缩量存量轮动", "中高"
        allowed = ["只做主线前排或容量核心的小仓确认", "跟踪订单流方向排名和板块VWAP"]
        forbidden = ["禁止追后排补涨", "禁止在板块流入减速时接力"]
        evidence.append(f"前三行业占正向订单流{float(metrics.top3_inflow_share):.1%}，方向估算集中于少数板块。")
    else:
        code, name, risk = "NEUTRAL_DIVERGENCE", "中性震荡分歧", "中"
        allowed = ["按个股预期和量价证据执行", "控制仓位并等待方向确认"]
        forbidden = ["禁止仅凭指数红绿下单", "禁止忽略板块与个股相对强弱"]

    return MarketRegimeClassificationOut(
        regime_code=code,
        regime_name=name,
        risk_level=risk,
        opportunity_score=opportunity,
        loss_score=loss,
        liquidity_score=liquidity,
        confidence=confidence,
        allowed_actions=allowed,
        forbidden_actions=forbidden,
        evidence=evidence,
        missing_fields=missing,
    )


def collect_market_regime_inputs(force_refresh: bool = False, now: datetime | None = None) -> MarketRegimeCollection:
    now = shanghai_now_naive(now)
    notes: list[str] = []
    sources: list[str] = []
    all_a: dict[str, Any] = {}
    indices: list[MarketIndexStateOut] = []
    sector: dict[str, Any] = {}
    history: tuple[float | None, float | None, str, list[str]] = (None, None, "unavailable", [])

    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks = {
            executor.submit(_fetch_all_a_market, now): "all_a",
            executor.submit(_fetch_indices): "indices",
            executor.submit(_fetch_sector_evidence, force_refresh): "sector",
        }
        for future in as_completed(tasks):
            key = tasks[future]
            try:
                value = future.result()
                if key == "all_a":
                    all_a, source, task_notes = value
                elif key == "indices":
                    indices, source, task_notes = value
                else:
                    sector, source, task_notes = value
                sources.append(source)
                notes.extend(task_notes)
            except Exception as exc:
                notes.append(f"{key}真实数据采集失败：{exc.__class__.__name__}；对应字段保持为空。")

    source_time = all_a.get("source_time")
    trade_date = source_time.date().isoformat() if isinstance(source_time, datetime) else now.date().isoformat()
    try:
        history = _fetch_turnover_history(trade_date)
        sources.append(history[2])
        notes.extend(history[3])
    except Exception as exc:
        notes.append(f"历史成交额采集失败：{exc.__class__.__name__}；量能比字段保持为空。")

    valid_indices = [item for item in indices if item.change_pct is not None]
    above_vwap = [item for item in indices if item.above_vwap is not None]
    index_composite = (
        round(sum(float(item.change_pct) for item in valid_indices) / len(valid_indices), 3)
        if len(valid_indices) >= 2 else None
    )
    previous_turnover, avg5_turnover = history[0], history[1]
    projected = all_a.get("projected_turnover_yi")
    volume_ratio_previous = (
        round(float(projected) / float(previous_turnover), 4)
        if projected is not None and previous_turnover and previous_turnover > 0 else None
    )
    volume_ratio = (
        round(float(projected) / float(avg5_turnover), 4)
        if projected is not None and avg5_turnover and avg5_turnover > 0 else None
    )
    metrics = MarketRegimeMetrics(
        **{key: value for key, value in all_a.items() if key in MarketRegimeMetrics.model_fields},
        previous_turnover_yi=previous_turnover,
        avg5_turnover_yi=avg5_turnover,
        volume_ratio_previous=volume_ratio_previous,
        volume_ratio_5d=volume_ratio,
        index_composite_change_pct=index_composite,
        index_above_vwap_count=(sum(bool(item.above_vwap) for item in above_vwap) if above_vwap else None),
        index_valid_count=len(valid_indices),
        **{key: value for key, value in sector.items() if key in MarketRegimeMetrics.model_fields},
    )
    if len(valid_indices) < 2:
        notes.append("主要指数有效数量不足2个，不输出指数合成涨跌幅。")
    return MarketRegimeCollection(
        metrics=metrics,
        trade_date=trade_date,
        captured_at=now,
        source="+".join(dict.fromkeys(source for source in sources if source)) or "unavailable",
        indices=indices,
        strongest_sectors=list(sector.get("strongest_sectors") or []),
        weakest_sectors=list(sector.get("weakest_sectors") or []),
        notes=list(dict.fromkeys(notes)),
    )


def _metrics_from_snapshot(row: MarketRegimeSnapshot) -> MarketRegimeMetrics:
    return MarketRegimeMetrics(**{
        field_name: getattr(row, field_name)
        for field_name in MarketRegimeMetrics.model_fields
    })


def _snapshot_to_out(row: MarketRegimeSnapshot, freshness_seconds: int = 0) -> MarketRegimeOut:
    return MarketRegimeOut(
        id=row.id,
        trade_date=row.trade_date,
        captured_at=row.captured_at,
        source=row.source,
        freshness_seconds=max(0, freshness_seconds),
        data_quality=row.data_quality,
        coverage_ratio=row.coverage_ratio,
        confidence=row.confidence,
        **_metrics_from_snapshot(row).model_dump(),
        indices=[MarketIndexStateOut.model_validate(item) for item in _json_list(row.indices_json)],
        strongest_sectors=[MarketSectorEvidenceOut.model_validate(item) for item in _json_list(row.strongest_sectors_json)],
        weakest_sectors=[MarketSectorEvidenceOut.model_validate(item) for item in _json_list(row.weakest_sectors_json)],
        regime_code=row.regime_code,
        regime_name=row.regime_name,
        risk_level=row.risk_level,
        opportunity_score=row.opportunity_score,
        loss_score=row.loss_score,
        liquidity_score=row.liquidity_score,
        allowed_actions=[str(item) for item in _json_list(row.allowed_actions_json)],
        forbidden_actions=[str(item) for item in _json_list(row.forbidden_actions_json)],
        evidence=[str(item) for item in _json_list(row.evidence_json)],
        missing_fields=[str(item) for item in _json_list(row.missing_fields_json)],
        notes=[str(item) for item in _json_list(row.notes_json)],
    )


def clear_market_regime_cache() -> None:
    global _REGIME_CACHE
    with _CACHE_LOCK:
        _REGIME_CACHE = None


def get_market_regime(db: Session, force_refresh: bool = False) -> MarketRegimeOut:
    global _REGIME_CACHE
    now_clock = clock.time()
    if not force_refresh:
        with _CACHE_LOCK:
            cached = _REGIME_CACHE
        if cached and cached[0] > now_clock:
            captured = cached[1].captured_at
            freshness = max(0, int((shanghai_now_naive() - captured).total_seconds()))
            return cached[1].model_copy(update={"freshness_seconds": freshness})

    collection = collect_market_regime_inputs(force_refresh=force_refresh)
    previous_row = (
        db.query(MarketRegimeSnapshot)
        .filter(MarketRegimeSnapshot.trade_date == collection.trade_date)
        .order_by(MarketRegimeSnapshot.captured_at.desc(), MarketRegimeSnapshot.id.desc())
        .first()
    )
    previous_metrics = None
    if previous_row and collection.captured_at - previous_row.captured_at <= timedelta(minutes=30):
        previous_metrics = _metrics_from_snapshot(previous_row)
    classification = classify_market_regime(collection.metrics, previous_metrics)
    field_values = [
        getattr(collection.metrics, field_name)
        for field_name in MarketRegimeMetrics.model_fields
        if field_name != "index_valid_count"
    ]
    coverage = round(sum(value is not None for value in field_values) / max(1, len(field_values)), 4)
    gap_markers = ("失败", "缺口", "不足", "为空", "不可用", "保持为空", "留空", "未返回")
    has_collection_gap = any(
        marker in note
        for note in collection.notes
        for marker in gap_markers
    )
    data_quality = (
        "missing"
        if classification.regime_code == "UNKNOWN"
        else "degraded"
        if classification.missing_fields or has_collection_gap
        else "complete"
    )
    row = MarketRegimeSnapshot(
        trade_date=collection.trade_date,
        captured_at=collection.captured_at,
        source=collection.source,
        data_quality=data_quality,
        coverage_ratio=coverage,
        confidence=classification.confidence,
        **collection.metrics.model_dump(),
        indices_json=json.dumps([item.model_dump(mode="json") for item in collection.indices], ensure_ascii=False),
        strongest_sectors_json=json.dumps([item.model_dump(mode="json") for item in collection.strongest_sectors], ensure_ascii=False),
        weakest_sectors_json=json.dumps([item.model_dump(mode="json") for item in collection.weakest_sectors], ensure_ascii=False),
        regime_code=classification.regime_code,
        regime_name=classification.regime_name,
        risk_level=classification.risk_level,
        opportunity_score=classification.opportunity_score,
        loss_score=classification.loss_score,
        liquidity_score=classification.liquidity_score,
        allowed_actions_json=json.dumps(classification.allowed_actions, ensure_ascii=False),
        forbidden_actions_json=json.dumps(classification.forbidden_actions, ensure_ascii=False),
        evidence_json=json.dumps(classification.evidence, ensure_ascii=False),
        missing_fields_json=json.dumps(classification.missing_fields, ensure_ascii=False),
        notes_json=json.dumps(collection.notes, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    result = _snapshot_to_out(row)
    with _CACHE_LOCK:
        _REGIME_CACHE = (clock.time() + REGIME_CACHE_SECONDS, result)
    return result
