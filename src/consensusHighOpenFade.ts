import type { ConsensusHighOpenFade } from './types'

export type ConsensusHighOpenFadeState =
  | 'triggered-high'
  | 'triggered-medium'
  | 'data-gap'
  | 'not-confirmed'

export type ConsensusHighOpenFadeView = {
  state: ConsensusHighOpenFadeState
  toneClass: string
  statusLabel: string
  scoreLabel: string
  conclusion: string
  riskColored: boolean
}

function normalized(value: string | null | undefined) {
  return (value ?? '').trim().toUpperCase()
}

export function buildConsensusHighOpenFadeView(
  signal: ConsensusHighOpenFade,
): ConsensusHighOpenFadeView {
  const status = normalized(signal.status)
  const code = normalized(signal.code)
  const riskLevel = normalized(signal.risk_level)
  const dataGap = status === 'DATA_GAP' || code === 'DATA_GAP'
  const triggered = !dataGap && (
    signal.triggered
    || ['TRIGGERED', 'CONFIRMED'].includes(status)
    || code === 'CONSENSUS_HIGH_OPEN_FADE'
  )

  if (dataGap) {
    return {
      state: 'data-gap',
      toneClass: 'consensus-fade-data-gap',
      statusLabel: '证据不足，无法判断风险',
      scoreLabel: '--',
      conclusion: signal.label || '关键字段尚未齐备，等待真实竞价、开盘与承接数据。',
      riskColored: false,
    }
  }

  if (triggered) {
    const highRisk = riskLevel === 'HIGH' || riskLevel === 'EXTREME'
    return {
      state: highRisk ? 'triggered-high' : 'triggered-medium',
      toneClass: highRisk ? 'consensus-fade-triggered-high' : 'consensus-fade-triggered-medium',
      statusLabel: '已触发一致性高开兑现风险',
      scoreLabel: signal.score == null ? '--' : `${signal.score.toFixed(0)} 分`,
      conclusion: signal.label || '高开一致后承接转弱，需按证据降低追高与接飞刀风险。',
      riskColored: true,
    }
  }

  return {
    state: 'not-confirmed',
    toneClass: 'consensus-fade-not-confirmed',
    statusLabel: '一致性兑现风险尚未确认',
    scoreLabel: signal.score == null ? '--' : `${signal.score.toFixed(0)} 分`,
    conclusion: signal.label || '当前证据未确认高开一致性兑现，继续等待下一时点验证。',
    riskColored: false,
  }
}
