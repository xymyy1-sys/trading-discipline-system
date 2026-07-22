import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'

describe('今日决策信息架构', () => {
  test('仅保留当前操作任务作为建议队列，并移除全持仓重复摘要', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/workspaces/WorkspacePages.tsx'), 'utf8')
    expect(source.match(/当前操作任务/g)).toHaveLength(1)
    expect(source).not.toContain('待确认操作建议')
    expect(source).not.toContain('active-recommendations')
    expect(source).not.toContain('全持仓证据摘要')
    expect(source).toContain('仅标记已读')
    expect(source).toContain('去持仓执行反馈')
    expect(source).toContain('/api/intraday-collector/run')
    expect(source).toContain('/api/next-day-plans')
    expect(source).toContain('<PlanLoopStatus plan={selectedPlan?.auction_plan} planDate={selectedPlan?.plan_date} compact />')
    expect(source).not.toContain('?force_refresh=true')
  })

  test('低风险不添加视觉风险类，高风险和中风险保留分级', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/workspaces/WorkspacePages.tsx'), 'utf8')
    expect(source).toContain("return 'risk-high'")
    expect(source).toContain("return 'risk-medium'")
    expect(source).not.toContain("return 'risk-low'")
  })

  test('外围行情和机构资金证据分别评级，并标注持久快照来源', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/workspaces/WorkspacePages.tsx'), 'utf8')
    expect(source).toContain('基础行情质量')
    expect(source).toContain('机构资金证据质量')
    expect(source).toContain('globalCues?.quote_quality')
    expect(source).toContain('globalCues?.institutional_flow_quality')
    expect(source).toContain('数据库持久快照')
  })

  test('退役旧 Service Worker，入口和静态资源采用可验证缓存策略', () => {
    const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')
    const worker = readFileSync(resolve(process.cwd(), 'public/sw.js'), 'utf8')
    const main = readFileSync(resolve(process.cwd(), 'src/main.tsx'), 'utf8')
    const nginx = readFileSync(resolve(process.cwd(), 'nginx.conf'), 'utf8')

    expect(html).not.toContain("serviceWorker.register")
    expect(worker).toContain('registration.unregister()')
    expect(worker).not.toContain('tds-cache-v1')
    expect(worker).not.toContain("addEventListener('fetch'")
    expect(main).toContain('getRegistrations()')
    expect(main).toContain("window.caches.delete")
    expect(nginx).toContain('location = /sw.js')
    expect(nginx).toContain('location /assets/')
    expect(nginx).toContain('try_files $uri =404;')
  })
})
