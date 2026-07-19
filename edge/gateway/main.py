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
from typing import Dict

import paho.mqtt.client as mqtt

from wellfobes_contract import (
    DEFAULT_CODEC, Metric, NodeIdentity, Payload, RBEState, SeqCounter, now_ms,
)
from gateway.sources import build_source


SITE = os.getenv("EDGE_SITE", "PLANT12")
GATEWAY = os.getenv("EDGE_GATEWAY", "GW-A")
BROKER_HOST = os.getenv("MQTT_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_PORT", "1883"))
SOURCE = os.getenv("EDGE_SOURCE", "simulated")
KEEPALIVE_S = float(os.getenv("EDGE_KEEPALIVE_S", "30"))
DEADBAND = float(os.getenv("EDGE_DEADBAND", "0"))


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
        self._connected = asyncio.Event()
        self._loop = None

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._publish_nbirth()
            if self._loop:
                self._loop.call_soon_threadsafe(self._connected.set)
            else:
                self._connected.set()

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

    def _publish_ddata(self, device: str, tag: str, value: float,
                       quality: int, ts_ms: int):
        alias = self._aliases.get(device, {}).get(tag)
        m = Metric(name=tag, value=value, timestamp_ms=ts_ms, alias=alias, quality=quality)
        p = Payload(seq=self.seq.next(), timestamp_ms=now_ms(), metrics=[m])
        self.client.publish(self.node.ddata_topic(device),
                            self.codec.encode(p), qos=0, retain=False)

    async def run(self):
        self._loop = asyncio.get_event_loop()
        self.client.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
        self.client.loop_start()
        await self._connected.wait()

        source = build_source(SOURCE)
        seen: Dict[str, set[str]] = {}
        async for device, tag, value, quality, ts_ms in source.stream():
            tags = seen.setdefault(device, set())
            if tag not in tags:
                tags.add(tag)
                self._publish_dbirth(device, list(tags))
            if self.rbe.should_send(f"{device}/{tag}", value):
                self._publish_ddata(device, tag, value, quality, ts_ms)


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
