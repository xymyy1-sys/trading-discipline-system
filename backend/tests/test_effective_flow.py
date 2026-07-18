from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.services.effective_flow import (
    ABSORPTION_CANDIDATE,
    ATTACK_CONFIRMED,
    DISTRIBUTION_RISK,
    INSUFFICIENT_DATA,
    LIQUIDITY_SHOCK,
    OUTFLOW_CONFIRMED,
    RECOVERY_CANDIDATE,
    analyze_effective_flow as _service_analyze_effective_flow,
)


NOW = datetime(2026, 7, 17, 10, 10)


def analyze_effective_flow(*args, **kwargs):
    kwargs.setdefault("active_flow_source", "provider_tick_direction")
    return _service_analyze_effective_flow(*args, **kwargs)


def _bars(
    prices: list[float],
    *,
    buys: list[float],
    sells: list[float],
    amounts: list[float] | None = None,
) -> list[dict]:
    amounts = amounts or [buy + sell for buy, sell in zip(buys, sells)]
    output = []
    first_minute = 10 - len(prices) + 1
    for index, (price, buy, sell, amount) in enumerate(zip(prices, buys, sells, amounts)):
        output.append(
            {
                "trade_date": "2026-07-17",
                "time": f"10:{first_minute + index:02d}",
                "price": price,
                "volume": amount / price,
                "amount": amount,
                "active_buy_amount": buy,
                "active_sell_amount": sell,
            }
        )
    return output


def test_confirms_effective_attack_and_preserves_history_normalization():
    prices = [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30]
    result = analyze_effective_flow(
        _bars(prices, buys=[80] * 10, sells=[20] * 10),
        now=NOW,
        vwap=10.12,
        vwap_reliable=True,
        data_quality="realtime",
        active_flow_source="eastmoney_tick",
        same_time_signed_flow_history=[80, -100, 150, 200, -300, 450, 500],
    )

    assert result.state == ATTACK_CONFIRMED
    assert result.signed_active_flow == 600
    assert result.buy_ratio == pytest.approx(0.8)
    assert result.directional_persistence == 1
    assert result.vwap_response_pct > 1
    assert result.impact_retention_ratio == 1
    assert result.same_time_flow_percentile == 100
    assert result.confidence >= 75
    assert "不等于允许追涨" in result.discipline
    assert result.active_flow_estimated is False
    json.dumps(result.as_dict(), ensure_ascii=False)


def test_positive_active_flow_without_price_response_is_distribution_risk():
    prices = [10.00, 10.03, 10.05, 10.02, 10.01, 10.00, 9.99, 10.00, 10.00, 10.00]
    result = analyze_effective_flow(
        _bars(prices, buys=[75] * 10, sells=[25] * 10),
        now=NOW,
        vwap=10.01,
        vwap_reliable=True,
    )

    assert result.state == DISTRIBUTION_RISK
    assert result.signed_active_flow > 0
    assert result.price_response_pct == 0
    assert "价格推不动" in result.discipline


def test_strong_v_repair_below_session_vwap_is_not_mislabeled_as_distribution():
    prices = [9.50, 9.58, 9.66, 9.75, 9.84, 9.92, 9.98, 10.00, 10.00, 10.00]
    result = analyze_effective_flow(
        _bars(prices, buys=[80] * 10, sells=[20] * 10),
        now=NOW,
        vwap=10.10,
        vwap_reliable=True,
    )

    assert result.price_response_pct > 5
    assert result.vwap_response_pct < 0
    assert result.impact_retention_ratio == pytest.approx(1)
    assert result.state == RECOVERY_CANDIDATE
    assert "避免在窗口低点附近恐慌卖出" in result.discipline
    assert "禁止追高或逆势补仓" in result.discipline


def test_sell_pressure_that_cannot_push_price_lower_is_only_absorption_candidate():
    prices = [10.00, 9.98, 9.97, 9.96, 9.98, 9.99, 10.00, 10.01, 10.01, 10.02]
    result = analyze_effective_flow(
        _bars(prices, buys=[25] * 10, sells=[75] * 10),
        now=NOW,
        vwap=9.99,
        vwap_reliable=True,
    )

    assert result.state == ABSORPTION_CANDIDATE
    assert result.signed_active_flow < 0
    assert result.price_response_pct > 0
    assert "不能直接推断机构吸筹" in result.discipline
    assert result.confidence <= 65


def test_confirms_effective_outflow_when_price_and_vwap_response_agree():
    prices = [10.30, 10.26, 10.22, 10.18, 10.14, 10.10, 10.06, 10.02, 9.98, 9.94]
    result = analyze_effective_flow(
        _bars(prices, buys=[20] * 10, sells=[80] * 10),
        now=NOW,
        vwap=10.12,
        vwap_reliable=True,
    )

    assert result.state == OUTFLOW_CONFIRMED
    assert result.buy_ratio == pytest.approx(0.2)
    assert result.price_response_pct < -3
    assert result.impact_retention_ratio == 1
    assert "禁止接飞刀" in result.discipline


