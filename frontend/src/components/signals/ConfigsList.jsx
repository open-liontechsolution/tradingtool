import { ToggleSwitch } from './ToggleSwitch'
import EmptyState from '../EmptyState'
import { fmtMoney } from './helpers'

export function ConfigsList({ configs, onToggle, onToggleTelegram, onDelete, onResetEquity }) {
  if (!configs || configs.length === 0) {
    return (
      <EmptyState
        icon="⚙️"
        title="No signal configs yet"
        description="Crea una configuración abajo para que el motor empiece a evaluar señales."
      />
    )
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="trade-table">
        <thead>
          <tr>
            <th className="ta-right">ID</th>
            <th>Pair</th>
            <th>Interval</th>
            <th>Strategy</th>
            <th className="ta-right" title="Capital inicial al crear la config (inmutable salvo edición manual)">Inicial</th>
            <th className="ta-right" title="Capital actual: arranca = inicial y evoluciona con el PnL neto de cada sim-trade cerrado. Se usa para dimensionar nuevos trades.">Actual</th>
            <th className="ta-right">Δ%</th>
            <th className="ta-center">Active</th>
            <th className="ta-center" title="Enviar alertas a Telegram para esta configuración">Telegram</th>
            <th className="ta-center">Actions</th>
          </tr>
        </thead>
        <tbody>
          {configs.map(c => {
            const initial = Number(c.initial_portfolio ?? 0)
            const current = Number(c.current_portfolio ?? initial)
            const deltaPct = initial > 0 ? ((current - initial) / initial) * 100 : 0
            const deltaColor = deltaPct > 0 ? 'var(--color-success)' : deltaPct < 0 ? 'var(--color-danger)' : 'var(--text-muted)'
            const blown = c.status === 'blown'
            return (
              <tr key={c.id} style={blown ? { opacity: 0.7 } : undefined}>
                <td className="ta-right num-col" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>
                  {c.id}
                  {blown && (
                    <span
                      title={`Blown at ${c.blown_at ?? ''}`}
                      aria-label={`Account blown at ${c.blown_at ?? 'unknown date'}`}
                      style={{
                        marginLeft: 6, padding: '1px 6px', borderRadius: 'var(--radius-sm)',
                        background: 'rgba(239,68,68,0.2)', color: 'var(--color-danger)',
                        fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.03em',
                      }}>
                      <span aria-hidden="true">⚠ </span>BLOWN
                    </span>
                  )}
                </td>
                <td>{c.symbol}</td>
                <td>{c.interval}</td>
                <td>{c.strategy}</td>
                <td className="ta-right num-col">{fmtMoney(initial)}</td>
                <td className="ta-right num-col" style={{ color: deltaColor, fontWeight: 600 }}>{fmtMoney(current)}</td>
                <td className="ta-right num-col" style={{ color: deltaColor }}>
                  {deltaPct === 0 ? '—' : (deltaPct > 0 ? '+' : '') + deltaPct.toFixed(2) + '%'}
                </td>
                <td className="ta-center">
                  <ToggleSwitch
                    checked={c.active}
                    onChange={(val) => onToggle(c.id, val)}
                    disabled={blown}
                    ariaLabel={`Activar config ${c.id} (${c.symbol} ${c.strategy})`}
                  />
                </td>
                <td className="ta-center">
                  <ToggleSwitch
                    checked={!!c.telegram_enabled}
                    onChange={(val) => onToggleTelegram(c.id, val)}
                    ariaLabel={`Notificaciones Telegram para config ${c.id} (${c.symbol} ${c.strategy})`}
                  />
                </td>
                <td className="ta-center" style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                  {blown && (
                    <button className="btn btn-sm btn-secondary"
                      title="Restore current_portfolio = initial_portfolio and reactivate the config"
                      onClick={() => { if (confirm('Reset equity to initial and reactivate this config?')) onResetEquity(c.id) }}>
                      Reset equity
                    </button>
                  )}
                  <button className="btn btn-sm btn-secondary" style={{ color: 'var(--color-danger)' }}
                    onClick={() => { if (confirm('Delete this config? Open trades will be closed.')) onDelete(c.id) }}>
                    Delete
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
