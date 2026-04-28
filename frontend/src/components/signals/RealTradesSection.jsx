import React, { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../../auth/apiFetch'
import FieldLabel from '../FieldLabel'
import EmptyState from '../EmptyState'
import { useToast } from '../useToast'
import { StatusBadge } from './ConfigBadge'
import { PAIRS, fmtNum, fmtMoney } from './helpers'

const FEE_WARN_PCT = 0.05

const TIPS = {
  simTradeId: 'ID del sim-trade que estás replicando con dinero real. Permite comparar slippage entre simulación y ejecución real (tab Compare).',
  symbol: 'Par operado en el exchange.',
  side: 'long: compraste primero. short: vendiste primero (margin/futures).',
  entryPrice: 'Precio efectivo al que abriste la posición (después de fees de entrada si tu exchange los descuenta del precio).',
  quantity: 'Cantidad de la criptomoneda base (no USDT). E.g. 0.1 BTC.',
  fees: 'Fees totales de entrada en USD. Si los pones aquí no los pongas también dentro del PnL al cerrar.',
  notes: 'Cualquier contexto que quieras retener: por qué tomaste el trade, condiciones del mercado, etc.',
  exitPrice: 'Precio efectivo al que cerraste la posición.',
  netPnl: 'PnL neto en USD (después de descontar fees del exchange). Si lo dejas en bruto, el sistema avisa si los fees implicados parecen anómalos.',
  exitTime: 'Hora del cierre. Si la dejas vacía se usa "ahora".',
}

export function RealTradesSection() {
  const [realTrades, setRealTrades] = useState([])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    sim_trade_id: '', symbol: '', side: 'long', entry_price: '', entry_time: '', quantity: '', fees: '0', notes: '',
  })
  const [loading, setLoading] = useState(false)
  const [closingId, setClosingId] = useState(null)
  const [closeForm, setCloseForm] = useState({ exit_price: '', pnl: '', exit_time: '', notes: '' })
  const [closeError, setCloseError] = useState(null)
  const [justClosedId, setJustClosedId] = useState(null)
  const toast = useToast()

  const fetchRealTrades = useCallback(async () => {
    try {
      const res = await apiFetch('/api/real-trades?limit=100')
      if (res.ok) {
        const data = await res.json()
        setRealTrades(data.real_trades ?? [])
      }
    } catch { /* ignore */ }
  }, [])

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
      const res = await apiFetch('/api/real-trades', {
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

  const computeCloseSummary = (trade) => {
    const exitPrice = parseFloat(closeForm.exit_price)
    const netPnl = parseFloat(closeForm.pnl)
    const qty = parseFloat(trade.quantity)
    const entry = parseFloat(trade.entry_price)
    if ([exitPrice, netPnl, qty, entry].some(v => !Number.isFinite(v))) return null
    const invested = qty * entry
    const gross = trade.side === 'long' ? qty * (exitPrice - entry) : qty * (entry - exitPrice)
    const fee = gross - netPnl
    const feePct = invested > 0 ? Math.abs(fee) / invested : 0
    const warn = fee < 0 || feePct > FEE_WARN_PCT
    return { invested, gross, netPnl, fee, feePct, warn }
  }

  const handleClose = async (trade) => {
    setLoading(true)
    setCloseError(null)
    try {
      const s = computeCloseSummary(trade)
      const body = {
        exit_price: parseFloat(closeForm.exit_price),
        exit_time: closeForm.exit_time || new Date().toISOString(),
        pnl: parseFloat(closeForm.pnl),
        fees: s?.fee ?? 0,
        status: 'closed',
      }
      if (closeForm.notes) body.notes = closeForm.notes
      const res = await apiFetch(`/api/real-trades/${trade.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        let detail = `HTTP ${res.status}`
        try { const j = await res.json(); if (j?.detail) detail = j.detail } catch { /* non-JSON body */ }
        const msg = `Close failed: ${detail}`
        setCloseError(msg)
        toast.error(msg)
        return
      }
      await fetchRealTrades()
      setClosingId(null)
      setCloseForm({ exit_price: '', pnl: '', exit_time: '', notes: '' })
      setJustClosedId(trade.id)
      setTimeout(() => setJustClosedId(prev => (prev === trade.id ? null : prev)), 1500)
      toast.success(`Real trade #${trade.id} closed`)
    } catch (e) {
      const msg = e?.message || 'Network error closing trade'
      setCloseError(msg)
      toast.error(msg)
    } finally {
      setLoading(false)
    }
  }

  const openCloseForm = (trade) => {
    setClosingId(trade.id)
    setCloseForm({ exit_price: '', pnl: '', exit_time: '', notes: '' })
    setCloseError(null)
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
              <FieldLabel tooltip={TIPS.simTradeId}>Link to SimTrade ID</FieldLabel>
              <input type="number" className="form-control" value={form.sim_trade_id}
                onChange={e => setForm(prev => ({ ...prev, sim_trade_id: e.target.value }))} placeholder="Optional" />
            </div>
            <div className="form-group">
              <FieldLabel tooltip={TIPS.symbol}>Symbol</FieldLabel>
              <select className="form-control" value={form.symbol}
                onChange={e => setForm(prev => ({ ...prev, symbol: e.target.value }))}>
                {PAIRS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div className="form-group">
              <FieldLabel tooltip={TIPS.side}>Side</FieldLabel>
              <select className="form-control" value={form.side}
                onChange={e => setForm(prev => ({ ...prev, side: e.target.value }))}>
                <option value="long">Long</option>
                <option value="short">Short</option>
              </select>
            </div>
            <div className="form-group">
              <FieldLabel tooltip={TIPS.entryPrice} required>Entry Price</FieldLabel>
              <input type="number" className="form-control" value={form.entry_price} step="0.01"
                onChange={e => setForm(prev => ({ ...prev, entry_price: e.target.value }))} />
            </div>
            <div className="form-group">
              <FieldLabel tooltip={TIPS.quantity} required>Quantity</FieldLabel>
              <input type="number" className="form-control" value={form.quantity} step="0.0001"
                onChange={e => setForm(prev => ({ ...prev, quantity: e.target.value }))} />
            </div>
            <div className="form-group">
              <FieldLabel tooltip={TIPS.fees}>Fees</FieldLabel>
              <input type="number" className="form-control" value={form.fees} step="0.01"
                onChange={e => setForm(prev => ({ ...prev, fees: e.target.value }))} />
            </div>
          </div>
          <div className="form-group" style={{ marginTop: 'var(--space-3)' }}>
            <FieldLabel tooltip={TIPS.notes}>Notes</FieldLabel>
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
        <EmptyState
          icon="💵"
          title="No real trades registered"
          description="Cuando ejecutes un trade real en tu exchange, regístralo aquí para comparar con el sim trade equivalente."
        />
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="trade-table">
            <thead>
              <tr>
                <th className="ta-right">ID</th>
                <th className="ta-right">Sim #</th>
                <th>Symbol</th>
                <th>Side</th>
                <th className="ta-right">Entry</th>
                <th className="ta-right">Exit</th>
                <th className="ta-right">PnL</th>
                <th className="ta-right">Fees</th>
                <th>Status</th>
                <th>Notes</th>
                <th className="ta-center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {realTrades.map(t => {
                const pnlColor = t.pnl > 0 ? 'var(--color-success)' : t.pnl < 0 ? 'var(--color-danger)' : 'var(--text-secondary)'
                return (
                  <React.Fragment key={t.id}>
                  <tr style={{
                    transition: 'background 300ms ease',
                    background: t.id === justClosedId ? 'rgba(34,197,94,0.18)' : undefined,
                  }}>
                    <td className="ta-right num-col" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{t.id}</td>
                    <td className="ta-right num-col">{t.sim_trade_id || '—'}</td>
                    <td>{t.symbol}</td>
                    <td style={{ color: t.side === 'long' ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                      {t.side?.toUpperCase()}
                    </td>
                    <td className="ta-right num-col">{fmtNum(t.entry_price, 4)}</td>
                    <td className="ta-right num-col">{t.exit_price ? fmtNum(t.exit_price, 4) : '—'}</td>
                    <td className="ta-right num-col" style={{ color: pnlColor, fontWeight: 600 }}>{t.pnl != null ? fmtMoney(t.pnl) : '—'}</td>
                    <td className="ta-right num-col">{fmtNum(t.fees, 2)}</td>
                    <td><StatusBadge status={t.status} /></td>
                    <td style={{ fontSize: '0.8rem', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.notes || '—'}
                    </td>
                    <td className="ta-center">
                      {t.status === 'open' && closingId !== t.id && (
                        <button className="btn btn-sm" onClick={() => openCloseForm(t)}
                          style={{ background: 'var(--color-warning)', color: '#000', fontSize: '0.75rem', padding: '2px 8px' }}>
                          Close
                        </button>
                      )}
                      {closingId === t.id && (
                        <button className="btn btn-sm btn-secondary" onClick={() => setClosingId(null)}
                          style={{ fontSize: '0.75rem', padding: '2px 8px' }}>
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                  {closingId === t.id && (() => {
                    const s = computeCloseSummary(t)
                    return (
                      <tr key={`close-${t.id}`} style={{ background: 'var(--bg-elevated)' }}>
                        <td colSpan={11} style={{ padding: 'var(--space-3)' }}>
                          <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                            <div className="form-group" style={{ marginBottom: 0 }}>
                              <FieldLabel tooltip={TIPS.exitPrice} required style={{ fontSize: '0.75rem' }}>Exit Price</FieldLabel>
                              <input type="number" className="form-control" step="0.01" style={{ width: 130 }}
                                value={closeForm.exit_price}
                                onChange={e => setCloseForm(prev => ({ ...prev, exit_price: e.target.value }))} />
                            </div>
                            <div className="form-group" style={{ marginBottom: 0 }}>
                              <FieldLabel tooltip={TIPS.netPnl} required style={{ fontSize: '0.75rem' }}>Net PnL</FieldLabel>
                              <input type="number" className="form-control" step="0.01" style={{ width: 130 }}
                                value={closeForm.pnl}
                                onChange={e => setCloseForm(prev => ({ ...prev, pnl: e.target.value }))} />
                            </div>
                            <div className="form-group" style={{ marginBottom: 0 }}>
                              <FieldLabel tooltip={TIPS.exitTime} style={{ fontSize: '0.75rem' }}>Exit Time</FieldLabel>
                              <input type="datetime-local" className="form-control" style={{ width: 190 }}
                                value={closeForm.exit_time}
                                onChange={e => setCloseForm(prev => ({ ...prev, exit_time: e.target.value }))} />
                            </div>
                            <div className="form-group" style={{ marginBottom: 0 }}>
                              <FieldLabel tooltip={TIPS.notes} style={{ fontSize: '0.75rem' }}>Notes</FieldLabel>
                              <input type="text" className="form-control" style={{ width: 160 }}
                                value={closeForm.notes} placeholder="Optional"
                                onChange={e => setCloseForm(prev => ({ ...prev, notes: e.target.value }))} />
                            </div>
                            <button className="btn btn-sm btn-primary" onClick={() => handleClose(t)}
                              disabled={loading || !closeForm.exit_price || !closeForm.pnl}
                              style={{ fontSize: '0.75rem' }}>
                              {loading ? 'Closing…' : 'Confirm Close'}
                            </button>
                          </div>
                          {s && (
                            <div style={{ marginTop: 'var(--space-2)', display: 'flex', gap: 'var(--space-4)',
                                          flexWrap: 'wrap', fontSize: '0.8rem',
                                          fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                              <span>Invertido: {fmtMoney(s.invested)}</span>
                              <span>Gross: {fmtMoney(s.gross)}</span>
                              <span style={{ color: s.warn ? 'var(--color-warning)' : undefined }}>
                                Fee: {fmtMoney(s.fee)} ({(s.feePct * 100).toFixed(2)}%)
                              </span>
                            </div>
                          )}
                          {s?.warn && (
                            <div style={{ marginTop: 'var(--space-2)', padding: '6px 10px',
                                          background: 'rgba(234,179,8,0.1)',
                                          border: '1px solid rgba(234,179,8,0.25)',
                                          borderRadius: 'var(--radius-sm)',
                                          color: 'var(--color-warning)', fontSize: '0.8rem' }}>
                              {s.fee < 0
                                ? 'Fee negativo: el Net PnL que has puesto es mayor que el Gross teórico — revisa el signo o la escala.'
                                : `Fee = ${(s.feePct * 100).toFixed(2)}% del invertido: inusualmente alto. Revisa el Net PnL.`}
                            </div>
                          )}
                          {closeError && (
                            <div style={{ marginTop: 'var(--space-2)', padding: '6px 10px',
                                          background: 'rgba(239,68,68,0.1)',
                                          border: '1px solid rgba(239,68,68,0.25)',
                                          borderRadius: 'var(--radius-sm)',
                                          color: 'var(--color-danger)', fontSize: '0.8rem' }}>
                              {closeError}
                            </div>
                          )}
                        </td>
                      </tr>
                    )
                  })()}
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
