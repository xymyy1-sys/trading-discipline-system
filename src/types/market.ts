export interface FlowPoint {
  time: string;
  value: number;
}

export interface SectorIndexPoint {
  time: string;
  price: number;
  vwap: number;
}

export interface ThemeStockRole {
  code: string;
  name: string;
  role: string;
  change_pct: number;
  amount: number;
  reason: string;
}

export interface ThemeRadarItem {
  name: string;
  board_code: string | null;
  theme_type: string;
  related_boards: string[];
  stage: string;
  stage_reason: string;
  score: number;
  rank: number;
  change_pct: number;
  net_inflow: number;
  main_inflow: number;
  flow_ratio?: number | null;
  breadth_ratio?: number | null;
  constituent_coverage?: number | null;
  score_basis?: string[];
  limit_up_count: number;
  stock_count: number;
  leader_names: string[];
  core_stocks: ThemeStockRole[];
  timeline: FlowPoint[];
  timeline_scope?: string;
  resonance_tags: string[];
  action: string;
  risk: string;
}

export interface ThemeRadar {
  source: string;
  updated_at: string;
  market_temperature: string;
  strongest_theme: ThemeRadarItem | null;
  resonance: ThemeRadarItem[];
  themes: ThemeRadarItem[];
  notes: string[];
}

export interface MarketIndexState {
  code: string;
  name: string;
  current: number | null;
  change_pct: number | null;
  amount_yi: number | null;
  open_price: number | null;
  high_price: number | null;
  low_price: number | null;
  prev_close: number | null;
  intraday_vwap: number | null;
  above_vwap: boolean | null;
  high_drawdown_pct: number | null;
  low_rebound_pct: number | null;
  data_quality: string;
  source: string;
}

export interface MarketSectorEvidence {
  name: string;
  change_pct: number;
  net_inflow: number;
  main_inflow: number;
  rank: number;
  above_vwap: boolean | null;
}

export interface MarketRegime {
  id: number | null;
  trade_date: string;
  captured_at: string;
  source: string;
  freshness_seconds: number;
  data_quality: string;
  coverage_ratio: number;
  confidence: number;
  active_stock_count: number | null;
  up_count: number | null;
  down_count: number | null;
  flat_count: number | null;
  up_5pct_count: number | null;
  down_5pct_count: number | null;
  limit_up_count: number | null;
  limit_down_count: number | null;
  median_change_pct: number | null;
  advance_ratio: number | null;
  turnover_yi: number | null;
  projected_turnover_yi: number | null;
  previous_turnover_yi: number | null;
  avg5_turnover_yi: number | null;
  volume_ratio_previous: number | null;
  volume_ratio_5d: number | null;
  market_main_net_inflow_yi: number | null;
  index_composite_change_pct: number | null;
  index_above_vwap_count: number | null;
  index_valid_count: number;
  positive_sector_count: number | null;
  negative_sector_count: number | null;
  positive_sector_ratio: number | null;
  sector_above_vwap_ratio: number | null;
  top3_inflow_share: number | null;
  indices: MarketIndexState[];
  strongest_sectors: MarketSectorEvidence[];
  weakest_sectors: MarketSectorEvidence[];
  regime_code: string;
  regime_name: string;
  risk_level: string;
  opportunity_score: number;
  loss_score: number;
  liquidity_score: number;
  allowed_actions: string[];
  forbidden_actions: string[];
  evidence: string[];
  missing_fields: string[];
  notes: string[];
}

export interface GlobalQuote {
  symbol: string;
  name: string;
  market: string;
  status: string;
  price: number | null;
  change: number | null;
  change_pct: number | null;
  previous_close: number | null;
  open_price: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount: number | null;
  as_of: string | null;
  source: string;
  freshness: string;
  theme: string | null;
  proxy_description: string | null;
  note: string;
  source_url: string;
  published_at: string | null;
  observed_at: string | null;
  related_a_share_sectors: string[];
  metric_kind: string;
  data_quality: string;
}

export interface GlobalMetric {
  metric_id: string;
  name: string;
  market: string;
  status: string;
  value: number | null;
  change: number | null;
  change_pct: number | null;
  direction: string | null;
  unit: string;
  period: string | null;
  source: string;
  source_url: string;
  published_at: string | null;
  observed_at: string | null;
  related_a_share_sectors: string[];
  metric_kind: string;
  data_quality: string;
  note: string;
}

