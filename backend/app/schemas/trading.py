from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HoldingCreate(BaseModel):
    code: str
    name: str
    quantity: int
    cost_price: float
    current_price: float
    total_asset: float = 0
    position_type: str = "盈利趋势仓"
    next_discipline: str = ""


class HoldingUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    quantity: int | None = None
    cost_price: float | None = None
    current_price: float | None = None
    total_asset: float | None = None
    position_type: str | None = None
    next_discipline: str | None = None


class HoldingOut(HoldingCreate):
    id: int
    market_value: float
    profit_amount: float
    profit_ratio: float
    today_profit_amount: float = 0
    today_profit_ratio: float = 0
    position_ratio: float
    stop_loss_price: float
    profit_guard_price: float | None
    price_source: str = "manual"
    price_note: str = ""
    prev_close: float = 0
    change_pct: float = 0
    amount: float = 0
    turnover: float = 0
    open_price: float = 0
    high_price: float = 0
    low_price: float = 0
    sector_flow_status: str = ""
    sector_flow_advice: str = ""
    updated_at: datetime

    class Config:
        from_attributes = True


class HoldingRefreshOut(BaseModel):
    holdings: list[HoldingOut]
    refreshed_at: datetime
    success_count: int
    fallback_count: int
    notes: list[str]
    total_asset: float = 0
    cash_available: float = 0
    total_market_value: float = 0
    total_position_ratio: float = 0
    today_profit_amount: float = 0
    today_profit_ratio: float = 0
    total_profit_amount: float = 0
    total_profit_ratio: float = 0
    today_open_profit_amount: float = 0
    today_realized_profit_amount: float = 0


class HoldingAccountSummaryOut(BaseModel):
    total_asset: float = 0
    cash_available: float = 0
    total_market_value: float = 0
    total_position_ratio: float = 0
    today_profit_amount: float = 0
    today_profit_ratio: float = 0
    today_open_profit_amount: float = 0
    today_realized_profit_amount: float = 0
    total_profit_amount: float = 0
    total_profit_ratio: float = 0
    calculated_at: datetime


class PortfolioExposureItemOut(BaseModel):
    name: str
    market_value: float
    ratio: float
    holding_count: int
    codes: list[str] = Field(default_factory=list)


