import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'

function source(name: string) {
  return readFileSync(resolve(process.cwd(), `src/components/${name}.tsx`), 'utf8')
}

describe('市场页面只读加载与显式刷新', () => {
  test('初次加载不再用 force_refresh 查询参数触发采集', () => {
    for (const name of ['Dashboard', 'IntelDesk', 'FlowDesk', 'LimitUpLadder']) {
      expect(source(name)).not.toContain('force_refresh')
    }
  })

  test('所有刷新按钮调用对应 POST refresh 端点', () => {
    const dashboard = source('Dashboard')
    expect(dashboard).toContain('/api/market/theme-radar/refresh')
    expect(dashboard).toContain("method: 'POST'")

    const intel = source('IntelDesk')
    expect(intel).toContain("${force ? '/refresh' : ''}")
    expect(intel).toContain("force ? { method: 'POST' }")

    const flow = source('FlowDesk')
    for (const resource of [
      'board-flow-panel',
      'hot-themes',
      'dark-trade',
      'sector-temperature',
    ]) {
      expect(flow).toContain(`/api/market/${resource}`)
    }
    expect(flow).toContain("fetch(url, { method: 'POST' })")
    expect(flow.match(/\/refresh/g)).toHaveLength(4)

    const ladder = source('LimitUpLadder')
    expect(ladder).toContain('/api/market/limit-up-ladder')
    expect(ladder).toContain('/api/market/limit-up-atmosphere')
    expect(ladder).toContain("fetch(url, { method: 'POST' })")
    expect(ladder.match(/\/refresh/g)).toHaveLength(2)

    const detail = readFileSync(
      resolve(process.cwd(), 'src/components/FlowDesk/SectorDetailDrawer.tsx'),
      'utf8',
    )
    expect(detail).toContain('/refresh?${query.toString()}')
    expect(detail).toContain("{ method: 'POST' }")
    expect(detail).toContain('刷新成分股')
    expect(detail).toContain('setCachedJson')
  })
})
