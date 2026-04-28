import { useState } from 'react'
import { apiFetch } from '../../auth/apiFetch'
import { fmtNum, fmtMoney } from './helpers'

export function ComparisonView() {
  const [simId, setSimId] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleFetch = async () => {
    if (!simId) return
    setLoading(true)
    try {
      const res = await apiFetch(`/api/comparison/${simId}`)
      if (res.ok) {
        setData(await res.json())
      }
    } catch { /* ignore */ }
    setLoading(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'flex-end', marginBottom: 'var(--space-4)' }}>
        <div className="form-group" style={{ marginBottom: 0 }}>
          <label className="form-label">SimTrade ID</label>
          <input type="number" className="form-control" value={simId} onChange={e => setSimId(e.target.value)}
            style={{ width: 120 }} />
        </div>
        <button className="btn btn-primary btn-sm" onClick={handleFetch} disabled={loading || !simId}>
          Compare
        </button>
      </div>

      {data && (
        <div>
          <div className="section-title">SimTrade #{data.sim_trade?.id}</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
            <div className="metric-card">
              <span className="metric-label">Side</span>
              <span className="metric-value">{data.sim_trade?.side?.toUpperCase()}</span>
            </div>
            <div className="metric-card">
              <span className="metric-label">Entry</span>
              <span className="metric-value">{fmtNum(data.sim_trade?.entry_price, 4)}</span>
            </div>
            <div className="metric-card">
              <span className="metric-label">Exit</span>
              <span className="metric-value">{data.sim_trade?.exit_price ? fmtNum(data.sim_trade.exit_price, 4) : '—'}</span>
            </div>
            <div className="metric-card">
              <span className="metric-label">PnL</span>
              <span className={`metric-value ${(data.sim_trade?.pnl ?? 0) >= 0 ? 'positive' : 'negative'}`}>
                {data.sim_trade?.pnl != null ? fmtMoney(data.sim_trade.pnl) : '—'}
              </span>
            </div>
            <div className="metric-card">
              <span className="metric-label">Reason</span>
              <span className="metric-value">{data.sim_trade?.exit_reason || '—'}</span>
            </div>
          </div>

          {data.comparisons?.length > 0 ? data.comparisons.map((comp, i) => (
            <div key={i} style={{ marginBottom: 'var(--space-4)' }}>
              <div className="section-title">vs Real Trade #{comp.real_trade?.id}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 'var(--space-2)' }}>
                <div className="metric-card">
                  <span className="metric-label">Entry Slippage</span>
                  <span className="metric-value">{comp.entry_slippage != null ? fmtNum(comp.entry_slippage, 6) : '—'}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Exit Slippage</span>
                  <span className="metric-value">{comp.exit_slippage != null ? fmtNum(comp.exit_slippage, 6) : '—'}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">PnL Difference</span>
                  <span className={`metric-value ${(comp.pnl_diff ?? 0) >= 0 ? 'positive' : 'negative'}`}>
                    {comp.pnl_diff != null ? fmtMoney(comp.pnl_diff) : '—'}
                  </span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Real PnL</span>
                  <span className="metric-value">{comp.real_trade?.pnl != null ? fmtMoney(comp.real_trade.pnl) : '—'}</span>
                </div>
              </div>
            </div>
          )) : (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No real trades linked to this SimTrade.</div>
          )}
        </div>
      )}
    </div>
  )
}
