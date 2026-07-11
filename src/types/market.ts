export interface FlowPoint {
  time: string;
  value: number;
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
  leaders: string[];
  timeline: FlowPoint[];
}

export interface SectorFlowOut {
  source: string;
  updated_at: string;
  inflow: SectorFlowItem[];
  outflow: SectorFlowItem[];
}

export type SectorFlow = SectorFlowOut;

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
}

export interface InformationDifferentialOut {
  source: string;
  date: string;
  updated_at: string;
  items: InformationItem[];
  watchlist: string[];
  data_notes: string[];
}
