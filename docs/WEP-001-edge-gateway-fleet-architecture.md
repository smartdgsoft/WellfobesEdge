# WEP-001 — Edge Gateway & Multi-Site Fleet Architecture

**Status:** Draft for discussion
**Product:** Wellfobes IQ
**Author:** Architecture discussion, this session
**Supersedes:** nothing (first proposal)

---

## 1. Summary

Split data **acquisition** out of the central platform into an independent,
stateless **edge gateway**. The gateway acquires data from plant equipment,
normalizes it, and publishes it over MQTT (Sparkplug-B). The central platform —
broker, historian, API — becomes a set of subscribers and services on the other
end of that boundary.

This enables three things the current monolithic stack cannot do cleanly:

1. A plant keeps collecting through a WAN outage (store-and-forward).
2. Many sites report to one historian without any site reaching into another.
3. A customer can buy the edge gateway **alone** to feed their own MES/ERP,
   with no central platform at all.

One architectural principle governs the whole design and keeps the product line
coherent:

> **The edge buffers for delivery, never for retrieval.**
> Data may pass through the edge and wait there during an outage, but the edge
> never *serves a read*. Anything that queries history requires the central
> platform. Retention is the center's job, exclusively.

---

## 2. Motivation

The current Wellfobes IQ stack runs acquisition (`opcua-client`,
`connector-hub`), storage (TimescaleDB historian), serving (FastAPI Data API,
webhook dispatcher), and analytics (twin) as one tightly-coupled Docker Compose
deployment. That is correct for a single site. It does not answer:

- **Resilience:** if the link between a remote plant and the center drops, data
  is lost for the duration.
- **Multi-site:** there is no notion of "site" as a first-class dimension; a tag
  is globally named, so 50 plants each with a `sim_level` collide.
- **Standalone edge:** a customer who wants only data acquisition — feeding their
  own systems — has to take the whole stack, including a historian they don't
  want.

The business direction is now explicit: **one company operating ~50 of its own
plants**, plus **individual customers who want the edge gateway to feed their own
MES/ERP**. Those are two different shapes on one codebase, and this proposal is
about drawing the boundaries so both are served without forking.

---

## 3. Goals and non-goals

### Goals

- A **stateless** edge gateway: acquire, normalize, buffer-for-delivery,
  publish. No local historian, no queryable store, no retention config.
- **MQTT / Sparkplug-B** as the edge→center transport (decided — see §6).
- A **site-first identity model** so cross-site queries and per-site scoping are
  natural, not bolted on.
- Two clean SKUs: **Edge Gateway standalone** and **Edge Gateway + Central**.
- Preserve the existing consumer-facing surface (Data API, keys, scopes, tag
  allowlists, webhooks) as the center's public face — it does not get rebuilt.

### Non-goals (explicitly out of scope for now)

- A historian at the edge, in any form. Ruled out by the core principle.
- Multi-**tenant** hostile isolation. The fleet is one company's own plants;
  cross-plant visibility is a feature, not a threat. (A future vendor-hosted
  offering would revisit this — noted in §12.)
- Running the full analytics/twin stack at the edge ("smart node"). Start thin.
  The `FEATURE_*` per-service flags leave this open for later without a rewrite.
- Fleet management UI polish. The management plane's *contract* is in scope; a
  pretty console is not, yet.

---

## 4. The core principle, stated precisely

The edge is allowed to hold data **in flight** — a bounded on-disk buffer that
absorbs a network outage and drains, in order, when the link returns. That is
local state, but it is *transient forwarding* state:

- **Write-once, drain-once.** The buffer is filled by acquisition and emptied by
  the publisher. Nothing else touches it.
- **Never read for a query.** There is no API, no OPC UA server, no SQL endpoint
  at the edge that returns historical rows. The buffer cannot be *asked a
  question*; it can only *flush forward*.
- **Bounded.** It holds a configured maximum (hours or MB), sized as outage
  insurance — not as a store. When full, the oldest-first policy and back-pressure
  behaviour are explicit (see §7.4), not accidental.

This single line resolves the product line (§5), keeps the edge effectively
stateless from a user's standpoint (nothing to query, govern, or back up), and
preserves completeness through outages.

---

## 5. Product line

### SKU 1 — Edge Gateway (standalone)

Acquire → normalize → buffer-for-delivery → publish. Moves data **live** to the
customer's own systems: their MQTT broker, their historian, their MES/ERP, or a
webhook. Retains nothing queryable.

- **Value proposition:** connectivity. "We get data off your plant floor into
  your systems, reliably, through outages."
- **Buyer:** the customer who brings their own MES/ERP.
- **Consumption:** see §8 — the customer's system pulls, or the gateway pushes,
  depending on what their MES expects.

### SKU 2 — Edge Gateway + Central (with historian)

