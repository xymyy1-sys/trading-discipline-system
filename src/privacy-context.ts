import { createContext, useContext } from 'react'

export const PrivacyModeContext = createContext(false)

export function usePrivacyMode() {
  return useContext(PrivacyModeContext)
}
