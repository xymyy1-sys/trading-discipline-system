from app.schemas.trading import ExpectationSnapshotOut, VolumePriceSnapshotOut
from app.services.consensus_risk import build_consensus_risk


def test_consensus_risk_detects_high_open_profit_pressure():
    expectation = ExpectationSnapshotOut(
        id=1, trade_date="2026-07-10", code="600584", name="长电科技", stage="open",
        base_expectation="STRONG", expected_open_low=3, expected_open_high=5,
        outperform_threshold=6, underperform_threshold=1, severe_underperform_threshold=0,
        actual_open_pct=7, expectation_gap_score=20, expectation_result="STRONGER",
        state_transition="STRONG_TO_STRONGER", confidence=.8, evidence=[], counter_evidence=[],
        suggestion="观察", created_at="2026-07-10T09:30:00",
    )
    volume = VolumePriceSnapshotOut(
        id=1, trade_date="2026-07-10", code="600584", name="长电科技", stage="open",
        captured_at="2026-07-10T10:00:00", price=38, vwap=39, vwap_source="minute",
        minute_bar_count=30, vwap_reliable=True, price_vs_vwap=-2.5, high_drawdown=7,
        attack_amount=5, pullback_amount=5, pullback_amount_ratio=100, data_quality="realtime",
    )
    result = build_consensus_risk({"price": 38}, expectation, volume, {"return_2d": 14})
    assert result.level == "HIGH"
    assert result.score == 100
    assert any("禁止追涨" in item for item in result.actions)


def test_consensus_risk_refuses_grade_without_real_history():
    expectation = ExpectationSnapshotOut(id=1, trade_date="2026-07-10", code="x", name="x", stage="open", base_expectation="UNKNOWN", created_at="2026-07-10T09:30:00")
    volume = VolumePriceSnapshotOut(id=1, trade_date="2026-07-10", code="x", name="x", stage="open", captured_at="2026-07-10T10:00:00")
    result = build_consensus_risk({}, expectation, volume, {})
    assert result.level == "UNKNOWN"
    assert result.data_complete is False
