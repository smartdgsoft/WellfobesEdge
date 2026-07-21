import React, { useEffect, useState, useCallback } from 'react'
import { api } from './api'
import { FleetView, DataView, ConfigView } from './views'
import { GatewayDrawer, DataDrawer, ConfigDrawer } from './drawers'

const NAV = [
  { id: 'fleet', label: 'Fleet' },
  { id: 'data', label: 'Live Data' },
  { id: 'config', label: 'Config' },
]

export default function App() {
  const [view, setView] = useState('fleet')
  const [clock, setClock] = useState('')
  const [svcUp, setSvcUp] = useState(true)
  const [drawer, setDrawer] = useState(null) // { kind, site, gw, title }
  const [toast, setToast] = useState(null)   // { msg, kind }

  // clock + service health ping
  useEffect(() => {
    const tick = () => setClock(new Date().toISOString().slice(11, 19) + ' UTC')
    tick(); const t = setInterval(tick, 1000)
    const ping = () => api.fleet().then(() => setSvcUp(true)).catch(() => setSvcUp(false))
    ping(); const p = setInterval(ping, 5000)
    return () => { clearInterval(t); clearInterval(p) }
  }, [])

  const showToast = useCallback((msg, kind = 'ok') => {
    setToast({ msg, kind })
    setTimeout(() => setToast(null), 2600)
  }, [])

  const openGateway = (site, gw) => setDrawer({ kind: 'gateway', site, gw, title: `${site} / ${gw}` })
  const openData = (site, gw) => setDrawer({ kind: 'data', site, gw, title: `${site} / ${gw}` })
  const openConfig = (site, gw) => { setView('config'); setDrawer({ kind: 'config', site, gw, title: `Config · ${site} / ${gw}` }) }
  const closeDrawer = () => setDrawer(null)

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') closeDrawer() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <>
      <div className="top">
        <div className="brand">
          <span className={`svc-dot ${svcUp ? '' : 'down'}`} title={svcUp ? 'center online' : 'center unreachable'} />
          <b>Wellfobes</b><span className="tag">FLEET&nbsp;CONTROL</span>
        </div>
        <div className="nav">
          {NAV.map(n => (
            <button key={n.id} className={view === n.id ? 'on' : ''} onClick={() => setView(n.id)}>{n.label}</button>
          ))}
        </div>
        <div className="clock">{clock}</div>
        <div className="live"><span className="d" />live</div>
      </div>

      <main>
        {view === 'fleet' && <FleetView onOpenGateway={openGateway} />}
        {view === 'data' && <DataView onOpenData={openData} />}
        {view === 'config' && <ConfigView onOpenConfig={openConfig} />}
      </main>

      <div className={`scrim ${drawer ? 'on' : ''}`} onClick={closeDrawer} />
      <div className={`drawer ${drawer ? 'on' : ''}`}>
        {drawer && (
          <>
            <div className="dhead">
              <b>{drawer.title}</b>
              <button className="x" onClick={closeDrawer}>×</button>
            </div>
            <div className="body">
              {drawer.kind === 'gateway' && <GatewayDrawer site={drawer.site} gw={drawer.gw} onEditConfig={openConfig} />}
              {drawer.kind === 'data' && <DataDrawer site={drawer.site} gw={drawer.gw} />}
              {drawer.kind === 'config' && <ConfigDrawer site={drawer.site} gw={drawer.gw} toast={showToast} />}
            </div>
          </>
        )}
      </div>

      {toast && <div className={`toast on ${toast.kind}`}>{toast.msg}</div>}
    </>
  )
}
