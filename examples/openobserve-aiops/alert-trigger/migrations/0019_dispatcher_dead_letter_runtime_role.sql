BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_worker') THEN
        REVOKE UPDATE (dead_lettered_at) ON TABLE outbox_events FROM aiops_worker;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_runtime') THEN
        GRANT UPDATE (dead_lettered_at) ON TABLE outbox_events TO aiops_runtime;
    END IF;
END
$$;

INSERT INTO schema_migrations (version) VALUES ('0019_dispatcher_dead_letter_runtime_role')
ON CONFLICT (version) DO NOTHING;

COMMIT;
