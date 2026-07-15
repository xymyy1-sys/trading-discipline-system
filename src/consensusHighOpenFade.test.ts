import { describe, expect, test } from 'vitest'
import { buildConsensusHighOpenFadeView } from './consensusHighOpenFade'
import type { ConsensusHighOpenFade } from './types'

function signal(overrides: Partial<ConsensusHighOpenFade>): ConsensusHighOpenFade {
  return {
    code: 'CONSENSUS_HIGH_OPEN_NOT_CONFIRMED',
    label: '尚未确认',
    status: 'NOT_CONFIRMED',
    triggered: false,
    risk_level: 'LOW',
    score: 32,
    evidence: [],
    counter_evidence: [],
    missing_fields: [],
    allowed_actions: [],
    forbidden_actions: [],
    next_validation_points: [],
    methodology_note: '规则说明',
    as_of: '2026-07-16T09:35:00+08:00',
    trade_date: '2026-07-16',
    source: ['东方财富集合竞价'],
    input_evidence: {},
    ...overrides,
  }
}

describe('一致性高开兑现风险显示语义', () => {
  test('兼容后端 CONFIRMED 并将高风险标成红色风险态', () => {
    const view = buildConsensusHighOpenFadeView(signal({
      code: 'CONSENSUS_HIGH_OPEN_FADE',
      status: 'CONFIRMED',
      triggered: true,
      risk_level: 'HIGH',
      score: 86,
    }))

    expect(view.state).toBe('triggered-high')
    expect(view.toneClass).toBe('consensus-fade-triggered-high')
    expect(view.statusLabel).toBe('已触发一致性高开兑现风险')
    expect(view.scoreLabel).toBe('86 分')
    expect(view.riskColored).toBe(true)
  })

  test('数据缺口保持中性且绝不表述为无风险', () => {
    const view = buildConsensusHighOpenFadeView(signal({
      code: 'DATA_GAP',
      status: 'DATA_GAP',
      risk_level: 'UNKNOWN',
      score: null,
      label: '证据不足，不能判断一致性兑现',
    }))

    expect(view.state).toBe('data-gap')
    expect(view.statusLabel).toBe('证据不足，无法判断风险')
    expect(view.statusLabel).not.toContain('无风险')
    expect(view.scoreLabel).toBe('--')
    expect(view.riskColored).toBe(false)
  })

  test('未确认状态不使用风险色，也不宣称安全', () => {
    const view = buildConsensusHighOpenFadeView(signal({ status: 'NOT_TRIGGERED' }))

    expect(view.state).toBe('not-confirmed')
    expect(view.toneClass).toBe('consensus-fade-not-confirmed')
    expect(view.statusLabel).toBe('一致性兑现风险尚未确认')
    expect(view.riskColored).toBe(false)
  })
})
