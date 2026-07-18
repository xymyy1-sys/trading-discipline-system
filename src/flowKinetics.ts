export type FlowKineticsFields = {
  flow_direction?: string | null
  flow_speed?: number | null
  flow_acceleration?: number | null
  flow_turning?: string | null
  flow_signal?: string | null
  flow_signal_level?: string | null
  flow_as_of?: string | null
  flow_window_minutes?: number | null
  flow_kinetics_reliable?: boolean
}

export type FlowKineticsView = {
  reliable: boolean
  signal: string
  direction: string
  speed: string | null
  acceleration: string | null
  asOf: string | null
  window: string | null
  tone: 'positive' | 'warning' | 'neutral' | 'waiting'
}

const DIRECTION_LABELS: Record<string, string> = {
  NET_INFLOW: '订单流方向净流入',
  NET_OUTFLOW: '订单流方向净流出',
  NEUTRAL: '订单流方向中性',
  UNKNOWN: '方向待确认',
}

const TURNING_LABELS: Record<string, string> = {
  TURN_TO_INFLOW: '订单流方向由净流出拐为净流入',
  TURN_TO_OUTFLOW: '订单流方向由净流入拐为净流出',
  OUTFLOW_NARROWING: '订单流方向净流出快速收窄',
  INFLOW_FADING: '订单流方向净流入快速回落',
  INFLOW_ACCELERATING: '订单流方向流入加速',
  OUTFLOW_ACCELERATING: '订单流方向流出加速',
  FLOW_IMPROVING: '订单流方向边际改善',
  FLOW_WEAKENING: '订单流方向边际转弱',
}

const POSITIVE_TURNS = new Set([
  'TURN_TO_INFLOW',
  'OUTFLOW_NARROWING',
  'INFLOW_ACCELERATING',
  'FLOW_IMPROVING',
])

const WARNING_TURNS = new Set([
  'TURN_TO_OUTFLOW',
  'INFLOW_FADING',
  'OUTFLOW_ACCELERATING',
  'FLOW_WEAKENING',
])

function formatSigned(value: number, digits: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`
}

function formatAsOf(value?: string | null) {
  if (!value) return null
  const matched = value.match(/(?:T|\s)(\d{2}:\d{2}(?::\d{2})?)/)
  return matched?.[1] ?? value
}

export function flowTurningLabel(value?: string | null) {
  const key = String(value || '').toUpperCase()
  if (!key) return ''
  return TURNING_LABELS[key] ?? '订单流方向拐点待识别'
}

export function buildFlowKineticsView(fields: FlowKineticsFields): FlowKineticsView {
  const speedPresent = fields.flow_speed !== null && fields.flow_speed !== undefined
  const explicitReliable = fields.flow_kinetics_reliable
  const reliable = explicitReliable === true || (
    explicitReliable === undefined
    && speedPresent
    && Boolean(fields.flow_as_of)
  )
  const turning = String(fields.flow_turning || '').toUpperCase()
  const direction = String(fields.flow_direction || '').toUpperCase()
  const signalLevel = String(fields.flow_signal_level || '').toUpperCase()

  if (!reliable) {
    return {
      reliable: false,
      signal: '等待至少两个真实时点',
      direction: DIRECTION_LABELS[direction] ?? '方向待确认',
      speed: null,
      acceleration: null,
      asOf: formatAsOf(fields.flow_as_of),
      window: null,
      tone: 'waiting',
    }
  }

  const fallbackSignal = flowTurningLabel(turning) || DIRECTION_LABELS[direction] || '订单流方向变化暂未形成显著拐点'
  return {
    reliable: true,
    signal: normalizeFlowSignalLabel(fields.flow_signal) || fallbackSignal,
    direction: DIRECTION_LABELS[direction] ?? '方向已确认',
    speed: speedPresent ? `${formatSigned(Number(fields.flow_speed), 3)} 亿/分钟` : null,
    acceleration: fields.flow_acceleration !== null && fields.flow_acceleration !== undefined
      ? `${formatSigned(Number(fields.flow_acceleration), 4)} 亿/分钟²`
      : '等待第三个真实时点',
    asOf: formatAsOf(fields.flow_as_of),
    window: fields.flow_window_minutes
      ? `${fields.flow_window_minutes} 个交易分钟`
      : null,
    tone: WARNING_TURNS.has(turning) || ['WARNING', 'RISK', 'HIGH', 'CRITICAL', '警告', '风险', '高'].includes(signalLevel)
      ? 'warning'
      : POSITIVE_TURNS.has(turning) || ['POSITIVE', 'IMPROVING', 'OPPORTUNITY', '正向', '改善', '机会'].includes(signalLevel)
        ? 'positive'
        : 'neutral',
  }
}

function normalizeFlowSignalLabel(value?: string | null) {
  return String(value || '')
    .replaceAll('主力净流入', '大单方向估算')
    .replaceAll('主力资金', '大单方向估算')
    .replaceAll('资金由净流出拐为净流入', '订单流方向由净流出拐为净流入')
    .replaceAll('资金由净流入拐为净流出', '订单流方向由净流入拐为净流出')
    .replaceAll('资金流入', '订单流方向流入')
    .replaceAll('资金流出', '订单流方向流出')
    .replaceAll('资金边际', '订单流方向边际')
    .replaceAll('资金价格背离', '订单流与价格背离')
    .replaceAll('资金转弱', '订单流方向转弱')
}

export function holdingFlowKineticsFields(fields: {
  sector_flow_direction?: string | null
  sector_flow_speed?: number | null
  sector_flow_acceleration?: number | null
  sector_flow_turning?: string | null
  sector_flow_signal?: string | null
  sector_flow_signal_level?: string | null
  sector_flow_as_of?: string | null
  sector_flow_window_minutes?: number | null
  sector_flow_kinetics_reliable?: boolean
}): FlowKineticsFields {
  return {
    flow_direction: fields.sector_flow_direction,
    flow_speed: fields.sector_flow_speed,
    flow_acceleration: fields.sector_flow_acceleration,
    flow_turning: fields.sector_flow_turning,
    flow_signal: fields.sector_flow_signal,
    flow_signal_level: fields.sector_flow_signal_level,
    flow_as_of: fields.sector_flow_as_of,
    flow_window_minutes: fields.sector_flow_window_minutes,
    flow_kinetics_reliable: fields.sector_flow_kinetics_reliable,
  }
}
