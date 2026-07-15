import json
from datetime import datetime, timezone

from app.api.helpers.reflexivity import (
    build_consensus_high_open_fade,
    market_reflexivity_metrics,
    stock_reflexivity_metrics,
)
from app.api.routes import market as market_routes
from app.api.routes import stocks as stock_routes
from app.models.trading import MarketRegimeSnapshot
from app.schemas.trading import MarketRegimeOut, StockDecisionCardOut


def _regime(**overrides) -> MarketRegimeOut:
    values = {
        "id": None,
        "trade_date": "2026-07-14",
        "captured_at": datetime(2026, 7, 14, 10, 35, tzinfo=timezone.utc),
        "source": "test-fixture",
        "freshness_seconds": 0,
        "data_quality": "realtime",
        "coverage_ratio": 0.99,
        "confidence": 0.92,
        "active_stock_count": 5200,
        "up_count": 2900,
        "down_count": 2200,
        "flat_count": 100,
        "limit_up_count": 45,
        "limit_down_count": 20,
        "advance_ratio": 0.56,
        "volume_ratio_5d": 1.05,
        "market_main_net_inflow_yi": -50.0,
        "index_composite_change_pct": 0.2,
        "positive_sector_ratio": 0.55,
        "indices": [{
            "code": "000001",
            "name": "上证指数",
            "current": 3535.0,
            "change_pct": 0.2,
            "open_price": 3502.0,
            "high_price": 3540.0,
            "low_price": 3465.0,
            "prev_close": 3500.0,
            "intraday_vwap": 3500.0,
            "above_vwap": True,
            "high_drawdown_pct": 0.14,
            "low_rebound_pct": 2.02,
            "data_quality": "realtime",
            "source": "fixture",
        }],
        "strongest_sectors": [],
        "weakest_sectors": [],
        "regime_code": "NEUTRAL_ROTATION",
        "regime_name": "中性轮动",
        "risk_level": "中",
        "opportunity_score": 55,
        "loss_score": 45,
        "liquidity_score": 60,
        "allowed_actions": [],
        "forbidden_actions": [],
        "evidence": [],
        "missing_fields": [],
        "notes": [],
    }
    values.update(overrides)
    return MarketRegimeOut.model_validate(values)


def _card(stop_source: str = "", hard_stop_price: float = 0) -> StockDecisionCardOut:
    now = datetime(2026, 7, 14, 10, 35, tzinfo=timezone.utc)
    execution = None
    if stop_source or hard_stop_price:
        execution = {
            "holding_id": 1,
            "code": "600879",
            "name": "航天电子",
            "trade_date": "2026-07-14",
            "state": "STOP_LOSS_WARNING",
            "expectation_state": "WEAKER",
            "volume_price_state": "VWAP_BREAKDOWN",
            "sector_state": "WEAK",
            "current_quantity": 1000,
            "sellable_quantity": 1000,
            "recommended_action": "等待验证",
            "hard_stop_price": hard_stop_price,
            "stop_source": stop_source,
            "updated_at": now,
        }
    return StockDecisionCardOut.model_validate({
        "code": "600879",
        "name": "航天电子",
        "industry": "军工电子",
        "current_price": 9.5,
        "change_pct": -4.2,
        "expectation": {
            "trade_date": "2026-07-14",
            "code": "600879",
            "name": "航天电子",
            "stage": "盘中验证",
            "base_expectation": "修复",
            "expected_open_low": 2.0,
            "expected_open_high": 5.0,
            "actual_open_pct": -1.0,
            "actual_change_pct": -4.2,
            "expectation_gap_score": -18,
            "expectation_result": "INVALID",
            "state_transition": "EXPECTATION_INVALIDATED",
            "created_at": now,
        },
        "volume_price": {
            "trade_date": "2026-07-14",
            "code": "600879",
            "name": "航天电子",
            "stage": "盘中验证",
            "captured_at": now,
            "price": 9.5,
            "change_pct": -4.2,
            "high_price": 10.4,
            "low_price": 9.3,
            "vwap": 9.9,
            "vwap_reliable": True,
            "volume_ratio": 1.35,
            "data_quality": "realtime",
        },
        "execution_state": execution,
        "data_quality": "realtime",
    })


