import { useState, useCallback, useMemo } from 'react'
import { RecommendationApplyContext } from './RecommendationApplyContext'

/**
 * Holds a one-shot "pending" payload that the Recommended Investments panel
 * sets when the user clicks "Aplicar a Backtest" / "Aplicar a Signal Config".
 * The destination panel reads it on mount and immediately consumes it so
 * navigating back-and-forth doesn't keep re-hydrating stale presets.
 *
 *   pending = { target: 'backtest' | 'signals', payload: { symbol, interval, strategy, params } }
 */
export function RecommendationApplyProvider({ children }) {
  const [pending, setPending] = useState(null)

  const apply = useCallback((target, payload) => {
    setPending({ target, payload })
  }, [])

  const consume = useCallback((target) => {
    let consumed = null
    setPending(prev => {
      if (prev && prev.target === target) {
        consumed = prev.payload
        return null
      }
      return prev
    })
    return consumed
  }, [])

  const value = useMemo(() => ({ pending, apply, consume }), [pending, apply, consume])

  return (
    <RecommendationApplyContext.Provider value={value}>
      {children}
    </RecommendationApplyContext.Provider>
  )
}
