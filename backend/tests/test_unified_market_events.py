import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api.helpers.decision import _event_in_trade_session
from app.api.routes.holdings import _sse_event_frame, _stream_cursor
from app.core.database import Base
from app.core.trading_clock import shanghai_today
from app.models.trading import Holding, IntradayEvidenceEvent
from app.services.unified_market_events import persist_unified_market_events


NOW = datetime(2026, 7, 16, 10, 30, tzinfo=timezone(timedelta(hours=8)))


def _radar(*, news_status="IMPACT_CONFIRMED", validation="CONFIRMED", claim="OFFICIAL"):
    return {
        "as_of": NOW.isoformat(),
        "items": [{
            "id": "news-600584-1",
            "title": "公司发布风险事项公告",
            "source": "东方财富公告",
            "published_at": (NOW - timedelta(minutes=10)).isoformat(),
            "url": "https://example.test/announcement/600584",
            "related_stocks": ["600584"],
            "primary_sector": "半导体",
            "sectors": ["半导体"],
            "status": "已确认",
            "confirmation_score": 82,
            "evidence": ["消息发布后板块资金转为净流出。", "价格跌破真实分时均价。"],
            "counter_evidence": ["尚未跌破计划硬止损。"],
            "missing": [],
            "action": "等待承接，继续转弱时按既有计划处理。",
            "trade_constraint": "消息不自动触发卖出。",
            "claim_level": claim,
            "news_impact_status": news_status,
            "market_validation": validation,
            "sentiment": "利空",
            "sentiment_reason": "正式公告中的风险事项",
            "buy_signal": False,
        }],
        "intraday_expansion": {
            "as_of": NOW.isoformat(),
            "source": ["东方财富涨停池", "东方财富板块资金"],
            "items": [{
                "sector": "商业航天",
                "status": "增量已确认",
                "confirmation_score": 91,
                "window_minutes": 15,
                "new_limit_up_count": 3,
                "total_limit_up_count": 5,
                "leaders": ["航天甲", "航天乙", "航天丙"],
                "flow_turning": "TURN_TO_INFLOW",
                "flow_speed": 2.4,
                "flow_acceleration": 0.4,
                "net_inflow": 18.5,
                "evidence": ["最近15个交易分钟新增3只涨停。", "板块资金转为流入。"],
                "counter_evidence": ["后排可能瞬时跟风。"],
                "risk": ["禁止追后排。"],
                "action": "加入观察，等待核心个股回踩确认。",
                "invalidation": ["资金重新拐为流出。"],
                "source": ["东方财富涨停池", "东方财富板块资金"],
                "buy_signal": False,
            }],
        },
    }


def test_persists_sector_expansion_and_holding_news_with_full_trace(db_session):
    emitted = persist_unified_market_events(
        db_session, _radar(), {"600584": "长电科技"}, now=NOW,
    )

    assert {row.event_type for row in emitted} == {
        "SECTOR_INCREMENT_CONFIRMED",
        "HOLDING_NEWS_NEGATIVE_IMPACT_CONFIRMED",
    }
    news = next(row for row in emitted if row.scope == "stock")
    assert news.target_code == "600584"
    assert news.target_name == "长电科技"
    assert news.severity == "warning"
    assert news.confirmed is True
    assert news.source == "东方财富公告"
    assert news.source_url == "https://example.test/announcement/600584"
    assert news.source_published_at == datetime(2026, 7, 16, 10, 20)
    assert "尚未跌破计划硬止损" in json.loads(news.counter_evidence_json)[0]
    metadata = json.loads(news.metadata_json)
    assert metadata["claim_level"] == "OFFICIAL"
    assert metadata["market_validation"] == "CONFIRMED"
    assert metadata["trade_constraint"] == "消息不自动触发卖出。"
    sector = next(row for row in emitted if row.scope == "sector")
    assert sector.target_name == "商业航天"
    assert sector.value == 91
    assert "禁止追后排" in "".join(json.loads(sector.counter_evidence_json))


