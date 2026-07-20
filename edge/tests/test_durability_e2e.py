"""Phase-2 durability: prove end-to-end ack + lossless redelivery.

Scenario:
  1. gateway buffers a batch to disk
  2. publish it; a historian writes + acks; gateway releases it  (happy path)
  3. simulate a LOST ACK: historian writes but ack never reaches the gateway ->
     batch stays buffered -> gateway redelivers -> historian dedupes (no dup rows)
  4. restart persistence: un-acked batch survives a buffer reopen

Uses the real DeliveryBuffer + real wire contract against a live broker. The
"historian" here is an in-test double that records writes and can be told to
drop its ack, so we can force the lost-ack path deterministically.
"""
import os, sys, time, tempfile
import paho.mqtt.client as mqtt
sys.path.insert(0, "edge")
from gateway.buffer import DeliveryBuffer
from wellfobes_contract import (NodeIdentity, DEFAULT_CODEC, Metric, Payload,
    SeqCounter, now_ms, decode_ack, encode_ack, parse_topic, DDATA, ack_topic_for)

PORT = int(os.getenv("MQTT_PORT", "18880"))
SITE, GW, DEV = "PLANT12", "GW-A", "SiemensPlc1200"

class HistorianDouble:
    """Writes to an in-memory 'DB' with dedupe; can drop acks on command."""
    def __init__(self, drop_acks_until=0):
        self.rows = {}                 # (batch_seq,tag,ts)->value  (dedupe key)
        self.drop_acks_until = drop_acks_until   # drop acks for batch<=this
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="hist2")
        self.c.on_connect = lambda cl,u,f,rc: cl.subscribe(f"spBv1.0/{SITE}/+/{GW}/#", qos=1)
        self.c.on_message = self._on
        self.c.connect("localhost", PORT, 30); self.c.loop_start()
    def _on(self, cl, u, msg):
        try: site,mt,gw,dev = parse_topic(msg.topic)
        except ValueError: return
        if mt != DDATA: return
        p = DEFAULT_CODEC.decode(msg.payload)
        if p.batch_seq is None: return
        # idempotent write (dedupe)
        for m in p.metrics:
            self.rows[(p.batch_seq, m.name, m.timestamp_ms)] = m.value
        # ack unless we're told to drop this one
        if p.batch_seq > self.drop_acks_until:
            cl.publish(ack_topic_for(site, gw), encode_ack(p.batch_seq), qos=1)
    def stop(self): self.c.loop_stop()

def run():
    d = tempfile.mkdtemp(); bpath = os.path.join(d, "outbox.db")
    buf = DeliveryBuffer(bpath)
    node = NodeIdentity(SITE, GW); codec = DEFAULT_CODEC; seq = SeqCounter()

    # gateway-side publisher + ack consumer
    freed = []
    pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="gw2")
    def on_ack(cl,u,msg):
        bs = decode_ack(msg.payload); n = buf.ack(bs)
        if n: freed.append(bs)
    pub.on_connect = lambda cl,u,f,rc: cl.subscribe(node.dack_topic(), qos=1)
    pub.on_message = on_ack
    pub.connect("localhost", PORT, 30); pub.loop_start(); time.sleep(0.4)

    def publish_all():
        for s in buf.pending_batches():
            rows = buf.batch_rows(s)
            mets = [Metric(t, v, ts, quality=q) for (_dv,t,v,q,ts) in rows]
            pub.publish(node.ddata_topic(DEV),
                codec.encode(Payload(seq.next(), now_ms(), mets, batch_seq=s)), qos=1)

    # ── happy path: historian acks; buffer should drain ──
    hist = HistorianDouble(drop_acks_until=0)
    time.sleep(0.4)
    b1 = buf.append([(DEV,"sim_level",1.0,192,111),(DEV,"sim_pressure",2.0,192,111)])
    publish_all(); time.sleep(1.0)
    assert buf.pending_batches() == [], f"happy path should drain, got {buf.pending_batches()}"
    print(f"  ✓ happy path: batch {b1} acked and released")

    # ── lost-ack path: historian writes but ack dropped -> stays buffered ──
    hist.stop()
    hist2 = HistorianDouble(drop_acks_until=999999)  # drop ALL acks
    time.sleep(0.4)
    b2 = buf.append([(DEV,"sim_level",1.1,192,222)])
    publish_all(); time.sleep(1.0)
    assert buf.pending_batches() == [b2], "lost ack -> batch must remain buffered"
    writes_after_first = len(hist2.rows)
    print(f"  ✓ lost ack: batch {b2} still buffered (historian wrote {writes_after_first} row)")

    # redeliver twice -> historian dedupes -> still exactly 1 logical row
    publish_all(); time.sleep(0.5); publish_all(); time.sleep(0.5)
    assert len(hist2.rows) == writes_after_first, "redelivery must NOT create duplicate rows"
    print(f"  ✓ redelivered 2x, historian deduped -> still {len(hist2.rows)} row (no dupes)")

    # ── restart persistence: un-acked batch survives buffer reopen ──
    buf.close()
    buf2 = DeliveryBuffer(bpath)
    assert buf2.pending_batches() == [b2], "un-acked batch must survive restart"
    print(f"  ✓ restart: un-acked batch {b2} survived buffer reopen")
    buf2.close(); hist2.stop(); pub.loop_stop()
    print("\n✅ end-to-end durability: ack-release, lost-ack redelivery, dedupe, restart-persistence")

if __name__ == "__main__":
    run()
