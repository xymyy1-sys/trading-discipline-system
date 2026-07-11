import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Flame, MoonStar, RefreshCcw, TrendingUp } from 'lucide-react'
import { API_BASE } from '../api'
import { cachedJson } from '../apiCache'
import type {
  BoardFlowPanel,
  DarkTradeItem,
  DarkTradeOut,
  HotThemeItem,
  HotThemesOut,
  SectorFlowItem,
} from '../types'

import FlowChartSection from './FlowDesk/FlowChartSection'
import SectorRanking from './FlowDesk/SectorRanking'
import SectorDetailDrawer from './FlowDesk/SectorDetailDrawer'

type MainTab = 'funds' | 'hot' | 'dark'
type BoardType = '行业' | '概念' | '风格' | '地域' | '港股'
type DarkScope = '个股' | '行业' | '概念'

const BOARD_TYPES: BoardType[] = ['行业', '概念', '风格', '地域', '港股']
const PERIODS = ['今日', '5日', '10日']
const DARK_SCOPES: DarkScope[] = ['个股', '行业', '概念']

export default function FlowDesk() {
  const [tab, setTab] = useState<MainTab>('funds')
  const [boardType, setBoardType] = useState<BoardType>('行业')
  const [period, setPeriod] = useState('今日')
  const [darkScope, setDarkScope] = useState<DarkScope>('个股')
  const [boardFlow, setBoardFlow] = useState<BoardFlowPanel | null>(null)
  const [hotThemes, setHotThemes] = useState<HotThemesOut | null>(null)
  const [darkTrade, setDarkTrade] = useState<DarkTradeOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [apiNote, setApiNote] = useState('同步中')
  const [selected, setSelected] = useState<string | null>(null)
  const [detailTarget, setDetailTarget] = useState<SectorFlowItem | null>(null)
  const [fetchedAt, setFetchedAt] = useState<string | null>(null)

  const loadFunds = useCallback((force = false) => {
    setLoading(true)
    const query = new URLSearchParams({ board_type: boardType, period })
    if (force) query.set('force_refresh', 'true')
    cachedJson<BoardFlowPanel>(
      `board-flow-panel:${boardType}:${period}`,
      `${API_BASE}/api/market/board-flow-panel?${query.toString()}`,
      force,
    )
      .then(({ data, fetchedAt }) => {
        setBoardFlow(data)
        setFetchedAt(fetchedAt)
        setApiNote(sourceLabel(data.source, data.notes))
      })
      .catch(() => setApiNote('后端未启动'))
      .finally(() => setLoading(false))
  }, [boardType, period])

  const loadHot = useCallback((force = false) => {
    setLoading(true)
    cachedJson<HotThemesOut>(
      'hot-themes',
      `${API_BASE}/api/market/hot-themes${force ? '?force_refresh=true' : ''}`,
      force,
    )
      .then(({ data, fetchedAt }) => {
        setHotThemes(data)
        setFetchedAt(fetchedAt)
        setApiNote(data.source.includes('fallback') ? '东方财富热点 · 资金补充受限' : '东方财富热点题材')
      })
      .catch(() => setApiNote('后端未启动'))
      .finally(() => setLoading(false))
  }, [])

  const loadDark = useCallback((force = false) => {
    setLoading(true)
    const query = new URLSearchParams({ scope: darkScope })
    if (force) query.set('force_refresh', 'true')
    cachedJson<DarkTradeOut>(
      `dark-trade:${darkScope}`,
      `${API_BASE}/api/market/dark-trade?${query.toString()}`,
      force,
    )
      .then(({ data, fetchedAt }) => {
        setDarkTrade(data)
        setFetchedAt(fetchedAt)
        setApiNote(data.items.length ? `东方财富暗盘资金 · ${data.scope}` : '暗盘资金暂不可用')
      })
      .catch(() => setApiNote('后端未启动'))
      .finally(() => setLoading(false))
  }, [darkScope])

  useEffect(() => {
    if (tab === 'funds') loadFunds()
    if (tab === 'hot') loadHot()
    if (tab === 'dark') loadDark()
  }, [loadDark, loadFunds, loadHot, tab])

  const refresh = () => {
    if (tab === 'funds') loadFunds(true)
    if (tab === 'hot') loadHot(true)
    if (tab === 'dark') loadDark(true)
  }

  const topIn = boardFlow?.inflow[0]
  const topOut = boardFlow?.outflow[0]
  const hotTop = hotThemes?.items[0]
  const darkTop = darkTrade?.items[0]

  return (
    <>
      <section className="flow-board flow-terminal">
        <div className="flow-header">
          <div className="flow-title-block">
            <h2>资金流证据</h2>
            <p>东方财富板块资金、热点题材、暗盘资金分开展示；没有连续分时数据时只给榜单，不画假曲线。</p>
          </div>

          <div className="segmented">
            <button className={tab === 'funds' ? 'selected' : ''} type="button" onClick={() => setTab('funds')}>
              <TrendingUp size={14} /> 主力资金
            </button>
            <button className={tab === 'hot' ? 'selected' : ''} type="button" onClick={() => setTab('hot')}>
              <Flame size={14} /> 热点题材
            </button>
            <button className={tab === 'dark' ? 'selected' : ''} type="button" onClick={() => setTab('dark')}>
              <MoonStar size={14} /> 暗盘资金
            </button>
          </div>

          {tab === 'funds' && (
            <>
              <div className="segmented compact">
                {BOARD_TYPES.map(item => (
                  <button className={boardType === item ? 'selected' : ''} key={item} type="button" onClick={() => setBoardType(item)}>
                    {item}
                  </button>
                ))}
              </div>
              <div className="segmented compact">
                {PERIODS.map(item => (
                  <button className={period === item ? 'selected' : ''} key={item} type="button" onClick={() => setPeriod(item)}>
                    {item}
                  </button>
                ))}
              </div>
            </>
          )}

          {tab === 'dark' && (
            <div className="segmented compact">
              {DARK_SCOPES.map(item => (
                <button className={darkScope === item ? 'selected' : ''} key={item} type="button" onClick={() => setDarkScope(item)}>
                  {item}
                </button>
              ))}
            </div>
          )}

          <button className="refresh-btn inline" type="button" onClick={refresh} disabled={loading}>
            <RefreshCcw size={14} />
            {loading ? '同步中' : '刷新'}
          </button>
          <span className="source-tag">{loading ? '同步中' : apiNote}</span>
          <span className="source-tag">{fetchedAt ? `缓存 ${new Date(fetchedAt).toLocaleTimeString('zh-CN')}` : '5 分钟缓存'}</span>
        </div>

        {tab === 'funds' && (
          <div className="flow-grid-new">
            <div className="flow-chart-area">
              <FlowChartSection
                flow={boardFlow}
                selected={selected}
                onSelect={setSelected}
                onOpenDetail={setDetailTarget}
              />
              {boardFlow?.notes?.length ? <p className="flow-note-line">{boardFlow.notes.join('；')}</p> : null}
            </div>
            <div className="flow-side-panels">
              <SectorRanking
                title={`${boardType}资金流入榜`}
                items={boardFlow?.inflow ?? []}
                direction="in"
                selected={selected}
                onSelect={setSelected}
                onOpenDetail={setDetailTarget}
              />
              <SectorRanking
                title={`${boardType}资金流出榜`}
                items={boardFlow?.outflow ?? []}
                direction="out"
                selected={selected}
                onSelect={setSelected}
                onOpenDetail={setDetailTarget}
              />
            </div>
          </div>
        )}

        {tab === 'hot' && <HotThemePanel data={hotThemes} loading={loading} />}
        {tab === 'dark' && <DarkTradePanel data={darkTrade} loading={loading} />}
      </section>

      <section className="decision-grid flow-summary-grid">
        <Panel title="当前最强证据">
          {tab === 'funds' && topIn && (
            <>
              <KV label="流入第一" value={topIn.name} />
              <KV label="净流入" value={`${fmtYi(topIn.net_inflow)}亿`} tone="up" />
              <KV label="主力净流入" value={`${fmtYi(topIn.main_inflow)}亿`} tone={topIn.main_inflow >= 0 ? 'up' : 'down'} />
            </>
          )}
          {tab === 'hot' && hotTop && (
            <>
              <KV label="热点第一" value={`${hotTop.period} · ${hotTop.name}`} />
              <KV label="涨幅" value={`${fmtPct(hotTop.change_pct)}`} tone={hotTop.change_pct >= 0 ? 'up' : 'down'} />
              <KV label="资金补充" value={`${fmtYi(hotTop.net_inflow)}亿`} tone={hotTop.net_inflow >= 0 ? 'up' : 'down'} />
            </>
          )}
          {tab === 'dark' && darkTop && (
            <>
              <KV label="暗盘第一" value={darkTop.name} />
              <KV label="暗盘资金" value={`${fmtYi(darkTop.dark_amount)}亿`} tone={darkTop.dark_amount >= 0 ? 'up' : 'down'} />
              <KV label="含暗盘主力" value={`${fmtYi(darkTop.main_net_inflow_with_dark)}亿`} tone={darkTop.main_net_inflow_with_dark >= 0 ? 'up' : 'down'} />
            </>
          )}
        </Panel>
        <Panel title="风险方向">
          {tab === 'funds' && topOut && (
            <>
              <KV label="流出第一" value={topOut.name} tone="down" />
              <KV label="净流出" value={`${fmtYi(topOut.net_inflow)}亿`} tone="down" />
            </>
          )}
          <p className="plain-text">资金证据只负责回答“钱在哪、哪里失血”，买卖动作仍要叠加持仓计划、个股强弱和利润保护触发器。</p>
        </Panel>
        <Panel title="口径说明">
          <div className="rule-list">
            <span>主力资金：东方财富板块资金流，失败才回落新浪</span>
            <span>热点题材：东方财富市场热点榜，资金字段按板块资金补充</span>
            <span>暗盘资金：东方财富算法口径，非 A 股真实暗盘交易</span>
          </div>
        </Panel>
      </section>

      {detailTarget && (
        <SectorDetailDrawer
          flowType={boardTypeToFlowType(boardType)}
          period={period}
          item={detailTarget}
          onClose={() => setDetailTarget(null)}
        />
      )}
    </>
  )
}

