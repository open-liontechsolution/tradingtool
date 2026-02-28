import { useState, useEffect, useCallback } from 'react'

const PAIRS = [
  'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
  'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'MATICUSDT',
]

const INTERVALS = [
  { value: '1h', label: '1 Hour' },
  { value: '4h', label: '4 Hours' },
  { value: '1d', label: '1 Day' },
  { value: '1w', label: '1 Week' },
  { value: '1M', label: '1 Month' },
]

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(digits)
}

function fmtMoney(v) {
  if (v === null || v === undefined) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(Number(v))
}

function fmtTime(ms) {
  if (!ms) return '—'
  return new Date(Number(ms)).toLocaleString()
}

function fmtIso(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

function fmtConfigParams(raw) {
  if (!raw) return ''
  try {
    const obj = typeof raw === 'string' ? JSON.parse(raw) : raw
    return Object.entries(obj).map(([k, v]) => `${k}: ${v}`).join('\n')
  } catch { return '' }
}

function ConfigBadge({ configId, strategy, params }) {
  const lines = fmtConfigParams(params)
  return (
    <span style={{ position: 'relative', display: 'inline-block' }} className="config-badge-wrap">
      <span
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4,
          padding: '2px 8px', borderRadius: 'var(--radius-sm)',
          background: 'var(--bg-elevated)', border: '1px solid var(--border-default)',
          fontSize: '0.75rem', cursor: lines ? 'default' : 'default',
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', fontWeight: 600 }}>#{configId}</span>
        <span style={{ color: 'var(--text-secondary)' }}>{strategy}</span>
        {lines && <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>ⓘ</span>}
      </span>
      {lines && (
        <span className="config-badge-popover">
          {lines.split('\n').map((l, i) => <span key={i} style={{ display: 'block' }}>{l}</span>)}
        </span>
      )}
    </span>
  )
}

/* ---- Status badge ---- */
function StatusBadge({ status }) {
  const colors = {
    open: 'var(--color-success)',
    pending_entry: 'var(--color-warning)',
    closed: 'var(--text-muted)',
    active: 'var(--color-success)',
    pending: 'var(--color-warning)',
  }
  const color = colors[status] || 'var(--text-secondary)'
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 'var(--radius-sm)',
      background: `${color}22`, color, fontSize: '0.75rem', fontWeight: 600,
      textTransform: 'uppercase', letterSpacing: '0.03em',
    }}>
      {status?.replace('_', ' ') || '—'}
    </span>
  )
}

/* ---- Toggle switch ---- */
function ToggleSwitch({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      onClick={() => !disabled && onChange(!checked)}
      style={{
        width: 44, height: 24, borderRadius: 12, border: 'none', cursor: disabled ? 'default' : 'pointer',
        background: checked ? 'var(--color-success)' : 'var(--border-strong)',
        position: 'relative', transition: 'background var(--transition-fast)',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <span style={{
        position: 'absolute', top: 2, left: checked ? 22 : 2,
        width: 20, height: 20, borderRadius: '50%', background: '#fff',
        transition: 'left var(--transition-fast)',
      }} />
    </button>
  )
}

/* ---- Capital mode selector ---- */
function CapitalConfig({ portfolio, setPortfolio, leverage, setLeverage, investedAmount, setInvestedAmount, mode, setMode, disabled }) {
  return (
    <div>
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
        <button
          type="button"
          className={`btn btn-sm ${mode === 'leverage' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setMode('leverage')}
          disabled={disabled}
        >Portfolio + Leverage</button>
        <button
          type="button"
          className={`btn btn-sm ${mode === 'invested' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setMode('invested')}
          disabled={disabled}
        >Portfolio + Invested</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-3)' }}>
        <div className="form-group">
          <label className="form-label">Portfolio (USD)</label>
          <input type="number" className="form-control" value={portfolio} min={1} step={100}
            onChange={e => setPortfolio(parseFloat(e.target.value) || 0)} disabled={disabled} />
        </div>
        {mode === 'leverage' ? (
          <div className="form-group">
            <label className="form-label">Leverage</label>
            <input type="number" className="form-control" value={leverage} min={0.1} step={0.1}
              onChange={e => setLeverage(parseFloat(e.target.value) || 1)} disabled={disabled} />
          </div>
        ) : (
          <div className="form-group">
            <label className="form-label">Invested Amount (USD)</label>
            <input type="number" className="form-control" value={investedAmount} min={1} step={100}
              onChange={e => setInvestedAmount(parseFloat(e.target.value) || 0)} disabled={disabled} />
          </div>
        )}
      </div>
    </div>
  )
}

/* ---- Config creation form ---- */
function ConfigForm({ strategies, onCreated }) {
  const [symbol, setSymbol] = useState('BTCUSDT')
  const [interval, setInterval] = useState('1d')
  const [selectedStrat, setSelectedStrat] = useState('')
  const [paramValues, setParamValues] = useState({})
  const [stopCrossPct, setStopCrossPct] = useState(0.02)
  const [costBps, setCostBps] = useState(10)
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
        stop_cross_pct: stopCrossPct,
        cost_bps: costBps,
        portfolio,
        ...(capitalMode === 'leverage'
          ? { leverage, invested_amount: null }
          : { invested_amount: investedAmount, leverage: null }),
      }
      const res = await fetch('/api/signals/configs', {
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
              <label className="form-label">Stop Cross % (extra)</label>
              <input type="number" className="form-control" value={stopCrossPct} min={0} step={0.005}
                onChange={e => setStopCrossPct(parseFloat(e.target.value) || 0)} disabled={loading} />
            </div>
            <div className="form-group">
              <label className="form-label">Cost (bps)</label>
              <input type="number" className="form-control" value={costBps} min={0} step={1}
                onChange={e => setCostBps(parseFloat(e.target.value) || 0)} disabled={loading} />
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

/* ---- Configs list ---- */
function ConfigsList({ configs, onToggle, onDelete }) {
  if (!configs || configs.length === 0) {
    return <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: 'var(--space-3)' }}>No signal configs yet.</div>
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="trade-table" style={{ width: '100%' }}>
        <thead>
          <tr>
            <th>ID</th>
            <th>Pair</th>
            <th>Interval</th>
            <th>Strategy</th>
            <th>Portfolio</th>
            <th>Stop Cross %</th>
            <th>Active</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {configs.map(c => (
            <tr key={c.id}>
              <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{c.id}</td>
              <td>{c.symbol}</td>
              <td>{c.interval}</td>
              <td>{c.strategy}</td>
              <td>{fmtMoney(c.portfolio)}</td>
              <td>{fmtNum(c.stop_cross_pct * 100, 1)}%</td>
              <td>
                <ToggleSwitch checked={c.active} onChange={(val) => onToggle(c.id, val)} />
              </td>
              <td>
                <button className="btn btn-sm btn-secondary" style={{ color: 'var(--color-danger)' }}
                  onClick={() => { if (confirm('Delete this config? Open trades will be closed.')) onDelete(c.id) }}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ---- Signals table ---- */
function SignalsList({ signals }) {
  if (!signals || signals.length === 0) {
    return <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: 'var(--space-3)' }}>No signals generated yet.</div>
  }
  return (
    <div>
      {/* Legend */}
      <div style={{
        padding: 'var(--space-2) var(--space-3)',
        background: 'var(--bg-elevated)', borderRadius: 'var(--radius-sm)',
        marginBottom: 'var(--space-3)', fontSize: '0.76rem', color: 'var(--text-muted)',
        lineHeight: 1.6,
      }}>
        💡 <strong style={{ color: 'var(--text-secondary)' }}>Entry (next open)</strong>: precio al que entrar en el exchange &nbsp;·&nbsp;
        <strong style={{ color: 'var(--text-secondary)' }}>Stop Base (SL)</strong>: nivel de stop-loss para tu exchange &nbsp;·&nbsp;
        <strong style={{ color: 'var(--text-secondary)' }}>Auto-close trigger</strong>: el sistema cierra el SimTrade aquí automáticamente (= Stop Base ± stop_cross_pct)
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table className="trade-table" style={{ width: '100%' }}>
          <thead>
            <tr>
              <th>ID</th>
              <th>Config</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Signal Candle</th>
              <th title="Open price of the next candle — use this as your entry on the exchange">Entry (next open) ↗</th>
              <th title="Strategy stop-loss level — set this as your SL on the exchange">Stop Base (SL) 🛑</th>
              <th title="Auto-close trigger = Stop Base ± stop_cross_pct. The system closes the SimTrade here automatically.">Auto-close trigger</th>
              <th>Status</th>
              <th>Sim Trade</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {signals.map(s => {
              const hasEntry = s.entry_price != null
              const entryColor = hasEntry ? 'var(--text-primary)' : 'var(--color-warning)'
              const simStatusLabel = s.sim_trade_status
                ? s.sim_trade_status.replace('_', ' ')
                : null
              return (
                <tr key={s.id}>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{s.id}</td>
                  <td>
                    <ConfigBadge configId={s.config_id} strategy={s.strategy} params={s.config_params} />
                  </td>
                  <td>{s.symbol}</td>
                  <td style={{ color: s.side === 'long' ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 700 }}>
                    {s.side?.toUpperCase()}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem' }}>{fmtTime(s.trigger_candle_time)}</td>
                  <td style={{ color: entryColor, fontWeight: hasEntry ? 600 : 400 }}>
                    {hasEntry ? fmtNum(s.entry_price, 4) : 'Pending…'}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>{fmtNum(s.stop_price, 4)}</td>
                  <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{fmtNum(s.stop_trigger_price, 4)}</td>
                  <td><StatusBadge status={s.status} /></td>
                  <td style={{ fontSize: '0.78rem' }}>
                    {s.sim_trade_id
                      ? <span>#{s.sim_trade_id}{simStatusLabel ? <span style={{ marginLeft: 4, color: 'var(--text-muted)' }}>({simStatusLabel})</span> : null}</span>
                      : '—'}
                  </td>
                  <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>{fmtIso(s.created_at)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ---- SimTrades table ---- */
function SimTradesList({ trades, onClose }) {
  if (!trades || trades.length === 0) {
    return <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: 'var(--space-3)' }}>No sim trades yet.</div>
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="trade-table" style={{ width: '100%' }}>
        <thead>
          <tr>
            <th>ID</th>
            <th>Config</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Entry</th>
            <th>Stop Trigger</th>
            <th>Exit</th>
            <th>Reason</th>
            <th>PnL</th>
            <th>PnL %</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {trades.map(t => {
            const pnlColor = t.pnl > 0 ? 'var(--color-success)' : t.pnl < 0 ? 'var(--color-danger)' : 'var(--text-secondary)'
            return (
              <tr key={t.id}>
                <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{t.id}</td>
                <td>
                  <ConfigBadge configId={t.config_id} strategy={t.config_strategy} params={t.config_params} />
                </td>
                <td>{t.symbol}</td>
                <td style={{ color: t.side === 'long' ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                  {t.side?.toUpperCase()}
                </td>
                <td>{t.entry_price ? fmtNum(t.entry_price, 4) : '—'}</td>
                <td>{fmtNum(t.stop_trigger, 4)}</td>
                <td>{t.exit_price ? fmtNum(t.exit_price, 4) : '—'}</td>
                <td>{t.exit_reason || '—'}</td>
                <td style={{ color: pnlColor, fontWeight: 600 }}>{t.pnl != null ? fmtMoney(t.pnl) : '—'}</td>
                <td style={{ color: pnlColor }}>{t.pnl_pct != null ? fmtNum(t.pnl_pct * 100, 2) + '%' : '—'}</td>
                <td><StatusBadge status={t.status} /></td>
                <td>
                  {t.status === 'open' && (
                    <button className="btn btn-sm btn-secondary" onClick={() => onClose(t.id)}>Close</button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/* ---- Real Trades section ---- */
function RealTradesSection() {
  const [realTrades, setRealTrades] = useState([])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    sim_trade_id: '', symbol: '', side: 'long', entry_price: '', entry_time: '', quantity: '', fees: '0', notes: '',
  })
  const [loading, setLoading] = useState(false)

  const fetchRealTrades = useCallback(async () => {
    try {
      const res = await fetch('/api/real-trades?limit=100')
      if (res.ok) {
        const data = await res.json()
        setRealTrades(data.real_trades ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { fetchRealTrades() }, [fetchRealTrades])

  const handleCreate = async () => {
    setLoading(true)
    try {
      const body = {
        symbol: form.symbol || 'BTCUSDT',
        side: form.side,
        entry_price: parseFloat(form.entry_price),
        entry_time: form.entry_time || new Date().toISOString(),
        quantity: parseFloat(form.quantity),
        fees: parseFloat(form.fees) || 0,
        notes: form.notes || null,
        sim_trade_id: form.sim_trade_id ? parseInt(form.sim_trade_id) : null,
      }
      const res = await fetch('/api/real-trades', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (res.ok) {
        setShowForm(false)
        setForm({ sim_trade_id: '', symbol: '', side: 'long', entry_price: '', entry_time: '', quantity: '', fees: '0', notes: '' })
        fetchRealTrades()
      }
    } catch { /* ignore */ }
    setLoading(false)
  }

  const _handleClose = async (id, exitPrice, fees) => {
    await fetch(`/api/real-trades/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exit_price: exitPrice, exit_time: new Date().toISOString(), status: 'closed', fees }),
    })
    fetchRealTrades()
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
        <button className="btn btn-sm btn-primary" onClick={() => setShowForm(!showForm)}>
          {showForm ? 'Cancel' : '+ Register Real Trade'}
        </button>
        <button className="btn btn-sm btn-secondary" onClick={fetchRealTrades}>Refresh</button>
      </div>

      {showForm && (
        <div className="card" style={{ marginBottom: 'var(--space-4)', padding: 'var(--space-4)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 'var(--space-3)' }}>
            <div className="form-group">
              <label className="form-label">Link to SimTrade ID</label>
              <input type="number" className="form-control" value={form.sim_trade_id}
                onChange={e => setForm(prev => ({ ...prev, sim_trade_id: e.target.value }))} placeholder="Optional" />
            </div>
            <div className="form-group">
              <label className="form-label">Symbol</label>
              <select className="form-control" value={form.symbol}
                onChange={e => setForm(prev => ({ ...prev, symbol: e.target.value }))}>
                {PAIRS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Side</label>
              <select className="form-control" value={form.side}
                onChange={e => setForm(prev => ({ ...prev, side: e.target.value }))}>
                <option value="long">Long</option>
                <option value="short">Short</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Entry Price</label>
              <input type="number" className="form-control" value={form.entry_price} step="0.01"
                onChange={e => setForm(prev => ({ ...prev, entry_price: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Quantity</label>
              <input type="number" className="form-control" value={form.quantity} step="0.0001"
                onChange={e => setForm(prev => ({ ...prev, quantity: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Fees</label>
              <input type="number" className="form-control" value={form.fees} step="0.01"
                onChange={e => setForm(prev => ({ ...prev, fees: e.target.value }))} />
            </div>
          </div>
          <div className="form-group" style={{ marginTop: 'var(--space-3)' }}>
            <label className="form-label">Notes</label>
            <input type="text" className="form-control" value={form.notes}
              onChange={e => setForm(prev => ({ ...prev, notes: e.target.value }))} placeholder="Optional notes" />
          </div>
          <button className="btn btn-primary btn-sm" style={{ marginTop: 'var(--space-3)' }}
            onClick={handleCreate} disabled={loading || !form.entry_price || !form.quantity}>
            Save Real Trade
          </button>
        </div>
      )}

      {realTrades.length === 0 ? (
        <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No real trades registered.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="trade-table" style={{ width: '100%' }}>
            <thead>
              <tr>
                <th>ID</th>
                <th>Sim #</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>PnL</th>
                <th>Fees</th>
                <th>Status</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {realTrades.map(t => {
                const pnlColor = t.pnl > 0 ? 'var(--color-success)' : t.pnl < 0 ? 'var(--color-danger)' : 'var(--text-secondary)'
                return (
                  <tr key={t.id}>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{t.id}</td>
                    <td>{t.sim_trade_id || '—'}</td>
                    <td>{t.symbol}</td>
                    <td style={{ color: t.side === 'long' ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                      {t.side?.toUpperCase()}
                    </td>
                    <td>{fmtNum(t.entry_price, 4)}</td>
                    <td>{t.exit_price ? fmtNum(t.exit_price, 4) : '—'}</td>
                    <td style={{ color: pnlColor, fontWeight: 600 }}>{t.pnl != null ? fmtMoney(t.pnl) : '—'}</td>
                    <td>{fmtNum(t.fees, 2)}</td>
                    <td><StatusBadge status={t.status} /></td>
                    <td style={{ fontSize: '0.8rem', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.notes || '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/* ---- Comparison view ---- */
function ComparisonView() {
  const [simId, setSimId] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleFetch = async () => {
    if (!simId) return
    setLoading(true)
    try {
      const res = await fetch(`/api/comparison/${simId}`)
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

/* ==================================================================
   Main SignalsPanel
   ================================================================== */
export default function SignalsPanel() {
  const [tab, setTab] = useState('configs')
  const [strategies, setStrategies] = useState([])
  const [configs, setConfigs] = useState([])
  const [signals, setSignals] = useState([])
  const [simTrades, setSimTrades] = useState([])
  const [status, setStatus] = useState(null)

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await fetch('/api/strategies')
      if (res.ok) {
        const data = await res.json()
        setStrategies(data.strategies ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  const fetchConfigs = useCallback(async () => {
    try {
      const res = await fetch('/api/signals/configs')
      if (res.ok) {
        const data = await res.json()
        setConfigs(data.configs ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  const fetchSignals = useCallback(async () => {
    try {
      const res = await fetch('/api/signals?limit=100')
      if (res.ok) {
        const data = await res.json()
        setSignals(data.signals ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  const fetchSimTrades = useCallback(async () => {
    try {
      const res = await fetch('/api/sim-trades?limit=100')
      if (res.ok) {
        const data = await res.json()
        setSimTrades(data.sim_trades ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/signals/status')
      if (res.ok) setStatus(await res.json())
    } catch { /* ignore */ }
  }, [])

  const refreshAll = useCallback(() => {
    fetchConfigs()
    fetchSignals()
    fetchSimTrades()
    fetchStatus()
  }, [fetchConfigs, fetchSignals, fetchSimTrades, fetchStatus])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchStrategies()
    refreshAll()
    const iv = window.setInterval(refreshAll, 15000)
    return () => clearInterval(iv)
  }, [fetchStrategies, refreshAll])

  const handleToggle = async (id, active) => {
    await fetch(`/api/signals/configs/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active }),
    })
    fetchConfigs()
  }

  const handleDelete = async (id) => {
    await fetch(`/api/signals/configs/${id}`, { method: 'DELETE' })
    refreshAll()
  }

  const handleCloseSimTrade = async (id) => {
    await fetch(`/api/sim-trades/${id}/close`, { method: 'POST' })
    refreshAll()
  }

  const TABS = [
    { id: 'configs', label: 'Configurations' },
    { id: 'signals', label: `Signals (${signals.length})` },
    { id: 'sim', label: `Sim Trades (${simTrades.length})` },
    { id: 'real', label: 'Real Trades' },
    { id: 'compare', label: 'Compare' },
  ]

  return (
    <div className="panel-section">

      {/* Status bar */}
      {status && (
        <div style={{
          display: 'flex', gap: 'var(--space-4)', padding: 'var(--space-3) var(--space-4)',
          background: 'var(--bg-elevated)', borderRadius: 'var(--radius-sm)',
          marginBottom: 'var(--space-4)', fontSize: '0.82rem', color: 'var(--text-secondary)',
          alignItems: 'center', flexWrap: 'wrap',
        }}>
          <span>Active configs: <strong style={{ color: 'var(--text-primary)' }}>{status.active_configs}</strong></span>
          <span>Open trades: <strong style={{ color: 'var(--color-success)' }}>{status.open_sim_trades}</strong></span>
          <span>Pending: <strong style={{ color: 'var(--color-warning)' }}>{status.pending_sim_trades}</strong></span>
          <span>Signals (24h): <strong style={{ color: 'var(--text-primary)' }}>{status.signals_last_24h}</strong></span>
          <div style={{ flex: 1 }} />
          <button className="btn btn-sm btn-secondary" onClick={refreshAll}>Refresh</button>
        </div>
      )}

      {/* Sub-tabs */}
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)', flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button
            key={t.id}
            className={`btn btn-sm ${tab === t.id ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setTab(t.id)}
          >{t.label}</button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'configs' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Signal Configurations</span>
          </div>
          <div className="card-body">
            <div className="section-title">Active Configurations</div>
            <ConfigsList configs={configs} onToggle={handleToggle} onDelete={handleDelete} onRefresh={fetchConfigs} />
            <hr className="divider" />
            <div className="section-title">New Configuration</div>
            <ConfigForm strategies={strategies} onCreated={refreshAll} />
          </div>
        </div>
      )}

      {tab === 'signals' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Generated Signals</span>
          </div>
          <div className="card-body">
            <SignalsList signals={signals} />
          </div>
        </div>
      )}

      {tab === 'sim' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Simulated Trades (Paper)</span>
          </div>
          <div className="card-body">
            <SimTradesList trades={simTrades} onClose={handleCloseSimTrade} />
          </div>
        </div>
      )}

      {tab === 'real' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Real Trades</span>
          </div>
          <div className="card-body">
            <RealTradesSection simTrades={simTrades} />
          </div>
        </div>
      )}

      {tab === 'compare' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Sim vs Real Comparison</span>
          </div>
          <div className="card-body">
            <ComparisonView />
          </div>
        </div>
      )}
    </div>
  )
}
