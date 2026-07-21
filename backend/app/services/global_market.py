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
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote as url_quote, urlsplit
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_OFFICIAL_QUALITIES = {"audited", "official", "official_audited"}
_OFFICIAL_METRIC_MAX_AGE = timedelta(hours=120)


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

# Assets and macro series below are deliberately kept outside the US sector
# ranking.  EWY is a country ETF and MU is a single company; mixing either into
# an industry leaderboard would give the UI a plausible-looking but invalid
# comparison.
STRATEGIC_ASSETS: Mapping[str, tuple[str, str, str, tuple[str, ...]]] = {
    "EWY": ("iShares韩国ETF", "美国", "韩国市场ETF", ("半导体", "存储芯片", "消费电子")),
    "MU": ("美光科技", "美国", "半导体存储", ("半导体", "存储芯片")),
}

MACRO_MARKET_DEFINITIONS: Mapping[
    str, tuple[str, str, str, str, tuple[str, ...]]
] = {
    "USDKRW": ("美元兑韩元", "KRW=X", "韩国", "fx_spot", ("全市场", "半导体", "消费电子")),
    "DXY": ("美元指数", "DX-Y.NYB", "美国", "fx_index", ("全市场", "成长风格")),
    "US10Y": ("美国10年期国债收益率代理", "^TNX", "美国", "yield_proxy", ("全市场", "成长风格", "半导体")),
}

EASTMONEY_GLOBAL_SOURCE_URL = "https://quote.eastmoney.com/center/gridlist.html#global_qtzs"
EASTMONEY_US_SOURCE_URL = "https://quote.eastmoney.com/center/gridlist.html#us_stocks"
KIS_SOURCE_URL = "https://apiportal.koreainvestment.com/"


