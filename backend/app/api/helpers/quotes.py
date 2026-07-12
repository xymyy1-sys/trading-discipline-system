import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
import requests
from app.services.market_data import _last_trading_day
from app.models.trading import Holding

_QUOTE_META_CACHE: dict[str, dict[str, Any]] = {}

def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "-":
            return 0.0
        return float(value)
    except Exception:
        return 0.0

def _safe_turnover(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw <= 0:
        return None
    turnover = raw * 100 if 0 < raw < 1 else raw
    if turnover > 120:
        return None
    return round(turnover, 2)

def _normalize_code(code: str) -> str:
    raw = str(code or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return raw.zfill(6)
    if len(digits) <= 6:
        return digits.zfill(6)
    return digits

def _quote_code_candidates(code: str) -> list[str]:
    raw = str(code or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    candidates: list[str] = []
    if len(digits) == 6:
        candidates.append(digits)
    elif len(digits) < 6 and digits:
        candidates.append(digits.zfill(6))
    elif len(digits) > 6:
        candidates.extend([digits[:6], digits[-6:]])
        if digits.startswith("5888") and len(digits) == 7:
            candidates.append(digits[:3] + digits[4:])
    return list(dict.fromkeys(item for item in candidates if len(item) == 6))

def _quote_lookup_code(code: str, quotes: dict[str, dict[str, Any]]) -> str:
    for candidate in _quote_code_candidates(code):
        if candidate in quotes:
            return candidate
    candidates = _quote_code_candidates(code)
    return candidates[0] if candidates else _normalize_code(code)

def _code_hint(code: str) -> str:
    normalized = _normalize_code(code)
    if len(normalized) != 6:
        candidates = "、".join(_quote_code_candidates(code)) or "无"
        return f" 代码长度异常，候选匹配：{candidates}。"
    return ""

def _is_realtime_note(price_note: str) -> bool:
    note = str(price_note or "")
    if not note:
        return False
    failure_words = ("失败", "未匹配", "暂用", "手动", "缓存", "数据缺口", "异常")
    if any(word in note for word in failure_words):
        return False
    return "实时行情" in note or "东方财富" in note or "AkShare" in note or "新浪" in note or "腾讯" in note

def _latest_a_share_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    try:
        import akshare as ak
        frame = ak.stock_zh_a_spot_em()
        if frame.empty:
            raise ValueError("empty spot quote")
        normalized = set()
        for code in codes:
            normalized.update(_quote_code_candidates(code))
        quotes: dict[str, dict[str, Any]] = {}
        for _, row in frame.iterrows():
            code = str(row.get("代码") or row.get("code") or "").zfill(6)
            if code not in normalized:
                continue
            price = _safe_float(row.get("最新价") or row.get("price"))
            if price <= 0:
                continue
            open_price = _safe_float(row.get("今开") or row.get("open"))
            prev_close = _safe_float(row.get("昨收") or row.get("prev_close"))
            high_price = _safe_float(row.get("最高") or row.get("high"))
            low_price = _safe_float(row.get("最低") or row.get("low"))
            quotes[code] = {
                "price": price,
                "change_pct": _safe_float(row.get("涨跌幅") or row.get("change_pct")),
                "amount": round(_safe_float(row.get("成交额") or row.get("amount")) / 1e8, 2),
                "turnover": _safe_turnover(row.get("换手率") or row.get("turnover")),
                "open": open_price,
                "prev_close": prev_close,
                "high": high_price,
                "low": low_price,
                "note": "AkShare/东方财富实时行情",
            }
        if quotes:
            _attach_minute_bars(quotes)
            return quotes
    except Exception:
        pass
    try:
        quotes = _latest_a_share_quotes_sina(codes)
        if quotes:
            _attach_minute_bars(quotes)
            return quotes
    except Exception:
        pass
    quotes = _latest_a_share_quotes_eastmoney(codes)
    _attach_minute_bars(quotes)
    return quotes

def _latest_a_share_quotes_sina(codes: list[str]) -> dict[str, dict[str, Any]]:
    symbols = []
    code_by_symbol: dict[str, str] = {}
    for code in codes:
        for candidate in _quote_code_candidates(code):
            prefix = "sh" if candidate.startswith(("5", "6", "9")) else "sz"
            symbol = f"{prefix}{candidate}"
            symbols.append(symbol)
            code_by_symbol[symbol] = candidate
    if not symbols:
        return {}
    url = "https://hq.sinajs.cn/list=" + ",".join(dict.fromkeys(symbols))
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        timeout=8,
    )
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="ignore")
    quotes: dict[str, dict[str, Any]] = {}
    for symbol, payload in re.findall(r'var hq_str_(s[hz]\d{6})="([^"]*)"', text):
        parts = payload.split(",")
        if len(parts) < 32 or not parts[0]:
            continue
        code = code_by_symbol.get(symbol, symbol[-6:])
        open_price = _safe_float(parts[1])
        prev_close = _safe_float(parts[2])
        price = _safe_float(parts[3])
        high_price = _safe_float(parts[4])
        low_price = _safe_float(parts[5])
        volume = _safe_float(parts[8])
        amount = _safe_float(parts[9])
        if price <= 0:
            continue
        change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
        quotes[code] = {
            "price": price,
            "change_pct": change_pct,
            "amount": round(amount / 1e8, 2),
            "turnover": 0.0,
            "open": open_price,
            "prev_close": prev_close,
            "high": high_price,
            "low": low_price,
            "volume": volume,
            "note": "新浪实时行情",
        }
    return quotes

def _latest_a_share_quotes_eastmoney(codes: list[str]) -> dict[str, dict[str, Any]]:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    secids = ",".join(
        _eastmoney_secid(candidate)
        for code in codes
        for candidate in _quote_code_candidates(code)
        if candidate
    )
    if not secids:
        return {}
    params = urlencode({
        "fltt": "2",
        "invt": "2",
        "fields": "f12,f14,f2,f3,f6,f8,f15,f16,f17,f18,f21",
        "secids": secids,
    })
    url = f"https://push2.eastmoney.com/api/qt/ulist.np/get?{params}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=6) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("data", {}).get("diff", []) or []
    quotes: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("f12") or "").zfill(6)
        price = _safe_float(row.get("f2"))
        if not code or price <= 0:
            continue
        quotes[code] = {
            "price": price,
            "change_pct": _safe_float(row.get("f3")),
            "amount": round(_safe_float(row.get("f6")) / 1e8, 2),
            "turnover": _safe_turnover(row.get("f8")),
            "turnover_source": "eastmoney_f8_free_float",
            "turnover_reliable": _safe_turnover(row.get("f8")) is not None,
            "float_cap": round(_safe_float(row.get("f21")) / 1e8, 2),
            "open": _safe_float(row.get("f17")),
            "prev_close": _safe_float(row.get("f18")),
            "high": _safe_float(row.get("f15")),
            "low": _safe_float(row.get("f16")),
            "note": "东方财富实时行情",
        }
    return quotes

