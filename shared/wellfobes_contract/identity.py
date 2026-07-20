"""Fleet identity model — WEP-001 §7.1.

A fully-qualified tag is a four-part path:

    {site} / {gateway} / {device} / {tag}

which maps onto the Sparkplug-B topic hierarchy:

    spBv1.0 / {group_id} / {msg_type} / {edge_node_id} / {device_id}
                  |                          |              |
                site                      gateway        device
    (tag is a metric name inside the payload)

This module is the single place that mapping lives, so the load-bearing
decision isn't scattered across the gateway and the subscriber.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SPB_NAMESPACE = "spBv1.0"

# Sparkplug message types we use in Phase 1.
NBIRTH = "NBIRTH"   # edge node came online (announces itself + metric aliases)
NDEATH = "NDEATH"   # edge node went offline (via MQTT will message)
DBIRTH = "DBIRTH"   # a device under the node came online
DDATA = "DDATA"     # a device reported values (report-by-exception)
# Phase 2: ack flowing center -> edge, confirming a history batch is durably
# stored so the edge can release it from its buffer. Not a standard Sparkplug
# message type — our extension on the same topic namespace.
DACK = "DACK"

# A site/gateway/device id must be safe in an MQTT topic: no wildcards, no
# separators, no whitespace. Keep it strict so identity can't be spoofed by a
# tag name containing a slash.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def valid_id(part: str) -> bool:
    return bool(_ID_RE.match(part))


@dataclass(frozen=True)
class NodeIdentity:
    """Identifies this edge gateway within the fleet. Set from config; it's who
    the gateway *is*, not what it reads."""
    site: str          # group_id  — the plant, unique within the enterprise
    gateway: str       # edge_node_id — which gateway at the site

    def __post_init__(self):
        for name, val in (("site", self.site), ("gateway", self.gateway)):
            if not valid_id(val):
                raise ValueError(
                    f"{name}={val!r} is not a valid identity part "
                    "(letters, digits, _ and - only; 1-64 chars)")

    # ── topic builders ──────────────────────────────────────────────────
    def topic(self, msg_type: str, device: str | None = None) -> str:
        base = f"{SPB_NAMESPACE}/{self.site}/{msg_type}/{self.gateway}"
        return f"{base}/{device}" if device else base

    def nbirth_topic(self) -> str:
        return self.topic(NBIRTH)

    def ndeath_topic(self) -> str:
        return self.topic(NDEATH)

    def dbirth_topic(self, device: str) -> str:
        return self.topic(DBIRTH, device)

    def ddata_topic(self, device: str) -> str:
        return self.topic(DDATA, device)

    def dack_topic(self) -> str:
        # center publishes acks here; this gateway subscribes to its own.
        return self.topic(DACK)


def ack_topic_for(site: str, gateway: str) -> str:
    """The DACK topic a given gateway listens on (center publishes here)."""
    return f"{SPB_NAMESPACE}/{site}/{DACK}/{gateway}"


def subscribe_pattern(site: str = "+", gateway: str = "+") -> str:
    """Topic filter for a subscriber (the historian). Defaults to all sites and
    all gateways; narrow either to scope. '#' catches every message type and
    device beneath."""
    return f"{SPB_NAMESPACE}/{site}/+/{gateway}/#"


@dataclass(frozen=True)
class MetricKey:
    """A fully-qualified tag, reassembled on the subscriber side from the topic
    (site, gateway, device) plus the metric name (tag) in the payload."""
    site: str
    gateway: str
    device: str
    tag: str

    @property
    def path(self) -> str:
        return f"{self.site}/{self.gateway}/{self.device}/{self.tag}"


def parse_topic(topic: str) -> tuple[str, str, str, str | None]:
    """Inverse of the topic builders: -> (site, msg_type, gateway, device|None).

    Raises ValueError on anything that isn't a well-formed Sparkplug topic in
    our namespace, so a malformed publish can't smuggle bad identity through.
    """
    parts = topic.split("/")
    if len(parts) < 4 or parts[0] != SPB_NAMESPACE:
        raise ValueError(f"not a {SPB_NAMESPACE} topic: {topic!r}")
    _, site, msg_type, gateway, *rest = parts
    device = rest[0] if rest else None
    for name, val in (("site", site), ("gateway", gateway)):
        if not valid_id(val):
            raise ValueError(f"invalid {name} in topic: {val!r}")
    if device is not None and not valid_id(device):
        raise ValueError(f"invalid device in topic: {device!r}")
    return site, msg_type, gateway, device
