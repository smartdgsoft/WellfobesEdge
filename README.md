# Wellfobes Fleet — edge gateway & central platform

A monorepo for the Wellfobes IQ fleet architecture (see `docs/WEP-001`). It is
laid out as **three loosely-coupled zones** so the edge deploys standalone today
and the repo splits cleanly tomorrow:

```
shared/    the wire contract — identity + Sparkplug-B codec. Dependency-free.
           Owned by neither side; both import it. Packaged as `wellfobes_contract`.
edge/      the edge gateway (SKU-1). Ships as edge/ + shared/, nothing else.
center/    the central platform side — historian subscriber, migrations, broker.
tools/     check_boundaries.py — the guard that keeps the zones decoupled.
docs/      WEP-001 architecture proposal.
```

## The one rule that makes everything work

> **edge imports only `shared`. center imports only `shared`. shared imports neither.**
> The edge and center are strangers who share only the wire contract.

This is enforced, not hoped for:

```bash
make check          # tools/check_boundaries.py — exits 1 on any cross-import
```

Run it in CI. Because of this rule:

- **"Deploy just the edge" is real.** The edge image builds from `shared/ + edge/`
  only (`edge/Dockerfile` never copies `center/`). A SKU-1 customer gets exactly
  those two folders and it runs — verified.
- **Splitting into two repos later is mechanical.** `git filter-repo --path edge
  --path shared` gives the edge repo; `shared/` becomes a published package both
  pull. Nothing to untangle, because nothing was tangled.

## Wire format is versioned from day one

Every payload carries `PROTOCOL_VERSION` (`shared/wellfobes_contract`). Today
edge and center move in lockstep, but the field is the insurance that lets an old
edge talk to a new center once they're on independent release cycles — the
decoder rejects a version it doesn't know rather than mis-parsing. (We were
bitten by payload-shape drift *within* one codebase twice already; across two
repos it's a certainty.)

## The two SKUs

| SKU | Deploy | What it is |
|-----|--------|------------|
| **1 — Edge standalone** | `edge/ + shared/` | acquire → publish live to the customer's own MES/ERP/broker. No historian, no retention. |
| **2 — Edge + Central** | `edge/ + shared/ + center/` | the same edge, plus the historian that subscribes, stores, and serves. |

The boundary *is* the product line: the edge never retains; retention is the
center's job (WEP-001 §4, "buffer for delivery, never for retrieval").

## Broker

**EMQX** (open source, Apache-2.0) at the center — it clusters, which the fleet
needs at scale. **Mosquitto** for local dev and the test suite. Everything is
broker-agnostic (plain MQTT via paho + Sparkplug on top), so the choice is
config, not code — the pipe was verified on Mosquitto and behaves identically on
EMQX.

## Quick start

```bash
make install                        # installs shared/ + deps
make check                          # boundary guard
# run the whole slice locally (edge + EMQX + historian):
docker compose -f docker-compose.full.yml up --build

# or just the standalone edge (SKU-1):
docker compose -f edge/docker-compose.edge.yml up --build
```

## Tests (all run against a real broker)

```bash
# start a broker on 18845 first, e.g.:
printf 'listener 18845\nallow_anonymous true\n' > /tmp/m.conf && mosquitto -c /tmp/m.conf -d
MQTT_PORT=18845 make test
```

- `shared/tests` — identity, topic round-trip, seq, RBE, protocol-version negotiation
- `edge/tests` — the real gateway service, end-to-end, from edge + shared only
- `center/tests` — the historian reassembling full identity, resolving aliases

## Status

Phase 1 (thin slice) — live only, no buffer, JSON payload behind a pluggable
codec. See `docs/WEP-001` §13 for the roadmap and each phase's deliberate
simplifications.