def _yahoo_quote_url(symbol: str) -> str:
    return f"https://finance.yahoo.com/quote/{url_quote(symbol, safe='')}"


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
class OfficialAdapterConfiguration:
    """Optional authenticated/official evidence endpoints.

    There is no stable anonymous endpoint for ETF creations/redemptions,
    Korean investor-class flow, or Korean single-stock leveraged-product
    inventory.  Operators may point these settings at an authorised vendor or
    an internal adapter backed by an official exchange/regulator feed.  When
    absent, the corresponding metric remains explicitly unavailable.
    """

    etf_flow_url: str = ""
    korea_foreign_flow_url: str = ""
    korea_leverage_url: str = ""
    korea_rate_url: str = ""
    bearer_token: str = ""

    @classmethod
    def from_environment(cls) -> "OfficialAdapterConfiguration":
        return cls(
            etf_flow_url=os.getenv("GLOBAL_ETF_FLOW_URL", "").strip(),
            korea_foreign_flow_url=os.getenv("GLOBAL_KOREA_FOREIGN_FLOW_URL", "").strip(),
            korea_leverage_url=os.getenv("GLOBAL_KOREA_LEVERAGE_URL", "").strip(),
            korea_rate_url=os.getenv("GLOBAL_KOREA_RATE_URL", "").strip(),
            bearer_token=os.getenv("GLOBAL_OFFICIAL_ADAPTER_TOKEN", "").strip(),
        )

    def endpoint(self, kind: str) -> str:
        return {
            "etf_flow": self.etf_flow_url,
            "korea_foreign_flow": self.korea_foreign_flow_url,
            "korea_leverage": self.korea_leverage_url,
            "korea_rate": self.korea_rate_url,
        }.get(kind, "")


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
    source_url: str = ""
    published_at: str | None = None
    observed_at: str | None = None
    related_a_share_sectors: list[str] = field(default_factory=list)
    metric_kind: str = "price_quote"
    data_quality: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GlobalMetric:
    metric_id: str
    name: str
    market: str
    status: str
    value: float | None = None
    change: float | None = None
    change_pct: float | None = None
    direction: str | None = None
    unit: str = ""
    period: str | None = None
    source: str = ""
    source_url: str = ""
    published_at: str | None = None
    observed_at: str | None = None
    related_a_share_sectors: list[str] = field(default_factory=list)
    metric_kind: str = ""
    data_quality: str = "missing"
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
    strategic_assets: list[GlobalQuote] = field(default_factory=list)
    macro_indicators: list[GlobalQuote] = field(default_factory=list)
    etf_flows: list[GlobalMetric] = field(default_factory=list)
    korea_foreign_flows: list[GlobalMetric] = field(default_factory=list)
    korea_leverage_products: list[GlobalMetric] = field(default_factory=list)
    official_rates: list[GlobalMetric] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    quality: str = "missing"
    quote_quality: str = "missing"
    institutional_flow_quality: str = "missing"
    quality_details: dict[str, Any] = field(default_factory=dict)
    kis: dict[str, Any] = field(default_factory=dict)
    official_adapters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        grouped_items = (
            [("korea_index", item) for item in payload["korea_indices"]]
            + [("korea_equity", item) for item in payload["korea_equities"]]
            + [("us_index", item) for item in payload["us_indices"]]
            + [("us_sector_proxy", item) for item in payload["us_sector_rank"]]
            + [("strategic_asset", item) for item in payload["strategic_assets"]]
            + [("macro_indicator", item) for item in payload["macro_indicators"]]
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
OfficialMetricLoader = Callable[[], Any]
_USE_DEFAULT_YAHOO_LOADER = object()


class GlobalMarketService:
    """Load and normalize overseas evidence without fabricating gaps."""

    def __init__(
        self,
        *,
        global_index_loader: Loader | None = None,
        us_stock_loader: Loader | None = None,
        sox_loader: Loader | None = None,
        macro_loader: Loader | None = None,
        kis_equity_loader: KISEquityLoader | None = None,
        yahoo_equity_loader: KISEquityLoader | None | object = _USE_DEFAULT_YAHOO_LOADER,
        kis_config: KISConfiguration | None = None,
        official_adapter_config: OfficialAdapterConfiguration | None = None,
        etf_flow_loader: OfficialMetricLoader | None = None,
        korea_foreign_flow_loader: OfficialMetricLoader | None = None,
        korea_leverage_loader: OfficialMetricLoader | None = None,
        korea_rate_loader: OfficialMetricLoader | None = None,
        cache_ttl_seconds: int = 60,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.global_index_loader = global_index_loader or _akshare_global_indices
        self.us_stock_loader = us_stock_loader or _akshare_us_stocks
        self.sox_loader = sox_loader or _akshare_sox_history
        self.macro_loader = macro_loader or (
            _yahoo_macro_quotes
            if global_index_loader is None and us_stock_loader is None
            else (lambda: [])
        )
        self.kis_equity_loader = kis_equity_loader
        self.yahoo_equity_loader = (
            _yahoo_korea_equities
            if yahoo_equity_loader is _USE_DEFAULT_YAHOO_LOADER
            else yahoo_equity_loader
        )
        self.kis_config = kis_config or KISConfiguration.from_environment()
        self.official_adapter_config = (
            official_adapter_config or OfficialAdapterConfiguration.from_environment()
        )
        self.etf_flow_loader = etf_flow_loader or self._configured_loader("etf_flow")
        self.korea_foreign_flow_loader = (
            korea_foreign_flow_loader or self._configured_loader("korea_foreign_flow")
        )
        self.korea_leverage_loader = (
            korea_leverage_loader or self._configured_loader("korea_leverage")
        )
        self.korea_rate_loader = korea_rate_loader or self._configured_loader("korea_rate")
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.now_provider = now_provider or (lambda: datetime.now(SHANGHAI_TZ))
        self._cached_at = 0.0
        self._cached: GlobalMarketSnapshot | None = None

    def _configured_loader(self, kind: str) -> OfficialMetricLoader | None:
        endpoint = self.official_adapter_config.endpoint(kind)
        if not endpoint:
            return None
        return lambda: _load_configured_official_json(
            endpoint,
            bearer_token=self.official_adapter_config.bearer_token,
        )

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

    def read_cached_snapshot(self) -> dict[str, Any]:
        """Return the process cache without triggering any external loader."""
        if self._cached is not None:
            return self._cached.to_dict()
        generated_at = _ensure_timezone(self.now_provider()).isoformat()
        return GlobalMarketSnapshot(
            generated_at=generated_at,
            quality="missing",
            notes=["尚无外围市场缓存，请点击刷新或等待后台采集。"],
        ).to_dict()

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
            us_rows = []
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

        strategic_assets = _strategic_asset_quotes(us_rows)
        if self.us_stock_loader is _akshare_us_stocks and len(strategic_assets) < len(STRATEGIC_ASSETS):
            try:
                yahoo_strategic_rows = _records(_yahoo_chart_quotes(STRATEGIC_ASSETS.keys()))
                strategic_assets = _merge_yahoo_strategic_assets(
                    strategic_assets,
                    yahoo_strategic_rows,
                )
                if yahoo_strategic_rows:
                    sources.append("Yahoo Finance战略资产（只读延迟降级）")
            except Exception as exc:
                notes.append(f"EWY/美光降级源不可用：{exc.__class__.__name__}")

        try:
            macro_rows = _records(self.macro_loader())
            macro_indicators = _macro_quotes(macro_rows)
            if macro_indicators:
                sources.append("Yahoo Finance宏观市场代理（只读延迟）")
        except Exception as exc:
            macro_indicators = []
            notes.append(f"汇率与利率代理源不可用：{exc.__class__.__name__}")
        macro_indicators = _complete_macro_placeholders(macro_indicators)

        korea_equities, kis_note = self._korea_equities()
        if kis_note:
            notes.append(kis_note)
        if any(item.status == "ok" and item.source == "KIS Open API" for item in korea_equities):
            sources.append("韩国投资证券KIS Open API")
        if any(item.status == "delayed" and item.source.startswith("Yahoo Finance") for item in korea_equities):
            sources.append("Yahoo Finance chart v8（只读延迟降级）")

        observed_at = current.isoformat()
        etf_flows = self._official_metric_group(
            self.etf_flow_loader,
            metric_kind="etf_share_creation_redemption",
            placeholder_id="ETF_FLOW",
            placeholder_name="ETF真实份额与申购赎回",
            market="美国",
            observed_at=observed_at,
            related_sectors=("半导体", "存储芯片", "消费电子"),
        )
        korea_foreign_flows = self._official_metric_group(
            self.korea_foreign_flow_loader,
            metric_kind="korea_foreign_net_flow",
            placeholder_id="KR_FOREIGN_FLOW",
            placeholder_name="韩国外资日净买卖",
            market="韩国",
            observed_at=observed_at,
            related_sectors=("半导体", "存储芯片", "消费电子"),
        )
        korea_leverage_products = self._official_metric_group(
            self.korea_leverage_loader,
            metric_kind="korea_single_stock_leverage_product",
            placeholder_id="KR_SINGLE_STOCK_LEVERAGE",
            placeholder_name="韩国单股杠杆产品规模、成交额、溢价与份额",
            market="韩国",
            observed_at=observed_at,
            related_sectors=("半导体", "存储芯片"),
        )
        official_rates = self._official_metric_group(
            self.korea_rate_loader,
            metric_kind="official_interest_rate",
            placeholder_id="KR_OFFICIAL_RATE",
            placeholder_name="韩国官方利率",
            market="韩国",
            observed_at=observed_at,
            related_sectors=("全市场", "半导体", "成长风格"),
        )

        for quote in (
            *korea_indices,
            *korea_equities,
            *us_indices,
            *us_sector_rank,
            *strategic_assets,
            *macro_indicators,
        ):
            _finalize_quote_traceability(quote, observed_at=observed_at)

        available_groups = sum(
            any(item.status in {"ok", "delayed"} for item in group)
            for group in (korea_indices, us_indices, us_sector_rank)
        )
        korea_semiconductor_available = any(
            item.status in {"ok", "delayed"} for item in korea_equities
        )
        us_semiconductor_available = any(
            item.symbol in {"SOX", "SMH", "SOXX"}
            and item.status in {"ok", "delayed"}
            for item in (*us_indices, *us_sector_rank)
        )
        micron_available = any(
            item.symbol == "MU" and item.status in {"ok", "delayed"}
            for item in strategic_assets
        )
        semiconductor_family_count = sum(
            (korea_semiconductor_available, us_semiconductor_available, micron_available)
        )
        core_semiconductor_available = semiconductor_family_count >= 2
        quote_quality = (
            "ok"
            if available_groups == 3 and core_semiconductor_available
            else "degraded"
            if available_groups or semiconductor_family_count
            else "missing"
        )
        institutional_groups = (
            etf_flows,
            korea_foreign_flows,
            korea_leverage_products,
        )
        institutional_available_count = sum(
            any(item.status == "ok" and item.value is not None for item in group)
            for group in institutional_groups
        )
        institutional_flow_quality = (
            "ok"
            if institutional_available_count == len(institutional_groups)
            else "partial"
            if institutional_available_count
            else "missing"
        )
        # ``data_quality`` is an overall envelope quality.  A complete quote
        # panel must not be advertised as fully OK when institutional-flow
        # evidence is absent.  Consumers that only need prices use
        # ``quote_quality`` explicitly.
        quality = (
            "ok"
            if quote_quality == "ok" and institutional_flow_quality == "ok"
            else "degraded"
            if quote_quality != "missing" or institutional_flow_quality != "missing"
            else "missing"
        )
        if not us_sector_rank:
            notes.append("美股行业表现不可用；不生成模拟行业排行。")
        if not korea_indices:
            notes.append("韩国指数不可用；不以其他亚洲指数代替。")
        if institutional_flow_quality == "missing":
            notes.append(
                "外围价格行情与机构资金证据分开评级：当前ETF真实申赎、韩国外资和杠杆产品均不可用，"
                "总体质量不标记为完整。"
            )
        elif institutional_flow_quality == "partial":
            notes.append(
                "外围机构资金证据仅部分可用；缺失项目保持未知，不以零值或价格走势替代。"
            )
        if not any(item.status == "ok" and item.value is not None for item in official_rates):
            notes.append("韩国官方利率证据不可用；不使用美债收益率代理冒充官方利率。")

        return GlobalMarketSnapshot(
            generated_at=current.isoformat(),
            korea_indices=korea_indices,
            korea_equities=korea_equities,
            us_indices=us_indices,
            us_sector_rank=us_sector_rank,
            strategic_assets=strategic_assets,
            macro_indicators=macro_indicators,
            etf_flows=etf_flows,
            korea_foreign_flows=korea_foreign_flows,
            korea_leverage_products=korea_leverage_products,
            official_rates=official_rates,
            sources=list(dict.fromkeys(sources)),
            notes=list(dict.fromkeys(notes)),
            quality=quality,
            quote_quality=quote_quality,
            institutional_flow_quality=institutional_flow_quality,
            quality_details={
                "base_group_count": available_groups,
                "base_group_total": 3,
                "core_semiconductor_available": core_semiconductor_available,
                "core_semiconductor_family_count": semiconductor_family_count,
                "korea_semiconductor_available": korea_semiconductor_available,
                "us_semiconductor_available": us_semiconductor_available,
                "micron_available": micron_available,
                "macro_available_count": sum(
                    item.status in {"ok", "delayed"} for item in macro_indicators
                ),
                "official_flow_available": any(
                    item.status == "ok"
                    for item in (*etf_flows, *korea_foreign_flows, *korea_leverage_products)
                ),
                "institutional_flow_available_count": institutional_available_count,
                "institutional_flow_group_total": len(institutional_groups),
                "official_rate_available": any(
                    item.status == "ok" and item.value is not None
                    for item in official_rates
                ),
            },
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
            official_adapters={
                "etf_flow": self._adapter_status("etf_flow", self.etf_flow_loader),
                "korea_foreign_flow": self._adapter_status(
                    "korea_foreign_flow", self.korea_foreign_flow_loader
                ),
                "korea_leverage": self._adapter_status(
                    "korea_leverage", self.korea_leverage_loader
                ),
                "korea_rate": self._adapter_status("korea_rate", self.korea_rate_loader),
                "policy": "未配置的授权/官方数据明确标记不可用，不使用价格或指数推算。",
            },
        )

    def _adapter_status(
        self,
        kind: str,
        loader: OfficialMetricLoader | None,
    ) -> dict[str, Any]:
        endpoint = self.official_adapter_config.endpoint(kind)
        return {
            "configured": loader is not None,
            "endpoint_configured": bool(endpoint),
        }

    def _official_metric_group(
        self,
        loader: OfficialMetricLoader | None,
        *,
        metric_kind: str,
        placeholder_id: str,
        placeholder_name: str,
        market: str,
        observed_at: str,
        related_sectors: tuple[str, ...],
    ) -> list[GlobalMetric]:
        endpoint = {
            "etf_share_creation_redemption": self.official_adapter_config.etf_flow_url,
            "korea_foreign_net_flow": self.official_adapter_config.korea_foreign_flow_url,
            "korea_single_stock_leverage_product": self.official_adapter_config.korea_leverage_url,
            "official_interest_rate": self.official_adapter_config.korea_rate_url,
        }.get(metric_kind, "")
        if loader is None:
            return [GlobalMetric(
                metric_id=placeholder_id,
                name=placeholder_name,
                market=market,
                status="unavailable",
                source="未配置授权/官方适配器",
                source_url="",
                observed_at=observed_at,
                related_a_share_sectors=list(related_sectors),
                metric_kind=metric_kind,
                data_quality="missing",
                note="没有稳定公开匿名接口；保持不可用，禁止使用价格、成交额或指数反推。",
            )]
        try:
            rows = _records(loader())
        except Exception as exc:
            return [GlobalMetric(
                metric_id=placeholder_id,
                name=placeholder_name,
                market=market,
                status="unavailable",
                source="已配置官方适配器",
                source_url="",
                observed_at=observed_at,
                related_a_share_sectors=list(related_sectors),
                metric_kind=metric_kind,
                data_quality="missing",
                note=f"适配器请求失败：{exc.__class__.__name__}；未生成替代值。",
            )]
        metrics = [
            _global_metric_from_row(
                row,
                default_kind=metric_kind,
                default_market=market,
                default_source_url="",
                observed_at=observed_at,
                default_related_sectors=related_sectors,
            )
            for row in rows
        ]
        usable = [item for item in metrics if item.status == "ok" and item.value is not None]
        if usable:
            return usable
        return [GlobalMetric(
            metric_id=placeholder_id,
            name=placeholder_name,
            market=market,
            status="unavailable",
            source="已配置官方适配器",
            source_url="",
            observed_at=observed_at,
            related_a_share_sectors=list(related_sectors),
            metric_kind=metric_kind,
            data_quality="missing",
            note="适配器未返回含有效数值和来源时间的数据；未生成替代值。",
        )]

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
                data_quality="ok",
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


def _yahoo_macro_quotes() -> list[dict[str, Any]]:
    return _yahoo_chart_quotes(
        definition[1] for definition in MACRO_MARKET_DEFINITIONS.values()
    )


def _load_configured_official_json(
    endpoint: str,
    *,
    bearer_token: str = "",
) -> Any:
    """Read a configured authorised adapter; no domain is silently assumed."""
    import httpx

    try:
        parsed = urlsplit(endpoint)
    except ValueError as exc:
        raise ValueError("official adapter endpoint is invalid") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("official adapter endpoint must use HTTPS")
    headers = {"Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    # Never forward a server-side bearer token through a redirect.  Operators
    # must configure the final authorised HTTPS endpoint explicitly.
    response = httpx.get(endpoint, headers=headers, timeout=10.0, follow_redirects=False)
    if response.is_redirect:
        raise ValueError("official adapter redirects are not allowed")
    response.raise_for_status()
    body = response.json()
    if isinstance(body, Mapping):
        for key in ("data", "items", "results", "records"):
            if isinstance(body.get(key), list):
                return body[key]
    return body


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
    source_url: str = "",
    related_a_share_sectors: Iterable[str] = (),
    metric_kind: str = "price_quote",
    data_quality: str | None = None,
) -> GlobalQuote:
    price = _number(_first(row, "最新价", "最新值", "现价", "price", "close", "stck_prpr"))
    status = "ok" if price is not None else "unavailable"
    as_of = str(_first(row, "时间", "更新时间", "日期", "date", "timestamp", "stck_bsop_date") or "") or None
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
        as_of=as_of,
        source=source,
        freshness=freshness,
        theme=theme,
        proxy_description=proxy_description,
        note="" if status == "ok" else "数据源未返回有效价格。",
        source_url=source_url,
        published_at=as_of,
        related_a_share_sectors=list(dict.fromkeys(str(item) for item in related_a_share_sectors if item)),
        metric_kind=metric_kind,
        data_quality=data_quality or ("unknown" if status == "ok" else "missing"),
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
    related_a_share_sectors: Iterable[str] = (),
    metric_kind: str = "price_quote",
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
        source_url=_yahoo_quote_url(vendor_symbol),
        related_a_share_sectors=related_a_share_sectors,
        metric_kind=metric_kind,
        data_quality="delayed",
    )
    if quote.status == "ok":
        quote.status = "delayed"
        quote.note = "只读延迟行情，仅用于预期修正，不作为实时成交或单独下单依据。"
    return quote


def _global_metric_from_row(
    row: Mapping[str, Any],
    *,
    default_kind: str,
    default_market: str,
    default_source_url: str,
    observed_at: str,
    default_related_sectors: Iterable[str],
) -> GlobalMetric:
    metric_id = str(_first(row, "metric_id", "id", "symbol", "code", "代码") or "").strip()
    name = str(_first(row, "name", "名称", "metric_name") or "").strip()
    value = _number(_first(
        row,
        "value",
        "数值",
        "net_flow",
        "net_buy",
        "shares",
        "shares_outstanding",
        "premium_pct",
        "rate",
    ))
    change = _number(_first(row, "change", "变动", "change_value", "delta"))
    change_pct = _number(_first(row, "change_pct", "涨跌幅", "change_percent", "pct_change"))
    raw_direction = str(_first(row, "direction", "方向", "flow_direction", "trend") or "").strip()
    direction_aliases = {
        "in": "inflow",
        "inflow": "inflow",
        "creation": "inflow",
        "buy": "inflow",
        "流入": "inflow",
        "净流入": "inflow",
        "out": "outflow",
        "outflow": "outflow",
        "redemption": "outflow",
        "sell": "outflow",
        "流出": "outflow",
        "净流出": "outflow",
        "up": "up",
        "rising": "up",
        "上升": "up",
        "增加": "up",
        "down": "down",
        "falling": "down",
        "下降": "down",
        "减少": "down",
    }
    direction = direction_aliases.get(raw_direction.lower()) or direction_aliases.get(raw_direction) or None
    raw_published_at = str(
        _first(row, "published_at", "as_of", "date", "日期", "timestamp") or ""
    ).strip()
    published_time = _aware_datetime(raw_published_at)
    observed_time = _aware_datetime(observed_at)
    published_at = published_time.isoformat() if published_time is not None else None
    source_url = str(_first(row, "source_url", "url") or default_source_url or "").strip()
    source = str(_first(row, "source", "来源", "provider") or "").strip()
    supplied_sectors = _first(row, "related_a_share_sectors", "sectors", "关联板块")
    if isinstance(supplied_sectors, str):
        sectors = [item.strip() for item in supplied_sectors.replace("，", ",").split(",") if item.strip()]
    elif isinstance(supplied_sectors, Iterable) and not isinstance(supplied_sectors, (bytes, Mapping)):
        sectors = [str(item).strip() for item in supplied_sectors if str(item).strip()]
    else:
        sectors = list(default_related_sectors)
    quality = str(_first(row, "data_quality", "quality") or "").strip().lower()
    declared_status = str(_first(row, "status") or "ok").lower()
    valid_time = bool(
        published_time is not None
        and observed_time is not None
        and published_time <= observed_time
        and observed_time - published_time <= _OFFICIAL_METRIC_MAX_AGE
    )
    valid_provenance = bool(
        metric_id
        and name
        and source
        and _valid_https_url(source_url)
        and valid_time
    )
    usable_quality = quality in _OFFICIAL_QUALITIES
    status = (
        "ok"
        if value is not None
        and valid_provenance
        and usable_quality
        and declared_status not in {"missing", "unavailable", "invalid", "error"}
        else "unavailable"
    )
    note = str(_first(row, "note", "说明") or "")
    if value is not None and not (valid_provenance and usable_quality):
        note = (
            "适配器数值未通过官方证据契约：必须提供metric_id、name、source、"
            "无凭据HTTPS来源、120小时内且不晚于观测时间的带时区published_at，"
            "并将data_quality标记为audited/official/official_audited。"
        )
        quality = "missing"
    return GlobalMetric(
        metric_id=metric_id,
        name=name,
        market=str(_first(row, "market", "市场") or default_market),
        status=status,
        value=value if status == "ok" else None,
        change=change if status == "ok" else None,
        change_pct=change_pct if status == "ok" else None,
        direction=direction if status == "ok" else None,
        unit=str(_first(row, "unit", "单位") or ""),
        period=str(_first(row, "period", "周期") or "") or None,
        source=source,
        source_url=source_url.split("?", 1)[0],
        published_at=published_at,
        observed_at=observed_at,
        related_a_share_sectors=list(dict.fromkeys(sectors)),
        # The endpoint configuration fixes the semantic family.  A provider
        # row may not relabel an ETF-flow endpoint as a rate or leverage fact.
        metric_kind=default_kind,
        data_quality=quality if status == "ok" else "missing",
        note=note,
    )


def _aware_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _valid_https_url(value: Any) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
    )