export interface GlobalMarketCues {
  generated_at: string;
  as_of: string;
  quality: string;
  data_quality: string;
  quote_quality: string;
  institutional_flow_quality: string;
  sources: string[];
  source: string[];
  notes: string[];
  quality_details: Record<string, unknown>;
  official_adapters: Record<string, unknown>;
  korea_indices: GlobalQuote[];
  korea_equities: GlobalQuote[];
  us_indices: GlobalQuote[];
  us_sector_rank: GlobalQuote[];
  strategic_assets: GlobalQuote[];
  macro_indicators: GlobalQuote[];
  etf_flows: GlobalMetric[];
  korea_foreign_flows: GlobalMetric[];
  korea_leverage_products: GlobalMetric[];
  official_rates: GlobalMetric[];
  snapshot_id?: number | null;
  snapshot_origin?: 'process_cache' | 'database' | 'unavailable' | string;
  persisted_at?: string | null;
}

export interface OpportunitySectorAssessment {
  sector: string;
  status: string;
  confirmation_score: number;
  funds_confirmed: boolean;
  price_confirmed: boolean;
  vwap_confirmed: boolean;
  evidence: string[];
  counter_evidence: string[];
  missing: string[];
  source: string;
  captured_at: string | null;
}

export interface OpportunityRadarItem {
  id: string;
  title: string;
  source: string;
  published_at: string;
  age_minutes: number | null;
  sectors: string[];
  related_stocks: string[];
  status: string;
  confirmation_score: number;
  primary_sector: string | null;
  evidence: string[];
  counter_evidence: string[];
  missing: string[];
  sector_assessments: OpportunitySectorAssessment[];
  action: string;
  trade_constraint: string;
  buy_signal: boolean;
  url: string | null;
  expires_at: string | null;
  claim_level: string;
  news_impact_status: string;
  market_validation: string;
  sentiment: string;
  sentiment_reason: string;
  escalate_to_holding_risk: boolean;
}

export interface SectorExpansionItem {
  sector: string;
  status: '增量已确认' | '增量待确认' | string;
  confirmation_score: number;
  window_minutes: number;
  total_limit_up_count: number;
  new_limit_up_count: number;
  highest_board: number;
  change_pct: number | null;
  net_inflow: number | null;
  flow_speed: number | null;
  flow_acceleration: number | null;
  flow_turning: string | null;
  leaders: string[];
  evidence: string[];
  counter_evidence: string[];
  missing: string[];
  risk: string[];
  action: string;
  invalidation: string[];
  source: string[];
  as_of: string;
  buy_signal: false;
}

export interface SectorExpansionRadar {
  updated_at: string;
  as_of: string;
  window_minutes: number;
  data_quality: string;
  source: string[];
  items: SectorExpansionItem[];
  counts: Record<string, number>;
  notes: string[];
}

export interface ConsensusHighOpenFade {
  code: string;
  label: string;
  status: string;
  triggered: boolean;
  risk_level: string;
  score: number | null;
  evidence: string[];
  counter_evidence: string[];
  missing_fields: string[];
  allowed_actions: string[];
  forbidden_actions: string[];
  next_validation_points: string[];
  methodology_note: string;
  as_of: string | null;
  trade_date: string;
  source: string[];
  input_evidence: Record<string, unknown>;
}

export interface OpportunityRadar {
  updated_at: string;
  as_of: string;
  source: string[];
  data_quality: string;
  items: OpportunityRadarItem[];
  counts: Record<string, number>;
  discipline: string;
  notes: string[];
  available_sector_evidence: number;
  intraday_expansion: SectorExpansionRadar | null;
  consensus_high_open_fade?: ConsensusHighOpenFade | null;
}

export interface ReflexivityCrowding {
  side: 'SELL_PRESSURE' | 'LONG_CHASING';
  label: string;
  score: number;
}

export interface ReflexivityScenario {
  code: string;
  label: string;
  match_score: number;
  evidence: string[];
  counter_evidence: string[];
  allowed_actions: string[];
  forbidden_actions: string[];
  next_validation_points: string[];
}

export interface ReflexivityMarketGate {
  scenario: string;
  risk_off: boolean;
  new_position_allowed: boolean;
}

