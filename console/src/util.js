export const fmt = (v) =>
  v == null ? '—'
  : typeof v === 'number' ? (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2))
  : v

export const ago = (ts) => {
  if (!ts) return 'never'
  const s = (Date.now() - new Date(ts).getTime()) / 1000
  if (s < 60) return Math.round(s) + 's ago'
  if (s < 3600) return Math.round(s / 60) + 'm ago'
  return Math.round(s / 3600) + 'h ago'
}

export const stale = (ts) => ts && (Date.now() - new Date(ts).getTime()) / 1000 > 120
