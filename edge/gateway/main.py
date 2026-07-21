"""Edge gateway — publishes live tag values as Sparkplug-B over MQTT.

Imports ONLY from `wellfobes_contract` (the shared wire contract) and its own
`sources`. Nothing from center/. That is what makes the edge image buildable
from `edge/ + shared/` alone — verified by tools/check_boundaries.py.

Phase 1: live only, no buffer (Phase 2), no storage at the edge ever.
"""
from __future__ import annotations

import asyncio
import os
import signal
from typing import Dict, Optional

import paho.mqtt.client as mqtt

from wellfobes_contract import (
    DEFAULT_CODEC, Metric, NodeIdentity, Payload, RBEState, SeqCounter, now_ms,
    decode_ack,
)
from gateway.sources import build_source
from gateway.buffer import DeliveryBuffer
from gateway.config_client import ConfigClient, apply_config


SITE = os.getenv("EDGE_SITE", "PLANT12")
GATEWAY = os.getenv("EDGE_GATEWAY", "GW-A")
BROKER_HOST = os.getenv("MQTT_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_PORT", "1883"))
SOURCE = os.getenv("EDGE_SOURCE", "simulated")
KEEPALIVE_S = float(os.getenv("EDGE_KEEPALIVE_S", "30"))
DEADBAND = float(os.getenv("EDGE_DEADBAND", "0"))
BUFFER_PATH = os.getenv("EDGE_BUFFER_PATH", "/data/outbox.db")
BUFFER_MAX_ROWS = int(os.getenv("EDGE_BUFFER_MAX_ROWS", "500000"))
FLUSH_INTERVAL_S = float(os.getenv("EDGE_FLUSH_INTERVAL_S", "1.0"))
BATCH_MAX = int(os.getenv("EDGE_BATCH_MAX", "200"))
CONFIG_URL = os.getenv("EDGE_CONFIG_URL", "")   # empty -> pure env config (SKU-1)
# How often to re-pull config + report status (a heartbeat). This does two jobs:
# keeps the center's "last seen" fresh (so a running gateway shows online), and
# picks up config changes without a restart. 0 disables (pull only on connect).
CONFIG_POLL_S = float(os.getenv("EDGE_CONFIG_POLL_S", "30"))


