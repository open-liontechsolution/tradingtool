import { useContext } from 'react'
import { RecommendationApplyContext } from './RecommendationApplyContext'

const NOOP = () => null
const FALLBACK = { pending: null, apply: NOOP, consume: NOOP }

export function useRecommendationApply() {
  const ctx = useContext(RecommendationApplyContext)
  return ctx ?? FALLBACK
}
