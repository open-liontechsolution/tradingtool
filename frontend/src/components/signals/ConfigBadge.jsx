import { fmtConfigParams } from './helpers'

export function ConfigBadge({ configId, strategy, params }) {
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

export function StatusBadge({ status }) {
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