def _finalize_quote_traceability(quote: GlobalQuote, *, observed_at: str) -> None:
    quote.observed_at = observed_at
    if quote.published_at is None:
        quote.published_at = quote.as_of
    if not quote.source_url:
        if quote.source.startswith("Yahoo Finance"):
            vendor_symbol = (
                YAHOO_KOREA_SYMBOLS.get(quote.symbol)
                or YAHOO_KOREA_INDEX_SYMBOLS.get(quote.symbol)
                or YAHOO_US_INDEX_SYMBOLS.get(quote.symbol)
                or quote.symbol
            )
            quote.source_url = _yahoo_quote_url(vendor_symbol)
        elif quote.source.startswith("东方财富"):
            quote.source_url = (
                EASTMONEY_US_SOURCE_URL
                if quote.market == "美国"
                else EASTMONEY_GLOBAL_SOURCE_URL
            )
        elif quote.source.startswith("KIS") or quote.source.startswith("韩国投资证券"):
            quote.source_url = KIS_SOURCE_URL
    if not quote.related_a_share_sectors:
        if quote.symbol in {"SOX", "SMH", "SOXX", "MU", "005930", "000660"}:
            quote.related_a_share_sectors = ["半导体", "存储芯片"]
        elif quote.symbol == "EWY" or quote.market == "韩国":
            quote.related_a_share_sectors = ["全市场", "半导体", "消费电子"]
        else:
            quote.related_a_share_sectors = [quote.theme] if quote.theme else ["全市场"]
    if quote.metric_kind == "price_quote":
        if quote.symbol in {"KS11", "KOSPI200", "SPX", "NDX", "DJIA", "SOX"}:
            quote.metric_kind = "index_price"
        elif quote.symbol in US_SECTOR_ETFS:
            quote.metric_kind = "sector_etf_price"
        elif quote.symbol in {code for code, _name in KOREA_EQUITIES}:
            quote.metric_kind = "company_price"
    if quote.status == "unavailable":
        quote.data_quality = "missing"
    elif quote.status == "delayed":
        quote.data_quality = "delayed"
    elif quote.data_quality == "unknown":
        quote.data_quality = (
            "delayed"
            if any(token in quote.freshness for token in ("延迟", "隔夜", "门户"))
            else "ok"
        )


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