class PortfolioExposureOut(BaseModel):
    generated_at: datetime
    total_market_value: float
    industries: list[PortfolioExposureItemOut] = Field(default_factory=list)
    themes: list[PortfolioExposureItemOut] = Field(default_factory=list)
    styles: list[PortfolioExposureItemOut] = Field(default_factory=list)
    risk_factors: list[PortfolioExposureItemOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HoldingSyncOut(BaseModel):
    holdings: list[HoldingOut]
    synced_at: datetime
    trade_count: int
    notes: list[str]
    total_asset: float = 0
    cash_available: float = 0
    total_market_value: float = 0
    total_position_ratio: float = 0
    today_profit_amount: float = 0
    today_profit_ratio: float = 0
    total_profit_amount: float = 0
    total_profit_ratio: float = 0
    today_open_profit_amount: float = 0
    today_realized_profit_amount: float = 0


class AccountAssetIn(BaseModel):
    total_asset: float


class AccountAssetOut(BaseModel):
    total_asset: float
    updated_at: datetime | None = None


class IntradayEvidenceEventOut(BaseModel):
    id: int | None = None
    captured_at: datetime
    scope: str
    target_code: str
    target_name: str = ""
    event_type: str
    severity: str
    value: float = 0
    previous_value: float = 0
    priority: int = 0
    group_key: str = ""
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    occurrence_count: int = 1
    confirmed: bool = False
    evidence: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ActionRecommendationOut(BaseModel):
    id: int | None = None
    trade_date: str = ""
    holding_id: int | None = None
    code: str = ""
    name: str = ""
    level: str
    state: str
    action: str
    recommended_ratio: float = 0
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    invalid_conditions: list[str] = Field(default_factory=list)
    recovery_conditions: list[str] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime | None = None
    acknowledged_at: datetime | None = None
    feedback_status: str = ""

    class Config:
        from_attributes = True


class ProfitProtectionSnapshotOut(BaseModel):
    id: int | None = None
    holding_id: int
    code: str
    captured_at: datetime
    current_profit_pct: float = 0
    maximum_profit_pct: float = 0
    profit_drawdown_pct: float = 0
    maximum_price: float = 0
    maximum_profit_at: datetime | None = None
    day_max_profit_pct: float = 0
    day_max_profit_at: datetime | None = None
    protection_level: str = "NONE"
    protection_floor: float = 0
    triggered: bool = False
    recommended_action: str = "继续持有"

    class Config:
        from_attributes = True


class PositionStateHistoryOut(BaseModel):
    id: int | None = None
    holding_id: int
    code: str
    name: str = ""
    trade_date: str
    old_state: str = ""
    new_state: str
    captured_at: datetime
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class PositionExecutionStateOut(BaseModel):
    id: int | None = None
    holding_id: int
    code: str
    name: str
    trade_date: str
    state: str
    expectation_state: str
    volume_price_state: str
    sector_state: str
    current_quantity: int
    sellable_quantity: int
    today_buy_quantity: int = 0
    yesterday_quantity: int = 0
    current_position_ratio: float = 0
    recommended_position_ratio: float = 0
    recommended_action: str
    recommended_reduce_ratio: float = 0
    structure_stop_price: float = 0
    hard_stop_price: float = 0
    stop_source: str = "fallback_candidate"
    stop_source_detail: str = ""
    trailing_stop_price: float = 0
    profit_protection_price: float = 0
    t_eligible: bool = False
    t_type: str = "NO_T"
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    invalid_conditions: list[str] = Field(default_factory=list)
    recovery_conditions: list[str] = Field(default_factory=list)
    events: list[IntradayEvidenceEventOut] = Field(default_factory=list)
    recommendation: ActionRecommendationOut | None = None
    profit_snapshot: ProfitProtectionSnapshotOut | None = None
    state_history: list[PositionStateHistoryOut] = Field(default_factory=list)
    data_quality: str = "manual"
    data_time: str = ""
    updated_at: datetime

    class Config:
        from_attributes = True


class RecommendationFeedbackIn(BaseModel):
    status: str
    reason: str = ""


class RecommendationFeedbackOut(BaseModel):
    id: int
    recommendation_id: int
    status: str
    reason: str
    trade_id: int | None = None
    result: str = "待匹配成交"
    created_at: datetime

    class Config:
        from_attributes = True


class ExpectationSnapshotOut(BaseModel):
    id: int | None = None
    trade_date: str
    code: str
    name: str = ""
    stage: str
    base_expectation: str
    expected_open_low: float = 0
    expected_open_high: float = 0
    outperform_threshold: float = 0
    underperform_threshold: float = 0
    severe_underperform_threshold: float = 0
    actual_open_pct: float = 0
    actual_change_pct: float = 0
    expectation_gap_score: int = 0
    expectation_result: str = "MATCHED"
    state_transition: str = "MATCHED"
    confidence: float = 0
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    suggestion: str = ""
    created_at: datetime

    class Config:
        from_attributes = True


class ExpectationSnapshotIn(BaseModel):
    code: str
    name: str = ""
    stage: str = ""
    base_hint: str = ""
    actual_open_pct: float | None = None
    actual_change_pct: float | None = None
    persist: bool = True


class ExpectationSnapshotUpdate(BaseModel):
    stage: str | None = None
    base_expectation: str | None = None
    expected_open_low: float | None = None
    expected_open_high: float | None = None
    outperform_threshold: float | None = None
    underperform_threshold: float | None = None
    severe_underperform_threshold: float | None = None
    actual_open_pct: float | None = None
    actual_change_pct: float | None = None
    expectation_gap_score: int | None = None
    expectation_result: str | None = None
    state_transition: str | None = None
    confidence: float | None = None
    evidence: list[str] | None = None
    counter_evidence: list[str] | None = None
    suggestion: str | None = None


class ExpectationScenarioOut(BaseModel):
    id: int
    scenario_type: str
    probability: float
    expected_low: float
    expected_high: float
    validation_conditions: list[str] = Field(default_factory=list)
    invalid_conditions: list[str] = Field(default_factory=list)
    action_discipline: str = ""


class ExpectationRevisionOut(BaseModel):
    id: int
    expectation_snapshot_id: int
    previous_revision_id: int | None = None
    version: int
    trade_date: str
    code: str
    name: str = ""
    stage: str
    trigger: str
    base_expectation: str
    expected_open_low: float
    expected_open_high: float
    actual_open_pct: float
    actual_change_pct: float
    expectation_gap_score: int
    expectation_result: str
    state_transition: str
    confidence: float
    volume_price_state: str = ""
    vwap: float = 0
    price_vs_vwap: float = 0
    data_quality: str = "manual"
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    invalid_conditions: list[str] = Field(default_factory=list)
    suggestion: str = ""
    scenarios: list[ExpectationScenarioOut] = Field(default_factory=list)
    created_at: datetime


class ExpectationChainOut(BaseModel):
    code: str
    trade_date: str
    generated_at: datetime
    current_stage: str
    revisions: list[ExpectationRevisionOut] = Field(default_factory=list)


class ExpectationRuleIn(BaseModel):
    script_type: str = "default"
    stage: str = "*"
    base_expectation: str
    display_name: str = ""
    expected_open_low: float
    expected_open_high: float
    outperform_threshold: float
    underperform_threshold: float
    severe_underperform_threshold: float
    enabled: bool = True


class ExpectationRuleOut(ExpectationRuleIn):
    id: int
    updated_at: datetime

    class Config:
        from_attributes = True


class VolumePriceSnapshotOut(BaseModel):
    id: int | None = None
    trade_date: str
    code: str
    name: str = ""
    stage: str
    captured_at: datetime
    price: float = 0
    change_pct: float = 0
    open_price: float = 0
    high_price: float = 0
    low_price: float = 0
    prev_close: float = 0
    volume: float = 0
    amount: float = 0
    estimated_full_day_amount: float = 0
    turnover: float = 0
    turnover_source: str = "unavailable"
    turnover_reliable: bool = False
    float_cap: float = 0
    volume_ratio: float = 0
    vwap: float = 0
    vwap_source: str = "estimated"
    minute_bar_count: int = 0
    vwap_reliable: bool = False
    price_vs_vwap: float = 0
    high_drawdown: float = 0
    active_buy_amount: float = 0
    active_sell_amount: float = 0
    active_flow_source: str = "unavailable"
    active_flow_estimated: bool = False
    ma5: float = 0
    ma10: float = 0
    ma20: float = 0
    return_5d: float = 0
    return_10d: float = 0
    distance_recent_high_pct: float = 0
    historical_volume_ratio: float = 0
    chip_profit_ratio: float = 0
    chip_avg_cost: float = 0
    chip_70_concentration: float = 0
    chip_90_concentration: float = 0
    chip_metrics_estimated: bool = True
    large_order_net_amount: float = 0
    large_order_threshold: float = 0
    attack_efficiency: float = 0
    volume_acceleration: float = 0
    attack_amount: float = 0
    pullback_amount: float = 0
    pullback_amount_ratio: float = 0
    pullback_sell_ratio: float = 0
    pattern: str = "量价中性"
    data_quality: str = "manual"
    data_source: str = ""
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class TTradePlanIn(BaseModel):
    t_type: str = "POSITIVE_T"
    planned_sell_price: float = 0
    planned_sell_quantity: int = 0
    buyback_price_low: float = 0
    buyback_price_high: float = 0
    buyback_conditions: list[str] = Field(default_factory=list)
    cancel_conditions: list[str] = Field(default_factory=list)


class TTradePlanUpdate(BaseModel):
    status: str | None = None
    actual_sell_price: float | None = None
    actual_buyback_price: float | None = None
    actual_quantity: int | None = None
    actual_sell_quantity: int | None = None
    actual_buyback_quantity: int | None = None
    execution_note: str | None = None


class TTradePlanOut(BaseModel):
    id: int | None = None
    holding_id: int
    trade_date: str
    code: str
    name: str
    t_type: str
    planned_sell_price: float = 0
    planned_sell_quantity: int = 0
    buyback_price_low: float = 0
    buyback_price_high: float = 0
    buyback_conditions: list[str] = Field(default_factory=list)
    cancel_conditions: list[str] = Field(default_factory=list)
    status: str = "planned"
    actual_sell_price: float = 0
    actual_buyback_price: float = 0
    actual_quantity: int = 0
    actual_sell_quantity: int = 0
    actual_buyback_quantity: int = 0
    execution_note: str = ""
    cost_reduction: float = 0
    evidence: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TEligibilityOut(BaseModel):
    holding_id: int
    code: str
    name: str
    t_type: str
    eligible: bool
    sellable_quantity: int = 0
    today_buy_quantity: int = 0
    yesterday_quantity: int = 0
    suggested_quantity: int = 0
    suggested_sell_price: float = 0
    buyback_price_low: float = 0
    buyback_price_high: float = 0
    buyback_conditions: list[str] = Field(default_factory=list)
    forbidden_reasons: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    current_action: str = ""


class StockDecisionCardOut(BaseModel):
    code: str
    name: str
    industry: str = ""
    concepts: list[str] = Field(default_factory=list)
    current_price: float = 0
    change_pct: float = 0
    expectation: ExpectationSnapshotOut
    volume_price: VolumePriceSnapshotOut | None = None
    execution_state: PositionExecutionStateOut | None = None
    timeline: list[IntradayEvidenceEventOut] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    t_eligibility: TEligibilityOut | None = None
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    data_quality: str = "manual"
    consensus_risk: "ConsensusRiskOut | None" = None
    minute_chart: list["MinuteChartPoint"] = Field(default_factory=list)


class ConsensusRiskOut(BaseModel):
    level: str = "UNKNOWN"
    score: int = 0
    data_complete: bool = False
    factors: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)


