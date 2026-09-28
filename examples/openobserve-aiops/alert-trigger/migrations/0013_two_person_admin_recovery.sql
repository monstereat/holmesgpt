DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_break_glass' AND rolcanlogin) THEN
        RAISE EXCEPTION 'run the database administrator role bootstrap first to disable the shared aiops_break_glass login';
    END IF;
END
$$;

CREATE TABLE public.break_glass_recovery_requests (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_user_id UUID NOT NULL REFERENCES public.users(id),
    operator_identity TEXT NOT NULL,
    change_reference TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'expired')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    approver_identity TEXT,
    completed_at TIMESTAMPTZ,
    audit_event_id BIGINT REFERENCES public.audit_events(id),
    CHECK ((status = 'approved') = (approver_identity IS NOT NULL)),
    CHECK ((status = 'approved') = (completed_at IS NOT NULL))
);

CREATE UNIQUE INDEX break_glass_one_pending_request
    ON public.break_glass_recovery_requests ((status))
    WHERE status = 'pending';

REVOKE ALL ON TABLE public.break_glass_recovery_requests FROM PUBLIC, aiops_runtime, aiops_worker, aiops_break_glass;
REVOKE ALL ON SEQUENCE public.break_glass_recovery_requests_id_seq FROM PUBLIC, aiops_runtime, aiops_worker, aiops_break_glass;

CREATE OR REPLACE FUNCTION public.request_break_glass_admin_recovery(
    p_target_user_id UUID,
    p_change_reference TEXT
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    active_admin_count INTEGER;
    target_role TEXT;
    target_active BOOLEAN;
    request_id BIGINT;
BEGIN
    IF NOT pg_has_role(session_user, 'aiops_break_glass', 'member') THEN
        RAISE EXCEPTION 'authenticated database identity is not an approved break-glass custodian';
    END IF;
    IF p_change_reference IS NULL OR p_change_reference !~ '^[A-Za-z0-9._:/-]{3,100}$' THEN
        RAISE EXCEPTION 'break-glass recovery requires a valid change reference';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('holmesgpt:break-glass-admin-recovery'));
    LOCK TABLE public.users IN EXCLUSIVE MODE;
    SELECT count(*) INTO active_admin_count
    FROM public.users
    WHERE role = 'admin' AND active = TRUE;
    IF active_admin_count <> 0 THEN
        RAISE EXCEPTION 'break-glass recovery is allowed only when no active administrator exists';
    END IF;

    SELECT role, active INTO target_role, target_active
    FROM public.users
    WHERE id = p_target_user_id
    FOR UPDATE;
    IF target_role IS DISTINCT FROM 'admin' OR target_active IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'target must be an existing disabled administrator';
    END IF;

    UPDATE public.break_glass_recovery_requests
    SET status = 'expired'
    WHERE status = 'pending' AND requested_at < clock_timestamp() - INTERVAL '15 minutes';

    INSERT INTO public.break_glass_recovery_requests (
        target_user_id, operator_identity, change_reference
    )
    VALUES (p_target_user_id, session_user::text, p_change_reference)
    RETURNING id INTO request_id;

    INSERT INTO public.audit_events (actor_id, event_type, details)
    VALUES (
        NULL,
        'user.break_glass_recovery_requested',
        jsonb_build_object(
            'request_id', request_id,
            'target_user_id', p_target_user_id,
            'operator_database_identity', session_user::text,
            'change_reference', p_change_reference
        )
    );

    RETURN request_id;
END
$$;

CREATE OR REPLACE FUNCTION public.approve_break_glass_admin_recovery(
    request_id BIGINT,
    idp_admin_membership_verified BOOLEAN
)
RETURNS TABLE (recovered_user_id UUID, audit_event_id BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    recovery_request public.break_glass_recovery_requests%ROWTYPE;
    active_admin_count INTEGER;
    new_audit_event_id BIGINT;
BEGIN
    IF NOT pg_has_role(session_user, 'aiops_break_glass', 'member') THEN
        RAISE EXCEPTION 'authenticated database identity is not an approved break-glass custodian';
    END IF;
    IF idp_admin_membership_verified IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'target IdP administrator membership must be verified';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('holmesgpt:break-glass-admin-recovery'));
    LOCK TABLE public.users IN EXCLUSIVE MODE;

    SELECT * INTO recovery_request
    FROM public.break_glass_recovery_requests AS requests
    WHERE requests.id = request_id
    FOR UPDATE;
    IF recovery_request.id IS NULL OR recovery_request.status <> 'pending' THEN
        RAISE EXCEPTION 'break-glass recovery request is missing or no longer pending';
    END IF;
    IF recovery_request.requested_at < clock_timestamp() - INTERVAL '15 minutes' THEN
        RAISE EXCEPTION 'break-glass recovery request expired; create a new request';
    END IF;
    IF recovery_request.operator_identity = session_user::text THEN
        RAISE EXCEPTION 'requester cannot approve their own break-glass recovery';
    END IF;

    SELECT count(*) INTO active_admin_count
    FROM public.users
    WHERE role = 'admin' AND active = TRUE;
    IF active_admin_count <> 0 THEN
        RAISE EXCEPTION 'break-glass recovery is allowed only when no active administrator exists';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.users
        WHERE id = recovery_request.target_user_id AND role = 'admin' AND active = FALSE
    ) THEN
        RAISE EXCEPTION 'target must remain an existing disabled administrator';
    END IF;

    UPDATE public.users
    SET active = TRUE,
        reactivation_requested_at = NULL,
        session_generation = session_generation + 1
    WHERE id = recovery_request.target_user_id;

    INSERT INTO public.audit_events (actor_id, event_type, details)
    VALUES (
        NULL,
        'user.break_glass_reactivated',
        jsonb_build_object(
            'request_id', recovery_request.id,
            'target_user_id', recovery_request.target_user_id,
            'operator_database_identity', recovery_request.operator_identity,
            'approver_database_identity', session_user::text,
            'change_reference', recovery_request.change_reference,
            'active_admin_count_before', active_admin_count,
            'identity_group_verified_out_of_band', idp_admin_membership_verified
        )
    )
    RETURNING id INTO new_audit_event_id;

    UPDATE public.break_glass_recovery_requests
    SET status = 'approved',
        approver_identity = session_user::text,
        completed_at = clock_timestamp(),
        audit_event_id = new_audit_event_id
    WHERE id = recovery_request.id;

    recovered_user_id := recovery_request.target_user_id;
    audit_event_id := new_audit_event_id;
    RETURN NEXT;
END
$$;

REVOKE ALL ON FUNCTION public.break_glass_reactivate_admin(UUID, TEXT, TEXT, TEXT, BOOLEAN)
    FROM PUBLIC, aiops_break_glass;
REVOKE ALL ON FUNCTION public.request_break_glass_admin_recovery(UUID, TEXT)
    FROM PUBLIC, aiops_runtime, aiops_worker;
REVOKE ALL ON FUNCTION public.approve_break_glass_admin_recovery(BIGINT, BOOLEAN)
    FROM PUBLIC, aiops_runtime, aiops_worker;
GRANT EXECUTE ON FUNCTION public.request_break_glass_admin_recovery(UUID, TEXT) TO aiops_break_glass;
GRANT EXECUTE ON FUNCTION public.approve_break_glass_admin_recovery(BIGINT, BOOLEAN) TO aiops_break_glass;
