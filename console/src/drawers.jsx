import React, { useEffect, useState } from 'react'
import { api } from './api'
import { fmt, ago, stale } from './util'
import { Pill, Spark } from './components'

// ─── Gateway detail: status + live readings, link to config ───
export function GatewayDrawer({ site, gw, onEditConfig }) {
  const [info, setInfo] = useState(null)
  const [latest, setLatest] = useState(null)
  useEffect(() => {
    let live = true
    Promise.all([api.fleet().catch(() => ({ gateways: [] })), api.latest(site, gw).catch(() => ({ tags: [] }))])
      .then(([f, l]) => {
        if (!live) return
        setInfo((f.gateways || []).find(x => x.site === site && x.gateway === gw) || {})
        setLatest(l.tags)
      })
    return () => { live = false }
  }, [site, gw])

  if (!info) return <p className="faint">Loading…</p>
  const [k, label] = stale(info.last_seen) ? ['bad', 'offline']
    : info.converged ? ['ok', 'converged'] : ['warn', 'drift']
  return (
    <>
      <Pill kind={k}>{label}</Pill>
      <dl className="kv">
        <dt>Desired config</dt><dd>v{info.desired_version ?? '—'}</dd>
        <dt>Running config</dt><dd>v{info.running_version ?? '—'}</dd>
        <dt>Last check-in</dt><dd>{ago(info.last_seen)}</dd>
      </dl>
      <h2 style={{ marginTop: 22 }}>Live readings</h2>
      {latest && latest.length
        ? latest.map(t => (
          <div className="readout" key={t.device + t.tag}>
            <span className="rtag">{t.tag}</span>
            <span className="rval">{fmt(t.value)}</span>
          </div>
        ))
        : <p className="empty">No readings yet.</p>}
      <div className="row" style={{ marginTop: 20 }}>
        <button className="btn ghost" onClick={() => onEditConfig(site, gw)}>Edit config →</button>
      </div>
    </>
  )
}

// ─── Data detail: readings + a trend chart for the selected tag ───
export function DataDrawer({ site, gw }) {
  const [tags, setTags] = useState(null)
  const [pick, setPick] = useState(null)
  const [series, setSeries] = useState(null)

  useEffect(() => {
    let live = true
    api.latest(site, gw).then(l => {
      if (!live) return
      setTags(l.tags)
      if (l.tags.length) setPick(l.tags[0].tag)
    }).catch(() => live && setTags([]))
    return () => { live = false }
  }, [site, gw])

  useEffect(() => {
    if (!pick) return
    let live = true
    api.series(site, gw, pick).then(s => live && setSeries(s.points.map(p => p.value))).catch(() => live && setSeries([]))
    return () => { live = false }
  }, [site, gw, pick])

  if (!tags) return <p className="faint">Loading…</p>
  return (
    <>
      <h2>Live readings</h2>
      {tags.length
        ? tags.map(t => (
          <div className={`readout click`} key={t.device + t.tag} onClick={() => setPick(t.tag)}>
            <span className={`rtag ${t.tag === pick ? 'cur' : ''}`}>{t.tag}</span>
            <span className="rval">{fmt(t.value)} <span className="faint" style={{ fontSize: 12 }}>q{t.quality ?? '—'}</span></span>
          </div>
        ))
        : <p className="empty">No readings yet.</p>}
      {pick && (
        <>
          <h2 style={{ marginTop: 22 }}>Trend · <span className="mono" style={{ color: 'var(--accent)' }}>{pick}</span></h2>
          <div className="card" style={{ padding: 14 }}>
            {series === null ? <p className="faint">Loading…</p> : <Spark values={series} />}
          </div>
        </>
      )}
    </>
  )
}

// ─── Config editor: edit + publish a new version, with version history ───
export function ConfigDrawer({ site, gw, toast, onPublished }) {
  const [hist, setHist] = useState(null)
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const load = () => api.configHistory(site, gw).then(h => {
    setHist(h.versions)
    const cur = h.versions[0]
    setText(cur ? JSON.stringify(cur.config, null, 2)
      : '{\n  "tags": ["sim_level"],\n  "keepalive_s": 20,\n  "deadband": 0\n}')
  }).catch(() => setHist([]))
  useEffect(() => { load() }, [site, gw])

  const publish = async () => {
    let cfg
    try { cfg = JSON.parse(text) }
    catch { toast("Config isn't valid JSON — fix and retry", 'bad'); return }
    setBusy(true)
    try {
      const r = await api.publishConfig(site, gw, cfg, note.trim())
      toast(`Published ${site}/${gw} → v${r.version}`, 'ok')
      setNote(''); load(); onPublished?.()
    } catch (e) { toast('Publish failed: ' + e.message, 'bad') }
    finally { setBusy(false) }
  }

  if (hist === null) return <p className="faint">Loading…</p>
  const cur = hist[0]
  return (
    <>
      <div className="note">Editing publishes a NEW version. The gateway applies it on its next check-in (startup / reconnect).</div>
      <textarea value={text} onChange={e => setText(e.target.value)} spellCheck={false} />
      <input className="inp" placeholder="change note (optional)" value={note} onChange={e => setNote(e.target.value)} />
      <div className="row">
        <button className="btn" onClick={publish} disabled={busy}>Publish new version</button>
        <span className="faint" style={{ fontSize: 12 }}>{cur ? `current: v${cur.version}` : 'no config yet'}</span>
      </div>
      <h2 style={{ marginTop: 24 }}>Version history</h2>
      <div className="verlist">
        {hist.length
          ? hist.map(v => (
            <div className="v" key={v.version}>
              <span>v{v.version}{v.note ? ' · ' + v.note : ''}</span>
              <span className="faint">{ago(v.created_at)}</span>
            </div>
          ))
          : <p className="empty">No versions yet.</p>}
      </div>
    </>
  )
}
