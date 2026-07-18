import { useEffect, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  Activity,
  BookOpenCheck,
  Flame,
  Target,
  WalletCards,
  Menu,
  X,
  Eye,
  EyeOff,
  FlaskConical,
} from 'lucide-react'
import { API_BASE } from './api'
import { PrivacyModeProvider } from './privacy'
import './App.css'
import { TodayDecisionSummary, WorkspacePage } from './components/workspaces/WorkspacePages'

import Dashboard from './components/Dashboard'
import FlowDesk from './components/FlowDesk'
import IntelDesk from './components/IntelDesk'
import LimitUpLadder from './components/LimitUpLadder'
import Positions from './components/Positions'
import DecisionCard from './components/DecisionCard'
import NextDayPlans from './components/NextDayPlans'
import TradeLog from './components/TradeLog'
import MonthlyReview from './components/MonthlyReview'
import ReviewCalibration from './components/ReviewCalibration'
import CandidatePool from './components/CandidatePool'
import StrategyTemplates from './components/StrategyTemplates'
import HistoricalReplay from './components/HistoricalReplay'
import {
  SimulationAccountOverview,
  SimulationEvidenceLedger,
  SimulationOrdersAndPositions,
  SimulationPerformanceDesk,
  SimulationStrategyLab,
} from './components/SimulationDesk'

const PositionOverview = () => <Positions mode="overview" />
const PositionDiscipline = () => <Positions mode="discipline" />
const HoldingExpectationCockpit = () => <DecisionCard mode="holding" />
const LimitPlans = () => <NextDayPlans mode="limit" />
const HoldingPlans = () => <NextDayPlans mode="holding" />

const navItems = [
  ['今日决策', Activity, '/今日决策'],
  ['选股中心', Target, '/选股中心'],
  ['打板预期', Flame, '/打板预期'],
  ['持仓执行', WalletCards, '/持仓执行'],
  ['模拟盘', FlaskConical, '/模拟盘'],
  ['复盘校准', BookOpenCheck, '/复盘校准'],
] as const

const workspacePaths = new Set<string>(navItems.map(([, , path]) => path))

const oldPathToWorkspace: Record<string, string> = {
  '/题材雷达': '/选股中心',
  '/资金流证据': '/选股中心',
  '/涨停天梯': '/打板预期',
  '/信息差': '/选股中心',
  '/市场环境': '/今日决策',
  '/持仓快照': '/持仓执行',
  '/个股决策卡': '/选股中心',
  '/次日计划卡': '/打板预期',
  '/交易日志': '/复盘校准',
  '/买入检查': '/选股中心',
  '/集中进攻': '/选股中心',
  '/卖出执行卡': '/持仓执行',
  '/月度复盘': '/复盘校准',
}

