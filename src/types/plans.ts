export interface ClassificationBasis {
  sector: string;
  mainline_position: string;
  fund_flow: string;
  amount: string;
  turnover: string;
  trend: string;
  support: string;
  pressure: string;
  weaker_than_sector: boolean;
}

export interface AuctionStageCheck {
  stage: string;
  status: string;
  trigger: string;
  decision: string;
  required_action: string;
  evidence: string[];
}

export type PlanBranch = 'low_open_selloff' | 'range_open_balance' | 'high_open_rally' | 'data_gap';
export type PlanBranchStatus = 'pending' | 'active';
export type PlanAdviceLevel = 'observe' | 'positive' | 'warning' | 'critical';
export type PlanAdviceState = 'active' | 'withdrawn';
export type PlanAdviceChange = 'initialized' | 'unchanged' | 'upgraded' | 'downgraded' | 'withdrawn' | 'replaced';

export interface PlanAdviceHistoryItem {
  revision: number;
  advice: string;
  level: PlanAdviceLevel;
  state: PlanAdviceState;
  stage: string;
  branch: PlanBranch | string;
  reason: string;
  created_at: string;
  withdrawn_at?: string;
  withdraw_reason?: string;
}

export interface AuctionPlan {
  board_level: string;
  industry: string;
  concepts: string[];
  overnight_order: boolean;
  order_price: number;
  limit_up_price: number;
  keep_order_condition: string;
  cancel_condition: string;
  opening_confirmation: string;
  max_position_ratio: number;
  break_limit_action: string;
  notes: string;
  board_strength: string;
  leader_support: string[];
  limit_quality: string;
  expectation_level: string;
  strong_boundary_price: number;
  weak_reduce_price: number;
  weak_exit_price: number;
  risk_notes: string[];
  intraday_status: string;
  expected_state: string;
  expectation_match: string;
  operation_advice: string;
  volume_price_status: string;
  board_strength_detail: string[];
  next_day_script: string[];
  sell_trigger_cards: string[];
  refreshed_at: string;
  current_stage: string;
  stage_decision: string;
  action_ladder: string[];
  stage_checks: AuctionStageCheck[];
  mainline_name: string;
  mainline_rank: number | null;
  mainline_score: number | null;
  mainline_level: string;
  is_mainline: boolean | null;
  theme_stage: string;
  theme_stage_reason: string;
  identity_roles: string[];
  identity_action: string;
  position_rule: string;
  theme_evidence: string[];
  selected_branch?: PlanBranch;
  selected_branch_label?: string;
  branch_status?: PlanBranchStatus;
  branch_reason?: string;
  branch_selected_at?: string;
  current_advice?: string;
  advice_level?: PlanAdviceLevel;
  advice_state?: PlanAdviceState;
  advice_revision?: number;
  previous_advice?: string;
  advice_change?: PlanAdviceChange;
  advice_change_reason?: string;
  auto_refreshed_at?: string;
  advice_history?: PlanAdviceHistoryItem[];
}

export interface NextDayPlan {
  id?: number;
  plan_date: string;
  plan_type: string;
  holding_id: number | null;
  code: string;
  name: string;
  quantity: number;
  cost_price: number;
  current_price: number;
  market_value?: number;
  profit_amount?: number;
  profit_ratio?: number;
  price_source?: string;
  price_note?: string;
  position_ratio: number;
  holding_category: string;
  classification_basis: ClassificationBasis;
  outperform_condition: string;
  outperform_action: string;
  expected_condition: string;
  expected_action: string;
  underperform_condition: string;
  underperform_action: string;
  confirm_price: number;
  trim_price: number;
  trim_condition: string;
  trim_quantity: number;
  allow_buyback: boolean;
  buyback_price: number;
  buyback_condition: string;
  max_buyback_quantity: number;
  reduce_price: number;
  final_risk_price: number;
  stop_loss_4pct: number;
  limit_up_price: number;
  auction_plan: AuctionPlan;
  forbidden_actions: string[];
  risk_priority?: number;
  risk_warnings?: string[];
  review_expectation: string;
  review_execution: string;
  review_deviation: string;
  created_at?: string;
  updated_at?: string;
}

export interface NextDayPlanOut extends NextDayPlan {
  id: number;
  market_value: number;
  profit_amount: number;
  profit_ratio: number;
  price_source: string;
  price_note: string;
  risk_priority: number;
  risk_warnings: string[];
  created_at: string;
  updated_at: string;
}

export interface ExitCard {
  id: number;
  code: string;
  name: string;
  mode: string;
  max_position_ratio: number;
  confirm_price: number;
  trim_price: number;
  failure_price: number;
  outperform_condition: string;
  underperform_action: string;
  allow_buyback: boolean;
  buyback_limit_ratio: number;
  created_at: string;
}

export interface SellPlan {
  code: string;
  name: string;
  first_trim_price: number;
  second_exit_price: number;
  failure_price: number;
  sell_ratios: string[];
  allow_buyback: boolean;
  buyback_condition: string;
  condition_orders: string[];
}
