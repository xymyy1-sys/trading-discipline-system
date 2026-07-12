import type { FlowPoint } from "./market";

export interface Holding {
  id?: number;
  code: string;
  name: string;
  quantity: number;
  cost_price: number;
  current_price: number;
  total_asset: number;
  position_type: string;
  next_discipline: string;
  created_at?: string;
  updated_at?: string;
}

export interface HoldingOut extends Holding {
  id: number;
  market_value: number;
  profit_amount: number;
  profit_ratio: number;
  today_profit_amount: number;
  today_profit_ratio: number;
  position_ratio: number;
  stop_loss_price: number;
  profit_guard_price: number | null;
  price_source: string;
  price_note: string;
  prev_close: number;
  change_pct: number;
  amount: number;
  turnover: number;
  open_price: number;
  high_price: number;
  low_price: number;
  sector_flow_status: string;
  sector_flow_advice: string;
  updated_at: string;
}

export interface SectorRotationItem {
  name: string;
  rank: number;
  change_pct: number;
  net_inflow: number;
  main_inflow: number;
  acceleration: number;
  limit_up_count: number;
  leaders: string[];
  evidence: string;
}

export interface HoldingSeesawItem {
  code: string;
  name: string;
  sector: string;
  holding_theme: string;
  theme_tags: string[];
  stock_industry: string;
  stock_concepts: string[];
  theme_source: string;
  flow_basis: string;
  primary_industry_sector: string;
  concept_flow_sectors: string[];
  concept_flow_summary: string;
  matched_flow_sector: string;
  theme_flow_sectors: string[];
  theme_flow_summary: string;
  theme_flow_current: number;
  theme_flow_peak: number;
  theme_flow_pullback: number;
  theme_flow_pullback_pct: number;
  external_inflow_target: string;
  current_price: number;
  change_pct: number;
  high_change_pct: number;
  pullback_from_high_pct: number;
  estimated_vwap: number;
  below_vwap: boolean;
  sector_rank: number;
  sector_net_inflow: number;
  sector_main_inflow: number;
  sector_acceleration: number;
  risk_level: string;
  signal: string;
  advice: string;
  profit_protection_state: string;
  trigger_action: string;
  sector_ebb_trigger: string[];
  stock_weakening_trigger: string[];
  profit_drawdown_trigger: string[];
  buyback_trigger: string[];
  evidence: string[];
  theme_flow_timeline: FlowPoint[];
}

export interface MarketSeesaw {
  source: string;
  updated_at: string;
  market_mode: string;
  summary: string;
  inflow_targets: SectorRotationItem[];
  outflow_targets: SectorRotationItem[];
  holding_alerts: HoldingSeesawItem[];
  notes: string[];
}
export interface AccountAsset {
  total_asset: number;
  updated_at?: string;
}

export interface IntradayEvidenceEvent {
  id: number | null;
  captured_at: string;
  scope: string;
  target_code: string;
  target_name: string;
  event_type: string;
  severity: string;
  value: number;
  previous_value: number;
  priority: number;
  group_key: string;
  first_seen_at: string | null;
  last_seen_at: string | null;
  occurrence_count: number;
  confirmed: boolean;
  evidence: string[];
}

export interface ActionRecommendation {
  id: number | null;
  level: string;
  state: string;
  action: string;
  recommended_ratio: number;
  evidence: string[];
  counter_evidence: string[];
  invalid_conditions: string[];
  recovery_conditions: string[];
  created_at: string;
  expires_at: string | null;
  acknowledged_at: string | null;
}

export interface ProfitProtectionSnapshot {
  id: number | null;
  holding_id: number;
  code: string;
  captured_at: string;
  current_profit_pct: number;
  maximum_profit_pct: number;
  profit_drawdown_pct: number;
  maximum_price: number;
  maximum_profit_at: string | null;
  day_max_profit_pct: number;
  day_max_profit_at: string | null;
  protection_level: string;
  protection_floor: number;
  triggered: boolean;
  recommended_action: string;
}

export interface PositionStateHistory {
  id: number | null;
  holding_id: number;
  code: string;
  name: string;
  trade_date: string;
  old_state: string;
  new_state: string;
  captured_at: string;
  reason: string;
  evidence: string[];
}