function HotThemePanel({ data, loading }: { data: HotThemesOut | null; loading: boolean }) {
  const grouped = useMemo(() => {
    const buckets = new Map<string, HotThemeItem[]>()
    ;(data?.items ?? []).forEach(item => {
      const list = buckets.get(item.period) ?? []
      list.push(item)
      buckets.set(item.period, list)
    })
    return Array.from(buckets.entries())
  }, [data])

  if (loading && !data) return <EmptyState title="热点题材同步中" body="正在读取东方财富市场热点榜。" />
  if (!data?.items.length) return <EmptyState title="暂无热点题材" body="东方财富热点接口暂未返回数据。" />

  return (
    <div className="flow-data-panel hot-theme-board">
      {grouped.map(([period, rows]) => (
        <article className="hot-theme-section" key={period}>
          <h3><Flame size={15} />{period}热点题材</h3>
          <div className="hot-theme-list">
            {rows.slice(0, 15).map(item => (
              <div className="hot-theme-row" key={`${item.period}-${item.board_code ?? item.name}`}>
                <span className="rank-num">{String(item.rank).padStart(2, '0')}</span>
                <strong>{item.name}</strong>
                <span className={item.change_pct >= 0 ? 'num-up' : 'num-down'}>{fmtPct(item.change_pct)}</span>
                <span className={item.net_inflow >= 0 ? 'num-up' : 'num-down'}>{fmtYi(item.net_inflow)}亿</span>
                <small>{item.reason}{item.leaders.length ? ` · ${item.leaders.slice(0, 2).join('、')}` : ''}</small>
              </div>
            ))}
          </div>
        </article>
      ))}
      {data.notes.length ? <p className="flow-note-line">{data.notes.join('；')}</p> : null}
    </div>
  )
}

