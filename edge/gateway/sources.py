"""Tag sources for the edge gateway.

A source yields (device, tag, value, quality, ts_ms). Three implementations,
deliberately separated so the ONE with a center dependency is impossible to miss:

  * SimulatedSource — no dependencies. For dev, tests, demos.
  * OpcUaSource     — acquires directly from a PLC. The TRUE standalone source
                      for a SKU-1 deployment (edge needs nothing from center).
                      Stub here; real driver is Phase 2+.
  * RedisLiveSource — taps the existing central stack's Redis/Postgres. This is
                      a CENTER DEPENDENCY and is only valid when edge and center
                      are co-located (the Phase-1 thin slice). It must NOT be
                      used for a real standalone edge — see the class docstring.

Note the import rule: this file imports only from `wellfobes_contract`. The
Redis/asyncpg imports are *inside* RedisLiveSource so the edge image doesn't even
need those libraries unless that source is chosen.
"""
from __future__ import annotations

import math
import os
import re
import time
from typing import AsyncIterator, Dict, Tuple

from wellfobes_contract import now_ms

Reading = Tuple[str, str, float, int, int]   # device, tag, value, quality, ts_ms


def safe_id(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]", "_", str(name or "unknown"))
    return s[:64] or "unknown"


class SimulatedSource:
    """Sine/step tags mirroring the real sim_* Siemens tags. Zero dependencies."""
    DEVICE = "SiemensPlc1200"

    def __init__(self, period_s: float = 0.5):
        self.period_s = period_s

    async def stream(self) -> AsyncIterator[Reading]:
        import asyncio
        t0 = time.time()
        while True:
            t = time.time() - t0
            yield self.DEVICE, "sim_level", 500 + 400 * math.sin(t / 5), 192, now_ms()
            yield self.DEVICE, "sim_pressure", 1.2 + 0.3 * math.sin(t / 3), 192, now_ms()
            yield self.DEVICE, "sim_running", 1.0, 192, now_ms()
            await asyncio.sleep(self.period_s)


class OpcUaSource:
    """Acquire directly from an OPC UA server — the real standalone source. A
    SKU-1 edge uses this and needs NOTHING from any central platform. Stub for
    Phase 1; the driver (reusing the existing opcua-client logic) lands in a
    later phase."""
    def __init__(self):
        self.endpoint = os.getenv("OPC_SERVER_URL", "")

    async def stream(self) -> AsyncIterator[Reading]:
        raise NotImplementedError(
            "OpcUaSource is the Phase-2+ standalone acquisition path. "
            "For the Phase-1 slice use EDGE_SOURCE=simulated (standalone) or "
            "EDGE_SOURCE=redis (co-located with the existing central stack).")
        yield  # pragma: no cover


class RedisLiveSource:
    """Tap the existing central stack's live stream.

    *** CENTER DEPENDENCY — NOT for a standalone SKU-1 edge. ***
    Valid only when this edge is co-located with the existing Wellfobes stack
    (the Phase-1 thin slice), so it can read Redis `tag:updates` and load the
    tag registry from Postgres. A real standalone edge uses OpcUaSource and its
    registry is pushed to it via the management plane (WEP-001 §8), so it reaches
    into no central infrastructure. Kept as a clearly-labelled alternate, never
    the default, so the seam stays visible.
    """
    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.pg_dsn = os.getenv("POSTGRES_DSN", "").replace("postgresql+asyncpg://", "postgresql://")

    async def _load_registry(self) -> Dict[str, Tuple[str, str]]:
        import asyncpg
        pool = await asyncpg.create_pool(self.pg_dsn, min_size=1, max_size=2)
        try:
            rows = await pool.fetch(
                """SELECT t.id::text AS tag_id,
                          COALESCE(s.name, 'unknown') AS device,
                          t.display_name AS tag
                     FROM tags t
                     LEFT JOIN sources s ON s.id = t.source_id""")
            return {r["tag_id"]: (safe_id(r["device"]), safe_id(r["tag"])) for r in rows}
        finally:
            await pool.close()

    async def stream(self) -> AsyncIterator[Reading]:
        import json
        import redis.asyncio as aioredis
        registry = await self._load_registry()
        r = aioredis.from_url(self.redis_url, decode_responses=True)
        ps = r.pubsub()
        await ps.subscribe("tag:updates")
        async for msg in ps.listen():
            if msg.get("type") != "message":
                continue
            try:
                d = json.loads(msg["data"])
            except Exception:
                continue
            dev_tag = registry.get(d.get("tag_id"))
            if not dev_tag:
                continue
            device, tag = dev_tag
            yield device, tag, d.get("value"), d.get("quality", 192), \
                int(d.get("ts_ms") or now_ms())


def build_source(name: str):
    return {
        "simulated": SimulatedSource,
        "opcua": OpcUaSource,
        "redis": RedisLiveSource,
    }.get(name, SimulatedSource)()
