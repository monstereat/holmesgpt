BEGIN;

-- A durable saga record lets the API resume a previously dispatched action or
-- rollback after process/database interruption. The approval UUID is also the
-- stable idempotency key sent to the owning service.
CREATE TABLE IF NOT EXISTS action_executions (
    approval_id UUID PRIMARY KEY REFERENCES approvals(id),
    incident_id UUID NOT NULL REFERENCES incidents(id),
    status TEXT NOT NULL DEFAULT 'prepared'
        CHECK (status IN ('prepared', 'dispatching', 'rollback_pending', 'succeeded', 'rolled_back', 'failed')),
    before_state JSONB,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((status = 'prepared' AND before_state IS NULL) OR (status <> 'prepared' AND before_state IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS action_executions_recovery_idx
    ON action_executions (updated_at, approval_id)
    WHERE status IN ('dispatching', 'rollback_pending');

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_runtime') THEN
        GRANT SELECT, INSERT, UPDATE ON TABLE action_executions TO aiops_runtime;
    END IF;
END
$$;

INSERT INTO schema_migrations (version) VALUES ('0017_action_execution_recovery')
ON CONFLICT (version) DO NOTHING;

COMMIT;
