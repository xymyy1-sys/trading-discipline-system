import { describe, expect, test } from 'vitest'
import { buildFlowKineticsView, flowTurningLabel, holdingFlowKineticsFields } from './flowKinetics'

describe('订单流方向流速与拐点显示', () => {
  test('单一时点不伪造流速或拐点', () => {
    const view = buildFlowKineticsView({
      flow_direction: 'NET_INFLOW',
      flow_as_of: '2026-07-16 09:31:00',
      flow_kinetics_reliable: false,
    })

    expect(view.reliable).toBe(false)
    expect(view.signal).toBe('等待至少两个真实时点')
    expect(view.speed).toBeNull()
    expect(view.asOf).toBe('09:31:00')
  })

  test('可靠的流入转流出显示为警示并保留量纲', () => {
    const view = buildFlowKineticsView({
      flow_direction: 'NET_OUTFLOW',
      flow_speed: -1.23456,
      flow_acceleration: -0.07891,
      flow_turning: 'TURN_TO_OUTFLOW',
      flow_as_of: '2026-07-16T09:36:00',
      flow_window_minutes: 5,
      flow_kinetics_reliable: true,
    })

    expect(view.signal).toBe('订单流方向由净流入拐为净流出')
    expect(view.tone).toBe('warning')
    expect(view.speed).toBe('-1.235 亿/分钟')
    expect(view.acceleration).toBe('-0.0789 亿/分钟²')
    expect(view.window).toBe('5 个交易分钟')
  })

  test('持仓板块字段可安全映射且中文化改善信号', () => {
    const view = buildFlowKineticsView(holdingFlowKineticsFields({
      sector_flow_speed: 0.8,
      sector_flow_acceleration: 0.12,
      sector_flow_turning: 'TURN_TO_INFLOW',
      sector_flow_as_of: '2026-07-16 13:05:00',
    }))

    expect(view.reliable).toBe(true)
    expect(view.tone).toBe('positive')
    expect(flowTurningLabel('OUTFLOW_NARROWING')).toBe('订单流方向净流出快速收窄')
  })

  test('未知拐点不泄露英文枚举，且显式风险等级决定颜色', () => {
    expect(flowTurningLabel('NEW_PROVIDER_CODE')).toBe('订单流方向拐点待识别')
    expect(buildFlowKineticsView({
      flow_speed: -0.2,
      flow_as_of: '2026-07-16 10:15:00',
      flow_signal_level: 'WARNING',
      flow_signal: '资金边际转弱',
    }).tone).toBe('warning')
  })
})
