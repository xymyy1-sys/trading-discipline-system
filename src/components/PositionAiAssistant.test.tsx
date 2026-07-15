import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { PrivacyModeProvider } from '../privacy'
import PositionAiAssistant from './PositionAiAssistant'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

function jsonResponse(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response
}

describe('持仓 AI 隐私保护', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('开启隐私模式后从 DOM 移除可能复述持仓数值的回答正文', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({
      id: 1,
      code: '600584',
      question: '这只持仓现在该不该卖？',
      model: 'deepseek-chat',
      content: '真实持仓 5300 股，成本 4.05 元，当前盈亏 -371 元。',
      status: 'success',
      cached: false,
      context_as_of: '2026-07-15T10:30:00+08:00',
      missing_fields: [],
      updated_at: '2026-07-15T10:30:00+08:00',
    })))

    const { rerender } = render(
      <PrivacyModeProvider value={false}>
        <PositionAiAssistant code="600584" name="长电科技" />
      </PrivacyModeProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: /问AI/ }))
    fireEvent.click(screen.getByRole('button', { name: '生成回答' }))
    await waitFor(() => expect(screen.getByText(/真实持仓 5300 股/)).toBeInTheDocument())

    rerender(
      <PrivacyModeProvider value>
        <PositionAiAssistant code="600584" name="长电科技" />
      </PrivacyModeProvider>,
    )

    expect(screen.queryByText(/真实持仓 5300 股/)).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('AI 回答正文已隐藏')
    expect(screen.getByRole('button', { name: /按当前证据生成/ })).toBeDisabled()
  })
})