export interface PositionExecutionState {
  id: number | null;
  holding_id: number;
  code: string;
  name: string;
  trade_date: string;
  state: string;
  expectation_state: string;
  volume_price_state: string;
  sector_state: string;
  current_quantity: number;
  sellable_quantity: number;
  today_buy_quantity: number;
  yesterday_quantity: number;
  current_position_ratio: number;
  recommended_position_ratio: number;
  recommended_action: string;
  recommended_reduce_ratio: number;
  structure_stop_price: number;
  hard_stop_price: number;
  trailing_stop_price: number;
  profit_protection_price: number;
  t_eligible: boolean;
  t_type: string;
  evidence: string[];
  counter_evidence: string[];
  invalid_conditions: string[];
  recovery_conditions: string[];
  events: IntradayEvidenceEvent[];
  recommendation: ActionRecommendation | null;
  profit_snapshot: ProfitProtectionSnapshot | null;
  state_history: PositionStateHistory[];
  data_quality: string;
  data_time: string;
  updated_at: string;
}

export interface RecommendationFeedback {
  id: number;
  recommendation_id: number;
  status: string;
  reason: string;
  created_at: string;
}

export interface ExpectationSnapshot {
  id: number | null;
  trade_date: string;
  code: string;
  name: string;
  stage: string;
  base_expectation: string;
  expected_open_low: number;
  expected_open_high: number;
  outperform_threshold: number;
  underperform_threshold: number;
  severe_underperform_threshold: number;
  actual_open_pct: number;
  actual_change_pct: number;
  expectation_gap_score: number;
  expectation_result: string;
  state_transition: string;
  confidence: number;
  evidence: string[];
  counter_evidence: string[];
  suggestion: string;
  created_at: string;
}

export interface VolumePriceSnapshot {
  id: number | null;
  trade_date: string;
  code: string;
  name: string;
  stage: string;
  captured_at: string;
  price: number;
  change_pct: number;
  open_price: number;
  high_price: number;
  low_price: number;
  prev_close: number;
  volume: number;
  amount: number;
  estimated_full_day_amount: number;
  turnover: number;
  volume_ratio: number;
  vwap: number;
  vwap_source: string;
  minute_bar_count: number;
  vwap_reliable: boolean;
  price_vs_vwap: number;
  high_drawdown: number;
  active_buy_amount: number;
  active_sell_amount: number;
  attack_efficiency: number;
  volume_acceleration: number;
  pattern: string;
  data_quality: string;
  data_source: string;
  evidence: string[];
  counter_evidence: string[];
}

export interface TEligibility {
  holding_id: number;
  code: string;
  name: string;
  t_type: string;
  eligible: boolean;
  sellable_quantity: number;
  today_buy_quantity: number;
  yesterday_quantity: number;
  suggested_quantity: number;
  suggested_sell_price: number;
  buyback_price_low: number;
  buyback_price_high: number;
  buyback_conditions: string[];
  forbidden_reasons: string[];
  evidence: string[];
  current_action: string;
}

export interface TTradePlan {
  id: number | null;
  holding_id: number;
  trade_date: string;
  code: string;
  name: string;
  t_type: string;
  planned_sell_price: number;
  planned_sell_quantity: number;
  buyback_price_low: number;
  buyback_price_high: number;
  buyback_conditions: string[];
  cancel_conditions: string[];
  status: string;
  actual_sell_price: number;
  actual_buyback_price: number;
  actual_quantity: number;
  cost_reduction: number;
  evidence: string[];
  created_at: string;
  updated_at: string;
}

export interface StockDecisionCard {
  code: string;
  name: string;
  industry: string;
  concepts: string[];
  current_price: number;
  change_pct: number;
  expectation: ExpectationSnapshot;
  volume_price: VolumePriceSnapshot | null;
  execution_state: PositionExecutionState | null;
  timeline: IntradayEvidenceEvent[];
  allowed_actions: string[];
  forbidden_actions: string[];
  t_eligibility: TEligibility | null;
  evidence: string[];
  counter_evidence: string[];
  data_quality: string;
}
