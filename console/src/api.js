// Thin client for the center's config-service. In dev, calls go through Vite's
// /api proxy to http://localhost:8080; in prod set VITE_API_BASE to the center.
const BASE = import.meta.env.VITE_API_BASE ?? '/api'

async function req(path, opts) {
  const r = await fetch(BASE + path, opts)
  if (!r.ok) throw new Error((await r.text().catch(() => '')) || `HTTP ${r.status}`)
  return r.json()
}

export const api = {
  fleet:        ()               => req('/fleet'),
  sites:        ()               => req('/sites'),
  latest:       (site, gw)       => req(`/data/${site}/${gw}/latest`),
  series:       (site, gw, tag)  => req(`/data/${site}/${gw}/series?tag=${encodeURIComponent(tag)}&limit=120`),
  configHistory:(site, gw)       => req(`/config/${site}/${gw}/history`),
  publishConfig:(site, gw, config, note) =>
    req(`/config/${site}/${gw}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config, note: note || null }),
    }),
}
