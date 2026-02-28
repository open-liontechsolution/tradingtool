import { useEffect, useRef, useState } from 'react'
import { createChart, ColorType } from 'lightweight-charts'

export default function TradeReviewChart({ symbol, interval, startMs, endMs, trades }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!symbol || !interval || !startMs || !endMs) return

    let cancelled = false

    const buildChart = async () => {
      setLoading(true)
      setError(null)

      try {
        const url = `/api/candles?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}&start=${startMs}&end=${endMs}&limit=10000`
        const res = await fetch(url)
        if (!res.ok) throw new Error(`Failed to fetch candles: HTTP ${res.status}`)
        const data = await res.json()
        const candles = data.candles ?? []

        if (cancelled) return
        if (candles.length === 0) {
          setError('No candle data available for this period. Make sure the data is downloaded first.')
          setLoading(false)
          return
        }

        // Clean up previous chart
        if (chartRef.current) {
          chartRef.current.remove()
          chartRef.current = null
        }

        const container = containerRef.current
        if (!container) return

        const chart = createChart(container, {
          width: container.clientWidth,
          height: 500,
          layout: {
            background: { type: ColorType.Solid, color: 'transparent' },
            textColor: 'rgba(180, 180, 195, 0.8)',
            fontSize: 11,
          },
          grid: {
            vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
            horzLines: { color: 'rgba(255, 255, 255, 0.04)' },
          },
          crosshair: {
            mode: 0,
          },
          timeScale: {
            timeVisible: true,
            secondsVisible: false,
            borderColor: 'rgba(255, 255, 255, 0.08)',
          },
          rightPriceScale: {
            borderColor: 'rgba(255, 255, 255, 0.08)',
          },
        })

        chartRef.current = chart

        // Candlestick series
        const candleSeries = chart.addCandlestickSeries({
          upColor: '#22c55e',
          downColor: '#ef4444',
          borderDownColor: '#ef4444',
          borderUpColor: '#22c55e',
          wickDownColor: '#ef4444',
          wickUpColor: '#22c55e',
        })

        const ohlcData = candles.map(c => ({
          time: Math.floor(Number(c.open_time) / 1000),
          open: parseFloat(c.open),
          high: parseFloat(c.high),
          low: parseFloat(c.low),
          close: parseFloat(c.close),
        }))

        candleSeries.setData(ohlcData)

        // Volume series
        const volumeSeries = chart.addHistogramSeries({
          priceFormat: { type: 'volume' },
          priceScaleId: 'volume',
        })

        chart.priceScale('volume').applyOptions({
          scaleMargins: { top: 0.85, bottom: 0 },
        })

        const volumeData = candles.map(c => ({
          time: Math.floor(Number(c.open_time) / 1000),
          value: parseFloat(c.volume),
          color: parseFloat(c.close) >= parseFloat(c.open)
            ? 'rgba(34, 197, 94, 0.15)'
            : 'rgba(239, 68, 68, 0.15)',
        }))

        volumeSeries.setData(volumeData)

        // Build markers from trades
        if (trades && trades.length > 0) {
          const markers = []

          for (const trade of trades) {
            const isLong = (trade.direction ?? trade.side) === 'long'
            const entryTime = Math.floor(Number(trade.entry_time) / 1000)
            const exitTime = Math.floor(Number(trade.exit_time) / 1000)
            const pnl = parseFloat(trade.pnl ?? 0)

            // Entry marker
            markers.push({
              time: entryTime,
              position: isLong ? 'belowBar' : 'aboveBar',
              color: isLong ? '#22c55e' : '#ef4444',
              shape: isLong ? 'arrowUp' : 'arrowDown',
              text: `${isLong ? 'Long' : 'Short'} @ ${parseFloat(trade.entry_price).toFixed(2)}`,
            })

            // Exit marker
            const exitReason = trade.exit_reason ?? 'exit'
            const _isStop = exitReason.includes('stop')
            markers.push({
              time: exitTime,
              position: isLong ? 'aboveBar' : 'belowBar',
              color: pnl >= 0 ? '#3b82f6' : '#f59e0b',
              shape: 'circle',
              text: `${exitReason} ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}`,
            })
          }

          // lightweight-charts requires markers sorted by time
          markers.sort((a, b) => a.time - b.time)
          candleSeries.setMarkers(markers)
        }

        chart.timeScale().fitContent()

        // Resize observer
        const ro = new ResizeObserver(entries => {
          if (entries.length === 0 || !chartRef.current) return
          const { width } = entries[0].contentRect
          chartRef.current.applyOptions({ width })
        })
        ro.observe(container)

        // Store cleanup ref
        chart._ro = ro
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    buildChart()

    return () => {
      cancelled = true
      if (chartRef.current) {
        if (chartRef.current._ro) chartRef.current._ro.disconnect()
        chartRef.current.remove()
        chartRef.current = null
      }
    }
  }, [symbol, interval, startMs, endMs, trades])

  if (!trades || trades.length === 0) {
    return (
      <div style={{ color: 'var(--text-muted)', padding: 'var(--space-4)', textAlign: 'center' }}>
        <p>No trades to review.</p>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div className="section-title" style={{ margin: 0 }}>
          Price Chart — {symbol} ({interval}) · {trades.length} trade{trades.length !== 1 ? 's' : ''}
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#22c55e', display: 'inline-block' }} />
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Long entry</span>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#ef4444', display: 'inline-block', marginLeft: 8 }} />
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Short entry</span>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#3b82f6', display: 'inline-block', marginLeft: 8 }} />
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Exit (profit)</span>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#f59e0b', display: 'inline-block', marginLeft: 8 }} />
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Exit (loss)</span>
        </div>
      </div>

      {loading && (
        <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-muted)' }}>
          <span className="spinner" style={{ width: 20, height: 20, marginRight: 8 }} />
          Loading candle data…
        </div>
      )}

      {error && (
        <div style={{
          padding: '8px 14px',
          background: 'rgba(239,68,68,0.1)',
          border: '1px solid rgba(239,68,68,0.25)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--color-danger)',
          fontSize: '0.83rem',
        }}>
          {error}
        </div>
      )}

      <div
        ref={containerRef}
        style={{
          width: '100%',
          minHeight: 500,
          borderRadius: 'var(--radius-sm)',
          overflow: 'hidden',
        }}
      />
    </div>
  )
}
