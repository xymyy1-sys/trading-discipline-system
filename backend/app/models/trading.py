from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    cost_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float)
    total_asset: Mapped[float] = mapped_column(Float)
    position_type: Mapped[str] = mapped_column(String(32), default="盈利趋势仓")
    next_discipline: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class HoldingSyncBaseline(Base):
    __tablename__ = "holding_sync_baselines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True, unique=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    cost_price: Mapped[float] = mapped_column(Float, default=0)
    current_price: Mapped[float] = mapped_column(Float, default=0)
    total_asset: Mapped[float] = mapped_column(Float, default=0)
    position_type: Mapped[str] = mapped_column(String(32), default="交易同步基线仓")
    next_discipline: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class AccountState(Base):
    __tablename__ = "account_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    total_asset: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class AccountDailyRisk(Base):
    __tablename__ = "account_daily_risk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    opening_asset: Mapped[float] = mapped_column(Float, default=0)
    current_asset: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    traded_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    side: Mapped[str] = mapped_column(String(16))
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Float)
    total_asset: Mapped[float] = mapped_column(Float)
    position_ratio: Mapped[float] = mapped_column(Float)
    cost_price: Mapped[float] = mapped_column(Float)
    stop_loss_price: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(32), default="标准短线模式")
    compliant: Mapped[bool] = mapped_column(Boolean, default=True)
    human_tags: Mapped[str] = mapped_column(String(255), default="")


class TradeReview(Base):
    __tablename__ = "trade_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trade_id: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(32), default="待改进")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    discipline_score: Mapped[int] = mapped_column(Integer, default=60)
    summary: Mapped[str] = mapped_column(Text, default="")
    stock_context: Mapped[str] = mapped_column(Text, default="")
    sector_context: Mapped[str] = mapped_column(Text, default="")
    market_context: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    mistakes: Mapped[str] = mapped_column(Text, default="[]")
    avoid_actions: Mapped[str] = mapped_column(Text, default="[]")
    weakness_tags: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ExitCard(Base):
    __tablename__ = "exit_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(32), default="板块共振集中进攻模式")
    max_position_ratio: Mapped[float] = mapped_column(Float)
    confirm_price: Mapped[float] = mapped_column(Float)
    trim_price: Mapped[float] = mapped_column(Float)
    failure_price: Mapped[float] = mapped_column(Float)
    outperform_condition: Mapped[str] = mapped_column(Text)
    underperform_action: Mapped[str] = mapped_column(Text)
    allow_buyback: Mapped[bool] = mapped_column(Boolean, default=False)
    buyback_limit_ratio: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    grade: Mapped[str] = mapped_column(String(8), default="B")
    turnover_score: Mapped[int] = mapped_column(Integer, default=0)
    limit_up_count: Mapped[int] = mapped_column(Integer, default=0)
    leading_theme: Mapped[str] = mapped_column(String(128), default="")
    leader_state: Mapped[str] = mapped_column(String(64), default="")
    loss_effect: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")


class MarketRegimeSnapshot(Base):
    """Persisted, evidence-backed full-market state at one collection time."""

    __tablename__ = "market_regime_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    source: Mapped[str] = mapped_column(String(255), default="")
    data_quality: Mapped[str] = mapped_column(String(24), default="missing", index=True)
    coverage_ratio: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0)

    active_stock_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    up_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    down_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flat_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    up_5pct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    down_5pct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_up_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_down_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    median_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    advance_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    turnover_yi: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_turnover_yi: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_turnover_yi: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg5_turnover_yi: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio_previous: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_main_net_inflow_yi: Mapped[float | None] = mapped_column(Float, nullable=True)

    index_composite_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    index_above_vwap_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    index_valid_count: Mapped[int] = mapped_column(Integer, default=0)
    indices_json: Mapped[str] = mapped_column(Text, default="[]")

    positive_sector_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    negative_sector_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    positive_sector_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    sector_above_vwap_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    top3_inflow_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    strongest_sectors_json: Mapped[str] = mapped_column(Text, default="[]")
    weakest_sectors_json: Mapped[str] = mapped_column(Text, default="[]")

    regime_code: Mapped[str] = mapped_column(String(48), default="UNKNOWN", index=True)
    regime_name: Mapped[str] = mapped_column(String(48), default="数据不足")
    risk_level: Mapped[str] = mapped_column(String(16), default="未知")
    opportunity_score: Mapped[int] = mapped_column(Integer, default=0)
    loss_score: Mapped[int] = mapped_column(Integer, default=0)
    liquidity_score: Mapped[int] = mapped_column(Integer, default=0)
    allowed_actions_json: Mapped[str] = mapped_column(Text, default="[]")
    forbidden_actions_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    missing_fields_json: Mapped[str] = mapped_column(Text, default="[]")
    notes_json: Mapped[str] = mapped_column(Text, default="[]")


