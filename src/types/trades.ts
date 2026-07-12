export interface TradeLog {
  id?: number;
  code: string;
  name: string;
  traded_at?: string;
  side: string;
  price: number;
  quantity: number;
  amount?: number;
  total_asset: number;
  position_ratio?: number;
  cost_price: number;
  stop_loss_price?: number;
  reason: string;
  mode: string;
  compliant: boolean;
  human_tags: string[];
  review?: TradeReview | null;
}

export interface TradeLogOut extends TradeLog {
  id: number;
  traded_at: string;
  amount: number;
  position_ratio: number;
  stop_loss_price: number;
  review?: TradeReview | null;
}

export interface TradeReview {
  id: number;
  trade_id: number;
  code: string;
  name: string;
  verdict: string;
  status: string;
  discipline_score: number;
  summary: string;
  stock_context: string;
  sector_context: string;
  market_context: string;
  error_message: string;
  mistakes: string[];
  avoid_actions: string[];
  weakness_tags: string[];
  created_at: string;
}

export interface GrowthProfile {
  trade_count: number;
  review_count: number;
  dominant_weaknesses: string[];
  frequent_mistakes: string[];
  current_focus: string;
  improvement_actions: string[];
  recent_scores: number[];
}

export interface CalibrationIssue {
  level: string;
  title: string;
  detail: string;
  action: string;
  code: string;
  name: string;
}

export interface PlanDeviation {
  plan_id: number;
  code: string;
  name: string;
  plan_date: string;
  expectation: string;
  execution: string;
  deviation: string;
  severity: string;
}

export interface FeedbackSummary {
  status: string;
  count: number;
}

export interface CalibrationMetric {
  key: string;
  label: string;
  sample_count: number;
  success_count: number;
  fail_count: number;
  success_rate: number;
  average_value: number;
  verdict: string;
  evidence: string[];
}

export interface CalibrationSuggestion {
  level: string;
  target: string;
  suggestion: string;
  reason: string;
  sample_count: number;
}

export interface CalibrationRuleChange {
  rule_id: number;
  display_name: string;
  field: string;
  before: number;
  after: number;
}

export interface CalibrationProposal {
  metric_key: string;
  sample_count: number;
  eligible: boolean;
  minimum_samples: number;
  rationale: string;
  changes: CalibrationRuleChange[];
}

export interface CalibrationRun {
  id: number;
  metric_key: string;
  sample_count: number;
  status: string;
  rationale: string;
  changes: CalibrationRuleChange[];
  created_at: string;
  rolled_back_at: string | null;
}

export interface ReviewCalibrationSummary {
  trade_count: number;
  review_count: number;
  plan_review_count: number;
  missing_plan_review_count: number;
  execution_feedback_count: number;
  ignored_recommendation_count: number;
  pending_review_count: number;
  avg_discipline_score: number;
  focus: string;
  issues: CalibrationIssue[];
  recent_plan_deviations: PlanDeviation[];
  feedback_summary: FeedbackSummary[];
  model_metrics: CalibrationMetric[];
  calibration_suggestions: CalibrationSuggestion[];
  next_actions: string[];
}

export interface PreTradeCheckIn {
  code: string;
  name: string;
  market_grade: string;
  position_ratio: number;
  target_role: string;
  is_mainline: boolean;
  has_sector_response: boolean;
  has_volume_price_confirm: boolean;
  buy_point: string;
  stop_loss_price: number;
  current_price: number;
  mode: string;
}

export interface PreTradeCheckOut {
  decision: string;
  score: number;
  allowed_position_ratio: number;
  warnings: string[];
  required_actions: string[];
}
