DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_runtime') THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_events FROM aiops_runtime;
    END IF;
END
$$;

REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_events FROM PUBLIC;
