import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../auth/apiFetch'
import EmptyState from './EmptyState'
import { useRecommendationApply } from './useRecommendationApply'
import { useToast } from './useToast'

const PERIOD_LABEL = { '1y': '1 año', '2y': '2 años', '3y': '3 años', '5y': '5 años' }
const PERIOD_ORDER = ['1y', '2y', '3y', '5y']

function fmtPct(fraction) {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return '—'
  const pct = Number(fraction) * 100
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

function fmtComposite(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toFixed(2)
}

function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString('es-ES', { year: 'numeric', month: 'long', day: 'numeric' })
}

function freshnessBadge(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (isNaN(d.getTime())) return null
  const ageDays = Math.floor((Date.now() - d.getTime()) / 86_400_000)
  let cls = 'rec-freshness rec-freshness--ok'
  let label = `Métricas calculadas el ${fmtDate(iso)} (hace ${ageDays} días)`
  if (ageDays > 90) {
    cls = 'rec-freshness rec-freshness--stale'
    label = `Métricas con ${ageDays} días de antigüedad — refresca el cache`
  } else if (ageDays > 30) {
    cls = 'rec-freshness rec-freshness--aging'
    label = `Métricas calculadas hace ${ageDays} días`
  }
  return { cls, label }
}

export default function RecommendedInvestmentPanel({ selectTab }) {
  const [pairs, setPairs] = useState([])
  const [pair, setPair] = useState('')
  const [data, setData] = useState(null)
  const [loadingList, setLoadingList] = useState(false)
  const [loadingRec, setLoadingRec] = useState(false)
  const [error, setError] = useState(null)
  const apply = useRecommendationApply()
  const toast = useToast()

  const fetchPairs = useCallback(async () => {
    setLoadingList(true)
    setError(null)
    try {
      const res = await apiFetch('/api/recommendations')
      if (!res.ok) {
        setError(`No se pudo cargar la lista de pares (HTTP ${res.status})`)
        return
      }
      const list = await res.json()
      setPairs(list)
      if (list.length > 0 && !pair) {
        setPair(list[0])
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingList(false)
    }
  }, [pair])

  useEffect(() => { fetchPairs() }, [fetchPairs])

  useEffect(() => {
    if (!pair) { setData(null); return }
    let cancelled = false
    setLoadingRec(true)
    setError(null)
    apiFetch(`/api/recommendations/${encodeURIComponent(pair)}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then(body => { if (!cancelled) setData(body) })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoadingRec(false) })
    return () => { cancelled = true }
  }, [pair])

  const buildPayload = (rec) => ({
    symbol: pair,
    interval: rec.timeframe,
    strategy: rec.strategy,
    params: { ...(rec.params || {}) },
  })

  const handleApply = (target, rec) => {
    const payload = buildPayload(rec)
    apply.apply(target, payload)
    if (selectTab) selectTab(target)
    const targetLabel = target === 'backtest' ? 'Backtesting' : 'Signals'
    toast.success?.(`Recomendación aplicada en ${targetLabel}`)
  }

  const handleEmptyGoToBacktest = () => {
    if (selectTab) selectTab('backtest')
  }

  const recommendation = data?.recommendation
  const freshness = recommendation && freshnessBadge(recommendation.metrics_computed_at)

  return (
    <div className="panel-section" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div className="card">
        <div className="card-header">
          <span className="card-title">Inversión recomendada</span>
        </div>
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', lineHeight: 1.5, margin: 0 }}>
            Recomendaciones validadas a mano para los pares principales. Cada entrada combina
            estrategia + timeframe + parámetros tuneados sobre datos históricos. Aplica la recomendación
            a Backtest para verificarla, o a Signal Config para empezar a generar señales en vivo.
          </p>

          <div className="form-group" style={{ maxWidth: 320 }}>
            <label className="form-label">Par</label>
            <select
              className="form-control"
              value={pair}
              onChange={e => setPair(e.target.value)}
              disabled={loadingList || pairs.length === 0}
            >
              {pairs.length === 0 && (
                <option value="">{loadingList ? 'Cargando…' : 'Sin pares disponibles'}</option>
              )}
              {pairs.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>

          {error && (
            <div style={{
              padding: '8px 12px', background: 'rgba(239,68,68,0.1)',
              border: '1px solid rgba(239,68,68,0.25)', borderRadius: 'var(--radius-sm)',
              color: 'var(--color-danger)', fontSize: '0.83rem',
            }}>{error}</div>
          )}

          {loadingRec && (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              <span className="spinner" /> Cargando recomendación…
            </div>
          )}

          {!loadingRec && data && !recommendation && (
            <EmptyState
              icon="🔎"
              title={`No hay recomendación validada para ${data.pair}`}
              description={data.message || 'Usa Backtest manual para investigar este par.'}
              action={
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={handleEmptyGoToBacktest}
                  style={{ marginTop: 'var(--space-3)' }}
                >
                  Ir a Backtest
                </button>
              }
            />
          )}

          {!loadingRec && recommendation && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
              <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', alignItems: 'center' }}>
                <span className="rec-chip rec-chip--strategy" title="Estrategia recomendada">
                  {recommendation.strategy}
                </span>
                <span className="rec-chip rec-chip--tf" title="Timeframe">
                  {recommendation.timeframe}
                </span>
                {recommendation.validated_by && (
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                    Validada por <strong style={{ color: 'var(--text-secondary)' }}>{recommendation.validated_by}</strong>
                    {recommendation.validated_at && ` el ${fmtDate(recommendation.validated_at)}`}
                  </span>
                )}
                {freshness && (
                  <span className={freshness.cls} style={{ marginLeft: 'auto' }}>
                    {freshness.label}
                  </span>
                )}
              </div>

              {recommendation.rationale && (
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.88rem', lineHeight: 1.5, margin: 0 }}>
                  {recommendation.rationale}
                </p>
              )}

              <MetricsTable cached={recommendation.metrics_cached} />

              <ParamsTable params={recommendation.params} />

              <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
                <button
                  type="button"
                  className="btn btn-primary btn-lg"
                  onClick={() => handleApply('backtest', recommendation)}
                >
                  Aplicar a Backtest
                </button>
                <button
                  type="button"
                  className="btn btn-secondary btn-lg"
                  onClick={() => handleApply('signals', recommendation)}
                >
                  Aplicar a Signal Config
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function MetricsTable({ cached }) {
  const rows = PERIOD_ORDER
    .map(p => ({ period: p, label: PERIOD_LABEL[p], cell: cached?.[p] }))
    .filter(r => r.cell)

  if (rows.length === 0) {
    return (
      <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
        Métricas no calculadas todavía. Ejecuta el script de refresh para generarlas.
      </div>
    )
  }

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th style={{ textAlign: 'left' }}>Periodo</th>
            <th style={{ textAlign: 'right' }}>Profit</th>
            <th style={{ textAlign: 'right' }}>Drawdown</th>
            <th style={{ textAlign: 'right' }}>Composite</th>
            <th style={{ textAlign: 'right' }}># trades</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ period, label, cell }) => {
            const profitClass = cell.profit >= 0 ? 'positive' : 'negative'
            return (
              <tr key={period}>
                <td>{label}</td>
                <td className={profitClass} style={{ textAlign: 'right' }}>{fmtPct(cell.profit)}</td>
                <td className="negative" style={{ textAlign: 'right' }}>{fmtPct(cell.dd)}</td>
                <td style={{ textAlign: 'right' }}>{fmtComposite(cell.composite)}</td>
                <td style={{ textAlign: 'right' }}>{cell.n_trades ?? '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ParamsTable({ params }) {
  const entries = Object.entries(params || {})
  if (entries.length === 0) return null
  return (
    <details className="rec-params" open>
      <summary style={{ cursor: 'pointer', color: 'var(--text-secondary)', fontSize: '0.85rem', marginBottom: 'var(--space-2)' }}>
        Parámetros tuneados ({entries.length})
      </summary>
      <div className="data-table-wrap">
        <table className="data-table">
          <tbody>
            {entries.map(([k, v]) => (
              <tr key={k}>
                <td style={{ fontFamily: 'var(--font-mono, monospace)' }}>{k}</td>
                <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono, monospace)' }}>{String(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
