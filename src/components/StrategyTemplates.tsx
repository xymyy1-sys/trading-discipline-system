import { useEffect, useState } from 'react'
import { Save } from 'lucide-react'
import { API_BASE } from '../api'

type Template = {
  id: number; code: string; name: string; category: string; market_environment: string[]; prerequisites: string[];
  premarket_expectation: string[]; auction_conditions: string[]; volume_price_conditions: string[];
  buy_confirmation: string[]; position_limit: number; structure_stop: string[]; invalid_conditions: string[];
  holding_management: string[]; forbidden_actions: string[]; enabled: boolean; version: number;
}
const listFields = ['market_environment', 'prerequisites', 'premarket_expectation', 'auction_conditions', 'volume_price_conditions', 'buy_confirmation', 'structure_stop', 'invalid_conditions', 'holding_management', 'forbidden_actions'] as const

export default function StrategyTemplates() {
  const [items, setItems] = useState<Template[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  useEffect(() => { fetch(`${API_BASE}/api/strategies/templates`).then(r => r.json()).then((rows: Template[]) => { setItems(rows); setSelected(rows[0]?.id ?? null) }) }, [])
  const current = items.find(item => item.id === selected)
  const patch = (value: Partial<Template>) => setItems(rows => rows.map(row => row.id === selected ? { ...row, ...value } : row))
  const save = () => {
    if (!current) return
    fetch(`${API_BASE}/api/strategies/templates/${current.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(current) })
      .then(async response => { if (!response.ok) throw new Error(await response.text()); return response.json() })
      .then((saved: Template) => { setItems(rows => rows.map(row => row.id === saved.id ? saved : row)); setMessage(`${saved.name} 草稿已保存为 v${saved.version}`) })
      .catch(() => setMessage('交易规则草稿保存失败'))
  }
  return <section className="strategy-template-page">
    <header className="pos-header"><div><h2>交易规则草稿</h2><p>草稿模板，当前尚未接入实时决策引擎；这里只保存和版本化规则，修改或启用不会改变系统操作建议。</p></div>{message && <span className="refresh-note">{message}</span>}</header>
    <div className="strategy-template-layout"><nav>{items.map(item => <button className={selected === item.id ? 'active' : ''} onClick={() => setSelected(item.id)} key={item.id}><b>{item.name}</b><span>{item.category} · v{item.version}</span></button>)}</nav>
    {current && <article className="panel strategy-editor">
      <div className="strategy-editor-head"><div><strong>{current.name}</strong><small>{current.code}</small></div><label>仓位上限<input type="number" min="0" max="1" step="0.05" value={current.position_limit} onChange={e => patch({ position_limit: Number(e.target.value) })} /></label></div>
      {listFields.map(field => <label key={field}>{field.replaceAll('_', ' ')}<textarea value={current[field].join('\n')} onChange={e => patch({ [field]: e.target.value.split('\n').map(v => v.trim()).filter(Boolean) } as Partial<Template>)} /></label>)}
      <label className="strategy-enabled"><input type="checkbox" checked={current.enabled} onChange={e => patch({ enabled: e.target.checked })} />草稿标记为启用（仅用于整理）</label>
      <button className="grade-btn" onClick={save}><Save size={15} />保存草稿新版本</button>
    </article>}</div>
  </section>
}
