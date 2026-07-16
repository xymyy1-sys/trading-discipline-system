from app.services.reflexivity import (
    METHODOLOGY_NOTE,
    ReflexivityService,
    analyze_market_reflexivity,
    analyze_stock_reflexivity,
)


def _market(**overrides):
    values = {
        "advance_ratio": 0.52,
        "index_change_pct": -0.4,
        "index_vwap_deviation_pct": 0.2,
        "market_main_net_inflow_yi": -180,
        "main_net_inflow_change_yi": 650,
        "positive_sector_ratio": 0.55,
        "low_rebound_pct": 1.6,
        "high_drawdown_pct": 0.7,
        "volume_ratio_5d": 1.02,
        "limit_up_count": 35,
        "limit_down_count": 20,
    }
    values.update(overrides)
    return values


def _stock(**overrides):
    values = {
        "code": "600879",
        "name": "样本股票",
        "expectation_gap_score": -18,
        "vwap_deviation_pct": -2.5,
        "change_pct": -9.8,
        "low_rebound_pct": 0.3,
        "high_drawdown_pct": 9.5,
        "volume_ratio": 1.5,
        "sector_relative_strength_pct": -3.0,
        "sector_net_inflow_yi": -25,
        "support_distance_pct": -1.0,
        "hard_stop_triggered": False,
    }
    values.update(overrides)
    return values


def _scenario(result, code):
    return next(item for item in result["scenarios"] if item["code"] == code)


def test_market_detects_extreme_no_rebound_liquidation_without_claiming_intent():
    result = analyze_market_reflexivity(_market(
        advance_ratio=0.15,
        index_change_pct=-3.0,
        index_vwap_deviation_pct=-1.2,
        market_main_net_inflow_yi=-1700,
        main_net_inflow_change_yi=-120,
        positive_sector_ratio=0.15,
        low_rebound_pct=0.25,
        high_drawdown_pct=3.2,
        volume_ratio_5d=0.84,
        limit_up_count=29,
        limit_down_count=172,
    ))

    assert result["current_scenario"] == "NO_REBOUND_LIQUIDATION"
    assert result["crowding"]["side"] == "SELL_PRESSURE"
    assert result["crowding"]["score"] >= 75
    assert "等待首次放量回收分时均价" in result["allowed_actions"]
    assert "主力" not in "".join(result["current_evidence"])
    assert "真实意图" in result["methodology_note"]


def test_market_detects_rebound_absorption_and_keeps_counter_evidence():
    result = ReflexivityService.analyze_market(_market(
        index_signal_count=3,
        index_signal_consistency_ratio=2 / 3,
    ))

    assert result["current_scenario"] == "REBOUND_ABSORPTION"
    assert any("资金净流向较前一快照改善" in item for item in result["current_evidence"])
    assert any("仍净流出" in item for item in result["current_counter_evidence"])
    assert any("回踩" in item for item in result["next_validation_points"])
    assert any("主要指数涨跌方向一致率" in item for item in result["current_evidence"])


def test_market_exposes_all_falsifiable_scenario_branches():
    result = analyze_market_reflexivity(_market())

    assert {item["code"] for item in result["scenarios"]} == {
        "REBOUND_ABSORPTION",
        "NO_REBOUND_LIQUIDATION",
        "REBOUND_FAILURE_SUPPLY",
        "UPSIDE_SURPRISE_REPAIR",
    }
    assert all(item["next_validation_points"] for item in result["scenarios"])
    assert all("match_score" in item for item in result["scenarios"])


def test_market_data_gap_forbids_a_deterministic_conclusion():
    result = analyze_market_reflexivity({"advance_ratio": 0.2, "index_change_pct": -2})

    assert result["current_scenario"] == "DATA_GAP"
    assert result["scenario_match_score"] is None
    assert "指数相对分时均价偏离" in result["missing_fields"]
    assert result["current_evidence"] == []
    assert any("确定性" in item for item in result["forbidden_actions"])


def test_market_leading_scenario_requires_its_own_core_evidence():
    payload = _market()
    payload.pop("main_net_inflow_change_yi")

    result = analyze_market_reflexivity(payload)

    assert result["scenarios"][0]["code"] == "REBOUND_ABSORPTION"
    assert result["current_scenario"] == "DATA_GAP"
    assert result["scenario_match_score"] is None
    assert "资金净流向较前一快照变化" in result["missing_fields"]


def test_market_scenario_gate_does_not_require_unrelated_optional_fields():
    result = analyze_market_reflexivity(_market(
        advance_ratio=0.15,
        index_change_pct=-3.0,
        index_vwap_deviation_pct=-1.2,
        low_rebound_pct=0.25,
        high_drawdown_pct=None,
        volume_ratio_5d=None,
        market_main_net_inflow_yi=None,
        main_net_inflow_change_yi=None,
        positive_sector_ratio=None,
    ))

    assert result["current_scenario"] == "NO_REBOUND_LIQUIDATION"


