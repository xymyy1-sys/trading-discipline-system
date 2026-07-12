const LABELS: Record<string, string> = {
  STRONGER: '强于预期', MATCHED: '符合预期', WEAKER: '弱于预期',
  SLIGHTLY_WEAKER: '略弱于预期', INVALID: '预期证伪', UNKNOWN: '未知',
  EXIT_REQUIRED: '必须退出', REDUCE_REQUIRED: '必须减仓',
  EXPECTATION_INVALIDATED: '预期失效', STOP_LOSS_WARNING: '止损警告',
  NORMAL_HOLD: '正常持有', PROFIT_EXPANSION: '利润扩张',
  PROFIT_PROTECTION: '利润保护', DIVERGENCE_HOLD: '分歧持有',
  DEGRADED_DATA_OBSERVATION: '数据降级观察',
  manual: '手动数据', missing: '数据缺失', realtime: '实时数据',
  degraded: '数据降级', degraded_vwap: '均价线降级',
  planned: '已计划', completed: '已完成', done: '已完成',
  LOW: '低', MEDIUM: '中', HIGH: '高',
  FLOW_NEW_HIGH: '资金新高', FLOW_PEAK_REVERSAL: '资金见顶回落',
  FLOW_TURN_NEGATIVE: '资金由正转负',
  REDUCE: '减仓', PROTECT: '利润保护', EXIT: '退出', HOLD: '持有', OBSERVE: '观察',
  REDUCE_ALL: '全面减仓', BLOCK_NEW_POSITION: '禁止新开仓',
  NO_T: '禁止做T', INTRADAY_T: '日内做T', ROLLING_T: '滚动做T',
  PRICE: '价格', EXPECTATION: '预期', VOLUME_PRICE: '量价', ACTION: '动作',
  EXTREME_STRONG: '极强', STRONG: '强势', NEUTRAL: '中性', WEAK: '弱势',
  REPAIR: '修复阶段', EBB: '退潮阶段',
  STRONG_TO_STRONGER: '强势转更强', WEAK_TO_STRONG: '弱转强',
  DIVERGENCE_TO_CONSENSUS: '分歧转一致', CONSENSUS_TO_DIVERGENCE: '一致转分歧',
  STRONG_TO_WEAK: '强转弱', REPAIR_SUCCESS: '修复成功', REPAIR_FAILED: '修复失败',
  VWAP_BROKEN: '跌破分时均价线', VWAP_RECOVERED: '收复分时均价线',
  VOLUME_PRICE_WEAKENING: '量价转弱', VOLUME_PRICE_STRENGTHENING: '量价转强',
  PROFIT_DRAWDOWN_WARNING: '利润回撤预警', TIME_STOP_TRIGGERED: '时间止损触发',
  HIGH_OPEN_FAILED_BREAKOUT: '高开冲板失败', LIMIT_UP_OPENED: '涨停开板',
  BELOW_VWAP: '位于分时均价线下方', ABOVE_VWAP: '位于分时均价线上方',
  VOLUME_EXPANSION: '放量', VOLUME_CONTRACTION: '缩量',
  EXPECTATION_VOLUME_BREAKDOWN: '预期与量价同步转弱',
  EXPECTATION_DOWNGRADE: '预期下调', VWAP_BREAKDOWN: '跌破分时均价线',
  VWAP_STRONG: '分时均价线上方强势', VOLUME_PRICE_NEUTRAL: '量价中性',
  HIGH_VOLUME_STAGNATION: '高位放量滞涨', PROFIT_TO_LOSS_RISK: '浮盈转亏风险',
  DIVERGENCE_RESEAL: '分歧后回封', REPAIR_CONFIRMED: '修复确认',
  SECTOR_REPAIR: '板块修复', TREND_BREAKOUT: '趋势突破',
  SLIGHTLY_STRONGER: '略强于预期',
}

export function chineseLabel(value?: string | null) {
  if (!value) return '暂无'
  return LABELS[value] ?? value
}

export function chineseEvidence(value: string) {
  let text = value
  const replacements: Array<[RegExp, string]> = [
    [/expectation evidence missing/gi, '缺少预期证据'],
    [/real minute VWAP is unavailable/gi, '真实分钟均价线不可用'],
    [/real minute VWAP is reliable/gi, '真实分钟均价线可靠'],
    [/expectation\s+MATCHED/gi, '预期符合'],
    [/expectation\s+STRONGER/gi, '预期增强'],
    [/expectation\s+WEAKER/gi, '预期转弱'],
    [/execution state\s+/gi, '执行状态：'],
  ]
  replacements.forEach(([pattern, replacement]) => { text = text.replace(pattern, replacement) })
  Object.entries(LABELS).forEach(([key, label]) => { text = text.replaceAll(key, label) })
  return text
}
