from __future__ import annotations

import json
from datetime import datetime

from app.services.entry_gate import evaluate_entry_gate


NOW = datetime(2026, 7, 17, 10, 15, 0)


def _bars(prices: list[float], volumes: list[float] | None = None) -> list[dict]:
    volumes = volumes or [1000 + index * 50 for index in range(len(prices))]
    result = []
    start_minute = 15 - len(prices) + 1
    for index, (price, volume) in enumerate(zip(prices, volumes)):
        result.append(
            {
                "trade_date": NOW.date().isoformat(),
                "time": f"10:{start_minute + index:02d}",
                "price": price,
                "high": price * 1.0005,
                "low": price * 0.9995,
                "volume": volume,
                "amount": price * volume,
            }
        )
    return result


def _volume(*, vwap: float, high: float, volume_ratio: float = 1.1, reliable: bool = True) -> dict:
    return {
        "vwap": vwap,
        "vwap_reliable": reliable,
        "high_price": high,
        "volume_ratio": volume_ratio,
        "data_quality": "realtime",
    }


def _neutral_expectation() -> dict:
    return {"expectation_gap_score": 0, "expectation_result": "MATCHED", "state_transition": "CONSENSUS"}


def _low_consensus() -> dict:
    return {"level": "LOW", "score": 10, "data_complete": True}


def test_blocks_a_straight_line_spike_near_high_and_far_above_vwap():
    prices = [10.00, 10.05, 10.20, 10.45, 10.70, 10.90]
    result = evaluate_entry_gate(
        "600000",
        {"price": prices[-1], "high": 10.91, "age_seconds": 0, "minute_bars": _bars(prices, [1200, 1050, 900, 760, 610, 450])},
        _neutral_expectation(),
        _volume(vwap=10.25, high=10.91, volume_ratio=0.72),
        _low_consensus(),
        {"heat_status": "OVERHEATED", "heat_score": 86},
        {"entry_gate": "OPEN", "risk_level": "LOW"},
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=10,
        now=NOW,
    )

    assert result["decision"] == "BLOCK"
    assert result["hard_blocked"] is True
    assert result["allowed_position_ratio"] == 0
    assert result["pulse_3m"] > 4
    assert result["pulse_5m"] > 8
    assert result["distance_vwap_pct"] > 5
    assert "DIRECT_LINE_SURGE" in result["reason_codes"]
    assert "CHASE_RISK_HARD_BLOCK" in result["reason_codes"]
    assert "SHRINKING_SPIKE" in result["reason_codes"]
    assert result["cooldown_until"] == "2026-07-17T10:20:00"
    json.dumps(result, ensure_ascii=False)


def test_moderate_pulse_waits_for_retest_instead_of_granting_a_buy_point():
    prices = [10.00, 10.01, 10.03, 10.06, 10.10, 10.16]
    result = evaluate_entry_gate(
        "600001",
        {"minute_bars": _bars(prices), "price": prices[-1], "high": 10.17, "age_seconds": 0},
        _neutral_expectation(),
        _volume(vwap=10.08, high=10.17),
        _low_consensus(),
        {"heat_status": "NORMAL"},
        {"entry_gate": "OPEN"},
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=10,
        now=NOW,
    )

    assert result["decision"] == "WAIT_RETEST"
    assert result["hard_blocked"] is False
    assert result["allowed_position_ratio"] == 0
    assert "NO_RETEST_CONFIRMATION" in result["reason_codes"]
    assert any("回踩" in item for item in result["missing_conditions"])
    assert any("5分钟" in item for item in result["recheck_conditions"])


def test_confirmed_vwap_retest_only_allows_a_small_probe_without_full_support():
    prices = [10.00, 10.05, 10.12, 10.24, 10.18, 10.12, 10.17, 10.23]
    bars = _bars(prices)
    bars[5]["low"] = 10.10
    result = evaluate_entry_gate(
        "600002",
        {"minute_bars": bars, "price": prices[-1], "high": 10.30, "age_seconds": 0},
        _neutral_expectation(),
        _volume(vwap=10.15, high=10.30),
        _low_consensus(),
        {"heat_status": "NORMAL"},
        {"entry_gate": "OPEN"},
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=10,
        now=NOW,
    )

    assert result["decision"] == "ALLOW_SMALL"
    assert result["hard_blocked"] is False
    assert result["allowed_position_ratio"] == 5
    assert "NO_RETEST_CONFIRMATION" not in result["reason_codes"]
    assert any("回踩分时均价" in item for item in result["counter_evidence"])


def test_missing_minute_and_vwap_data_is_a_hard_zero_position_gate():
    result = evaluate_entry_gate(
        "600003",
        {"price": 10.1, "age_seconds": 0, "minute_bars": _bars([10.0, 10.1])},
        _neutral_expectation(),
        _volume(vwap=0, high=10.1, reliable=False),
        _low_consensus(),
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=10,
        now=NOW,
    )

    assert result["decision"] == "BLOCK"
    assert result["data_quality"] == "missing"
    assert result["allowed_position_ratio"] == 0
    assert "INSUFFICIENT_MINUTE_BARS" in result["reason_codes"]
    assert "VWAP_UNRELIABLE" in result["reason_codes"]


