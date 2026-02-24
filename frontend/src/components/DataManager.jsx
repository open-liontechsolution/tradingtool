import { useState, useEffect, useRef, useCallback } from 'react'

const PAIRS = [
  'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
  'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'MATICUSDT',
]

const INTERVALS = [
  { value: '1h',  label: '1 Hour' },
  { value: '4h',  label: '4 Hours' },
  { value: '1d',  label: '1 Day' },
  { value: '1w',  label: '1 Week' },
  { value: '1M',  label: '1 Month' },
]

const DEFAULT_START = () => {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 2)
  return d.toISOString().split('T')[0]
}

const DEFAULT_END = () => new Date().toISOString().split('T')[0]

// Format timestamp (ms) to readable date
function fmtDate(ms) {
  return new Date(ms).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

function classifyLog(text) {
  const t = text.toLowerCase()
  if (t.includes('error') || t.includes('failed') || t.includes('fail')) return 'log-error'
  if (t.includes('warn') || t.includes('429') || t.includes('418') || t.includes('backoff')) return 'log-warn'
  if (t.includes('complete') || t.includes('success') || t.includes('done') || t.includes('finish')) return 'log-ok'
  if (t.includes('start') || t.includes('download') || t.includes('gap') || t.includes('batch')) return 'log-info'
  return ''
}

function RateLimitIndicator({ weight, limit, status }) {
  const pct = limit > 0 ? Math.min(100, (weight / limit) * 100) : 0
  const barColor = pct > 80 ? 'var(--color-danger)' : pct > 50 ? 'var(--color-warning)' : 'var(--color-success)'

  let badgeClass = 'badge-ok'
  let badgeLabel = 'OK'
  if (status === 'blocked') { badgeClass = 'badge-danger'; badgeLabel = 'Blocked' }
  else if (status === 'backoff') { badgeClass = 'badge-warning'; badgeLabel = 'Backoff' }
  else if (pct > 80) { badgeClass = 'badge-danger'; badgeLabel = 'High Load' }
  else if (pct > 50) { badgeClass = 'badge-warning'; badgeLabel = 'Warning' }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
          Binance API Weight
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
            {weight} / {limit}
          </span>
          <span className={`badge ${badgeClass}`}>
            <span className={`dot${status === 'downloading' ? ' dot-pulse' : ''}`}></span>
            {badgeLabel}
          </span>
        </div>
      </div>
      <div style={{ background: 'var(--bg-elevated)', borderRadius: 99, height: 8, overflow: 'hidden', border: '1px solid var(--border-subtle)' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: barColor, borderRadius: 99, transition: 'width 0.5s ease, background-color 0.5s ease' }} />
      </div>
    </div>
  )
}

