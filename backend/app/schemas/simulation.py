from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


StrategySource = Literal["limit_up", "expectation_volume_price", "holding_execution"]


class SimulationAccountCreate(BaseModel):
    name: str = Field(default="模拟账户", min_length=1, max_length=64)
    initial_cash: float = Field(default=1_000_000, gt=0)
    commission_rate: float = Field(default=0.0003, ge=0, le=0.01)
    minimum_commission: float = Field(default=5, ge=0, le=100)
    stamp_tax_rate: float = Field(default=0.0005, ge=0, le=0.01)
    transfer_fee_rate: float = Field(default=0.00001, ge=0, le=0.01)


class SimulationAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    initial_cash: float
    cash: float
    commission_rate: float
    minimum_commission: float
    stamp_tax_rate: float
    transfer_fee_rate: float
    account_type: str = "manual"
    status: str
    created_at: datetime
    updated_at: datetime


class SimulationOrderCreate(BaseModel):
    strategy_source: StrategySource
    code: str = Field(min_length=6, max_length=16)
    name: str = Field(default="", max_length=64)
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: float = Field(default=0, ge=0)
    quantity: int = Field(gt=0)
    client_note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_limit_price(self):
        if self.order_type == "LIMIT" and self.limit_price <= 0:
            raise ValueError("限价委托必须提供正数限价")
        return self


class SimulationOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    decision_evidence_snapshot_id: int
    strategy_source: str
    code: str
    name: str
    side: str
    order_type: str
    limit_price: float
    quantity: int
    filled_quantity: int
    average_fill_price: float
    status: str
    reject_reason: str
    client_note: str
    trade_date: str
    submitted_at: datetime
    last_evaluated_at: datetime


class SimulationFillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    account_id: int
    fill_evidence_snapshot_id: int
    strategy_source: str
    code: str
    name: str
    side: str
    price: float
    quantity: int
    gross_amount: float
    commission: float
    stamp_tax: float
    transfer_fee: float
    net_cash_flow: float
    realized_pnl: float
    trade_date: str
    filled_at: datetime


class SimulationPositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    code: str
    name: str
    quantity: int
    available_quantity: int
    today_buy_quantity: int
    average_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    last_rollover_date: str
    updated_at: datetime


class SimulationDailyEquityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    trade_date: str
    cash: float
    market_value: float
    total_equity: float
    daily_pnl: float
    total_pnl: float
    return_pct: float
    drawdown_pct: float
    captured_at: datetime


class SimulationEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    code: str
    name: str
    strategy_source: str
    trade_date: str
    version: int
    captured_at: datetime
    quote_time: datetime | None = None
    data_quality: str
    market_regime: str
    expectation_gap_score: int
    expectation_gap_band: str
    volume_price_state: str
    sector_state: str
    content_hash: str
    quote_json: str
    market_json: str
    sector_json: str
    expectation_json: str
    volume_price_json: str
    source_versions_json: str


class SimulationClosedTradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    lot_id: int
    code: str
    name: str
    strategy_source: str
    entry_decision_evidence_snapshot_id: int | None
    entry_order_id: int | None
    entry_fill_id: int | None
    closing_order_id: int
    closing_fill_id: int
    quantity: int
    entry_average_price: float
    exit_average_price: float
    entry_gross_amount: float
    exit_gross_amount: float
    total_costs: float
    realized_pnl: float
    return_pct: float
    opened_at: datetime
    closed_at: datetime
    holding_days: int


class SimulationPerformanceSlice(BaseModel):
    key: str
    closed_trade_count: int
    sell_count: int
    win_count: int
    loss_count: int
    win_rate: float
    total_realized_pnl: float
    average_win: float
    average_loss: float
    profit_loss_ratio: float


class SimulationPerformanceOut(BaseModel):
    account_id: int
    closed_trade_count: int
    sell_count: int
    win_count: int
    loss_count: int
    win_rate: float
    total_realized_pnl: float
    profit_loss_ratio: float
    maximum_drawdown_pct: float
    by_strategy: list[SimulationPerformanceSlice]
    by_market_regime: list[SimulationPerformanceSlice]
    by_expectation_gap: list[SimulationPerformanceSlice]


class SimulationCalibrationCandidate(BaseModel):
    target: str
    field: str
    direction: Literal["tighten", "loosen", "hold"]
    suggestion: str
    reason: str
    sample_count: int
    support_metric: str


class SimulationCalibrationMetric(BaseModel):
    key: str = "overall"
    sample_count: int
    win_rate: float
    average_return_pct: float
    median_return_pct: float
    profit_loss_ratio: float
    total_realized_pnl: float


class SimulationCalibrationProposalOut(BaseModel):
    account_id: int
    generated_at: datetime
    status: str
    eligible: bool
    candidate_generation_allowed: bool = False
    statistics_only: bool = True
    minimum_samples: int
    statistical_sample_count: int = 0
    usable_sample_count: int
    excluded_sample_count: int
    exclusion_reasons: list[str] = Field(default_factory=list)
    summary: str
    overall: SimulationCalibrationMetric
    by_strategy: list[SimulationCalibrationMetric] = Field(default_factory=list)
    by_market_regime: list[SimulationCalibrationMetric] = Field(default_factory=list)
    by_expectation_gap: list[SimulationCalibrationMetric] = Field(default_factory=list)
    maximum_drawdown_pct: float = 0
    candidates: list[SimulationCalibrationCandidate] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    requires_manual_confirmation: bool = True
    auto_apply_allowed: Literal[False] = False


class SimulationShadowDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    signal_key: str
    strategy_source: str
    source_kind: str
    source_id: int | None = None
    rule_version: str
    source_version: str
    trade_date: str
    source_at: datetime | None = None
    evaluated_at: datetime
    code: str
    name: str
    intent: str
    side: str
    quantity: int
    status: str
    reason: str
    order_id: int | None = None
    evidence_json: str
