DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_runtime') THEN
        REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM aiops_runtime;

        IF to_regclass('public.oidc_login_transactions') IS NOT NULL THEN
            GRANT DELETE ON TABLE oidc_login_transactions TO aiops_runtime;
        END IF;
        IF to_regclass('public.revoked_sessions') IS NOT NULL THEN
            GRANT DELETE ON TABLE revoked_sessions TO aiops_runtime;
        END IF;

        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE aiops_migrator IN SCHEMA public REVOKE DELETE ON TABLES FROM aiops_runtime';
    END IF;
END
$$;