const routeLabels: Record<string, string> = Object.fromEntries([
  ...navItems.map(([label, _, path]) => [path, label]),
])

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const [backendUp, setBackendUp] = useState(false)
  const [apiStatus, setApiStatus] = useState('检测中')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [privacyMode, setPrivacyMode] = useState(() => localStorage.getItem('privacy-mask') === 'on')

  const activePath = location.pathname === '/' ? '/今日决策' : decodeURIComponent(location.pathname)
  const activeWorkspacePath = oldPathToWorkspace[activePath] ?? activePath
  const activeLabel = routeLabels[activePath] ?? routeLabels[activeWorkspacePath] ?? '今日决策'
  const normalizedWorkspacePath = workspacePaths.has(activeWorkspacePath) ? activeWorkspacePath : '/今日决策'
  const [visitedWorkspaces, setVisitedWorkspaces] = useState<Set<string>>(() => new Set([normalizedWorkspacePath]))

  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then(r => r.json())
      .then(() => {
        setBackendUp(true)
        setApiStatus('已连接')
      })
      .catch(() => {
        setBackendUp(false)
        setApiStatus('未启动')
      })
  }, [])

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail
      const item = navItems.find(([label]) => label === detail)
      if (item) {
        navigate(item[2])
        setSidebarOpen(false)
      }
    }
    window.addEventListener('nav', handler)
    return () => window.removeEventListener('nav', handler)
  }, [navigate])

  useEffect(() => {
    const destination = oldPathToWorkspace[activePath]
    if (destination) {
      navigate(destination, { replace: true })
      return
    }
    if (!workspacePaths.has(activePath)) {
      navigate('/今日决策', { replace: true })
      return
    }
    setVisitedWorkspaces(previous => previous.has(activePath) ? previous : new Set(previous).add(activePath))
  }, [activePath, navigate])

  return (
    <PrivacyModeProvider value={privacyMode}>
    <main className={`terminal-shell ${privacyMode ? 'privacy-mode' : ''}`}>
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="brand" style={{ display: 'flex', width: '100%', alignItems: 'center' }}>
            <BrandIcon />
          <div style={{ flex: 1 }}>
            <strong>知行交易驾驶舱</strong>
            <span>A股预期与执行决策台</span>
          </div>
          <button className="hamburger-btn" style={{ marginLeft: 'auto' }} onClick={() => setSidebarOpen(false)}>
            <X size={18} />
          </button>
        </div>
        <nav aria-label="主导航">
          {navItems.map(([label, Icon, path]) => (
            <button
              className={activeWorkspacePath === path ? 'active' : ''}
              key={label}
              onClick={() => {
                navigate(path)
                setSidebarOpen(false)
              }}
              type="button"
              title={label}
            >
              <Icon size={17} strokeWidth={1.8} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="discipline-strip">
          <span>纪律原则</span>
          <strong>计划先于操作</strong>
          <small>以当前计划、真实证据和风险闸门为准</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div className="topbar-title-row">
            <button className="hamburger-btn" onClick={() => setSidebarOpen(true)}>
              <Menu size={18} />
            </button>
            <div>
              <span className="eyebrow">知行交易驾驶舱</span>
              <h1>{activeLabel}</h1>
            </div>
          </div>
          <div className="status-cluster">
            {normalizedWorkspacePath === '/持仓执行' && <button
              className={`privacy-toggle ${privacyMode ? 'active' : ''}`}
              type="button"
              onClick={() => setPrivacyMode(current => {
                const next = !current
                localStorage.setItem('privacy-mask', next ? 'on' : 'off')
                return next
              })}
              title={privacyMode ? '恢复显示敏感数据' : '隐藏全部资金和持仓金额'}
            >{privacyMode ? <Eye size={16}/> : <EyeOff size={16}/>} {privacyMode ? '恢复资金数据' : '隐藏资金数据'}</button>}
            <Metric
              label="后端"
              value={apiStatus}
              tone={backendUp ? 'neutral' : 'muted'}
            />
          </div>
        </header>

        {(visitedWorkspaces.has('/今日决策') || normalizedWorkspacePath === '/今日决策') && <div hidden={normalizedWorkspacePath !== '/今日决策'}><TodayDecisionWorkspace /></div>}
        {(visitedWorkspaces.has('/选股中心') || normalizedWorkspacePath === '/选股中心') && <div hidden={normalizedWorkspacePath !== '/选股中心'}><StockSelectionWorkspace /></div>}
        {(visitedWorkspaces.has('/打板预期') || normalizedWorkspacePath === '/打板预期') && <div hidden={normalizedWorkspacePath !== '/打板预期'}><LimitExpectationWorkspace /></div>}
        {(visitedWorkspaces.has('/持仓执行') || normalizedWorkspacePath === '/持仓执行') && <div hidden={normalizedWorkspacePath !== '/持仓执行'}><PositionExecutionWorkspace /></div>}
        {(visitedWorkspaces.has('/模拟盘') || normalizedWorkspacePath === '/模拟盘') && <div hidden={normalizedWorkspacePath !== '/模拟盘'}><SimulationWorkspace /></div>}
        {(visitedWorkspaces.has('/复盘校准') || normalizedWorkspacePath === '/复盘校准') && <div hidden={normalizedWorkspacePath !== '/复盘校准'}><ReviewCalibrationWorkspace /></div>}
      </section>
    </main>
    </PrivacyModeProvider>
  )
}

function TodayDecisionWorkspace() {
  return (
    <WorkspacePage
      title="今日决策中心"
      subtitle="盘中决策与风险处置"
      objective="先回答今天该做什么、什么不能做、哪些持仓必须处理，再进入具体模块。"
      allowed={['处理持仓风险', '按市场档位选择策略', '只执行有证据的计划']}
      forbidden={['风险未解除前扩大仓位', '无计划追高', '亏损仓补仓摊低成本']}
      modules={[
        { key: 'cockpit', label: '盘中驾驶舱', description: '市场状态、持仓预期、量价证据、事件轨迹和操作建议', Component: TodayDecisionSummary },
      ]}
    />
  )
}

function StockSelectionWorkspace() {
  return (
    <WorkspacePage
      title="选股中心"
      subtitle="盘前发现与观察池管理"
      objective="从主线、题材强度、订单流方向与涨停质量中发现新标的，形成少而精的观察池。"
      allowed={['主线前排', '订单流与价格确认', '量价健康', '预期差不为负']}
      forbidden={['后排跟风', '数据质量不合格', '高位巨量滞涨', '板块订单流方向持续转弱']}
      modules={[
        { key: 'candidates', label: '自动观察池', description: '主线、涨停质量、订单流方向与风险综合推荐', Component: CandidatePool },
        { key: 'intel', label: '行业要闻', description: '东方财富、央视及行业资讯原文与市场验证', Component: IntelDesk },
        { key: 'radar', label: '主线题材', description: '主线强度、共振方向、核心股', Component: Dashboard },
        { key: 'flow', label: '订单流证据', description: '板块订单流方向拐点、排名与强弱', Component: FlowDesk },
        { key: 'ladder', label: '涨停质量', description: '查看打板氛围摘要并进入唯一的完整天梯', Component: LimitUpQualityShortcut },
        { key: 'card', label: '个股研判', description: '预期、实际、事件与失效条件', Component: DecisionCard },
      ]}
    />
  )
}

function LimitExpectationWorkspace() {
  return (
    <WorkspacePage
      title="打板预期中心"
      subtitle="盘前预期与条件单预案"
      objective="围绕涨停质量、次日合理预期、竞价验证、开盘确认和冲板/炸板风险组织打板决策。"
      allowed={['强预期且量价确认', '前排助攻充分', '封板质量可解释']}
      forbidden={['竞价严重低于预期仍买入', '炸板后无修复继续幻想', '弱预期不降仓']}
      modules={[
        { key: 'ladder', label: '涨停天梯', description: '从真实涨停梯队直接生成打板预案', Component: LimitUpLadder },
        { key: 'plans', label: '打板预案', description: '仅管理涨停股、涨停持仓及其竞价与打板剧本', Component: LimitPlans },
      ]}
    />
  )
}

function PositionExecutionWorkspace() {
  return (
    <WorkspacePage
      title="持仓执行中心"
      subtitle="盘中持仓与风险执行"
      objective="把持仓事实、执行纪律和次日计划分开管理，避免在同一张表重复堆叠。"
      allowed={['继续持有有证据', '按状态机减仓', '只在逻辑成立时做T']}
      forbidden={['预期证伪后补仓', '用做T掩盖止损', '利润保护失效仍等待回本']}
      modules={[
        { key: 'positions', label: '持仓总览', description: '数量、成本、现价、盈亏和仓位事实', Component: PositionOverview },
        { key: 'expectation', label: '持仓预期驾驶舱', description: '次日基准、竞价验证、分钟量价、事件轨迹与预期差', Component: HoldingExpectationCockpit },
        { key: 'discipline', label: '执行纪律', description: '状态机、时间止损、利润保护和做T约束', Component: PositionDiscipline },
        { key: 'next-plan', label: '持仓次日计划', description: '仅针对普通持仓的下一交易日操作计划', Component: HoldingPlans },
      ]}
    />
  )
}

function SimulationWorkspace() {
  return (
    <WorkspacePage
      title="模拟交易实验室"
      subtitle="独立模拟账本与策略验证"
      objective="在不连接券商、不改变真实持仓的前提下，用带时点的真实行情证据验证打板、预期量价和持仓执行策略。"
      allowed={['仅提交模拟委托', '记录未成交原因', '按市场环境与预期差分层验证']}
      forbidden={['暗示真实下单', '用模拟成交替代真实执行', '缺少数据时伪造收益或胜率']}
      modules={[
        { key: 'account', label: '账户概览', description: '独立模拟资金、权益、持仓市值与盈亏', Component: SimulationAccountOverview },
        { key: 'orders', label: '模拟委托/持仓', description: '模拟委托、撮合状态、未成交原因和模拟持仓', Component: SimulationOrdersAndPositions },
        { key: 'strategies', label: '策略实验', description: '打板、预期量价与持仓执行三类实验', Component: SimulationStrategyLab },
        { key: 'evidence', label: '成交与决策证据', description: '成交、未成交、决策依据与行情时点回放', Component: SimulationEvidenceLedger },
        { key: 'performance', label: '绩效统计', description: '胜率、盈亏比、回撤及多维分层统计', Component: SimulationPerformanceDesk },
      ]}
    />
  )
}

function ReviewCalibrationWorkspace() {
  return (
    <WorkspacePage
      title="复盘校准中心"
      subtitle="盘后复盘与规则校准"
      objective="先沉淀计划、执行、偏差和纪律记录；P2 再接入模型有效性统计和参数自动校准。"
      allowed={['记录真实执行', '复盘计划偏差', '统计纪律问题']}
      forbidden={['只复盘盈亏', '忽略未执行提醒', '用主观判断覆盖证据']}
      modules={[
        { key: 'replay', label: '历史回放', description: '单股盘中事件与操作建议时间线', Component: HistoricalReplay },
        { key: 'strategies', label: '交易规则草稿', description: '仅保存规则草稿，尚未接入实时决策引擎', Component: StrategyTemplates },
        { key: 'calibration', label: '执行校准', description: '计划偏差、执行反馈、纪律缺口', Component: ReviewCalibration },
        { key: 'trades', label: '交易日志', description: '交易记录、深度复盘、执行原因', Component: TradeLog },
        { key: 'outcomes', label: '建议结果账本', description: '建议产生后的真实前向走势与数据完整度', Component: MonthlyReview },
      ]}
    />
  )
}

function BrandIcon() {
  return <span className="brand-mark brand-mark-new" aria-hidden="true">
    <svg viewBox="0 0 48 48">
      <path className="brand-shield" d="M24 4 40 10v11c0 10.4-6.4 18.4-16 23C14.4 39.4 8 31.4 8 21V10Z" />
      <path className="brand-path" d="m13 30 7-8 5 4 10-12" />
      <path className="brand-bars" d="M15 27v6M24 23v10M33 16v17" />
    </svg>
  </span>
}

function LimitUpQualityShortcut() {
  return (
    <section className="panel ladder-shortcut-panel">
      <div>
        <span className="eyebrow">唯一数据入口</span>
        <h2>完整涨停天梯已归入“打板预期”</h2>
        <p>选股中心只把涨停质量作为观察池的一项证据，不再重复加载整套天梯。连板高度、题材梯队、封板质量和打板预案统一在打板预期查看。</p>
      </div>
      <button type="button" onClick={() => window.dispatchEvent(new CustomEvent('nav', { detail: '打板预期' }))}>进入打板预期</button>
    </section>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone: 'neutral' | 'muted' }) {
  return (
    <div className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
