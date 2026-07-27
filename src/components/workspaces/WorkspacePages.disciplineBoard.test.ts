import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'

describe('今日决策纪律主控条', () => {
  test('把冲高兑现、禁止恐慌卖和逆势加仓评估作为顶层闸门展示', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/workspaces/WorkspacePages.tsx'), 'utf8')
    const styles = readFileSync(resolve(process.cwd(), 'src/App.css'), 'utf8')

    expect(source).toContain('command-signal-board')
    expect(source).toContain('持仓纪律主控')
    expect(source).toContain('冲高兑现')
    expect(source).toContain('禁止恐慌卖')
    expect(source).toContain('逆势加仓评估')
    expect(source).toContain('HIGH_SELL_WINDOW')
    expect(source).toContain('PANIC_SELL_GUARD')
    expect(source).toContain('CONTRARIAN_ADD_EVALUATION')
    expect(styles).toContain('.command-signal-grid')
    expect(styles).toContain('signal-panic-guard')
  })
})
