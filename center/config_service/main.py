"""Config service (management plane) — WEP-001 §8.

A small HTTP API on the center. The center is the source of truth for each
site's desired config; gateways PULL their own config here on startup/reconnect,
and report back which version they're actually running. Bespoke per site — each
gateway has a whole explicit config document, no templates.

Imports nothing from edge/ — it only speaks the HTTP contract. (Boundary-clean.)

Endpoints:
  GET  /config/{site}/{gateway}          -> that gateway's latest config + version
  POST /config/{site}/{gateway}          -> publish a NEW config version (admin)
  POST /status/{site}/{gateway}          -> gateway reports its running version
  GET  /fleet                            -> desired-vs-actual across all gateways
  GET  /healthz                          -> liveness
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

PG_DSN = os.getenv("POSTGRES_DSN", "").replace("postgresql+asyncpg://", "postgresql://")

app = FastAPI(title="Wellfobes Fleet — Config Service")


def _conn():
    # Short-lived connections keep this simple and resilient; config traffic is
    # low-rate (startup + reconnect + occasional admin), so no pool needed.
    return psycopg2.connect(PG_DSN)


def _connect_with_retry(timeout_s: int = 60):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            c = psycopg2.connect(PG_DSN); c.close(); return
        except Exception as exc:
            last = exc; time.sleep(2)
    raise RuntimeError(f"config db unreachable in {timeout_s}s: {last}")


@app.on_event("startup")
def _startup():
    _connect_with_retry()


# ── models ────────────────────────────────────────────────────────────────────
class ConfigOut(BaseModel):
    site: str
    gateway: str
    version: int
    config: dict


class ConfigIn(BaseModel):
    config: dict
    note: Optional[str] = None


class StatusIn(BaseModel):
    running_version: Optional[int] = None


# ── gateway-facing: pull config ──────────────────────────────────────────────
@app.get("/config/{site}/{gateway}")
def get_config(site: str, gateway: str):
    """The gateway calls this on startup and reconnect. Returns the latest
    version for this specific gateway. 404 if none has been published yet — the
    gateway then runs on its built-in env defaults (see gateway config client)."""
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT site, gateway, version, config
                 FROM site_config
                WHERE site=%s AND gateway=%s
                ORDER BY version DESC LIMIT 1""", (site, gateway))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="no config published for this gateway")
    return row


# ── gateway-facing: report actual running version ────────────────────────────
@app.post("/status/{site}/{gateway}")
def report_status(site: str, gateway: str, body: StatusIn):
    """The gateway reports which config version it's actually running, so the
    center can show desired-vs-actual. Upserts last_seen too (a heartbeat)."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO gateway_status (site, gateway, running_version, last_seen)
               VALUES (%s,%s,%s, now())
               ON CONFLICT (site, gateway)
               DO UPDATE SET running_version=EXCLUDED.running_version,
                             last_seen=now()""",
            (site, gateway, body.running_version))
        c.commit()
    return {"ok": True}


# ── admin: publish a new config version ──────────────────────────────────────
@app.post("/config/{site}/{gateway}")
def put_config(site: str, gateway: str, body: ConfigIn):
    """Publish a NEW version of a gateway's config (monotonic per gateway). This
    is the desired-state change; the gateway picks it up on its next pull."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM site_config WHERE site=%s AND gateway=%s",
            (site, gateway))
        next_ver = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO site_config (site, gateway, version, config, note)
               VALUES (%s,%s,%s,%s,%s)""",
            (site, gateway, next_ver, psycopg2.extras.Json(body.config), body.note))
        c.commit()
    return {"site": site, "gateway": gateway, "version": next_ver}


# ── admin/observability: fleet desired-vs-actual ─────────────────────────────
@app.get("/fleet")
def fleet():
    """The reason the center exists: one query, every gateway's desired vs actual
    config version, and whether it's converged. With one site this is trivial;
    with fifty it's the fleet dashboard."""
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            WITH desired AS (
                SELECT site, gateway, MAX(version) AS desired_version
                  FROM site_config GROUP BY site, gateway)
            SELECT d.site, d.gateway, d.desired_version,
                   s.running_version, s.last_seen,
                   (s.running_version IS NOT DISTINCT FROM d.desired_version) AS converged
              FROM desired d
              LEFT JOIN gateway_status s USING (site, gateway)
             ORDER BY d.site, d.gateway""")
        return {"gateways": cur.fetchall()}


@app.get("/healthz")
def healthz():
    return {"ok": True}
