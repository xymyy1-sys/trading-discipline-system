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
      'SECTOR_DISTRIBUTION_RISK',
      'PRICE_VOLUME_PATTERN_SHRINKING_RISE_SUPPORTED',
      'PRICE_VOLUME_PATTERN_SHRINKING_RISE_FRAGILE',
      'PRICE_VOLUME_PATTERN_SHRINKING_RISE_PENDING',
      'PRICE_VOLUME_PATTERN_SHRINKING_PULLBACK_HOLD',
      'PRICE_VOLUME_PATTERN_VOLUME_RISE_CONFIRMED',
      'PRICE_VOLUME_PATTERN_VOLUME_RISE_STALLED',
      'PRICE_VOLUME_PATTERN_VOLUME_RISE_PENDING',
    ]
    events.forEach(event => expect(chineseLabel(event)).not.toBe(event))
  })

  test('放量下跌资金加速流出使用高风险红色等级', () => {
    const semantics = intradayEventSemantics('VOLUME_DOWN_FLOW_ACCELERATION', 'critical')
    expect(semantics.kind).toBe('risk')
    expect(semantics.toneClass).toBe('risk-high')
    expect(semantics.guidance).toContain('禁止逆势补仓')
  })

  test('板块派发联合证据标红但不越权生成机械卖出', () => {
    const semantics = intradayEventSemantics('SECTOR_DISTRIBUTION_RISK', 'critical')
    expect(semantics.kind).toBe('risk')
    expect(semantics.toneClass).toBe('risk-high')
    expect(semantics.guidance).toContain('仍须叠加个股预期')
  })

  test('反弹确认与缩量回踩观察不被误标为风险', () => {
    expect(intradayEventSemantics('VOLUME_REBOUND_CONFIRMED', 'warning').toneClass).toBe('opportunity-positive')
    expect(intradayEventSemantics('SHRINKING_PULLBACK_SUPPORT_WATCH', 'warning').toneClass).toBe('opportunity-watch')
    expect(intradayEventSemantics('PANIC_SELL_GUARD', 'warning').toneClass).toBe('opportunity-watch')
    expect(isActionableIntradayEvent('SHRINKING_DECLINE_EXHAUSTION_WATCH', 'info')).toBe(true)
    expect(intradayEventSemantics('PRICE_VOLUME_PATTERN_SHRINKING_PULLBACK_HOLD', 'info').kind).toBe('watch')
    expect(intradayEventSemantics('PRICE_VOLUME_PATTERN_SHRINKING_RISE_SUPPORTED', 'info').kind).toBe('watch')
  })

  test('新增量价形态按确认、分歧和风险分别着色', () => {
    expect(intradayEventSemantics('PRICE_VOLUME_PATTERN_VOLUME_RISE_CONFIRMED', 'info').kind).toBe('opportunity')
    expect(intradayEventSemantics('PRICE_VOLUME_PATTERN_VOLUME_RISE_STALLED', 'warning').kind).toBe('risk')
    expect(intradayEventSemantics('PRICE_VOLUME_PATTERN_VOLUME_RISE_PENDING', 'info').kind).toBe('watch')
  })

  test('统一板块与持仓新闻事件按验证后的业务语义着色', () => {
    expect(intradayEventSemantics('SECTOR_INCREMENT_CONFIRMED', 'info').kind).toBe('opportunity')
    expect(intradayEventSemantics('HOLDING_NEWS_NEGATIVE_IMPACT_CONFIRMED', 'warning').kind).toBe('risk')
    expect(intradayEventSemantics('HOLDING_NEWS_PENDING_VALIDATION', 'info').kind).toBe('watch')
    expect(isActionableIntradayEvent('HOLDING_NEWS_PENDING_VALIDATION', 'info')).toBe(true)
  })
})
