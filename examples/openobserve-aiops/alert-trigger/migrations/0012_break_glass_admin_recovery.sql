CREATE OR REPLACE FUNCTION public.break_glass_reactivate_admin(
    target_user_id UUID,
    operator_identity TEXT,
    approver_identity TEXT,
    change_reference TEXT,
    idp_admin_membership_verified BOOLEAN
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
    recovered_user_id UUID;
    audit_event_id BIGINT;
BEGIN
    IF operator_identity IS NULL OR btrim(operator_identity) = '' OR length(operator_identity) > 128
       OR operator_identity ~ '[[:cntrl:]]'
       OR approver_identity IS NULL OR btrim(approver_identity) = '' OR length(approver_identity) > 128
       OR approver_identity ~ '[[:cntrl:]]'
       OR btrim(operator_identity) = btrim(approver_identity) THEN
        RAISE EXCEPTION 'break-glass recovery requires two distinct named custodians';
    END IF;
    IF change_reference IS NULL OR change_reference !~ '^[A-Za-z0-9._:/-]{3,100}$' THEN
        RAISE EXCEPTION 'break-glass recovery requires a valid change reference';
    END IF;
    IF idp_admin_membership_verified IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'target IdP administrator membership must be verified';
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
    WHERE id = target_user_id
    FOR UPDATE;
    IF target_role IS DISTINCT FROM 'admin' OR target_active IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'target must be an existing disabled administrator';
    END IF;

    UPDATE public.users
    SET active = TRUE,
        reactivation_requested_at = NULL,
        session_generation = session_generation + 1
    WHERE id = target_user_id
    RETURNING id INTO recovered_user_id;

    INSERT INTO public.audit_events (actor_id, event_type, details)
    VALUES (
        NULL,
        'user.break_glass_reactivated',
        jsonb_build_object(
            'target_user_id', recovered_user_id,
            'operator_id', btrim(operator_identity),
            'approver_id', btrim(approver_identity),
            'change_reference', change_reference,
            'active_admin_count_before', active_admin_count,
            'identity_group_verified_out_of_band', idp_admin_membership_verified
        )
    )
    RETURNING id INTO audit_event_id;

    RETURN audit_event_id;
END
$$;

REVOKE ALL ON FUNCTION public.break_glass_reactivate_admin(UUID, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.break_glass_reactivate_admin(UUID, TEXT, TEXT, TEXT, BOOLEAN) FROM aiops_runtime, aiops_worker;
GRANT EXECUTE ON FUNCTION public.break_glass_reactivate_admin(UUID, TEXT, TEXT, TEXT, BOOLEAN) TO aiops_break_glass;
