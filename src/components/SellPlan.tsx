import { useEffect, useState } from 'react'
import { ShieldAlert, RefreshCw } from 'lucide-react'
import { API_BASE } from '../api'

type SellPlan = {
  code: string
  name: string
  first_trim_price: number
  second_exit_price: number
  failure_price: number
  sell_ratios: string[]
  allow_buyback: boolean
  buyback_condition: string
  condition_orders: string[]
}

export default function SellPlan() {
  const [plans, setPlans] = useState<SellPlan[]>([])

  const fetchPlans = () => {
    fetch(`${API_BASE}/api/sell-plans`)
      .then(r => r.json())
      .then(setPlans)
      .catch(() => {})
  }
  useEffect(() => { fetchPlans() }, [])

  return (
    <div className="sell-layout">
      <header className="env-hero">
        <div>
          <h2>卖出执行卡</h2>
          <p>每只持仓的卖出计划：减仓位、退出位、失效位、卖出比例、是否买回、条件单建议。</p>
        </div>
        <button className="grade-btn" onClick={fetchPlans}>
          <RefreshCw size={16} /> 刷新计划
        </button>
      </header>

      {plans.length === 0 ? (
        <div className="panel">
          <p className="plain-text">暂无持仓，无法生成卖出计划。请先在"持仓快照"中录入持仓。</p>
        </div>
      ) : (
        <>
          <div className="sell-summary">共 {plans.length} 只持仓需要卖出计划</div>
          <div className="sell-cards">
            {plans.map((plan, i) => (
              <article className="sell-card" key={plan.code}>
                <div className="sell-card-header">
                  <span className="sell-idx">#{i + 1}</span>
                  <div>
                    <strong>{plan.name}</strong>
                    <span className="mono">{plan.code}</span>
                  </div>
                  <span className="sell-badge">已生成</span>
                </div>
                <div className="sell-prices">
                  <div className="sell-price-tier tier-1">
                    <span>第一减仓位</span>
                    <strong>{plan.first_trim_price.toFixed(2)}</strong>
                    <small>{plan.sell_ratios[0]}</small>
                  </div>
                  <div className="sell-price-tier tier-2">
                    <span>第二退出位</span>
                    <strong>{plan.second_exit_price.toFixed(2)}</strong>
                    <small>{plan.sell_ratios[1]}</small>
                  </div>
                  <div className="sell-price-tier tier-fail">
                    <span>失效位</span>
                    <strong style={{ color: 'var(--down)' }}>{plan.failure_price.toFixed(2)}</strong>
                    <small>{plan.sell_ratios[2]}</small>
                  </div>
                </div>
                <div className="sell-conditions">
                  <h4>盘后条件单建议</h4>
                  <div className="condition-list">
                    {plan.condition_orders.map((c, i) => (
                      <span key={i}><ShieldAlert size={13} /> {c}</span>
                    ))}
                  </div>
                </div>
                <div className="sell-buyback">
                  <span>买回：{plan.allow_buyback ? '允许' : '不允许'} · {plan.buyback_condition}</span>
                </div>
              </article>
            ))}
          </div>
        </>
      )}

      <div className="panel sell-guide">
        <h3>卖出纪律总纲</h3>
        <div className="sell-guide-grid">
          <div>
            <h4>仓位类型与处理</h4>
            <table className="ref-table">
              <thead><tr><th>类型</th><th>定义</th><th>默认处理</th></tr></thead>
              <tbody>
                <tr><td>盈利趋势仓</td><td>持仓盈利，仍是主线前排</td><td>可让盈利奔跑，但设移动回撤线</td></tr>
                <tr><td>亏损修复仓</td><td>整体亏损，反弹只是修复</td><td>反弹到压力位优先减仓</td></tr>
                <tr><td>退出型风险仓</td><td>已破止损、龙头地位丧失</td><td>反抽就是退出窗口，不做T、不接回</td></tr>
              </tbody>
            </table>
          </div>
          <div>
            <h4>利润保护规则</h4>
            <ul className="reason-list">
              <li>浮盈 ~5%：超短可先兑现一部分</li>
              <li>浮盈 8%-10%：继续兑现</li>
              <li>浮盈 10%-15%：进入利润保护状态</li>
              <li>从高点回撤 ~5%：触发回撤止损/减仓</li>
              <li>已有明显浮盈次日必须设止盈条件单</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}
