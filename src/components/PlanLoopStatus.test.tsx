import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test } from 'vitest'
import type { AuctionPlan } from '../types'
import PlanLoopStatus from './PlanLoopStatus'

const loopPlan = {
  selected_branch: 'low_open_selloff',
  selected_branch_label: '低开下杀剧本',
  branch_status: 'active',
  branch_reason: '竞价低于合理区间，先观察恐慌盘承接。',
  current_advice: '撤销立即卖出，等待站稳分时均价再决定。',
  advice_level: 'warning',
  advice_state: 'active',
  advice_revision: 3,
  previous_advice: '反抽无力时减仓。',
  advice_change: 'downgraded',
  advice_change_reason: '低点抬高且卖压衰减，风险建议降级。',
  auto_refreshed_at: '2026-07-23 09:36:00',
  stage_checks: [
    { stage: '竞价确认', status: '失败', trigger: '低于区间', decision: '弱于预期', required_action: '禁止抢跑', evidence: [] },
    { stage: '五分钟确认', status: '通过', trigger: '站回均价', decision: '修复确认', required_action: '撤销低点卖出', evidence: [] },
  ],
  advice_history: [
    { revision: 2, advice: '反抽无力时减仓。', level: 'warning', state: 'withdrawn', stage: '开盘确认', branch: 'low_open_selloff', reason: '低开弱势', created_at: '2026-07-23 09:31:00', withdrawn_at: '2026-07-23 09:36:00', withdraw_reason: 'V形修复' },
    { revision: 3, advice: '撤销立即卖出，等待站稳分时均价再决定。', level: 'observe', state: 'active', stage: '五分钟确认', branch: 'low_open_selloff', reason: 'V形修复', created_at: '2026-07-23 09:36:00' },
  ],
} as unknown as AuctionPlan

describe('自动计划执行闭环', () => {
  afterEach(cleanup)

  test('完整视图呈现自动分支、建议修正、阶段链和可追溯版本', () => {
    render(<PlanLoopStatus plan={loopPlan} />)

    expect(screen.getByText('自动计划执行闭环')).toBeInTheDocument()
    expect(screen.getByText('低开下杀剧本')).toBeInTheDocument()
    expect(screen.getAllByText('撤销立即卖出，等待站稳分时均价再决定。')).toHaveLength(2)
    expect(screen.getByText('建议已降级')).toBeInTheDocument()
    expect(screen.getByLabelText('自动闭环阶段链')).toHaveTextContent('竞价确认')
    expect(screen.getByLabelText('自动闭环阶段链')).toHaveTextContent('五分钟确认')
    expect(screen.getByText('查看建议版本历史（2）')).toBeInTheDocument()
  })

  test('驾驶舱紧凑视图只保留当前分支和当前建议', () => {
    render(<PlanLoopStatus plan={loopPlan} compact />)

    expect(screen.getByText('计划执行闭环')).toBeInTheDocument()
    expect(screen.getByText(/低开下杀剧本/)).toBeInTheDocument()
    expect(screen.queryByText('自动闭环阶段链')).not.toBeInTheDocument()
    expect(screen.queryByText('查看建议版本历史（2）')).not.toBeInTheDocument()
  })

  test('当前计划缺失时明确显示数据缺口，不沿用旧建议', () => {
    render(<PlanLoopStatus plan={null} compact />)

    expect(screen.getByText('尚无当前计划')).toBeInTheDocument()
    expect(screen.getByText(/不使用旧计划替代/)).toBeInTheDocument()
  })
})
