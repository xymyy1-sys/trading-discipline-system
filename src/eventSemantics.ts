export type IntradayEventKind = 'risk' | 'opportunity' | 'watch' | 'neutral'

export type IntradayEventSemantics = {
  kind: IntradayEventKind
  toneClass: 'risk-high' | 'risk-medium' | 'opportunity-positive' | 'opportunity-watch' | 'event-neutral'
  guidance: string
}

const CRITICAL_RISK_EVENTS = new Set([
  'VOLUME_DOWN_FLOW_ACCELERATION',
])

const RISK_EVENTS = new Set([
  'SECTOR_FLOW_TURN_OUT',
  'SECTOR_FLOW_WEAKENING',
  'SHRINKING_RISE_DIVERGENCE',
  'SHRINKING_REBOUND_UNCONFIRMED',
  'SHRINKING_DECLINE_WEAKNESS',
  'FLOW_TURN_OUT_DISTRIBUTION_WARNING',
  'HIGH_SELL_WINDOW',
])

const POSITIVE_EVENTS = new Set([
  'SECTOR_FLOW_TURN_IN',
  'SECTOR_FLOW_RECOVERY',
  'VOLUME_FLOW_STRENGTH_CONFIRMED',
  'VOLUME_REBOUND_CONFIRMED',
])

const WATCH_EVENTS = new Set([
  'SHRINKING_DECLINE_EXHAUSTION_WATCH',
  'SHRINKING_PULLBACK_SUPPORT_WATCH',
  'FLOW_TURN_IN_REBOUND_WATCH',
  'LOW_PANIC_SELL_GUARD',
  'PANIC_SELL_GUARD',
  'CONTRARIAN_ADD_EVALUATION',
])

export const ACTIONABLE_FLOW_VOLUME_EVENTS = new Set([
  ...CRITICAL_RISK_EVENTS,
  ...RISK_EVENTS,
  ...POSITIVE_EVENTS,
  ...WATCH_EVENTS,
])

const GUIDANCE: Record<string, string> = {
  SECTOR_FLOW_TURN_OUT: '板块资金已由流入拐为流出，降低乐观预期并核对利润保护条件。',
  SECTOR_FLOW_WEAKENING: '板块资金边际转弱，禁止加仓；等待流出收窄或重新拐入。',
  SHRINKING_RISE_DIVERGENCE: '缩量上涨与资金转弱背离，禁止追高，等待放量确认。',
  SHRINKING_REBOUND_UNCONFIRMED: '反弹未获量能和分时均价确认，不追反弹、不急于买回。',
  SHRINKING_DECLINE_WEAKNESS: '缩量不等于见底；资金仍弱时禁止接飞刀。',
  VOLUME_DOWN_FLOW_ACCELERATION: '放量下跌且资金加速流出，禁止逆势补仓。',
  FLOW_TURN_OUT_DISTRIBUTION_WARNING: '上涨中资金拐出，提高利润保护；跌破分时均价后按兑现窗口处理。',
  SECTOR_FLOW_TURN_IN: '板块资金开始拐入，先观察持续性，不能仅凭拐点追涨。',
  SECTOR_FLOW_RECOVERY: '板块资金边际修复，等待价格和分时均价同步确认。',
  VOLUME_FLOW_STRENGTH_CONFIRMED: '放量上涨且资金改善；回踩分时均价不破后再确认延续。',
  VOLUME_REBOUND_CONFIRMED: '放量反弹已站回分时均价，停止沿用低点卖出结论；仍不自动加仓。',
  SHRINKING_DECLINE_EXHAUSTION_WATCH: '抛压可能衰减，不在低位追卖；等待低点抬高和分时均价修复。',
  SHRINKING_PULLBACK_SUPPORT_WATCH: '缩量回踩仍有承接，不因一次回踩恐慌卖出。',
  FLOW_TURN_IN_REBOUND_WATCH: '下跌中资金开始拐入，停止低位追卖，等待分时均价确认。',
  LOW_PANIC_SELL_GUARD: '接近日内低位且资金停止恶化，禁止恐慌追卖；固定硬止损仍优先。',
  PANIC_SELL_GUARD: '接近日内低位且承接尚未失效，先停止恐慌追卖；固定硬止损仍优先。',
  CONTRARIAN_ADD_EVALUATION: '仅进入逆势加仓评估，必须继续等待板块、资金和分时均价共振，不自动买入。',
  HIGH_SELL_WINDOW: '冲高兑现窗口已出现，按利润保护计划分批处理，不因盘中幻想撤销规则。',
}

export function intradayEventSemantics(eventType?: string | null, severity?: string | null): IntradayEventSemantics {
  const type = String(eventType || '').toUpperCase()
  const level = String(severity || '').toLowerCase()
  // 已知事件的业务语义优先于后端的通用 severity。
  // 例如“低位禁止恐慌卖出”即使为了提醒可见性被标记 warning，
  // 也不能在前端误染成风险红色。
  if (CRITICAL_RISK_EVENTS.has(type)) {
    return {
      kind: 'risk',
      toneClass: 'risk-high',
      guidance: GUIDANCE[type] || '出现严重风险证据，立即核对执行闸门并降低风险。',
    }
  }
  if (RISK_EVENTS.has(type)) {
    return {
      kind: 'risk',
      toneClass: 'risk-medium',
      guidance: GUIDANCE[type] || '出现风险证据，按计划核对失效条件。',
    }
  }
  if (POSITIVE_EVENTS.has(type)) {
    return {
      kind: 'opportunity',
      toneClass: 'opportunity-positive',
      guidance: GUIDANCE[type] || '出现正向证据，等待持续性确认。',
    }
  }
  if (WATCH_EVENTS.has(type)) {
    return {
      kind: 'watch',
      toneClass: 'opportunity-watch',
      guidance: GUIDANCE[type] || '进入观察，不据此直接交易。',
    }
  }
  if (level === 'critical') {
    return {
      kind: 'risk',
      toneClass: 'risk-high',
      guidance: '出现严重风险证据，立即核对执行闸门并降低风险。',
    }
  }
  if (level === 'warning') {
    return {
      kind: 'risk',
      toneClass: 'risk-medium',
      guidance: '出现风险证据，按计划核对失效条件。',
    }
  }
  return {
    kind: 'neutral',
    toneClass: 'event-neutral',
    guidance: '记录证据变化，等待后续量价确认。',
  }
}

export function isActionableIntradayEvent(eventType?: string | null, severity?: string | null) {
  const level = String(severity || '').toLowerCase()
  return ACTIONABLE_FLOW_VOLUME_EVENTS.has(String(eventType || '').toUpperCase())
    || level === 'warning'
    || level === 'critical'
}
