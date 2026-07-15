const PRIVATE_EVIDENCE_LABELS = [
  '账户总资产', '总资产', '期初资产', '当前资产', '可用资金', '持仓市值', '市值',
  '盈亏金额', '今日盈亏', '当日亏损', '账户亏损', '日内亏损', '已实现盈亏', '浮动盈亏', '累计浮盈', '持仓盈亏', '当前盈亏',
  '最大浮盈', '利润回撤', '浮盈', '成本价', '持仓成本', '成本', '持仓数量', '当前数量',
  '可卖数量', '可卖', '今日买入', '计划卖出', '实际卖出', '已卖', '实际接回', '已接回',
  '接回数量', '卖出数量', '买入数量', '持仓仓位', '当前仓位', '建议仓位', '仓位比例', '持仓比例',
]

const NUMBER_TEXT = '[+\\-]?\\d[\\d,]*(?:\\.\\d+)?'
const PRIVATE_EVIDENCE_VALUE = `${NUMBER_TEXT}\\s*(?:元|万元|万|亿|%|pct|个百分点|个百分(?:点)?|股|手)?`
const PRIVATE_EVIDENCE_PATTERN = new RegExp(
  `(${PRIVATE_EVIDENCE_LABELS.join('|')})\\s*(?:为|是|约|达到|达|[:：=])?\\s*${PRIVATE_EVIDENCE_VALUE}`,
  'gi',
)
const PRIVATE_SHARE_ACTION_PATTERN = new RegExp(
  `((?:卖出|买入|接回|买回|减仓|加仓|补仓|持有|退出)\\s*)${NUMBER_TEXT}\\s*(?:万|亿)?\\s*(?:股|手)`,
  'gi',
)

/**
 * 脱敏持仓/执行证据里的账户口径，同时保留当前价、VWAP、涨跌幅等公开行情证据。
 * 不应把原文放进 data/title/aria 属性；调用方只渲染本函数返回值。
 */
export function redactSensitiveEvidence(value: string) {
  return value
    .replace(PRIVATE_EVIDENCE_PATTERN, (_match, label: string) => `${label} ******`)
    .replace(PRIVATE_SHARE_ACTION_PATTERN, (_match, action: string) => `${action}******`)
}