class MinuteChartPoint(BaseModel):
    time: str
    price: float
    vwap: float
    amount: float
    amount_estimated: bool = False


class CandidateOut(BaseModel):
    code: str
    name: str = ""
    pool: str
    score: int
    expectation_result: str = "UNKNOWN"
    volume_price_state: str = "UNKNOWN"
    execution_state: str = ""
    data_quality: str = "missing"
    reasons: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class WatchlistEntryIn(BaseModel):
    code: str
    name: str = ""


class WatchlistEntryOut(BaseModel):
    code: str
    name: str
    status: str
    source: str
    entry_reason: str = ""
    exit_reason: str = ""
    observation_days: int = 0
    converted: bool = False


class WatchlistRecommendationOut(BaseModel):
    code: str
    name: str
    score: int
    tier: str
    theme: str = ""
    role: str = ""
    limit_level: int = 0
    limit_quality: str = ""
    fund_signal: str = ""
    expectation_status: str = "未建立盘前预期"
    volume_price_status: str = "量价待确认"
    expectation_gap: float | None = None
    risk_reward_ratio: float | None = None
    gate_passed: bool = False
    missing_conditions: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    source: str = ""
    category: str = ""
    entry_reason: str = ""
    observation_days: int = 0
    converted: bool = False
    updated_at: datetime | None = None


class AccountRiskIn(BaseModel):
    opening_asset: float | None = None
    current_asset: float | None = None


class AccountRiskOut(BaseModel):
    trade_date: str
    opening_asset: float = 0
    current_asset: float = 0
    daily_profit_ratio: float = 0
    level: str = "UNKNOWN"
    new_positions_allowed: bool = False
    recommended_action: str
    degraded_position_count: int = 0
    stop_loss_count: int = 0
    data_complete: bool = False
    evidence: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class StrategyTemplateIn(BaseModel):
    code: str
    name: str
    category: str = "general"
    market_environment: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    premarket_expectation: list[str] = Field(default_factory=list)
    auction_conditions: list[str] = Field(default_factory=list)
    volume_price_conditions: list[str] = Field(default_factory=list)
    buy_confirmation: list[str] = Field(default_factory=list)
    position_limit: float = 0
    structure_stop: list[str] = Field(default_factory=list)
    invalid_conditions: list[str] = Field(default_factory=list)
    holding_management: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    enabled: bool = True


class StrategyTemplateOut(StrategyTemplateIn):
    id: int
    version: int
    created_at: datetime
    updated_at: datetime


class ReplayFrame(BaseModel):
    timestamp: datetime
    frame_type: str
    state: str = ""
    action: str = ""
    price: float = 0
    vwap: float = 0
    data_quality: str = ""
    evidence: list[str] = Field(default_factory=list)