def test_abrupt_move_with_low_active_coverage_is_liquidity_shock():
    prices = [10.00, 10.01, 10.02, 10.03, 10.04, 10.05, 10.06, 10.07, 10.08, 10.30]
    result = analyze_effective_flow(
        _bars(
            prices,
            buys=[6] * 10,
            sells=[4] * 10,
            amounts=[100] * 10,
        ),
        now=NOW,
        vwap=10.05,
        vwap_reliable=True,
    )

    assert result.state == LIQUIDITY_SHOCK
    assert result.max_one_minute_move_pct > 2
    assert result.active_flow_coverage_ratio == pytest.approx(0.1)
    assert "停止追涨" in result.discipline


@pytest.mark.parametrize(
    ("mutation", "kwargs", "reason"),
    [
        (lambda bars: bars.__setitem__(9, {**bars[9], "trade_date": "2026-07-16"}), {}, "WRONG_TRADE_DATE"),
        (lambda bars: bars.__setitem__(9, {**bars[9], "amount_estimated": True}), {}, "ESTIMATED_MINUTE_AMOUNT"),
        (lambda bars: bars.__setitem__(9, {**bars[9], "active_flow_estimated": True}), {}, "ESTIMATED_ACTIVE_FLOW"),
        (lambda bars: bars[9].pop("active_buy_amount"), {}, "MISSING_ACTIVE_FLOW"),
        (lambda bars: None, {"data_quality": "cached_stale"}, "UNTRUSTED_DATA_QUALITY"),
    ],
)
def test_wrong_date_estimated_missing_or_untrusted_data_fails_closed(mutation, kwargs, reason):
    bars = _bars([10 + index * 0.01 for index in range(10)], buys=[60] * 10, sells=[40] * 10)
    mutation(bars)
    result = analyze_effective_flow(bars, now=NOW, vwap=10.02, vwap_reliable=True, **kwargs)

    assert result.state == INSUFFICIENT_DATA
    assert result.confidence == 0
    assert result.signed_active_flow is None
    assert reason in result.reason_codes
    assert "不生成资金介入" in result.discipline


def test_estimation_flags_keep_active_flow_and_minute_amount_semantics_separate():
    bars = _bars([10 + index * 0.01 for index in range(10)], buys=[60] * 10, sells=[40] * 10)
    bars[-1]["amount_estimated"] = True

    result = analyze_effective_flow(bars, now=NOW, vwap=10.02, vwap_reliable=True)

    assert result.state == INSUFFICIENT_DATA
    assert result.active_flow_estimated is False
    assert result.minute_amount_estimated is True
    assert "ESTIMATED_ACTIVE_FLOW" not in result.reason_codes


def test_stale_and_future_timestamps_fail_closed():
    stale = _bars([10 + index * 0.01 for index in range(10)], buys=[60] * 10, sells=[40] * 10)
    future = _bars([10 + index * 0.01 for index in range(10)], buys=[60] * 10, sells=[40] * 10)
    future[-1]["time"] = "10:11"

    stale_result = analyze_effective_flow(
        stale,
        now=datetime(2026, 7, 17, 10, 20),
        vwap=10.02,
        vwap_reliable=True,
    )
    future_result = analyze_effective_flow(future, now=NOW, vwap=10.02, vwap_reliable=True)

    assert stale_result.state == INSUFFICIENT_DATA
    assert "STALE_MINUTE_TAPE" in stale_result.reason_codes
    assert future_result.state == INSUFFICIENT_DATA
    assert "FUTURE_TIMESTAMP" in future_result.reason_codes


def test_exact_minute_amount_and_volume_can_build_reliable_vwap():
    prices = [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30]
    result = analyze_effective_flow(
        _bars(prices, buys=[80] * 10, sells=[20] * 10),
        now=NOW,
    )

    assert result.state == ATTACK_CONFIRMED
    assert result.vwap_reliable is True
    assert result.vwap_source == "exact_minute_amount_volume"
    assert result.vwap is not None


def test_no_history_does_not_fabricate_a_flow_percentile_or_large_money_claim():
    prices = [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30]
    result = analyze_effective_flow(
        _bars(prices, buys=[80] * 10, sells=[20] * 10),
        now=NOW,
        vwap=10.12,
        vwap_reliable=True,
        same_time_signed_flow_history=[100, 200, 300, 400],
    )

    assert result.state == ATTACK_CONFIRMED
    assert result.same_time_flow_percentile is None
    assert result.normalization_sample_count == 4
    assert result.confidence <= 69
    assert any("不判断成交方向规模是否异常" in item for item in result.counter_evidence)