function DarkTradePanel({ data, loading }: { data: DarkTradeOut | null; loading: boolean }) {
  if (loading && !data) return <EmptyState title="暗盘资金同步中" body="正在读取东方财富暗盘资金榜。" />
  if (!data?.items.length) return <EmptyState title="暂无暗盘资金" body="东方财富暗盘接口暂未返回数据。" />

  return (
    <div className="flow-data-panel">
      <div className="dark-table-head">
        <strong>{data.scope}暗盘资金榜</strong>
        <span>交易日 {data.trade_date}</span>
      </div>
      <div className="drawer-table-wrap dark-table-wrap">
        <table className="pos-table detail-table dark-flow-table">
          <thead>
            <tr>
              <th>排名</th>
              <th>名称</th>
              <th>涨幅</th>
              <th className="num">暗盘资金</th>
              <th className="num">明盘资金</th>
              <th className="num">主力净流入含暗盘</th>
              <th className="num">活跃度</th>
              <th className="num">流入比例</th>
              <th>领涨/行业</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map(item => (
              <DarkTradeRow item={item} key={`${item.code}-${item.rank}`} />
            ))}
          </tbody>
        </table>
      </div>
      {data.notes.length ? <p className="flow-note-line">{data.notes.join('；')}</p> : null}
    </div>
  )
}

