# Wellfobes Fleet — Operator Console

A React (Vite) single-page app: the operator's window into the fleet. Three
views — Fleet status (desired-vs-actual config), Live Data (tag values + trends),
and Config (view/edit/publish per-site config). Read *and* control.

Separate from `edge/` and `center/`: it only talks to the center's config-service
over HTTP, so it deploys and versions independently.

## Dev

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api -> localhost:8080
```

Point the proxy at a different center with `VITE_API_TARGET`:
```bash
VITE_API_TARGET=http://your-center:8080 npm run dev
```

## Build / deploy

```bash
npm run build      # -> dist/  (static files)
```

Or via Docker (builds + serves with nginx, proxies /api to the config-service):
```bash
docker build -t wellfobes-console .
```

The full stack (`docker-compose.full.yml`) includes this as the `console`
service on port 3000.

## What it talks to

All endpoints on the center's config-service:
`/fleet`, `/sites`, `/data/{site}/{gw}/latest`, `/data/{site}/{gw}/series`,
`/config/{site}/{gw}` (GET history / POST publish).
