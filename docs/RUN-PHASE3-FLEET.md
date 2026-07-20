# Phase 3 — second site + management plane

Adds the management plane: the center holds each site's desired config
(versioned, in Postgres), gateways PULL their own config over HTTP on
startup/reconnect and report the version they're running. Plus a second site
(PLANT30) to prove multi-site. Bespoke per site — each gateway gets its own
explicit config, no templates.

## What's new

```
center/config_service/main.py            HTTP config API (pull, publish, status, fleet)
center/infra/migrations/032_site_config.sql   site_config + gateway_status tables
edge/gateway/config_client.py            gateway pulls/applies config, reports version
```
Plus: docker-compose.full.yml gains a `config-service` and a second gateway
(`edge-gateway-plant30`).

## Run

```bash
docker compose -f docker-compose.full.yml up -d --build
```

Six services now: timescaledb, mqtt, config-service, edge-gateway (PLANT12),
edge-gateway-plant30 (PLANT30), historian.

If your DB volume already exists, apply the new migration manually:
```bash
docker compose -f docker-compose.full.yml exec timescaledb \
  psql -U wellfobes -d fleet -f /docker-entrypoint-initdb.d/03_site_config.sql
```

## Try the management plane

**Publish a bespoke config for each site (the center is source of truth):**
```bash
curl -X POST localhost:8080/config/PLANT12/GW-A \
  -H 'Content-Type: application/json' \
  -d '{"config":{"tags":["sim_level","sim_pressure"],"keepalive_s":20,"deadband":0.5},"note":"plant12"}'

curl -X POST localhost:8080/config/PLANT30/GW-A \
  -H 'Content-Type: application/json' \
  -d '{"config":{"tags":["sim_level"],"keepalive_s":60,"deadband":5},"note":"plant30 bespoke"}'
```

**Restart the gateways so they pull the new config:**
```bash
docker compose -f docker-compose.full.yml restart edge-gateway edge-gateway-plant30
docker compose -f docker-compose.full.yml logs edge-gateway | grep config
# -> [config] applied version 1: {'keepalive_s': 20.0, 'deadband': 0.5, ...}
```

**See the fleet — desired vs actual across sites:**
```bash
curl -s localhost:8080/fleet | python3 -m json.tool
```
Each gateway shows `desired_version`, `running_version`, `converged`. Change a
config (POST again -> version bumps) and watch that gateway go `converged:false`
until it re-pulls.

**Two sites landing data:**
```bash
docker compose -f docker-compose.full.yml exec timescaledb psql -U wellfobes -d fleet -c \
  "SELECT site, count(*), max(ts) FROM edge_values GROUP BY site;"
# -> PLANT12 and PLANT30, both climbing. The GROUP BY site rollup is now real.
```

## The resilience property (SKU-1 stays intact)

A gateway with no `EDGE_CONFIG_URL`, or one that can't reach the center, runs on
its env defaults and keeps working — the management plane is optional, never
required. A standalone SKU-1 edge simply never talks to a config service. Proven
in `center/tests/test_config_e2e.py` and the config client's fallback tests.

## What Phase 3 does NOT do yet

Config currently tunes runtime knobs (keepalive, deadband). Full tag-set control
from config (the gateway acquiring exactly the tags the config lists) is the next
increment. Device auth (per-gateway credentials, WEP-001 §10) is still open.
Templates were deliberately skipped — plants are bespoke, so each config is whole.
