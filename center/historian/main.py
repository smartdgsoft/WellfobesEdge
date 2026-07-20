"""Historian subscriber — WEP-001 §9.

Subscribes to the Sparkplug topics, decodes payloads, reassembles the full
four-part identity (site/gateway/device/tag), and writes to the site-first
hypertable. This is the "historian is just another subscriber" property in
practice — it holds no special position, it's one MQTT client among potentially
many (the live API, a dashboard, a third party could each be another).

Phase 1: writes to `edge_values`, a NEW table, so it proves the site-first model
without disturbing the existing `tag_values`. Handles births (learns aliases),
DDATA (resolves alias->name), and death (logs the node offline).
"""
from __future__ import annotations

import os
import time
from typing import Dict, Optional, Tuple

import paho.mqtt.client as mqtt

from wellfobes_contract import (
    DBIRTH, DDATA, NBIRTH, NDEATH, MetricKey, parse_topic, subscribe_pattern,
    DEFAULT_CODEC, ack_topic_for,
)


BROKER_HOST = os.getenv("MQTT_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_PORT", "1883"))
PG_DSN = os.getenv("POSTGRES_DSN", "").replace("postgresql+asyncpg://", "postgresql://")
# Scope which sites/gateways this subscriber ingests. Defaults to all.
SITE_FILTER = os.getenv("SUB_SITE", "+")
GATEWAY_FILTER = os.getenv("SUB_GATEWAY", "+")


class Historian:
    def __init__(self, writer):
        self.codec = DEFAULT_CODEC
        self.writer = writer            # callable(MetricKey, value, quality, ts_ms)
        # (site,gateway,device) -> {alias: name}, learned at DBIRTH
        self._aliases: Dict[Tuple[str, str, str], Dict[int, str]] = {}
        self._last_seq: Dict[Tuple[str, str], int] = {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="historian-sub", clean_session=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        client.subscribe(subscribe_pattern(SITE_FILTER, GATEWAY_FILTER), qos=1)

    def _on_message(self, client, userdata, msg):
        try:
            site, msg_type, gateway, device = parse_topic(msg.topic)
        except ValueError:
            return                       # not our namespace / malformed — ignore
        try:
            payload = self.codec.decode(msg.payload)
        except Exception:
            return                       # undecodable — drop, don't crash the sub

        node = (site, gateway)
        if msg_type == NBIRTH:
            # Node (re)born: seq resets, all prior device aliases are stale.
            self._last_seq[node] = payload.seq
            for k in list(self._aliases):
                if k[0] == site and k[1] == gateway:
                    self._aliases.pop(k, None)
            return

        if msg_type == NDEATH:
            self.writer_death(site, gateway)
            return

        if msg_type == DBIRTH and device:
            amap = {m.alias: m.name for m in payload.metrics if m.alias is not None}
            self._aliases[(site, gateway, device)] = amap
            return

        if msg_type == DDATA and device:
            amap = self._aliases.get((site, gateway, device), {})
            rows = []
            for m in payload.metrics:
                # Resolve the tag name: prefer explicit name, else the alias map.
                tag = m.name or amap.get(m.alias or -1)
                if not tag:
                    continue             # can't identify -> skip (never guess)
                key = MetricKey(site=site, gateway=gateway, device=device, tag=tag)
                rows.append((key, m.value, m.quality, m.timestamp_ms))

            # Write the whole batch. Only ack (release the edge's buffer) if the
            # DB write reports success — that's the end-to-end guarantee.
            batch_seq = payload.batch_seq
            ok = self.writer(rows, gateway=gateway, batch_seq=batch_seq)
            if ok and batch_seq is not None:
                self._publish_ack(site, gateway, batch_seq)

    def writer_death(self, site: str, gateway: str):
        print(f"[historian] node offline: {site}/{gateway}", flush=True)

    def _publish_ack(self, site: str, gateway: str, batch_seq: int):
        from wellfobes_contract import encode_ack
        self.client.publish(ack_topic_for(site, gateway),
                            encode_ack(batch_seq), qos=1, retain=False)

    def run_forever(self):
        self.client.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
        self.client.loop_forever()


# ── the actual DB writer (real deployment) ───────────────────────────────────
class TimescaleWriter:
    """Writes each resolved metric to the edge_values hypertable. Uses a tiny
    synchronous psycopg connection for Phase 1 simplicity; batching/back-pressure
    is a Phase-2/5 concern (WEP-001 §7.4, §9).

    Waits for the DB to be reachable at startup (containers race), and reconnects
    if the connection drops, so the historian doesn't die on a transient blip."""
    def __init__(self, connect_timeout_s: int = 60):
        self._conn = None
        self._connect_with_retry(connect_timeout_s)

    def _connect_with_retry(self, timeout_s: int):
        import psycopg2
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            try:
                self._conn = psycopg2.connect(PG_DSN)
                self._conn.autocommit = True
                print("[historian] connected to database", flush=True)
                return
            except Exception as exc:                       # DB not up yet
                last = exc
                print("[historian] waiting for database…", flush=True)
                time.sleep(2)
        raise RuntimeError(f"could not reach database within {timeout_s}s: {last}")

    def __call__(self, rows, gateway: str = None, batch_seq=None) -> bool:
        """Write a whole batch idempotently. Returns True only if the batch is
        durably committed — the historian acks the edge only on True, so a
        failed write means the edge keeps the data and redelivers.

        ON CONFLICT DO NOTHING makes a redelivered batch (after a lost ack) a
        no-op instead of duplicate rows — at-least-once in flight, effectively
        exactly-once at rest."""
        rows = [(k, v, q, ts) for (k, v, q, ts) in rows if v is not None]
        if not rows:
            return True                    # nothing to write == success
        try:
            with self._conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO edge_values
                           (site, gateway, device, tag, value, quality, ts, batch_seq)
                       VALUES (%s,%s,%s,%s,%s,%s, to_timestamp(%s/1000.0), %s)
                       ON CONFLICT (site, gateway, batch_seq, device, tag, ts) DO NOTHING""",
                    [(k.site, k.gateway, k.device, k.tag, v, q, ts, batch_seq)
                     for (k, v, q, ts) in rows])
            return True
        except Exception as exc:
            # Write failed -> DON'T ack; reconnect and let the edge redeliver.
            print(f"[historian] batch write failed ({exc}); reconnecting", flush=True)
            try:
                self._connect_with_retry(30)
            except Exception:
                pass
            return False


def main():
    Historian(TimescaleWriter()).run_forever()


if __name__ == "__main__":
    main()