def test_unverified_headline_can_only_create_pending_holding_observation(db_session):
    emitted = persist_unified_market_events(
        db_session,
        _radar(news_status="UNVERIFIED", validation="CONFIRMED", claim="RUMOR"),
        {"600584": "长电科技"},
        now=NOW,
    )

    news = next(row for row in emitted if row.scope == "stock")
    assert news.event_type == "HOLDING_NEWS_PENDING_VALIDATION"
    assert news.severity == "info"
    assert news.confirmed is False
    assert json.loads(news.metadata_json)["claim_level"] == "RUMOR"


def test_news_uses_source_session_and_after_close_collection_is_allowed(db_session):
    stale = _radar()
    stale["items"][0]["published_at"] = (NOW - timedelta(days=1)).isoformat()
    emitted = persist_unified_market_events(
        db_session, stale, {"600584": "长电科技"}, now=NOW,
    )
    assert all(row.scope != "stock" for row in emitted)

    emitted = persist_unified_market_events(
        db_session,
        _radar(),
        {"600584": "长电科技"},
        now=NOW.replace(hour=18),
    )
    assert any(row.target_code == "600584" for row in emitted)


def test_polling_deduplicates_same_state_but_emits_validation_transition(db_session):
    persist_unified_market_events(db_session, _radar(), {"600584": "长电科技"}, now=NOW)
    persist_unified_market_events(
        db_session, _radar(), {"600584": "长电科技"}, now=NOW + timedelta(minutes=1),
    )
    news_rows = db_session.query(IntradayEvidenceEvent).filter(
        IntradayEvidenceEvent.target_code == "600584"
    ).all()
    assert len(news_rows) == 1
    assert news_rows[0].occurrence_count == 2

    transitioned = _radar(news_status="IMPACT_INVALIDATED", validation="INVALIDATED")
    persist_unified_market_events(
        db_session, transitioned, {"600584": "长电科技"}, now=NOW + timedelta(minutes=2),
    )
    news_rows = db_session.query(IntradayEvidenceEvent).filter(
        IntradayEvidenceEvent.target_code == "600584"
    ).order_by(IntradayEvidenceEvent.id).all()
    assert [row.event_type for row in news_rows] == [
        "HOLDING_NEWS_NEGATIVE_IMPACT_CONFIRMED",
        "HOLDING_NEWS_IMPACT_INVALIDATED",
    ]
    assert all(row.state_key for row in news_rows)

    # Returning to A is a new turning point, not another occurrence of the
    # morning A state.  The generation-aware state key must preserve it.
    persist_unified_market_events(
        db_session, _radar(), {"600584": "长电科技"}, now=NOW + timedelta(minutes=3),
    )
    news_rows = db_session.query(IntradayEvidenceEvent).filter(
        IntradayEvidenceEvent.target_code == "600584"
    ).order_by(IntradayEvidenceEvent.id).all()
    assert [row.event_type for row in news_rows] == [
        "HOLDING_NEWS_NEGATIVE_IMPACT_CONFIRMED",
        "HOLDING_NEWS_IMPACT_INVALIDATED",
        "HOLDING_NEWS_NEGATIVE_IMPACT_CONFIRMED",
    ]
    assert len({row.state_key for row in news_rows}) == 3