The full stack. Data lands centrally, is retained, queried, rolled up across
sites, and served through the existing Data API.

- **Value proposition:** the historian and everything above it — history,
  dashboards, fleet comparison, the third-party Data API.
- **Buyer:** the ~50-plant operator; anyone who wants to *keep and use* history.

### Why the split stays clean

The boundary *is* the product line. The edge never retains; retention is the
center's whole job. So the upgrade path sells itself: a SKU-1 customer who later
asks "how do I see last month's data?" has exactly one answer — add the Central.
There is no crippled-feature awkwardness, because the edge is *architecturally
incapable* of retention by design.

**Discipline to hold:** the pressure to let SKU 1 "keep a little history" will
come. Saying yes once blurs the boundary and erodes SKU 2's value. The core
principle is what lets us decline cleanly — it is not a sales position, it is how
the product is built.

---

## 6. Transport: MQTT / Sparkplug-B (decided)

Edge→center is **MQTT with the Sparkplug-B specification** on top. Rationale:

- It is the OT-industry default for exactly this problem, so customers'
  systems (and their integrators) already speak it.
- Sparkplug-B adds what raw MQTT lacks and what we would otherwise hand-roll and
  get subtly wrong:
  - **A defined topic namespace** — which forces the identity decision (§7.1)
    up front instead of discovering it at site 20.
  - **Birth/death certificates** — a subscriber knows a gateway or device went
    *offline* versus merely *quiet*. This is the self-registration mechanism that
    makes onboarding site N+1 near-zero-touch.
  - **Report-by-exception (RBE)** — publish on change with periodic keepalives.
    This is the same problem solved by hand in the current webhook trigger work,
    handled at the protocol level.

**Where MQTT lives, and where it does *not*:** MQTT/Sparkplug-B is the *internal*
edge→center backbone. The center's *public* face stays the existing HTTP Data API
(REST/SSE/WebSocket, API keys, scopes, tag allowlists). A third party integrating
with the center wants `GET /api/v1/data/live` with a key — not to stand up a
Sparkplug subscriber. So: **MQTT inward, HTTP outward.** The historian is one
MQTT subscriber; the Data API is a service beside it. (For SKU 1, the customer's
MES may itself be an MQTT/Sparkplug subscriber — then MQTT is also the outward
face *for that customer*. That is their choice of consumption, §8.)

---

## 7. The edge gateway

### 7.1 Identity model (the load-bearing decision)

This is the one thing genuinely painful to change later, because it propagates
into every historian row, API path, and key scope. Sparkplug-B's topic hierarchy
maps to it directly:

```
Sparkplug topic:  spBv1.0 / {group_id} / {msg_type} / {edge_node_id} / {device_id}
Our meaning:                  site          —            gateway         source/PLC
Metric (in payload):                                                     tag
```

A fully-qualified tag is therefore a **four-part path**:

```
{site} / {gateway} / {device} / {tag}
   |         |          |         |
 PLANT12   GW-A     SiemensPlc1200   sim_level
```

- **site** — the plant. Globally unique within the enterprise. The dimension HQ
  groups and compares by.
