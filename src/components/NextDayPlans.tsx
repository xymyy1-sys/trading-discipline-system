import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCcw, Save, Trash2 } from 'lucide-react'
import { API_BASE } from '../api'
import { usePrivacyMode } from '../privacy-context'
import { SensitiveValue } from '../privacy'

import type {
  ClassificationBasis as Basis,
  AuctionPlan,
  NextDayPlanOut as Plan,
  MarketSeesaw as SeesawMonitor,
} from '../types'

const categories = ['超预期', '强预期', '符合预期', '弱转强', '弱于预期', '分歧转弱']

export default function NextDayPlans({ mode = 'holding' }: { mode?: 'holding' | 'limit' }) {
  const privacyMode = usePrivacyMode()
  const [plans, setPlans] = useState<Plan[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [draft, setDraft] = useState<Plan | null>(null)
  const [seesaw, setSeesaw] = useState<SeesawMonitor | null>(null)
  const [loading, setLoading] = useState(false)
  const [statusText, setStatusText] = useState('')
  const selectedIdRef = useRef<number | null>(null)

  const loadPlans = useCallback((refresh = false) => {
    setLoading(true)
    setStatusText('')
    fetch(
      `${API_BASE}/api/next-day-plans${refresh ? '/refresh' : ''}`,
      refresh ? { method: 'POST' } : undefined,
    )
      .then(async response => {
        if (!response.ok) throw new Error(await response.text())
        return response.json()
      })
      .then((data: Plan[]) => {
        const scoped = data.filter(item => mode === 'limit' ? item.plan_type === 'limit_up_auction' : item.plan_type === 'holding')
        setPlans(scoped)
        const now = new Date().toLocaleTimeString('zh-CN', { hour12: false })
        setStatusText(`${now} ${refresh ? '已刷新盘中现状' : '已读取计划卡'}`)
        const currentSelectedId = selectedIdRef.current
        if (!currentSelectedId && scoped[0]) {
          selectedIdRef.current = scoped[0].id
          setSelectedId(scoped[0].id)
          setDraft(structuredClone(scoped[0]))
        } else if (currentSelectedId) {
          const refreshed = scoped.find(item => item.id === currentSelectedId)
          if (refreshed) setDraft(refreshed)
        }
      })
      .catch(() => setStatusText(refresh ? '刷新失败，继续保留原计划' : '计划读取失败，请稍后重试'))
      .finally(() => setLoading(false))
  }, [mode])

  useEffect(() => {
    loadPlans()
    const loadSeesaw = () => {
      fetch(`${API_BASE}/api/market/seesaw-monitor`)
        .then(r => r.json())
        .then((data: SeesawMonitor) => setSeesaw(data))
        .catch(() => {})
    }
    loadSeesaw()
  }, [loadPlans, mode])

  const selected = useMemo(() => plans.find(p => p.id === selectedId) ?? null, [plans, selectedId])
  const selectedSeesaw = useMemo(
    () => seesaw?.holding_alerts.find(item => item.code === selected?.code || item.name === selected?.name) ?? null,
    [seesaw, selected],
  )
  const limitOrderEligibility = useMemo(() => {
    if (!draft || draft.plan_type !== 'limit_up_auction') {
      return { allowed: false, reason: '仅打板预案适用主线门控。' }
    }
    const auction = draft.auction_plan
    if (auction.is_mainline !== true) {
      return { allowed: false, reason: auction.is_mainline === false ? '该标的不属于当前主线，系统仓位上限为 0。' : '主线归属尚未被真实数据确认，暂不允许挂单。' }
    }
    if (['高潮', '退潮'].includes(auction.theme_stage)) {
      return { allowed: false, reason: `题材处于${auction.theme_stage}阶段，不接一致性高潮，也不参与退潮博弈。` }
    }
    if (!(auction.max_position_ratio > 0)) {
      return { allowed: false, reason: '系统未开放仓位，只生成观察预案。' }
    }
    if (!auction.board_strength || !auction.limit_quality) {
      return { allowed: false, reason: '板块订单流方向或封板质量证据不完整，暂不允许挂单。' }
    }
    return { allowed: true, reason: '主线、题材阶段、身份竞争和封板质量均已通过，仍须等待竞价与开盘量价确认。' }
  }, [draft])
  const conditionOrderAdvice = useMemo(() => {
    if (!draft) return null
    if (draft.plan_type === 'limit_up_auction') {
      return {
        level: limitOrderEligibility.allowed ? '谨慎预埋' : '禁止预埋',
        action: limitOrderEligibility.allowed
          ? `可在券商端预填 ${num(draft.auction_plan.order_price || draft.limit_up_price)} 的买入委托，但盘前不得无条件启用。`
          : `${limitOrderEligibility.reason}只保存观察预案，不向券商提交委托。`,
        trigger: draft.auction_plan.keep_order_condition || draft.auction_plan.opening_confirmation || '竞价强度、主线地位和量价同时确认后才允许生效。',
        cancel: draft.auction_plan.cancel_condition || '竞价弱于预期、主线退潮、核心股负反馈或数据源异常时立即撤单。',
      }
    }
    const riskPrice = draft.final_risk_price || draft.reduce_price
    if (!riskPrice) {
      return { level: '暂不预埋', action: '尚未设置有效风险价，先补全减仓线与最终风险线。', trigger: '价格条件缺失。', cancel: '计划更新前不提交条件单。' }
    }
    return {
      level: '建议预埋防守单',
      action: `预埋卖出保护：跌破 ${num(riskPrice)} 执行减仓/退出；不预埋补仓买单。`,
      trigger: `减仓线 ${num(draft.reduce_price)}，最终风险线 ${num(draft.final_risk_price)}；数量以计划卡和可卖数量为上限。`,
      cancel: '只有收盘后重新评估并保存了更强证据，才允许上调或撤销保护线；盘中不得因主观期待放宽。',
    }
  }, [draft, limitOrderEligibility])

  const selectPlan = (plan: Plan) => {
    selectedIdRef.current = plan.id
    setSelectedId(plan.id)
    setDraft(structuredClone(plan))
  }

  const generatePlans = () => {
    setLoading(true)
    setStatusText('')
    fetch(`${API_BASE}/api/next-day-plans/generate`, { method: 'POST' })
      .then(r => r.json())
      .then((data: Plan[]) => {
        const scoped = data.filter(item => item.plan_type === 'holding')
        setPlans(scoped)
        setStatusText('已同步：已有持仓计划刷新，新仓才新增')
        if (scoped[0]) selectPlan(scoped[0])
      })
      .finally(() => setLoading(false))
  }

  const refreshPlanStatus = () => {
    loadPlans(true)
  }

  const updateDraft = <K extends keyof Plan>(key: K, value: Plan[K]) => {
    setDraft(p => p ? { ...p, [key]: value } : p)
  }

  const updateBasis = <K extends keyof Basis>(key: K, value: Basis[K]) => {
    setDraft(p => p ? { ...p, classification_basis: { ...p.classification_basis, [key]: value } } : p)
  }

  const updateAuctionPlan = <K extends keyof AuctionPlan>(key: K, value: AuctionPlan[K]) => {
    setDraft(p => p ? { ...p, auction_plan: { ...p.auction_plan, [key]: value } } : p)
  }

  const savePlan = () => {
    if (!draft) return
    const payload = draft.plan_type === 'limit_up_auction' && !limitOrderEligibility.allowed
      ? { ...draft, auction_plan: { ...draft.auction_plan, overnight_order: false } }
      : draft
    fetch(`${API_BASE}/api/next-day-plans/${draft.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(r => r.json())
      .then((saved: Plan) => {
        setPlans(prev => prev.map(item => item.id === saved.id ? saved : item))
        setDraft(saved)
      })
  }

  const refreshStage = () => {
    if (!draft?.id) return
    setLoading(true)
    fetch(`${API_BASE}/api/next-day-plans/${draft.id}/stage-refresh`, { method: 'POST' })
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((saved: Plan) => {
        setPlans(prev => prev.map(item => item.id === saved.id ? saved : item))
        setDraft(saved)
        setStatusText(`已刷新阶段验收：${saved.auction_plan.current_stage || '当前阶段'}`)
      })
      .catch(() => setStatusText('阶段验收刷新失败'))
      .finally(() => setLoading(false))
  }

  const deletePlan = (plan: Plan) => {
    if (!window.confirm(`确认删除计划卡 ${plan.name}？`)) return
    setLoading(true)
    fetch(`${API_BASE}/api/next-day-plans/${plan.id}`, { method: 'DELETE' })
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        const nextPlans = plans.filter(item => item.id !== plan.id)
        setPlans(nextPlans)
        if (selectedId === plan.id) {
          const next = nextPlans[0] ?? null
          selectedIdRef.current = next?.id ?? null
          setSelectedId(next?.id ?? null)
          setDraft(next ? structuredClone(next) : null)
        }
        setStatusText('计划卡已删除')
      })
      .catch(() => setStatusText('删除失败'))
      .finally(() => setLoading(false))
  }

  return (
    <section className="plan-page">
      <header className="pos-header">
        <div>
          <h2>{mode === 'limit' ? '打板预案' : '持仓次日计划'}</h2>
          <p>{mode === 'limit' ? '只展示从涨停天梯生成的打板预案，以及当日涨停的持仓股。' : '只管理普通持仓的下一交易日剧本，涨停持仓自动转入打板预案。'}</p>
        </div>
        <div className="header-actions">
          <button className="refresh-btn inline" type="button" onClick={refreshPlanStatus} disabled={loading}>
            <RefreshCcw size={16} />
            {loading ? '刷新中' : '刷新现状'}
          </button>
          {mode === 'holding' && <button className="grade-btn" type="button" onClick={generatePlans} disabled={loading}>
            <RefreshCcw size={16} />
            {loading ? '同步中' : '生成/同步计划卡'}
          </button>}
        </div>
      </header>
      {statusText && <p className="refresh-note">{statusText}</p>}

      <div className="plan-layout">
        <aside className="panel plan-list">
          <h3>持仓计划</h3>
          {plans.map(plan => (
            <div className="plan-row-wrap" key={plan.id}>
              <button
                className={`plan-row risk-${plan.risk_priority} ${selected?.id === plan.id ? 'active' : ''}`}
                type="button"
                onClick={() => selectPlan(plan)}
              >
                <strong>{plan.name}</strong>
                <span>{plan.code} · {plan.holding_category}</span>
                {plan.plan_type === 'limit_up_auction' && <em>打板/竞价预案</em>}
                <small>
                  {plan.plan_date} · 仓位 <SensitiveValue>{(plan.position_ratio * 100).toFixed(1)}%</SensitiveValue>
                  {' · '}浮盈 <SensitiveValue>{(plan.profit_ratio * 100).toFixed(2)}%</SensitiveValue>
                </small>
              </button>
              <button className="plan-delete-btn" type="button" title="删除计划" onClick={() => deletePlan(plan)}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          {!plans.length && <p className="plain-text">暂无计划卡，点击生成。</p>}
        </aside>

        {draft ? (
          <div className="plan-editor">
            <section className="panel">
              <div className="selected-theme-head">
                <div>
                  <strong>{draft.name} <span className="mono">{draft.code}</span></strong>
                  <span>
                    <SensitiveValue>{draft.quantity.toLocaleString()} 股</SensitiveValue>
                    {' · '}成本 <SensitiveValue>{draft.cost_price.toFixed(2)}</SensitiveValue>
                    {' · '}现价 {draft.current_price.toFixed(2)}
                    {draft.plan_type === 'holding' && <>
                      {' · '}市值 <SensitiveValue>{draft.market_value.toLocaleString()}</SensitiveValue>
                      {' · '}盈亏 <SensitiveValue>{draft.profit_amount >= 0 ? '+' : ''}{draft.profit_amount.toLocaleString()}</SensitiveValue>
                    </>}
                  </span>
                  {draft.plan_type === 'holding' && (
                    <span className={draft.price_source === 'realtime' ? 'quote-note live' : 'quote-note stale'}>
                      {draft.price_source === 'realtime' ? '实时行情' : '缓存/手工价'} · {draft.price_note || '行情说明暂无'}
                    </span>
                  )}
                  {draft.plan_type === 'limit_up_auction' && (
                    <span className="auction-headline">
                      明日涨停价 {draft.limit_up_price.toFixed(2)} · 隔夜委托价 {draft.auction_plan.order_price.toFixed(2)}
                    </span>
                  )}
                </div>
                <button className="refresh-btn inline" type="button" onClick={savePlan}>
                  <Save size={14} />
                  保存
                </button>
                <button className="grade-btn" type="button" onClick={refreshStage} disabled={loading}>
                  <RefreshCcw size={14} />
                  刷新阶段验收
                </button>
              </div>

              {conditionOrderAdvice && (
                <div className="condition-order-advice">
                  <div>
                    <span>条件单结论</span>
                    <strong>{conditionOrderAdvice.level}</strong>
                  </div>
                  <p><b>怎么挂：</b>{conditionOrderAdvice.action}</p>
                  <p><b>生效条件：</b>{conditionOrderAdvice.trigger}</p>
                  <p><b>撤销条件：</b>{conditionOrderAdvice.cancel}</p>
                  <small>系统只生成券商条件单模板，不会替你自动下单；提交前必须核对价格、可卖数量和券商触发规则。</small>
                </div>
              )}

              <div className="auction-evidence-grid">
                <div>
                  <b>实时行情与现状</b>
                  <p>{draft.auction_plan.intraday_status || draft.price_note || '等待刷新行情。'}</p>
                </div>
                <div>
                  <b>今日量价状态</b>
                  <p>{draft.auction_plan.volume_price_status || '等待5日成交量与今日成交量计算。'}{draft.auction_plan.notes ? `；${draft.auction_plan.notes}` : ''}</p>
                </div>
                <div>
                  <b>预期校验</b>
                  <p>{draft.auction_plan.expectation_match || draft.auction_plan.expectation_level || draft.holding_category}；原预期：{draft.auction_plan.expected_state || draft.expected_condition}</p>
                </div>
                <div>
                  <b>操作建议</b>
                  <p>{draft.auction_plan.operation_advice || '按三套剧本和关键价执行。'}</p>
                </div>
                <div>
                  <b>订单流跷跷板监控</b>
                  {selectedSeesaw ? (
                    <>
                      <p>{selectedSeesaw.risk_level} · {selectedSeesaw.signal}。{selectedSeesaw.advice}</p>
                      <p>
                        所属主线：{selectedSeesaw.holding_theme || selectedSeesaw.sector || draft.classification_basis.sector || '待确认'}；
                        主口径：{(selectedSeesaw.flow_basis || '订单流方向估算').replace('资金流', '订单流算法')}；
                        板块订单流方向曲线：{selectedSeesaw.primary_industry_sector || selectedSeesaw.matched_flow_sector || '未匹配'}，当前 {selectedSeesaw.theme_flow_current.toFixed(2)} 亿（供应商算法，非账户真实流水）
                        {selectedSeesaw.theme_flow_pullback > 0 ? `，高位回落 ${selectedSeesaw.theme_flow_pullback.toFixed(2)} 亿（${selectedSeesaw.theme_flow_pullback_pct.toFixed(1)}%）` : ''}；
                        个股画像：{selectedSeesaw.stock_industry || '未抓到行业'} / {(selectedSeesaw.stock_concepts || []).slice(0, 4).join('、') || '未抓到概念'}；
                        概念辅助：{(selectedSeesaw.concept_flow_sectors?.length ? selectedSeesaw.concept_flow_sectors.slice(0, 4).join('、') : '不参与主曲线')}；
                        外部吸金：{selectedSeesaw.external_inflow_target || '暂无'}。
                      </p>
                      <ul>
                        {selectedSeesaw.evidence.slice(0, 4).map(item => <li key={item}>{item}</li>)}
                      </ul>
                    </>
                  ) : (
                    <p>{seesaw?.summary || '等待市场跷跷板监控刷新。'}</p>
                  )}
                </div>
                <div>
                  <b>盘中卖出触发器</b>
                  {selectedSeesaw ? (
                    <div className="sell-trigger-list">
                      <p>{selectedSeesaw.profit_protection_state || '尚未进入利润保护区。'}</p>
                      <p>板块退潮：{selectedSeesaw.sector_ebb_trigger[0] || '未触发'}</p>
                      <p>个股弱化：{selectedSeesaw.stock_weakening_trigger[0] || '未触发'}</p>
                      <p>利润回撤：{selectedSeesaw.profit_drawdown_trigger[0] || '未触发'}</p>
                      <p>接回：{selectedSeesaw.buyback_trigger[0] || '等待板块止跌、个股站回均价。'}</p>
                    </div>
                  ) : (
                    <ul>
                      {(draft.auction_plan.sell_trigger_cards?.length ? draft.auction_plan.sell_trigger_cards : [
                        '利润保护：浮盈5%以上进入保护，不再幻想涨停。',
                        '板块退潮：板块订单流方向排名下滑、主线核心同步回落时触发。',
                        '个股弱化：冲高不能封板、跌破分时均价、放量下跌时触发。',
                        '接回条件：只在板块止跌、个股站回均价、量价重新转强时接回。',
                      ]).slice(0, 6).map(item => <li key={item}>{item}</li>)}
                    </ul>
                  )}
                </div>
              </div>

              <div className="form-grid">
                <label>预期管理分类
                  <select value={draft.holding_category} onChange={e => updateDraft('holding_category', e.target.value)}>
                    {categories.map(item => <option key={item}>{item}</option>)}
                  </select>
                </label>
                <label>确认位
                  <input type="number" step="0.01" value={draft.confirm_price} onChange={e => updateDraft('confirm_price', Number(e.target.value))} />
                </label>
                <label>高抛价
                  <input type="number" step="0.01" value={draft.trim_price} onChange={e => updateDraft('trim_price', Number(e.target.value))} />
                </label>
                <label>高抛股数
                  <SensitiveNumberInput privacyMode={privacyMode} value={draft.trim_quantity} onChange={value => updateDraft('trim_quantity', value)} />
                </label>
                <label>减仓线
                  <input type="number" step="0.01" value={draft.reduce_price} onChange={e => updateDraft('reduce_price', Number(e.target.value))} />
                </label>
                <label>最终风险线
                  <input type="number" step="0.01" value={draft.final_risk_price} onChange={e => updateDraft('final_risk_price', Number(e.target.value))} />
                </label>
                <label>买回价
                  <input type="number" step="0.01" value={draft.buyback_price} onChange={e => updateDraft('buyback_price', Number(e.target.value))} />
                </label>
                <label>最大买回股数
                  <SensitiveNumberInput privacyMode={privacyMode} value={draft.max_buyback_quantity} onChange={value => updateDraft('max_buyback_quantity', value)} />
                </label>
                <label className="check-item done">
                  <input type="checkbox" checked={draft.allow_buyback} onChange={e => updateDraft('allow_buyback', e.target.checked)} />
                  <span>允许买回</span>
                </label>
              </div>
            </section>

            {(draft.plan_type === 'limit_up_auction' || draft.auction_plan.board_strength || draft.auction_plan.limit_quality) && (
              <section className="panel auction-plan-panel">
                <h3>打板预期分析</h3>
                <div className="stage-decision-bar">
                  <div>
                    <b>{draft.auction_plan.current_stage || '阶段待确认'}</b>
                    <p>{draft.auction_plan.stage_decision || draft.auction_plan.operation_advice || '点击刷新阶段验收，生成竞价/开盘/五分钟/冲板/炸板处理结论。'}</p>
                  </div>
                  <span>{draft.auction_plan.refreshed_at || '未刷新'}</span>
                </div>
                {draft.plan_type === 'limit_up_auction' && (
                  <div className={`limit-mainline-decision ${limitOrderEligibility.allowed ? 'eligible' : 'blocked'}`}>
                    <div className="limit-mainline-head">
                      <div>
                        <span>主线地位 × 题材阶段 × 个股身份</span>
                        <h4>{draft.auction_plan.mainline_name || draft.auction_plan.industry || '主线尚未确认'}</h4>
                      </div>
                      <strong>{limitOrderEligibility.allowed ? '允许条件观察仓' : '禁止开仓'}</strong>
                    </div>
                    <div className="limit-mainline-badges">
                      <span className={draft.auction_plan.is_mainline ? 'positive' : 'negative'}>
                        {draft.auction_plan.mainline_level || (draft.auction_plan.is_mainline ? '主线' : '非主线')}
                      </span>
                      <span>主线排名 {draft.auction_plan.mainline_rank ?? '--'}</span>
                      <span>主线强度 {draft.auction_plan.mainline_score == null ? '--' : draft.auction_plan.mainline_score.toFixed(0)}</span>
                      <span className={['高潮', '退潮'].includes(draft.auction_plan.theme_stage) ? 'negative' : ''}>
                        题材阶段 {draft.auction_plan.theme_stage || '待确认'}
                      </span>
                      <span>身份 {draft.auction_plan.identity_roles?.join(' · ') || '待确认'}</span>
                    </div>
                    <p className="limit-stage-reason">{draft.auction_plan.theme_stage_reason || '等待题材雷达、订单流方向排名和涨停梯队形成阶段判断。'}</p>
                    <div className="limit-position-gate">
                      <div>
                        <b>身份竞争结论</b>
                        <p>{draft.auction_plan.identity_action || '身份未确认，不参与开仓。'}</p>
                      </div>
                      <div>
                        <b>阶段仓位规则</b>
                        <p>{draft.auction_plan.position_rule || '主线与阶段未确认前仓位为 0。'}</p>
                      </div>
                      <div className="limit-system-cap">
                        <b>系统仓位上限</b>
                        <strong>{(draft.auction_plan.max_position_ratio * 100).toFixed(0)}%</strong>
                      </div>
                    </div>
                    <p className="limit-order-gate-result"><b>条件单门控：</b>{limitOrderEligibility.reason}</p>
                    <div className="limit-theme-evidence">
                      <b>数据依据</b>
                      {draft.auction_plan.theme_evidence?.length ? (
                        <ul>{draft.auction_plan.theme_evidence.slice(0, 8).map(item => <li key={item}>{item}</li>)}</ul>
                      ) : <p>暂无可审计的主线排名、阶段和梯队证据，仓位保持 0。</p>}
                    </div>
                  </div>
                )}
                {!!draft.auction_plan.stage_checks?.length && (
                  <div className="stage-check-grid">
                    {draft.auction_plan.stage_checks.map(item => (
                      <article className={`stage-check status-${item.status}`} key={item.stage}>
                        <div>
                          <strong>{item.stage}</strong>
                          <span>{item.status}</span>
                        </div>
                        <p>{item.trigger}</p>
                        <p>{item.decision}</p>
                        <b>{item.required_action}</b>
                        {!!item.evidence.length && (
                          <ul>{item.evidence.slice(0, 3).map(evidence => <li key={evidence}>{evidence}</li>)}</ul>
                        )}
                      </article>
                    ))}
                  </div>
                )}
                {!!draft.auction_plan.action_ladder?.length && (
                  <div className="action-ladder">
                    {draft.auction_plan.action_ladder.map(item => <span key={item}>{item}</span>)}
                  </div>
                )}
                <div className="auction-metrics">
                  <span>连板高度 <strong>{draft.auction_plan.board_level || '--'}</strong></span>
                  <span>明日涨停 <strong>{draft.limit_up_price.toFixed(2)}</strong></span>
                  <span>仓位上限 <strong><SensitiveValue>{(draft.auction_plan.max_position_ratio * 100).toFixed(0)}%</SensitiveValue></strong></span>
                  <span>预期级别 <strong>{draft.auction_plan.expectation_match || draft.auction_plan.expectation_level || draft.holding_category}</strong></span>
                </div>
                <div className="auction-evidence-grid">
                  <div>
                    <b>板块订单流强度</b>
                    <p>{draft.auction_plan.board_strength || '等待刷新题材雷达/订单流方向估算后补充。'}</p>
                    {!!draft.auction_plan.board_strength_detail?.length && (
                      <ul>{draft.auction_plan.board_strength_detail.slice(0, 4).map(item => <li key={item}>{item}</li>)}</ul>
                    )}
                  </div>
                  <div>
                    <b>封板质量</b>
                    <p>{draft.auction_plan.limit_quality || '等待涨停天梯数据补充。'}</p>
                  </div>
                  <div>
                    <b>弱预期关键价</b>
                    <p>
                      强弱分界 {num(draft.auction_plan.strong_boundary_price || draft.confirm_price)}；
                      跌破 {num(draft.auction_plan.weak_reduce_price || draft.reduce_price)} 减仓；
                      跌破 {num(draft.auction_plan.weak_exit_price || draft.final_risk_price)} 清仓。
                    </p>
                  </div>
                  <div>
                    <b>前排助攻</b>
                    <ul>
                      {(draft.auction_plan.leader_support?.length ? draft.auction_plan.leader_support : ['等待刷新涨停天梯和题材雷达。']).slice(0, 6).map(item => <li key={item}>{item}</li>)}
                    </ul>
                  </div>
                  <div>
                    <b>明日三套剧本</b>
                    <ul>
                      {(draft.auction_plan.next_day_script?.length ? draft.auction_plan.next_day_script : [
                        draft.outperform_condition,
                        draft.expected_condition,
                        draft.underperform_condition,
                      ]).slice(0, 3).map(item => <li key={item}>{item}</li>)}
                    </ul>
                  </div>
                </div>
                {draft.plan_type === 'limit_up_auction' && <div className="form-grid">
                  <label>隔夜委托价
                    <input type="number" step="0.01" value={draft.auction_plan.order_price} onChange={e => updateAuctionPlan('order_price', Number(e.target.value))} />
                  </label>
                  <label>明日涨停价
                    <input type="number" step="0.01" value={draft.limit_up_price} onChange={e => updateDraft('limit_up_price', Number(e.target.value))} />
                  </label>
                  <label>系统仓位上限（只读）
                    <SensitiveNumberInput privacyMode={privacyMode} readOnly step="0.01" value={draft.auction_plan.max_position_ratio} onChange={() => {}} />
                  </label>
                  <label className="check-item done">
                    <input
                      type="checkbox"
                      checked={limitOrderEligibility.allowed && draft.auction_plan.overnight_order}
                      disabled={!limitOrderEligibility.allowed}
                      onChange={e => updateAuctionPlan('overnight_order', e.target.checked)}
                    />
                    <span>允许隔夜挂单</span>
                  </label>
                </div>}
                {draft.plan_type === 'limit_up_auction' && (
                  <>
                    <textarea className="plan-textarea" placeholder="保留委托条件" value={draft.auction_plan.keep_order_condition} onChange={e => updateAuctionPlan('keep_order_condition', e.target.value)} />
                    <textarea className="plan-textarea" placeholder="撤单条件" value={draft.auction_plan.cancel_condition} onChange={e => updateAuctionPlan('cancel_condition', e.target.value)} />
                    <textarea className="plan-textarea" placeholder="开盘确认" value={draft.auction_plan.opening_confirmation} onChange={e => updateAuctionPlan('opening_confirmation', e.target.value)} />
                    <textarea className="plan-textarea" placeholder="炸板处理" value={draft.auction_plan.break_limit_action} onChange={e => updateAuctionPlan('break_limit_action', e.target.value)} />
                  </>
                )}
              </section>
            )}

            <section className="panel">
              <h3>分类依据</h3>
              <div className="form-grid">
                <input placeholder="板块" value={draft.classification_basis.sector} onChange={e => updateBasis('sector', e.target.value)} />
                <input placeholder="主线地位" value={draft.classification_basis.mainline_position} onChange={e => updateBasis('mainline_position', e.target.value)} />
                <input placeholder="订单流方向估算（供应商算法）" value={draft.classification_basis.fund_flow} onChange={e => updateBasis('fund_flow', e.target.value)} />
                <input placeholder="成交额" value={draft.classification_basis.amount} onChange={e => updateBasis('amount', e.target.value)} />
                <input placeholder="换手率" value={draft.classification_basis.turnover} onChange={e => updateBasis('turnover', e.target.value)} />
                <input placeholder="趋势" value={draft.classification_basis.trend} onChange={e => updateBasis('trend', e.target.value)} />
                <input placeholder="支撑位" value={draft.classification_basis.support} onChange={e => updateBasis('support', e.target.value)} />
                <input placeholder="压力位" value={draft.classification_basis.pressure} onChange={e => updateBasis('pressure', e.target.value)} />
              </div>
            </section>

            <section className="plan-scripts">
              <Scenario title="超预期" condition={draft.outperform_condition} action={draft.outperform_action} onCondition={v => updateDraft('outperform_condition', v)} onAction={v => updateDraft('outperform_action', v)} />
              <Scenario title="符合预期" condition={draft.expected_condition} action={draft.expected_action} onCondition={v => updateDraft('expected_condition', v)} onAction={v => updateDraft('expected_action', v)} />
              <Scenario title="弱于预期" condition={draft.underperform_condition} action={draft.underperform_action} onCondition={v => updateDraft('underperform_condition', v)} onAction={v => updateDraft('underperform_action', v)} />
            </section>

            <section className="panel">
              <h3>高抛低吸约束</h3>
              <div className="form-grid">
                <input placeholder="高抛条件" value={draft.trim_condition} onChange={e => updateDraft('trim_condition', e.target.value)} />
                <input placeholder="买回条件" value={draft.buyback_condition} onChange={e => updateDraft('buyback_condition', e.target.value)} />
                <input placeholder="4%止损参考" type="number" step="0.01" value={draft.stop_loss_4pct} onChange={e => updateDraft('stop_loss_4pct', Number(e.target.value))} />
                <input placeholder="盘后复盘：属于哪种预期" value={draft.review_expectation} onChange={e => updateDraft('review_expectation', e.target.value)} />
              </div>
              <textarea className="plan-textarea" placeholder="实际执行情况" value={draft.review_execution} onChange={e => updateDraft('review_execution', e.target.value)} />
              <textarea className="plan-textarea" placeholder="偏离原因" value={draft.review_deviation} onChange={e => updateDraft('review_deviation', e.target.value)} />
              <div className="warning-list">
                {draft.forbidden_actions.map(item => <span key={item}>{item}</span>)}
                {draft.risk_warnings.map(item => <strong key={item}>{item}</strong>)}
              </div>
            </section>
          </div>
        ) : (
          <div className="panel"><p className="plain-text">选择一张计划卡，或先生成计划卡。</p></div>
        )}
      </div>
    </section>
  )
}

function num(value: number) {
  return Number.isFinite(value) && value > 0 ? value.toFixed(2) : '--'
}

function SensitiveNumberInput({
  privacyMode,
  value,
  onChange,
  step,
  readOnly = false,
}: {
  privacyMode: boolean
  value: number
  onChange: (value: number) => void
  step?: string
  readOnly?: boolean
}) {
  const locked = privacyMode || readOnly
  return (
    <input
      className="sensitive-number-input"
      data-sensitive="true"
      type={privacyMode ? 'text' : 'number'}
      step={privacyMode ? undefined : step}
      value={privacyMode ? '******' : value}
      readOnly={locked}
      aria-label={privacyMode ? '敏感数据已隐藏' : readOnly ? '系统仓位上限，只读' : undefined}
      onChange={event => {
        if (!locked) onChange(Number(event.target.value))
      }}
    />
  )
}

function Scenario({
  title,
  condition,
  action,
  onCondition,
  onAction,
}: {
  title: string
  condition: string
  action: string
  onCondition: (value: string) => void
  onAction: (value: string) => void
}) {
  return (
    <article className="panel">
      <h3>{title}</h3>
      <textarea className="plan-textarea" value={condition} onChange={e => onCondition(e.target.value)} />
      <textarea className="plan-textarea" value={action} onChange={e => onAction(e.target.value)} />
    </article>
  )
}
