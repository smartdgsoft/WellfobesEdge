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
    DEFAULT_CODEC,
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
            for m in payload.metrics:
                # Resolve the tag name: prefer explicit name, else the alias map.
                tag = m.name or amap.get(m.alias or -1)
                if not tag:
                    continue             # can't identify -> skip (never guess)
                key = MetricKey(site=site, gateway=gateway, device=device, tag=tag)
                self.writer(key, m.value, m.quality, m.timestamp_ms)

    def writer_death(self, site: str, gateway: str):
        print(f"[historian] node offline: {site}/{gateway}", flush=True)

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

    def __call__(self, key: MetricKey, value: Optional[float],
                 quality: Optional[int], ts_ms: int):
        if value is None:
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO edge_values
                           (site, gateway, device, tag, value, quality, ts)
                       VALUES (%s,%s,%s,%s,%s,%s, to_timestamp(%s/1000.0))""",
                    (key.site, key.gateway, key.device, key.tag,
                     value, quality, ts_ms))
        except Exception as exc:
            # connection dropped -> reconnect once and retry; never crash the sub
            print(f"[historian] write failed ({exc}); reconnecting", flush=True)
            self._connect_with_retry(30)


def main():
    Historian(TimescaleWriter()).run_forever()


if __name__ == "__main__":
    main()
