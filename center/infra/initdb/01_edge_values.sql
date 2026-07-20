-- 030_edge_values.sql — WEP-001 §7.1, §9
--
-- Site-first historian table. Separate from any existing tag store so it proves
-- the fleet identity model in isolation. Natural key: (site, gateway, device, tag).
--
-- The create_hypertable signature has shifted across TimescaleDB versions
-- (partitioning_column/number_partitions vs the older add_dimension flow), so we
-- create the plain table first (always works), then attempt the hypertable +
-- space partition defensively — if the extension or a specific arg isn't
-- available, the table still functions as a normal table and ingestion is
-- unaffected. A DO block keeps a signature mismatch from aborting startup.

CREATE TABLE IF NOT EXISTS edge_values (
    site     TEXT        NOT NULL,
    gateway  TEXT        NOT NULL,
    device   TEXT        NOT NULL,
    tag      TEXT        NOT NULL,
    value    DOUBLE PRECISION,
    quality  INTEGER,
    ts       TIMESTAMPTZ NOT NULL
);

-- Make it a hypertable on time. Wrapped so a version/arg mismatch is a NOTICE,
-- not a fatal error — the table still works either way.
DO $$
BEGIN
    PERFORM create_hypertable('edge_values', 'ts',
                              chunk_time_interval => INTERVAL '1 day',
                              if_not_exists => TRUE);
    RAISE NOTICE 'edge_values is now a hypertable';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'create_hypertable skipped (%): table works as a plain table', SQLERRM;
END $$;

-- Add site as a space partition dimension. Also defensive: newer TimescaleDB
-- uses by_hash(); older uses add_dimension with number_partitions. Try hash,
-- fall back, and if neither is available just carry on (site is still a column).
DO $$
BEGIN
    BEGIN
        PERFORM add_dimension('edge_values', by_hash('site', 16), if_not_exists => TRUE);
        RAISE NOTICE 'site hash partition added (by_hash)';
    EXCEPTION WHEN undefined_function THEN
        PERFORM add_dimension('edge_values', 'site', number_partitions => 16, if_not_exists => TRUE);
        RAISE NOTICE 'site partition added (legacy add_dimension)';
    END;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'site partitioning skipped (%): still queryable by site column', SQLERRM;
END $$;

CREATE INDEX IF NOT EXISTS edge_values_site_tag_ts_idx
    ON edge_values (site, device, tag, ts DESC);
CREATE INDEX IF NOT EXISTS edge_values_site_ts_idx
    ON edge_values (site, ts DESC);

COMMENT ON TABLE edge_values IS
    'Site-first historian data from edge gateways via Sparkplug-B. '
    'Natural key: (site, gateway, device, tag). Retention lives here, never at the edge.';
