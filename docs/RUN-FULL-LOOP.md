# Running the full loop (SKU-2) — land the data end to end

Brings up the complete pipe in one command, self-contained (its own EMQX and
TimescaleDB — nothing external):

```
edge gateway  ->  EMQX  ->  historian  ->  TimescaleDB (edge_values)
```

## 1. Stop the edge-only stack (it has its own broker; the full stack has its own)

```bash
cd edge && docker compose -f docker-compose.edge.yml down && cd ..
```

## 2. Bring up the full loop

```bash
docker compose -f docker-compose.full.yml up -d --build
```

First run pulls TimescaleDB + EMQX and auto-applies the migration. Give it ~15s.

## 3. Watch it work

**Containers healthy:**
```bash
docker compose -f docker-compose.full.yml ps
# all four Up; timescaledb (healthy)
```

**Historian connected + writing:**
```bash
docker compose -f docker-compose.full.yml logs historian | tail
# expect: "[historian] connected to database"  (no write errors)
```

**The payoff — query what landed:**
```bash
docker compose -f docker-compose.full.yml exec timescaledb \
  psql -U wellfobes -d fleet -c \
  "SELECT site, gateway, device, tag, round(value::numeric,2) AS value, ts
     FROM edge_values ORDER BY ts DESC LIMIT 10;"
```

You should see fresh `PLANT12 / GW-A / SiemensPlc1200 / sim_level` (and
sim_pressure) rows, timestamps ticking. That's the whole architecture proven:
a simulated tag acquired at the edge, published as Sparkplug-B, carried over the
broker, decoded by the historian, and stored under its full four-part identity.

**Count climbing (run twice):**
```bash
docker compose -f docker-compose.full.yml exec timescaledb \
  psql -U wellfobes -d fleet -c "SELECT tag, count(*) FROM edge_values GROUP BY tag;"
```
sim_level / sim_pressure grow fast; sim_running stays low (report-by-exception).

**Cross-site query shape (the fleet payoff):**
```bash
docker compose -f docker-compose.full.yml exec timescaledb \
  psql -U wellfobes -d fleet -c \
  "SELECT site, count(*), max(ts) FROM edge_values GROUP BY site;"
```
One site now; the same query rolls up 50 later — that's why site is first-class.

## 4. EMQX dashboard (optional)

http://<host>:18083  (default admin / public) — see the connected gateway,
topics, throughput.

## Notes

- **Same broker is the point.** Edge publishes to `emqx`; historian subscribes to
  `emqx`. In the edge-only stack they were different brokers, so nothing landed —
  correct for SKU-1 (edge is a pipe), but for the loop they must share one.
- **DB on host port 5433** to avoid clashing with your production Postgres.
- **To use your real TimescaleDB:** drop the `timescaledb` service and point
  `historian`'s `POSTGRES_DSN` at it (apply the migration there first).
- Still simulated data. Real Siemens tags = swap the edge source next.
