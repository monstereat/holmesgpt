DO $$
DECLARE
    audit_sequence TEXT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_worker') THEN
        EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM aiops_worker';
        EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM aiops_worker';

        EXECUTE 'GRANT USAGE ON SCHEMA public TO aiops_worker';
        EXECUTE 'GRANT SELECT ON TABLE incidents TO aiops_worker';
        EXECUTE 'GRANT SELECT ON TABLE tasks TO aiops_worker';
        EXECUTE 'GRANT UPDATE (status, attempt, started_at, lease_expires_at, result, completed_at, error_code, available_at, updated_at) ON TABLE tasks TO aiops_worker';
        EXECUTE 'GRANT INSERT (incident_id, task_id, event_type, details) ON TABLE audit_events TO aiops_worker';
        EXECUTE 'GRANT SELECT ON TABLE outbox_events TO aiops_worker';
        EXECUTE 'GRANT UPDATE (last_enqueued_at, delivery_attempts, published_at) ON TABLE outbox_events TO aiops_worker';

        audit_sequence := pg_get_serial_sequence('public.audit_events', 'id');
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO aiops_worker', audit_sequence);
    END IF;
END
$$;
