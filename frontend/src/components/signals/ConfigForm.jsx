import { useState, useEffect, useMemo } from 'react'
import { apiFetch } from '../../auth/apiFetch'
import FieldLabel from '../FieldLabel'
import { useToast } from '../useToast'
import { PAIRS, INTERVALS, fmtMoney } from './helpers'
import { CapitalConfig } from './CapitalConfig'

const STRATEGY_PARAM_TIPS = {
  reversal_pct: 'Mínimo % de retroceso desde el extremo móvil para confirmar un swing (soporte/resistencia). E.g. 0.03 = 3%.',
  N_entrada: 'Nº de velas hacia atrás para detectar el breakout de entrada (sin contar la actual).',
  M_salida: 'Nº de velas hacia atrás para la señal de salida. Debe ser ≤ N.',
  stop_pct: 'Distancia del stop como fracción del precio de referencia de entrada (e.g. 0.02 = 2%).',
  modo_ejecucion: 'open_next: la orden se llena al open de la siguiente vela (realista). close_current: se llena al close de la vela de señal (optimista).',
  habilitar_long: 'Permite entradas long (compra) cuando el precio cierra por encima del breakout de la N-vela.',
  habilitar_short: 'Permite entradas short (venta) cuando el precio cierra por debajo del breakout de la N-vela.',
  coste_total_bps: 'Coste total round-trip en basis points (1 bps = 0.01%). Cubre fees + slippage de entrada y salida.',
}

const FIELD_TIPS = {
  pair: 'Par de criptomonedas a operar. Solo USDT por ahora.',
  interval: 'Timeframe de las velas. Define cada cuánto evalúa el motor de señales el cierre de vela.',
  strategy: 'Estrategia a ejecutar. Cada una expone sus propios parámetros más abajo.',
  costBps: 'Coste de transacción aplicado a cada lado del trade en basis points (1 bps = 0.01%). Binance ≈ 10 bps/lado.',
  maintenanceMargin: 'Margen de mantenimiento usado para calcular el precio de liquidación. Binance baseline ≈ 0.005 (0.5%) para notional bajo.',
}

const STEPS = [
  { id: 1, label: 'Strategy & symbol' },
  { id: 2, label: 'Capital & risk' },
  { id: 3, label: 'Confirm' },
]

function StepIndicator({ step }) {
  return (
    <ol className="wizard-steps" aria-label="Form progress">
      {STEPS.map(s => {
        const cls = s.id === step ? 'wizard-step--active' : (s.id < step ? 'wizard-step--done' : '')
        return (
          <li key={s.id} className={`wizard-step ${cls}`} aria-current={s.id === step ? 'step' : undefined}>
            <span className="wizard-step__num" aria-hidden="true">{s.id < step ? '✓' : s.id}</span>
            <span>{s.label}</span>
          </li>
        )
      })}
    </ol>
  )
}

export function ConfigForm({ strategies, onCreated }) {
  const [step, setStep] = useState(1)
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
  const toast = useToast()

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

  const step1Valid = !!selectedStrat
  const step2Valid =
    portfolio > 0 &&
    maintenanceMarginPct >= 0 &&
    costBps >= 0 &&
    (capitalMode === 'leverage' ? leverage > 0 : investedAmount > 0)

  const summary = useMemo(() => ({
    Symbol: symbol,
    Interval: INTERVALS.find(i => i.value === interval)?.label ?? interval,
    Strategy: selectedStrat,
    Params: Object.entries(paramValues).map(([k, v]) => `${k}=${v}`).join(', ') || '—',
    'Cost (bps)': costBps,
    'Maint. margin %': maintenanceMarginPct,
    Portfolio: fmtMoney(portfolio),
    ...(capitalMode === 'leverage'
      ? { Leverage: leverage > 1 ? `${leverage}× (liquidación calculada)` : `${leverage}× (sin apalancamiento)` }
      : { 'Invested amount': fmtMoney(investedAmount) }),
  }), [symbol, interval, selectedStrat, paramValues, costBps, maintenanceMarginPct, portfolio, leverage, investedAmount, capitalMode])

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
        const msg = err.detail ?? `HTTP ${res.status}`
        setError(msg)
        toast.error(`Failed to create config: ${msg}`)
        return
      }
      toast.success(`Signal config created for ${symbol} ${interval}`)
      setStep(1)
      onCreated()
    } catch (e) {
      setError(e.message)
      toast.error(`Network error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <StepIndicator step={step} />

      {step === 1 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 'var(--space-3)' }}>
            <div className="form-group">
              <FieldLabel tooltip={FIELD_TIPS.pair}>Pair</FieldLabel>
              <select className="form-control" value={symbol} onChange={e => setSymbol(e.target.value)} disabled={loading}>
                {PAIRS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div className="form-group">
              <FieldLabel tooltip={FIELD_TIPS.interval}>Interval</FieldLabel>
              <select className="form-control" value={interval} onChange={e => setInterval(e.target.value)} disabled={loading}>
                {INTERVALS.map(iv => <option key={iv.value} value={iv.value}>{iv.label}</option>)}
              </select>
            </div>
            <div className="form-group">
              <FieldLabel tooltip={FIELD_TIPS.strategy}>Strategy</FieldLabel>
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
                const tip = STRATEGY_PARAM_TIPS[p.name] ?? p.description ?? undefined
                if (p.type === 'bool') {
                  return (
                    <div key={p.name} className="form-group">
                      <FieldLabel tooltip={tip}>{p.name}</FieldLabel>
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
                      <FieldLabel tooltip={tip}>{p.name}</FieldLabel>
                      <select className="form-control" value={val}
                        onChange={e => setParamValues(prev => ({ ...prev, [p.name]: e.target.value }))} disabled={loading}>
                        {['open_next', 'close_current'].map(o => <option key={o} value={o}>{o}</option>)}
                      </select>
                    </div>
                  )
                }
                return (
                  <div key={p.name} className="form-group">
                    <FieldLabel tooltip={tip}>{p.name}</FieldLabel>
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
        </div>
      )}

      {step === 2 && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
          <div>
            <div className="section-title">Risk parameters</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-3)' }}>
              <div className="form-group">
                <FieldLabel tooltip={FIELD_TIPS.costBps}>Cost (bps)</FieldLabel>
                <input type="number" className="form-control" value={costBps} min={0} step={1}
                  onChange={e => setCostBps(parseFloat(e.target.value) || 0)} disabled={loading} />
              </div>
              <div className="form-group">
                <FieldLabel tooltip={FIELD_TIPS.maintenanceMargin}>Maint. margin %</FieldLabel>
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
      )}

      {step === 3 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          <div className="section-title">Review and confirm</div>
          <dl className="wizard-summary">
            {Object.entries(summary).map(([k, v]) => (
              <div key={k} style={{ display: 'contents' }}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {error && (
        <div style={{
          padding: '8px 12px', background: 'rgba(239,68,68,0.1)',
          border: '1px solid rgba(239,68,68,0.25)', borderRadius: 'var(--radius-sm)',
          color: 'var(--color-danger)', fontSize: '0.83rem',
        }}>{error}</div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => setStep(s => Math.max(1, s - 1))}
          disabled={step === 1 || loading}
        >← Back</button>
        {step < 3 ? (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setStep(s => Math.min(3, s + 1))}
            disabled={(step === 1 && !step1Valid) || (step === 2 && !step2Valid) || loading}
          >Next →</button>
        ) : (
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleCreate}
            disabled={loading || !step1Valid || !step2Valid}
          >{loading ? 'Creating…' : 'Create signal config'}</button>
        )}
      </div>
    </div>
  )
}
