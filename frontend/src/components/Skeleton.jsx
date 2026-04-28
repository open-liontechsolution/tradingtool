export default function Skeleton({ rows = 3, height, width = '100%', style }) {
  if (rows === 1) {
    return (
      <span
        className="skeleton"
        aria-hidden="true"
        style={{ display: 'inline-block', height: height ?? '1em', width, ...style }}
      />
    )
  }
  return (
    <div role="status" aria-live="polite" aria-busy="true" style={style}>
      <span className="visually-hidden" style={{ position: 'absolute', left: -9999 }}>Loading…</span>
      {Array.from({ length: rows }).map((_, i) => (
        <span
          key={i}
          className="skeleton skeleton-row"
          aria-hidden="true"
          style={{ height: height ?? 20, width }}
        />
      ))}
    </div>
  )
}