def test_stock_at_intraday_low_without_hard_stop_forbids_emotional_liquidation():
    result = analyze_stock_reflexivity(
        _stock(),
        {"current_scenario": "EXTREME_SHRINK_DECLINE"},
    )

    assert result["current_scenario"] == "NO_REBOUND_LIQUIDATION"
    assert result["market_gate"]["risk_off"] is True
    assert result["market_gate"]["new_position_allowed"] is False
    assert any("日内低点附近情绪化清仓" in item for item in result["forbidden_actions"])
    assert any("反抽验证窗口" in item for item in result["allowed_actions"])


def test_stock_hard_stop_prioritises_predefined_execution_over_waiting():
    result = analyze_stock_reflexivity(
        _stock(hard_stop_triggered=True),
        {"current_scenario": "VOLUME_SELL_OFF"},
    )

    assert result["hard_stop_triggered"] is True
    assert any("执行盘前已定义" in item for item in result["allowed_actions"])
    assert not any("情绪化清仓" in item for item in result["forbidden_actions"])


def test_stock_leading_scenario_requires_its_own_core_evidence():
    payload = _stock()
    payload.pop("vwap_deviation_pct")

    result = analyze_stock_reflexivity(payload)

    assert result["scenarios"][0]["code"] == "NO_REBOUND_LIQUIDATION"
    assert result["current_scenario"] == "DATA_GAP"
    assert result["scenario_match_score"] is None
    assert "股价相对分时均价偏离" in result["missing_fields"]


def test_stock_detects_failed_rebound_supply_instead_of_calling_every_bounce_a_reversal():
    result = analyze_stock_reflexivity(_stock(
        expectation_gap_score=-5,
        vwap_deviation_pct=-1.0,
        change_pct=-3.0,
        low_rebound_pct=4.0,
        high_drawdown_pct=8.0,
        volume_ratio=1.5,
        sector_relative_strength_pct=-2.0,
        support_distance_pct=0.5,
    ))

    assert result["current_scenario"] == "REBOUND_FAILURE_SUPPLY"
    assert any("反弹后仍低于分时均价" in item for item in result["current_evidence"])
    assert any("二次反弹" in item for item in result["next_validation_points"])


def test_stock_detects_upside_surprise_but_market_gate_can_still_forbid_new_position():
    result = ReflexivityService.analyze_stock(
        _stock(
            expectation_gap_score=16,
            vwap_deviation_pct=1.2,
            change_pct=4.0,
            low_rebound_pct=0.5,
            high_drawdown_pct=0.8,
            volume_ratio=1.3,
            sector_relative_strength_pct=2.0,
            support_distance_pct=3.0,
        ),
        {"current_scenario": "VOLUME_SELL_OFF"},
    )

    assert result["current_scenario"] == "UPSIDE_SURPRISE_REPAIR"
    assert result["market_gate"]["new_position_allowed"] is False
    assert any("大盘风险闸门关闭" in item for item in result["forbidden_actions"])
    assert result["methodology_note"] == METHODOLOGY_NOTE


def test_opening_range_can_generate_expectation_gap_without_a_precomputed_score():
    payload = _stock()
    payload.pop("expectation_gap_score")
    payload.update({"actual_open_pct": 1.0, "expected_open_low": -4.0, "expected_open_high": -1.0})

    result = analyze_stock_reflexivity(payload)

    assert "预期差" not in result["missing_fields"]
    surprise = _scenario(result, "UPSIDE_SURPRISE_REPAIR")
    assert any("预期差+" in item.replace(" ", "") for item in surprise["evidence"])


def test_stock_unknown_or_shrink_rotation_market_keeps_expansion_gate_closed():
    for market_scenario in ("UNKNOWN", "DATA_GAP", "SHRINK_ROTATION"):
        result = analyze_stock_reflexivity(
            _stock(
                expectation_gap_score=12,
                vwap_deviation_pct=1.0,
                change_pct=3.0,
                low_rebound_pct=2.0,
                high_drawdown_pct=1.0,
                volume_ratio=1.2,
                sector_relative_strength_pct=1.0,
            ),
            {"current_scenario": market_scenario},
        )
        assert result["market_gate"]["risk_off"] is True
        assert result["market_gate"]["new_position_allowed"] is False


def test_stock_reflexivity_uses_reliable_sector_flow_turning_as_dynamic_evidence():
    result = analyze_stock_reflexivity(_stock(
        expectation_gap_score=-5,
        vwap_deviation_pct=-1.0,
        change_pct=-3.0,
        low_rebound_pct=3.0,
        high_drawdown_pct=6.0,
        volume_ratio=1.4,
        sector_relative_strength_pct=-1.5,
        sector_net_inflow_yi=-20,
        sector_flow_speed_yi_per_minute=-0.5,
        sector_flow_acceleration=-0.08,
        sector_flow_turning="TURN_TO_OUTFLOW",
        sector_flow_kinetics_reliable=True,
        support_distance_pct=0.5,
    ))

    failed_rebound = _scenario(result, "REBOUND_FAILURE_SUPPLY")
    assert any("板块资金仍在边际转弱" in item for item in failed_rebound["evidence"])
    assert result["crowding"]["side"] == "SELL_PRESSURE"