function DarkTradeRow({ item }: { item: DarkTradeItem }) {
  const info = item.leading_stock || [item.industry, item.concept].filter(Boolean).join(' / ') || item.market
  return (
    <tr>
      <td className="mono">{String(item.rank).padStart(2, '0')}</td>
      <td>
        <strong>{item.name}</strong>
        <small>{item.code}</small>
      </td>
      <td className={item.change_pct >= 0 ? 'num num-up' : 'num num-down'}>{fmtPct(item.change_pct)}</td>
      <td className={item.dark_amount >= 0 ? 'num num-up' : 'num num-down'}>{fmtYi(item.dark_amount)}亿</td>
      <td className={item.lit_amount >= 0 ? 'num num-up' : 'num num-down'}>{fmtYi(item.lit_amount)}亿</td>
      <td className={item.main_net_inflow_with_dark >= 0 ? 'num num-up' : 'num num-down'}>{fmtYi(item.main_net_inflow_with_dark)}亿</td>
      <td className="num">{item.dark_activity.toFixed(2)}%</td>
      <td className="num">{item.inflow_stock_ratio ? `${item.inflow_stock_ratio.toFixed(2)}%` : '--'}</td>
      <td>{info || '--'}</td>
    </tr>
  )
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="chart-surface-large chart-empty-state">
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  )
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
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

function sourceLabel(source: string, notes: string[]) {
  if (source.includes('diagnostic')) return '诊断数据'
  if (source.includes('sina')) return '新浪资金流兜底'
  if (source.includes('eastmoney-fflow')) return '东方财富板块资金 · 分时'
  if (source.includes('snapshots')) {
    const n = source.split('snapshots:')[1]?.split('|')[0] || '?'
    return `东方财富 · ${n} 个快照`
  }
  if (source.includes('estimates')) return '东方财富 · 快照累积中'
  if (notes.some(note => note.includes('分层资金可能缺失'))) return '东方财富板块资金 · 分层资金受限'
  return '东方财富板块资金'
}

function boardTypeToFlowType(boardType: BoardType) {
  if (boardType === '概念' || boardType === '风格') return '概念资金流'
  if (boardType === '地域') return '地域资金流'
  return '行业资金流'
}

function fmtYi(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`
}

function fmtPct(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}
