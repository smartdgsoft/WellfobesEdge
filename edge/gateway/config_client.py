"""Gateway config client (management plane, edge side) — WEP-001 §8.

On startup and reconnect, the gateway PULLS its config from the center's config
service and reports the version it's running. The center is the source of truth.

Resilience is the point: if the center is unreachable (or hasn't published a
config for this gateway yet), the gateway runs on its built-in env defaults and
keeps working. A standalone SKU-1 edge with no center simply never reaches the
service and stays on env config — the management plane is optional, not required.
That keeps the edge deployable with nothing central present.

Imports nothing from center/ — it only speaks the HTTP contract. Boundary-clean.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

import urllib.request
import urllib.error
import json


class ConfigClient:
    def __init__(self, base_url: str, site: str, gateway: str, timeout_s: float = 5.0):
        self.base = base_url.rstrip("/") if base_url else ""
        self.site = site
        self.gateway = gateway
        self.timeout = timeout_s

    @property
    def enabled(self) -> bool:
        # No CONFIG_URL -> management plane off -> pure env-config edge (SKU-1).
        return bool(self.base)

    def pull(self) -> Tuple[Optional[int], Optional[dict]]:
        """Return (version, config) from the center, or (None, None) if the
        center is unreachable or has no config for this gateway. Never raises —
        the caller falls back to env defaults."""
        if not self.enabled:
            return None, None
        url = f"{self.base}/config/{self.site}/{self.gateway}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
                return int(data["version"]), data["config"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, None            # nothing published yet -> env defaults
            print(f"[config] pull failed (HTTP {e.code}) — using current config", flush=True)
            return None, None
        except Exception as exc:
            print(f"[config] center unreachable ({exc}) — using current config", flush=True)
            return None, None

    def report(self, running_version: Optional[int]) -> None:
        """Tell the center which config version we're actually running. Best
        effort — a failure here doesn't affect data flow."""
        if not self.enabled:
            return
        url = f"{self.base}/status/{self.site}/{self.gateway}"
        body = json.dumps({"running_version": running_version}).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout):
                pass
        except Exception as exc:
            print(f"[config] status report failed ({exc})", flush=True)


def apply_config(cfg: dict) -> dict:
    """Translate a pulled config document into the gateway's runtime settings.
    Bespoke configs are free-form JSON; we read the keys we understand and leave
    the rest for future phases. Returns the effective settings actually applied.

    Recognised keys (Phase 3 starter set):
      poll_ms       -> source poll interval
      keepalive_s   -> RBE keepalive
      deadband      -> RBE deadband
      tags          -> (informational for now; real tag-set control is later)
    """
    applied = {}
    if "poll_ms" in cfg:
        applied["poll_ms"] = float(cfg["poll_ms"])
    if "keepalive_s" in cfg:
        applied["keepalive_s"] = float(cfg["keepalive_s"])
    if "deadband" in cfg:
        applied["deadband"] = float(cfg["deadband"])
    if "tags" in cfg:
        applied["tags"] = list(cfg["tags"])
    return applied