class NextDayPlan(Base):
    __tablename__ = "next_day_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_date: Mapped[str] = mapped_column(String(16), index=True)
    plan_type: Mapped[str] = mapped_column(String(24), default="holding", index=True)
    holding_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    cost_price: Mapped[float] = mapped_column(Float, default=0)
    current_price: Mapped[float] = mapped_column(Float, default=0)
    position_ratio: Mapped[float] = mapped_column(Float, default=0)
    holding_category: Mapped[str] = mapped_column(String(32), default="震荡趋势股")
    risk_priority: Mapped[int] = mapped_column(Integer, default=4)
    classification_basis: Mapped[str] = mapped_column(Text, default="{}")
    outperform_condition: Mapped[str] = mapped_column(Text, default="")
    outperform_action: Mapped[str] = mapped_column(Text, default="")
    expected_condition: Mapped[str] = mapped_column(Text, default="")
    expected_action: Mapped[str] = mapped_column(Text, default="")
    underperform_condition: Mapped[str] = mapped_column(Text, default="")
    underperform_action: Mapped[str] = mapped_column(Text, default="")
    confirm_price: Mapped[float] = mapped_column(Float, default=0)
    trim_price: Mapped[float] = mapped_column(Float, default=0)
    trim_condition: Mapped[str] = mapped_column(Text, default="")
    trim_quantity: Mapped[int] = mapped_column(Integer, default=0)
    allow_buyback: Mapped[bool] = mapped_column(Boolean, default=False)
    buyback_price: Mapped[float] = mapped_column(Float, default=0)
    buyback_condition: Mapped[str] = mapped_column(Text, default="")
    max_buyback_quantity: Mapped[int] = mapped_column(Integer, default=0)
    reduce_price: Mapped[float] = mapped_column(Float, default=0)
    final_risk_price: Mapped[float] = mapped_column(Float, default=0)
    stop_loss_4pct: Mapped[float] = mapped_column(Float, default=0)
    limit_up_price: Mapped[float] = mapped_column(Float, default=0)
    auction_plan: Mapped[str] = mapped_column(Text, default="{}")
    forbidden_actions: Mapped[str] = mapped_column(Text, default="[]")
    risk_warnings: Mapped[str] = mapped_column(Text, default="[]")
    review_expectation: Mapped[str] = mapped_column(String(32), default="")
    review_execution: Mapped[str] = mapped_column(Text, default="")
    review_deviation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class PositionExecutionState(Base):
    __tablename__ = "position_execution_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    holding_id: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    state: Mapped[str] = mapped_column(String(48), default="NORMAL_HOLD")
    expectation_state: Mapped[str] = mapped_column(String(48), default="MATCHED")
    volume_price_state: Mapped[str] = mapped_column(String(64), default="")
    sector_state: Mapped[str] = mapped_column(String(64), default="")
    current_quantity: Mapped[int] = mapped_column(Integer, default=0)
    sellable_quantity: Mapped[int] = mapped_column(Integer, default=0)
    today_buy_quantity: Mapped[int] = mapped_column(Integer, default=0)
    yesterday_quantity: Mapped[int] = mapped_column(Integer, default=0)
    current_position_ratio: Mapped[float] = mapped_column(Float, default=0)
    recommended_position_ratio: Mapped[float] = mapped_column(Float, default=0)
    recommended_action: Mapped[str] = mapped_column(String(64), default="继续持有")
    recommended_reduce_ratio: Mapped[float] = mapped_column(Float, default=0)
    structure_stop_price: Mapped[float] = mapped_column(Float, default=0)
    hard_stop_price: Mapped[float] = mapped_column(Float, default=0)
    stop_source: Mapped[str] = mapped_column(String(48), default="fallback_candidate")
    stop_source_detail: Mapped[str] = mapped_column(Text, default="")
    trailing_stop_price: Mapped[float] = mapped_column(Float, default=0)
    profit_protection_price: Mapped[float] = mapped_column(Float, default=0)
    t_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    t_type: Mapped[str] = mapped_column(String(24), default="NO_T")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    invalid_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    recovery_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    data_quality: Mapped[str] = mapped_column(String(32), default="manual")
    data_time: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class PositionStateHistory(Base):
    __tablename__ = "position_state_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    holding_id: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    old_state: Mapped[str] = mapped_column(String(48), default="")
    new_state: Mapped[str] = mapped_column(String(48), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")


class TimeStopRule(Base):
    __tablename__ = "time_stop_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    script_type: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    confirmation_deadline: Mapped[str] = mapped_column(String(8), default="10:00")
    below_vwap_minutes: Mapped[int] = mapped_column(Integer, default=5)
    below_vwap_min_bars: Mapped[int] = mapped_column(Integer, default=5)
    recent_window_minutes: Mapped[int] = mapped_column(Integer, default=15)
    failed_limit_reseal_pct: Mapped[float] = mapped_column(Float, default=0.985)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class ProfitProtectionSnapshot(Base):
    __tablename__ = "profit_protection_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    holding_id: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    current_profit_pct: Mapped[float] = mapped_column(Float, default=0)
    maximum_profit_pct: Mapped[float] = mapped_column(Float, default=0)
    profit_drawdown_pct: Mapped[float] = mapped_column(Float, default=0)
    maximum_price: Mapped[float] = mapped_column(Float, default=0)
    maximum_profit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    day_max_profit_pct: Mapped[float] = mapped_column(Float, default=0)
    day_max_profit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    protection_level: Mapped[str] = mapped_column(String(32), default="NONE")
    protection_floor: Mapped[float] = mapped_column(Float, default=0)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    recommended_action: Mapped[str] = mapped_column(String(64), default="继续持有")


class IntradayEvidenceEvent(Base):
    __tablename__ = "intraday_evidence_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    scope: Mapped[str] = mapped_column(String(24), default="stock")
    target_code: Mapped[str] = mapped_column(String(16), index=True)
    target_name: Mapped[str] = mapped_column(String(64), default="")
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(24), default="info")
    value: Mapped[float] = mapped_column(Float, default=0)
    previous_value: Mapped[float] = mapped_column(Float, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    group_key: Mapped[str] = mapped_column(String(64), default="")
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    recommendation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class ActionRecommendation(Base):
    __tablename__ = "action_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    holding_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    level: Mapped[str] = mapped_column(String(24), default="INFO")
    state: Mapped[str] = mapped_column(String(48), default="")
    action: Mapped[str] = mapped_column(String(64), default="")
    recommended_ratio: Mapped[float] = mapped_column(Float, default=0)
    trigger_events_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    invalid_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    recovery_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionRecommendationRevision(Base):
    __tablename__ = "action_recommendation_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    recommendation_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    level: Mapped[str] = mapped_column(String(24), default="INFO")
    state: Mapped[str] = mapped_column(String(48), default="")
    action: Mapped[str] = mapped_column(String(64), default="")
    recommended_ratio: Mapped[float] = mapped_column(Float, default=0)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    invalid_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    recovery_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class RecommendationFeedback(Base):
    __tablename__ = "recommendation_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    recommendation_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(24), default="暂不执行")
    reason: Mapped[str] = mapped_column(Text, default="")
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    result: Mapped[str] = mapped_column(String(32), default="待匹配成交")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ExpectationSnapshot(Base):
    __tablename__ = "expectation_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    stage: Mapped[str] = mapped_column(String(32), index=True)
    base_expectation: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    expected_open_low: Mapped[float] = mapped_column(Float, default=0)
    expected_open_high: Mapped[float] = mapped_column(Float, default=0)
    outperform_threshold: Mapped[float] = mapped_column(Float, default=0)
    underperform_threshold: Mapped[float] = mapped_column(Float, default=0)
    severe_underperform_threshold: Mapped[float] = mapped_column(Float, default=0)
    actual_open_pct: Mapped[float] = mapped_column(Float, default=0)
    actual_change_pct: Mapped[float] = mapped_column(Float, default=0)
    expectation_gap_score: Mapped[int] = mapped_column(Integer, default=0)
    expectation_result: Mapped[str] = mapped_column(String(32), default="MATCHED")
    state_transition: Mapped[str] = mapped_column(String(48), default="MATCHED")
    confidence: Mapped[float] = mapped_column(Float, default=0)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class VolumePriceSnapshot(Base):
    __tablename__ = "volume_price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    stage: Mapped[str] = mapped_column(String(32), default="盘中状态", index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    price: Mapped[float] = mapped_column(Float, default=0)
    change_pct: Mapped[float] = mapped_column(Float, default=0)
    open_price: Mapped[float] = mapped_column(Float, default=0)
    high_price: Mapped[float] = mapped_column(Float, default=0)
    low_price: Mapped[float] = mapped_column(Float, default=0)
    prev_close: Mapped[float] = mapped_column(Float, default=0)
    volume: Mapped[float] = mapped_column(Float, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)
    estimated_full_day_amount: Mapped[float] = mapped_column(Float, default=0)
    turnover: Mapped[float] = mapped_column(Float, default=0)
    turnover_source: Mapped[str] = mapped_column(String(48), default="unavailable")
    turnover_reliable: Mapped[bool] = mapped_column(Boolean, default=False)
    float_cap: Mapped[float] = mapped_column(Float, default=0)
    volume_ratio: Mapped[float] = mapped_column(Float, default=0)
    vwap: Mapped[float] = mapped_column(Float, default=0)
    vwap_source: Mapped[str] = mapped_column(String(32), default="estimated")
    minute_bar_count: Mapped[int] = mapped_column(Integer, default=0)
    vwap_reliable: Mapped[bool] = mapped_column(Boolean, default=False)
    price_vs_vwap: Mapped[float] = mapped_column(Float, default=0)
    high_drawdown: Mapped[float] = mapped_column(Float, default=0)
    active_buy_amount: Mapped[float] = mapped_column(Float, default=0)
    active_sell_amount: Mapped[float] = mapped_column(Float, default=0)
    active_flow_source: Mapped[str] = mapped_column(String(48), default="unavailable")
    active_flow_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    ma5: Mapped[float] = mapped_column(Float, default=0)
    ma10: Mapped[float] = mapped_column(Float, default=0)
    ma20: Mapped[float] = mapped_column(Float, default=0)
    return_5d: Mapped[float] = mapped_column(Float, default=0)
    return_10d: Mapped[float] = mapped_column(Float, default=0)
    distance_recent_high_pct: Mapped[float] = mapped_column(Float, default=0)
    historical_volume_ratio: Mapped[float] = mapped_column(Float, default=0)
    chip_profit_ratio: Mapped[float] = mapped_column(Float, default=0)
    chip_avg_cost: Mapped[float] = mapped_column(Float, default=0)
    chip_70_concentration: Mapped[float] = mapped_column(Float, default=0)
    chip_90_concentration: Mapped[float] = mapped_column(Float, default=0)
    chip_metrics_estimated: Mapped[bool] = mapped_column(Boolean, default=True)
    large_order_net_amount: Mapped[float] = mapped_column(Float, default=0)
    large_order_threshold: Mapped[float] = mapped_column(Float, default=0)
    attack_efficiency: Mapped[float] = mapped_column(Float, default=0)
    volume_acceleration: Mapped[float] = mapped_column(Float, default=0)
    attack_amount: Mapped[float] = mapped_column(Float, default=0)
    pullback_amount: Mapped[float] = mapped_column(Float, default=0)
    pullback_amount_ratio: Mapped[float] = mapped_column(Float, default=0)
    pullback_sell_ratio: Mapped[float] = mapped_column(Float, default=0)
    pattern: Mapped[str] = mapped_column(String(64), default="量价中性")
    data_quality: Mapped[str] = mapped_column(String(32), default="manual")
    data_source: Mapped[str] = mapped_column(String(64), default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_evidence_json: Mapped[str] = mapped_column(Text, default="[]")


class ExpectationRule(Base):
    __tablename__ = "expectation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    script_type: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(32), default="*", index=True)
    base_expectation: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    expected_open_low: Mapped[float] = mapped_column(Float)
    expected_open_high: Mapped[float] = mapped_column(Float)
    outperform_threshold: Mapped[float] = mapped_column(Float)
    underperform_threshold: Mapped[float] = mapped_column(Float)
    severe_underperform_threshold: Mapped[float] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CalibrationRun(Base):
    __tablename__ = "calibration_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    metric_key: Mapped[str] = mapped_column(String(48), index=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="applied", index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    before_json: Mapped[str] = mapped_column(Text, default="[]")
    after_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExpectationRevision(Base):
    __tablename__ = "expectation_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    expectation_snapshot_id: Mapped[int] = mapped_column(Integer, index=True)
    previous_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    stage: Mapped[str] = mapped_column(String(32), index=True)
    trigger: Mapped[str] = mapped_column(String(48), default="collector")
    base_expectation: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    expected_open_low: Mapped[float] = mapped_column(Float, default=0)
    expected_open_high: Mapped[float] = mapped_column(Float, default=0)
    actual_open_pct: Mapped[float] = mapped_column(Float, default=0)
    actual_change_pct: Mapped[float] = mapped_column(Float, default=0)
    expectation_gap_score: Mapped[int] = mapped_column(Integer, default=0)
    expectation_result: Mapped[str] = mapped_column(String(32), default="MATCHED")
    state_transition: Mapped[str] = mapped_column(String(48), default="MATCHED")
    confidence: Mapped[float] = mapped_column(Float, default=0)
    volume_price_state: Mapped[str] = mapped_column(String(64), default="")
    vwap: Mapped[float] = mapped_column(Float, default=0)
    price_vs_vwap: Mapped[float] = mapped_column(Float, default=0)
    data_quality: Mapped[str] = mapped_column(String(32), default="manual")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    invalid_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ExpectationScenario(Base):
    __tablename__ = "expectation_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    revision_id: Mapped[int] = mapped_column(Integer, index=True)
    scenario_type: Mapped[str] = mapped_column(String(32), index=True)
    probability: Mapped[float] = mapped_column(Float, default=0)
    expected_low: Mapped[float] = mapped_column(Float, default=0)
    expected_high: Mapped[float] = mapped_column(Float, default=0)
    validation_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    invalid_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    action_discipline: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class WatchlistEntry(Base):
    __tablename__ = "watchlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    snapshot_date: Mapped[str] = mapped_column(String(16), default="", index=True)
    category: Mapped[str] = mapped_column(String(32), default="")
    snapshot_rank: Mapped[int] = mapped_column(Integer, default=0)
    entry_reason: Mapped[str] = mapped_column(Text, default="")
    exit_reason: Mapped[str] = mapped_column(Text, default="")
    exited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class StrategyTemplate(Base):
    __tablename__ = "strategy_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), default="general")
    market_environment_json: Mapped[str] = mapped_column(Text, default="[]")
    prerequisites_json: Mapped[str] = mapped_column(Text, default="[]")
    premarket_expectation_json: Mapped[str] = mapped_column(Text, default="[]")
    auction_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    volume_price_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    buy_confirmation_json: Mapped[str] = mapped_column(Text, default="[]")
    position_limit: Mapped[float] = mapped_column(Float, default=0)
    structure_stop_json: Mapped[str] = mapped_column(Text, default="[]")
    invalid_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    holding_management_json: Mapped[str] = mapped_column(Text, default="[]")
    forbidden_actions_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DataCaptureSnapshot(Base):
    __tablename__ = "data_capture_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    data_type: Mapped[str] = mapped_column(String(32), index=True)
    target_code: Mapped[str] = mapped_column(String(32), index=True)
    target_name: Mapped[str] = mapped_column(String(64), default="")
    raw_value_json: Mapped[str] = mapped_column(Text, default="{}")
    normalized_value_json: Mapped[str] = mapped_column(Text, default="{}")
    quality: Mapped[str] = mapped_column(String(32), default="missing")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    is_degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    error_message: Mapped[str] = mapped_column(Text, default="")
    raw_payload_hash: Mapped[str] = mapped_column(String(64), default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    actor: Mapped[str] = mapped_column(String(128), default="unknown")
    method: Mapped[str] = mapped_column(String(12))
    path: Mapped[str] = mapped_column(String(255))
    status_code: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    previous_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)


class TTradePlan(Base):
    __tablename__ = "t_trade_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    holding_id: Mapped[int] = mapped_column(Integer, index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    t_type: Mapped[str] = mapped_column(String(24), default="NO_T")
    planned_sell_price: Mapped[float] = mapped_column(Float, default=0)
    planned_sell_quantity: Mapped[int] = mapped_column(Integer, default=0)
    buyback_price_low: Mapped[float] = mapped_column(Float, default=0)
    buyback_price_high: Mapped[float] = mapped_column(Float, default=0)
    buyback_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    cancel_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="planned")
    actual_sell_price: Mapped[float] = mapped_column(Float, default=0)
    actual_buyback_price: Mapped[float] = mapped_column(Float, default=0)
    actual_quantity: Mapped[int] = mapped_column(Integer, default=0)
    actual_sell_quantity: Mapped[int] = mapped_column(Integer, default=0)
    actual_buyback_quantity: Mapped[int] = mapped_column(Integer, default=0)
    execution_note: Mapped[str] = mapped_column(Text, default="")
    cost_reduction: Mapped[float] = mapped_column(Float, default=0)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class IntradayCollectionRun(Base):
    __tablename__ = "intraday_collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    trigger: Mapped[str] = mapped_column(String(32), default="scheduler")
    holding_count: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    notes_json: Mapped[str] = mapped_column(Text, default="[]")
    error_message: Mapped[str] = mapped_column(Text, default="")


class AiAnalysisCache(Base):
    __tablename__ = "ai_analysis_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    scope: Mapped[str] = mapped_column(String(32), index=True)
    target: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64), default="gpt-5.6-sol")
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="completed")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SimulationAccount(Base):
    __tablename__ = "simulation_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="模拟账户")
    initial_cash: Mapped[float] = mapped_column(Float, default=1000000)
    cash: Mapped[float] = mapped_column(Float, default=1000000)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.0003)
    minimum_commission: Mapped[float] = mapped_column(Float, default=5)
    stamp_tax_rate: Mapped[float] = mapped_column(Float, default=0.0005)
    transfer_fee_rate: Mapped[float] = mapped_column(Float, default=0.00001)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class SimulationEvidenceSnapshot(Base):
    __tablename__ = "simulation_evidence_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "code", "strategy_source", "trade_date", "version",
            name="uq_sim_evidence_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    strategy_source: Mapped[str] = mapped_column(String(32), index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    quote_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    data_quality: Mapped[str] = mapped_column(String(24), default="missing")
    quote_json: Mapped[str] = mapped_column(Text, default="{}")
    market_json: Mapped[str] = mapped_column(Text, default="{}")
    sector_json: Mapped[str] = mapped_column(Text, default="{}")
    expectation_json: Mapped[str] = mapped_column(Text, default="{}")
    volume_price_json: Mapped[str] = mapped_column(Text, default="{}")
    source_versions_json: Mapped[str] = mapped_column(Text, default="{}")
    market_regime: Mapped[str] = mapped_column(String(48), default="UNKNOWN", index=True)
    expectation_gap_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    expectation_gap_band: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    volume_price_state: Mapped[str] = mapped_column(String(64), default="")
    sector_state: Mapped[str] = mapped_column(String(64), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)