class EdgeGateway:
    def __init__(self):
        self.node = NodeIdentity(site=SITE, gateway=GATEWAY)
        self.codec = DEFAULT_CODEC
        self.seq = SeqCounter()
        self.rbe = RBEState(deadband=DEADBAND, keepalive_s=KEEPALIVE_S)
        self._aliases: Dict[str, Dict[str, int]] = {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                                  client_id=f"edge-{SITE}-{GATEWAY}", clean_session=True)
        # NDEATH as MQTT will: broker announces our death if we drop uncleanly.
        death = Payload(seq=0, timestamp_ms=now_ms(), metrics=[])
        self.client.will_set(self.node.ndeath_topic(),
                             self.codec.encode(death), qos=1, retain=False)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message      # acks come back here
        self._connected = asyncio.Event()
        self._loop = None
        # Durable history buffer. Survives restart; released only on center ack.
        os.makedirs(os.path.dirname(BUFFER_PATH) or ".", exist_ok=True)
        self.buffer = DeliveryBuffer(BUFFER_PATH, max_rows=BUFFER_MAX_ROWS)
        self._inflight: set[int] = set()   # batch_seqs published, awaiting ack
        self._pending: Dict[str, list] = {}   # per-device readings not yet buffered
        self.config_client = ConfigClient(CONFIG_URL, SITE, GATEWAY)
        self._config_version: Optional[int] = None
        # Tag allowlist from config. None = no restriction (pass all); a set =
        # emit ONLY these tags (empty set = emit nothing). Applied at the source
        # boundary so every source type is filtered by one gate.
        self._allowed_tags: Optional[set] = None

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._publish_nbirth()
            # Listen for this gateway's delivery acks (center -> edge).
            client.subscribe(self.node.dack_topic(), qos=1)
            # Reconnect = a chance the desired config changed while we were gone.
            # Pull on the loop thread to avoid blocking the MQTT callback.
            if self._loop:
                self._loop.call_soon_threadsafe(self._pull_and_apply_config)
            # On (re)connect, anything still buffered is un-acked -> allow resend.
            self._inflight.clear()
            if self._loop:
                self._loop.call_soon_threadsafe(self._connected.set)
            else:
                self._connected.set()

    def _on_message(self, client, userdata, msg):
        """Ack handler (paho network thread). Releases the acked batch from the
        durable buffer — the only place buffer rows are deleted after delivery."""
        try:
            batch_seq = decode_ack(msg.payload)
        except Exception:
            return
        freed = self.buffer.ack(batch_seq)
        self._inflight.discard(batch_seq)
        if freed:
            print(f"[gateway] ack batch {batch_seq} -> released {freed} rows", flush=True)

    def _publish_nbirth(self):
        p = Payload(seq=self.seq.reset(), timestamp_ms=now_ms(), metrics=[])
        self.client.publish(self.node.nbirth_topic(),
                            self.codec.encode(p), qos=1, retain=False)
        self._aliases.clear()

    def _publish_dbirth(self, device: str, known_tags: list[str]):
        amap: Dict[str, int] = {}
        metrics = []
        for i, tag in enumerate(sorted(known_tags), start=1):
            amap[tag] = i
            metrics.append(Metric(name=tag, value=None, timestamp_ms=now_ms(), alias=i))
        self._aliases[device] = amap
        p = Payload(seq=self.seq.next(), timestamp_ms=now_ms(), metrics=metrics)
        self.client.publish(self.node.dbirth_topic(device),
                            self.codec.encode(p), qos=1, retain=False)

    def _publish_live(self, device: str, tag: str, value: float,
                      quality: int, ts_ms: int):
        """Live, ephemeral DDATA — QoS 0, no batch_seq, not buffered."""
        alias = self._aliases.get(device, {}).get(tag)
        m = Metric(name=tag, value=value, timestamp_ms=ts_ms, alias=alias, quality=quality)
        p = Payload(seq=self.seq.next(), timestamp_ms=now_ms(), metrics=[m])
        self.client.publish(self.node.ddata_topic(device),
                            self.codec.encode(p), qos=0, retain=False)

    def _publish_history_batch(self, device: str, seq: int,
                               rows: list) -> None:
        """Publish a durable history batch: QoS 1, tagged with batch_seq so the
        center can ack it. The rows stay in the buffer until that ack arrives."""
        metrics = []
        for (tag, value, quality, ts_ms) in rows:
            alias = self._aliases.get(device, {}).get(tag)
            metrics.append(Metric(name=tag, value=value, timestamp_ms=ts_ms,
                                  alias=alias, quality=quality))
        p = Payload(seq=self.seq.next(), timestamp_ms=now_ms(),
                    metrics=metrics, batch_seq=seq)
        self.client.publish(self.node.ddata_topic(device),
                            self.codec.encode(p), qos=1, retain=False)

    async def _config_poll_loop(self):
        """Periodically re-pull config and re-report running version — the
        heartbeat that keeps the center's view of this gateway fresh and lets
        config changes land without a restart. No-op if polling disabled or no
        center configured."""
        if CONFIG_POLL_S <= 0 or not self.config_client.enabled:
            return
        while True:
            await asyncio.sleep(CONFIG_POLL_S)
            if not self._connected.is_set():
                continue
            # run the blocking HTTP pull/report in a thread so we don't stall the
            # event loop (and thus publishing).
            await asyncio.to_thread(self._pull_and_apply_config)

    def _pull_and_apply_config(self):
        """Pull this gateway's config from the center and apply what we
        understand. Falls back silently to env defaults if the center is
        unreachable or has nothing published. Then report our running version."""
        version, cfg = self.config_client.pull()
        if cfg is not None:
            applied = apply_config(cfg)
            # apply the runtime knobs we support today
            if "keepalive_s" in applied:
                self.rbe.keepalive_s = applied["keepalive_s"]
            if "deadband" in applied:
                self.rbe.deadband = applied["deadband"]
            # Tag-set control: an explicit `tags` key (even []) restricts what
            # this gateway emits. Absent key -> no restriction (pass all).
            if "tags" in applied:
                self._allowed_tags = set(applied["tags"])
                print(f"[config] tag allowlist -> {sorted(self._allowed_tags) or '(none: gateway silent)'}", flush=True)
            else:
                self._allowed_tags = None
            self._config_version = version
            print(f"[config] applied version {version}: {applied}", flush=True)
        else:
            print("[config] running on env defaults (no central config)", flush=True)
        # report actual running version (None if on env defaults)
        self.config_client.report(self._config_version)

    async def run(self):
        self._loop = asyncio.get_event_loop()
        # Management plane: pull config before we start publishing.
        self._pull_and_apply_config()
        # Reconnecting client: paho auto-reconnects, redelivering on the will/birth.
        self.client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=30)
        self.client.loop_start()

        source = build_source(SOURCE)
        seen: Dict[str, set[str]] = {}
        drainer = asyncio.create_task(self._drain_loop())
        heartbeat = asyncio.create_task(self._config_poll_loop())
        try:
            async for device, tag, value, quality, ts_ms in source.stream():
                # Tag-set control (management plane): if a config restricts the
                # tag set, drop anything not on the allowlist BEFORE it births,
                # buffers, or publishes. None => no restriction.
                if self._allowed_tags is not None and tag not in self._allowed_tags:
                    continue
                tags = seen.setdefault(device, set())
                if tag not in tags:
                    tags.add(tag)
                    # DBIRTH only meaningful once connected; if offline it's
                    # re-sent on reconnect via on_connect clearing state.
                    if self._connected.is_set():
                        self._publish_dbirth(device, list(tags))
                # Report-by-exception governs WHAT we emit.
                if self.rbe.should_send(f"{device}/{tag}", value):
                    # LIVE path: publish immediately, fire-and-forget (QoS 0),
                    # newest-wins. A consumer watching live sees fresh values even
                    # while history is still draining. Dropped on congestion —
                    # that's fine, history (below) is the durable copy.
                    if self._connected.is_set():
                        self._publish_live(device, tag, value, quality, ts_ms)
                    # HISTORY path: accumulate into a durable batch.
                    self._pending.setdefault(device, []).append((tag, value, quality, ts_ms))
                    if len(self._pending[device]) >= BATCH_MAX:
                        self._enqueue(device, self._pending.pop(device))
            # (simulated source never ends; real sources loop)
        finally:
            drainer.cancel()
            heartbeat.cancel()

    def _enqueue(self, device: str, rows: list):
        """Persist a batch to disk (durable). Device travels with each row, so
        the drain loop needs no extra bookkeeping."""
        self.buffer.append([(device, t, v, q, ts) for (t, v, q, ts) in rows])

    async def _drain_loop(self):
        """Every FLUSH_INTERVAL_S: flush any partial pending batch to disk, then
        (re)publish all un-acked buffered batches. Un-acked batches are resent —
        that's what makes an outage lossless: they stay on disk until the center
        acks, and get republished each cycle until then."""
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_S)
            # Flush any partial in-memory batch to the durable buffer on the
            # timer, so low-rate tags don't wait for BATCH_MAX to persist.
            for dev in list(self._pending.keys()):
                rows = self._pending.pop(dev)
                if rows:
                    self._enqueue(dev, rows)
            if not self._connected.is_set():
                continue                    # offline: keep buffering, don't publish
            # Publish every pending (un-acked) batch, oldest first.
            for seq in self.buffer.pending_batches():
                rows = self.buffer.batch_rows(seq)   # (device,tag,value,quality,ts)
                if not rows:
                    continue
                # rows share a device (we enqueue per device)
                device = rows[0][0]
                hist = [(t, v, q, ts) for (_d, t, v, q, ts) in rows]
                self._publish_history_batch(device, seq, hist)
                self._inflight.add(seq)
            b, r = self.buffer.depth()
            if b:
                print(f"[gateway] buffer depth: {b} batches / {r} rows un-acked", flush=True)


async def _amain():
    gw = EdgeGateway()
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(gw.run())
    await stop.wait()
    task.cancel()


if __name__ == "__main__":
    asyncio.run(_amain())
