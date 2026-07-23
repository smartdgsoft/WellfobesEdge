"""Durable buffer-for-delivery — WEP-001 §7.4.

A SQLite-backed queue that holds history samples until the *center* confirms it
wrote them. Survives an edge restart (on disk). Enforces the core principle: it
is write-once / drain-once and is NEVER read for a query — it is a delivery
queue, not a mini-historian.

Lifecycle of a batch:
  1. append()            -> rows written to disk, given a monotonic batch_seq
  2. published to broker (by the gateway)
  3. center writes to DB, publishes an ack with that batch_seq
  4. ack(batch_seq)      -> rows for that batch deleted from disk

Anything un-acked stays and is redelivered on reconnect/restart -> at-least-once.
Because redelivery can duplicate, the center dedupes on (gateway, batch_seq).

Bounded: when the buffer exceeds max_rows, the OLDEST un-acked batches are
dropped (with a loud log) — an outage longer than the buffer can hold loses the
oldest data, not the newest, and never grows without limit.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import List, Optional, Tuple


class DeliveryBuffer:
    def __init__(self, path: str, max_rows: int = 500_000):
        self.path = path
        self.max_rows = max_rows
        # check_same_thread=False: the paho network thread acks while the async
        # loop appends. A single lock serialises them (SQLite handles the rest).
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")      # durable + concurrent-ish
        # synchronous=FULL, not NORMAL. In WAL mode NORMAL does NOT fsync on
        # commit — SQLite only syncs at checkpoints, so a power cut can roll
        # back recently committed transactions (the DB stays uncorrupted, but
        # the rows are gone). An edge gateway loses power abruptly, which is
        # precisely the case store-and-forward exists to survive, so a batch
        # must be durable the moment append() returns. Cost is one fsync per
        # BATCH, not per row, because append() commits as a single explicit
        # transaction — fewer syncs than the previous autocommit path.
        self._db.execute("PRAGMA synchronous=FULL")
        self._lock = threading.Lock()
        self._init_schema()
        self._seq = self._max_seq()

    def _init_schema(self):
        with self._lock:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    batch_seq INTEGER NOT NULL,
                    device    TEXT    NOT NULL,
                    tag       TEXT    NOT NULL,
                    value     REAL,
                    quality   INTEGER,
                    ts_ms     INTEGER NOT NULL
                )""")
            self._db.execute("CREATE INDEX IF NOT EXISTS outbox_seq ON outbox(batch_seq)")

    def _max_seq(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(batch_seq), 0) FROM outbox").fetchone()
            return int(row[0])

    # ── producer side ────────────────────────────────────────────────────
    def append(self, readings: List[Tuple[str, str, float, int, int]]) -> int:
        """Persist a batch of (device, tag, value, quality, ts_ms). Returns the
        batch_seq assigned. The whole batch commits as ONE transaction and is
        fsync'd (synchronous=FULL) before this returns, so a crash OR power cut
        after append but before publish still redelivers every row. Atomic too:
        a partial batch can never be observed after a crash mid-write."""
        with self._lock:
            self._seq += 1
            seq = self._seq
            # Explicit transaction: the connection is in autocommit
            # (isolation_level=None), so without this each row would be its own
            # transaction — 200 fsyncs per batch instead of 1.
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.executemany(
                    "INSERT INTO outbox (batch_seq, device, tag, value, quality, ts_ms) "
                    "VALUES (?,?,?,?,?,?)",
                    [(seq, d, t, v, q, ts) for (d, t, v, q, ts) in readings])
                self._enforce_bound_locked()
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                self._seq -= 1          # seq was never durably used
                raise
            return seq

    def _enforce_bound_locked(self):
        n = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        if n <= self.max_rows:
            return
        # drop oldest batches until under the bound
        over = n - self.max_rows
        victims = self._db.execute(
            "SELECT DISTINCT batch_seq FROM outbox ORDER BY batch_seq ASC").fetchall()
        removed = 0
        for (seq,) in victims:
            if removed >= over:
                break
            cnt = self._db.execute("SELECT COUNT(*) FROM outbox WHERE batch_seq=?", (seq,)).fetchone()[0]
            self._db.execute("DELETE FROM outbox WHERE batch_seq=?", (seq,))
            removed += cnt
            print(f"[buffer] OVERFLOW: dropped oldest batch {seq} ({cnt} rows) — "
                  f"outage exceeded buffer capacity", flush=True)

    # ── delivery side ────────────────────────────────────────────────────
    def pending_batches(self) -> List[int]:
        """Un-acked batch_seqs, oldest first (drain order)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT batch_seq FROM outbox ORDER BY batch_seq ASC").fetchall()
            return [int(r[0]) for r in rows]

    def batch_rows(self, seq: int) -> List[Tuple[str, str, float, int, int]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT device, tag, value, quality, ts_ms FROM outbox "
                "WHERE batch_seq=? ORDER BY rowid", (seq,)).fetchall()
            return [(d, t, v, q, ts) for (d, t, v, q, ts) in rows]

    def ack(self, seq: int) -> int:
        """Center confirmed this batch is durably stored -> release it. Returns
        rows freed. Idempotent: acking an unknown/already-freed seq is a no-op."""
        with self._lock:
            cur = self._db.execute("DELETE FROM outbox WHERE batch_seq=?", (seq,))
            return cur.rowcount

    def depth(self) -> Tuple[int, int]:
        """(pending_batches, pending_rows) — for health/observability."""
        with self._lock:
            b = self._db.execute("SELECT COUNT(DISTINCT batch_seq) FROM outbox").fetchone()[0]
            r = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            return int(b), int(r)

    def close(self):
        with self._lock:
            self._db.close()
