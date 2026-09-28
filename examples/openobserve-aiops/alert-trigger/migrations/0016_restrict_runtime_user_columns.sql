BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_runtime') THEN
        REVOKE INSERT, UPDATE ON TABLE public.users FROM aiops_runtime, PUBLIC;
        REVOKE INSERT (id, username, password_hash, role, resource_scopes, active, created_at, oidc_issuer, oidc_subject, session_generation, reactivation_requested_at)
            ON TABLE public.users FROM aiops_runtime, PUBLIC;
        REVOKE UPDATE (id, username, password_hash, role, resource_scopes, active, created_at, oidc_issuer, oidc_subject, session_generation, reactivation_requested_at)
            ON TABLE public.users FROM aiops_runtime, PUBLIC;

        GRANT INSERT (id, username, password_hash, role, resource_scopes, active, oidc_issuer, oidc_subject)
            ON TABLE public.users TO aiops_runtime;
        GRANT UPDATE (username, password_hash, role, resource_scopes, active, session_generation, reactivation_requested_at)
            ON TABLE public.users TO aiops_runtime;
    END IF;
END
$$;

INSERT INTO schema_migrations (version) VALUES ('0016_restrict_runtime_user_columns')
ON CONFLICT (version) DO NOTHING;

COMMIT;