class ReplayCheckpoint(BaseModel):
    expected_time: str
    expected_signal: str
    matched: bool = False
    matched_time: datetime | None = None


class ReplayReportOut(BaseModel):
    code: str
    name: str = ""
    trade_date: str
    generated_at: datetime
    complete: bool = False
    frames: list[ReplayFrame] = Field(default_factory=list)
    checkpoints: list[ReplayCheckpoint] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)


class DataProviderHealthOut(BaseModel):
    source: str
    data_type: str = ""
    sample_count: int
    success_count: int
    degraded_count: int
    stale_count: int
    missing_count: int = 0
    missing_rate: float = 0
    average_latency_ms: float
    latest_status: str
    latest_at: datetime
    latest_trade_date: str = ""
    trade_date_consistent: bool = True
    degraded_source: str = ""


class DataQualityHealthOut(BaseModel):
    generated_at: datetime
    providers: list[DataProviderHealthOut] = Field(default_factory=list)


class StopLevelsOut(BaseModel):
    holding_id: int
    code: str
    name: str
    structure_stop_price: float = 0
    hard_stop_price: float = 0
    stop_source: str = "fallback_candidate"
    stop_source_detail: str = ""
    trailing_stop_price: float = 0
    profit_protection_price: float = 0
    data_quality: str = "manual"
    evidence: list[str] = Field(default_factory=list)
    invalid_conditions: list[str] = Field(default_factory=list)


class IntradayReviewOut(BaseModel):
    code: str
    name: str = ""
    generated_at: datetime
    latest_action: str = ""
    latest_state: str = ""
    data_quality: str = "manual"
    timeline: list[IntradayEvidenceEventOut] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class CollectionRunOut(BaseModel):
    id: int | None = None
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    trigger: str
    holding_count: int = 0
    snapshot_count: int = 0
    event_count: int = 0
    notes: list[str] = Field(default_factory=list)
    error_message: str = ""


class IntradayCollectorStatusOut(BaseModel):
    enabled: bool
    interval_seconds: int
    running: bool
    queue_depth: int = 0
    open_circuits: list[str] = Field(default_factory=list)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    last_run: CollectionRunOut | None = None


class TimeStopRuleUpdate(BaseModel):
    confirmation_deadline: str | None = None
    below_vwap_minutes: int | None = None
    below_vwap_min_bars: int | None = None
    recent_window_minutes: int | None = None
    failed_limit_reseal_pct: float | None = None
    enabled: bool | None = None


class TimeStopRuleOut(BaseModel):
    id: int | None = None
    script_type: str
    display_name: str
    confirmation_deadline: str
    below_vwap_minutes: int
    below_vwap_min_bars: int
    recent_window_minutes: int
    failed_limit_reseal_pct: float
    enabled: bool = True
    updated_at: datetime

    class Config:
        from_attributes = True


class TradeLogCreate(BaseModel):
    code: str
    name: str
    side: str
    price: float
    quantity: int
    total_asset: float
    cost_price: float
    reason: str
    mode: str = "标准短线模式"
    compliant: bool = True
    human_tags: list[str] = Field(default_factory=list)


class TradeLogUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    side: str | None = None
    price: float | None = None
    quantity: int | None = None
    total_asset: float | None = None
    cost_price: float | None = None
    reason: str | None = None
    mode: str | None = None
    compliant: bool | None = None
    human_tags: list[str] | None = None


class TradeLogOut(BaseModel):
    id: int
    code: str
    name: str
    traded_at: datetime
    side: str
    price: float
    quantity: int
    amount: float
    total_asset: float
    position_ratio: float
    cost_price: float
    stop_loss_price: float
    reason: str
    mode: str
    compliant: bool
    human_tags: list[str]
    review: "TradeReviewOut | None" = None

    class Config:
        from_attributes = True


class TradeReviewOut(BaseModel):
    id: int
    trade_id: int
    code: str
    name: str
    verdict: str
    status: str = "done"
    discipline_score: int
    summary: str
    stock_context: str
    sector_context: str
    market_context: str
    error_message: str = ""
    mistakes: list[str]
    avoid_actions: list[str]
    weakness_tags: list[str]
    created_at: datetime

    class Config:
        from_attributes = True


class GrowthProfileOut(BaseModel):
    trade_count: int
    review_count: int
    dominant_weaknesses: list[str]
    frequent_mistakes: list[str]
    current_focus: str
    improvement_actions: list[str]
    recent_scores: list[int]


class CalibrationIssueOut(BaseModel):
    level: str
    title: str
    detail: str
    action: str
    code: str = ""
    name: str = ""


class PlanDeviationOut(BaseModel):
    plan_id: int
    code: str
    name: str
    plan_date: str
    expectation: str = ""
    execution: str = ""
    deviation: str = ""
    severity: str = "观察"


class FeedbackSummaryOut(BaseModel):
    status: str
    count: int


class CalibrationMetricOut(BaseModel):
    key: str
    label: str
    sample_count: int
    success_count: int = 0
    fail_count: int = 0
    success_rate: float = 0
    average_value: float = 0
    verdict: str = "样本不足"
    evidence: list[str] = Field(default_factory=list)


class CalibrationSuggestionOut(BaseModel):
    level: str
    target: str
    suggestion: str
    reason: str
    sample_count: int = 0


