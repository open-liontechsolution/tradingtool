import { useState, useCallback, useRef, useEffect } from 'react'
import { ToastContext } from './ToastContext'

const ICONS = { error: '⚠', warning: '⚠', success: '✓', info: 'ℹ' }
const DEFAULT_DURATION_MS = 4000

export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const idRef = useRef(0)
  const timersRef = useRef(new Map())

  const dismiss = useCallback((id) => {
    setItems(prev => prev.filter(t => t.id !== id))
    const timer = timersRef.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timersRef.current.delete(id)
    }
  }, [])

  const show = useCallback((message, variant = 'info', durationMs = DEFAULT_DURATION_MS) => {
    idRef.current += 1
    const id = idRef.current
    setItems(prev => [...prev, { id, message, variant }])
    if (durationMs > 0) {
      const timer = setTimeout(() => dismiss(id), durationMs)
      timersRef.current.set(id, timer)
    }
    return id
  }, [dismiss])

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      for (const t of timers.values()) clearTimeout(t)
      timers.clear()
    }
  }, [])

  const api = {
    show,
    dismiss,
    error: (msg, ms) => show(msg, 'error', ms),
    warning: (msg, ms) => show(msg, 'warning', ms),
    success: (msg, ms) => show(msg, 'success', ms),
    info: (msg, ms) => show(msg, 'info', ms),
  }

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-container" aria-live="polite" aria-atomic="false">
        {items.map(t => (
          <div
            key={t.id}
            className={`toast-item toast-item--${t.variant}`}
            role={t.variant === 'error' ? 'alert' : 'status'}
          >
            <span className="toast-item__icon" aria-hidden="true">{ICONS[t.variant] ?? ICONS.info}</span>
            <span className="toast-item__body">{t.message}</span>
            <button
              type="button"
              className="toast-item__close"
              aria-label="Dismiss notification"
              onClick={() => dismiss(t.id)}
            >×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