def test_market_reflexivity_endpoint_uses_previous_fund_snapshot(
    client, db_session, monkeypatch
):
    previous_time = datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc)
    db_session.add(MarketRegimeSnapshot(
        trade_date="2026-07-14",
        captured_at=previous_time,
        market_main_net_inflow_yi=-220.0,
    ))
    db_session.commit()
    calls: list[bool] = []

    def fake_regime(db, force_refresh=False):
        calls.append(force_refresh)
        return _regime()

    monkeypatch.setattr(market_routes, "get_market_regime", fake_regime)
    monkeypatch.setattr(
        market_routes.market_provider,
        "sector_opening_breadth",
        lambda **_kwargs: {
            "trade_date": "2026-07-14",
            "data_quality": "missing",
            "sample_count": 0,
        },
    )
    response = client.get("/api/market/reflexivity?force_refresh=true")

    assert response.status_code == 200
    payload = response.json()
    assert calls == [True]
    assert payload["level"] == "MARKET"
    assert payload["current_scenario"] == "REBOUND_ABSORPTION"
    assert any("较前一快照改善" in item for item in payload["current_evidence"])
    assert payload["market_regime_code"] == "NEUTRAL_ROTATION"
    assert payload["consensus_high_open_fade"]["status"] == "DATA_GAP"


def test_market_reflexivity_ignores_a_stale_previous_fund_snapshot(db_session):
    db_session.add(MarketRegimeSnapshot(
        trade_date="2026-07-14",
        captured_at=datetime(2026, 7, 14, 10, 19, tzinfo=timezone.utc),
        market_main_net_inflow_yi=-220.0,
    ))
    db_session.commit()

    metrics = market_reflexivity_metrics(db_session, _regime())

    assert metrics["main_net_inflow_change_yi"] is None


def test_market_reflexivity_aggregates_three_major_indices_and_reports_consistency(db_session):
    regime = _regime(indices=[
        {
            "code": "000001", "name": "上证指数", "current": 3535.0,
            "change_pct": 0.9, "open_price": 3510.0, "prev_close": 3500.0,
            "intraday_vwap": 3520.0, "low_rebound_pct": 1.2,
            "high_drawdown_pct": 0.4,
        },
        {
            "code": "399001", "name": "深证成指", "current": 11100.0,
            "change_pct": -0.3, "open_price": 11150.0, "prev_close": 11200.0,
            "intraday_vwap": 11120.0, "low_rebound_pct": 0.8,
            "high_drawdown_pct": 0.7,
        },
        {
            "code": "399006", "name": "创业板指", "current": 2300.0,
            "change_pct": 0.3, "open_price": 2290.0, "prev_close": 2280.0,
            "intraday_vwap": 2295.0, "low_rebound_pct": 1.0,
            "high_drawdown_pct": 0.5,
        },
    ])

    metrics = market_reflexivity_metrics(db_session, regime)

    assert metrics["index_change_pct"] == 0.3
    assert metrics["low_rebound_pct"] == 1.0
    assert metrics["high_drawdown_pct"] == 0.5333
    assert metrics["index_signal_count"] == 3
    assert metrics["index_signal_consistency_ratio"] == 0.6667


def _add_previous_deep_v_snapshot(db_session) -> None:
    indices = [
        {
            "code": "000001", "name": "上证指数", "current": 101.0,
            "low_price": 98.0, "prev_close": 100.0, "intraday_vwap": 100.0,
            "data_quality": "realtime", "source": "eastmoney",
        },
        {
            "code": "399001", "name": "深证成指", "current": 202.0,
            "low_price": 196.0, "prev_close": 200.0, "intraday_vwap": 200.0,
            "data_quality": "realtime", "source": "eastmoney",
        },
    ]
    db_session.add(MarketRegimeSnapshot(
        trade_date="2026-07-13",
        captured_at=datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc),
        source="eastmoney-index",
        data_quality="realtime",
        indices_json=json.dumps(indices, ensure_ascii=False),
    ))
    db_session.commit()


