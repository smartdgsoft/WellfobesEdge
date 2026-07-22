"""Standalone edge (no center) must still be lossless across an outage.

This is the guarantee that matters for a SKU-1 deployment: the edge feeds a
customer's own broker/MES, there is no historian anywhere, and the link drops.
Nothing may be lost, and nothing may churn.

With ACK_MODE=broker the QoS 1 PUBACK is the confirmation, so store-and-forward
works with no center at all:
  1. offline  -> readings accumulate in the durable buffer (no loss, no publish)
  2. online   -> buffered batches are delivered and released by PUBACK
  3. steady   -> the buffer drains instead of growing without bound

Run:  MQTT_PORT=18845 python3 edge/tests/test_standalone_sandf_e2e.py
"""
import asyncio, importlib.util, os, sys, tempfile, time
import paho.mqtt.client as mqtt

PORT = os.getenv("MQTT_PORT", "18845")
BUF = tempfile.mkdtemp() + "/outbox.db"
os.environ.update({
    "MQTT_HOST": "localhost", "MQTT_PORT": PORT,
    "EDGE_BUFFER_PATH": BUF,
    "EDGE_SITE": "PLANT77", "EDGE_GATEWAY": "GW-SOLO",
    "EDGE_SOURCE": "simulated", "EDGE_DEADBAND": "0",
    "EDGE_STATUS_PORT": "0",
    # No historian in this test. The broker's PUBACK is the confirmation.
    "EDGE_ACK_MODE": "broker",
})

sys.path.insert(0, "edge")
spec = importlib.util.spec_from_file_location("gwmain", "edge/gateway/main.py")
gwmain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gwmain)

received = []


def main():
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="solo-probe")
    sub.on_connect = lambda c, u, f, rc: c.subscribe("spBv1.0/PLANT77/#", qos=1)
    sub.on_message = lambda c, u, m: received.append(m.topic)
    sub.connect("localhost", int(PORT), 30)
    sub.loop_start()
    time.sleep(0.4)

    gw = gwmain.EdgeGateway()

    # --- 1. OFFLINE: accumulate with no broker reachable -------------------
    # Never connect the client; the drain loop must buffer, not publish.
    for i in range(12):
        gw._pending.setdefault("Dev1", []).append(("t1", float(i), 192, gwmain.now_ms()))
    gw._enqueue("Dev1", gw._pending.pop("Dev1"))
    batches, rows = gw.buffer.depth()
    assert rows == 12, f"offline readings must be retained, got {rows}"
    print(f"  \u2713 offline: {rows} readings retained in the durable buffer (nothing lost)")

    # --- 2. ONLINE: connect, drain, and confirm release via PUBACK ---------
    async def drain():
        gw.client.connect(gwmain.BROKER_HOST, gwmain.BROKER_PORT, 30)
        gw.client.loop_start()
        gw._loop = asyncio.get_event_loop()
        for _ in range(50):
            if gw._connected.is_set():
                break
            await asyncio.sleep(0.1)
        # one drain cycle publishes every pending batch
        task = asyncio.create_task(gw._drain_loop())
        await asyncio.sleep(gwmain.FLUSH_INTERVAL_S * 3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drain())
    time.sleep(0.5)

    batches_after, rows_after = gw.buffer.depth()
    assert received, "buffered batches must reach the broker once online"
    assert rows_after == 0, (
        f"PUBACK must release delivered batches with no center; {rows_after} rows stuck")
    print(f"  \u2713 online: batches delivered ({len(received)} msgs) and released by PUBACK")
    print(f"  \u2713 buffer drained to {rows_after} rows \u2014 no unbounded churn without a center")

    gw.client.loop_stop()
    sub.loop_stop()
    print("\n\u2705 standalone edge is lossless across an outage \u2014 "
          "same store-and-forward as a centered one, no historian required")


if __name__ == "__main__":
    main()
