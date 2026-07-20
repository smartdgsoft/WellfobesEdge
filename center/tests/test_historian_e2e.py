"""Historian subscriber end-to-end: publish Sparkplug over a real broker, assert
the handler reassembles full site/gateway/device/tag identity and resolves an
alias-only DDATA via DBIRTH. Uses a capturing writer (no DB needed)."""
import importlib.util, os, sys, time
import paho.mqtt.client as mqtt
from wellfobes_contract import NodeIdentity, DEFAULT_CODEC, Metric, Payload, SeqCounter, now_ms

PORT = int(os.getenv("MQTT_PORT", "18842"))
os.environ.update({"MQTT_HOST":"localhost","MQTT_PORT":str(PORT)})
here = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("hist", os.path.join(here,"..","historian","main.py"))
hist = importlib.util.module_from_spec(spec); spec.loader.exec_module(hist)

def test():
    captured = []
    def capture(rows, gateway=None, batch_seq=None):
        for (k, v, q, ts) in rows:
            captured.append((k.path, v, q))
        return True
    h = hist.Historian(capture)
    h.client.connect("localhost", PORT, 30); h.client.loop_start(); time.sleep(0.5)

    node = NodeIdentity("PLANT12","GW-A"); c = DEFAULT_CODEC; seq = SeqCounter(); dev="SiemensPlc1200"
    pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="probe")
    pub.connect("localhost", PORT, 30); pub.loop_start(); time.sleep(0.3)
    pub.publish(node.nbirth_topic(), c.encode(Payload(seq.reset(), now_ms())), qos=1)
    pub.publish(node.dbirth_topic(dev), c.encode(Payload(seq.next(), now_ms(), [
        Metric("sim_level", None, now_ms(), alias=1),
        Metric("sim_pressure", None, now_ms(), alias=2)])), qos=1)
    time.sleep(0.3)
    pub.publish(node.ddata_topic(dev), c.encode(Payload(seq.next(), now_ms(),
        [Metric("sim_level", 669.0, now_ms(), alias=1, quality=192)])), qos=0)
    pub.publish(node.ddata_topic(dev), c.encode(Payload(seq.next(), now_ms(),
        [Metric("", 1.27, now_ms(), alias=2, quality=192)])), qos=0)  # alias-only
    time.sleep(0.8); pub.loop_stop(); h.client.loop_stop()

    paths = {p:(v,q) for p,v,q in captured}
    for p,v,q in captured: print(f"    {p} = {v} (q={q})")
    assert paths["PLANT12/GW-A/SiemensPlc1200/sim_level"][0] == 669.0
    assert paths["PLANT12/GW-A/SiemensPlc1200/sim_pressure"][0] == 1.27, "alias didn't resolve"
    print("  \u2713 identity reassembled; alias-only DDATA resolved via DBIRTH")

if __name__ == "__main__":
    test(); print("\n\u2705 historian works end-to-end over a real broker")
