from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
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
    current_position_ratio: Mapped[float] = mapped_column(Float, default=0)
    recommended_position_ratio: Mapped[float] = mapped_column(Float, default=0)
    recommended_action: Mapped[str] = mapped_column(String(64), default="继续持有")
    recommended_reduce_ratio: Mapped[float] = mapped_column(Float, default=0)
    structure_stop_price: Mapped[float] = mapped_column(Float, default=0)
    hard_stop_price: Mapped[float] = mapped_column(Float, default=0)
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


class RecommendationFeedback(Base):
    __tablename__ = "recommendation_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    recommendation_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(24), default="暂不执行")
    reason: Mapped[str] = mapped_column(Text, default="")
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
    volume_ratio: Mapped[float] = mapped_column(Float, default=0)
    vwap: Mapped[float] = mapped_column(Float, default=0)
    price_vs_vwap: Mapped[float] = mapped_column(Float, default=0)
    high_drawdown: Mapped[float] = mapped_column(Float, default=0)
    active_buy_amount: Mapped[float] = mapped_column(Float, default=0)
    active_sell_amount: Mapped[float] = mapped_column(Float, default=0)
    attack_efficiency: Mapped[float] = mapped_column(Float, default=0)
    volume_acceleration: Mapped[float] = mapped_column(Float, default=0)
    pattern: Mapped[str] = mapped_column(String(64), default="量价中性")
    data_quality: Mapped[str] = mapped_column(String(32), default="manual")
    data_source: Mapped[str] = mapped_column(String(64), default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_evidence_json: Mapped[str] = mapped_column(Text, default="[]")


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
    cost_reduction: Mapped[float] = mapped_column(Float, default=0)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