def _strategic_asset_quotes(rows: list[dict[str, Any]]) -> list[GlobalQuote]:
    output: list[GlobalQuote] = []
    for symbol, (name, market, description, sectors) in STRATEGIC_ASSETS.items():
        row = _find_row(rows, (symbol,))
        if row is None:
            continue
        quote = _quote_from_row(
            row,
            symbol=symbol,
            name=name,
            market=market,
            source="东方财富美股行情（AkShare适配）",
            freshness="隔夜收盘参考，门户行情可能延迟",
            theme=sectors[0] if sectors else None,
            proxy_description=description,
            source_url=EASTMONEY_US_SOURCE_URL,
            related_a_share_sectors=sectors,
            metric_kind="country_etf_price" if symbol == "EWY" else "company_price",
            data_quality="delayed",
        )
        if quote.status == "ok":
            output.append(quote)
    return output


def _merge_yahoo_strategic_assets(
    primary: list[GlobalQuote],
    rows: list[dict[str, Any]],
) -> list[GlobalQuote]:
    by_symbol = {item.symbol: item for item in primary}
    for symbol, (name, market, description, sectors) in STRATEGIC_ASSETS.items():
        if symbol in by_symbol:
            continue
        row = _find_row(rows, (symbol,))
        if row is None:
            continue
        item = _delayed_yahoo_quote(
            row,
            symbol=symbol,
            name=name,
            market=market,
            vendor_symbol=symbol,
            theme=sectors[0] if sectors else None,
            proxy_description=description,
            related_a_share_sectors=sectors,
            metric_kind="country_etf_price" if symbol == "EWY" else "company_price",
        )
        if item.price is not None:
            by_symbol[symbol] = item
    return [by_symbol[symbol] for symbol in STRATEGIC_ASSETS if symbol in by_symbol]