class ReviewCalibrationSummaryOut(BaseModel):
    trade_count: int
    review_count: int
    plan_review_count: int
    missing_plan_review_count: int
    execution_feedback_count: int
    ignored_recommendation_count: int
    pending_review_count: int
    avg_discipline_score: int
    focus: str
    issues: list[CalibrationIssueOut] = Field(default_factory=list)
    recent_plan_deviations: list[PlanDeviationOut] = Field(default_factory=list)
    feedback_summary: list[FeedbackSummaryOut] = Field(default_factory=list)
    model_metrics: list[CalibrationMetricOut] = Field(default_factory=list)
    calibration_suggestions: list[CalibrationSuggestionOut] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class EffectivenessReportOut(BaseModel):
    metric: CalibrationMetricOut
    suggestions: list[CalibrationSuggestionOut] = Field(default_factory=list)
    auto_calibration_allowed: bool = False


class CalibrationRuleChangeOut(BaseModel):
    rule_id: int
    display_name: str
    field: str
    before: float
    after: float


class CalibrationProposalOut(BaseModel):
    metric_key: str = "expectation_hit"
    sample_count: int
    eligible: bool
    minimum_samples: int = 20
    rationale: str
    changes: list[CalibrationRuleChangeOut] = Field(default_factory=list)


class CalibrationApplyIn(BaseModel):
    confirmation: str


class CalibrationRunOut(BaseModel):
    id: int
    metric_key: str
    sample_count: int
    status: str
    rationale: str
    changes: list[CalibrationRuleChangeOut] = Field(default_factory=list)
    created_at: datetime
    rolled_back_at: datetime | None = None


class MarketGradeOut(BaseModel):
    grade: str
    total_position_limit: str
    single_position_limit: str
    reasons: list[str]
    risk_warnings: list[str]


class MarketIndexStateOut(BaseModel):
    code: str
    name: str
    current: float | None = None
    change_pct: float | None = None
    amount_yi: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    prev_close: float | None = None
    intraday_vwap: float | None = None
    above_vwap: bool | None = None
    high_drawdown_pct: float | None = None
    low_rebound_pct: float | None = None
    data_quality: str = "missing"
    source: str = ""


class MarketSectorEvidenceOut(BaseModel):
    name: str
    change_pct: float
    net_inflow: float
    main_inflow: float
    rank: int
    above_vwap: bool | None = None


class MarketRegimeMetrics(BaseModel):
    active_stock_count: int | None = None
    up_count: int | None = None
    down_count: int | None = None
    flat_count: int | None = None
    up_5pct_count: int | None = None
    down_5pct_count: int | None = None
    limit_up_count: int | None = None
    limit_down_count: int | None = None
    median_change_pct: float | None = None
    advance_ratio: float | None = None
    turnover_yi: float | None = None
    projected_turnover_yi: float | None = None
    previous_turnover_yi: float | None = None
    avg5_turnover_yi: float | None = None
    volume_ratio_previous: float | None = None
    volume_ratio_5d: float | None = None
    market_main_net_inflow_yi: float | None = None
    index_composite_change_pct: float | None = None
    index_above_vwap_count: int | None = None
    index_valid_count: int = 0
    positive_sector_count: int | None = None
    negative_sector_count: int | None = None
    positive_sector_ratio: float | None = None
    sector_above_vwap_ratio: float | None = None
    top3_inflow_share: float | None = None


class MarketRegimeClassificationOut(BaseModel):
    regime_code: str
    regime_name: str
    risk_level: str
    opportunity_score: int
    loss_score: int
    liquidity_score: int
    confidence: float
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class MarketRegimeOut(MarketRegimeMetrics):
    id: int | None = None
    trade_date: str
    captured_at: datetime
    source: str
    freshness_seconds: int = 0
    data_quality: str
    coverage_ratio: float
    confidence: float
    indices: list[MarketIndexStateOut] = Field(default_factory=list)
    strongest_sectors: list[MarketSectorEvidenceOut] = Field(default_factory=list)
    weakest_sectors: list[MarketSectorEvidenceOut] = Field(default_factory=list)
    regime_code: str
    regime_name: str
    risk_level: str
    opportunity_score: int
    loss_score: int
    liquidity_score: int
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ReflexivityCrowdingOut(BaseModel):
    side: str
    label: str
    score: float


class ReflexivityMarketGateOut(BaseModel):
    scenario: str = "UNKNOWN"
    risk_off: bool = False
    new_position_allowed: bool = False


class ReflexivityScenarioOut(BaseModel):
    code: str
    label: str
    match_score: float
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    next_validation_points: list[str] = Field(default_factory=list)


class ReflexivityAssessmentOut(BaseModel):
    level: str
    code: str = ""
    name: str = ""
    as_of: datetime | None = None
    data_quality: str = "missing"
    market_regime_code: str = "UNKNOWN"
    market_regime_name: str = "数据不足"
    current_scenario: str
    current_scenario_label: str
    scenario_match_score: float | None = None
    crowding: ReflexivityCrowdingOut
    confidence: float
    current_evidence: list[str] = Field(default_factory=list)
    current_counter_evidence: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    next_validation_points: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    market_gate: ReflexivityMarketGateOut | None = None
    hard_stop_triggered: bool = False
    scenarios: list[ReflexivityScenarioOut] = Field(default_factory=list)
    methodology_note: str


class GlobalQuoteOut(BaseModel):
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


class GlobalQuoteEnvelopeOut(GlobalQuoteOut):
    group: str


class GlobalMarketOut(BaseModel):
    generated_at: str
    as_of: str
    quality: str
    data_quality: str
    sources: list[str] = Field(default_factory=list)
    source: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    kis: dict[str, Any] = Field(default_factory=dict)
    korea_indices: list[GlobalQuoteOut] = Field(default_factory=list)
    korea_equities: list[GlobalQuoteOut] = Field(default_factory=list)
    us_indices: list[GlobalQuoteOut] = Field(default_factory=list)
    us_sector_rank: list[GlobalQuoteOut] = Field(default_factory=list)
    items: list[GlobalQuoteEnvelopeOut] = Field(default_factory=list)


