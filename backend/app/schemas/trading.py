from __future__ import annotations

from datetime import datetime

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


class AccountAssetIn(BaseModel):
    total_asset: float


class AccountAssetOut(BaseModel):
    total_asset: float
    updated_at: datetime | None = None


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


class MarketGradeOut(BaseModel):
    grade: str
    total_position_limit: str
    single_position_limit: str
    reasons: list[str]
    risk_warnings: list[str]


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
    leaders: list[str]
    timeline: list[SectorFlowPoint]
    flow_breakdown: list[SectorFlowBreakdownItem] = Field(default_factory=list)


class SectorFlowOut(BaseModel):
    source: str
    updated_at: datetime
    inflow: list[SectorFlowItem]
    outflow: list[SectorFlowItem]


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
