import React, { useEffect, useState, useCallback } from 'react'
import { api } from './api'
import { FleetView, DataView, ConfigView } from './views'
import { GatewayDrawer, DataDrawer, ConfigDrawer } from './drawers'

const NAV = [
  { id: 'fleet', label: 'Fleet' },
  { id: 'data', label: 'Live Data' },
  { id: 'config', label: 'Config' },
]

// Minimal line icons (stroke uses currentColor so they follow the active state).
const ICONS = {
  fleet: (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <rect x="2" y="2" width="5" height="5" rx="1" /><rect x="9" y="2" width="5" height="5" rx="1" />
      <rect x="2" y="9" width="5" height="5" rx="1" /><rect x="9" y="9" width="5" height="5" rx="1" />
    </svg>
  ),
  data: (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M2 11l3-4 3 3 4-6 2 3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  config: (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M2 4h12M2 8h12M2 12h8" strokeLinecap="round" />
    </svg>
  ),
}

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

  const activeLabel = NAV.find(n => n.id === view)?.label ?? ''

  return (
    <>
      <div className="shell">
        <aside className="side">
          <div className="side-brand">
            <span className={`svc-dot ${svcUp ? '' : 'down'}`} title={svcUp ? 'center online' : 'center unreachable'} />
            <div>
              <b>Wellfobes</b>
              <span className="tag">FLEET&nbsp;CONTROL</span>
            </div>
          </div>
          <nav className="side-nav">
            {NAV.map(n => (
              <button key={n.id} className={view === n.id ? 'on' : ''} onClick={() => setView(n.id)}>
                <span className="ic" aria-hidden>{ICONS[n.id]}</span>{n.label}
              </button>
            ))}
          </nav>
          <div className="side-foot">
            <div className="live"><span className="d" />live</div>
            <div className="clock">{clock}</div>
          </div>
        </aside>

        <div className="content">
          <div className="topstrip">
            <span className="crumb">{activeLabel}</span>
            <span className={`svc-tag ${svcUp ? 'ok' : 'bad'}`}>{svcUp ? 'center online' : 'center unreachable'}</span>
          </div>
          <main>
            {view === 'fleet' && <FleetView onOpenGateway={openGateway} />}
            {view === 'data' && <DataView onOpenData={openData} />}
            {view === 'config' && <ConfigView onOpenConfig={openConfig} />}
          </main>
        </div>
      </div>

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