def test_recent_event_route_exposes_source_link_time_and_counter_evidence(client, db_session):
    session_time = datetime.combine(shanghai_today(), time(10, 30))
    db_session.add(Holding(
        code="600584", name="长电科技", quantity=200, cost_price=100,
        current_price=101, total_asset=200_000,
    ))
    row = IntradayEvidenceEvent(
        trade_date=shanghai_today().isoformat(),
        captured_at=session_time,
        scope="stock",
        target_code="600584",
        target_name="长电科技",
        event_type="HOLDING_NEWS_PENDING_VALIDATION",
        severity="info",
        group_key="news:test",
        evidence_json='["等待发布后的资金量价确认。"]',
        counter_evidence_json='["板块资金尚未转强。"]',
        source="东方财富公告",
        source_url="https://example.test/news",
        source_published_at=session_time - timedelta(minutes=5),
        metadata_json='{"claim_level":"OFFICIAL","market_validation":"PENDING"}',
    )
    db_session.add(row)
    db_session.add(IntradayEvidenceEvent(
        trade_date=shanghai_today().isoformat(), captured_at=session_time, scope="stock",
        target_code="000001", target_name="观察池股票", event_type="VWAP_BROKEN",
        severity="warning", evidence_json='["不应进入持仓驾驶舱"]',
    ))
    db_session.commit()

    response = client.get("/api/intraday-events/recent?limit=10")

    assert response.status_code == 200
    payloads = response.json()
    assert {item["target_code"] for item in payloads} == {"600584"}
    payload = payloads[0]
    assert payload["source"] == "东方财富公告"
    assert payload["source_url"] == "https://example.test/news"
    assert payload["source_published_at"]
    assert payload["counter_evidence"] == ["板块资金尚未转强。"]
    assert payload["metadata"]["market_validation"] == "PENDING"


def test_recent_event_route_tolerates_corrupt_json_and_keeps_market_scope(client, db_session):
    db_session.add(IntradayEvidenceEvent(
        trade_date=shanghai_today().isoformat(), captured_at=datetime.now(), scope="sector",
        target_code="sector:test", target_name="测试板块", event_type="SECTOR_INCREMENT_CONFIRMED",
        severity="info", evidence_json="{broken", counter_evidence_json="not-json",
        metadata_json="[not-a-dict]",
    ))
    db_session.commit()

    response = client.get("/api/intraday-events/recent?limit=10")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["scope"] == "sector"
    assert payload["evidence"] == []
    assert payload["counter_evidence"] == []
    assert payload["metadata"] == {}


def test_sse_cursor_prefers_query_then_header_and_frames_have_ids(db_session):
    header_request = Request({"type": "http", "headers": [(b"last-event-id", b"41")]})
    assert _stream_cursor(header_request, replay=False, last_event_id=None) == 41
    assert _stream_cursor(header_request, replay=False, last_event_id=52) == 52
    empty_request = Request({"type": "http", "headers": []})
    assert _stream_cursor(empty_request, replay=True, last_event_id=None) == 0
    assert _stream_cursor(empty_request, replay=False, last_event_id=None) is None

    row = IntradayEvidenceEvent(
        trade_date=shanghai_today().isoformat(), captured_at=datetime.now(), scope="market",
        target_code="market", target_name="全市场", event_type="SECTOR_INCREMENT_WATCH",
        severity="info", evidence_json="[]",
    )
    db_session.add(row)
    db_session.commit()
    frame = _sse_event_frame(row)
    assert frame.startswith(f"id: {row.id}\nevent: intraday-risk\n")


def test_news_decision_session_uses_publication_time_not_collection_time():
    after_close_capture = SimpleNamespace(
        event_type="HOLDING_NEWS_PENDING_VALIDATION",
        source_published_at=datetime(2026, 7, 16, 10, 15),
        captured_at=datetime(2026, 7, 16, 18, 30),
    )
    old_news_collected_today = SimpleNamespace(
        event_type="HOLDING_NEWS_PENDING_VALIDATION",
        source_published_at=datetime(2026, 7, 15, 10, 15),
        captured_at=datetime(2026, 7, 16, 10, 30),
    )
    assert _event_in_trade_session(after_close_capture, "2026-07-16") is True
    assert _event_in_trade_session(old_news_collected_today, "2026-07-16") is False


def test_concurrent_radar_polls_create_one_row_per_material_state(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'events.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine, autoflush=False)

    def run_once() -> None:
        session = local_session()
        try:
            persist_unified_market_events(session, _radar(), {"600584": "长电科技"}, now=NOW)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _index: run_once(), range(2)))

    session = local_session()
    try:
        rows = session.query(IntradayEvidenceEvent).all()
        assert len(rows) == 2
        assert len({row.state_key for row in rows}) == 2
        assert {row.occurrence_count for row in rows} == {2}
    finally:
        session.close()
        engine.dispose()