def test_existing_holding_does_not_bypass_plan_or_mode_for_an_add_order():
    prices = [10.00, 10.05, 10.12, 10.24, 10.18, 10.12, 10.17, 10.23]
    bars = _bars(prices)
    bars[5]["low"] = 10.10
    result = evaluate_entry_gate(
        "600004",
        {"minute_bars": bars, "price": prices[-1], "high": 10.30},
        _neutral_expectation(),
        _volume(vwap=10.15, high=10.30),
        _low_consensus(),
        is_holding=True,
        has_plan=False,
        mode_match=False,
        now=NOW,
    )

    assert result["decision"] == "BLOCK"
    assert result["hard_blocked"] is True
    assert "NO_TRADE_PLAN" in result["reason_codes"]
    assert "OUT_OF_TRADING_MODE" in result["reason_codes"]
    assert any("已有持仓" in item for item in result["evidence"])


def test_oversold_context_alone_never_becomes_permission_to_buy():
    prices = [10.30, 10.25, 10.20, 10.16, 10.14, 10.12]
    result = evaluate_entry_gate(
        "600005",
        {"minute_bars": _bars(prices), "price": prices[-1], "high": 10.31, "age_seconds": 0},
        _neutral_expectation(),
        _volume(vwap=10.20, high=10.31),
        _low_consensus(),
        {"heat_status": "OVERSOLD"},
        {"entry_gate": "OPEN"},
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=10,
        now=NOW,
    )

    assert result["decision"] == "WAIT_RETEST"
    assert result["allowed_position_ratio"] == 0
    assert any("超跌不是买点" in item for item in result["counter_evidence"])


def test_holding_retest_probe_is_smaller_than_a_new_position_probe():
    prices = [10.00, 10.05, 10.12, 10.24, 10.18, 10.12, 10.17, 10.23]
    bars = _bars(prices)
    bars[5]["low"] = 10.10
    result = evaluate_entry_gate(
        "600006",
        {"minute_bars": bars, "price": prices[-1], "high": 10.30, "age_seconds": 0},
        _neutral_expectation(),
        _volume(vwap=10.15, high=10.30),
        _low_consensus(),
        {"heat_status": "NORMAL"},
        {"entry_gate": "OPEN"},
        is_holding=True,
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=3,
        now=NOW,
    )

    assert result["decision"] == "ALLOW_SMALL"
    assert result["allowed_position_ratio"] == 3
    assert any("现有总仓位" in item for item in result["recheck_conditions"])


def test_stale_estimated_minute_data_can_never_be_recomputed_as_realtime_vwap():
    prices = [10.00, 10.05, 10.12, 10.24, 10.18, 10.12, 10.17, 10.23]
    bars = _bars(prices)
    for bar in bars:
        bar["trade_date"] = "2026-07-16"
        bar["amount_estimated"] = True
    result = evaluate_entry_gate(
        "600007",
        {
            "minute_bars": bars,
            "minute_bar_trade_date": "2026-07-16",
            "minute_amount_estimated": True,
            "price": 10.60,
            "high": 10.65,
        },
        _neutral_expectation(),
        _volume(vwap=10.15, high=10.65, reliable=False),
        _low_consensus(),
        {"heat_status": "NORMAL", "crowding_evaluated": True},
        {"entry_gate": "OPEN"},
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=5,
        now=NOW,
    )

    assert result["decision"] == "BLOCK"
    assert result["data_quality"] == "missing"
    assert "STALE_MINUTE_BARS" in result["reason_codes"]
    assert "ESTIMATED_MINUTE_AMOUNT" in result["reason_codes"]
    assert "MINUTE_QUOTE_MISMATCH" in result["reason_codes"]


def test_plan_trigger_and_risk_reward_are_required_and_plan_cap_is_enforced():
    prices = [10.00, 10.05, 10.12, 10.24, 10.18, 10.12, 10.17, 10.23]
    bars = _bars(prices)
    bars[5]["low"] = 10.10
    common = (
        "600008",
        {"minute_bars": bars, "price": prices[-1], "high": 10.30, "age_seconds": 0},
        _neutral_expectation(),
        _volume(vwap=10.15, high=10.30),
        _low_consensus(),
        {"heat_status": "NORMAL", "crowding_evaluated": True},
        {"entry_gate": "OPEN"},
    )
    waiting = evaluate_entry_gate(
        *common,
        has_plan=True,
        mode_match=True,
        plan_triggered=False,
        risk_reward_passed=False,
        plan_position_cap_pct=2.0,
        now=NOW,
    )
    allowed = evaluate_entry_gate(
        *common,
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=2.0,
        now=NOW,
    )

    assert waiting["decision"] == "WAIT_RETEST"
    assert waiting["allowed_position_ratio"] == 0
    assert "PLAN_TRIGGER_NOT_MET" in waiting["reason_codes"]
    assert "RISK_REWARD_NOT_PASSED" in waiting["reason_codes"]
    assert allowed["decision"] == "ALLOW_SMALL"
    assert allowed["allowed_position_ratio"] == 2.0


