import { ConfigBadge, StatusBadge } from './ConfigBadge'
import EmptyState from '../EmptyState'
import { fmtNum, fmtTime, fmtIso } from './helpers'

export function SignalsList({ signals }) {
  if (!signals || signals.length === 0) {
    return (
      <EmptyState
        icon="📡"
        title="No signals generated yet"
        description="El motor genera señales cuando una vela cierra cumpliendo las condiciones de la estrategia. Suele tardar al menos un cierre completo del intervalo configurado."
      />
    )
  }
  return (
    <div>
      <div style={{
        padding: 'var(--space-2) var(--space-3)',
        background: 'var(--bg-elevated)', borderRadius: 'var(--radius-sm)',
        marginBottom: 'var(--space-3)', fontSize: '0.76rem', color: 'var(--text-muted)',
        lineHeight: 1.6,
      }}>
        💡 <strong style={{ color: 'var(--text-secondary)' }}>Entry (next open)</strong>: precio al que entrar en el exchange &nbsp;·&nbsp;
        <strong style={{ color: 'var(--text-secondary)' }}>Stop (SL)</strong>: nivel de stop-loss para tu exchange — el sistema cierra aquí también el SimTrade.
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table className="trade-table">
          <thead>
            <tr>
              <th className="ta-right">ID</th>
              <th>Config</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Signal Candle</th>
              <th className="ta-right" title="Open price of the next candle — use this as your entry on the exchange">Entry (next open) ↗</th>
              <th className="ta-right" title="Strategy stop-loss level — set this as your SL on the exchange and the system closes the SimTrade here too">Stop (SL) 🛑</th>
              <th>Status</th>
              <th className="ta-center">Sim Trade</th>
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
                  <td className="ta-right num-col" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{s.id}</td>
                  <td>
                    <ConfigBadge configId={s.config_id} strategy={s.strategy} params={s.config_params} />
                  </td>
                  <td>{s.symbol}</td>
                  <td style={{ color: s.side === 'long' ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 700 }}>
                    {s.side?.toUpperCase()}
                  </td>
                  <td className="num-col" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem' }}>{fmtTime(s.trigger_candle_time)}</td>
                  <td className="ta-right num-col" style={{ color: entryColor, fontWeight: hasEntry ? 600 : 400 }}>
                    {hasEntry ? fmtNum(s.entry_price, 4) : 'Pending…'}
                  </td>
                  <td className="ta-right num-col" style={{ fontFamily: 'var(--font-mono)' }}>{fmtNum(s.stop_price, 4)}</td>
                  <td><StatusBadge status={s.status} /></td>
                  <td className="ta-center" style={{ fontSize: '0.78rem' }}>
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
