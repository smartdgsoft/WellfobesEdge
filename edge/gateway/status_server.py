"""Edge-local status server — the standalone gateway's own window.

A tiny stdlib HTTP server (no framework — this may run on modest plant hardware)
that serves this ONE gateway's health: connection, buffer depth, config version,
live tag values. It depends on nothing external, so a SKU-1 edge with no center
is still observable by a technician standing next to it.

  GET /            -> the status page (self-contained HTML)
  GET /status.json -> the live status snapshot (the page polls this)

Runs in a daemon thread; reads a status snapshot via a callback so it never
touches the async pipe. Imports nothing from center/. Boundary-clean.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable


def start_status_server(port: int, snapshot: Callable[[], dict]) -> None:
    """Start the local status server in a background thread. `snapshot` returns
    the current status dict (EdgeGateway.status). No-op if port <= 0."""
    if port <= 0:
        return

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):     # keep the gateway's stdout clean
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else body.encode())

        def do_GET(self):
            if self.path.startswith("/status.json"):
                try:
                    self._send(200, json.dumps(snapshot()), "application/json")
                except Exception as exc:
                    self._send(500, json.dumps({"error": str(exc)}), "application/json")
            elif self.path == "/" or self.path.startswith("/index"):
                self._send(200, PAGE, "text/html; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain")

    srv = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name="edge-status").start()
    print(f"[status] local status page on :{port}", flush=True)


# ── the page — same control-room identity as the center console, one gateway ──
PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Edge Gateway — Status</title>
<style>
:root{--bg:#0d1117;--panel:#131a24;--panel-2:#182230;--line:#243244;--ink:#e6edf3;
--dim:#8b98a9;--faint:#5b6b7d;--accent:#3fb6ff;--ok:#3fd07a;--warn:#f0b84c;--bad:#ff5d5d;
--mono:"SF Mono",Menlo,Consolas,monospace;--sans:"Inter","Segoe UI",system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:22px 18px}
.top{display:flex;align-items:center;gap:11px;margin-bottom:20px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--faint);flex-shrink:0}
.dot.ok{background:var(--ok);box-shadow:0 0 9px var(--ok)}
.dot.bad{background:var(--bad);box-shadow:0 0 9px var(--bad)}
.top b{font-weight:700;letter-spacing:-.01em}.top .id{font-family:var(--mono);color:var(--faint);font-size:13px}
.top .stamp{margin-left:auto;font-family:var(--mono);color:var(--faint);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:15px 16px}
.stat .n{font-family:var(--mono);font-size:22px;font-weight:600;letter-spacing:-.02em}
.stat .l{color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-top:3px}
.stat.alert .n{color:var(--warn)}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);font-weight:600;margin:0 0 10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.row{display:flex;justify-content:space-between;align-items:center;padding:11px 16px;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}.row .k{font-family:var(--mono);color:var(--dim)}
.row .v{font-family:var(--mono)}.row .v.big{font-size:17px;font-weight:600}
.pill{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:12px;padding:3px 9px;border-radius:20px}
.pill.ok{color:var(--ok);background:rgba(63,208,122,.1)}.pill.bad{color:var(--bad);background:rgba(255,93,93,.1)}
.pill .d{width:7px;height:7px;border-radius:50%}.pill.ok .d{background:var(--ok)}.pill.bad .d{background:var(--bad)}
.empty{padding:26px;text-align:center;color:var(--faint);font-size:13px}
.faint{color:var(--faint)}.mt{margin-top:20px}
</style></head><body><div class="wrap">
<div class="top"><span class="dot" id="dot"></span><b>Edge Gateway</b>
<span class="id" id="id">—</span><span class="stamp" id="stamp"></span></div>
<div class="grid" id="stats"></div>
<h2>Connection</h2>
<div class="card" id="conn"></div>
<h2 class="mt">Live readings</h2>
<div class="card" id="tags"><div class="empty">—</div></div>
</div>
<script>
const $=s=>document.querySelector(s);
const fmt=v=>v==null?"—":(typeof v==="number"?(Math.abs(v)>=100?v.toFixed(0):v.toFixed(2)):v);
function dur(s){if(s<60)return s+"s";if(s<3600)return Math.floor(s/60)+"m";return Math.floor(s/3600)+"h "+Math.floor(s%3600/60)+"m";}
async function tick(){
  let d; try{ d=await (await fetch("/status.json",{cache:"no-store"})).json(); }
  catch(e){ $("#dot").className="dot bad"; return; }
  const conn=d.connected;
  $("#dot").className="dot "+(conn?"ok":"bad");
  $("#id").textContent=d.site+" / "+d.gateway;
  $("#stamp").textContent=new Date(d.now_ms).toISOString().slice(11,19)+" UTC";
  const buf=d.buffer||{};
  const backlog=buf.pending_rows||0;
  $("#stats").innerHTML=[
    ["Status",conn?"online":"offline",!conn],
    ["Buffered rows",backlog.toLocaleString(),backlog>0],
    ["Config",d.config_version!=null?("v"+d.config_version):"env",false],
    ["Uptime",dur(d.uptime_s||0),false],
  ].map(([l,n,a])=>`<div class="stat ${a?'alert':''}"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
  $("#conn").innerHTML=`
    <div class="row"><span class="k">broker</span><span class="v">${d.broker}</span></div>
    <div class="row"><span class="k">link</span><span class="pill ${conn?'ok':'bad'}"><span class="d"></span>${conn?'connected':'disconnected'}</span></div>
    <div class="row"><span class="k">source</span><span class="v">${d.source}</span></div>
    <div class="row"><span class="k">buffer</span><span class="v">${(buf.pending_batches||0)} batches / ${backlog} rows <span class="faint">/ ${(buf.max_rows||0).toLocaleString()} max</span></span></div>
    <div class="row"><span class="k">config source</span><span class="v">${d.config_url||"env (standalone)"}</span></div>
    ${d.allowed_tags?`<div class="row"><span class="k">tag allowlist</span><span class="v">${d.allowed_tags.join(", ")||"(none)"}</span></div>`:""}`;
  const tags=d.tags||[];
  $("#tags").innerHTML=tags.length? tags.map(t=>`
    <div class="row"><span class="k">${t.tag}</span>
    <span class="v big">${fmt(t.value)} <span class="faint" style="font-size:12px">q${t.quality??"—"}</span></span></div>`).join("")
    : `<div class="empty">No readings yet.</div>`;
}
tick(); setInterval(tick,2000);
</script></body></html>"""