export interface ReflexivityAssessment {
  level: 'MARKET' | 'STOCK';
  code?: string;
  name?: string;
  current_scenario: string;
  current_scenario_label: string;
  scenario_match_score: number | null;
  crowding: ReflexivityCrowding;
  confidence: number;
  market_gate?: ReflexivityMarketGate;
  hard_stop_triggered?: boolean;
  missing_fields: string[];
  current_evidence: string[];
  current_counter_evidence: string[];
  allowed_actions: string[];
  forbidden_actions: string[];
  next_validation_points: string[];
  scenarios: ReflexivityScenario[];
  methodology_note: string;
  consensus_high_open_fade?: ConsensusHighOpenFade | null;
}

export interface SectorConstituent {
  code: string;
  name: string;
  price: number;
  change_pct: number;
  amount: number;
  turnover: number;
  main_inflow: number;
  net_inflow: number;
  float_cap: number;
  is_limit_up: boolean;
  consecutive_limit_days: number;
  concepts: string[];
}

export interface SectorDetail {
  source: string;
  updated_at: string;
  name: string;
  display_name: string | null;
  raw_name: string | null;
  board_code: string | null;
  provider: string | null;
  theme_line: string | null;
  mainline: string | null;
  subline: string | null;
  category: string | null;
  change_pct: number;
  net_inflow: number;
  main_inflow: number;
  strength: number;
  leaders: string[];
  flow_breakdown: Array<{
    name: string;
    net: number;
    ratio: number;
  }>;
  constituents: SectorConstituent[];
  limit_up_stocks: SectorConstituent[];
  notes: string[];
}

export interface SectorFlowItem {
  name: string;
  display_name: string | null;
  raw_name: string | null;
  board_code: string | null;
  provider: string | null;
  theme_line: string | null;
  mainline: string | null;
  subline: string | null;
  category: string | null;
  change_pct: number;
  net_inflow: number;
  main_inflow: number;
  limit_up_count: number;
  strength: number;
  rank: number;
  rank_change: number | null;
  leaders: string[];
  timeline: FlowPoint[];
  timeline_reliable: boolean;
  flow_peak: number | null;
  flow_peak_time: string | null;
  flow_pullback: number | null;
  flow_pullback_pct: number | null;
  flow_event: 'FLOW_NEW_HIGH' | 'FLOW_PEAK_REVERSAL' | 'FLOW_TURN_NEGATIVE' | null;
  flow_direction: string | null;
  flow_speed: number | null;
  flow_acceleration: number | null;
  flow_turning: string | null;
  flow_signal: string | null;
  flow_signal_level: string | null;
  flow_as_of: string | null;
  flow_window_minutes: number | null;
  flow_kinetics_reliable: boolean;
  index_timeline: SectorIndexPoint[];
  sector_price: number | null;
  sector_vwap: number | null;
  sector_vwap_reliable: boolean;
  sector_below_vwap: boolean | null;
  flow_breakdown: Array<{
    name: string;
    net: number;
    ratio: number;
  }>;
}

export interface SectorFlowOut {
  source: string;
  updated_at: string;
  inflow: SectorFlowItem[];
  outflow: SectorFlowItem[];
}

export type SectorFlow = SectorFlowOut;

export interface BoardFlowPanel extends SectorFlowOut {
  board_type: string;
  period: string;
  notes: string[];
}

