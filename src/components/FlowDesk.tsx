import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { ChevronRight, RefreshCcw, TrendingDown, TrendingUp, X } from 'lucide-react'
import { API_BASE } from '../api'
import { cachedJson } from '../apiCache'

type FlowPoint = { time: string; value: number }
type SectorFlowItem = {
  name: string
  display_name: string | null
  raw_name: string | null
  board_code: string | null
  provider: string | null
  theme_line: string | null
  mainline: string | null
  subline: string | null
  category: string | null
  change_pct: number
  net_inflow: number
  main_inflow: number
  strength: number
  leaders: string[]
  timeline: FlowPoint[]
}
type SectorFlow = {
  source: string
  updated_at: string
  inflow: SectorFlowItem[]
  outflow: SectorFlowItem[]
}
type HoldingSeesawItem = {
  code: string
  name: string
  holding_theme: string
  theme_tags: string[]
  stock_industry: string
  stock_concepts: string[]
  theme_source: string
  flow_basis: string
  primary_industry_sector: string
  concept_flow_sectors: string[]
  matched_flow_sector: string
  theme_flow_sectors: string[]
  theme_flow_summary: string
  theme_flow_current: number
  theme_flow_peak: number
  theme_flow_pullback: number
  theme_flow_pullback_pct: number
  sector_main_inflow: number
  sector_acceleration: number
  change_pct: number
  risk_level: string
  evidence: string[]
}
type MarketSeesaw = {
  source: string
  updated_at: string
  holding_alerts: HoldingSeesawItem[]
}
type SectorConstituent = {
  code: string
  name: string
  price: number
  change_pct: number
  amount: number
  turnover: number
  main_inflow: number
  net_inflow: number
  float_cap: number
  is_limit_up: boolean
  consecutive_limit_days: number
  concepts: string[]
}
type SectorDetail = {
  source: string
  updated_at: string
  name: string
  display_name: string | null
  raw_name: string | null
  board_code: string | null
  provider: string | null
  theme_line: string | null
  mainline: string | null
  subline: string | null
  category: string | null
  change_pct: number
  net_inflow: number
  main_inflow: number
  strength: number
  leaders: string[]
  constituents: SectorConstituent[]
  limit_up_stocks: SectorConstituent[]
  notes: string[]
}

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

/* ── Flow chart ─────────────────────────────────────── */

function FlowChartSection({
  flow,
  selected,
  onSelect,
  onOpenDetail,
}: {
  flow: SectorFlow | null
  selected: string | null
  onSelect: (name: string | null) => void
  onOpenDetail: (item: SectorFlowItem) => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  const items = useMemo(() => {
    if (!flow) return []
    const top = flow.inflow.slice(0, 14)
    const bot = flow.outflow.slice(0, 18)
    return [...top, ...bot]
  }, [flow])

  const xData = useMemo(() => {
    if (!items.length) return ['09:30', '15:00']
    const all = new Set<string>()
    items.forEach(it => it.timeline.forEach(p => all.add(p.time)))
    return Array.from(all).sort()
  }, [items])

  useEffect(() => {
    if (!ref.current || !items.length) return
    const chart = echarts.init(ref.current)
    const inflowColors = ['#d92d20', '#e23b2e', '#f05a28', '#ff6b35', '#d62828', '#f77f00', '#c1121f', '#ef476f', '#ff8a3d', '#b91c1c', '#fb5607', '#d9480f', '#f94144', '#e76f51']
    const outflowColors = ['#00875a', '#009a6c', '#00b894', '#2f9e44', '#43aa8b', '#2a9d8f', '#3a7d44', '#55a630', '#1b9e77', '#66a182', '#6abf69', '#588157', '#4d908e', '#52b788', '#40916c', '#6c757d', '#adb5bd', '#8d99ae']

    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 64, right: 24, top: 20, bottom: 48 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1a241e',
        borderColor: '#3a5244',
        textStyle: { color: '#e8ede7', fontSize: 13 },
        valueFormatter: (v: unknown) => `${Number(v).toFixed(2)} 亿`,
      },
      legend: {
        type: 'scroll',
        bottom: 6,
        left: 'center',
        textStyle: { color: '#5d685d', fontSize: 11 },
        pageTextStyle: { color: '#5d685d' },
        itemWidth: 12,
        itemHeight: 8,
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: xData,
        axisLine: { lineStyle: { color: '#c5c9c0' } },
        axisLabel: { color: '#697069', fontSize: 11, rotate: xData.length > 20 ? 45 : 0 },
      },
      yAxis: {
        type: 'value',
        name: '亿元',
        nameTextStyle: { color: '#8a9186', fontSize: 11 },
        axisLabel: { color: '#697069', fontSize: 11, formatter: '{value}' },
        splitLine: { lineStyle: { color: '#e8ebe4', type: 'dashed' } },
      },
      series: items.map((item, i) => {
        const isInflow = item.net_inflow >= 0
        const colorIdx = isInflow ? i % inflowColors.length : (i - flow!.inflow.slice(0, 14).length) % outflowColors.length
        const color = isInflow ? inflowColors[colorIdx] : outflowColors[colorIdx]
        const label = displayName(item)
        const isSelected = selected === label
        return {
          name: label,
          type: 'line',
          smooth: 0.4,
          symbol: 'none',
          emphasis: { focus: 'series' },
          lineStyle: {
            width: isSelected ? 4 : Math.abs(item.net_inflow) > 8 ? 2.5 : 1.5,
            color,
            opacity: selected && !isSelected ? 0.25 : 0.9,
          },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: isInflow ? 'rgba(217,45,32,0.10)' : 'rgba(0,135,90,0.08)' },
              { offset: 1, color: 'rgba(0,0,0,0)' },
            ]),
            opacity: isSelected ? 0.2 : 0.05,
          },
          data: xData.map(t => {
            const pt = item.timeline.find(p => p.time === t)
            return pt ? pt.value : null
          }),
          connectNulls: true,
        }
      }),
      color: [...inflowColors, ...outflowColors],
    })

    chart.on('click', (params: any) => {
      if (params.seriesName) {
        onSelect(params.seriesName === selected ? null : params.seriesName)
        const item = items.find(it => displayName(it) === params.seriesName)
        if (item) onOpenDetail(item)
      }
    })

    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [items, xData, selected, onSelect, onOpenDetail, flow])

  return <div className="chart-surface-large" ref={ref} aria-label="全天资金流向图" />
}

