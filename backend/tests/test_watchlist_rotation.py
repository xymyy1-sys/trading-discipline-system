from __future__ import annotations

from datetime import datetime
import json
from types import SimpleNamespace

import pytest

from app.models.trading import DataCaptureSnapshot, WatchlistEntry
from app.schemas.trading import LimitUpGroupOut, LimitUpLadderOut, LimitUpStockOut


def _ladder(trade_date: str, *codes: str) -> LimitUpLadderOut:
    return LimitUpLadderOut(
        source="观察池轮换测试源",
        trade_date=trade_date,
        updated_at=datetime.now(),
        groups=[
            LimitUpGroupOut(
                level=1,
                label="首板",
                stocks=[
                    LimitUpStockOut(
                        code=code,
                        name=f"测试{code[-3:]}",
                        price=10.0,
                        turnover=10.0,
                        sealed_amount=1.0,
                        break_count=0,
                        consecutive_limit_days=1,
                        concepts=["测试题材"],
                    )
                    for code in codes
                ],
            )
        ],
        clusters=[],
        summary=[],
        notes=[],
    )


def _install_market_stubs(monkeypatch, state: dict) -> None:
    from app.services.market_data import MarketDataProvider

    def no_theme(_self):
        raise RuntimeError("theme source deliberately unavailable")

    monkeypatch.setattr(MarketDataProvider, "theme_radar", no_theme)
    monkeypatch.setattr(MarketDataProvider, "limit_up_ladder", lambda _self, *_args: state["ladder"])
    monkeypatch.setattr(MarketDataProvider, "broken_limit_pool", lambda _self, *_args: [])


def test_daily_auto_rotation_preserves_manual_and_does_not_refill_same_day(client, db_session, monkeypatch):
    state = {"ladder": _ladder("2026-07-13", "600101", "600102", "600103")}
    _install_market_stubs(monkeypatch, state)

    added = client.post("/api/watchlist", json={"code": "600999", "name": "永久手动标的"})
    assert added.status_code == 200

    first = client.post("/api/watchlist-recommendations/refresh")
    assert first.status_code == 200
    assert {item["code"] for item in first.json()} == {"600101", "600102", "600103", "600999"}

    persisted = client.get("/api/watchlist-recommendations")
    assert persisted.status_code == 200
    assert persisted.json() == first.json()
    assert db_session.query(DataCaptureSnapshot).filter(
        DataCaptureSnapshot.data_type == "watchlist_recommendation",
    ).count() == 4

    removed = client.delete("/api/watchlist/600101?exit_reason=当天手动剔除")
    assert removed.status_code == 200
    state["ladder"] = _ladder("2026-07-13", "600101", "600102", "600103", "600104")

    same_day = client.get("/api/watchlist-recommendations")
    assert same_day.status_code == 200
    same_day_codes = {item["code"] for item in same_day.json()}
    assert same_day_codes == {"600102", "600103", "600999"}
    assert "600104" not in same_day_codes  # deletion must not be back-filled

    state["ladder"] = _ladder("2026-07-14", "600101", "600104")
    next_day = client.post("/api/watchlist-recommendations/refresh")
    assert next_day.status_code == 200
    assert {item["code"] for item in next_day.json()} == {"600101", "600104", "600999"}

    requalified = db_session.query(WatchlistEntry).filter(WatchlistEntry.code == "600101").one()
    assert requalified.source == "auto"
    assert requalified.status == "active"
    assert requalified.snapshot_date == "2026-07-14"
    assert requalified.exit_reason == ""

    stale = db_session.query(WatchlistEntry).filter(WatchlistEntry.code == "600102").one()
    assert stale.status == "expired"
    manual = db_session.query(WatchlistEntry).filter(WatchlistEntry.code == "600999").one()
    assert manual.source == "manual"
    assert manual.status == "active"
    assert manual.snapshot_date == ""


