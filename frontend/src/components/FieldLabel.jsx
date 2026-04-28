export default function FieldLabel({ children, tooltip, required, htmlFor, style }) {
  return (
    <label
      htmlFor={htmlFor}
      className="form-label"
      style={{ display: 'flex', alignItems: 'center', gap: 4, ...style }}
    >
      {children}
      {required && <span aria-hidden="true" style={{ color: 'var(--color-danger)' }}>*</span>}
      {tooltip && (
        <span style={{ position: 'relative', display: 'inline-flex' }} className="param-help-wrap">
          <span
            aria-label="Field help"
            role="img"
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 14, height: 14, borderRadius: '50%',
              background: 'var(--border-default)', color: 'var(--text-muted)',
              fontSize: '0.65rem', fontWeight: 700, cursor: 'default', userSelect: 'none', flexShrink: 0,
            }}
          >?</span>
          <span className="param-tooltip">{tooltip}</span>
        </span>
      )}
    </label>
  )
}
