import { useState, useMemo } from 'react'

function fmtDate(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function fmtPrice(v) {
  if (v === null || v === undefined) return '—'
  const n = parseFloat(v)
  return new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

function fmtPnl(v) {
  if (v === null || v === undefined) return '—'
  const n = parseFloat(v)
  const sign = n >= 0 ? '+' : ''
  return sign + new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(n)
}

function fmtPct(v) {
  if (v === null || v === undefined) return '—'
  const n = parseFloat(v) * 100
  const sign = n >= 0 ? '+' : ''
  return sign + n.toFixed(2) + '%'
}

const COLUMNS = [
  { key: 'trade_num',     label: '#',           align: 'right' },
  { key: 'direction',     label: 'Side',        align: 'left'  },
  { key: 'entry_time',    label: 'Entry Time',  align: 'left'  },
  { key: 'entry_price',   label: 'Entry',       align: 'right' },
  { key: 'exit_time',     label: 'Exit Time',   align: 'left'  },
  { key: 'exit_price',    label: 'Exit',        align: 'right' },
  { key: 'exit_reason',   label: 'Reason',      align: 'left'  },
  { key: 'equity_before', label: 'Capital In',  align: 'right' },
  { key: 'equity_after',  label: 'Capital Out', align: 'right' },
  { key: 'pnl',           label: 'PnL',         align: 'right' },
  { key: 'pnl_pct',       label: 'PnL %',       align: 'right' },
  { key: 'fees',          label: 'Fees',        align: 'right' },
]

export default function TradeLog({ trades = [] }) {
  const [sortKey,  setSortKey]  = useState('trade_num')
  const [sortDir,  setSortDir]  = useState('asc')
  const [page,     setPage]     = useState(0)
  const PAGE_SIZE = 25

  const handleSort = key => {
    if (key === sortKey) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('asc') }
    setPage(0)
  }

  const sorted = useMemo(() => {
    if (!trades || trades.length === 0) return []
    return [...trades].sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey]
      if (av === null || av === undefined) av = sortDir === 'asc' ? Infinity : -Infinity
      if (bv === null || bv === undefined) bv = sortDir === 'asc' ? Infinity : -Infinity
      if (typeof av === 'string') av = av.toLowerCase()
      if (typeof bv === 'string') bv = bv.toLowerCase()
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ?  1 : -1
      return 0
    })
  }, [trades, sortKey, sortDir])

  const totalPages = Math.ceil(sorted.length / PAGE_SIZE)
  const visible = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  if (!trades || trades.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">📋</div>
        <div className="empty-state-title">No trades</div>
        <div className="empty-state-text">The backtest produced no completed trades for the selected parameters.</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          {sorted.length} trade{sorted.length !== 1 ? 's' : ''}
        </span>
        {totalPages > 1 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
            >
              Prev
            </button>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              {page + 1} / {totalPages}
            </span>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              disabled={page === totalPages - 1}
            >
              Next
            </button>
          </div>
        )}
      </div>

      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              {COLUMNS.map(col => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  style={{ textAlign: col.align }}
                >
                  {col.label}
                  {' '}
                  <span className={`sort-icon${sortKey === col.key ? ' active' : ''}`}>
                    {sortKey === col.key ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((trade, i) => {
              const pnl = parseFloat(trade.pnl ?? 0)
              const pnlClass = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''
              return (
                <tr key={trade.trade_num ?? i}>
                  <td style={{ textAlign: 'right', color: 'var(--text-muted)' }}>
                    {trade.trade_num ?? i + 1}
                  </td>
                  <td className="plain">
                    <span className={`badge ${trade.direction === 'long' ? 'badge-ok' : 'badge-warning'}`}
                      style={{ textTransform: 'capitalize' }}>
                      {trade.direction ?? '—'}
                    </span>
                  </td>
                  <td className="plain">{fmtDate(trade.entry_time)}</td>
                  <td style={{ textAlign: 'right' }}>{fmtPrice(trade.entry_price)}</td>
                  <td className="plain">{fmtDate(trade.exit_time)}</td>
                  <td style={{ textAlign: 'right' }}>{fmtPrice(trade.exit_price)}</td>
                  <td className="plain">
                    {trade.exit_reason
                      ? (() => {
                          const r = trade.exit_reason
                          const isStop = r === 'stop_long' || r === 'stop_short'
                          return (
                            <span className={`badge ${isStop ? 'badge-danger' : 'badge-ok'}`} style={{ fontSize: '0.72rem' }}>
                              {r}
                            </span>
                          )
                        })()
                      : '—'}
                  </td>
                  <td style={{ textAlign: 'right', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>
                    {trade.equity_before != null ? fmtPnl(trade.equity_before) : '—'}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }} className={pnlClass}>
                    {trade.equity_after != null ? fmtPnl(trade.equity_after) : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }} className={pnlClass}>
                    {fmtPnl(trade.pnl)}
                  </td>
                  <td style={{ textAlign: 'right' }} className={pnlClass}>
                    {fmtPct(trade.pnl_pct)}
                  </td>
                  <td style={{ textAlign: 'right', color: 'var(--color-warning)' }}>
                    {trade.fees ? fmtPnl(-Math.abs(trade.fees)) : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