def test_same_day_but_old_quote_and_minute_tape_are_hard_blocked():
    prices = [10.00, 10.05, 10.12, 10.24, 10.18, 10.12, 10.17, 10.23]
    bars = _bars(prices)
    bars[5]["low"] = 10.10
    result = evaluate_entry_gate(
        "600009",
        {
            "minute_bars": bars,
            "price": prices[-1],
            "high": 10.30,
            "provider_event_at": "2026-07-17T10:15:00",
        },
        _neutral_expectation(),
        _volume(vwap=10.15, high=10.30),
        _low_consensus(),
        {"heat_status": "NORMAL", "crowding_evaluated": True},
        {"entry_gate": "OPEN"},
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=2,
        now=datetime(2026, 7, 17, 10, 20),
    )

    assert result["decision"] == "BLOCK"
    assert "STALE_MINUTE_TAPE" in result["reason_codes"]
    assert "STALE_QUOTE" in result["reason_codes"]


def test_outside_session_and_unknown_plan_or_quote_fields_fail_closed():
    prices = [10.00, 10.05, 10.12, 10.24, 10.18, 10.12, 10.17, 10.23]
    bars = _bars(prices)
    result = evaluate_entry_gate(
        "600010",
        {"minute_bars": bars, "price": prices[-1], "high": 10.30},
        _neutral_expectation(),
        _volume(vwap=10.15, high=10.30),
        _low_consensus(),
        {"heat_status": "NORMAL", "crowding_evaluated": True},
        {"entry_gate": "OPEN"},
        has_plan=True,
        mode_match=True,
        now=datetime(2026, 7, 17, 12, 0),
    )

    assert result["decision"] == "BLOCK"
    assert "OUTSIDE_CONTINUOUS_SESSION" in result["reason_codes"]
    assert "MISSING_QUOTE_TIMESTAMP" in result["reason_codes"]
    assert "PLAN_TRIGGER_NOT_MET" in result["reason_codes"]
    assert "RISK_REWARD_NOT_PASSED" in result["reason_codes"]
    assert "PLAN_POSITION_CAP_MISSING" in result["reason_codes"]


def test_confirmed_sector_distribution_freezes_new_exposure_without_sell_semantics():
    prices = [10.00, 10.05, 10.12, 10.24, 10.18, 10.12, 10.17, 10.23]
    bars = _bars(prices)
    bars[5]["low"] = 10.10
    result = evaluate_entry_gate(
        "600011",
        {"minute_bars": bars, "price": prices[-1], "high": 10.30, "age_seconds": 0},
        _neutral_expectation(),
        _volume(vwap=10.15, high=10.30),
        _low_consensus(),
        {
            "crowding_evaluated": True,
            "distribution_state": "高位派发风险",
            "distribution_risk_level": "HIGH",
            "distribution_confirmation_count": 3,
            "order_flow_exhausted": True,
            "price_response_weak": True,
            "leverage_crowding": False,
        },
        {"entry_gate": "OPEN"},
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=5,
        now=NOW,
    )

    assert result["decision"] == "BLOCK"
    assert result["allowed_position_ratio"] == 0
    assert "SECTOR_DISTRIBUTION_RISK" in result["reason_codes"]
    assert any("不会单独要求已有持仓卖出" in item for item in result["evidence"])
    assert not any("清仓" in item for item in result["evidence"])


def test_margin_crowding_alone_is_watch_only_and_does_not_create_high_risk_gate():
    prices = [10.00, 10.05, 10.12, 10.24, 10.18, 10.12, 10.17, 10.23]
    bars = _bars(prices)
    bars[5]["low"] = 10.10
    result = evaluate_entry_gate(
        "600012",
        {"minute_bars": bars, "price": prices[-1], "high": 10.30, "age_seconds": 0},
        _neutral_expectation(),
        _volume(vwap=10.15, high=10.30),
        _low_consensus(),
        {
            "crowding_evaluated": True,
            "distribution_state": "杠杆追涨观察",
            "distribution_risk_level": "MEDIUM",
            "distribution_confirmation_count": 1,
            "order_flow_exhausted": False,
            "price_response_weak": False,
            "leverage_crowding": True,
        },
        {"entry_gate": "OPEN"},
        has_plan=True,
        mode_match=True,
        plan_triggered=True,
        risk_reward_passed=True,
        plan_position_cap_pct=5,
        now=NOW,
    )

    assert result["hard_blocked"] is False
    assert "SECTOR_DISTRIBUTION_RISK" not in result["reason_codes"]
    assert "SECTOR_DISTRIBUTION_WATCH" in result["reason_codes"]
