from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.reflexivity import (
    ReflexivityService,
    analyze_consensus_high_open_fade,
    analyze_news_impact,
)


NOW = datetime(2026, 7, 15, 10, 20, tzinfo=ZoneInfo("Asia/Shanghai"))


def _consensus(**overrides):
    values = {
        "previous_reversal_confirmed": True,
        "opening_data_real": True,
        "actual_open_pct": 2.2,
        "sector_open_breadth_ratio": 0.75,
        "sector_high_open_count": 12,
        "sector_component_count": 16,
        "post_open_drawdown_pct": 3.1,
        "vwap_deviation_pct": -1.0,
        "vwap_reliable": True,
        "sector_flow_turning": "TURN_TO_OUTFLOW",
        "sector_flow_speed_yi_per_minute": -0.42,
        "sector_flow_acceleration": -0.03,
        "flow_kinetics_reliable": True,
    }
    values.update(overrides)
    return values


def _message(**overrides):
    values = {
        "title": "半导体行业重要事项公告",
        "source": "上海证券交易所公告",
        "url": "https://example.test/announcement/1",
        "published_at": (NOW - timedelta(minutes=15)).isoformat(),
        "verification_level": "FORMAL_ANNOUNCEMENT",
        "sentiment": "利空",
        "sentiment_reason": "公告披露的结构化影响待市场验证",
        "sectors": ["半导体"],
        "related_stocks": ["600584"],
        "holding_related": True,
    }
    values.update(overrides)
    return values


def _negative_market(**overrides):
    values = {
        "fund_direction": "NET_OUTFLOW",
        "flow_turning": "TURN_TO_OUTFLOW",
        "price_direction": "DOWN",
        "vwap_position": "BELOW",
        "captured_at": NOW.isoformat(),
        "fund_reliable": True,
        "price_reliable": True,
        "holding_related": True,
        "consensus_high_open_fade": True,
    }
    values.update(overrides)
    return values


def test_consensus_high_open_fade_needs_full_causal_chain():
    result = analyze_consensus_high_open_fade(_consensus())

    assert result["triggered"] is True
    assert result["code"] == "CONSENSUS_HIGH_OPEN_FADE"
    assert result["status"] == "CONFIRMED"
    assert result["risk_level"] == "HIGH"
    assert any("板块高开广度" in item for item in result["evidence"])
    assert any("分批减仓" in item for item in result["allowed_actions"])
    assert any("自动卖出" in item for item in result["forbidden_actions"])


def test_consensus_rule_returns_data_gap_without_real_open_or_sector_breadth():
    payload = _consensus()
    payload.pop("opening_data_real")
    payload.pop("sector_open_breadth_ratio")
    payload.pop("sector_high_open_count")
    payload.pop("sector_component_count")

    result = ReflexivityService.analyze_consensus_open(payload)

    assert result["triggered"] is False
    assert result["status"] == "DATA_GAP"
    assert result["score"] is None
    assert "真实集合竞价/开盘数据" in result["missing_fields"]
    assert "板块高开广度或多只成分股高开" in result["missing_fields"]


def test_consensus_high_open_does_not_trigger_before_fade_and_weakening():
    result = analyze_consensus_high_open_fade(_consensus(
        post_open_drawdown_pct=0.4,
        vwap_deviation_pct=0.8,
        sector_flow_turning="INFLOW_ACCELERATING",
        sector_flow_speed_yi_per_minute=0.3,
        sector_flow_acceleration=0.02,
    ))

    assert result["triggered"] is False
    assert result["status"] == "NOT_TRIGGERED"
    assert any("尚未出现明显兑现" in item for item in result["counter_evidence"])


def test_consensus_rule_refuses_unreliable_vwap_and_flow_numbers():
    result = analyze_consensus_high_open_fade(_consensus(
        vwap_reliable=False,
        flow_kinetics_reliable=False,
    ))

    assert result["status"] == "DATA_GAP"
    assert result["triggered"] is False
    assert "分时均价或资金转弱证据" in result["missing_fields"]


