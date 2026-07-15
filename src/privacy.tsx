import type { ReactNode } from 'react'
import { PrivacyModeContext, usePrivacyMode } from './privacy-context'
import { redactSensitiveEvidence } from './privacy-redaction'

export function PrivacyModeProvider({ value, children }: { value: boolean; children: ReactNode }) {
  return <PrivacyModeContext.Provider value={value}>{children}</PrivacyModeContext.Provider>
}

export function SensitiveValue({ children, className = '' }: { children: ReactNode; className?: string }) {
  const privacyMode = usePrivacyMode()
  return (
    <span
      className={`private-value ${className}`.trim()}
      data-sensitive="true"
      aria-label={privacyMode ? '敏感数据已隐藏' : undefined}
    >
      {privacyMode ? '******' : children}
    </span>
  )
}

export function SensitiveEvidenceText({ value, className = '' }: { value: string; className?: string }) {
  const privacyMode = usePrivacyMode()
  const visibleValue = privacyMode ? redactSensitiveEvidence(value) : value
  const redacted = visibleValue !== value
  return (
    <span
      className={className}
      data-sensitive-evidence={redacted ? 'redacted' : undefined}
      aria-label={redacted ? '敏感证据已脱敏' : undefined}
    >
      {visibleValue}
    </span>
  )
}
