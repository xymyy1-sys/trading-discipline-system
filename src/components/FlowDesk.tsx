import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCcw } from 'lucide-react'
import { API_BASE } from '../api'
import { cachedJson } from '../apiCache'
import type {
  SectorFlowItem,
  SectorFlow,
  MarketSeesaw,
} from '../types'

import FlowChartSection from './FlowDesk/FlowChartSection'
import SectorRanking from './FlowDesk/SectorRanking'
import SectorDetailDrawer from './FlowDesk/SectorDetailDrawer'

export default function FlowDesk() {
  const [flowType, setFlowType] = useState('行业资金流')
  const [period, setPeriod] = useState('今日')
  const [category, setCategory] = useState('全部')
  const [viewMode, setViewMode] = useState<'market' | 'holdings'>('holdings')
  const [flow, setFlow] = useState<SectorFlow | null>(null)
  const [seesaw, setSeesaw] = useState<MarketSeesaw | null>(null)
  const [loading, setLoading] = useState(true)
  const [apiNote, setApiNote] = useState('同步中')
  const [selected, setSelected] = useState<string | null>(null)
  const [detailTarget, setDetailTarget] = useState<SectorFlowItem | null>(null)
  const [fetchedAt, setFetchedAt] = useState<string | null>(null)

  const loadFlow = useCallback((force = false) => {
    setLoading(true)
    const query = `flow_type=${encodeURIComponent(flowType)}&period=${encodeURIComponent(period)}${force ? '&force_refresh=true' : ''}`
    cachedJson<SectorFlow>(
      `sector-flow:${flowType}:${period}`,
      `${API_BASE}/api/market/sector-flow?${query}`,
      force,
    )
      .then(({ data, fetchedAt }) => {
        setFlow(data)
        setFetchedAt(fetchedAt)
        const src = data.source
        let note = '东方财富'
        if (src.includes('diagnostic')) {
          note = '诊断数据（非交易日）'
        } else if (src.includes('sina')) {
          note = '新浪资金流'
        } else if (src.includes('snapshots')) {
          const n = src.split('snapshots:')[1]?.split('|')[0] || '?'
          note = `东方财富 · ${n} 个快照`
        } else if (src.includes('estimates')) {
          note = '东方财富 · 快照累积中'
        } else if (src.includes('akshare')) {
          note = 'AkShare'
        }
        if (src.includes('最近交易日')) {
          note += ' · 最近交易日'
        }
        setApiNote(note)
      })
      .catch(() => setApiNote('后端未启动'))
      .finally(() => setLoading(false))
  }, [flowType, period])

  const loadSeesaw = useCallback((force = false) => {
    setLoading(true)
    cachedJson<MarketSeesaw>(
      'market-seesaw-monitor',
      `${API_BASE}/api/market/seesaw-monitor${force ? '?force_refresh=true' : ''}`,
      force,
    )
      .then(({ data, fetchedAt }) => {
        setSeesaw(data)
        setFetchedAt(fetchedAt)
        setApiNote(`持仓主线证据 · ${data.source}`)
      })
      .catch(() => setApiNote('后端未启动'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (viewMode === 'holdings') {
      loadSeesaw()
    } else {
      loadFlow()
    }
    const timer = setInterval(() => {
      if (viewMode === 'holdings') loadSeesaw()
      else loadFlow()
    }, 300000)
    return () => clearInterval(timer)
  }, [loadFlow, loadSeesaw, viewMode])

  const evidenceFlow = useMemo(() => holdingsToFlow(seesaw), [seesaw])
  const activeFlow = viewMode === 'holdings' ? evidenceFlow : flow
  const filteredFlow = useMemo(() => filterFlowByCategory(activeFlow, category), [activeFlow, category])
  const strongest = filteredFlow?.inflow[0]
  const weakest = filteredFlow?.outflow[0]

  return (
    <>
      <section className="flow-board">
        <div className="flow-header">
          <div>
            <h2>资金流证据</h2>
            <p>用于验证题材强度，不单独作为买点。持仓视角优先展示个股板块画像归纳后的主线资金曲线。</p>
          </div>
          <div className="segmented">
            <button className={viewMode === 'holdings' ? 'selected' : ''} type="button" onClick={() => setViewMode('holdings')}>
              持仓主线证据
            </button>
            <button className={viewMode === 'market' ? 'selected' : ''} type="button" onClick={() => setViewMode('market')}>
              行业总览
            </button>
          </div>
          {viewMode === 'market' && <div className="segmented">
            {['行业资金流', '概念资金流', '地域资金流'].map(t => (
              <button className={flowType === t ? 'selected' : ''} key={t} onClick={() => setFlowType(t)}>
                {t.replace('资金流', '')}
              </button>
            ))}
          </div>}
          {viewMode === 'market' && <div className="segmented compact">
            {['今日', '5日', '10日'].map(p => (
              <button className={period === p ? 'selected' : ''} key={p} onClick={() => setPeriod(p)}>{p}</button>
            ))}
          </div>}
          <div className="category-filter">
            {['全部', '半导体链', 'AI算力链', '有色金属链', '商业航天', '机器人', '汽车链', '消费电子', '医药', '金融地产', '其他'].map(item => (
              <button className={category === item ? 'selected' : ''} key={item} onClick={() => setCategory(item)} type="button">
                {item}
              </button>
            ))}
          </div>
          <button className="refresh-btn inline" type="button" onClick={() => viewMode === 'holdings' ? loadSeesaw(true) : loadFlow(true)} disabled={loading}>
            <RefreshCcw size={14} />
            {loading ? '同步中' : '刷新'}
          </button>
          <span className="source-tag">{loading ? '同步中' : apiNote}</span>
          <span className="source-tag">{fetchedAt ? `缓存 ${new Date(fetchedAt).toLocaleTimeString('zh-CN')}` : '5 分钟缓存'}</span>
        </div>

        <div className="flow-grid-new">
          <div className="flow-chart-area">
            <FlowChartSection
              flow={filteredFlow}
              selected={selected}
              onSelect={setSelected}
              onOpenDetail={setDetailTarget}
            />
          </div>
          <div className="flow-side-panels">
            <SectorRanking
              title="资金流入榜"
              items={filteredFlow?.inflow ?? []}
              direction="in"
              selected={selected}
              onSelect={setSelected}
              onOpenDetail={setDetailTarget}
            />
            <SectorRanking
              title="资金流出榜"
              items={filteredFlow?.outflow ?? []}
              direction="out"
              selected={selected}
              onSelect={setSelected}
              onOpenDetail={setDetailTarget}
            />
          </div>
        </div>
      </section>

      <section className="decision-grid">
        <Panel title="主线判断">
          {strongest && (
            <>
              <KV label="最强板块" value={strongest.name} />
              <KV label="归属主线" value={strongest.mainline ?? strongest.theme_line ?? strongest.name} />
              <KV label="细分方向" value={strongest.subline ?? strongest.raw_name ?? strongest.name} />
              <KV label="净流入" value={`${strongest.net_inflow.toFixed(2)} 亿`} tone="up" />
              <KV label="板块强度" value={`${strongest.strength}/100`} />
              <KV label="涨跌幅" value={`${strongest.change_pct.toFixed(2)}%`} tone={strongest.change_pct >= 0 ? 'up' : 'down'} />
            </>
          )}
          <p className="plain-text">若该板块连续 2-3 天反复走强，且龙一未破位，可提升到进攻档位。</p>
        </Panel>
        <Panel title="风险提示">
          {weakest && (
            <>
              <KV label="流出集中" value={weakest.name} tone="down" />
              <KV label="净流出" value={`${weakest.net_inflow.toFixed(2)} 亿`} tone="down" />
            </>
          )}
          <p className="plain-text">买入检查器会拦截后排跟风、无主线股、无量价确认、止损不可执行和超仓位计划。</p>
        </Panel>
        <Panel title="集中进攻防火墙">
          <div className="rule-list">
            <span>超过 60% 仓位：必须写退出卡</span>
            <span>做T无卖出腿：禁止变隔夜进攻</span>
            <span>亏损后扳本：当天停止新增风险</span>
          </div>
        </Panel>
      </section>
      {detailTarget && (
        <SectorDetailDrawer
          flowType={flowType}
          period={period}
          item={detailTarget}
          onClose={() => setDetailTarget(null)}
        />
      )}
    </>
  )
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return <article className="panel"><h3>{title}</h3>{children}</article>
}

function KV({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return (
    <div className="key-value">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  )
}

function filterFlowByCategory(flow: SectorFlow | null, category: string): SectorFlow | null {
  if (!flow || category === '全部') return flow
  const pick = (item: SectorFlowItem) => (item.category || '其他') === category
  return {
    ...flow,
    inflow: flow.inflow.filter(pick),
    outflow: flow.outflow.filter(pick),
  }
}

function holdingsToFlow(seesaw: MarketSeesaw | null): SectorFlow | null {
  if (!seesaw) return null
  const items: SectorFlowItem[] = seesaw.holding_alerts.map(item => {
    const current = item.theme_flow_current || 0
    const mainName = item.holding_theme || item.primary_industry_sector || '待确认主线'
    const display = `${item.name} · ${mainName}`
    const rawConcepts = (item.stock_concepts || []).slice(0, 8)

    const realTl = item.theme_flow_timeline || []
    const timeline = realTl.length >= 2
      ? realTl.map(p => ({ time: p.time, value: p.value }))
      : [
          { time: '前值', value: Number((current - (item.sector_acceleration || 0)).toFixed(2)) },
          { time: item.theme_flow_peak ? '高点' : '盘中', value: Number((item.theme_flow_peak || current).toFixed(2)) },
          { time: '当前', value: Number(current.toFixed(2)) },
        ]

    return {
      name: display,
      display_name: display,
      raw_name: item.primary_industry_sector || item.matched_flow_sector || item.holding_theme,
      board_code: item.code,
      provider: item.theme_source || 'holding-profile',
      theme_line: item.holding_theme,
      mainline: item.holding_theme,
      subline: [item.stock_industry, item.flow_basis].filter(Boolean).join(' / '),
      category: mainCategory(item.holding_theme, item.theme_tags),
      change_pct: item.change_pct || 0,
      net_inflow: current,
      main_inflow: item.sector_main_inflow || current,
      strength: Math.max(5, Math.min(100, Math.round(50 + current / 2 - item.theme_flow_pullback_pct / 2))),
      leaders: [
        item.stock_industry ? `行业:${item.stock_industry}` : '',
        rawConcepts.length ? `概念:${rawConcepts.join('、')}` : '',
        item.flow_basis ? `曲线:${item.flow_basis}` : '',
      ].filter(Boolean),
      timeline,
    }
  })
  return {
    source: seesaw.source,
    updated_at: seesaw.updated_at,
    inflow: items.filter(item => item.net_inflow >= 0).sort((a, b) => b.net_inflow - a.net_inflow),
    outflow: items.filter(item => item.net_inflow < 0).sort((a, b) => a.net_inflow - b.net_inflow),
  }
}

function mainCategory(theme: string, tags: string[]) {
  const text = `${theme} ${(tags || []).join(' ')}`
  if (/半导体|芯片|集成电路/.test(text)) return '半导体链'
  if (/AI|算力|服务器|云计算|液冷|东数西算/.test(text)) return 'AI算力链'
  if (/航天|卫星|军工|北斗|低空/.test(text)) return '商业航天'
  if (/机器人/.test(text)) return '机器人'
  if (/医药|创新药|医疗/.test(text)) return '医药'
  if (/新能源|光伏|锂电|储能/.test(text)) return '新能源'
  if (/电子|消费电子|PCB|OLED/.test(text)) return '消费电子'
  return '其他'
}
