import { useContext } from 'react'
import { ToastContext } from './ToastContext'

const NOOP = () => {}
const FALLBACK = { show: NOOP, dismiss: NOOP, error: NOOP, warning: NOOP, success: NOOP, info: NOOP }

export function useToast() {
  const ctx = useContext(ToastContext)
  return ctx ?? FALLBACK
}