/* ── Sector ranking ─────────────────────────────────── */

function SectorRanking({
  title,
  items,
  direction,
  selected,
  onSelect,
  onOpenDetail,
}: {
  title: string
  items: SectorFlowItem[]
  direction: 'in' | 'out'
  selected: string | null
  onSelect: (name: string | null) => void
  onOpenDetail: (item: SectorFlowItem) => void
}) {
  const color = direction === 'in' ? '#d92d20' : '#00875a'
  return (
    <div className="ranking-full">
      <h3>
        {direction === 'in' ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
        {title}
      </h3>
      <div className="ranking-full-list">
        {items.slice(0, 18).map((item, i) => (
          <button
            className={`rank-row-full ${selected === displayName(item) ? 'picked' : ''}`}
            key={item.name}
            type="button"
            onClick={() => {
              onSelect(displayName(item))
              onOpenDetail(item)
            }}
          >
            <span className="rank-num">{String(i + 1).padStart(2, '0')}</span>
            <span className="rank-name">{displayName(item)}</span>
            <span className="rank-theme-line">{categoryLine(item)}</span>
            <span className="rank-stats">
              <span className={direction === 'in' ? 'num-up' : 'num-down'}>
                {item.net_inflow >= 0 ? '+' : ''}{item.net_inflow.toFixed(2)}亿
              </span>
              <span className="rank-pct" style={{ color: item.change_pct >= 0 ? 'var(--up)' : 'var(--down)' }}>
                {item.change_pct >= 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
              </span>
            </span>
            <span className="rank-bar">
              <i style={{ width: `${Math.max(4, item.strength)}%`, background: color }} />
            </span>
            {item.leaders.filter(l => l !== '待识别').length > 0 && (
              <span className="rank-leaders">{item.leaders.filter(l => l !== '待识别').join(' · ')}</span>
            )}
            <ChevronRight size={12} className="rank-arrow" />
          </button>
        ))}
      </div>
    </div>
  )
}

function SectorDetailDrawer({
  flowType,
  period,
  item,
  onClose,
}: {
  flowType: string
  period: string
  item: SectorFlowItem
  onClose: () => void
}) {
  const [detail, setDetail] = useState<SectorDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState<'change_pct' | 'amount' | 'consecutive_limit_days'>('change_pct')

  useEffect(() => {
    setLoading(true)
    const query = new URLSearchParams({
      name: item.name,
      flow_type: flowType,
      period,
    })
    if (item.board_code) query.set('board_code', item.board_code)
    if (item.provider) query.set('provider', item.provider)
    cachedJson<SectorDetail>(
      `sector-detail:${flowType}:${period}:${item.name}:${item.board_code ?? ''}`,
      `${API_BASE}/api/market/sector-detail?${query.toString()}`,
    )
      .then(({ data }) => setDetail(data))
      .finally(() => setLoading(false))
  }, [flowType, item, period])

  const rows = useMemo(() => {
    const source = detail?.constituents ?? []
    return [...source].sort((a, b) => b[sortKey] - a[sortKey])
  }, [detail, sortKey])

  return (
    <div className="drawer-backdrop" role="presentation">
      <aside className="sector-drawer" aria-label={`${item.name}成分股`}>
        <div className="drawer-head">
          <div>
            <span className="eyebrow">Sector Drilldown</span>
            <h2>{detail?.name ?? item.name}</h2>
            <p>{[detail?.category ?? item.category, detail?.subline ?? item.subline].filter(Boolean).join(' / ')}</p>
          </div>
          <button className="icon-btn" type="button" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </div>

        <div className="drawer-stats">
          <MiniStat label="净流入" value={`${(detail?.net_inflow ?? item.net_inflow).toFixed(2)}亿`} tone={(detail?.net_inflow ?? item.net_inflow) >= 0 ? 'up' : 'down'} />
          <MiniStat label="涨跌幅" value={`${(detail?.change_pct ?? item.change_pct).toFixed(2)}%`} tone={(detail?.change_pct ?? item.change_pct) >= 0 ? 'up' : 'down'} />
          <MiniStat label="涨停股" value={`${detail?.limit_up_stocks.length ?? 0}只`} />
          <MiniStat label="强度" value={`${detail?.strength ?? item.strength}/100`} />
        </div>

        <div className="taxonomy-strip">
          <span>原始板块：{detail?.raw_name ?? item.raw_name ?? item.name}</span>
          <span>归属主线：{detail?.mainline ?? item.mainline ?? item.theme_line ?? '--'}</span>
          <span>细分方向：{detail?.subline ?? item.subline ?? '--'}</span>
          <span>分类：{detail?.category ?? item.category ?? '--'}</span>
        </div>

        <div className="drawer-toolbar">
          <div className="segmented compact">
            <button className={sortKey === 'change_pct' ? 'selected' : ''} type="button" onClick={() => setSortKey('change_pct')}>涨跌幅</button>
            <button className={sortKey === 'amount' ? 'selected' : ''} type="button" onClick={() => setSortKey('amount')}>成交额</button>
            <button className={sortKey === 'consecutive_limit_days' ? 'selected' : ''} type="button" onClick={() => setSortKey('consecutive_limit_days')}>连板</button>
          </div>
          <span>{loading ? '加载中' : `${rows.length} 只成分股`}</span>
        </div>

        {detail?.limit_up_stocks.length ? (
          <div className="limit-strip">
            {detail.limit_up_stocks.slice(0, 8).map(stock => (
              <span key={stock.code || stock.name}>{stock.name}{stock.consecutive_limit_days > 1 ? ` · ${stock.consecutive_limit_days}板` : ''}</span>
            ))}
          </div>
        ) : null}

        <div className="drawer-table-wrap">
          <table className="pos-table detail-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th className="num">涨跌</th>
                <th className="num">现价</th>
                <th className="num">成交额</th>
                <th className="num">换手</th>
                <th>概念</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(stock => (
                <tr key={`${stock.code}-${stock.name}`}>
                  <td className="mono">{stock.code}</td>
                  <td>
                    <strong>{stock.name}</strong>
                    {stock.is_limit_up && <span className="limit-badge">{stock.consecutive_limit_days > 1 ? `${stock.consecutive_limit_days}板` : '涨停'}</span>}
                  </td>
                  <td className={`num ${stock.change_pct >= 0 ? 'num-up' : 'num-down'}`}>{stock.change_pct.toFixed(2)}%</td>
                  <td className="num">{stock.price.toFixed(2)}</td>
                  <td className="num">{stock.amount.toFixed(2)}亿</td>
                  <td className="num">{stock.turnover.toFixed(2)}%</td>
                  <td className="tags-cell">{stock.concepts.map(tag => <span className="weak-tag" key={tag}>{tag}</span>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && rows.length === 0 && <div className="empty-msg">暂无成分股数据</div>}
        </div>

        {detail?.notes.length ? <p className="plain-text">{detail.notes.join('；')}</p> : null}
      </aside>
    </div>
  )
}

function MiniStat({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  )
}

function displayName(item: SectorFlowItem) {
  return item.display_name || item.raw_name || item.name
}

function categoryLine(item: SectorFlowItem) {
  const category = item.category || '其他'
  const subline = item.subline || item.mainline || item.theme_line || item.raw_name || item.name
  return category === subline ? category : `${category} / ${subline}`
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
    const peak = item.theme_flow_peak || current
    const start = current - (item.sector_acceleration || 0)
    const name = `${item.name} · ${item.holding_theme || item.primary_industry_sector || '待确认主线'}`
    const rawConcepts = (item.stock_concepts || []).slice(0, 8)
    return {
      name,
      display_name: name,
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
      timeline: [
        { time: '盘初', value: Number(start.toFixed(2)) },
        { time: '高点', value: Number(peak.toFixed(2)) },
        { time: '当前', value: Number(current.toFixed(2)) },
      ],
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

/* ── Shared ──────────────────────────────────────────── */

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
