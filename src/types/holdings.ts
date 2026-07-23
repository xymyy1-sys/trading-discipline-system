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
  flow_direction?: string | null;
  flow_speed: number | null;
  flow_acceleration: number | null;
  flow_turning: string | null;
  flow_signal: string | null;
  flow_signal_level?: string | null;
  flow_as_of: string | null;
  flow_window_minutes?: number | null;
  flow_kinetics_reliable?: boolean;
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
  sector_flow_direction?: string | null;
  sector_flow_speed: number | null;
  sector_flow_acceleration: number | null;
  sector_flow_turning: string | null;
  sector_flow_signal: string | null;
  sector_flow_signal_level?: string | null;
  sector_flow_as_of: string | null;
  sector_flow_window_minutes?: number | null;
  sector_flow_kinetics_reliable?: boolean;
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
  state_key?: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  occurrence_count: number;
  confirmed: boolean;
  evidence: string[];
  counter_evidence?: string[];
  source?: string;
  source_url?: string | null;
  source_published_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ActionRecommendation {
  id: number | null;
  trade_date: string;
  holding_id: number | null;
  code: string;
  name: string;
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
  feedback_status: string;
  revision_id?: number | null;
  revision_version?: number | null;
  decision_hash?: string | null;
}

export interface AccountRisk {
  trade_date: string;
  opening_asset: number;
  current_asset: number;
  daily_profit_ratio: number;
  level: string;
  new_positions_allowed: boolean;
  recommended_action: string;
  degraded_position_count: number;
  stop_loss_count: number;
  data_complete: boolean;
  evidence: string[];
  updated_at: string | null;
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

export interface HoldingExecutionSignal {
  code: string;
  status: 'ACTIVE' | 'WATCH' | 'EXPIRED' | 'ELIGIBLE' | 'BLOCKED' | 'INACTIVE' | string;
  level: 'HIGH' | 'MEDIUM' | 'PROTECT' | 'OPPORTUNITY' | 'WATCH' | 'NEUTRAL' | string;
  title: string;
  action: string;
  recommended_ratio: number;
  evidence: string[];
  missing_conditions: string[];
  cancel_conditions: string[];
  recovery_conditions: string[];
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
  stop_source: string;
  stop_source_detail: string;
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
  high_sell_signal: HoldingExecutionSignal | null;
  panic_sell_guard: HoldingExecutionSignal | null;
  contrarian_add_signal: HoldingExecutionSignal | null;
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

export interface ExpectationScenario {
  id: number;
  scenario_type: string;
  probability: number;
  expected_low: number;
  expected_high: number;
  validation_conditions: string[];
  invalid_conditions: string[];
  action_discipline: string;
}

export interface ExpectationRevision {
  id: number;
  version: number;
  trade_date: string;
  stage: string;
  trigger: string;
  base_expectation: string;
  expected_open_low: number;
  expected_open_high: number;
  actual_open_pct: number;
  actual_change_pct: number;
  expectation_gap_score: number;
  expectation_result: string;
  state_transition: string;
  confidence: number;
  volume_price_state: string;
  vwap: number;
  price_vs_vwap: number;
  data_quality: string;
  evidence: string[];
  counter_evidence: string[];
  invalid_conditions: string[];
  suggestion: string;
  scenarios: ExpectationScenario[];
  created_at: string;
}

export interface ExpectationChain {
  code: string;
  trade_date: string;
  generated_at: string;
  current_stage: string;
  revisions: ExpectationRevision[];
}

export interface ExpectationRule {
  id: number;
  script_type: string;
  stage: string;
  base_expectation: string;
  display_name: string;
  expected_open_low: number;
  expected_open_high: number;
  outperform_threshold: number;
  underperform_threshold: number;
  severe_underperform_threshold: number;
  enabled: boolean;
  updated_at: string;
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
  turnover_source: string;
  turnover_reliable: boolean;
  float_cap: number;
  volume_ratio: number;
  vwap: number;
  vwap_source: string;
  minute_bar_count: number;
  vwap_reliable: boolean;
  price_vs_vwap: number;
  high_drawdown: number;
  active_buy_amount: number;
  active_sell_amount: number;
  active_flow_source: string;
  active_flow_estimated: boolean;
  ma5: number;
  ma10: number;
  ma20: number;
  return_5d: number;
  return_10d: number;
  distance_recent_high_pct: number;
  historical_volume_ratio: number;
  chip_profit_ratio: number;
  chip_avg_cost: number;
  chip_70_concentration: number;
  chip_90_concentration: number;
  chip_metrics_estimated: boolean;
  large_order_net_amount: number;
  large_order_threshold: number;
  attack_efficiency: number;
  volume_acceleration: number;
  attack_amount: number;
  pullback_amount: number;
  pullback_amount_ratio: number;
  pullback_sell_ratio: number;
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
  actual_sell_quantity: number;
  actual_buyback_quantity: number;
  execution_note: string;
  cost_reduction: number;
  evidence: string[];
  created_at: string;
  updated_at: string;
}

export interface EntryDiscipline {
  decision: 'BLOCK' | 'WAIT_RETEST' | 'ALLOW_SMALL' | 'ALLOW';
  label: string;
  risk_level: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';
  hard_blocked: boolean;
  chase_score: number;
  allowed_position_ratio: number;
  reason_codes: string[];
  evidence: string[];
  counter_evidence: string[];
  missing_conditions: string[];
  recheck_conditions: string[];
  cooldown_until: string | null;
  pulse_1m: number | null;
  pulse_3m: number | null;
  pulse_5m: number | null;
  distance_vwap_pct: number | null;
  distance_high_pct: number | null;
  data_quality: string;
  expires_at: string | null;
}

export interface EffectiveCapitalMetrics {
  sample_count: number;
  active_buy_yi: number | null;
  active_sell_yi: number | null;
  signed_flow_yi: number | null;
  buy_ratio: number | null;
  active_flow_coverage_ratio: number | null;
  same_time_flow_percentile: number | null;
  normalization_sample_count: number;
  price_change_pct: number | null;
  vwap_distance_pct: number | null;
  price_response_per_signed_yi: number | null;
  impact_retention_pct: number | null;
  persistence_score: number | null;
  window_minutes: number | null;
}

export interface EffectiveCapitalEvidence {
  state: string;
  state_label: string;
  confidence: number | null;
  state_severity: string;
  data_quality: string;
  source_label: string;
  as_of: string | null;
  estimated: boolean;
  metrics: EffectiveCapitalMetrics | null;
  evidence: string[];
  warnings: string[];
  invalidation: string[];
  discipline: string[];
  reason_codes: string[];
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
  consensus_risk: ConsensusRisk | null;
  minute_chart: MinuteChartPoint[];
  entry_discipline: EntryDiscipline | null;
  effective_capital?: EffectiveCapitalEvidence | null;
  market_data_trade_date: string;
  market_data_as_of: string | null;
  provider_event_at: string | null;
  data_age_seconds: number | null;
  is_current_session: boolean;
  is_latest_available: boolean;
  data_status_note: string;
}

export interface MinuteChartPoint {
  time: string;
  price: number;
  vwap: number;
  amount: number;
  amount_estimated: boolean;
}

export interface ConsensusRisk {
  level: 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN';
  score: number;
  data_complete: boolean;
  factors: string[];
  counter_evidence: string[];
  actions: string[];
}

export interface IntradayReview {
  code: string;
  name: string;
  generated_at: string;
  latest_action: string;
  latest_state: string;
  data_quality: string;
  timeline: IntradayEvidenceEvent[];
  evidence: string[];
  counter_evidence: string[];
  next_actions: string[];
}

export interface TimeStopRule {
  id: number | null;
  script_type: string;
  display_name: string;
  confirmation_deadline: string;
  below_vwap_minutes: number;
  below_vwap_min_bars: number;
  recent_window_minutes: number;
  failed_limit_reseal_pct: number;
  enabled: boolean;
  updated_at: string;
}
