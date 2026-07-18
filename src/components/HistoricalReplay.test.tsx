import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import HistoricalReplay from './HistoricalReplay'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

describe('历史事件回放真实性语义', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('有持久化帧时展示真实时间线，不再宣称通过验收检查点', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok({
      code: '600584',
      name: '长电科技',
      trade_date: '2026-07-10',
      complete: true,
      summary: ['共重建 1 帧。'],
      checkpoints: [],
      frames: [{
        timestamp: '2026-07-10T10:05:00+08:00',
        frame_type: 'volume_price',
        state: 'MATCHED',
        action: '',
        price: 101.11,
        vwap: 100.58,
        data_quality: 'realtime',
        evidence: ['价格位于分时均价线上方。'],
      }],
    })))

    render(<HistoricalReplay />)
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))

    expect(await screen.findByText('已重建真实持久化时间线')).toBeInTheDocument()
    expect(screen.queryByText(/验收检查点通过|检查点未完全匹配/)).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '显式验收规则' })).not.toBeInTheDocument()
  })

  test('没有帧时明确暂无证据；服务端显式返回规则时才展示规则区', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok({
      code: '600584',
      name: '长电科技',
      trade_date: '2026-07-10',
      complete: false,
      summary: [],
      checkpoints: [{ expected_time: '10:05', expected_signal: 'VWAP_BROKEN', matched: false }],
      frames: [],
    })))

    render(<HistoricalReplay />)
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))

    expect(await screen.findByText('暂无可回放证据')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '显式验收规则' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/10:05/)).toBeInTheDocument())
  })
})

function ok(payload: unknown) {
  return { ok: true, json: async () => payload } as Response
}
