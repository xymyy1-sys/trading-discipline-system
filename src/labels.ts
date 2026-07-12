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
  NO_T: '禁止做T', INTRADAY_T: '日内做T', ROLLING_T: '滚动做T',
  PRICE: '价格', EXPECTATION: '预期', VOLUME_PRICE: '量价', ACTION: '动作',
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