class SimulationOrder(Base):
    __tablename__ = "simulation_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    # Immutable evidence captured when the user/strategy made the decision.
    # A later matching observation must never replace this reference.
    decision_evidence_snapshot_id: Mapped[int] = mapped_column(Integer, index=True)
    strategy_source: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    side: Mapped[str] = mapped_column(String(8), index=True)
    order_type: Mapped[str] = mapped_column(String(16), default="MARKET")
    limit_price: Mapped[float] = mapped_column(Float, default=0)
    quantity: Mapped[int] = mapped_column(Integer)
    filled_quantity: Mapped[int] = mapped_column(Integer, default=0)
    average_fill_price: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    reject_reason: Mapped[str] = mapped_column(Text, default="")
    client_note: Mapped[str] = mapped_column(Text, default="")
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class SimulationFill(Base):
    __tablename__ = "simulation_fills"
    __table_args__ = (UniqueConstraint("order_id", name="uq_sim_fill_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    # Evidence captured from the strictly-later quote used for matching.
    fill_evidence_snapshot_id: Mapped[int] = mapped_column(Integer, index=True)
    strategy_source: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    side: Mapped[str] = mapped_column(String(8), index=True)
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    gross_amount: Mapped[float] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0)
    stamp_tax: Mapped[float] = mapped_column(Float, default=0)
    transfer_fee: Mapped[float] = mapped_column(Float, default=0)
    net_cash_flow: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    filled_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class SimulationTradeLot(Base):
    """One entry batch whose strategy/evidence survives every partial exit."""

    __tablename__ = "simulation_trade_lots"
    __table_args__ = (
        UniqueConstraint("entry_order_id", name="uq_sim_trade_lot_entry_order"),
        UniqueConstraint("entry_fill_id", name="uq_sim_trade_lot_entry_fill"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    entry_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    entry_fill_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    entry_decision_evidence_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    strategy_source: Mapped[str] = mapped_column(String(32), index=True)
    initial_quantity: Mapped[int] = mapped_column(Integer)
    remaining_quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_gross_amount: Mapped[float] = mapped_column(Float)
    entry_costs: Mapped[float] = mapped_column(Float, default=0)
    exit_quantity: Mapped[int] = mapped_column(Integer, default=0)
    exit_gross_amount: Mapped[float] = mapped_column(Float, default=0)
    exit_costs: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class SimulationClosedTrade(Base):
    """A completed round trip; partial exits create exactly one result."""

    __tablename__ = "simulation_closed_trades"
    __table_args__ = (UniqueConstraint("lot_id", name="uq_sim_closed_trade_lot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    lot_id: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    strategy_source: Mapped[str] = mapped_column(String(32), index=True)
    entry_decision_evidence_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    entry_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    entry_fill_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    closing_order_id: Mapped[int] = mapped_column(Integer, index=True)
    closing_fill_id: Mapped[int] = mapped_column(Integer, index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    entry_average_price: Mapped[float] = mapped_column(Float)
    exit_average_price: Mapped[float] = mapped_column(Float)
    entry_gross_amount: Mapped[float] = mapped_column(Float)
    exit_gross_amount: Mapped[float] = mapped_column(Float)
    total_costs: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    return_pct: Mapped[float] = mapped_column(Float, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    holding_days: Mapped[int] = mapped_column(Integer, default=0)


class SimulationPosition(Base):
    __tablename__ = "simulation_positions"
    __table_args__ = (UniqueConstraint("account_id", "code", name="uq_sim_position_account_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    available_quantity: Mapped[int] = mapped_column(Integer, default=0)
    today_buy_quantity: Mapped[int] = mapped_column(Integer, default=0)
    average_cost: Mapped[float] = mapped_column(Float, default=0)
    market_price: Mapped[float] = mapped_column(Float, default=0)
    market_value: Mapped[float] = mapped_column(Float, default=0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    last_rollover_date: Mapped[str] = mapped_column(String(16), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class SimulationDailyEquity(Base):
    __tablename__ = "simulation_daily_equity"
    __table_args__ = (UniqueConstraint("account_id", "trade_date", name="uq_sim_equity_account_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    cash: Mapped[float] = mapped_column(Float, default=0)
    market_value: Mapped[float] = mapped_column(Float, default=0)
    total_equity: Mapped[float] = mapped_column(Float, default=0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0)
    return_pct: Mapped[float] = mapped_column(Float, default=0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
