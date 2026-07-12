import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import type { SectorFlowItem, SectorDetail } from '../../types'
import { API_BASE } from '../../api'
import { cachedJson } from '../../apiCache'

function MiniStat({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  )
}

export default function SectorDetailDrawer({
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
          <MiniStat label="板块指数" value={item.sector_price?.toFixed(2) ?? '--'} tone={item.sector_vwap_reliable ? (item.sector_below_vwap ? 'down' : 'up') : undefined} />
          <MiniStat label="板块VWAP" value={item.sector_vwap?.toFixed(2) ?? '--'} />
        </div>

        <p className="plain-text">
          {item.sector_vwap_reliable
            ? `东方财富板块指数分钟均价口径：当前指数${item.sector_below_vwap ? '低于' : '高于'} VWAP，共 ${item.index_timeline.length} 个真实分钟点。`
            : '板块指数真实分钟均价暂不可用，不生成板块 VWAP 结论。'}
        </p>

        {((detail?.flow_breakdown?.length ? detail.flow_breakdown : item.flow_breakdown) ?? []).length > 0 && (
          <div className="drawer-flow-breakdown">
            {(detail?.flow_breakdown?.length ? detail.flow_breakdown : item.flow_breakdown).map(part => (
              <span className={part.net >= 0 ? 'in' : 'out'} key={part.name}>
                <b>{part.name}</b>
                <strong>{part.net >= 0 ? '+' : ''}{part.net.toFixed(2)}亿</strong>
                <em>{part.ratio >= 0 ? '+' : ''}{part.ratio.toFixed(2)}%</em>
              </span>
            ))}
          </div>
        )}

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