function ProgressSection({ job, onCancel }) {
  if (!job) return null

  const pct = job.progress_pct ?? 0
  const isActive = job.status === 'running' || job.status === 'queued'

  return (
    <div className="card mt-4" style={{ border: '1px solid var(--border-default)' }}>
      <div className="card-header">
        <span className="card-title">
          {isActive && <span className="spinner" style={{ width: 14, height: 14 }}></span>}
          Download Progress — {job.symbol} {job.interval}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={`badge ${
            job.status === 'completed'  ? 'badge-ok' :
            job.status === 'running'    ? 'badge-info' :
            job.status === 'failed'     ? 'badge-danger' :
            job.status === 'cancelled'  ? 'badge-muted' : 'badge-muted'
          }`}>
            {job.status}
          </span>
          {isActive && (
            <button className="btn btn-danger btn-sm" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </div>
      <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>

        {/* Stats row */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 'var(--space-3)' }}>
          <div className="metric-card">
            <span className="metric-label">Progress</span>
            <span className="metric-value">{pct.toFixed(1)}%</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Candles</span>
            <span className="metric-value">{(job.candles_downloaded ?? 0).toLocaleString()}</span>
            <span className="metric-sub">of {(job.candles_expected ?? 0).toLocaleString()} expected</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Gaps Found</span>
            <span className={`metric-value ${(job.gaps_found ?? 0) > 0 ? 'negative' : 'neutral'}`}>
              {job.gaps_found ?? 0}
            </span>
          </div>
        </div>

        {/* Progress bar */}
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              {job.start_time ? fmtDate(job.start_time) : '—'}
            </span>
            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              {job.end_time ? fmtDate(job.end_time) : '—'}
            </span>
          </div>
          <div className="progress-bar-wrap">
            <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>

        {/* Event log */}
        {job.log && job.log.length > 0 && (
          <div>
            <div className="section-title">Event Log</div>
            <EventLog entries={job.log} />
          </div>
        )}
      </div>
    </div>
  )
}

function fmtDateShort(ms) {
  return new Date(ms).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

function CoverageTable({ coverage, selectedSymbol, selectedInterval, onSelect }) {
  if (!coverage) return null

  return (
    <div className="card mt-4">
      <div className="card-header">
        <span className="card-title">Local Data Coverage</span>
        <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>{coverage.length} dataset{coverage.length !== 1 ? 's' : ''} stored</span>
      </div>
      <div className="card-body" style={{ padding: 0 }}>
        {coverage.length === 0 ? (
          <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            No data downloaded yet. Use the form above to download your first dataset.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  {['Pair', 'Interval', 'Candles', 'From', 'To'].map(h => (
                    <th key={h} style={{ padding: '8px 16px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {coverage.map((row, i) => {
                  const isActive = row.symbol === selectedSymbol && row.interval === selectedInterval
                  return (
                    <tr
                      key={i}
                      onClick={() => onSelect(row.symbol, row.interval)}
                      style={{
                        borderBottom: '1px solid var(--border-subtle)',
                        background: isActive ? 'rgba(99,102,241,0.08)' : 'transparent',
                        cursor: 'pointer',
                        transition: 'background 0.15s',
                      }}
                      onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)' }}
                      onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
                    >
                      <td style={{ padding: '8px 16px', fontWeight: isActive ? 600 : 400, color: isActive ? 'var(--color-primary)' : 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>{row.symbol}</td>
                      <td style={{ padding: '8px 16px', fontFamily: 'var(--font-mono)' }}>{row.interval}</td>
                      <td style={{ padding: '8px 16px', color: 'var(--text-secondary)' }}>{row.count.toLocaleString()}</td>
                      <td style={{ padding: '8px 16px', color: 'var(--text-muted)' }}>{fmtDateShort(row.from_ms)}</td>
                      <td style={{ padding: '8px 16px', color: 'var(--text-muted)' }}>{fmtDateShort(row.to_ms)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function EventLog({ entries }) {
  const ref = useRef(null)

  useEffect(() => {
    if (ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight
    }
  }, [entries])

  return (
    <div className="event-log" ref={ref}>
      {entries.map((entry, i) => {
        const text = typeof entry === 'string' ? entry : JSON.stringify(entry)
        return (
          <div key={i} className={`log-entry ${classifyLog(text)}`}>
            {text}
          </div>
        )
      })}
    </div>
  )
}

export default function DataManager() {
  const [symbol, setSymbol]     = useState('BTCUSDT')
  const [interval, setSelectedInterval] = useState('1d')
  const [startDate, setStart]   = useState(DEFAULT_START())
  const [endDate, setEnd]       = useState(DEFAULT_END())

  const [activeJobId, setActiveJobId] = useState(null)
  const [jobData, setJobData]         = useState(null)
  const [rateLimit, setRateLimit]     = useState({ weight_used: 0, weight_limit: 1200, status: 'ok' })
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError]             = useState(null)
  const [metricsStatus, setMetricsStatus] = useState(null)
  const [computingMetrics, setComputingMetrics] = useState(false)
  const [coverage, setCoverage] = useState(null)

  const pollRef = useRef(null)

  // Poll rate limit
  const fetchRateLimit = useCallback(async () => {
    try {
      const res = await fetch('/api/rate-limit')
      if (!res.ok) return
      const data = await res.json()
      setRateLimit(data)
    } catch {
      // ignore
    }
  }, [])

  // Fetch data coverage
  const fetchCoverage = useCallback(async () => {
    try {
      const res = await fetch('/api/coverage')
      if (!res.ok) return
      const data = await res.json()
      setCoverage(data.coverage)
    } catch {
      // ignore
    }
  }, [])

  // Fetch metrics status
  const fetchMetricsStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/metrics/status')
      if (!res.ok) return
      const data = await res.json()
      setMetricsStatus(data)
    } catch {
      // ignore
    }
  }, [])

  // Poll job status
  const pollJob = useCallback(async (jobId) => {
    try {
      const res = await fetch(`/api/download/${jobId}`)
      if (!res.ok) return
      const data = await res.json()
      setJobData(data)
      if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
        clearInterval(pollRef.current)
        pollRef.current = null
        fetchRateLimit()
        fetchCoverage()
      }
    } catch {
      // ignore network errors during polling
    }
  }, [fetchRateLimit, fetchCoverage])

  // Start polling when job is active
  useEffect(() => {
    if (activeJobId) {
      pollJob(activeJobId)
      pollRef.current = setInterval(() => pollJob(activeJobId), 1500)
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [activeJobId, pollJob])

  // Fetch coverage on mount
  useEffect(() => { fetchCoverage() }, [fetchCoverage])

  // Fetch rate limit periodically
  useEffect(() => {
    fetchRateLimit()
    const id = setInterval(fetchRateLimit, 5000)
    return () => clearInterval(id)
  }, [fetchRateLimit])

  const handleDownload = async () => {
    if (!symbol || !interval || !startDate || !endDate) return
    const startMs = Date.parse(startDate)
    const endMs   = Date.parse(endDate)
    if (isNaN(startMs) || isNaN(endMs)) {
      setError('Invalid date range — please select valid start and end dates.')
      return
    }
    if (endMs <= startMs) {
      setError('End date must be after start date.')
      return
    }
    setError(null)
    setIsSubmitting(true)
    setJobData(null)
    setActiveJobId(null)

    try {
      const res = await fetch('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          interval,
          start_time: startMs,
          end_time: endMs,
        }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setError(err.detail ?? `HTTP ${res.status}`)
        return
      }

      const data = await res.json()
      setActiveJobId(data.job_id)
    } catch (e) {
      setError(e.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleCancel = async () => {
    if (!activeJobId) return
    try {
      await fetch(`/api/download/${activeJobId}/cancel`)
    } catch {
      // ignore
    }
  }

  const handleComputeMetrics = async () => {
    setComputingMetrics(true)
    try {
      await fetch('/api/metrics/compute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, interval }),
      })
      await fetchMetricsStatus()
    } catch {
      // ignore
    } finally {
      setComputingMetrics(false)
    }
  }

  const isRunning = jobData?.status === 'running' || jobData?.status === 'queued'
  const isCompleted = jobData?.status === 'completed'

  return (
    <div className="panel-section">

      {/* Config card */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Data Configuration</span>
          <RateLimitIndicator
            weight={rateLimit.weight_used ?? 0}
            limit={rateLimit.weight_limit ?? 1200}
            status={isRunning ? 'downloading' : (rateLimit.status ?? 'ok')}
          />
        </div>
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>

          <div className="grid-2">
            {/* Pair selector */}
            <div className="form-group">
              <label className="form-label">Trading Pair</label>
              <select
                className="form-control"
                value={symbol}
                onChange={e => setSymbol(e.target.value)}
                disabled={isRunning}
              >
                {PAIRS.map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>

            {/* Interval selector */}
            <div className="form-group">
              <label className="form-label">Interval</label>
              <select
                className="form-control"
                value={interval}
                onChange={e => setSelectedInterval(e.target.value)}
                disabled={isRunning}
              >
                {INTERVALS.map(iv => (
                  <option key={iv.value} value={iv.value}>{iv.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid-2">
            {/* Start date */}
            <div className="form-group">
              <label className="form-label">Start Date</label>
              <input
                type="date"
                className="form-control"
                value={startDate}
                max={endDate}
                onChange={e => setStart(e.target.value)}
                disabled={isRunning}
              />
            </div>

            {/* End date */}
            <div className="form-group">
              <label className="form-label">End Date</label>
              <input
                type="date"
                className="form-control"
                value={endDate}
                min={startDate}
                max={DEFAULT_END()}
                onChange={e => setEnd(e.target.value)}
                disabled={isRunning}
              />
            </div>
          </div>

          {/* Error */}
          {error && (
            <div style={{ padding: '8px 12px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)', borderRadius: 'var(--radius-sm)', color: 'var(--color-danger)', fontSize: '0.83rem' }}>
              {error}
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}>
            <button
              className="btn btn-primary btn-lg"
              onClick={handleDownload}
              disabled={isRunning || isSubmitting}
            >
              {isRunning ? (
                <><span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span> Downloading…</>
              ) : isSubmitting ? (
                <><span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span> Starting…</>
              ) : (
                'Download / Update'
              )}
            </button>

            {isCompleted && (
              <button
                className="btn btn-secondary"
                onClick={handleComputeMetrics}
                disabled={computingMetrics}
              >
                {computingMetrics ? (
                  <><span className="spinner"></span> Computing…</>
                ) : (
                  'Calculate Derived Metrics'
                )}
              </button>
            )}
          </div>

          {/* Metrics status */}
          {metricsStatus && (
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className={`badge ${metricsStatus.status === 'computed' ? 'badge-ok' : 'badge-muted'}`}>
                {metricsStatus.status}
              </span>
              {metricsStatus.metrics_count && `${metricsStatus.metrics_count} metrics computed`}
            </div>
          )}
        </div>
      </div>

      {/* Progress section */}
      <ProgressSection job={jobData} onCancel={handleCancel} />

      {/* Coverage table */}
      <CoverageTable
        coverage={coverage}
        selectedSymbol={symbol}
        selectedInterval={interval}
        onSelect={(sym, iv) => { setSymbol(sym); setSelectedInterval(iv) }}
      />

    </div>
  )
}