- **gateway** — a site may run more than one edge node; this identifies which.
- **device** — a source/PLC under a gateway (matches today's `src:...` notion).
- **tag** — the metric (today's `display_name` / node).

**Reconciliation with today's UUIDs:** the central historian keeps a registry
mapping `(site, gateway, device, tag) → internal tag_id (UUID)`. Existing queries
and the Data API keep working against `tag_id`; the four-part path is the natural
key and the thing humans and cross-site queries use. `site` becomes a column on
the historian's value tables (partition dimension — see §9).

**Consequence for the Data API:** the per-key **tag allowlist** built this
session gains a **site axis** — "this key reads PLANT12 only", or an HQ key reads
all sites. Conceptually the same deny-by-default machinery, one level up. Not a
new security tier — an extra dimension on the one we have.

### 7.2 What runs on the gateway

Stateless services, mirroring today's collector but with no storage role:

- **Acquire** — the existing OPC UA / Modbus / source drivers (`opcua-client`,
  `connector-hub` drivers), polling and normalizing to the canonical payload.
- **Buffer-for-delivery** — the bounded on-disk queue (today's SQLite local
  buffer seed), write-once/drain-once, never read for a query.
- **Publisher** — the Sparkplug-B client: births on connect, publishes RBE +
  keepalive, deaths on disconnect.
- **Agent** — the management-plane client (§8/§10): reports actual state, pulls
  desired config, reconciles.
- **Health/observability** — connected? publishing? buffer depth? last delivery?
  Critical for SKU 1, where we *cannot* see the customer's downstream (§11).

### 7.3 The two data paths inside the gateway

One acquisition, two paths with **opposite guarantees**:

- **Live path** — taps the fresh value, publishes on-change (RBE), newest-wins.
  If the link is congested it *drops stale* readings; nobody wants a "live" feed
  replaying a backlog.
- **History path** — every sample into the buffer-for-delivery, held until the
  center **acks**, drained in order. A six-hour outage: live pauses; history
  accumulates and drains on reconnect. Completeness preserved without the edge
  ever becoming a store.

### 7.4 Buffer policy (must be explicit, not accidental)

- **Bound:** configured max (hours and/or MB).
- **Overflow:** oldest-first drop when full, with a loud health signal *before*
  the drop, so the center/operator sees it coming.
- **Back-pressure / reconnect flood:** when a gateway reconnects after a long
  outage, it must drain at a *rate-limited* pace so 30 sites recovering from a
  regional blip don't stampede the historian at once (§9).
- **Ordering:** in-order drain per device; the historian must tolerate
  late-arriving history without corrupting live (two data paths, already a
  distinction the current system understands).

---

## 8. The management plane (center→edge)

Data flows edge→center. **Configuration flows the opposite way**, and this is the
half that decides whether 5 sites become 50 gracefully. It is a *desired-vs-actual*
reconciliation loop, not a push:

- The **center holds desired state** — per site: tag definitions, alarm limits,
  calc formulas, feature flags, gateway software version.
- Each **gateway reports actual state** and **reconciles toward desired** when it
  can reach the center.
- A site offline during a config push **converges automatically** when it
  reconnects — no SSH, no manual reapply.

For **~50 of your own similar plants**, this is tractable because it is
**templates-with-overrides**: define the house standard for a plant type once at
the fleet level, apply to all sites, override per-site where a plant genuinely
differs. Inheritance does the heavy lifting; onboarding a new plant of a known
type is "apply the template."

**Note the shift from today:** the live-flag system built this session reads
config from *one shared DB*. Across sites, each edge has its own local state and
the center reconciles to it. That is a genuinely different pattern and is the
real engineering of the fleet — budget for it as co-equal with the data plane,
not an afterthought.

### 8.1 Management-plane modes (design for three now)

Because SKU 1 exists (customer-owned edge that may never phone home to us), the
gateway's config/update/licensing mechanism must work in **three modes**, decided
now so both businesses run on one codebase:

1. **Center-managed** — the fleet case. Center is always reachable; it owns
   desired state.
2. **Self-contained** — customer owns the edge, it never contacts us; config is
   local, updates are manual or via a channel they permit.
3. **Update-channel** — customer-owned, but we can push *signed* updates through
   a door they control.

Baking mode-awareness in early is cheap; bolting it on later forks the codebase.
This also improves the fleet design: the center↔edge contract should **not assume
the center is always there**, which makes the whole system more robust.

---

## 9. The central platform

Largely what exists today, re-cast:

- **MQTT broker** — the new internal backbone. Gateways publish here.
- **Historian** — a **subscriber** to the broker, writing to TimescaleDB. Gains
  a **`site` dimension**: partition by site as well as time; retention may differ
  per site; the reconnect-flood is real, so ingest needs rate-aware back-pressure.
- **Data API** — unchanged public face (REST/SSE/WebSocket). Keys/scopes/tag
  allowlists gain the site axis (§7.1).
- **Config service** — holds fleet desired state, serves the management plane
  (§8), template + override model.
- **Twin / analytics** — unchanged, central-only for now.

The historian being *just another subscriber* is the point: "historian ingest
AND live-to-a-consumer" falls out for free, because nothing was coded to "send to
our center" — it was coded to "publish," and who subscribes is deployment config.
That same property is what makes SKU 1 a *configuration* (point the gateway's
outbound at the customer's broker/historian) rather than a new product.

---

## 10. Security model

Two distinct concerns; do not conflate them:

- **Consumer auth (exists):** API keys, scopes, per-key tag allowlists — humans
  and apps proving themselves to the Data API. Extends with the site axis.
- **Device auth (new):** each **gateway** is a principal that authenticates to
  the center (or to a customer's broker), with a **per-gateway credential that
  can be revoked without touching the others** — a compromised gateway at one
  site must not be a master key to all. Mutual TLS or per-node tokens. This runs
  the *opposite* direction from consumer auth (the gateway proving itself), and
  is a different mechanism — do not stretch the API-key system to cover it.

---

## 11. Operational reality (esp. SKU 1)

A SKU-1 customer owns their data but still calls **us** when their MES isn't
getting values — and the fault is often on *their* side of the boundary (their
broker, network, MES config). Since we **cannot see their downstream**, the
gateway must expose strong **edge-side health**: connected, publishing, buffer
depth, last-delivery timestamp, birth/death status. That telemetry is what keeps
SKU 1 from becoming a support sinkhole. Build it into the gateway from day one,
not as a later addition.

---

## 12. Open decisions (not blocking the first slice, but track them)

- **SKU-1 consumption shape (§8):** how does a given customer's MES expect data —
  pull (REST/OPC UA), push (their bus/webhook), DB-style, or an OPC UA server the
  gateway exposes? The customer usually has a strong existing preference; it
  decides whether an integration is a day of config or a connector to write.
- **Data-model mapping:** their MES/ERP has its own notion of asset/batch/unit.
  Mapping "tag on line 3" to "work-center WC-12" is where integration projects
  balloon. Hand-map for one customer; if it recurs, that mapping layer becomes a
  product surface of its own.
- **Naming standard across the 50 plants:** inconsistent tag naming is the quiet
  thing that wrecks fleet rollups. Mandate a convention early — one company means
  we get to be opinionated; use it.
- **Future vendor-hosted multi-tenant:** if we ever host separate customers'
  sites, hostile isolation returns and reshapes the center. Out of scope now,
  but the site-first identity model does not preclude it.
- **Broker topology:** single broker vs clustered/bridged as site count grows;
  where TLS terminates; whether each SKU-1 customer gets an isolated broker.

---

## 13. Phased delivery

Deliberately incremental — the same discipline used all session. Design the
identity and template models up front (they are assumptions everything sits on);
build the fleet machinery only when a *second* site forces it, to avoid
over-engineering for 50 while we have 1.

**Phase 0 — this document.** Lock the identity model (§7.1), the core principle
(§4), and the transport (§6). No code until these are agreed.

**Phase 1 — thin slice, one real tag.** One gateway, publishing **live only** (no
buffer yet), one real Siemens tag (e.g. `sim_level`), to a broker, with the
historian subscribing and landing it. Proves the pipe end-to-end on real data and
forces the identity model onto real hardware. Smallest thing that proves the
architecture.

**Phase 2 — durability.** Add the buffer-for-delivery and the two-path split
(§7.3–7.4). Prove it survives a real network partition: pull the link, confirm
live pauses and history drains in order on reconnect, with no edge-side read path.

**Phase 3 — second site + management plane.** Stand up a second gateway. Build
desired-vs-actual config reconciliation (§8) and templates-with-overrides — now
concrete because there are two sites to differ. Device auth (§10).

**Phase 4 — SKU 1 hardening.** Edge-side health/observability (§11), the
three management-plane modes (§8.1), point-outbound-at-customer config. Package
the standalone SKU.

**Phase 5 — scale-out.** Historian partitioning by site, reconnect-flood
back-pressure, fleet onboarding flow, cross-site rollups in the API/dashboards.

---

## 14. Risks

- **Building the pipe before the identity model.** The visible, demoable thing is
  the data flow; the thing that decides scale is identity + reconciliation
  underneath it. Get identity wrong and everything inherits it. *Mitigation:*
  Phase 0 locks identity before any code.
- **Scope creep into a rewrite.** "Since we're splitting the edge, let's also
  redo the twin, unify calc namespaces, fix DSN duplication…" — and nothing
  ships. *Mitigation:* the edge split is valuable alone; keep each phase narrow.
- **Schema/payload drift across versions.** An old gateway sending shapes a new
  historian rejects — already bitten twice this session (the `quality` int/str
  and `node_id` optional bugs). *Mitigation:* version the Sparkplug payload
  contract explicitly; the historian tolerates known-older shapes.
- **SKU-1 boundary erosion.** Requests to "keep a little history" at the edge.
  *Mitigation:* the core principle (§4) is the standing answer.
- **Reconnect flood.** Many sites recovering at once. *Mitigation:* rate-limited
  drain (§7.4) and ingest back-pressure (§9) designed in from Phase 2.

---

## 15. A note on verification

Every change this session was verified against the live server — a change, then
proof on real data. This work is different: it is greenfield across service and
network boundaries (a broker, an edge node, a real WAN partition) that cannot be
stood up or tested from a chat tool. The role shifts from "make a change and
prove it on your box" to "design the pieces and hand you something you stand up
and test." Early deliverables are therefore **a spec plus a runnable skeleton you
verify**, not finished features proven remotely. Worth stating plainly so the
tight verify-loop's absence is expected, not a surprise.

---

## 16. Decisions needed to proceed to Phase 1

1. **Identity model (§7.1)** — confirm `site / gateway / device / tag`, and the
   registry approach that keeps existing `tag_id` UUIDs working.
2. **Core principle (§4)** — confirm "buffer for delivery, never for retrieval"
   as the inviolable boundary.
3. **Transport (§6)** — confirmed: MQTT / Sparkplug-B inward, HTTP outward.
4. **Broker choice** — which MQTT broker (e.g. EMQX, HiveMQ, Mosquitto) for the
   first slice. Affects nothing architectural; needed to write Phase 1.

Once 1–2 are confirmed and 4 is chosen, Phase 1 (the thin slice) is well-defined
and buildable.
