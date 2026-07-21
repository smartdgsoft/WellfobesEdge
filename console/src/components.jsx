import React from 'react'
import { fmt } from './util'

export function Pill({ kind, children }) {
  return <span className={`pill ${kind}`}><span className="d" />{children}</span>
}

export function Stat({ label, value, alert }) {
  return (
    <div className={`stat ${alert ? 'alert' : ''}`}>
      <div className="n">{value}</div>
      <div className="l">{label}</div>
    </div>
  )
}

// A minimal, dependency-free sparkline (no chart lib — control panels want
// legibility, not chrome). Draws the last N values with min/max labels.
export function Spark({ values }) {
  if (!values || !values.length) return <p className="empty">No series data.</p>
  const w = 500, h = 120, pad = 6
  const min = Math.min(...values), max = Math.max(...values), rng = (max - min) || 1
  const pts = values.map((v, i) => {
    const x = pad + (i / (values.length - 1 || 1)) * (w - 2 * pad)
    const y = h - pad - ((v - min) / rng) * (h - 2 * pad)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  return (
    <>
      <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <polyline fill="none" stroke="var(--accent)" strokeWidth="1.5" points={pts} />
        <text x={w - 4} y="14" textAnchor="end" fill="var(--ink-faint)" fontFamily="monospace" fontSize="11">{fmt(max)}</text>
        <text x={w - 4} y={h - 4} textAnchor="end" fill="var(--ink-faint)" fontFamily="monospace" fontSize="11">{fmt(min)}</text>
      </svg>
      <div className="note">last {values.length} readings · current {fmt(values[values.length - 1])}</div>
    </>
  )
}
