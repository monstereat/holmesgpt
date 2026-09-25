BEGIN;

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE outbox_events ADD COLUMN IF NOT EXISTS last_enqueued_at TIMESTAMPTZ;
ALTER TABLE outbox_events ADD COLUMN IF NOT EXISTS delivery_attempts INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS outbox_reconcile_idx
    ON outbox_events (last_enqueued_at, available_at, created_at)
    WHERE event_type = 'investigation.requested';

INSERT INTO schema_migrations (version) VALUES ('0002_task_leases')
ON CONFLICT (version) DO NOTHING;

COMMIT;