def _crowded_fade_regime() -> MarketRegimeOut:
    return _regime(indices=[
        {
            "code": "000001", "name": "上证指数", "current": 100.0,
            "change_pct": 0.0, "open_price": 101.0, "high_price": 103.0,
            "low_price": 99.5, "prev_close": 100.0, "intraday_vwap": 101.0,
            "data_quality": "realtime", "source": "eastmoney",
        },
        {
            "code": "399001", "name": "深证成指", "current": 200.0,
            "change_pct": 0.0, "open_price": 202.0, "high_price": 206.0,
            "low_price": 199.0, "prev_close": 200.0, "intraday_vwap": 202.0,
            "data_quality": "realtime", "source": "eastmoney",
        },
    ])


def test_consensus_high_open_fade_uses_real_prior_and_current_market_evidence(db_session):
    _add_previous_deep_v_snapshot(db_session)
    sector_opening = {
        "trade_date": "2026-07-14",
        "updated_at": "2026-07-14T09:31:00+08:00",
        "source": "eastmoney-sector-open",
        "data_quality": "ok",
        "sample_count": 50,
        "sector_high_open_count": 35,
        "sector_component_count": 50,
        "sector_open_breadth_ratio": 0.7,
    }

    result = build_consensus_high_open_fade(
        db_session,
        _crowded_fade_regime(),
        sector_opening,
    )

    assert result["status"] == "CONFIRMED"
    assert result["code"] == "CONSENSUS_HIGH_OPEN_FADE"
    assert result["triggered"] is True
    assert result["input_evidence"]["previous_reversal"]["deep_v_index_count"] == 2
    assert result["input_evidence"]["reliable_vwap_index_count"] == 2


def test_consensus_high_open_fade_returns_data_gap_without_real_sector_breadth(db_session):
    _add_previous_deep_v_snapshot(db_session)

    result = build_consensus_high_open_fade(
        db_session,
        _crowded_fade_regime(),
        {"trade_date": "2026-07-14", "data_quality": "missing", "sample_count": 0},
    )

    assert result["status"] == "DATA_GAP"
    assert result["triggered"] is False
    assert result["missing_fields"]


def test_consensus_high_open_fade_returns_data_gap_without_prior_session(db_session):
    result = build_consensus_high_open_fade(
        db_session,
        _crowded_fade_regime(),
        {
            "trade_date": "2026-07-14", "data_quality": "ok", "sample_count": 50,
            "sector_high_open_count": 35, "sector_component_count": 50,
            "sector_open_breadth_ratio": 0.7,
        },
    )

    assert result["status"] == "DATA_GAP"
    assert result["input_evidence"]["previous_reversal"]["previous_trade_date"] is None


def test_stock_reflexivity_endpoint_accepts_force_refresh_without_network(
    client, monkeypatch
):
    calls: list[bool] = []
    monkeypatch.setattr(stock_routes, "decision_card", lambda db, code: _card())

    def fake_regime(db, force_refresh=False):
        calls.append(force_refresh)
        return _regime()

    monkeypatch.setattr(stock_routes, "get_market_regime", fake_regime)
    response = client.get("/api/stocks/600879/reflexivity?force_refresh=true")

    assert response.status_code == 200
    payload = response.json()
    assert calls == [True]
    assert payload["level"] == "STOCK"
    assert payload["code"] == "600879"
    assert "个股相对板块强弱" in payload["missing_fields"]
    assert payload["hard_stop_triggered"] is False


def test_only_an_explicit_frozen_stop_can_trigger_hard_stop():
    cost_reference = stock_reflexivity_metrics(
        _card(stop_source="cost_reference", hard_stop_price=10.0)
    )
    fallback = stock_reflexivity_metrics(
        _card(stop_source="fallback_candidate", hard_stop_price=10.0)
    )
    explicit = stock_reflexivity_metrics(
        _card(stop_source="next_day_plan+sell_card", hard_stop_price=10.0)
    )

    assert cost_reference["hard_stop_triggered"] is False
    assert fallback["hard_stop_triggered"] is False
    assert explicit["hard_stop_triggered"] is True


def test_missing_volume_price_stays_missing_instead_of_becoming_zero():
    card = _card().model_copy(update={"volume_price": None})
    metrics = stock_reflexivity_metrics(card)

    assert metrics["vwap_deviation_pct"] is None
    assert metrics["low_rebound_pct"] is None
    assert metrics["high_drawdown_pct"] is None
    assert metrics["volume_ratio"] is None
