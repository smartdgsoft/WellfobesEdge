"""Unit tests for the load-bearing logic: identity mapping, topic round-trip,
sequence numbers, report-by-exception. No broker needed."""
from wellfobes_contract import (NodeIdentity, parse_topic, subscribe_pattern,
    valid_id, SPB_NAMESPACE, DDATA, JsonCodec, Metric, Payload, RBEState,
    SeqCounter, now_ms, PROTOCOL_VERSION, UnsupportedProtocolVersion)


def test_identity_validation():
    NodeIdentity("PLANT12", "GW-A")                       # ok
    for bad in ("has space", "has/slash", "has+wild", "has#hash", "", "x" * 65):
        try:
            NodeIdentity(bad, "GW-A"); assert False, f"{bad!r} should be rejected"
        except ValueError:
            pass
    print("  ✓ identity rejects unsafe ids")


def test_topic_roundtrip():
    node = NodeIdentity("PLANT12", "GW-A")
    t = node.ddata_topic("SiemensPlc1200")
    assert t == f"{SPB_NAMESPACE}/PLANT12/{DDATA}/GW-A/SiemensPlc1200", t
    site, msg_type, gateway, device = parse_topic(t)
    assert (site, msg_type, gateway, device) == ("PLANT12", DDATA, "GW-A", "SiemensPlc1200")
    print("  ✓ topic build/parse round-trips")


def test_parse_rejects_foreign():
    for bad in ("spBv1.0/PLANT12", "other/PLANT12/DDATA/GW-A", "spBv1.0/has space/DDATA/GW-A/d"):
        try:
            parse_topic(bad); assert False, f"{bad!r} should be rejected"
        except ValueError:
            pass
    print("  ✓ parse rejects foreign/malformed topics")


def test_subscribe_pattern():
    assert subscribe_pattern() == f"{SPB_NAMESPACE}/+/+/+/#"
    assert subscribe_pattern("PLANT12") == f"{SPB_NAMESPACE}/PLANT12/+/+/#"
    print("  ✓ subscribe pattern scopes by site/gateway")


def test_codec_roundtrip():
    p = Payload(seq=3, timestamp_ms=now_ms(),
                metrics=[Metric("sim_level", 669.0, now_ms(), alias=1, quality=192)])
    c = JsonCodec()
    back = c.decode(c.encode(p))
    assert back.seq == 3
    assert back.metrics[0].name == "sim_level"
    assert back.metrics[0].value == 669.0
    assert back.metrics[0].alias == 1
    assert back.metrics[0].quality == 192
    print("  ✓ payload codec round-trips")


def test_seq_wraps():
    s = SeqCounter()
    assert s.reset() == 0
    vals = [s.next() for _ in range(258)]
    assert vals[0] == 0 and vals[255] == 255 and vals[256] == 0 and vals[257] == 1
    print("  ✓ seq wraps 0..255")


def test_rbe():
    # identical values suppressed; keepalive off for the test
    r = RBEState(deadband=0, keepalive_s=1e9)
    t = 1000.0
    assert r.should_send("d/t", 1.0, now=t) is True         # first
    assert r.should_send("d/t", 1.0, now=t) is False        # dup
    assert r.should_send("d/t", 2.0, now=t) is True         # changed

    # deadband vs last-SENT (drift accumulates)
    r = RBEState(deadband=5.0, keepalive_s=1e9)
    seq = [r.should_send("d/t", v, now=t) for v in (10, 12, 14, 16)]
    assert seq == [True, False, False, True], seq

    # keepalive forces a send even when unchanged
    r = RBEState(deadband=0, keepalive_s=30)
    assert r.should_send("d/t", 1.0, now=0) is True
    assert r.should_send("d/t", 1.0, now=10) is False       # unchanged, not due
    assert r.should_send("d/t", 1.0, now=40) is True        # keepalive due
    print("  ✓ RBE: dedupe, deadband-vs-sent, keepalive")


def test_protocol_version():
    c = JsonCodec()
    raw = c.encode(Payload(seq=0, timestamp_ms=now_ms(), metrics=[]))
    import json
    assert json.loads(raw)["v"] == PROTOCOL_VERSION
    # a future version must be rejected, not mis-parsed
    future = raw.replace(b'"v":1', b'"v":999')
    try:
        c.decode(future); assert False, "future version should be rejected"
    except UnsupportedProtocolVersion:
        pass
    # a pre-versioning payload (no v) decodes as v1
    legacy = json.dumps({"seq":0,"timestamp":0,"metrics":[]}).encode()
    c.decode(legacy)
    print("  \u2713 protocol version stamped, future rejected, legacy tolerated")


if __name__ == "__main__":
    for fn in [test_identity_validation, test_topic_roundtrip, test_parse_rejects_foreign,
               test_subscribe_pattern, test_codec_roundtrip, test_seq_wraps, test_rbe, test_protocol_version]:
        fn()
    print("\n✅ all unit tests pass")
