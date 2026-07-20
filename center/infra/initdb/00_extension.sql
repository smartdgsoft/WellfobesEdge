-- Ensure TimescaleDB is available before the migration calls create_hypertable.
CREATE EXTENSION IF NOT EXISTS timescaledb;