def test_saved_pool_survives_temporary_market_source_failure(client, db_session, monkeypatch):
    from app.services.market_data import MarketDataProvider

    db_session.add_all([
        WatchlistEntry(
            code="600201", name="昨日系统标的", source="auto", status="active",
            snapshot_date="2026-07-13", snapshot_rank=1, category="昨日涨停承接观察",
            entry_reason="昨日盘后评分入选",
        ),
        WatchlistEntry(
            code="600299", name="手动保留标的", source="manual", status="active",
            category="手动自选", entry_reason="用户手动加入观察池",
        ),
    ])
    db_session.commit()

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(MarketDataProvider, "theme_radar", unavailable)
    monkeypatch.setattr(MarketDataProvider, "limit_up_ladder", unavailable)
    monkeypatch.setattr(MarketDataProvider, "broken_limit_pool", unavailable)

    response = client.get("/api/watchlist-recommendations")
    assert response.status_code == 200
    rows = {item["code"]: item for item in response.json()}
    assert set(rows) == {"600201", "600299"}
    assert rows["600201"]["category"] == "昨日涨停承接观察"
    assert rows["600299"]["category"] == "手动自选"


@pytest.mark.parametrize("source", ["unavailable", "东方财富涨停池"])
def test_saved_pool_survives_typed_unavailable_current_pool(client, db_session, monkeypatch, source):
    """The real provider failure shape is an object, not an exception."""
    from app.services.market_data import MarketDataProvider

    db_session.add(WatchlistEntry(
        code="600211", name="上一有效交易日标的", source="auto", status="active",
        snapshot_date="2026-07-13", snapshot_rank=1, category="昨日涨停承接观察",
        entry_reason="上一有效交易日盘后评分入选",
    ))
    db_session.commit()

    unavailable_ladder = LimitUpLadderOut(
        source=source,
        trade_date="2026-07-14",
        updated_at=datetime.now(),
        groups=[], clusters=[], summary=[],
        notes=["涨停池暂不可用: ValueError", "不生成模拟涨停股票"],
    )

    def no_theme(_self):
        raise RuntimeError("theme source deliberately unavailable")

    monkeypatch.setattr(MarketDataProvider, "theme_radar", no_theme)
    monkeypatch.setattr(MarketDataProvider, "limit_up_ladder", lambda _self, *_args: unavailable_ladder)
    monkeypatch.setattr(MarketDataProvider, "broken_limit_pool", lambda _self, *_args: [])

    response = client.get("/api/watchlist-recommendations")

    assert response.status_code == 200
    assert [item["code"] for item in response.json()] == ["600211"]
    saved = db_session.query(WatchlistEntry).filter(WatchlistEntry.code == "600211").one()
    assert saved.status == "active"
    assert saved.snapshot_date == "2026-07-13"
    assert not db_session.query(WatchlistEntry).filter(
        WatchlistEntry.source == "auto",
        WatchlistEntry.snapshot_date == "2026-07-14",
    ).count()


def test_manual_addition_converts_auto_row_into_permanent_entry(client, db_session):
    db_session.add(WatchlistEntry(
        code="600301", name="原系统标的", source="auto", status="excluded",
        snapshot_date="2026-07-13", snapshot_rank=3, category="昨日涨停承接观察",
        exit_reason="当天剔除",
    ))
    db_session.commit()

    response = client.post("/api/watchlist", json={"code": "600301", "name": "改为手动保留"})
    assert response.status_code == 200
    row = db_session.query(WatchlistEntry).filter(WatchlistEntry.code == "600301").one()
    assert row.source == "manual"
    assert row.status == "active"
    assert row.snapshot_date == ""
    assert row.snapshot_rank == 0
    assert row.exit_reason == ""


def test_older_provider_snapshot_never_rotates_persisted_pool_backwards(client, db_session, monkeypatch):
    state = {"ladder": _ladder("2026-07-13", "600401")}
    _install_market_stubs(monkeypatch, state)
    db_session.add(WatchlistEntry(
        code="600499", name="较新盘后标的", source="auto", status="active",
        snapshot_date="2026-07-14", snapshot_rank=1, category="昨日涨停承接观察",
        entry_reason="较新盘后快照",
    ))
    db_session.commit()

    response = client.post("/api/watchlist-recommendations/refresh")
    assert response.status_code == 200
    assert [item["code"] for item in response.json()] == ["600499"]
    current = db_session.query(WatchlistEntry).filter(WatchlistEntry.code == "600499").one()
    assert current.status == "active"
    assert current.snapshot_date == "2026-07-14"


