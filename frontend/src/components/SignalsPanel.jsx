import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../auth/apiFetch'
import { ConfigForm } from './signals/ConfigForm'
import { ConfigsList } from './signals/ConfigsList'
import { SignalsList } from './signals/SignalsList'
import { SimTradesList } from './signals/SimTradesList'
import { RealTradesSection } from './signals/RealTradesSection'
import { ComparisonView } from './signals/ComparisonView'

export default function SignalsPanel() {
  const [tab, setTab] = useState('configs')
  const [strategies, setStrategies] = useState([])
  const [configs, setConfigs] = useState([])
  const [signals, setSignals] = useState([])
  const [simTrades, setSimTrades] = useState([])
  const [status, setStatus] = useState(null)

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await apiFetch('/api/strategies')
      if (res.ok) {
        const data = await res.json()
        setStrategies(data.strategies ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  const fetchConfigs = useCallback(async () => {
    try {
      const res = await apiFetch('/api/signals/configs')
      if (res.ok) {
        const data = await res.json()
        setConfigs(data.configs ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  const fetchSignals = useCallback(async () => {
    try {
      const res = await apiFetch('/api/signals?limit=100')
      if (res.ok) {
        const data = await res.json()
        setSignals(data.signals ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  const fetchSimTrades = useCallback(async () => {
    try {
      const res = await apiFetch('/api/sim-trades?limit=100')
      if (res.ok) {
        const data = await res.json()
        setSimTrades(data.sim_trades ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch('/api/signals/status')
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
    await apiFetch(`/api/signals/configs/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active }),
    })
    fetchConfigs()
  }

  const handleToggleTelegram = async (id, telegram_enabled) => {
    await apiFetch(`/api/signals/configs/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ telegram_enabled }),
    })
    fetchConfigs()
  }

  const handleDelete = async (id) => {
    await apiFetch(`/api/signals/configs/${id}`, { method: 'DELETE' })
    refreshAll()
  }

  const handleResetEquity = async (id) => {
    await apiFetch(`/api/signals/configs/${id}/reset-equity`, { method: 'POST' })
    refreshAll()
  }

  const handleCloseSimTrade = async (id) => {
    await apiFetch(`/api/sim-trades/${id}/close`, { method: 'POST' })
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

      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)', flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button
            key={t.id}
            className={`btn btn-sm ${tab === t.id ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setTab(t.id)}
          >{t.label}</button>
        ))}
      </div>

      {tab === 'configs' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Signal Configurations</span>
          </div>
          <div className="card-body">
            <div className="section-title">Active Configurations</div>
            <ConfigsList configs={configs} onToggle={handleToggle} onToggleTelegram={handleToggleTelegram} onDelete={handleDelete} onResetEquity={handleResetEquity} />
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
            <RealTradesSection />
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