export interface SectorTemperatureItem {
  name: string;
  board_code: string | null;
  board_type: string;
  heat_score: number;
  status: string;
  risk_level: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';
  trend_score: number;
  flow_score: number;
  crowding_score: number | null;
  margin_score: number | null;
  attention_score: number | null;
  change_pct: number | null;
  change_pct_5d: number | null;
  change_pct_10d: number | null;
  net_inflow: number | null;
  net_inflow_5d: number | null;
  net_inflow_10d: number | null;
  flow_ratio?: number | null;
  flow_ratio_5d?: number | null;
  flow_ratio_10d?: number | null;
  flow_speed: number | null;
  flow_acceleration: number | null;
  flow_turning: string | null;
  provider_trade_date: string | null;
  provider_updated_at: string | null;
  limit_up_count: number;
  financing_balance: number | null;
  financing_buy?: number | null;
  financing_reference_turnover?: number | null;
  financing_turnover_as_of?: string;
  financing_net_buy: number | null;
  financing_balance_ratio: number | null;
  financing_net_buy_5d: number | null;
  financing_net_buy_10d: number | null;
  financing_net_buy_20d: number | null;
  financing_net_buy_slope_5d?: number | null;
  financing_net_buy_slope_10d?: number | null;
  financing_net_buy_slope_20d?: number | null;
  financing_balance_ratio_percentile_60d?: number | null;
  financing_balance_ratio_percentile_120d?: number | null;
  margin_history_sample_count?: number;
  margin_history_method?: string;
  margin_as_of: string;
  margin_realtime: boolean;
  distribution_state: string;
  distribution_risk_level: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';
  distribution_risk_score: number;
  order_flow_exhausted: boolean;
  leverage_crowding: boolean;
  price_response_weak: boolean;
  distribution_confirmation_count: number;
  capital_price_carrying_efficiency?: number | null;
  capital_price_carrying_sample_count?: number;
  capital_price_carrying_span_minutes?: number | null;
  capital_price_carrying_slope?: number | null;
  capital_price_carrying_method?: string;
  sector_turnover_amount?: number | null;
  financing_buy_turnover_ratio?: number | null;
  financing_turnover_date_aligned?: boolean;
  non_leveraged_net_inflow?: number | null;
  non_leveraged_flow_audited?: boolean;
  non_leveraged_flow_source_url?: string;
  non_leveraged_flow_published_at?: string | null;
  non_leveraged_net_inflow_unit?: string;
  non_leveraged_methodology_id?: string;
  etf_share_net_change?: number | null;
  etf_share_change_pct?: number | null;
  etf_flow_audited?: boolean;
  etf_id?: string;
  etf_share_unit?: string;
  etf_share_base?: number | null;
  etf_methodology_id?: string;
  leader_change_pct?: number | null;
  leader_divergence_pct?: number | null;
  advance_count?: number | null;
  decline_count?: number | null;
  constituent_count?: number | null;
  advance_ratio?: number | null;
  new_high_count?: number | null;
  new_high_ratio?: number | null;
  promotion_rate?: number | null;
  break_rate?: number | null;
  sector_price?: number | null;
  sector_vwap?: number | null;
  sector_vwap_reliable?: boolean;
  sector_below_vwap?: boolean | null;
  strict_state?: string | null;
  instantaneous_distribution_state?: string | null;
  confirmed_state?: string | null;
  sample_confirmation_count?: number;
  sample_confirmation_min_interval_seconds?: number;
  trading_day_confirmation_count?: number;
  persistence_confirmed?: boolean;
  persistence_state?: string | null;
  persistence_basis?: string[];
  data_as_of?: string | null;
  last_sample_at?: string | null;
  recent_state_samples?: SectorStateSample[];
  recent_samples?: SectorStateSample[];
  margin_history_degraded?: boolean;
  margin_history_sequence_complete?: boolean;
  distribution_evidence: string[];
  distribution_counter_evidence: string[];
  distribution_actions: string[];
  evidence: string[];
  counter_evidence: string[];
  actions: string[];
  data_quality: string;
}

export interface SectorStateSample {
  trade_date: string;
  captured_at: string;
  provider_updated_at: string | null;
  data_quality: string;
  strict_state: string;
  risk_level: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';
  risk_score: number | null;
}

export interface SectorTemperatureOut {
  source: string;
  updated_at: string;
  board_type: string;
  lookback_windows: number[];
  items: SectorTemperatureItem[];
  overheated: SectorTemperatureItem[];
  stabilizing: SectorTemperatureItem[];
  oversold_watch: SectorTemperatureItem[];
  notes: string[];
}

export interface HotThemeItem {
  name: string;
  board_code: string | null;
  period: string;
  rank: number;
  change_pct: number;
  net_inflow: number;
  main_inflow: number;
  source: string;
  reason: string;
  leaders: string[];
}

export interface HotThemesOut {
  source: string;
  updated_at: string;
  items: HotThemeItem[];
  notes: string[];
}