def _eastmoney_secid(code: str) -> str:
    normalized = _quote_code_candidates(code)[0] if _quote_code_candidates(code) else _normalize_code(code)
    market = "1" if normalized.startswith(("5", "6", "9")) else "0"
    return f"{market}.{normalized}"


def _attach_minute_bars(quotes: dict[str, dict[str, Any]]) -> None:
    for code, quote in quotes.items():
        primary_error = ""
        try:
            bars = _eastmoney_minute_bars(code)
        except Exception as exc:
            bars = []
            primary_error = str(exc)
        if bars:
            source = "东方财富1分钟分时K线"
            status = "ok"
        else:
            try:
                bars = _sina_minute_bars(code)
            except Exception as exc:
                quote["minute_bar_status"] = "fetch_error" if primary_error else "no_recent_rows"
                quote["minute_fetch_error"] = "; ".join(
                    item for item in (f"东方财富: {primary_error}" if primary_error else "", f"新浪: {exc}") if item
                )
                continue
            source = "新浪1分钟分时K线（成交额按收盘价估算）"
            status = "fallback_ok"
            quote["minute_amount_estimated"] = True
            if primary_error:
                quote["minute_fetch_error"] = f"东方财富: {primary_error}"
        if not bars:
            quote["minute_bar_status"] = "no_recent_rows"
            continue
        quote["minute_bars"] = bars
        quote["minute_bar_source"] = source
        quote["minute_bar_status"] = status
        quote["minute_bar_trade_date"] = bars[-1].get("trade_date") or _last_trading_day()
        date_note = "" if quote["minute_bar_trade_date"] == datetime.now().date().isoformat() else f"({quote['minute_bar_trade_date']})"
        source_note = "东方财富1分钟成交" if status == "ok" else source
        quote["note"] = f"{quote.get('note') or '实时行情'} + {source_note}{date_note}"


