-- 032_site_config.sql — WEP-001 §8 (management plane, Phase 3)
--
-- The center is the source of truth for each site's desired config. Plants are
-- bespoke, so each site holds a whole explicit config document (JSONB) — no
-- template/inheritance to resolve. Config is versioned: every change is a new
-- row, so we can see what a site *should* run, roll back, and compare desired
-- (here) against actual (what the gateway reports it's running).

CREATE TABLE IF NOT EXISTS site_config (
    site        TEXT        NOT NULL,
    gateway     TEXT        NOT NULL,
    version     INTEGER     NOT NULL,
    config      JSONB       NOT NULL,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (site, gateway, version)
);

-- Fast "what's the latest version for this gateway?" lookup.
CREATE INDEX IF NOT EXISTS site_config_latest_idx
    ON site_config (site, gateway, version DESC);

-- Actual state the gateway reports back: which config version it is currently
-- running, and when it last checked in. Desired = MAX(version) in site_config;
-- actual = this. The gap between them is the fleet's reconciliation status.
CREATE TABLE IF NOT EXISTS gateway_status (
    site            TEXT        NOT NULL,
    gateway         TEXT        NOT NULL,
    running_version INTEGER,
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (site, gateway)
);

COMMENT ON TABLE site_config IS
    'Desired per-site config, versioned. Center is source of truth; gateways '
    'pull their own via the config API. Bespoke per site — no templates.';
COMMENT ON TABLE gateway_status IS
    'Actual state each gateway reports: running config version + last check-in. '
    'Compared against MAX(site_config.version) for desired-vs-actual.';
