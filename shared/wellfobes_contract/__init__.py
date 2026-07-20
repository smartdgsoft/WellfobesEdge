"""wellfobes_contract — the wire contract between edge and center.

This package is the *only* thing edge and center share. It depends on neither of
them; both depend on it. Keeping it dependency-free in both directions is what
lets the edge deploy standalone (edge + this) and lets the monorepo split into
two repos later (this becomes a published package both pull) with no untangling.

PROTOCOL_VERSION is stamped into every payload. Today edge and center move in
lockstep so it's always current — but the instant they're two repos on
independent release cycles, an old edge will talk to a new center, and this is
what lets the center recognise the format instead of silently mis-parsing.
Bump it only on a breaking wire change.
"""
from .identity import (                                  # noqa: F401
    SPB_NAMESPACE, NBIRTH, NDEATH, DBIRTH, DDATA, DACK,
    NodeIdentity, MetricKey, parse_topic, subscribe_pattern, ack_topic_for, valid_id,
)
from .sparkplug import (                                 # noqa: F401
    PROTOCOL_VERSION, Metric, Payload, Codec, JsonCodec, DEFAULT_CODEC,
    SeqCounter, RBEState, now_ms, UnsupportedProtocolVersion,
    encode_ack, decode_ack,
)

__version__ = "0.1.0"
