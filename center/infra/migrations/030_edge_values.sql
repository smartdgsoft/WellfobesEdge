-- 030_edge_values.sql — WEP-001 §7.1, §9
--
-- The site-first historian table. Separate from the existing `tag_values` so
-- Phase 1 proves the fleet identity model without disturbing anything running.
--
-- The natural key is the four-part path (site, gateway, device, tag). `site` is
-- a real column and a partition dimension, so "level in PLANT12" and "level
-- across the fleet" are both natural queries — the whole point of site-first.

CREATE TABLE IF NOT EXISTS edge_values (
    site     TEXT        NOT NULL,
    gateway  TEXT        NOT NULL,
    device   TEXT        NOT NULL,
    tag      TEXT        NOT NULL,
    value    DOUBLE PRECISION,
    quality  INTEGER,
    ts       TIMESTAMPTZ NOT NULL
);

-- Hypertable on time; also partition (space) by site so a 50-plant fleet
-- doesn't pile every site into the same chunks.
SELECT create_hypertable(
    'edge_values', 'ts',
    partitioning_column => 'site',
    number_partitions   => 16,
    if_not_exists       => TRUE,
    chunk_time_interval => INTERVAL '1 day'
);

-- "everything for tag X at site Y over time" — the common query shape.
CREATE INDEX IF NOT EXISTS edge_values_site_tag_ts_idx
    ON edge_values (site, device, tag, ts DESC);

-- "everything at site Y" and cross-site rollups.
CREATE INDEX IF NOT EXISTS edge_values_site_ts_idx
    ON edge_values (site, ts DESC);

COMMENT ON TABLE edge_values IS
    'Site-first historian data landed from edge gateways via Sparkplug-B. '
    'Natural key: (site, gateway, device, tag). Retention lives here, never at the edge.';
