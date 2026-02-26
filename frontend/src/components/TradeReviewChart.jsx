export default function TradeReviewChart({ symbol, interval, startMs, endMs, trades }) {
  if (!trades || trades.length === 0) {
    return <div style={{ color: 'var(--text-muted)', padding: 'var(--space-4)' }}>No trades to review.</div>
  }

  return (
    <div style={{ color: 'var(--text-secondary)', padding: 'var(--space-4)', textAlign: 'center' }}>
      <p>Trade review chart for {symbol} ({interval}) — {trades.length} trades</p>
      <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Detailed chart visualization coming soon.</p>
    </div>
  )
}