def test_manual_names_are_not_dropped_by_automatic_ten_expectation_cap(db_session, monkeypatch):
    from app.api.routes import stocks
    from app.models.trading import ExpectationSnapshot
    from app.services.next_day_expectations import generate_next_day_expectations

    recommendations = [
        SimpleNamespace(
            code=f"60{index:04d}", name=f"自动{index}", category="昨日涨停承接观察",
            score=80, theme="测试题材", limit_quality="封板稳定", reasons=[],
        )
        for index in range(10)
    ]
    recommendations.append(SimpleNamespace(
        code="600999", name="手动第十一只", category="手动自选",
        score=85, theme="手动观察", limit_quality="等待验证", reasons=[],
    ))
    monkeypatch.setattr(stocks, "watchlist_recommendations", lambda _db: recommendations)

    assert generate_next_day_expectations(db_session) == 11
    manual = db_session.query(ExpectationSnapshot).filter(ExpectationSnapshot.code == "600999").one()
    assert manual.base_expectation == "NEUTRAL"
    assert "手动观察池" in manual.evidence_json


def test_completed_trading_day_switches_only_after_close():
    from app.api.routes.stocks import _completed_trading_days

    assert _completed_trading_days(1, datetime(2026, 7, 14, 14, 59)) == ["2026-07-13"]
    assert _completed_trading_days(1, datetime(2026, 7, 14, 15, 0)) == ["2026-07-14"]


def test_refresh_persists_complete_positive_and_negative_evidence(client, db_session, monkeypatch):
    from app.services.market_data import MarketDataProvider

    ladder = LimitUpLadderOut(
        source="东方财富涨停池",
        trade_date="2026-07-14",
        updated_at=datetime(2026, 7, 14, 15, 1),
        groups=[LimitUpGroupOut(
            level=1,
            label="首板",
            stocks=[LimitUpStockOut(
                code="600501",
                name="证据持久化标的",
                price=12.5,
                turnover=35.0,
                sealed_amount=0.8,
                break_count=1,
                consecutive_limit_days=3,
                concepts=["测试题材"],
            )],
        )],
        clusters=[], summary=[], notes=[],
    )
    monkeypatch.setattr(MarketDataProvider, "theme_radar", lambda _self: (_ for _ in ()).throw(RuntimeError("unavailable")))
    monkeypatch.setattr(MarketDataProvider, "limit_up_ladder", lambda _self, *_args: ladder)
    monkeypatch.setattr(MarketDataProvider, "broken_limit_pool", lambda _self, *_args: [])

    refreshed = client.post("/api/watchlist-recommendations/refresh")
    assert refreshed.status_code == 200
    refreshed_row = refreshed.json()[0]
    assert refreshed_row["code"] == "600501"
    assert refreshed_row["score"] > 0
    assert refreshed_row["reasons"]
    assert any("炸板" in value or "换手率" in value for value in refreshed_row["risks"])
    assert refreshed_row["expectation_status"] != "等待建立预期"
    assert refreshed_row["volume_price_status"]
    assert refreshed_row["risk_reward_ratio"] is not None

    persisted = client.get("/api/watchlist-recommendations")
    assert persisted.status_code == 200
    assert persisted.json() == refreshed.json()

    capture = db_session.query(DataCaptureSnapshot).filter(
        DataCaptureSnapshot.data_type == "watchlist_recommendation",
        DataCaptureSnapshot.target_code == "600501",
        DataCaptureSnapshot.trade_date == "2026-07-14",
    ).one()
    payload = json.loads(capture.normalized_value_json)
    assert payload["reasons"] == refreshed_row["reasons"]
    assert payload["risks"] == refreshed_row["risks"]
    assert capture.is_complete is True
    assert capture.status == "ok"
