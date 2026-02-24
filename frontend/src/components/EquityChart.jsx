import {
  ResponsiveContainer,
  ComposedChart,
  AreaChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
} from 'recharts'

function formatDate(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
}

function formatMoney(v) {
  if (v === null || v === undefined) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(v)
}

function formatPct(v) {
  if (v === null || v === undefined) return '—'
  return (v * 100).toFixed(2) + '%'
}

function EquityTooltip({ active, payload, label }) {
  if (!active || !payload || payload.length === 0) return null
  return (
    <div className="custom-tooltip">
      <div className="custom-tooltip-label">{formatDate(label)}</div>
      {payload.map(p => (
        <div key={p.name} className="custom-tooltip-item">
          <div className="custom-tooltip-dot" style={{ background: p.color }} />
          <span style={{ color: 'var(--text-muted)', minWidth: 70 }}>{p.name}</span>
          <span>{p.name === 'Equity' ? formatMoney(p.value) : formatPct(p.value)}</span>
        </div>
      ))}
    </div>
  )
}

function DrawdownTooltip({ active, payload, label }) {
  if (!active || !payload || payload.length === 0) return null
  return (
    <div className="custom-tooltip">
      <div className="custom-tooltip-label">{formatDate(label)}</div>
      {payload.map(p => (
        <div key={p.name} className="custom-tooltip-item">
          <div className="custom-tooltip-dot" style={{ background: p.color }} />
          <span style={{ color: 'var(--text-muted)', minWidth: 80 }}>Drawdown</span>
          <span style={{ color: 'var(--color-danger)' }}>{formatPct(p.value)}</span>
        </div>
      ))}
    </div>
  )
}

export default function EquityChart({ equityCurve = [], drawdownCurve = [] }) {
  if (!equityCurve || equityCurve.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">📈</div>
        <div className="empty-state-title">No chart data</div>
        <div className="empty-state-text">Run a backtest to see the equity curve.</div>
      </div>
    )
  }

  // Both arrays are plain number[] — merge by index
  const chartData = equityCurve.map((eq, i) => ({
    ts: i,
    equity: eq,
    drawdown: (drawdownCurve[i] ?? 0) / 100,  // backend returns pct, chart uses ratio
  }))

  const initialEquity = chartData[0]?.equity ?? 1
  const minDrawdown   = Math.min(...chartData.map(d => d.drawdown))

  // Dynamic Y-axis formatter for equity
  const equityFmt = v => {
    if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
    if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
    return `$${v.toFixed(0)}`
  }

  const pctFmt = v => `${(v * 100).toFixed(1)}%`

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>

      {/* Equity curve */}
      <div>
        <div className="section-title">Equity Curve</div>
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 10, bottom: 0 }}>
            <defs>
              <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
            <XAxis
              dataKey="ts"
              tickFormatter={formatDate}
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              axisLine={{ stroke: 'var(--border-subtle)' }}
              tickLine={false}
              minTickGap={60}
            />
            <YAxis
              tickFormatter={equityFmt}
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={72}
            />
            <Tooltip content={<EquityTooltip />} />
            <ReferenceLine
              y={initialEquity}
              stroke="var(--border-default)"
              strokeDasharray="4 4"
              strokeWidth={1}
            />
            <Area
              type="monotone"
              dataKey="equity"
              name="Equity"
              stroke="#3b82f6"
              strokeWidth={2}
              fill="url(#equityGrad)"
              dot={false}
              activeDot={{ r: 4, fill: '#3b82f6', stroke: '#fff', strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Drawdown */}
      <div>
        <div className="section-title">Drawdown</div>
        <ResponsiveContainer width="100%" height={160}>
          <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 10, bottom: 0 }}>
            <defs>
              <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#ef4444" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#ef4444" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
            <XAxis
              dataKey="ts"
              tickFormatter={formatDate}
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              axisLine={{ stroke: 'var(--border-subtle)' }}
              tickLine={false}
              minTickGap={60}
            />
            <YAxis
              tickFormatter={pctFmt}
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={56}
              domain={[minDrawdown * 1.1, 0]}
            />
            <Tooltip content={<DrawdownTooltip />} />
            <ReferenceLine y={0} stroke="var(--border-default)" strokeWidth={1} />
            <Area
              type="monotone"
              dataKey="drawdown"
              name="Drawdown"
              stroke="#ef4444"
              strokeWidth={1.5}
              fill="url(#ddGrad)"
              dot={false}
              activeDot={{ r: 3, fill: '#ef4444', stroke: '#fff', strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
