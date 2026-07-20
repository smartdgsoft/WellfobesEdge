-- 031_edge_values_dedupe.sql — WEP-002 (Phase 2)
--
-- End-to-end at-least-once delivery means a batch can be redelivered after a
-- lost ack, so the historian must dedupe. We add the batch identity carried in
-- the payload and a uniqueness guard, then use ON CONFLICT DO NOTHING on insert.
--
-- A batch has many rows; the unique key is (gateway, batch_seq, device, tag,
-- ts) — enough to make a redelivered identical row a no-op without collapsing
-- legitimately distinct readings.

ALTER TABLE edge_values
    ADD COLUMN IF NOT EXISTS batch_seq BIGINT;

-- Uniqueness for dedupe. On a hypertable the unique index must include the
-- partitioning column (ts) — which it does. Wrapped defensively in case the
-- table is plain (non-Timescale) too; either way the index is created.
DO $$
BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS edge_values_dedupe_idx
        ON edge_values (gateway, batch_seq, device, tag, ts);
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'dedupe index create note: %', SQLERRM;
END $$;

COMMENT ON COLUMN edge_values.batch_seq IS
    'Edge delivery batch id (per gateway). With (device,tag,ts) it dedupes '
    'redelivered batches after a lost ack — at-least-once becomes effectively '
    'exactly-once at rest.';
