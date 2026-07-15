import { describe, expect, test } from 'vitest'
import { chineseLabel } from './labels'
import { intradayEventSemantics, isActionableIntradayEvent } from './eventSemantics'

describe('盘中资金与量价事件语义', () => {
  test('所有新增事件均有中文标签', () => {
    const events = [
      'SECTOR_FLOW_TURN_OUT',
      'SECTOR_FLOW_WEAKENING',
      'SHRINKING_RISE_DIVERGENCE',
      'SHRINKING_REBOUND_UNCONFIRMED',
      'SHRINKING_DECLINE_EXHAUSTION_WATCH',
      'SHRINKING_DECLINE_WEAKNESS',
      'SHRINKING_PULLBACK_SUPPORT_WATCH',
      'VOLUME_REBOUND_CONFIRMED',
      'VOLUME_DOWN_FLOW_ACCELERATION',
      'FLOW_TURN_OUT_DISTRIBUTION_WARNING',
    ]
    events.forEach(event => expect(chineseLabel(event)).not.toBe(event))
  })

  test('放量下跌资金加速流出使用高风险红色等级', () => {
    const semantics = intradayEventSemantics('VOLUME_DOWN_FLOW_ACCELERATION', 'critical')
    expect(semantics.kind).toBe('risk')
    expect(semantics.toneClass).toBe('risk-high')
    expect(semantics.guidance).toContain('禁止逆势补仓')
  })

  test('反弹确认与缩量回踩观察不被误标为风险', () => {
    expect(intradayEventSemantics('VOLUME_REBOUND_CONFIRMED', 'warning').toneClass).toBe('opportunity-positive')
    expect(intradayEventSemantics('SHRINKING_PULLBACK_SUPPORT_WATCH', 'warning').toneClass).toBe('opportunity-watch')
    expect(intradayEventSemantics('PANIC_SELL_GUARD', 'warning').toneClass).toBe('opportunity-watch')
    expect(isActionableIntradayEvent('SHRINKING_DECLINE_EXHAUSTION_WATCH', 'info')).toBe(true)
  })
})
