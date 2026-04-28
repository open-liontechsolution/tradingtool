export const PAIRS = [
  'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
  'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'MATICUSDT',
]

export const INTERVALS = [
  { value: '1h', label: '1 Hour' },
  { value: '4h', label: '4 Hours' },
  { value: '1d', label: '1 Day' },
  { value: '1w', label: '1 Week' },
  { value: '1M', label: '1 Month' },
]

export function fmtNum(v, digits = 2) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(digits)
}

export function fmtMoney(v) {
  if (v === null || v === undefined) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(Number(v))
}

export function fmtTime(ms) {
  if (!ms) return '—'
  return new Date(Number(ms)).toLocaleString()
}

export function fmtIso(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function fmtConfigParams(raw) {
  if (!raw) return ''
  try {
    const obj = typeof raw === 'string' ? JSON.parse(raw) : raw
    return Object.entries(obj).map(([k, v]) => `${k}: ${v}`).join('\n')
  } catch { return '' }
}
