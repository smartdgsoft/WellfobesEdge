"""Runs the real edge gateway (simulated source) against a broker; a lightweight
in-test subscriber (using only the shared contract) captures and asserts full
identity + report-by-exception. Proves the edge works with only edge + shared."""
import asyncio, importlib.util, os, sys, time
from collections import Counter
import paho.mqtt.client as mqtt
from wellfobes_contract import parse_topic, DEFAULT_CODEC, DBIRTH, DDATA, NBIRTH, NDEATH

PORT = os.getenv("MQTT_PORT", "18840")
import tempfile
os.environ.update({"MQTT_HOST":"localhost","MQTT_PORT":PORT,
    "EDGE_BUFFER_PATH": tempfile.mkdtemp()+"/outbox.db",
    "EDGE_SITE":"PLANT30","EDGE_GATEWAY":"GW-B","EDGE_SOURCE":"simulated",
    "EDGE_KEEPALIVE_S":"2","EDGE_DEADBAND":"0",
    # This is the SKU-1 / live-path test: RBE governs the live stream and there
    # is no historian here to ack. Durability (buffer, redelivery, ack-release)
    # has its own test — test_durability_e2e.py — with a real acker.
    "EDGE_DELIVERY_MODE":"live"})

# load the real gateway module
here = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(here, ".."))  # so `gateway` package resolves
spec = importlib.util.spec_from_file_location("gwmain", os.path.join(here, "..","gateway","main.py"))
gwmain = importlib.util.module_from_spec(spec); spec.loader.exec_module(gwmain)

def _decode(topic, payload, aliases, out):
    try: site, mtype, gw, dev = parse_topic(topic)
    except ValueError: return
    p = DEFAULT_CODEC.decode(payload)
    if mtype == DBIRTH and dev:
        aliases[(site,gw,dev)] = {m.alias:m.name for m in p.metrics if m.alias is not None}
    elif mtype == DDATA and dev:
        amap = aliases.get((site,gw,dev), {})
        is_history = p.batch_seq is not None   # history batches carry a batch_seq
        for m in p.metrics:
            tag = m.name or amap.get(m.alias)
            if tag: out.append((f"{site}/{gw}/{dev}/{tag}", m.value, is_history))

async def main():
    out, aliases = [], {}
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="probe")
    sub.on_connect = lambda c,u,f,rc: c.subscribe("spBv1.0/#", qos=1)
    sub.on_message = lambda c,u,msg: _decode(msg.topic, msg.payload, aliases, out)
    sub.connect("localhost", int(PORT), 30); sub.loop_start(); time.sleep(0.5)

    gw = gwmain.EdgeGateway()
    task = asyncio.create_task(gw.run())
    await asyncio.sleep(5.0); task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    time.sleep(0.3); sub.loop_stop()

    live = [(p, v) for (p, v, is_hist) in out if not is_hist]
    history = [(p, v) for (p, v, is_hist) in out if is_hist]
    paths = {p for p, _ in live}
    print(f"  captured {len(live)} live readings, {len(paths)} tags")
    for p in sorted(paths): print(f"    {p}")

    expect = {f"PLANT30/GW-B/SiemensPlc1200/{t}" for t in ("sim_level","sim_pressure","sim_running")}
    assert expect.issubset(paths), f"missing {expect-paths}"

    # RBE is a live-path property: a constant tag must be suppressed down to just
    # keepalive republishes. Measured on the LIVE stream only — history batches
    # (a separate durable path) redeliver until acked and would mask this.
    counts = Counter(p for p, _ in live)
    assert counts["PLANT30/GW-B/SiemensPlc1200/sim_running"] <= 3, "constant tag should stay sparse"

    # Live mode is the standalone SKU-1: it must emit NO durable history batches
    # (nothing here would ever ack them). This locks in the delivery-mode split.
    assert not history, f"live mode must not emit history batches, saw {len(history)}"
    print("  \u2713 all tags arrived with full identity; RBE kept the constant tag sparse; "
          "live mode emitted no history")

if __name__ == "__main__":
    asyncio.run(main()); print("\n\u2705 edge gateway works end-to-end (edge + shared only)")
