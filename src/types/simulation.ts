export type SimulationStrategyType = 'limit_up' | 'expectation_volume_price' | 'holding_execution'
export type SimulationOrderSide = 'BUY' | 'SELL'
export type SimulationOrderType = 'LIMIT' | 'MARKET'

export interface SimulationAccount {
  id: number
  name: string
  initial_cash: number
  cash: number
  commission_rate: number
  minimum_commission: number
  stamp_tax_rate: number
  transfer_fee_rate: number
  account_type: 'manual' | 'shadow' | string
  status: string
  created_at: string
  updated_at: string
}

export interface SimulationPosition {
  id: number
  account_id: number
  code: string
  name: string
  quantity: number
  available_quantity: number
  today_buy_quantity: number
  average_cost: number
  market_price: number
  market_value: number
  unrealized_pnl: number
  realized_pnl: number
  last_rollover_date: string
  updated_at: string
}

export interface SimulationOrder {
  id: number
  account_id: number
  decision_evidence_snapshot_id: number
  strategy_source: SimulationStrategyType
  code: string
  name: string
  side: SimulationOrderSide
  order_type: SimulationOrderType
  limit_price: number
  quantity: number
  filled_quantity: number
  average_fill_price: number
  status: string
  reject_reason: string
  client_note: string
  trade_date: string
  submitted_at: string
  last_evaluated_at: string
}

export interface SimulationFill {
  id: number
  order_id: number
  account_id: number
  fill_evidence_snapshot_id: number
  strategy_source: SimulationStrategyType
  code: string
  name: string
  side: SimulationOrderSide
  price: number
  quantity: number
  gross_amount: number
  commission: number
  stamp_tax: number
  transfer_fee: number
  net_cash_flow: number
  realized_pnl: number
  trade_date: string
  filled_at: string
}

export interface SimulationDailyEquity {
  id: number
  account_id: number
  trade_date: string
  cash: number
  market_value: number
  total_equity: number
  daily_pnl: number
  total_pnl: number
  return_pct: number
  drawdown_pct: number
  captured_at: string
}

export interface SimulationShadowDecision {
  id: number
  account_id: number
  signal_key: string
  strategy_source: SimulationStrategyType
  source_kind: string
  source_id: number | null
  rule_version: string
  source_version: string
  trade_date: string
  source_at: string | null
  evaluated_at: string
  code: string
  name: string
  intent: string
  side: SimulationOrderSide | ''
  quantity: number
  status: string
  reason: string
  order_id: number | null
  evidence_json: string
}

export interface SimulationEvidence {
  id: number
  account_id: number
  code: string
  name: string
  strategy_source: SimulationStrategyType
  trade_date: string
  version: number
  captured_at: string
  quote_time: string | null
  data_quality: string
  market_regime: string
  expectation_gap_score: number
  expectation_gap_band: string
  volume_price_state: string
  sector_state: string
  content_hash: string
  quote_json: string
  market_json: string
  sector_json: string
  expectation_json: string
  volume_price_json: string
  source_versions_json: string
}

export interface SimulationPerformanceSlice {
  key: string
  closed_trade_count: number
  sell_count: number
  win_count: number
  loss_count: number
  win_rate: number
  total_realized_pnl: number
  average_win: number
  average_loss: number
  profit_loss_ratio: number
}

export interface SimulationPerformance {
  account_id: number
  closed_trade_count: number
  sell_count: number
  win_count: number
  loss_count: number
  win_rate: number
  total_realized_pnl: number
  profit_loss_ratio: number
  maximum_drawdown_pct: number
  by_strategy: SimulationPerformanceSlice[]
  by_market_regime: SimulationPerformanceSlice[]
  by_expectation_gap: SimulationPerformanceSlice[]
}

export interface SimulationCalibrationMetric {
  key: string
  sample_count: number
  win_rate: number
  average_return_pct: number
  median_return_pct: number
  profit_loss_ratio: number
  total_realized_pnl: number
}

export interface SimulationCalibrationCandidate {
  target: string
  field: string
  direction: 'tighten' | 'loosen' | 'hold'
  suggestion: string
  reason: string
  sample_count: number
  support_metric: string
}

export interface SimulationCalibrationProposal {
  account_id: number
  generated_at: string
  status: string
  eligible: boolean
  candidate_generation_allowed: boolean
  statistics_only: boolean
  minimum_samples: number
  statistical_sample_count: number
  usable_sample_count: number
  excluded_sample_count: number
  exclusion_reasons: string[]
  summary: string
  overall: SimulationCalibrationMetric
  by_strategy: SimulationCalibrationMetric[]
  by_market_regime: SimulationCalibrationMetric[]
  by_expectation_gap: SimulationCalibrationMetric[]
  maximum_drawdown_pct: number
  candidates: SimulationCalibrationCandidate[]
  evidence: string[]
  limitations: string[]
  requires_manual_confirmation: boolean
  auto_apply_allowed: false
}
