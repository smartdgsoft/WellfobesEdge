"""Sparkplug-B message semantics — WEP-001 §6.

Scope of Phase 1, stated plainly so the simplification is a decision, not an
accident:

  * We implement the Sparkplug-B **topic namespace and semantics** faithfully:
    NBIRTH/NDEATH/DBIRTH/DDATA, a per-node monotonic sequence number, metric
    aliases established at birth, and report-by-exception.
  * We DO NOT yet emit the Sparkplug-B **protobuf** payload. Phase 1's producer
    and consumer are both ours, so protobuf interop isn't exercised. We use a
    JSON payload behind a pluggable `Codec`, and the protobuf codec is an
    explicit swap (see `Codec` subclasses) BEFORE any real Sparkplug interop —
    e.g. a SKU-1 customer whose MES speaks Sparkplug natively (WEP-001 §12).

Keeping the codec pluggable means that swap touches one class, not the gateway
or the historian.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Wire-format version, stamped into every payload. Bump ONLY on a breaking
# change to the payload shape. The decoder rejects versions it doesn't know
# rather than mis-parsing — the insurance that lets an old edge and a new center
# coexist once they're on independent release cycles.
PROTOCOL_VERSION = 1


class UnsupportedProtocolVersion(ValueError):
    """Raised when a payload's version isn't one this build understands."""


# ── metric ──────────────────────────────────────────────────────────────────
@dataclass
class Metric:
    """One tag value. `alias` lets DDATA carry a small integer instead of the
    full name once the name→alias mapping is announced at DBIRTH — the core of
    Sparkplug's bandwidth story."""
    name: str
    value: Optional[float]
    timestamp_ms: int
    alias: Optional[int] = None
    quality: Optional[int] = None      # OPC-UA style: 192 = Good

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "value": self.value,
                             "timestamp": self.timestamp_ms}
        if self.alias is not None:
            d["alias"] = self.alias
        if self.quality is not None:
            d["quality"] = self.quality
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Metric":
        return Metric(
            name=d.get("name", ""),
            value=d.get("value"),
            timestamp_ms=int(d.get("timestamp", 0)),
            alias=d.get("alias"),
            quality=d.get("quality"),
        )


@dataclass
class Payload:
    """A Sparkplug payload: a sequence number, a timestamp, and metrics."""
    seq: int
    timestamp_ms: int
    metrics: List[Metric] = field(default_factory=list)


# ── codec (pluggable — JSON now, protobuf later) ─────────────────────────────
class Codec:
    content_type = "application/octet-stream"

    def encode(self, payload: Payload) -> bytes: ...
    def decode(self, raw: bytes) -> Payload: ...


class JsonCodec(Codec):
    """Phase-1 payload: human-readable, trivially debuggable with mosquitto_sub.
    NOT wire-compatible with third-party Sparkplug tooling — see module docstring.
    """
    content_type = "application/json"

    def encode(self, payload: Payload) -> bytes:
        return json.dumps({
            "v": PROTOCOL_VERSION,
            "seq": payload.seq,
            "timestamp": payload.timestamp_ms,
            "metrics": [m.to_dict() for m in payload.metrics],
        }, separators=(",", ":")).encode()

    def decode(self, raw: bytes) -> Payload:
        d = json.loads(raw.decode())
        # A missing version means a pre-versioning payload -> treat as v1.
        v = int(d.get("v", 1))
        if v > PROTOCOL_VERSION:
            raise UnsupportedProtocolVersion(
                f"payload protocol v{v} > supported v{PROTOCOL_VERSION}")
        return Payload(
            seq=int(d["seq"]),
            timestamp_ms=int(d.get("timestamp", 0)),
            metrics=[Metric.from_dict(m) for m in d.get("metrics", [])],
        )


# Placeholder for the real thing. Swapping the gateway/historian to Sparkplug
# protobuf means implementing this against Eclipse Tahu's schema and changing
# ONE line where the codec is constructed. Left explicit so the path is obvious.
class ProtobufCodecNotYetImplemented(Codec):
    content_type = "application/protobuf"

    def encode(self, payload: Payload) -> bytes:
        raise NotImplementedError(
            "Sparkplug-B protobuf codec is a Phase-2+ deliverable "
            "(needed before external Sparkplug interop).")

    def decode(self, raw: bytes) -> Payload:
        raise NotImplementedError


DEFAULT_CODEC = JsonCodec()


# ── sequence number (per node, wraps 0..255 like Sparkplug) ──────────────────
class SeqCounter:
    """Sparkplug's seq is 0-255 and increments on every NBIRTH/NDATA/DDATA so a
    subscriber can detect a missed message and force a rebirth. NBIRTH resets it
    to 0."""
    def __init__(self):
        self._n = 0

    def reset(self) -> int:
        self._n = 0
        return 0

    def next(self) -> int:
        v = self._n
        self._n = (self._n + 1) % 256
        return v


# ── report-by-exception ──────────────────────────────────────────────────────
@dataclass
class RBEState:
    """Decides whether a new reading is worth publishing. Mirrors the webhook
    trigger logic already shipped: identical consecutive values are suppressed;
    an optional deadband ignores small moves, measured against the last value
    actually SENT so slow drift still eventually reports. A keepalive interval
    forces a publish even when quiet, so a subscriber can tell 'unchanged' from
    'gateway died'."""
    deadband: float = 0.0
    keepalive_s: float = 30.0
    _last_sent: Dict[str, float] = field(default_factory=dict)
    _last_time: Dict[str, float] = field(default_factory=dict)

    def should_send(self, tag: str, value: Optional[float], now: Optional[float] = None) -> bool:
        now = now if now is not None else time.monotonic()
        if value is None:
            return False
        prev = self._last_sent.get(tag)
        last_t = self._last_time.get(tag, 0.0)
        due = (now - last_t) >= self.keepalive_s

        if prev is not None and not due:
            if self.deadband > 0:
                if abs(value - prev) < self.deadband:
                    return False
            elif value == prev:
                return False

        self._last_sent[tag] = value
        self._last_time[tag] = now
        return True


def now_ms() -> int:
    return int(time.time() * 1000)
