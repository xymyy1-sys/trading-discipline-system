"""Global market evidence used by the A-share pre-market cockpit.

The module deliberately keeps its output independent from the shared API
schemas.  It can therefore be integrated by a route without making external
data availability a startup dependency.

Data policy:

* AkShare is only an adapter for the underlying Eastmoney public pages.  The
  source and possible delay are always exposed.
* Korean single-stock prices are never inferred from an index.  Samsung
  Electronics and SK Hynix are unavailable until a licensed KIS adapter is
  configured.
* Missing values stay ``None``.  A failed source must not turn into a zero
  quote, because zero is a valid (and highly misleading) numeric value.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


KOREA_INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("KS11", "韩国综合指数", ("KS11", "KOSPI", "韩国综合", "韩国KOSPI")),
    ("KOSPI200", "韩国KOSPI 200", ("KOSPI200", "KOSPI 200", "韩国KOSPI200")),
)

US_INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("SPX", "标普500", ("SPX", "标普500", "标准普尔500", "S&P 500")),
    ("NDX", "纳斯达克100", ("NDX", "纳斯达克100", "NASDAQ 100")),
    ("DJIA", "道琼斯工业指数", ("DJIA", "道琼斯", "道指")),
)

US_SECTOR_ETFS: Mapping[str, tuple[str, str]] = {
    "XLK": ("信息技术", "科技行业ETF代理"),
    "XLC": ("通信服务", "通信服务行业ETF代理"),
    "XLY": ("可选消费", "可选消费行业ETF代理"),
    "XLP": ("必选消费", "必选消费行业ETF代理"),
    "XLE": ("能源", "能源行业ETF代理"),
    "XLF": ("金融", "金融行业ETF代理"),
    "XLV": ("医疗保健", "医疗保健行业ETF代理"),
    "XLI": ("工业", "工业行业ETF代理"),
    "XLB": ("原材料", "原材料行业ETF代理"),
    "XLU": ("公用事业", "公用事业行业ETF代理"),
    "XLRE": ("房地产", "房地产行业ETF代理"),
    "SMH": ("半导体", "半导体ETF代理"),
    "SOXX": ("半导体", "半导体ETF代理"),
    "ITA": ("国防军工", "航空国防ETF代理"),
    "ARKX": ("商业航天", "太空探索ETF代理"),
    "XBI": ("生物科技", "生物科技ETF代理"),
}

KOREA_EQUITIES: tuple[tuple[str, str], ...] = (
    ("005930", "三星电子"),
    ("000660", "SK海力士"),
)
YAHOO_KOREA_SYMBOLS: dict[str, str] = {
    "005930": "005930.KS",
    "000660": "000660.KS",
}
YAHOO_KOREA_INDEX_SYMBOLS: dict[str, str] = {
    "KS11": "^KS11",
    "KOSPI200": "^KS200",
}
YAHOO_US_INDEX_SYMBOLS: dict[str, str] = {
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "DJIA": "^DJI",
}


@dataclass(slots=True)
class KISConfiguration:
    """Configuration marker for a future licensed KIS quote adapter."""

    app_key: str = ""
    app_secret: str = ""

    @classmethod
    def from_environment(cls) -> "KISConfiguration":
        return cls(
            app_key=os.getenv("KIS_APP_KEY", "").strip(),
            app_secret=os.getenv("KIS_APP_SECRET", "").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)


@dataclass(slots=True)
class GlobalQuote:
    symbol: str
    name: str
    market: str
    status: str
    price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    previous_close: float | None = None
    open_price: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    amount: float | None = None
    as_of: str | None = None
    source: str = ""
    freshness: str = "unknown"
    theme: str | None = None
    proxy_description: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GlobalMarketSnapshot:
    generated_at: str
    korea_indices: list[GlobalQuote] = field(default_factory=list)
    korea_equities: list[GlobalQuote] = field(default_factory=list)
    us_indices: list[GlobalQuote] = field(default_factory=list)
    us_sector_rank: list[GlobalQuote] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    quality: str = "missing"
    kis: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        grouped_items = (
            [("korea_index", item) for item in payload["korea_indices"]]
            + [("korea_equity", item) for item in payload["korea_equities"]]
            + [("us_index", item) for item in payload["us_indices"]]
            + [("us_sector_proxy", item) for item in payload["us_sector_rank"]]
        )
        # Stable envelope aliases let routes/frontends consume this service in
        # the same way as the domestic evidence services while the explicit
        # groups above remain available for purpose-built UI panels.
        payload.update(
            {
                "source": list(self.sources),
                "as_of": self.generated_at,
                "data_quality": self.quality,
                "items": [{"group": group, **item} for group, item in grouped_items],
            }
        )
        return payload


Loader = Callable[[], Any]
KISEquityLoader = Callable[[Iterable[str]], Any]
_USE_DEFAULT_YAHOO_LOADER = object()


class GlobalMarketService:
    """Load and normalize overseas evidence without fabricating gaps."""

    def __init__(
        self,
        *,
        global_index_loader: Loader | None = None,
        us_stock_loader: Loader | None = None,
        sox_loader: Loader | None = None,
        kis_equity_loader: KISEquityLoader | None = None,
        yahoo_equity_loader: KISEquityLoader | None | object = _USE_DEFAULT_YAHOO_LOADER,
        kis_config: KISConfiguration | None = None,
        cache_ttl_seconds: int = 60,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.global_index_loader = global_index_loader or _akshare_global_indices
        self.us_stock_loader = us_stock_loader or _akshare_us_stocks
        self.sox_loader = sox_loader or _akshare_sox_history
        self.kis_equity_loader = kis_equity_loader
        self.yahoo_equity_loader = (
            _yahoo_korea_equities
            if yahoo_equity_loader is _USE_DEFAULT_YAHOO_LOADER
            else yahoo_equity_loader
        )
        self.kis_config = kis_config or KISConfiguration.from_environment()
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.now_provider = now_provider or (lambda: datetime.now(SHANGHAI_TZ))
        self._cached_at = 0.0
        self._cached: GlobalMarketSnapshot | None = None

    def snapshot(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._cached is not None
            and now - self._cached_at < self.cache_ttl_seconds
        ):
            return self._cached.to_dict()

        result = self._build_snapshot()
        self._cached = result
        self._cached_at = now
        return result.to_dict()

    # Alias kept explicit for route authors and background collectors.
    get_snapshot = snapshot

    def _build_snapshot(self) -> GlobalMarketSnapshot:
        current = _ensure_timezone(self.now_provider())
        notes: list[str] = []
        sources: list[str] = []

        try:
            index_rows = _records(self.global_index_loader())
            sources.append("东方财富全球指数（AkShare适配）")
        except Exception as exc:  # each source degrades independently
            index_rows = []
            notes.append(f"全球指数源不可用：{exc.__class__.__name__}")

        korea_indices = _select_defined_indices(
            index_rows,
            KOREA_INDEX_DEFINITIONS,
            market="韩国",
            source="东方财富全球指数（AkShare适配）",
            freshness="门户行情，可能延迟",
        )
        us_indices = _select_defined_indices(
            index_rows,
            US_INDEX_DEFINITIONS,
            market="美国",
            source="东方财富全球指数（AkShare适配）",
            freshness="隔夜收盘参考，门户行情可能延迟",
        )

        # The Eastmoney global endpoint occasionally closes the connection.
        # When the production defaults are in use, fill only genuinely missing
        # indices from Yahoo chart metadata and label them as delayed.
        if self.global_index_loader is _akshare_global_indices and (
            len(korea_indices) < len(KOREA_INDEX_DEFINITIONS)
            or len(us_indices) < len(US_INDEX_DEFINITIONS)
        ):
            try:
                yahoo_index_rows = _records(_yahoo_chart_quotes([
                    *YAHOO_KOREA_INDEX_SYMBOLS.values(),
                    *YAHOO_US_INDEX_SYMBOLS.values(),
                ]))
                korea_indices = _merge_yahoo_indices(
                    korea_indices,
                    yahoo_index_rows,
                    KOREA_INDEX_DEFINITIONS,
                    YAHOO_KOREA_INDEX_SYMBOLS,
                    market="韩国",
                )
                us_indices = _merge_yahoo_indices(
                    us_indices,
                    yahoo_index_rows,
                    US_INDEX_DEFINITIONS,
                    YAHOO_US_INDEX_SYMBOLS,
                    market="美国",
                )
                sources.append("Yahoo Finance全球指数（只读延迟降级）")
            except Exception as exc:
                notes.append(f"Yahoo全球指数降级源不可用：{exc.__class__.__name__}")

        try:
            sox_rows = _records(self.sox_loader())
            sox = _latest_sox_quote(sox_rows)
            if sox is not None:
                us_indices.append(sox)
                sources.append("东方财富费城半导体历史指标（AkShare适配）")
        except Exception as exc:
            notes.append(f"费城半导体指数源不可用：{exc.__class__.__name__}")

        try:
            us_rows = _records(self.us_stock_loader())
            us_sector_rank = _sector_etf_quotes(us_rows)
            sources.append("东方财富美股行情（AkShare适配）")
        except Exception as exc:
            us_sector_rank = []
            notes.append(f"美股行业ETF源不可用：{exc.__class__.__name__}")

        if self.us_stock_loader is _akshare_us_stocks and len(us_sector_rank) < len(US_SECTOR_ETFS):
            try:
                yahoo_sector_rows = _records(_yahoo_chart_quotes(US_SECTOR_ETFS.keys()))
                us_sector_rank = _merge_yahoo_sector_etfs(us_sector_rank, yahoo_sector_rows)
                if yahoo_sector_rows:
                    sources.append("Yahoo Finance美股行业ETF（只读延迟代理）")
            except Exception as exc:
                notes.append(f"Yahoo美股行业ETF降级源不可用：{exc.__class__.__name__}")

        korea_equities, kis_note = self._korea_equities()
        if kis_note:
            notes.append(kis_note)
        if any(item.status == "ok" and item.source == "KIS Open API" for item in korea_equities):
            sources.append("韩国投资证券KIS Open API")
        if any(item.status == "delayed" and item.source.startswith("Yahoo Finance") for item in korea_equities):
            sources.append("Yahoo Finance chart v8（只读延迟降级）")

        available_groups = sum(
            any(item.status in {"ok", "delayed"} for item in group)
            for group in (korea_indices, us_indices, us_sector_rank)
        )
        quality = "ok" if available_groups == 3 else "degraded" if available_groups else "missing"
        if not us_sector_rank:
            notes.append("美股行业表现不可用；不生成模拟行业排行。")
        if not korea_indices:
            notes.append("韩国指数不可用；不以其他亚洲指数代替。")

        return GlobalMarketSnapshot(
            generated_at=current.isoformat(),
            korea_indices=korea_indices,
            korea_equities=korea_equities,
            us_indices=us_indices,
            us_sector_rank=us_sector_rank,
            sources=list(dict.fromkeys(sources)),
            notes=list(dict.fromkeys(notes)),
            quality=quality,
            kis={
                "configured": self.kis_config.configured,
                "adapter_enabled": self.kis_equity_loader is not None,
                "yahoo_fallback_enabled": self.yahoo_equity_loader is not None,
                "note": (
                    "KIS凭证和行情适配器已启用。"
                    if self.kis_config.configured and self.kis_equity_loader
                    else "已检测KIS凭证，等待启用授权行情适配器。"
                    if self.kis_config.configured
                    else "未配置KIS_APP_KEY/KIS_APP_SECRET；如Yahoo降级源可用，仅展示其只读延迟行情。"
                    if self.yahoo_equity_loader is not None
                    else "未配置KIS_APP_KEY/KIS_APP_SECRET，韩国个股行情保持不可用。"
                ),
            },
        )

    def _korea_equities(self) -> tuple[list[GlobalQuote], str]:
        if not self.kis_config.configured:
            return self._with_yahoo_fallback(
                [
                    GlobalQuote(
                        symbol=code,
                        name=name,
                        market="韩国",
                        status="unavailable",
                        source="KIS Open API未配置",
                        freshness="unavailable",
                        note="必须配置授权KIS行情；禁止用指数涨跌推算个股价格。",
                    )
                    for code, name in KOREA_EQUITIES
                ],
                "三星电子、SK海力士：KIS授权行情未配置，明确标记不可用。",
            )
        if self.kis_equity_loader is None:
            return self._with_yahoo_fallback(
                [
                    GlobalQuote(
                        symbol=code,
                        name=name,
                        market="韩国",
                        status="configuration_pending",
                        source="KIS Open API",
                        freshness="unavailable",
                        note="已检测KIS凭证，但授权行情适配器尚未启用。",
                    )
                    for code, name in KOREA_EQUITIES
                ],
                "已配置KIS凭证，但尚未注入KIS行情加载器。",
            )

        try:
            rows = _records(self.kis_equity_loader(code for code, _ in KOREA_EQUITIES))
        except Exception as exc:
            return self._with_yahoo_fallback(
                [
                    GlobalQuote(
                        symbol=code,
                        name=name,
                        market="韩国",
                        status="unavailable",
                        source="KIS Open API",
                        freshness="unavailable",
                        note=f"KIS行情请求失败：{exc.__class__.__name__}",
                    )
                    for code, name in KOREA_EQUITIES
                ],
                f"KIS韩国个股行情暂不可用：{exc.__class__.__name__}",
            )

        output: list[GlobalQuote] = []
        for code, name in KOREA_EQUITIES:
            row = _find_row(rows, (code, name))
            if row is None:
                output.append(GlobalQuote(
                    symbol=code, name=name, market="韩国", status="unavailable",
                    source="KIS Open API", freshness="unavailable", note="KIS本次未返回该标的。",
                ))
                continue
            output.append(_quote_from_row(
                row, symbol=code, name=name, market="韩国", source="KIS Open API",
                freshness="授权实时/延迟状态以KIS响应为准",
            ))
        return self._with_yahoo_fallback(output, "")

    def _with_yahoo_fallback(
        self,
        primary: list[GlobalQuote],
        primary_note: str,
    ) -> tuple[list[GlobalQuote], str]:
        """Fill only missing KIS quotes with an explicitly delayed Yahoo quote."""
        if all(item.status == "ok" for item in primary) or self.yahoo_equity_loader is None:
            return primary, primary_note
        try:
            fallback_rows = _records(
                self.yahoo_equity_loader(YAHOO_KOREA_SYMBOLS.values())
            )
        except Exception as exc:
            note = f"Yahoo Finance只读降级源不可用：{exc.__class__.__name__}"
            return primary, "；".join(value for value in (primary_note, note) if value)

        fallback_by_code: dict[str, GlobalQuote] = {}
        for code, name in KOREA_EQUITIES:
            vendor_symbol = YAHOO_KOREA_SYMBOLS[code]
            row = _find_row(fallback_rows, (vendor_symbol, code, name))
            if row is None:
                continue
            quote = _quote_from_row(
                row,
                symbol=code,
                name=name,
                market="韩国",
                source="Yahoo Finance chart v8（只读降级）",
                freshness="延迟行情；延迟时长以Yahoo Finance上游实际标记为准",
                proxy_description=f"Yahoo代码 {vendor_symbol}",
            )
            if quote.price is None:
                continue
            quote.status = "delayed"
            quote.note = (
                f"KIS实时行情不可用，当前仅展示 {vendor_symbol} "
                "Yahoo Finance只读延迟行情，不得当作实时成交依据。"
            )
            fallback_by_code[code] = quote

        merged = [
            item if item.status == "ok" else fallback_by_code.get(item.symbol, item)
            for item in primary
        ]
        if fallback_by_code:
            yahoo_note = "三星电子、SK海力士使用Yahoo Finance只读延迟降级；KIS仍为首选实时源。"
            return merged, "；".join(value for value in (primary_note, yahoo_note) if value)
        missing_note = "Yahoo Finance本次未返回有效韩国个股价格。"
        return merged, "；".join(value for value in (primary_note, missing_note) if value)


def _akshare_global_indices() -> Any:
    import akshare as ak

    return ak.index_global_spot_em()


def _akshare_us_stocks() -> Any:
    import akshare as ak

    return ak.stock_us_spot_em()


def _akshare_sox_history() -> Any:
    import akshare as ak

    return ak.macro_global_sox_index()


def _yahoo_chart_quotes(symbols: Iterable[str]) -> list[dict[str, Any]]:
    """Read Yahoo chart metadata in parallel; every returned quote stays delayed."""
    import httpx
    from concurrent.futures import ThreadPoolExecutor, as_completed

    normalized_symbols = list(dict.fromkeys(
        str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()
    ))
    headers = {"User-Agent": "Mozilla/5.0 trading-discipline-system/3.0"}
    with httpx.Client(timeout=8.0, follow_redirects=True, headers=headers) as client:
        def _load(normalized: str) -> dict[str, Any] | None:
            response = client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{normalized}",
                params={"interval": "1m", "range": "1d"},
            )
            response.raise_for_status()
            chart = response.json().get("chart") or {}
            results = chart.get("result") or []
            if not results:
                return None
            meta = results[0].get("meta") or {}
            price = _number(meta.get("regularMarketPrice"))
            previous_close = _number(
                meta.get("chartPreviousClose") or meta.get("previousClose")
            )
            change = price - previous_close if price is not None and previous_close is not None else None
            change_pct = (
                change / previous_close * 100
                if change is not None and previous_close not in (None, 0)
                else None
            )
            market_time = _number(meta.get("regularMarketTime"))
            as_of = (
                datetime.fromtimestamp(market_time, tz=SHANGHAI_TZ).isoformat()
                if market_time is not None
                else None
            )
            return {
                "symbol": normalized,
                "name": meta.get("shortName") or meta.get("longName") or normalized,
                "price": price,
                "previous_close": previous_close,
                "change": change,
                "change_pct": change_pct,
                "open": meta.get("regularMarketOpen"),
                "high": meta.get("regularMarketDayHigh"),
                "low": meta.get("regularMarketDayLow"),
                "volume": meta.get("regularMarketVolume"),
                "timestamp": as_of,
            }

        by_symbol: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(normalized_symbols)))) as executor:
            futures = {executor.submit(_load, symbol): symbol for symbol in normalized_symbols}
            for future in as_completed(futures):
                try:
                    row = future.result()
                except Exception:
                    row = None
                if row is not None:
                    by_symbol[futures[future]] = row
    return [by_symbol[symbol] for symbol in normalized_symbols if symbol in by_symbol]


def _yahoo_korea_equities(symbols: Iterable[str]) -> list[dict[str, Any]]:
    """Compatibility wrapper for the Korean-equity delayed fallback."""
    return _yahoo_chart_quotes(symbols)


def _ensure_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def _records(value: Any) -> list[dict[str, Any]]:
    """Convert a DataFrame, mapping sequence or mapping to plain records."""
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            records = value.to_dict(orient="records")
        except TypeError:
            records = value.to_dict()
        if isinstance(records, list):
            return [dict(item) for item in records if isinstance(item, Mapping)]
        if isinstance(records, Mapping):
            return [dict(records)]
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() not in {"", "--", "-"}:
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("%", "")
        if value in {"", "--", "-", "None", "nan", "NaN"}:
            return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _row_identity(row: Mapping[str, Any]) -> str:
    return " ".join(
        str(_first(row, key) or "")
        for key in ("代码", "symbol", "code", "名称", "name", "股票简称")
    ).upper()


def _find_row(rows: Iterable[Mapping[str, Any]], aliases: Iterable[str]) -> Mapping[str, Any] | None:
    normalized = [str(alias).strip().upper().replace(" ", "") for alias in aliases]
    for row in rows:
        identity = _row_identity(row).replace(" ", "")
        raw_symbol = str(_first(row, "代码", "symbol", "code", "股票代码") or "").upper()
        symbol_tail = raw_symbol.split(".")[-1]
        for alias in normalized:
            if alias and (alias in identity or alias == symbol_tail):
                return row
    return None


def _quote_from_row(
    row: Mapping[str, Any],
    *,
    symbol: str,
    name: str,
    market: str,
    source: str,
    freshness: str,
    theme: str | None = None,
    proxy_description: str | None = None,
) -> GlobalQuote:
    price = _number(_first(row, "最新价", "最新值", "现价", "price", "close", "stck_prpr"))
    status = "ok" if price is not None else "unavailable"
    return GlobalQuote(
        symbol=symbol,
        name=name,
        market=market,
        status=status,
        price=price,
        change=_number(_first(row, "涨跌额", "change", "prdy_vrss")),
        change_pct=_number(_first(row, "涨跌幅", "涨跌率", "change_pct", "rate", "prdy_ctrt")),
        previous_close=_number(_first(row, "昨收价", "昨收", "previous_close", "prev_close")),
        open_price=_number(_first(row, "开盘价", "今开", "open", "stck_oprc")),
        high=_number(_first(row, "最高价", "最高", "high", "stck_hgpr")),
        low=_number(_first(row, "最低价", "最低", "low", "stck_lwpr")),
        volume=_number(_first(row, "成交量", "volume", "acml_vol")),
        amount=_number(_first(row, "成交额", "amount", "acml_tr_pbmn")),
        as_of=str(_first(row, "时间", "更新时间", "日期", "date", "timestamp", "stck_bsop_date") or "") or None,
        source=source,
        freshness=freshness,
        theme=theme,
        proxy_description=proxy_description,
        note="" if status == "ok" else "数据源未返回有效价格。",
    )


def _delayed_yahoo_quote(
    row: Mapping[str, Any],
    *,
    symbol: str,
    name: str,
    market: str,
    vendor_symbol: str,
    theme: str | None = None,
    proxy_description: str | None = None,
) -> GlobalQuote:
    quote = _quote_from_row(
        row,
        symbol=symbol,
        name=name,
        market=market,
        source="Yahoo Finance chart v8（只读延迟降级）",
        freshness="只读延迟行情；时点以数据源时间戳为准",
        theme=theme,
        proxy_description=proxy_description or f"Yahoo代码 {vendor_symbol}",
    )
    if quote.status == "ok":
        quote.status = "delayed"
        quote.note = "只读延迟行情，仅用于预期修正，不作为实时成交或单独下单依据。"
    return quote


def _merge_yahoo_indices(
    primary: list[GlobalQuote],
    rows: list[dict[str, Any]],
    definitions: Iterable[tuple[str, str, tuple[str, ...]]],
    vendor_symbols: Mapping[str, str],
    *,
    market: str,
) -> list[GlobalQuote]:
    by_symbol = {item.symbol: item for item in primary}
    output = list(primary)
    for symbol, name, _aliases in definitions:
        if symbol in by_symbol:
            continue
        vendor_symbol = vendor_symbols.get(symbol)
        if not vendor_symbol:
            continue
        row = _find_row(rows, (vendor_symbol,))
        if row is None:
            continue
        quote = _delayed_yahoo_quote(
            row,
            symbol=symbol,
            name=name,
            market=market,
            vendor_symbol=vendor_symbol,
        )
        if quote.price is not None:
            output.append(quote)
    return output


def _merge_yahoo_sector_etfs(
    primary: list[GlobalQuote],
    rows: list[dict[str, Any]],
) -> list[GlobalQuote]:
    by_symbol = {item.symbol: item for item in primary}
    for symbol, (theme, description) in US_SECTOR_ETFS.items():
        if symbol in by_symbol:
            continue
        row = _find_row(rows, (symbol,))
        if row is None:
            continue
        quote = _delayed_yahoo_quote(
            row,
            symbol=symbol,
            name=str(_first(row, "name", "名称") or symbol),
            market="美国",
            vendor_symbol=symbol,
            theme=theme,
            proxy_description=description,
        )
        if quote.price is not None and quote.change_pct is not None:
            by_symbol[symbol] = quote
    return sorted(
        by_symbol.values(),
        key=lambda item: item.change_pct if item.change_pct is not None else -math.inf,
        reverse=True,
    )


def _select_defined_indices(
    rows: list[dict[str, Any]],
    definitions: Iterable[tuple[str, str, tuple[str, ...]]],
    *,
    market: str,
    source: str,
    freshness: str,
) -> list[GlobalQuote]:
    output: list[GlobalQuote] = []
    for symbol, name, aliases in definitions:
        row = _find_row(rows, aliases)
        if row is not None:
            output.append(_quote_from_row(
                row, symbol=symbol, name=name, market=market, source=source, freshness=freshness,
            ))
    return output


def _latest_sox_quote(rows: list[dict[str, Any]]) -> GlobalQuote | None:
    if not rows:
        return None
    dated = sorted(rows, key=lambda row: str(_first(row, "日期", "date") or ""))
    row = dated[-1]
    quote = _quote_from_row(
        row,
        symbol="SOX",
        name="费城半导体指数",
        market="美国",
        source="东方财富费城半导体历史指标（AkShare适配）",
        freshness="隔夜收盘参考",
        theme="半导体",
    )
    return quote if quote.status == "ok" else None


def _sector_etf_quotes(rows: list[dict[str, Any]]) -> list[GlobalQuote]:
    output: list[GlobalQuote] = []
    for symbol, (theme, description) in US_SECTOR_ETFS.items():
        row = _find_row(rows, (symbol,))
        if row is None:
            continue
        quote = _quote_from_row(
            row,
            symbol=symbol,
            name=str(_first(row, "名称", "name") or symbol),
            market="美国",
            source="东方财富美股行情（AkShare适配）",
            freshness="隔夜收盘参考，门户行情可能延迟",
            theme=theme,
            proxy_description=description,
        )
        if quote.status == "ok" and quote.change_pct is not None:
            output.append(quote)
    return sorted(output, key=lambda item: item.change_pct if item.change_pct is not None else -math.inf, reverse=True)


# One process-wide cache is shared by the public route, holding execution and
# AI context builder.  This prevents three independent external refreshes for
# the same five-minute evidence window.
global_market_service = GlobalMarketService(cache_ttl_seconds=300)


__all__ = [
    "GlobalMarketService",
    "GlobalMarketSnapshot",
    "GlobalQuote",
    "KISConfiguration",
    "KOREA_EQUITIES",
    "US_SECTOR_ETFS",
    "global_market_service",
]
