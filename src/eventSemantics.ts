export type IntradayEventKind = 'risk' | 'opportunity' | 'watch' | 'neutral'

export type IntradayEventSemantics = {
  kind: IntradayEventKind
  toneClass: 'risk-high' | 'risk-medium' | 'opportunity-positive' | 'opportunity-watch' | 'event-neutral'
  guidance: string
}

const CRITICAL_RISK_EVENTS = new Set([
  'VOLUME_DOWN_FLOW_ACCELERATION',
  'SECTOR_DISTRIBUTION_RISK',
])

const RISK_EVENTS = new Set([
  'SECTOR_FLOW_TURN_OUT',
  'SECTOR_FLOW_WEAKENING',
  'SHRINKING_RISE_DIVERGENCE',
  'SHRINKING_REBOUND_UNCONFIRMED',
  'SHRINKING_DECLINE_WEAKNESS',
  'FLOW_TURN_OUT_DISTRIBUTION_WARNING',
  'HIGH_SELL_WINDOW',
  'NEWS_NEGATIVE_IMPACT_CONFIRMED',
  'HOLDING_NEWS_NEGATIVE_IMPACT_CONFIRMED',
  'PRICE_VOLUME_PATTERN_SHRINKING_RISE_FRAGILE',
  'PRICE_VOLUME_PATTERN_VOLUME_RISE_STALLED',
])

const POSITIVE_EVENTS = new Set([
  'SECTOR_FLOW_TURN_IN',
  'SECTOR_FLOW_RECOVERY',
  'VOLUME_FLOW_STRENGTH_CONFIRMED',
  'VOLUME_REBOUND_CONFIRMED',
  'SECTOR_INCREMENT_CONFIRMED',
  'NEWS_POSITIVE_IMPACT_CONFIRMED',
  'HOLDING_NEWS_POSITIVE_IMPACT_CONFIRMED',
  'PRICE_VOLUME_PATTERN_VOLUME_RISE_CONFIRMED',
])

const WATCH_EVENTS = new Set([
  'SHRINKING_DECLINE_EXHAUSTION_WATCH',
  'SHRINKING_PULLBACK_SUPPORT_WATCH',
  'FLOW_TURN_IN_REBOUND_WATCH',
  'LOW_PANIC_SELL_GUARD',
  'PANIC_SELL_GUARD',
  'CONTRARIAN_ADD_EVALUATION',
  'SECTOR_INCREMENT_WATCH',
  'HOLDING_NEWS_PENDING_VALIDATION',
  'HOLDING_NEWS_IMPACT_INVALIDATED',
  'NEWS_IMPACT_INVALIDATED',
  'PRICE_VOLUME_PATTERN_SHRINKING_RISE_SUPPORTED',
  'PRICE_VOLUME_PATTERN_SHRINKING_RISE_PENDING',
  'PRICE_VOLUME_PATTERN_SHRINKING_PULLBACK_HOLD',
  'PRICE_VOLUME_PATTERN_VOLUME_RISE_PENDING',
])

export const ACTIONABLE_FLOW_VOLUME_EVENTS = new Set([
  ...CRITICAL_RISK_EVENTS,
  ...RISK_EVENTS,
  ...POSITIVE_EVENTS,
  ...WATCH_EVENTS,
])