def test_active_flow_larger_than_total_turnover_is_rejected_as_inconsistent():
    prices = [10 + index * 0.01 for index in range(10)]
    result = analyze_effective_flow(
        _bars(prices, buys=[80] * 10, sells=[20] * 10, amounts=[50] * 10),
        now=NOW,
        vwap=10.04,
        vwap_reliable=True,
    )

    assert result.state == INSUFFICIENT_DATA
    assert "ACTIVE_FLOW_EXCEEDS_TURNOVER" in result.reason_codes


def test_uncovered_older_bars_do_not_invalidate_complete_latest_window():
    older = _bars([9.8 + index * 0.01 for index in range(10)], buys=[50] * 10, sells=[50] * 10)
    for index, row in enumerate(older):
        row["time"] = f"09:{31 + index:02d}"
        row.pop("active_buy_amount")
        row.pop("active_sell_amount")
    latest = _bars(
        [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30],
        buys=[80] * 10,
        sells=[20] * 10,
    )

    result = analyze_effective_flow(
        [*older, *latest],
        now=NOW,
        vwap=10.12,
        vwap_reliable=True,
        data_quality="realtime",
        active_flow_source="provider_tick_direction",
    )

    assert result.state == ATTACK_CONFIRMED
    assert result.bar_count == 20
    assert result.exact_flow_bar_count == 10


def test_truncated_tick_batch_fails_closed_when_it_does_not_cover_window_start():
    bars = _bars(
        [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30],
        buys=[80] * 10,
        sells=[20] * 10,
    )
    for row in bars:
        row.update({"tick_batch_truncated": True, "tick_first_time": "10:05:01"})

    result = analyze_effective_flow(
        bars,
        now=NOW,
        vwap=10.12,
        vwap_reliable=True,
        data_quality="realtime",
    )

    assert result.state == INSUFFICIENT_DATA
    assert "TRUNCATED_TICK_WINDOW" in result.reason_codes
    assert any("窗口成交方向不完整" in item for item in result.evidence)


def test_truncated_tick_batch_can_classify_only_when_latest_window_is_covered():
    bars = _bars(
        [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30],
        buys=[80] * 10,
        sells=[20] * 10,
    )
    for row in bars:
        row.update({"tick_batch_truncated": True, "tick_first_time": "09:31:01"})

    result = analyze_effective_flow(
        bars,
        now=NOW,
        vwap=10.12,
        vwap_reliable=True,
        data_quality="realtime",
    )

    assert result.state == ATTACK_CONFIRMED
    assert any("不外推到更早时段" in item for item in result.counter_evidence)


def test_low_classified_flow_coverage_cannot_be_called_effective_attack():
    prices = [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30]
    result = analyze_effective_flow(
        _bars(prices, buys=[8] * 10, sells=[2] * 10, amounts=[100] * 10),
        now=NOW,
        vwap=10.12,
        vwap_reliable=True,
        data_quality="realtime",
        active_flow_source="provider_tick_direction",
    )

    assert result.state == "INCONCLUSIVE"
    assert result.active_flow_coverage_ratio == pytest.approx(0.1)
    assert result.data_quality == "partial"
    assert "覆盖不足" in result.discipline


def test_sub_half_flow_coverage_remains_unconfirmed():
    prices = [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30]
    result = analyze_effective_flow(
        _bars(prices, buys=[32] * 10, sells=[8] * 10, amounts=[100] * 10),
        now=NOW,
        vwap=10.12,
        vwap_reliable=True,
        data_quality="realtime",
        active_flow_source="provider_tick_direction",
    )

    assert result.state == "INCONCLUSIVE"
    assert result.active_flow_coverage_ratio == pytest.approx(0.4)
    assert any("低于50%" in item for item in result.counter_evidence)


def test_one_directional_minute_among_balanced_bars_is_not_persistent_attack():
    prices = [10.00, 10.01, 10.02, 10.03, 10.04, 10.05, 10.06, 10.07, 10.08, 10.10]
    result = analyze_effective_flow(
        _bars(prices, buys=[500, *([50] * 9)], sells=[0, *([50] * 9)]),
        now=NOW,
        vwap=10.04,
        vwap_reliable=True,
        data_quality="realtime",
        active_flow_source="provider_tick_direction",
    )

    assert result.state == "INCONCLUSIVE"
    assert result.directional_persistence == pytest.approx(0.1)


def test_missing_active_flow_provenance_fails_closed():
    prices = [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30]
    result = _service_analyze_effective_flow(
        _bars(prices, buys=[80] * 10, sells=[20] * 10),
        now=NOW,
        vwap=10.12,
        vwap_reliable=True,
        data_quality="realtime",
    )

    assert result.state == INSUFFICIENT_DATA
    assert "UNTRUSTED_ACTIVE_FLOW_SOURCE" in result.reason_codes
