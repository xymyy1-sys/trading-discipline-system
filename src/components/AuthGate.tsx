import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { LockKeyhole } from 'lucide-react'
import { getSession, login } from '../auth'

export default function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'checking' | 'anonymous' | 'authenticated'>('checking')
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    getSession().then(ok => setState(ok ? 'authenticated' : 'anonymous')).catch(() => setState('anonymous'))
    const requireAuth = () => setState('anonymous')
    window.addEventListener('auth-required', requireAuth)
    return () => window.removeEventListener('auth-required', requireAuth)
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setMessage('')
    login(username, password, otp)
      .then(() => {
        setPassword('')
        setOtp('')
        setState('authenticated')
      })
      .catch(error => setMessage(error instanceof Error ? error.message : '登录失败'))
      .finally(() => setSubmitting(false))
  }

  if (state === 'checking') return <div className="auth-screen"><p>正在验证访问权限…</p></div>
  if (state === 'authenticated') return <>{children}</>

  return (
    <main className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <LockKeyhole size={30} />
        <div>
          <span className="eyebrow">私人交易决策台</span>
          <h1>知行交易驾驶舱</h1>
          <p>持仓和交易记录属于敏感数据，请登录后继续。</p>
        </div>
        <label>用户名<input autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} /></label>
        <label>密码<input autoComplete="current-password" type="password" value={password} onChange={event => setPassword(event.target.value)} /></label>
        <label>动态验证码（未启用可留空）<input inputMode="numeric" autoComplete="one-time-code" value={otp} onChange={event => setOtp(event.target.value.replace(/\D/g, '').slice(0, 6))} /></label>
        {message && <p className="auth-error">{message}</p>}
        <button type="submit" disabled={submitting}>{submitting ? '登录中…' : '登录'}</button>
      </form>
    </main>
  )
}
