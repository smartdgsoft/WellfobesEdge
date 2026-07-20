# Central Platform (SKU-2 side)

The historian subscriber and central broker config. Subscribes to the Sparkplug
topics, reassembles the four-part identity (`site/gateway/device/tag`), and
writes to the site-first `edge_values` hypertable.

The historian is *just another subscriber* — it holds no special position on the
broker. The live API, a dashboard, or a third party could each be another
subscriber beside it. That's what makes "historian ingest AND live-to-a-consumer"
fall out for free (WEP-001 §9).

## Pieces

- `historian/main.py` — MQTT subscribe → decode → write. Handles birth/death,
  learns aliases at DBIRTH, resolves them in DDATA.
- `infra/migrations/030_edge_values.sql` — the site-first hypertable.
- Broker: Mosquitto (open source), configured inline in the compose file.
  Harden before prod (WEP-001 §10): disable anonymous, per-gateway auth, TLS.

## Run

```bash
cat infra/migrations/030_edge_values.sql | psql "$POSTGRES_DSN"
docker compose -f center/docker-compose.center.yml up --build
```

Set `POSTGRES_DSN` to your TimescaleDB. `SUB_SITE` / `SUB_GATEWAY` default to
`+` (all); narrow them to scope what this subscriber ingests.