const GUIDANCE: Record<string, string> = {
  SECTOR_FLOW_TURN_OUT: '板块订单流方向估算已由正转负，降低乐观预期并核对利润保护条件。',
  SECTOR_FLOW_WEAKENING: '板块订单流方向估算边际转弱，禁止加仓；等待负值收窄或重新转正。',
  SHRINKING_RISE_DIVERGENCE: '缩量上涨与订单流方向估算转弱背离，禁止追高，等待放量确认。',
  SHRINKING_REBOUND_UNCONFIRMED: '反弹未获量能和分时均价确认，不追反弹、不急于买回。',
  SHRINKING_DECLINE_WEAKNESS: '缩量不等于见底；订单流方向估算仍弱时禁止接飞刀。',
  VOLUME_DOWN_FLOW_ACCELERATION: '放量下跌且订单流方向负值加速，禁止逆势补仓。',
  FLOW_TURN_OUT_DISTRIBUTION_WARNING: '上涨中订单流方向估算转负，提高利润保护；跌破分时均价后按兑现窗口处理。',
  SECTOR_DISTRIBUTION_RISK: '板块资金承载与价格响应已形成派发联合证据；停止追涨、加仓和做T买回，已有仓位仍须叠加个股预期、量价或固定止损才执行卖出。',
  SECTOR_FLOW_TURN_IN: '板块订单流方向估算开始转正，先观察持续性，不能仅凭拐点追涨。',
  SECTOR_FLOW_RECOVERY: '板块订单流方向估算边际修复，等待价格和分时均价同步确认。',
  VOLUME_FLOW_STRENGTH_CONFIRMED: '放量上涨且订单流方向估算改善；回踩分时均价不破后再确认延续。',
  VOLUME_REBOUND_CONFIRMED: '放量反弹已站回分时均价，停止沿用低点卖出结论；仍不自动加仓。',
  SHRINKING_DECLINE_EXHAUSTION_WATCH: '抛压可能衰减，不在低位追卖；等待低点抬高和分时均价修复。',
  SHRINKING_PULLBACK_SUPPORT_WATCH: '缩量回踩仍有承接，不因一次回踩恐慌卖出。',
  PRICE_VOLUME_PATTERN_SHRINKING_RISE_SUPPORTED: '缩量上涨同时抛压较轻，说明上方阻力暂小；只保留延续观察，不能把缩量单独当成追涨依据。',
  PRICE_VOLUME_PATTERN_SHRINKING_RISE_FRAGILE: '缩量上涨的订单流、位置或承载证据偏弱；禁止追高，等待回踩分时均价和增量资金确认。',
  PRICE_VOLUME_PATTERN_SHRINKING_RISE_PENDING: '缩量上涨既可能是阻力较小，也可能是承接不足；等待订单流和回踩结果完成区分。',
  PRICE_VOLUME_PATTERN_SHRINKING_PULLBACK_HOLD: '缩量回踩仍守住分时均价线，先观察承接，不在一次回踩中恐慌卖出。',
  PRICE_VOLUME_PATTERN_VOLUME_RISE_CONFIRMED: '放量上涨同时获得订单流、价格位置与承载效率确认；继续观察回踩是否守住分时均价，不自动追涨。',
  PRICE_VOLUME_PATTERN_VOLUME_RISE_STALLED: '成交放大但价格推动效率下降，说明分歧和上方抛压增加；禁止追高并核对冲高兑现条件。',
  PRICE_VOLUME_PATTERN_VOLUME_RISE_PENDING: '成交放大只代表分歧增加；等待价格站稳和订单流持续，不能只凭放量判断延续。',
  FLOW_TURN_IN_REBOUND_WATCH: '下跌中订单流方向估算开始转正，停止低位追卖，等待分时均价确认。',
  LOW_PANIC_SELL_GUARD: '接近日内低位且订单流方向估算停止恶化，禁止恐慌追卖；固定硬止损仍优先。',
  PANIC_SELL_GUARD: '接近日内低位且承接尚未失效，先停止恐慌追卖；固定硬止损仍优先。',
  CONTRARIAN_ADD_EVALUATION: '仅进入逆势加仓评估，必须继续等待板块、订单流方向估算和分时均价共振，不自动买入。',
  HIGH_SELL_WINDOW: '冲高兑现窗口已出现，按利润保护计划分批处理，不因盘中幻想撤销规则。',
  SECTOR_INCREMENT_CONFIRMED: '新增涨停、板块订单流方向估算与价格强度共同确认；仅加入观察，禁止追后排。',
  SECTOR_INCREMENT_WATCH: '板块出现增量迹象但证据尚未齐备，等待订单流方向与价格继续确认。',
  NEWS_NEGATIVE_IMPACT_CONFIRMED: '可追溯消息发布后，订单流方向与量价确认负面影响；不把市场同向表现写成消息因果。',
  HOLDING_NEWS_NEGATIVE_IMPACT_CONFIRMED: '持仓相关负面消息的市场影响已获订单流方向与量价确认；按既有失效条件处理，不因标题直接卖出。',
  NEWS_POSITIVE_IMPACT_CONFIRMED: '可追溯消息发布后，订单流方向与量价确认正向影响；仍不自动追涨。',
  HOLDING_NEWS_POSITIVE_IMPACT_CONFIRMED: '持仓相关消息的正向影响获得订单流方向与量价确认；不自动解除硬风险或触发加仓。',
  HOLDING_NEWS_PENDING_VALIDATION: '持仓相关消息仅形成待验证假设，等待发布后的订单流方向、价格和分时均价共同确认。',
  HOLDING_NEWS_IMPACT_INVALIDATED: '后续订单流方向与量价不支持消息方向，降低该消息权重，不据此交易。',
  NEWS_IMPACT_INVALIDATED: '后续订单流方向与量价不支持消息方向，降低该消息权重，不据此交易。',
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