def _sina_minute_bars(code: str) -> list[dict[str, Any]]:
    import akshare as ak

    normalized = _quote_code_candidates(code)[0] if _quote_code_candidates(code) else _normalize_code(code)
    symbol = ("sh" if normalized.startswith(("5", "6", "9")) else "sz") + normalized
    trade_date = _last_trading_day()
    frame = ak.stock_zh_a_minute(symbol=symbol, period="1", adjust="")
    if frame is None or frame.empty:
        return []
    bars: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        timestamp = str(row.get("day") or "")
        if not timestamp.startswith(trade_date):
            continue
        close = _safe_float(row.get("close"))
        volume = _safe_float(row.get("volume"))
        bars.append({
            "trade_date": trade_date, "time": timestamp[11:16],
            "open": _safe_float(row.get("open")), "price": close, "close": close,
            "high": _safe_float(row.get("high")), "low": _safe_float(row.get("low")),
            "volume": volume, "amount": close * volume, "amount_estimated": True,
        })
    return bars


def _eastmoney_minute_bars(code: str) -> list[dict[str, Any]]:
    normalized = _quote_code_candidates(code)[0] if _quote_code_candidates(code) else _normalize_code(code)
    secid = _eastmoney_secid(normalized)
    trade_date = _last_trading_day()
    params = {
        "secid": secid,
        "klt": "1",
        "fqt": "1",
        "lmt": "320",
        "end": "20500101",
        "iscca": "1",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json,text/plain,*/*",
    }
    rows: list[str] = []
    last_exc: Exception | None = None
    for host in ("https://push2his.eastmoney.com", "https://push2delay.eastmoney.com"):
        try:
            resp = requests.get(f"{host}/api/qt/stock/kline/get", params=params, headers=headers, timeout=6)
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            rows = data.get("klines") or []
            if rows:
                break
        except Exception as exc:
            last_exc = exc
    if not rows:
        if last_exc:
            raise last_exc
        return []
    bars: list[dict[str, Any]] = []
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 7:
            continue
        ts = parts[0]
        if not ts.startswith(trade_date):
            continue
        bars.append({
            "trade_date": trade_date,
            "time": ts[-5:],
            "open": _safe_float(parts[1]),
            "price": _safe_float(parts[2]),
            "close": _safe_float(parts[2]),
            "high": _safe_float(parts[3]),
            "low": _safe_float(parts[4]),
            "volume": _safe_float(parts[5]) * 100,
            "amount": _safe_float(parts[6]),
            "turnover": _safe_float(parts[10]) if len(parts) > 10 else 0.0,
        })
    try:
        tick_flow = _eastmoney_tick_flow(normalized)
        for bar in bars:
            flow = tick_flow.get(str(bar.get("time") or ""))
            if flow:
                bar.update(flow)
    except Exception:
        pass
    return bars


def _eastmoney_tick_flow(code: str, large_order_threshold: float = 200_000) -> dict[str, dict[str, float]]:
    normalized = _quote_code_candidates(code)[0] if _quote_code_candidates(code) else _normalize_code(code)
    response = requests.get(
        "https://70.push2.eastmoney.com/api/qt/stock/details/sse",
        params={
            "fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f55", "mpi": "2000",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": "2", "pos": "-0",
            "secid": _eastmoney_secid(normalized), "wbp2u": "|0|0|0|web",
        },
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        timeout=6,
    )
    response.raise_for_status()
    payload = None
    for line in response.text.splitlines():
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            break
    details = ((payload or {}).get("data") or {}).get("details") or []
    output: dict[str, dict[str, float]] = {}
    for detail in details:
        parts = str(detail).split(",")
        if len(parts) < 5:
            continue
        raw_time, price, hands, _, nature = parts[:5]
        minute = raw_time[:5]
        amount = _safe_float(price) * _safe_float(hands) * 100
        row = output.setdefault(minute, {"active_buy_amount": 0.0, "active_sell_amount": 0.0, "large_order_net_amount": 0.0})
        if nature == "2":
            row["active_buy_amount"] += amount
            if amount >= large_order_threshold:
                row["large_order_net_amount"] += amount
        elif nature == "1":
            row["active_sell_amount"] += amount
            if amount >= large_order_threshold:
                row["large_order_net_amount"] -= amount
        row["large_order_threshold"] = large_order_threshold
    return output

def _latest_quote_for_holding(holding: Holding) -> dict[str, Any]:
    try:
        quotes = _latest_a_share_quotes([holding.code])
        return quotes.get(_quote_lookup_code(holding.code, quotes), {})
    except Exception:
        return {}

def _daily_history_metrics(code: str) -> dict[str, float]:
    candidates = _quote_code_candidates(code)
    if not candidates:
        return {}
    try:
        candidate = candidates[0]
        symbol = ("sh" if candidate.startswith(("5", "6", "9")) else "sz") + candidate
        url = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
        resp = requests.get(
            url,
            params={"param": f"{symbol},day,,,30"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
            timeout=4,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = (((payload.get("data") or {}).get(symbol) or {}).get("day") or [])
        if not rows:
            return {}
        volumes = [_safe_float(row[5]) for row in rows if len(row) > 5]
        closes = [_safe_float(row[2]) for row in rows if len(row) > 2]
        prev_volumes = volumes[:-1] if len(volumes) >= 2 else volumes
        highs = [_safe_float(row[3]) for row in rows if len(row) > 3]
        def cumulative_return(days: int) -> float:
            if len(closes) <= days or closes[-days - 1] <= 0:
                return 0.0
            return (closes[-1] / closes[-days - 1] - 1) * 100
        weighted = sorted(zip(closes[-30:], volumes[-30:]), key=lambda item: item[0])
        total_weight = sum(weight for _, weight in weighted if weight > 0)
        def weighted_quantile(ratio: float) -> float:
            target = total_weight * ratio
            running = 0.0
            for value, weight in weighted:
                running += max(0, weight)
                if running >= target:
                    return value
            return weighted[-1][0] if weighted else 0.0
        current_close = closes[-1] if closes else 0.0
        chip_profit_ratio = (
            sum(weight for close, weight in weighted if close <= current_close) / total_weight * 100
            if total_weight > 0 else 0.0
        )
        chip_avg_cost = sum(close * weight for close, weight in weighted) / total_weight if total_weight > 0 else 0.0
        q15, q85 = weighted_quantile(.15), weighted_quantile(.85)
        q05, q95 = weighted_quantile(.05), weighted_quantile(.95)
        return {
            "five_day_avg_volume": sum(prev_volumes[-5:]) / len(prev_volumes[-5:]) if prev_volumes else 0,
            "ma5": sum(closes[-5:]) / len(closes[-5:]) if closes else 0,
            "ma10": sum(closes[-10:]) / len(closes[-10:]) if closes else 0,
            "ma20": sum(closes[-20:]) / len(closes[-20:]) if closes else 0,
            "return_1d": cumulative_return(1),
            "return_2d": cumulative_return(2),
            "return_3d": cumulative_return(3),
            "return_5d": cumulative_return(5),
            "return_10d": cumulative_return(10),
            "recent_high": max(highs[-20:], default=0),
            "latest_volume": volumes[-1] if volumes else 0,
            "chip_profit_ratio": chip_profit_ratio,
            "chip_avg_cost": chip_avg_cost,
            "chip_70_concentration": (q85 - q15) / (q85 + q15) * 100 if q85 + q15 > 0 else 0,
            "chip_90_concentration": (q95 - q05) / (q95 + q05) * 100 if q95 + q05 > 0 else 0,
        }
    except Exception:
        return {}

def _estimated_vwap(quote: dict[str, Any]) -> float:
    amount_yuan = _safe_float(quote.get("amount")) * 1e8
    volume_shares = _safe_float(quote.get("volume"))
    return amount_yuan / volume_shares if amount_yuan and volume_shares else 0.0

def _next_limit_up_price(price: float, ratio: str = "1.10") -> float:
    value = Decimal(str(price)) * Decimal(ratio)
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def _json_obj(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}

def _json_list(raw: str) -> list[str]:
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []
