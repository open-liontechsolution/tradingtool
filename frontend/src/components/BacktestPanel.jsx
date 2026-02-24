import { useState, useEffect, useCallback } from 'react'
import EquityChart from './EquityChart'
import TradeLog from './TradeLog'

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

const DEFAULT_START = () => {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 3)
  return d.toISOString().split('T')[0]
}

const DEFAULT_END = () => new Date().toISOString().split('T')[0]

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(digits)
}

function fmtMoney(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(n)
}

/* ---- Dynamic parameter form for a strategy ---- */
function ParamForm({ params, values, onChange, disabled }) {
  if (!params || params.length === 0) return null

  return (
    <div className="grid-3" style={{ gap: 'var(--space-3)' }}>
      {params.map(p => {
        const val = values[p.name] !== undefined ? values[p.name] : p.default
        if (p.type === 'bool') {
          return (
            <div key={p.name} className="form-group">
              <label className="form-label" title={p.description}>{p.name}</label>
              <div className="toggle-group">
                <button
                  type="button"
                  className={`toggle-option${val === true || val === 'true' ? ' active' : ''}`}
                  onClick={() => !disabled && onChange(p.name, true)}
                >On</button>
                <button
                  type="button"
                  className={`toggle-option${val === false || val === 'false' ? ' active' : ''}`}
                  onClick={() => !disabled && onChange(p.name, false)}
                >Off</button>
              </div>
            </div>
          )
        }

        if (p.type === 'str') {
          const options = ['open_next', 'close_current']
          return (
            <div key={p.name} className="form-group">
              <label className="form-label" title={p.description}>{p.name}</label>
              <select
                className="form-control"
                value={val}
                onChange={e => onChange(p.name, e.target.value)}
                disabled={disabled}
              >
                {options.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
          )
        }

        // int / float
        return (
          <div key={p.name} className="form-group">
            <label className="form-label" title={p.description}>{p.name}</label>
            <input
              type="number"
              className="form-control"
              value={val}
              min={p.min ?? undefined}
              max={p.max ?? undefined}
              step={p.type === 'float' ? 0.001 : 1}
              onChange={e => {
                const raw = e.target.value
                const parsed = p.type === 'float' ? parseFloat(raw) : parseInt(raw, 10)
                onChange(p.name, isNaN(parsed) ? raw : parsed)
              }}
              disabled={disabled}
            />
          </div>
        )
      })}
    </div>
  )
}

/* ---- Summary metrics grid ---- */
function MetricsGrid({ summary, capital, liquidated }) {
  if (!summary || Object.keys(summary).length === 0) return null

  const np = summary.net_profit ?? 0
  const npPositive = np >= 0

  const CARDS = [
    { label: 'Net Profit',    value: fmtMoney(summary.net_profit),         cls: npPositive ? 'positive' : 'negative' },
    { label: 'Net Profit %',  value: fmtNum(summary.net_profit_pct) + '%', cls: npPositive ? 'positive' : 'negative' },
    { label: 'CAGR',          value: fmtNum(summary.cagr_pct) + '%',       cls: (summary.cagr_pct ?? 0) >= 0 ? 'positive' : 'negative' },
    { label: 'Max Drawdown',  value: fmtNum(summary.max_drawdown_pct) + '%', cls: 'negative' },
    { label: 'Sharpe',        value: fmtNum(summary.sharpe, 3),            cls: 'neutral' },
    { label: 'Sortino',       value: fmtNum(summary.sortino, 3),           cls: 'neutral' },
    { label: 'Win Rate',      value: fmtNum(summary.win_rate_pct) + '%',   cls: (summary.win_rate_pct ?? 0) >= 50 ? 'positive' : 'negative' },
    { label: 'Profit Factor', value: summary.profit_factor != null ? fmtNum(summary.profit_factor, 3) : '∞', cls: (summary.profit_factor ?? 0) >= 1 ? 'positive' : 'negative' },
    { label: 'Expectancy',    value: fmtMoney(summary.expectancy),         cls: (summary.expectancy ?? 0) >= 0 ? 'positive' : 'negative' },
    { label: 'Avg Win',       value: fmtMoney(summary.avg_win),            cls: 'positive' },
    { label: 'Avg Loss',      value: fmtMoney(summary.avg_loss),           cls: 'negative' },
    { label: 'Payoff Ratio',  value: summary.payoff_ratio != null ? fmtNum(summary.payoff_ratio, 3) : '∞', cls: 'neutral' },
    { label: 'Trades',        value: summary.n_trades ?? 0,                cls: 'neutral' },
    { label: 'Time in Market',value: fmtNum(summary.time_in_market_pct) + '%', cls: 'neutral' },
  ]

  return (
    <div>
      {liquidated && (
        <div style={{
          padding: '8px 14px', marginBottom: 'var(--space-4)',
          background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)',
          borderRadius: 'var(--radius-sm)', color: 'var(--color-danger)', fontSize: '0.85rem',
        }}>
          Account liquidated — equity reached zero during backtest.
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 'var(--space-3)' }}>
        {CARDS.map(c => (
          <div key={c.label} className="metric-card">
            <span className="metric-label">{c.label}</span>
            <span className={`metric-value ${c.cls}`}>{c.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---- Results tabs (Chart | Trades) ---- */
function ResultsView({ result }) {
  const [view, setView] = useState('chart')

  if (!result) return null

  const equityCurve   = result.equity_curve ?? []
  const tradeLog      = result.trade_log    ?? []
  const drawdownCurve = result.summary?.drawdown_curve
    ? result.summary.drawdown_curve.map((v, i) => ({
        timestamp: equityCurve[i]?.timestamp,
        drawdown: v / 100,  // backend returns percentages, chart expects ratio
      }))
    : []

  return (
    <div className="panel-section">
      <MetricsGrid summary={result.summary} liquidated={result.liquidated} />

      <hr className="divider" />

      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
        {[
          { id: 'chart',  label: 'Charts' },
          { id: 'trades', label: `Trades (${tradeLog.length})` },
        ].map(t => (
          <button
            key={t.id}
            className={`btn btn-sm ${view === t.id ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setView(t.id)}
          >
            {t.label}
          </button>
        ))}

        <div style={{ flex: 1 }} />

        <a
          href={`/api/backtest/${result.id}/export?format=csv`}
          target="_blank"
          rel="noreferrer"
          className="btn btn-secondary btn-sm"
        >
          Export CSV
        </a>
        <a
          href={`/api/backtest/${result.id}/export?format=json`}
          target="_blank"
          rel="noreferrer"
          className="btn btn-secondary btn-sm"
        >
          Export JSON
        </a>
      </div>

      {view === 'chart'  && <EquityChart equityCurve={equityCurve} drawdownCurve={drawdownCurve} />}
      {view === 'trades' && <TradeLog trades={tradeLog} />}
    </div>
  )
}

/* ---- Main BacktestPanel component ---- */
export default function BacktestPanel() {
  const [symbol,    setSymbol]   = useState('BTCUSDT')
  const [interval,  setInterval] = useState('1d')
  const [startDate, setStart]    = useState(DEFAULT_START())
  const [endDate,   setEnd]      = useState(DEFAULT_END())
  const [capital,   setCapital]  = useState(10000)

  const [strategies,    setStrategies]    = useState([])
  const [selectedStrat, setSelectedStrat] = useState('')
  const [paramValues,   setParamValues]   = useState({})

  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState(null)
  const [resultId,   setResultId]   = useState(null)
  const [result,     setResult]     = useState(null)
  const [loadingRes, setLoadingRes] = useState(false)

  // Fetch available strategies
  const fetchStrategies = useCallback(async () => {
    try {
      const res = await fetch('/api/strategies')
      if (!res.ok) return
      const data = await res.json()
      const list = data.strategies ?? []
      setStrategies(list)
      if (list.length > 0 && !selectedStrat) {
        const first = list[0]
        setSelectedStrat(first.name)
        const defaults = {}
        for (const p of first.parameters ?? []) {
          defaults[p.name] = p.default
        }
        setParamValues(defaults)
      }
    } catch {
      // backend not running — ignore
    }
  }, [selectedStrat])

  useEffect(() => { fetchStrategies() }, [])

  // When strategy changes, reset param defaults
  const handleStrategyChange = e => {
    const name = e.target.value
    setSelectedStrat(name)
    const strat = strategies.find(s => s.name === name)
    if (strat) {
      const defaults = {}
      for (const p of strat.parameters ?? []) {
        defaults[p.name] = p.default
      }
      setParamValues(defaults)
    }
  }

  const handleParamChange = (name, value) => {
    setParamValues(prev => ({ ...prev, [name]: value }))
  }

  // Fetch full result after getting ID
  const fetchResult = useCallback(async (id) => {
    setLoadingRes(true)
    try {
      const res = await fetch(`/api/backtest/${id}`)
      if (!res.ok) { setError(`Could not fetch backtest ${id}`); return }
      const data = await res.json()
      setResult(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingRes(false)
    }
  }, [])

  const handleRun = async () => {
    if (!symbol || !interval || !startDate || !endDate || !selectedStrat) return
    setError(null)
    setResult(null)
    setResultId(null)
    setLoading(true)

    try {
      const res = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          interval,
          start_time: new Date(startDate).getTime(),
          end_time:   new Date(endDate).getTime(),
          strategy:   selectedStrat,
          params:     paramValues,
          initial_capital: Number(capital),
        }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setError(err.detail ?? `HTTP ${res.status}`)
        return
      }

      const data = await res.json()
      setResultId(data.id)
      // Fetch full result (with equity curve)
      await fetchResult(data.id)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const currentStrat = strategies.find(s => s.name === selectedStrat)

  return (
    <div className="panel-section">

      {/* Configuration card */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Backtest Configuration</span>
        </div>
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>

          {/* Data selection row */}
          <div>
            <div className="section-title">Data Selection</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 'var(--space-3)' }}>
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
                <label className="form-label">Start Date</label>
                <input type="date" className="form-control" value={startDate} max={endDate} onChange={e => setStart(e.target.value)} disabled={loading} />
              </div>
              <div className="form-group">
                <label className="form-label">End Date</label>
                <input type="date" className="form-control" value={endDate} min={startDate} onChange={e => setEnd(e.target.value)} disabled={loading} />
              </div>
            </div>
          </div>

          <hr className="divider" style={{ margin: 0 }} />

          {/* Strategy + capital */}
          <div>
            <div className="section-title">Strategy & Capital</div>
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 'var(--space-3)', marginBottom: 'var(--space-4)' }}>
              <div className="form-group">
                <label className="form-label">Strategy</label>
                {strategies.length === 0 ? (
                  <div style={{ padding: '8px 12px', background: 'var(--bg-input)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', color: 'var(--text-muted)', fontSize: '0.83rem' }}>
                    Connect to backend to load strategies…
                  </div>
                ) : (
                  <select className="form-control" value={selectedStrat} onChange={handleStrategyChange} disabled={loading}>
                    {strategies.map(s => (
                      <option key={s.name} value={s.name}>{s.name} — {s.description?.slice(0, 60)}{s.description?.length > 60 ? '…' : ''}</option>
                    ))}
                  </select>
                )}
              </div>
              <div className="form-group">
                <label className="form-label">Initial Capital (USD)</label>
                <input
                  type="number"
                  className="form-control"
                  value={capital}
                  min={1}
                  step={100}
                  onChange={e => setCapital(e.target.value)}
                  disabled={loading}
                />
              </div>
            </div>

            {/* Dynamic parameter form */}
            {currentStrat && currentStrat.parameters && currentStrat.parameters.length > 0 && (
              <div>
                <div className="section-title">{currentStrat.name} Parameters</div>
                <ParamForm
                  params={currentStrat.parameters}
                  values={paramValues}
                  onChange={handleParamChange}
                  disabled={loading}
                />
              </div>
            )}
          </div>

          {/* Error */}
          {error && (
            <div style={{
              padding: '8px 12px',
              background: 'rgba(239,68,68,0.1)',
              border: '1px solid rgba(239,68,68,0.25)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--color-danger)',
              fontSize: '0.83rem',
            }}>
              {error}
            </div>
          )}

          {/* Run button */}
          <div>
            <button
              className="btn btn-primary btn-lg"
              onClick={handleRun}
              disabled={loading || !selectedStrat}
            >
              {loading ? (
                <><span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span> Running…</>
              ) : (
                'Run Backtest'
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Results */}
      {(result || loadingRes) && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">
              {loadingRes ? (
                <><span className="spinner" style={{ width: 14, height: 14 }}></span> Loading results…</>
              ) : (
                `Results — ${result.symbol} ${result.interval} · ${result.strategy}`
              )}
            </span>
          </div>
          <div className="card-body">
            {!loadingRes && result && <ResultsView result={result} />}
          </div>
        </div>
      )}
    </div>
  )
}