def _macro_quotes(rows: list[dict[str, Any]]) -> list[GlobalQuote]:
    output: list[GlobalQuote] = []
    for symbol, (name, vendor_symbol, market, metric_kind, sectors) in MACRO_MARKET_DEFINITIONS.items():
        row = _find_row(rows, (vendor_symbol, symbol))
        if row is None:
            continue
        item = _delayed_yahoo_quote(
            row,
            symbol=symbol,
            name=name,
            market=market,
            vendor_symbol=vendor_symbol,
            proxy_description=(
                "美国十年期国债收益率市场代理，不等于美联储政策利率。"
                if symbol == "US10Y"
                else "公开市场延迟代理。"
            ),
            related_a_share_sectors=sectors,
            metric_kind=metric_kind,
        )
        if item.price is not None:
            output.append(item)
    return output


def _complete_macro_placeholders(primary: list[GlobalQuote]) -> list[GlobalQuote]:
    by_symbol = {item.symbol: item for item in primary}
    for symbol, (name, vendor_symbol, market, metric_kind, sectors) in MACRO_MARKET_DEFINITIONS.items():
        if symbol in by_symbol:
            continue
        by_symbol[symbol] = GlobalQuote(
            symbol=symbol,
            name=name,
            market=market,
            status="unavailable",
            source="Yahoo Finance宏观市场代理",
            source_url=_yahoo_quote_url(vendor_symbol),
            freshness="unavailable",
            proxy_description="本次没有返回有效值，不以其他指标替代。",
            related_a_share_sectors=list(sectors),
            metric_kind=metric_kind,
            data_quality="missing",
            note="宏观代理本次不可用；保留空值，禁止以零值代替。",
        )
    return [by_symbol[symbol] for symbol in MACRO_MARKET_DEFINITIONS]


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
    "GlobalMetric",
    "GlobalQuote",
    "KISConfiguration",
    "OfficialAdapterConfiguration",
    "KOREA_EQUITIES",
    "MACRO_MARKET_DEFINITIONS",
    "STRATEGIC_ASSETS",
    "US_SECTOR_ETFS",
    "global_market_service",
]
