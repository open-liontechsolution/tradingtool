import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { apiFetch } from '../../auth/apiFetch'
import TradeReviewChart from '../TradeReviewChart'
import EmptyState from '../EmptyState'
import Skeleton from '../Skeleton'
import { ConfigBadge, StatusBadge } from './ConfigBadge'
import { fmtNum, fmtMoney } from './helpers'

const STORAGE_KEY = 'signalsTableMode'

function loadInitialMode() {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY)
    return v === 'detailed' ? 'detailed' : 'compact'
  } catch {
    return 'compact'
  }
}

export function SimTradesList({ trades, onClose }) {
  const [expandedId, setExpandedId] = useState(null)
  const [movesByTrade, setMovesByTrade] = useState({})
  const [loadingMoves, setLoadingMoves] = useState(false)
  const [viewMode, setViewMode] = useState(loadInitialMode)

  useEffect(() => {
    try { window.localStorage.setItem(STORAGE_KEY, viewMode) } catch { /* ignore */ }
  }, [viewMode])

  const toggleExpand = useCallback(async (tradeId) => {
    if (expandedId === tradeId) {
      setExpandedId(null)
      return
    }
    setExpandedId(tradeId)
    if (movesByTrade[tradeId] !== undefined) return
    setLoadingMoves(true)
    try {
      const res = await apiFetch(`/api/sim-trades/${tradeId}/stop-moves`)
      if (res.ok) {
        const data = await res.json()
        setMovesByTrade(prev => ({ ...prev, [tradeId]: data.stop_moves ?? [] }))
      } else {
        setMovesByTrade(prev => ({ ...prev, [tradeId]: [] }))
      }
    } catch {
      setMovesByTrade(prev => ({ ...prev, [tradeId]: [] }))
    } finally {
      setLoadingMoves(false)
    }
  }, [expandedId, movesByTrade])

  if (!trades || trades.length === 0) {
    return (
      <EmptyState
        icon="📊"
        title="No sim trades yet"
        description="Activa una configuración en Configurations y espera a que el motor de señales abra un trade simulado."
      />
    )
  }

  const detailed = viewMode === 'detailed'
  const colCount = detailed ? 15 : 10

  return (
    <div>
      <div style={{
        display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 'var(--space-2)',
        marginBottom: 'var(--space-2)', fontSize: '0.78rem', color: 'var(--text-muted)',
      }}>
        <span>View:</span>
        <div className="toggle-group" role="tablist" aria-label="Table density">
          <button
            type="button"
            role="tab"
            aria-selected={!detailed}
            className={`toggle-option${!detailed ? ' active' : ''}`}
            onClick={() => setViewMode('compact')}
          >Compact</button>
          <button
            type="button"
            role="tab"
            aria-selected={detailed}
            className={`toggle-option${detailed ? ' active' : ''}`}
            onClick={() => setViewMode('detailed')}
          >Detailed</button>
        </div>
      </div>

      <div style={{ overflowX: 'auto', maxHeight: '70vh', overflowY: 'auto' }}>
        <table className="trade-table trade-table--sticky">
          <thead>
            <tr>
              <th style={{ width: '2rem' }}></th>
              <th className="ta-right">ID</th>
              <th>Config</th>
              <th>Symbol</th>
              <th>Side</th>
              {detailed && (
                <th className="ta-right" title="Capital usado al abrir este trade (snapshot del current_portfolio en ese momento)">Cap@entry</th>
              )}
              <th className="ta-right">Entry{!detailed && <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}> / Stop</span>}</th>
              {detailed && <th className="ta-right">Stop</th>}
              {detailed && (
                <th className="ta-right" title="Precio de liquidación. NULL en trades sin apalancamiento. Se cierra automáticamente aquí en vez de en el stop si el precio cruza primero.">Liq</th>
              )}
              <th className="ta-right">Exit</th>
              {detailed && <th>Reason</th>}
              <th className="ta-right">PnL{!detailed && <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}> / %</span>}</th>
              {detailed && <th className="ta-right">PnL %</th>}
              <th>Status</th>
              <th className="ta-center">Actions</th>
            </tr>
          </thead>
          <tbody>
            {trades.map(t => {
              const pnlColor = t.pnl > 0 ? 'var(--color-success)' : t.pnl < 0 ? 'var(--color-danger)' : 'var(--text-secondary)'
              const isExpanded = expandedId === t.id
              const moves = movesByTrade[t.id]
              return (
                <React.Fragment key={t.id}>
                  <tr>
                    <td className="ta-center">
                      <button
                        type="button"
                        onClick={() => toggleExpand(t.id)}
                        title="Show stop-move history"
                        style={{
                          background: 'transparent', border: 'none', cursor: 'pointer',
                          color: 'var(--text-muted)', padding: '0.25rem',
                        }}
                      >{isExpanded ? '▾' : '▸'}</button>
                    </td>
                    <td className="ta-right num-col" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{t.id}</td>
                    <td>
                      <ConfigBadge configId={t.config_id} strategy={t.config_strategy} params={t.config_params} />
                    </td>
                    <td>{t.symbol}</td>
                    <td style={{ color: t.side === 'long' ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                      {t.side?.toUpperCase()}
                    </td>
                    {detailed && (
                      <td className="ta-right num-col" style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{t.portfolio != null ? fmtMoney(t.portfolio) : '—'}</td>
                    )}
                    {detailed ? (
                      <td className="ta-right num-col">{t.entry_price ? fmtNum(t.entry_price, 4) : '—'}</td>
                    ) : (
                      <td className="ta-right num-col">
                        <div className="cell-stack">
                          <span className="cell-stack__primary">{t.entry_price ? fmtNum(t.entry_price, 4) : '—'}</span>
                          <span className="cell-stack__secondary">{fmtNum(t.stop_base, 4)}{t.liquidation_price != null && <span style={{ color: 'var(--color-warning)' }}> · liq {fmtNum(t.liquidation_price, 4)}</span>}</span>
                        </div>
                      </td>
                    )}
                    {detailed && <td className="ta-right num-col">{fmtNum(t.stop_base, 4)}</td>}
                    {detailed && (
                      <td className="ta-right num-col" style={{ color: t.liquidation_price != null ? 'var(--color-warning)' : 'var(--text-muted)', fontSize: '0.85rem' }}>
                        {t.liquidation_price != null ? fmtNum(t.liquidation_price, 4) : '—'}
                      </td>
                    )}
                    <td className="ta-right num-col">{t.exit_price ? fmtNum(t.exit_price, 4) : '—'}</td>
                    {detailed && <td>{t.exit_reason || '—'}</td>}
                    {detailed ? (
                      <td className="ta-right num-col" style={{ color: pnlColor, fontWeight: 600 }}>
                        {t.pnl != null ? (t.pnl > 0 ? '+' : '') + fmtMoney(t.pnl) : '—'}
                      </td>
                    ) : (
                      <td className="ta-right num-col" style={{ color: pnlColor }}>
                        <div className="cell-stack">
                          <span className="cell-stack__primary" style={{ fontWeight: 600 }}>
                            {t.pnl != null ? (t.pnl > 0 ? '+' : '') + fmtMoney(t.pnl) : '—'}
                          </span>
                          <span className="cell-stack__secondary" style={{ color: pnlColor, opacity: 0.85 }}>
                            {t.pnl_pct != null ? (t.pnl_pct > 0 ? '+' : '') + fmtNum(t.pnl_pct * 100, 2) + '%' : ''}
                          </span>
                        </div>
                      </td>
                    )}
                    {detailed && (
                      <td className="ta-right num-col" style={{ color: pnlColor }}>
                        {t.pnl_pct != null ? (t.pnl_pct > 0 ? '+' : '') + fmtNum(t.pnl_pct * 100, 2) + '%' : '—'}
                      </td>
                    )}
                    <td><StatusBadge status={t.status} /></td>
                    <td className="ta-center">
                      {t.status === 'open' && (
                        <button className="btn btn-sm btn-secondary" onClick={() => onClose(t.id)}>Close</button>
                      )}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr>
                      <td colSpan={colCount} style={{ background: 'var(--surface-1)', padding: 'var(--space-3)' }}>
                        <SimTradeReview trade={t} moves={moves} loadingMoves={loadingMoves && moves === undefined} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function SimTradeReview({ trade, moves, loadingMoves }) {
  const [nowMs] = useState(() => Date.now())

  const { startMs, endMs } = useMemo(() => {
    const stepBy = {
      '1h': 60 * 60 * 1000,
      '2h': 2 * 60 * 60 * 1000,
      '4h': 4 * 60 * 60 * 1000,
      '6h': 6 * 60 * 60 * 1000,
      '8h': 8 * 60 * 60 * 1000,
      '12h': 12 * 60 * 60 * 1000,
      '1d': 24 * 60 * 60 * 1000,
      '3d': 3 * 24 * 60 * 60 * 1000,
      '1w': 7 * 24 * 60 * 60 * 1000,
      '1M': 30 * 24 * 60 * 60 * 1000,
    }
    const STEP_MS = stepBy[trade.interval] || 24 * 60 * 60 * 1000
    const entryMs = Number(trade.entry_time) || nowMs
    const exitMs = Number(trade.exit_time) || nowMs
    return {
      startMs: Math.max(0, entryMs - 50 * STEP_MS),
      endMs: exitMs + 10 * STEP_MS,
    }
  }, [trade.interval, trade.entry_time, trade.exit_time, nowMs])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      {trade.entry_time && (
        <TradeReviewChart
          symbol={trade.symbol}
          interval={trade.interval}
          startMs={startMs}
          endMs={endMs}
          trades={[{
            id: trade.id,
            side: trade.side,
            entry_time: trade.entry_time,
            exit_time: trade.exit_time ?? endMs,
            entry_price: trade.entry_price,
            exit_price: trade.exit_price,
            pnl: trade.pnl,
            exit_reason: trade.exit_reason,
            stop_base: trade.stop_base,
          }]}
        />
      )}
      <StopMovesDetail moves={moves} loading={loadingMoves} />
    </div>
  )
}

function StopMovesDetail({ moves, loading }) {
  if (loading) {
    return (
      <div>
        <div style={{ fontWeight: 600, marginBottom: 'var(--space-2)' }}>Stop moves</div>
        <Skeleton rows={3} />
      </div>
    )
  }
  if (!moves || moves.length === 0) {
    return <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No stop moves for this trade.</div>
  }
  return (
    <div>
      <div style={{ fontWeight: 600, marginBottom: 'var(--space-2)' }}>Stop moves ({moves.length})</div>
      <table className="trade-table" style={{ fontSize: '0.8rem' }}>
        <thead>
          <tr>
            <th className="ta-right">#</th>
            <th className="ta-right">Stop anterior</th>
            <th className="ta-right">Stop nuevo</th>
            <th className="ta-right">Candle time</th>
            <th>Created at</th>
          </tr>
        </thead>
        <tbody>
          {moves.map((m, i) => (
            <tr key={m.id}>
              <td className="ta-right num-col">{i + 1}</td>
              <td className="ta-right num-col">{fmtNum(m.prev_stop_base, 4)}</td>
              <td className="ta-right num-col">{fmtNum(m.new_stop_base, 4)}</td>
              <td className="ta-right num-col">{new Date(m.candle_time).toISOString().replace('T', ' ').slice(0, 19)}</td>
              <td>{m.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