export interface DarkTradeItem {
  code: string;
  name: string;
  market: string;
  board_type: string;
  rank: number;
  latest: number;
  change_pct: number;
  dark_amount: number;
  lit_amount: number;
  main_net_inflow_with_dark: number;
  dark_activity: number;
  inflow_stock_ratio: number;
  inflow_count: number;
  stock_count: number;
  leading_stock: string;
  leading_stock_code: string;
  industry: string;
  concept: string;
}

export interface DarkTradeOut {
  source: string;
  trade_date: string;
  updated_at: string;
  scope: string;
  items: DarkTradeItem[];
  notes: string[];
}

export interface LimitUpStock {
  code: string;
  name: string;
  price: number;
  change_pct: number;
  amount: number;
  turnover: number;
  sealed_amount: number;
  first_limit_time: string;
  last_limit_time: string;
  break_count: number;
  consecutive_limit_days: number;
  industry: string;
  concepts: string[];
  expectation: string;
}

export interface LimitUpGroup {
  level: number;
  label: string;
  stocks: LimitUpStock[];
}

export interface LimitUpCluster {
  name: string;
  count: number;
  highest_level: number;
  stocks: string[];
  expectation: string;
}

export interface LimitUpLadder {
  source: string;
  trade_date: string;
  updated_at: string;
  groups: LimitUpGroup[];
  clusters: LimitUpCluster[];
  summary: string[];
  notes: string[];
}

export interface LimitUpAtmosphereMetrics {
  limit_up_count: number;
  limit_down_count: number | null;
  broken_count: number | null;
  seal_rate: number | null;
  break_rate: number | null;
  highest_board: number;
  previous_limit_up_count: number | null;
  promoted_count: number | null;
  promotion_rate: number | null;
  next_day_open_sample_count: number;
  next_day_premium_sample_count: number;
  next_day_average_open_pct: number | null;
  next_day_average_premium_pct: number | null;
  next_day_low_open_ratio: number | null;
  top_theme: string | null;
  top_theme_count: number | null;
  theme_concentration_pct: number | null;
}

export interface LimitUpIdentityRole {
  code: string;
  name: string;
  level: number;
  roles: string[];
  role_score: number;
  amount: number;
  sealed_amount: number;
  break_count: number;
  reason: string;
  recommended_action: string;
  max_position_ratio: number;
  risk_level: string;
  persistence_basis: string[];
}

export interface LimitUpThemeLadder {
  name: string;
  limit_up_count: number;
  broken_count: number | null;
  seal_rate: number | null;
  first_board_count: number;
  second_board_count: number;
  high_board_count: number;
  highest_level: number;
  layer_count: number;
  completeness_score: number;
  completeness_label: string;
  action: string;
  continuation_expectation: string;
  invalidation_conditions: string[];
  identity_roles: LimitUpIdentityRole[];
  mainline_name: string;
  mainline_rank: number | null;
  mainline_score: number | null;
  mainline_level: string;
  is_mainline: boolean | null;
  stage: string;
  stage_reason: string;
  net_inflow: number | null;
  main_inflow: number | null;
  stage_position_rule: string;
  max_position_ratio: number;
  eligible_roles: string[];
  evidence: string[];
}

export interface LimitUpAtmosphere {
  source: string;
  trade_date: string;
  previous_trade_date: string | null;
  updated_at: string;
  decision: 'ALLOW' | 'CAUTION' | 'FORBID' | 'DATA_GAP';
  decision_label: string;
  score: number;
  data_quality: string;
  metrics: LimitUpAtmosphereMetrics;
  evidence: string[];
  risks: string[];
  missing_data: string[];
  theme_ladders: LimitUpThemeLadder[];
  role_disclaimer: string;
  notes: string[];
}

export interface MarketGrade {
  grade: string;
  total_position_limit: string;
  single_position_limit: string;
  reasons: string[];
  risk_warnings: string[];
}

export interface InformationItem {
  id: string;
  title: string;
  summary: string;
  source: string;
  published_at: string;
  keywords: string[];
  sectors: string[];
  related_stocks: string[];
  strength_score: number;
  credibility: string;
  fund_status: string;
  action: string;
  url: string | null;
  sentiment: string;
  sentiment_reason: string;
  related_holdings: string[];
  verification_level: string;
  attribution: string;
}

export interface InformationDifferentialOut {
  source: string;
  date: string;
  updated_at: string;
  items: InformationItem[];
  watchlist: string[];
  data_notes: string[];
}
