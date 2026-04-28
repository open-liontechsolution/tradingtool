import { useState, useEffect } from 'react'
import { apiFetch } from '../../auth/apiFetch'
import { PAIRS, INTERVALS } from './helpers'
import { CapitalConfig } from './CapitalConfig'

export function ConfigForm({ strategies, onCreated }) {
  const [symbol, setSymbol] = useState('BTCUSDT')
  const [interval, setInterval] = useState('1d')
  const [selectedStrat, setSelectedStrat] = useState('')
  const [paramValues, setParamValues] = useState({})
  const [costBps, setCostBps] = useState(10)
  const [maintenanceMarginPct, setMaintenanceMarginPct] = useState(0.005)
  const [portfolio, setPortfolio] = useState(10000)
  const [leverage, setLeverage] = useState(1)
  const [investedAmount, setInvestedAmount] = useState(10000)
  const [capitalMode, setCapitalMode] = useState('leverage')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (strategies.length > 0 && !selectedStrat) {
      const first = strategies[0]
      setSelectedStrat(first.name)
      const defaults = {}
      for (const p of first.parameters ?? []) defaults[p.name] = p.default
      setParamValues(defaults)
    }
  }, [strategies, selectedStrat])

  const handleStrategyChange = e => {
    const name = e.target.value
    setSelectedStrat(name)
    const strat = strategies.find(s => s.name === name)
    if (strat) {
      const defaults = {}
      for (const p of strat.parameters ?? []) defaults[p.name] = p.default
      setParamValues(defaults)
    }
  }

  const currentStrat = strategies.find(s => s.name === selectedStrat)

  const handleCreate = async () => {
    setLoading(true)
    setError(null)
    try {
      const body = {
        symbol, interval, strategy: selectedStrat,
        params: paramValues,
        cost_bps: costBps,
        maintenance_margin_pct: maintenanceMarginPct,
        initial_portfolio: portfolio,
        ...(capitalMode === 'leverage'
          ? { leverage, invested_amount: null }
          : { invested_amount: investedAmount, leverage: null }),
      }
      const res = await apiFetch('/api/signals/configs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setError(err.detail ?? `HTTP ${res.status}`)
        return
      }
      onCreated()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 'var(--space-3)' }}>
        <div className="form-group">
          <label className="form-label">Pair</label>
          <select className="form-control" value={symbol} onChange={e => setSymbol(e.target.value)} disabled={loading}>
            {PAIRS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Interval</label>
          <select className="form-control" value={interval} onChange={e => setInterval(e.target.value)} disabled={loading}>
            {INTERVALS.map(iv => <option key={iv.value} value={iv.value}>{iv.label}</option>)}
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Strategy</label>
          {strategies.length === 0 ? (
            <div style={{ padding: '8px 12px', background: 'var(--bg-input)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', color: 'var(--text-muted)', fontSize: '0.83rem' }}>
              Loading strategies…
            </div>
          ) : (
            <select className="form-control" value={selectedStrat} onChange={handleStrategyChange} disabled={loading}>
              {strategies.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          )}
        </div>
      </div>

      {currentStrat && currentStrat.parameters && currentStrat.parameters.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 'var(--space-3)' }}>
          {currentStrat.parameters.map(p => {
            const val = paramValues[p.name] !== undefined ? paramValues[p.name] : p.default
            if (p.type === 'bool') {
              return (
                <div key={p.name} className="form-group">
                  <label className="form-label">{p.name}</label>
                  <div className="toggle-group">
                    <button type="button" className={`toggle-option${val === true || val === 'true' ? ' active' : ''}`}
                      onClick={() => !loading && setParamValues(prev => ({ ...prev, [p.name]: true }))}>On</button>
                    <button type="button" className={`toggle-option${val === false || val === 'false' ? ' active' : ''}`}
                      onClick={() => !loading && setParamValues(prev => ({ ...prev, [p.name]: false }))}>Off</button>
                  </div>
                </div>
              )
            }
            if (p.type === 'str') {
              return (
                <div key={p.name} className="form-group">
                  <label className="form-label">{p.name}</label>
                  <select className="form-control" value={val}
                    onChange={e => setParamValues(prev => ({ ...prev, [p.name]: e.target.value }))} disabled={loading}>
                    {['open_next', 'close_current'].map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
              )
            }
            return (
              <div key={p.name} className="form-group">
                <label className="form-label">{p.name}</label>
                <input type="number" className="form-control" value={val}
                  min={p.min ?? undefined} max={p.max ?? undefined}
                  step={p.type === 'float' ? 0.001 : 1}
                  onChange={e => {
                    const parsed = p.type === 'float' ? parseFloat(e.target.value) : parseInt(e.target.value, 10)
                    setParamValues(prev => ({ ...prev, [p.name]: isNaN(parsed) ? e.target.value : parsed }))
                  }} disabled={loading} />
              </div>
            )
          })}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
        <div>
          <div className="section-title">Risk Parameters</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-3)' }}>
            <div className="form-group">
              <label className="form-label">Cost (bps)</label>
              <input type="number" className="form-control" value={costBps} min={0} step={1}
                onChange={e => setCostBps(parseFloat(e.target.value) || 0)} disabled={loading} />
            </div>
            <div className="form-group">
              <label className="form-label" title="Maintenance margin used for the liquidation-price formula. Binance baseline ≈ 0.005 (0.5%) for low notional.">Maint. margin %</label>
              <input type="number" className="form-control" value={maintenanceMarginPct} min={0} step={0.001}
                onChange={e => setMaintenanceMarginPct(parseFloat(e.target.value) || 0)} disabled={loading} />
            </div>
          </div>
        </div>
        <div>
          <div className="section-title">Capital</div>
          <CapitalConfig
            portfolio={portfolio} setPortfolio={setPortfolio}
            leverage={leverage} setLeverage={setLeverage}
            investedAmount={investedAmount} setInvestedAmount={setInvestedAmount}
            mode={capitalMode} setMode={setCapitalMode}
            disabled={loading}
          />
        </div>
      </div>

      {error && (
        <div style={{
          padding: '8px 12px', background: 'rgba(239,68,68,0.1)',
          border: '1px solid rgba(239,68,68,0.25)', borderRadius: 'var(--radius-sm)',
          color: 'var(--color-danger)', fontSize: '0.83rem',
        }}>{error}</div>
      )}

      <div>
        <button className="btn btn-primary" onClick={handleCreate} disabled={loading || !selectedStrat}>
          {loading ? 'Creating…' : 'Create Signal Config'}
        </button>
      </div>
    </div>
  )
}
