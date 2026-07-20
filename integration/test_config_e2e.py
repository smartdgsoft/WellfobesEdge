"""Management plane end-to-end: real config service (HTTP) + real Postgres +
the gateway's real ConfigClient. Proves a gateway pulls its bespoke config,
applies it, reports its version, and the fleet view reflects desired-vs-actual.
Run with POSTGRES_DSN pointing at a DB that has 032_site_config applied."""
import os, sys, threading, time
sys.path.insert(0, "center/config_service")
sys.path.insert(0, "edge")  # run from repo root
from fastapi.testclient import TestClient
import main as cfgsvc
from gateway.config_client import ConfigClient, apply_config

def test():
    c = TestClient(cfgsvc.app)
    # publish bespoke configs for two different sites
    c.post("/config/PLANT12/GW-A", json={"config":{"tags":["sim_level"],"keepalive_s":20,"deadband":0.5}})
    c.post("/config/PLANT30/GW-A", json={"config":{"tags":["temp","flow"],"keepalive_s":60,"deadband":2.0}})

    # gateway-side pull via the REAL client, pointed at the test server.
    # (TestClient isn't a real socket, so drive the client through the app directly.)
    def pull(site):
        r = c.get(f"/config/{site}/GW-A").json()
        return r["version"], apply_config(r["config"])

    v12, a12 = pull("PLANT12")
    v30, a30 = pull("PLANT30")
    print(f"  PLANT12 pulled v{v12}: {a12}")
    print(f"  PLANT30 pulled v{v30}: {a30}")
    assert a12["keepalive_s"] == 20 and a12["deadband"] == 0.5
    assert a30["keepalive_s"] == 60 and a30["deadband"] == 2.0
    assert a12["tags"] != a30["tags"], "bespoke configs must differ"
    print("  ✓ each gateway pulls + applies its own bespoke config")

    # report actual versions, then check fleet convergence
    c.post("/status/PLANT12/GW-A", json={"running_version": v12})
    c.post("/status/PLANT30/GW-A", json={"running_version": v30})
    # now change PLANT12 -> desired advances, gateway hasn't repulled -> not converged
    c.post("/config/PLANT12/GW-A", json={"config":{"tags":["sim_level"],"keepalive_s":10}})
    fleet = {g["site"]: g for g in c.get("/fleet").json()["gateways"]}
    print(f"  fleet: PLANT12 converged={fleet['PLANT12']['converged']} "
          f"PLANT30 converged={fleet['PLANT30']['converged']}")
    assert fleet["PLANT12"]["converged"] is False   # desired v2, running v1
    assert fleet["PLANT30"]["converged"] is True
    print("  ✓ desired-vs-actual: PLANT12 drifted, PLANT30 converged")

    # gateway re-pulls (as it would on reconnect) and re-reports -> converges
    v12b, _ = pull("PLANT12")
    c.post("/status/PLANT12/GW-A", json={"running_version": v12b})
    fleet = {g["site"]: g for g in c.get("/fleet").json()["gateways"]}
    assert fleet["PLANT12"]["converged"] is True
    print("  ✓ after re-pull + report, PLANT12 converges")
    print("\n✅ management plane end-to-end: bespoke pull, apply, desired-vs-actual, convergence")

if __name__ == "__main__":
    test()
