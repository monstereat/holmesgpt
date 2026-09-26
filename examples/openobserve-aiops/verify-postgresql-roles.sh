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
    -c "INSERT INTO schema_migrations (version) VALUES ('0006_audit_events_append_only') ON CONFLICT DO NOTHING" >/dev/null

# Re-running the bootstrap must not restore audit mutation privileges.
docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
    < "$repo_dir/postgresql-roles.psql"

privileges=$(docker exec "$container_name" psql -At -U aiops_runtime -d postgres -c "
    SELECT has_table_privilege(current_user, 'audit_events', 'SELECT')
       AND has_table_privilege(current_user, 'audit_events', 'INSERT')
       AND NOT has_table_privilege(current_user, 'audit_events', 'UPDATE')
       AND NOT has_table_privilege(current_user, 'audit_events', 'DELETE')
       AND NOT has_table_privilege(current_user, 'audit_events', 'TRUNCATE')
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
    "CREATE TABLE runtime_must_not_create (id integer)"; do
    if docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_runtime -d postgres \
        -c "$statement" >/dev/null 2>&1; then
        echo "runtime role unexpectedly succeeded: $statement" >&2
        exit 1
    fi
done

echo "PostgreSQL role verification passed: audit append/read allowed; audit mutation and schema creation denied"
