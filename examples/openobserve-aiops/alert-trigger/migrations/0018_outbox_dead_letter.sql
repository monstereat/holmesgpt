BEGIN;

ALTER TABLE outbox_events ADD COLUMN IF NOT EXISTS dead_lettered_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS outbox_dead_lettered_idx
    ON outbox_events (dead_lettered_at)
    WHERE dead_lettered_at IS NOT NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_worker') THEN
        GRANT UPDATE (dead_lettered_at) ON TABLE outbox_events TO aiops_worker;
    END IF;
END
$$;

INSERT INTO schema_migrations (version) VALUES ('0018_outbox_dead_letter')
ON CONFLICT (version) DO NOTHING;

COMMIT;
