# Edge Gateway (SKU-1, standalone)

Acquires plant data, normalizes it, and publishes **live** values as
Sparkplug-B over MQTT to whatever you point it at — your own broker, historian,
or MES/ERP. It holds **nothing queryable**: buffer for delivery, never for
retrieval (WEP-001 §4). If you want to keep and query history, that's the
central platform (SKU-2).

## Runs from `edge/ + shared/` alone

This directory plus `../shared` is the entire deployable unit. The Dockerfile
copies only those two — never `center/`. That's what makes it standalone; it's
enforced by `tools/check_boundaries.py` at the repo root.

## Configure (env)

| Var | Default | Meaning |
|-----|---------|---------|
| `MQTT_HOST` / `MQTT_PORT` | `localhost` / `1883` | where to publish |
| `EDGE_SITE` | `PLANT12` | this plant's id (Sparkplug group) |
| `EDGE_GATEWAY` | `GW-A` | this gateway's id (Sparkplug edge node) |
| `EDGE_SOURCE` | `simulated` | `simulated` · `opcua` · `redis` (see below) |
| `EDGE_KEEPALIVE_S` | `30` | force a publish this often even if unchanged |
| `EDGE_DEADBAND` | `0` | ignore moves smaller than this |

**Sources:**
- `simulated` — sine/step tags, zero deps. Dev, demo, tests.
- `opcua` — acquire straight from a PLC. The **real standalone** source; needs
  nothing from any central platform. (Stub in Phase 1; driver is Phase 2+.)
- `redis` — tap an existing co-located Wellfobes stack's live stream. **A center
  dependency** — only for the co-located thin slice, never a true standalone
  edge. Clearly isolated in `gateway/sources.py`.

## Run

```bash
# from the repo root (Dockerfile needs shared/):
docker compose -f edge/docker-compose.edge.yml up --build
# watch it publish:
mosquitto_sub -h localhost -p 1883 -t 'spBv1.0/#' -v
```

You'll see `NBIRTH`, then `DBIRTH`, then `DDATA` — moving tags often, constant
tags rarely (report-by-exception).