class OpportunitySectorAssessmentOut(BaseModel):
    sector: str
    status: str
    confirmation_score: int
    funds_confirmed: bool
    price_confirmed: bool
    vwap_confirmed: bool
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    source: str = ""
    captured_at: str | None = None


class OpportunityRadarItemOut(BaseModel):
    id: str
    title: str
    source: str
    published_at: str
    age_minutes: int | None = None
    sectors: list[str] = Field(default_factory=list)
    related_stocks: list[str] = Field(default_factory=list)
    status: str
    confirmation_score: int
    primary_sector: str | None = None
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    sector_assessments: list[OpportunitySectorAssessmentOut] = Field(default_factory=list)
    action: str
    trade_constraint: str
    buy_signal: bool = False
    url: str | None = None
    expires_at: str | None = None


class OpportunityRadarOut(BaseModel):
    updated_at: str
    as_of: str
    source: list[str] = Field(default_factory=list)
    data_quality: str
    items: list[OpportunityRadarItemOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    discipline: str
    notes: list[str] = Field(default_factory=list)
    available_sector_evidence: int = 0


class PreTradeCheckIn(BaseModel):
    code: str
    name: str
    market_grade: str = "B"
    position_ratio: float
    target_role: str
    is_mainline: bool
    has_sector_response: bool
    has_volume_price_confirm: bool
    buy_point: str
    stop_loss_price: float
    current_price: float
    mode: str = "标准短线模式"


class PreTradeCheckOut(BaseModel):
    decision: str
    score: int
    allowed_position_ratio: float
    warnings: list[str]
    required_actions: list[str]


class RiskPositionIn(BaseModel):
    net_asset: float
    risk_ratio: float = 0.01
    entry_price: float
    stop_price: float
    lot_size: int = 100
    script_limit: float = 1
    market_limit: float = 1
    single_stock_limit: float = 1
    sector_limit: float = 1
    liquidity_limit: float = 1


class RiskPositionOut(BaseModel):
    risk_budget: float
    loss_per_share: float
    risk_based_value: float
    final_position_value: float
    final_position_ratio: float
    quantity: int
    binding_limit: str
    caps: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ExitCardCreate(BaseModel):
    code: str
    name: str
    max_position_ratio: float
    confirm_price: float
    trim_price: float
    failure_price: float
    outperform_condition: str
    underperform_action: str
    allow_buyback: bool = False
    buyback_limit_ratio: float = 0


class ExitCardOut(ExitCardCreate):
    id: int
    mode: str
    created_at: datetime

    class Config:
        from_attributes = True


class SectorFlowPoint(BaseModel):
    time: str
    value: float


class SectorFlowBreakdownItem(BaseModel):
    name: str
    net: float
    ratio: float = 0


class SectorIndexPoint(BaseModel):
    time: str
    price: float
    vwap: float


class SectorFlowItem(BaseModel):
    name: str
    display_name: str | None = None
    raw_name: str | None = None
    board_code: str | None = None
    provider: str | None = None
    theme_line: str | None = None
    mainline: str | None = None
    subline: str | None = None
    category: str | None = None
    change_pct: float
    net_inflow: float
    main_inflow: float
    strength: int
    rank: int
    rank_change: int | None = None
    leaders: list[str]
    timeline: list[SectorFlowPoint]
    timeline_reliable: bool = False
    flow_peak: float | None = None
    flow_peak_time: str | None = None
    flow_pullback: float | None = None
    flow_pullback_pct: float | None = None
    flow_event: str | None = None
    index_timeline: list[SectorIndexPoint] = Field(default_factory=list)
    sector_price: float | None = None
    sector_vwap: float | None = None
    sector_vwap_reliable: bool = False
    sector_below_vwap: bool | None = None
    flow_breakdown: list[SectorFlowBreakdownItem] = Field(default_factory=list)


class SectorFlowOut(BaseModel):
    source: str
    updated_at: datetime
    inflow: list[SectorFlowItem]
    outflow: list[SectorFlowItem]


class BoardFlowPanelOut(BaseModel):
    source: str
    updated_at: datetime
    board_type: str
    period: str
    inflow: list[SectorFlowItem]
    outflow: list[SectorFlowItem]
    notes: list[str] = Field(default_factory=list)


class HotThemeItem(BaseModel):
    name: str
    board_code: str | None = None
    period: str
    rank: int
    change_pct: float = 0
    net_inflow: float = 0
    main_inflow: float = 0
    source: str = ""
    reason: str = ""
    leaders: list[str] = Field(default_factory=list)


class HotThemesOut(BaseModel):
    source: str
    updated_at: datetime
    items: list[HotThemeItem]
    notes: list[str] = Field(default_factory=list)


class DarkTradeItem(BaseModel):
    code: str
    name: str
    market: str = ""
    board_type: str = ""
    rank: int = 0
    latest: float = 0
    change_pct: float = 0
    dark_amount: float = 0
    lit_amount: float = 0
    main_net_inflow_with_dark: float = 0
    dark_activity: float = 0
    inflow_stock_ratio: float = 0
    inflow_count: int = 0
    stock_count: int = 0
    leading_stock: str = ""
    leading_stock_code: str = ""
    industry: str = ""
    concept: str = ""


class DarkTradeOut(BaseModel):
    source: str
    trade_date: str
    updated_at: datetime
    scope: str
    items: list[DarkTradeItem]
    notes: list[str] = Field(default_factory=list)


class SectorRotationItem(BaseModel):
    name: str
    rank: int
    change_pct: float = 0
    net_inflow: float = 0
    main_inflow: float = 0
    acceleration: float = 0
    limit_up_count: int = 0
    leaders: list[str] = Field(default_factory=list)
    evidence: str = ""


class CapitalRotationAssessment(BaseModel):
    code: str
    name: str
    source_theme: str = ""
    target_theme: str = ""
    confirmed: bool = False
    confidence: int = 0
    source_net_inflow: float = 0
    source_flow_peak: float = 0
    evidence: list[str] = Field(default_factory=list)


class CapitalRotationOut(BaseModel):
    generated_at: datetime
    assessments: list[CapitalRotationAssessment] = Field(default_factory=list)


class FlowTimelinePoint(BaseModel):
    time: str
    value: float = 0


class HoldingSeesawItem(BaseModel):
    code: str
    name: str
    sector: str = ""
    holding_theme: str = ""
    theme_tags: list[str] = Field(default_factory=list)
    stock_industry: str = ""
    stock_concepts: list[str] = Field(default_factory=list)
    theme_source: str = ""
    flow_basis: str = "行业资金流"
    primary_industry_sector: str = ""
    concept_flow_sectors: list[str] = Field(default_factory=list)
    concept_flow_summary: str = ""
    matched_flow_sector: str = ""
    theme_flow_sectors: list[str] = Field(default_factory=list)
    theme_flow_summary: str = ""
    theme_flow_current: float = 0
    theme_flow_peak: float = 0
    theme_flow_pullback: float = 0
    theme_flow_pullback_pct: float = 0
    external_inflow_target: str = ""
    current_price: float = 0
    change_pct: float = 0
    high_change_pct: float = 0
    pullback_from_high_pct: float = 0
    estimated_vwap: float = 0
    below_vwap: bool = False
    sector_rank: int = 0
    sector_net_inflow: float = 0
    sector_main_inflow: float = 0
    sector_acceleration: float = 0
    risk_level: str = "观察"
    signal: str = ""
    advice: str = ""
    profit_protection_state: str = ""
    trigger_action: str = ""
    sector_ebb_trigger: list[str] = Field(default_factory=list)
    stock_weakening_trigger: list[str] = Field(default_factory=list)
    profit_drawdown_trigger: list[str] = Field(default_factory=list)
    buyback_trigger: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    theme_flow_timeline: list[FlowTimelinePoint] = Field(default_factory=list)


class MarketSeesawOut(BaseModel):
    source: str
    updated_at: datetime
    market_mode: str
    summary: str
    inflow_targets: list[SectorRotationItem]
    outflow_targets: list[SectorRotationItem]
    holding_alerts: list[HoldingSeesawItem]
    notes: list[str] = Field(default_factory=list)


class SectorConstituentOut(BaseModel):
    code: str
    name: str
    price: float = 0
    change_pct: float = 0
    amount: float = 0
    turnover: float = 0
    main_inflow: float = 0
    net_inflow: float = 0
    float_cap: float = 0
    is_limit_up: bool = False
    consecutive_limit_days: int = 0
    concepts: list[str] = Field(default_factory=list)


class SectorDetailOut(BaseModel):
    source: str
    updated_at: datetime
    name: str
    display_name: str | None = None
    raw_name: str | None = None
    board_code: str | None = None
    provider: str | None = None
    theme_line: str | None = None
    mainline: str | None = None
    subline: str | None = None
    category: str | None = None
    change_pct: float = 0
    net_inflow: float = 0
    main_inflow: float = 0
    strength: int = 0
    leaders: list[str] = Field(default_factory=list)
    constituents: list[SectorConstituentOut]
    limit_up_stocks: list[SectorConstituentOut]
    flow_breakdown: list[SectorFlowBreakdownItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LimitUpStockOut(BaseModel):
    code: str
    name: str
    price: float = 0
    change_pct: float = 0
    amount: float = 0
    turnover: float = 0
    sealed_amount: float = 0
    first_limit_time: str = ""
    last_limit_time: str = ""
    break_count: int = 0
    consecutive_limit_days: int = 1
    industry: str = ""
    concepts: list[str] = Field(default_factory=list)
    expectation: str = ""


class LimitUpGroupOut(BaseModel):
    level: int
    label: str
    stocks: list[LimitUpStockOut]


class LimitUpClusterOut(BaseModel):
    name: str
    count: int
    highest_level: int
    stocks: list[str]
    expectation: str


class LimitUpLadderOut(BaseModel):
    source: str
    trade_date: str
    updated_at: datetime
    groups: list[LimitUpGroupOut]
    clusters: list[LimitUpClusterOut]
    summary: list[str]
    notes: list[str] = Field(default_factory=list)


class ThemeStockRole(BaseModel):
    code: str
    name: str
    role: str
    change_pct: float
    amount: float = 0
    reason: str


class ThemeRadarItem(BaseModel):
    name: str
    board_code: str | None = None
    theme_type: str
    related_boards: list[str] = Field(default_factory=list)
    stage: str
    stage_reason: str
    score: int
    rank: int
    change_pct: float
    net_inflow: float
    main_inflow: float
    limit_up_count: int
    stock_count: int
    leader_names: list[str]
    core_stocks: list[ThemeStockRole]
    timeline: list[SectorFlowPoint] = Field(default_factory=list)
    resonance_tags: list[str]
    action: str
    risk: str


class ThemeRadarOut(BaseModel):
    source: str
    updated_at: datetime
    market_temperature: str
    strongest_theme: ThemeRadarItem | None
    resonance: list[ThemeRadarItem]
    themes: list[ThemeRadarItem]
    notes: list[str]


class InformationItem(BaseModel):
    id: str
    title: str
    summary: str
    source: str
    published_at: str
    keywords: list[str]
    sectors: list[str]
    related_stocks: list[str]
    strength_score: int
    credibility: str
    fund_status: str
    action: str
    url: str | None = None
    sentiment: str = "中性"
    sentiment_reason: str = "需结合资金与价格验证"
    related_holdings: list[str] = Field(default_factory=list)


class InformationDifferentialOut(BaseModel):
    source: str
    date: str
    updated_at: datetime
    items: list[InformationItem]
    watchlist: list[str]
    data_notes: list[str]


class SellPlanOut(BaseModel):
    code: str
    name: str
    first_trim_price: float
    second_exit_price: float
    failure_price: float
    sell_ratios: list[str]
    allow_buyback: bool
    buyback_condition: str
    condition_orders: list[str]


class ClassificationBasis(BaseModel):
    sector: str = ""
    mainline_position: str = ""
    fund_flow: str = ""
    amount: str = ""
    turnover: str = ""
    trend: str = ""
    support: str = ""
    pressure: str = ""
    weaker_than_sector: bool = False


class AuctionStageCheck(BaseModel):
    stage: str
    status: str = "待确认"
    trigger: str = ""
    decision: str = ""
    required_action: str = ""
    evidence: list[str] = Field(default_factory=list)


class AuctionPlan(BaseModel):
    board_level: str = ""
    industry: str = ""
    concepts: list[str] = Field(default_factory=list)
    overnight_order: bool = True
    order_price: float = 0
    limit_up_price: float = 0
    keep_order_condition: str = ""
    cancel_condition: str = ""
    opening_confirmation: str = ""
    max_position_ratio: float = 0.1
    break_limit_action: str = ""
    notes: str = ""
    board_strength: str = ""
    leader_support: list[str] = Field(default_factory=list)
    limit_quality: str = ""
    expectation_level: str = ""
    strong_boundary_price: float = 0
    weak_reduce_price: float = 0
    weak_exit_price: float = 0
    risk_notes: list[str] = Field(default_factory=list)
    intraday_status: str = ""
    expected_state: str = ""
    expectation_match: str = ""
    operation_advice: str = ""
    volume_price_status: str = ""
    board_strength_detail: list[str] = Field(default_factory=list)
    next_day_script: list[str] = Field(default_factory=list)
    sell_trigger_cards: list[str] = Field(default_factory=list)
    refreshed_at: str = ""
    current_stage: str = ""
    stage_decision: str = ""
    action_ladder: list[str] = Field(default_factory=list)
    stage_checks: list[AuctionStageCheck] = Field(default_factory=list)


class NextDayPlanBase(BaseModel):
    plan_date: str = ""
    plan_type: str = "holding"
    holding_id: int | None = None
    code: str
    name: str
    quantity: int = 0
    cost_price: float = 0
    current_price: float = 0
    market_value: float = 0
    profit_amount: float = 0
    profit_ratio: float = 0
    price_source: str = "manual"
    price_note: str = ""
    position_ratio: float = 0
    holding_category: str = "震荡趋势股"
    classification_basis: ClassificationBasis = Field(default_factory=ClassificationBasis)
    outperform_condition: str = ""
    outperform_action: str = ""
    expected_condition: str = ""
    expected_action: str = ""
    underperform_condition: str = ""
    underperform_action: str = ""
    confirm_price: float = 0
    trim_price: float = 0
    trim_condition: str = ""
    trim_quantity: int = 0
    allow_buyback: bool = False
    buyback_price: float = 0
    buyback_condition: str = ""
    max_buyback_quantity: int = 0
    reduce_price: float = 0
    final_risk_price: float = 0
    stop_loss_4pct: float = 0
    limit_up_price: float = 0
    auction_plan: AuctionPlan = Field(default_factory=AuctionPlan)
    forbidden_actions: list[str] = Field(default_factory=list)
    review_expectation: str = ""
    review_execution: str = ""
    review_deviation: str = ""


class NextDayPlanCreate(NextDayPlanBase):
    pass


class NextDayPlanUpdate(BaseModel):
    plan_date: str | None = None
    plan_type: str | None = None
    holding_category: str | None = None
    classification_basis: ClassificationBasis | None = None
    auction_plan: AuctionPlan | None = None
    outperform_condition: str | None = None
    outperform_action: str | None = None
    expected_condition: str | None = None
    expected_action: str | None = None
    underperform_condition: str | None = None
    underperform_action: str | None = None
    confirm_price: float | None = None
    trim_price: float | None = None
    trim_condition: str | None = None
    trim_quantity: int | None = None
    allow_buyback: bool | None = None
    buyback_price: float | None = None
    buyback_condition: str | None = None
    max_buyback_quantity: int | None = None
    reduce_price: float | None = None
    final_risk_price: float | None = None
    limit_up_price: float | None = None
    forbidden_actions: list[str] | None = None
    review_expectation: str | None = None
    review_execution: str | None = None
    review_deviation: str | None = None


class NextDayPlanReview(BaseModel):
    review_expectation: str
    review_execution: str = ""
    review_deviation: str = ""


class NextDayPlanOut(NextDayPlanBase):
    id: int
    risk_priority: int
    risk_warnings: list[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LimitUpPlanCreate(BaseModel):
    code: str
    name: str
    price: float
    level: int = 1
    industry: str = ""
    concepts: list[str] = Field(default_factory=list)
    sealed_amount: float = 0
    amount: float = 0
    turnover: float = 0
    break_count: int = 0
    first_limit_time: str = ""
    last_limit_time: str = ""
    expectation: str = ""
    max_position_ratio: float = 0.1
