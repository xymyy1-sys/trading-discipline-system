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
  limit_up_count: number;
  stock_count: number;
  leader_names: string[];
  core_stocks: ThemeStockRole[];
  timeline: FlowPoint[];
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
}

export interface GlobalMarketCues {
  generated_at: string;
  as_of: string;
  quality: string;
  data_quality: string;
  sources: string[];
  source: string[];
  notes: string[];
  korea_indices: GlobalQuote[];
  korea_equities: GlobalQuote[];
  us_indices: GlobalQuote[];
  us_sector_rank: GlobalQuote[];
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
}

export interface InformationDifferentialOut {
  source: string;
  date: string;
  updated_at: string;
  items: InformationItem[];
  watchlist: string[];
  data_notes: string[];
}
