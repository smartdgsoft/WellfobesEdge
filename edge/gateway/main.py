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
import threading
from typing import Dict, Optional

import paho.mqtt.client as mqtt

from wellfobes_contract import (
    DEFAULT_CODEC, Metric, NodeIdentity, Payload, RBEState, SeqCounter, now_ms,
    decode_ack,
)
from gateway.sources import build_source
from gateway.buffer import DeliveryBuffer
from gateway.config_client import ConfigClient, apply_config
from gateway.status_server import start_status_server


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
STATUS_PORT = int(os.getenv("EDGE_STATUS_PORT", "8090"))   # local status page; 0 disables

# The edge ALWAYS does store-and-forward. That is its job and it does not vary
# by deployment: acquire -> persist -> deliver -> release on confirmation. A
# standalone edge needs this MORE than a centered one, not less, because there
# is no historian to backfill from later.
#
# What varies is only which signal counts as "delivered":
#   broker (default) -- the broker's QoS 1 PUBACK. The broker has taken
#     ownership of the message. Needs no center, so it works identically for a
#     standalone edge feeding a customer's own broker/MES and for a full stack.
#   center -- the historian's DACK, i.e. the row is committed in TimescaleDB.
#     A strictly stronger, end-to-end guarantee: it survives the broker
#     accepting a message and the historian then dying before the DB write.
#     Requires a historian; if none is acking, batches are correctly retained.
#
# This is a durability level, not a product SKU. Both SKUs run the same path.
ACK_MODE = os.getenv("EDGE_ACK_MODE", "broker").strip().lower()
_ACK_MODES = ("broker", "center")
if ACK_MODE not in _ACK_MODES:
    raise ValueError(
        f"EDGE_ACK_MODE={ACK_MODE!r} invalid; expected one of {_ACK_MODES}")


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
        self.client.on_message = self._on_message      # center DACKs come back here
        self.client.on_publish = self._on_publish      # broker PUBACKs land here
        self._connected = asyncio.Event()
        self._loop = None
        # Durable history buffer — ALWAYS on. Survives restart; a batch is freed
        # only once delivery is confirmed (see ACK_MODE).
        os.makedirs(os.path.dirname(BUFFER_PATH) or ".", exist_ok=True)
        self.buffer = DeliveryBuffer(BUFFER_PATH, max_rows=BUFFER_MAX_ROWS)
        self._inflight: set[int] = set()   # batch_seqs published, awaiting ack
        # PUBACK bookkeeping for ACK_MODE=broker. paho delivers on_publish from
        # its network thread while we publish from ours, so a PUBACK can land
        # before we record its mid. _mid_seq maps mid -> batch_seq; _early holds
        # mids that were confirmed before registration. Guarded by _mid_lock.
        self._mid_seq: Dict[int, int] = {}
        self._early: set[int] = set()
        self._mid_lock = threading.Lock()
        self._pending: Dict[str, list] = {}   # per-device readings not yet buffered
        self.config_client = ConfigClient(CONFIG_URL, SITE, GATEWAY)
        self._config_version: Optional[int] = None
        # Tag allowlist from config. None = no restriction (pass all); a set =
        # emit ONLY these tags (empty set = emit nothing). Applied at the source
        # boundary so every source type is filtered by one gate.
        self._allowed_tags: Optional[set] = None
        self._latest: Dict[str, dict] = {}   # tag -> {value, quality, ts_ms} for the local status page
        self._started_ms = now_ms()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._publish_nbirth()
            # Listen for this gateway's delivery acks (center -> edge). Always
            # subscribed: a center may or may not be present, and if one is it
            # costs nothing to hear it. ACK_MODE decides whether we act on it.
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
        """Center DACK (paho network thread): the historian has COMMITTED this
        batch to TimescaleDB. In ACK_MODE=center this is what releases it — a
        strictly stronger guarantee than PUBACK, which only proves the broker
        accepted the message and would lose data if the historian then died
        before its DB write. In ACK_MODE=broker the batch is already gone."""
        if ACK_MODE != "center":
            return
        try:
            batch_seq = decode_ack(msg.payload)
        except Exception:
            return
        self._release(batch_seq, "center")

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
        self._latest[f"{device}/{tag}"] = {"device": device, "tag": tag,
            "value": value, "quality": quality, "ts_ms": ts_ms}
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
        info = self.client.publish(self.node.ddata_topic(device),
                                   self.codec.encode(p), qos=1, retain=False)
        if ACK_MODE == "broker":
            # Tie this mid to the batch so its PUBACK releases it. Handle the
            # race where on_publish already fired for this mid.
            with self._mid_lock:
                if info.mid in self._early:
                    self._early.discard(info.mid)
                    confirmed = True
                else:
                    self._mid_seq[info.mid] = seq
                    confirmed = False
            if confirmed:
                self._release(seq, "broker")

    def _release(self, batch_seq: int, by: str) -> None:
        """Free a confirmed batch from the durable buffer."""
        freed = self.buffer.ack(batch_seq)
        self._inflight.discard(batch_seq)
        if freed:
            print(f"[gateway] batch {batch_seq} confirmed by {by}, "
                  f"{freed} rows released", flush=True)

    def _on_publish(self, client, userdata, mid):
        """QoS 1 PUBACK from the broker. In ACK_MODE=broker this is what makes
        delivery durable — no center required, so a standalone edge gets the
        same store-and-forward guarantee as a centered one."""
        if ACK_MODE != "broker":
            return
        with self._mid_lock:
            seq = self._mid_seq.pop(mid, None)
            if seq is None:
                self._early.add(mid)     # PUBACK beat registration; publish() will see it
                return
        self._release(seq, "broker")

    def status(self) -> dict:
        """Point-in-time health of THIS gateway. Read by the local status page.
        Depends on nothing external — works for a standalone SKU-1 edge."""
        batches, rows = self.buffer.depth()
        buffer_status = {"pending_batches": batches, "pending_rows": rows,
                         "max_rows": BUFFER_MAX_ROWS}
        return {
            "site": SITE, "gateway": GATEWAY, "source": SOURCE,
            "ack_mode": ACK_MODE,
            "connected": self._connected.is_set(),
            "broker": f"{BROKER_HOST}:{BROKER_PORT}",
            "config_version": self._config_version,
            "config_url": CONFIG_URL or None,
            "allowed_tags": sorted(self._allowed_tags) if self._allowed_tags is not None else None,
            "buffer": buffer_status,
            "uptime_s": (now_ms() - self._started_ms) // 1000,
            "tags": sorted(self._latest.values(), key=lambda x: x["tag"]),
            "now_ms": now_ms(),
        }

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
        # Local status page — up immediately, independent of broker/center, so a
        # technician can see this gateway even when it's disconnected.
        start_status_server(STATUS_PORT, self.status)
        # Management plane: pull config before we start publishing.
        self._pull_and_apply_config()
        # Reconnecting client: paho auto-reconnects, redelivering on the will/birth.
        self.client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=30)
        self.client.loop_start()

        source = build_source(SOURCE)
        seen: Dict[str, set[str]] = {}
        # The drain loop only exists in store-and-forward mode — it flushes and
        # redelivers the durable buffer. A live edge has no buffer to drain.
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
                    # HISTORY path: accumulate into a durable batch. Always on —
                    # this is the store-and-forward guarantee, same in every
                    # deployment, standalone or centered.
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
