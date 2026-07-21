import React, { useEffect, useState } from 'react'
import { api } from './api'
import { fmt, ago, stale } from './util'
import { Pill, Stat, Spark } from './components'

// ─── FLEET: desired-vs-actual config across every gateway ───
export function FleetView({ onOpenGateway }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    let live = true
    const load = () => api.fleet().then(d => live && setRows(d.gateways)).catch(() => live && setRows([]))
    load(); const t = setInterval(load, 5000)
    return () => { live = false; clearInterval(t) }
  }, [])

  if (rows === null) return <div className="empty">Loading fleet…</div>
  const converged = rows.filter(r => r.converged).length
  const drift = rows.filter(r => !r.converged).length
  const down = rows.filter(r => stale(r.last_seen)).length

  return (
    <div className="view">
      <h2>Fleet status</h2>
      <p className="sub">Desired vs actual config across every gateway. Drift means a gateway hasn't picked up its latest config.</p>
      <div className="stats">
        <Stat label="Gateways" value={rows.length} />
        <Stat label="Converged" value={converged} />
        <Stat label="Drift" value={drift} alert={drift > 0} />
        <Stat label="Not checked in" value={down} alert={down > 0} />
      </div>
      <div className="card">
        <table>
          <thead><tr><th>Site</th><th>Gateway</th><th>Status</th><th>Config</th><th>Last check-in</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan="5" className="empty">No gateways registered yet.</td></tr>}
            {rows.map(r => {
              const [k, label] = stale(r.last_seen) ? ['bad', 'offline']
                : r.converged ? ['ok', 'converged'] : ['warn', 'drift']
              return (
                <tr key={r.site + r.gateway} className="click" onClick={() => onOpenGateway(r.site, r.gateway)}>
                  <td><b>{r.site}</b></td>
                  <td className="mono dim">{r.gateway}</td>
                  <td><Pill kind={k}>{label}</Pill></td>
                  <td className="mono">v{r.running_version ?? '—'} <span className="faint">/ v{r.desired_version}</span></td>
                  <td className="dim">{ago(r.last_seen)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── DATA: sites the historian has seen, drill into live readings ───
export function DataView({ onOpenData }) {
  const [sites, setSites] = useState(null)
  useEffect(() => {
    let live = true
    const load = () => api.sites().then(d => live && setSites(d.sites)).catch(() => live && setSites([]))
    load(); const t = setInterval(load, 5000)
    return () => { live = false; clearInterval(t) }
  }, [])

  if (sites === null) return <div className="empty">Loading sites…</div>
  return (
    <div className="view">
      <h2>Live data</h2>
      <p className="sub">Tag values landing in the historian, per gateway. Select a site to see its live readings and trends.</p>
      <div className="card">
        <table>
          <thead><tr><th>Site</th><th>Gateway</th><th>Tags</th><th>Readings</th><th>Last reading</th></tr></thead>
          <tbody>
            {sites.length === 0 && <tr><td colSpan="5" className="empty">No data has landed yet. Bring up an edge gateway.</td></tr>}
            {sites.map(s => (
              <tr key={s.site + s.gateway} className="click" onClick={() => onOpenData(s.site, s.gateway)}>
                <td><b>{s.site}</b></td>
                <td className="mono dim">{s.gateway}</td>
                <td className="mono">{s.tags}</td>
                <td className="mono dim">{Number(s.readings).toLocaleString()}</td>
                <td className="dim">{ago(s.last_reading)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── CONFIG: per-site config with in-sync status; row opens the editor ───
export function ConfigView({ onOpenConfig }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    let live = true
    const load = () => api.fleet().then(d => live && setRows(d.gateways)).catch(() => live && setRows([]))
    load(); const t = setInterval(load, 5000)
    return () => { live = false; clearInterval(t) }
  }, [])

  if (rows === null) return <div className="empty">Loading…</div>
  return (
    <div className="view">
      <h2>Config management</h2>
      <p className="sub">Each site runs its own bespoke config. Publishing creates a new version; the gateway applies it on its next check-in.</p>
      <div className="card">
        <table>
          <thead><tr><th>Site</th><th>Gateway</th><th>Desired</th><th>Running</th><th>Status</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan="5" className="empty">No gateways yet.</td></tr>}
            {rows.map(r => {
              const [k, label] = r.converged ? ['ok', 'in sync'] : ['warn', 'pending']
              return (
                <tr key={r.site + r.gateway} className="click" onClick={() => onOpenConfig(r.site, r.gateway)}>
                  <td><b>{r.site}</b></td>
                  <td className="mono dim">{r.gateway}</td>
                  <td className="mono">v{r.desired_version}</td>
                  <td className="mono dim">v{r.running_version ?? '—'}</td>
                  <td><Pill kind={k}>{label}</Pill></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
