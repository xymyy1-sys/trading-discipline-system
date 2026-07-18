from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.services.opportunity_radar import (
    OpportunityRadarService,
    STATUS_CONFIRMED,
    STATUS_DECAYED,
    STATUS_INVALIDATED,
    STATUS_PENDING,
)


NOW = datetime(2026, 7, 13, 13, 5, tzinfo=ZoneInfo("Asia/Shanghai"))


def _news(*, published_at=None, sectors=None, title="商业航天重大项目取得进展"):
    return {
        "id": "news-1",
        "title": title,
        "summary": title,
        "source": "东方财富快讯",
        "published_at": (published_at or NOW - timedelta(minutes=5)).isoformat(),
        "sectors": sectors if sectors is not None else ["商业航天"],
        "related_stocks": ["600879"],
        "url": "https://example.test/news-1",
    }


def _flow(**overrides):
    values = {
        "name": "商业航天",
        "provider": "eastmoney",
        "change_pct": 2.3,
        "net_inflow": 12.5,
        "main_inflow": 8.1,
        "sector_price": 1234.5,
        "sector_vwap": 1220.0,
        "sector_vwap_reliable": True,
        "timeline": [
            {"time": "13:00", "value": 8.0},
            {"time": "13:05", "value": 12.5},
        ],
        "leaders": ["航天电子"],
    }
    values.update(overrides)
    return values


def test_news_requires_funds_price_and_vwap_before_confirmation():
    service = OpportunityRadarService(now_provider=lambda: NOW)

    result = service.assess({"items": [_news()]}, {"inflow": [_flow()], "outflow": []}, market_change_pct=-1.0)
    item = result["items"][0]

    assert item["status"] == STATUS_CONFIRMED
    assert item["confirmation_score"] >= 75
    assert item["buy_signal"] is False
    assert "不得单独触发买入" in item["trade_constraint"]
    assert "观察池" in item["action"]
    assert result["as_of"] == result["updated_at"]
    assert result["data_quality"] == "ok"
    assert result["source"]
    assert result["notes"] == [result["discipline"]]


def test_news_alone_is_pending_and_never_becomes_buy_signal():
    service = OpportunityRadarService(now_provider=lambda: NOW)

    result = service.assess([_news()], None)
    item = result["items"][0]

    assert item["status"] == STATUS_PENDING
    assert item["buy_signal"] is False
    assert set(item["missing"]) == {"板块订单流方向估算", "板块涨幅/相对强度", "可靠板块VWAP"}


def test_negative_funds_relative_price_and_vwap_invalidate_news():
    service = OpportunityRadarService(now_provider=lambda: NOW)
    weak = _flow(
        change_pct=-2.0,
        net_inflow=-9.0,
        main_inflow=-5.0,
        sector_price=1190.0,
        sector_vwap=1220.0,
    )

    result = service.assess([_news()], [weak], market_change_pct=-0.5)
    item = result["items"][0]

    assert item["status"] == STATUS_INVALIDATED
    assert item["buy_signal"] is False
    assert any("板块订单流方向净额" in text for text in item["counter_evidence"])
    assert "停止据此开仓" in item["action"]


def test_old_news_decays_even_when_sector_is_currently_strong():
    service = OpportunityRadarService(max_age_minutes=120, now_provider=lambda: NOW)
    old = _news(published_at=NOW - timedelta(minutes=121))

    result = service.assess([old], [_flow()], market_change_pct=-1.0)
    item = result["items"][0]

    assert item["status"] == STATUS_DECAYED
    assert item["age_minutes"] == 121
    assert item["buy_signal"] is False


def test_previously_confirmed_news_decays_after_losing_one_mandatory_confirmation():
    service = OpportunityRadarService(now_provider=lambda: NOW)
    no_vwap = _flow(sector_vwap_reliable=False)

    result = service.assess(
        [_news()],
        [no_vwap],
        market_change_pct=-1.0,
        previous_statuses={"news-1": STATUS_CONFIRMED},
    )

    assert result["items"][0]["status"] == STATUS_DECAYED
    assert "可靠板块VWAP" in result["items"][0]["missing"]


def test_service_accepts_existing_object_shaped_news_and_sector_flow_models():
    service = OpportunityRadarService(now_provider=lambda: NOW)
    item = SimpleNamespace(
        id="object-news",
        title="半导体产业政策发布",
        summary="支持半导体设备发展",
        source="新闻联播",
        published_at=(NOW - timedelta(minutes=3)).isoformat(),
        sectors=["半导体"],
        related_stocks=[],
        url=None,
    )
    flow = SimpleNamespace(
        name="半导体行业",
        display_name="半导体",
        provider="eastmoney",
        change_pct=1.8,
        net_inflow=15.0,
        main_inflow=10.0,
        sector_price=1500.0,
        sector_vwap=1488.0,
        sector_vwap_reliable=True,
        timeline=[],
        leaders=["中芯国际"],
    )
    flow_out = SimpleNamespace(inflow=[flow], outflow=[])

    result = service.assess(SimpleNamespace(items=[item]), flow_out, market_change_pct=-0.2)

    assert result["items"][0]["status"] == STATUS_CONFIRMED
    assert result["items"][0]["primary_sector"] == "半导体"


def test_keyword_mapping_does_not_require_precomputed_news_sectors():
    service = OpportunityRadarService(now_provider=lambda: NOW)
    news = _news(sectors=[], title="午间消息：卫星互联网重大进展")

    result = service.assess([news], [_flow()], market_change_pct=-0.5)

    assert "商业航天" in result["items"][0]["sectors"]
    assert result["items"][0]["status"] == STATUS_CONFIRMED
