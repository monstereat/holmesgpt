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
    -c "INSERT INTO schema_migrations (version) VALUES ('0003_incident_retrospectives'), ('0006_audit_events_append_only'), ('0009_user_reactivation') ON CONFLICT DO NOTHING" >/dev/null

migration_count=$(docker exec "$container_name" psql -At -U aiops_migrator -d postgres \
    -c "SELECT count(*) FROM schema_migrations")
if [[ "$migration_count" != "9" ]]; then
    echo "migration runner applied $migration_count records instead of 9" >&2
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
       AND has_column_privilege(current_user, 'incidents', 'severity', 'UPDATE')
       AND has_column_privilege(current_user, 'incidents', 'assignee_user_id', 'UPDATE')
       AND has_column_privilege(current_user, 'users', 'session_generation', 'UPDATE')
       AND has_column_privilege(current_user, 'users', 'reactivation_requested_at', 'UPDATE')
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

for statement in \
    "UPDATE audit_events SET event_type = 'tampered'" \
    "DELETE FROM audit_events" \
    "TRUNCATE audit_events" \
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

echo "PostgreSQL role verification passed: 9 migrations including incident triage and user reactivation; runtime business access and audit append allowed; audit/ledger mutation and schema creation denied"
