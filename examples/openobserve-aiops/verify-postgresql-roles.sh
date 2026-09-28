#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
container_name="holmesgpt-aiops-pg-role-check-$$"
postgres_image="postgres:16.6-alpine"

cleanup() {
    docker stop "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --detach --rm --network none --name "$container_name" \
    --tmpfs /var/lib/postgresql/data:rw,size=256m \
    -e POSTGRES_HOST_AUTH_METHOD=trust "$postgres_image" >/dev/null

ready=false
for _ in $(seq 1 30); do
    if docker exec "$container_name" pg_isready -U postgres >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    echo "temporary PostgreSQL did not become ready" >&2
    exit 1
fi

docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
    < "$repo_dir/postgresql-roles.psql"

for migration in "$repo_dir"/alert-trigger/migrations/*.sql; do
    docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_migrator -d postgres \
        < "$migration"
done

docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_migrator -d postgres \
    -c "INSERT INTO schema_migrations (version) VALUES ('0003_incident_retrospectives'), ('0006_audit_events_append_only'), ('0009_user_reactivation'), ('0010_worker_database_privileges'), ('0011_restrict_runtime_delete'), ('0012_break_glass_admin_recovery'), ('0013_two_person_admin_recovery') ON CONFLICT DO NOTHING" >/dev/null

migration_count=$(docker exec "$container_name" psql -At -U aiops_migrator -d postgres \
    -c "SELECT count(*) FROM schema_migrations")
if [[ "$migration_count" != "13" ]]; then
    echo "migration runner applied $migration_count records instead of 13" >&2
    exit 1
fi

break_glass_privileges=$(docker exec "$container_name" psql -At -U postgres -d postgres -c "
    SELECT NOT rolcanlogin FROM pg_roles WHERE rolname = 'aiops_break_glass'
       AND has_function_privilege('aiops_break_glass', 'public.request_break_glass_admin_recovery(uuid,text)', 'EXECUTE')
       AND has_function_privilege('aiops_break_glass', 'public.approve_break_glass_admin_recovery(bigint,boolean)', 'EXECUTE')
       AND NOT has_function_privilege('aiops_break_glass', 'public.break_glass_reactivate_admin(uuid,text,text,text,boolean)', 'EXECUTE')
       AND NOT has_function_privilege('aiops_runtime', 'public.approve_break_glass_admin_recovery(bigint,boolean)', 'EXECUTE')
       AND NOT has_function_privilege('aiops_worker', 'public.approve_break_glass_admin_recovery(bigint,boolean)', 'EXECUTE')
       AND NOT has_table_privilege('aiops_break_glass', 'users', 'SELECT')
       AND NOT has_table_privilege('aiops_break_glass', 'users', 'UPDATE')
       AND NOT has_table_privilege('aiops_break_glass', 'audit_events', 'INSERT')
       AND NOT has_table_privilege('aiops_break_glass', 'break_glass_recovery_requests', 'SELECT')")
if [[ "$break_glass_privileges" != t ]]; then
    echo "break-glass identity does not have the restricted function-only grants" >&2
    exit 1
fi

docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_migrator -d postgres -c "
    INSERT INTO users (id, username, password_hash, role, resource_scopes, active)
    VALUES ('00000000-0000-0000-0000-000000000123', 'disabled-admin', 'test-hash', 'admin', ARRAY['order-service'], FALSE)" >/dev/null

docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres -c \
    "CREATE ROLE bg_operator LOGIN; CREATE ROLE bg_approver LOGIN; GRANT aiops_break_glass TO bg_operator, bg_approver" >/dev/null
request_id=$(docker exec "$container_name" psql -At -v ON_ERROR_STOP=1 -U bg_operator -d postgres -c \
    "SELECT public.request_break_glass_admin_recovery('00000000-0000-0000-0000-000000000123', 'INC-2026-123')")
if docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U bg_operator -d postgres -c \
    "SELECT * FROM public.approve_break_glass_admin_recovery($request_id, TRUE)" >/dev/null 2>&1; then
    echo "break-glass recovery unexpectedly accepted the requester's approval" >&2
    exit 1
fi
approval_result=$(docker exec "$container_name" psql -At -v ON_ERROR_STOP=1 -U bg_approver -d postgres -c \
    "SELECT recovered_user_id::text || ':' || audit_event_id::text FROM public.approve_break_glass_admin_recovery($request_id, TRUE)")
audit_id="${approval_result##*:}"
recovery_state=$(docker exec "$container_name" psql -At -U aiops_migrator -d postgres -c "
    SELECT active::text || ':' || session_generation::text
    FROM users WHERE id = '00000000-0000-0000-0000-000000000123'")
audit_record=$(docker exec "$container_name" psql -At -U aiops_migrator -d postgres -c "
    SELECT event_type || ':' || (details->>'operator_database_identity') || ':' || (details->>'approver_database_identity')
    FROM audit_events WHERE id = $audit_id")
if [[ "$approval_result" != "00000000-0000-0000-0000-000000000123:$audit_id" || "$recovery_state" != "true:1" || "$audit_record" != "user.break_glass_reactivated:bg_operator:bg_approver" ]]; then
    echo "break-glass recovery did not enforce separate authenticated custodians and audit both identities" >&2
    exit 1
fi

if docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_runtime -d postgres -c \
    "SELECT public.request_break_glass_admin_recovery('00000000-0000-0000-0000-000000000123', 'INC-2026-125')" >/dev/null 2>&1; then
    echo "application runtime unexpectedly executed the break-glass request function" >&2
    exit 1
fi
docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_migrator -d postgres -c "
    UPDATE users SET active = FALSE WHERE id = '00000000-0000-0000-0000-000000000123';
    INSERT INTO users (id, username, password_hash, role, resource_scopes, active)
    VALUES ('00000000-0000-0000-0000-000000000124', 'disabled-admin-2', 'test-hash', 'admin', ARRAY['order-service'], FALSE)" >/dev/null
second_request_id=$(docker exec "$container_name" psql -At -v ON_ERROR_STOP=1 -U bg_operator -d postgres -c \
    "SELECT public.request_break_glass_admin_recovery('00000000-0000-0000-0000-000000000124', 'INC-2026-124')")
docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_migrator -d postgres -c "
    INSERT INTO users (id, username, password_hash, role, resource_scopes, active)
    VALUES ('00000000-0000-0000-0000-000000000125', 'active-admin', 'test-hash', 'admin', ARRAY['order-service'], TRUE)" >/dev/null
if docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U bg_approver -d postgres -c \
    "SELECT * FROM public.approve_break_glass_admin_recovery($second_request_id, TRUE)" >/dev/null 2>&1; then
    echo "break-glass recovery unexpectedly reactivated an account while an administrator was active" >&2
    exit 1
fi
still_disabled=$(docker exec "$container_name" psql -At -U aiops_migrator -d postgres -c \
    "SELECT active::text FROM users WHERE id = '00000000-0000-0000-0000-000000000124'")
if [[ "$still_disabled" != false ]]; then
    echo "failed break-glass attempt changed the target account" >&2
    exit 1
fi

triage_columns=$(docker exec "$container_name" psql -At -U aiops_migrator -d postgres -c "
    SELECT count(*) FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'incidents'
      AND column_name IN ('severity', 'assignee_user_id')")
if [[ "$triage_columns" != "2" ]]; then
    echo "incident triage migration did not create both expected columns" >&2
    exit 1
fi

identity_columns=$(docker exec "$container_name" psql -At -U aiops_migrator -d postgres -c "
    SELECT count(*) FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users'
      AND column_name IN ('session_generation', 'reactivation_requested_at')")
if [[ "$identity_columns" != "2" ]]; then
    echo "user reactivation migration did not create both expected columns" >&2
    exit 1
fi

# Re-running the bootstrap must not restore audit mutation privileges.
docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
    < "$repo_dir/postgresql-roles.psql"

privileges=$(docker exec "$container_name" psql -At -U aiops_runtime -d postgres -c "
    SELECT has_table_privilege(current_user, 'audit_events', 'SELECT')
       AND has_table_privilege(current_user, 'audit_events', 'INSERT')
       AND NOT has_table_privilege(current_user, 'audit_events', 'UPDATE')
       AND NOT has_table_privilege(current_user, 'audit_events', 'DELETE')
       AND NOT has_table_privilege(current_user, 'audit_events', 'TRUNCATE')
       AND has_table_privilege(current_user, 'schema_migrations', 'SELECT')
       AND NOT has_table_privilege(current_user, 'schema_migrations', 'INSERT')
       AND NOT has_table_privilege(current_user, 'schema_migrations', 'UPDATE')
       AND NOT has_table_privilege(current_user, 'schema_migrations', 'DELETE')
       AND NOT has_table_privilege(current_user, 'schema_migrations', 'TRUNCATE')
       AND NOT has_table_privilege(current_user, 'incidents', 'DELETE')
       AND NOT has_table_privilege(current_user, 'tasks', 'DELETE')
       AND NOT has_table_privilege(current_user, 'outbox_events', 'DELETE')
       AND NOT has_table_privilege(current_user, 'approvals', 'DELETE')
       AND NOT has_table_privilege(current_user, 'incident_retrospectives', 'DELETE')
       AND NOT has_table_privilege(current_user, 'users', 'DELETE')
       AND has_table_privilege(current_user, 'oidc_login_transactions', 'DELETE')
       AND has_table_privilege(current_user, 'revoked_sessions', 'DELETE')
       AND has_column_privilege(current_user, 'incidents', 'severity', 'UPDATE')
       AND has_column_privilege(current_user, 'incidents', 'assignee_user_id', 'UPDATE')
       AND has_column_privilege(current_user, 'users', 'session_generation', 'UPDATE')
       AND has_column_privilege(current_user, 'users', 'reactivation_requested_at', 'UPDATE')
       AND has_table_privilege(current_user, 'users', 'UPDATE')
       AND NOT has_table_privilege(current_user, 'break_glass_recovery_requests', 'SELECT')
       AND NOT has_sequence_privilege(current_user, 'break_glass_recovery_requests_id_seq', 'USAGE')
       AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
")
if [[ "$privileges" != t ]]; then
    echo "runtime role has unexpected audit or schema privileges" >&2
    exit 1
fi

docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_runtime -d postgres \
    -c "INSERT INTO audit_events (event_type) VALUES ('permission.test')" >/dev/null
docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_runtime -d postgres \
    -c "SELECT count(*) FROM audit_events" >/dev/null
docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_migrator -d postgres \
    -c "INSERT INTO users (id, username, role) VALUES ('00000000-0000-0000-0000-000000000010', 'permission-test', 'viewer')" >/dev/null
docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_runtime -d postgres \
    -c "UPDATE users SET session_generation = session_generation + 1 WHERE username = 'permission-test'" >/dev/null
docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_migrator -d postgres \
    -c "CREATE TABLE public.runtime_delete_probe (id integer PRIMARY KEY); INSERT INTO public.runtime_delete_probe VALUES (1)" >/dev/null
future_table_delete=$(docker exec "$container_name" psql -At -U aiops_runtime -d postgres -c \
    "SELECT has_table_privilege(current_user, 'public.runtime_delete_probe', 'DELETE')")
if [[ "$future_table_delete" != f ]]; then
    echo "runtime role unexpectedly has DELETE on a future migrator-owned table" >&2
    exit 1
fi
if docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_runtime -d postgres \
    -c "DELETE FROM public.runtime_delete_probe WHERE false" >/dev/null 2>&1; then
    echo "runtime role unexpectedly deleted from a future migrator-owned table" >&2
    exit 1
fi

worker_privileges=$(docker exec "$container_name" psql -At -U aiops_worker -d postgres -c "
    SELECT has_table_privilege(current_user, 'incidents', 'SELECT')
       AND has_table_privilege(current_user, 'tasks', 'SELECT')
       AND has_column_privilege(current_user, 'tasks', 'status', 'UPDATE')
       AND has_column_privilege(current_user, 'outbox_events', 'delivery_attempts', 'UPDATE')
       AND has_column_privilege(current_user, 'audit_events', 'event_type', 'INSERT')
       AND has_sequence_privilege(current_user, 'audit_events_id_seq', 'USAGE')
       AND NOT has_table_privilege(current_user, 'users', 'SELECT')
       AND NOT has_table_privilege(current_user, 'users', 'UPDATE')
       AND NOT has_table_privilege(current_user, 'incidents', 'UPDATE')
       AND NOT has_table_privilege(current_user, 'tasks', 'DELETE')
       AND NOT has_table_privilege(current_user, 'audit_events', 'UPDATE')
       AND NOT has_table_privilege(current_user, 'audit_events', 'DELETE')
       AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
")
if [[ "$worker_privileges" != t ]]; then
    echo "worker role has unexpected database privileges" >&2
    exit 1
fi

docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_worker -d postgres \
    -c "INSERT INTO audit_events (event_type) VALUES ('worker.permission.test')" >/dev/null

for statement in \
    "UPDATE audit_events SET event_type = 'tampered'" \
    "DELETE FROM audit_events" \
    "TRUNCATE audit_events" \
    "DELETE FROM incidents WHERE false" \
    "DELETE FROM tasks WHERE false" \
    "DELETE FROM outbox_events WHERE false" \
    "DELETE FROM approvals WHERE false" \
    "DELETE FROM incident_retrospectives WHERE false" \
    "DELETE FROM users WHERE false" \
    "DELETE FROM public.runtime_delete_probe WHERE false" \
    "INSERT INTO schema_migrations (version) VALUES ('runtime.must.not.write')" \
    "UPDATE schema_migrations SET version = version WHERE false" \
    "DELETE FROM schema_migrations WHERE false" \
    "TRUNCATE schema_migrations" \
    "CREATE TABLE runtime_must_not_create (id integer)"; do
    if docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_runtime -d postgres \
        -c "$statement" >/dev/null 2>&1; then
        echo "runtime role unexpectedly succeeded: $statement" >&2
        exit 1
    fi
done

for statement in \
    "SELECT * FROM users" \
    "UPDATE incidents SET status = status WHERE false" \
    "DELETE FROM tasks WHERE false" \
    "UPDATE audit_events SET event_type = event_type WHERE false" \
    "CREATE TABLE worker_must_not_create (id integer)"; do
    if docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_worker -d postgres \
        -c "$statement" >/dev/null 2>&1; then
        echo "worker role unexpectedly succeeded: $statement" >&2
        exit 1
    fi
done

docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_runtime -d postgres \
    -c "DELETE FROM oidc_login_transactions WHERE false; DELETE FROM revoked_sessions WHERE false" >/dev/null

echo "PostgreSQL role verification passed: 13 migrations; break-glass requires two separately authenticated database identities and exposes functions only, API deletes are limited to expired authentication/session records, and worker grants are scoped"