def test_official_negative_news_only_escalates_when_market_and_crowding_confirm():
    result = analyze_news_impact(_message(), _negative_market(), now=NOW)

    assert result["status"] == "IMPACT_CONFIRMED"
    assert result["claim_level"] == "OFFICIAL"
    assert result["market_validation"] == "CONFIRMED"
    assert result["escalate_to_holding_risk"] is True
    assert result["url"] == "https://example.test/announcement/1"
    assert result["published_at"] == (NOW - timedelta(minutes=15)).isoformat()
    assert result["sectors"] == ["半导体"]
    assert result["related_stocks"] == ["600584"]
    assert "等待承接" in result["action"]
    assert "自动触发卖出" in result["trade_constraint"]


def test_rumour_remains_unverified_even_if_market_moves_in_same_direction():
    message = _message(
        source="社交平台转述",
        verification_level="RUMOR",
        title="市场传闻：某事项或影响半导体",
    )

    result = ReflexivityService.analyze_news(message, _negative_market(), now=NOW)

    assert result["claim_level"] == "RUMOR"
    assert result["status"] == "UNVERIFIED"
    assert result["market_validation"] == "CONFIRMED"
    assert result["escalate_to_holding_risk"] is False
    assert "不得写成事实" in result["action"]


def test_media_attribution_requires_original_attribution_and_metadata():
    result = analyze_news_impact(
        _message(verification_level="MEDIA_ATTRIBUTION", source="财经媒体", attribution=""),
        _negative_market(),
        now=NOW,
    )

    assert result["status"] == "DATA_GAP"
    assert result["escalate_to_holding_risk"] is False
    assert "媒体归因原始出处" in result["missing_fields"]


def test_attributed_media_keeps_media_claim_level_when_impact_is_confirmed():
    result = analyze_news_impact(
        _message(
            verification_level="MEDIA_ATTRIBUTION",
            source="权威财经媒体",
            attribution="公司公开说明",
        ),
        _negative_market(),
        now=NOW,
    )

    assert result["status"] == "IMPACT_CONFIRMED"
    assert result["claim_level"] == "MEDIA_ATTRIBUTION"
    assert any("只验证市场影响，不验证消息内容真伪" in item for item in result["market_evidence"])


def test_news_without_url_or_published_at_is_data_gap_not_a_risk_fact():
    result = analyze_news_impact(
        _message(url=None, published_at=None),
        _negative_market(),
        now=NOW,
    )

    assert result["status"] == "DATA_GAP"
    assert result["freshness"] == "UNKNOWN"
    assert result["escalate_to_holding_risk"] is False
    assert {"原文URL", "发布时间"}.issubset(set(result["missing_fields"]))


def test_stale_news_keeps_traceability_but_cannot_escalate_current_risk():
    result = analyze_news_impact(
        _message(published_at=(NOW - timedelta(minutes=361)).isoformat()),
        _negative_market(),
        now=NOW,
        max_age_minutes=360,
    )

    assert result["status"] == "STALE"
    assert result["freshness"] == "STALE"
    assert result["age_minutes"] == 361
    assert result["escalate_to_holding_risk"] is False


def test_news_cannot_be_validated_by_market_evidence_captured_before_publication():
    result = analyze_news_impact(
        _message(published_at=(NOW - timedelta(minutes=15)).isoformat()),
        _negative_market(captured_at=(NOW - timedelta(minutes=20)).isoformat()),
        now=NOW,
    )

    assert result["status"] == "DATA_GAP"
    assert result["market_validation"] == "DATA_GAP"
    assert result["escalate_to_holding_risk"] is False
    assert "缺少消息发布后的资金量价验证" in result["missing_fields"]


def test_news_cannot_be_validated_by_market_evidence_after_evaluation_time():
    result = analyze_news_impact(
        _message(),
        _negative_market(captured_at=(NOW + timedelta(minutes=1)).isoformat()),
        now=NOW,
    )

    assert result["status"] == "DATA_GAP"
    assert result["market_validation"] == "DATA_GAP"
    assert result["escalate_to_holding_risk"] is False
    assert "资金量价验证时点晚于评估时点" in result["missing_fields"]
