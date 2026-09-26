#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
container_name="holmesgpt-aiops-pg-backup-check-$$"
backup_dir="$(mktemp -d "${TMPDIR:-/tmp}/holmesgpt-pg-backup-check.XXXXXX")"
postgres_image="postgres:16.6-alpine"

cleanup() {
    docker stop "$container_name" >/dev/null 2>&1 || true
    rm -rf -- "$backup_dir"
}
trap cleanup EXIT

docker run --detach --rm --network none --name "$container_name" \
    --mount "type=bind,src=$backup_dir,dst=/backups" \
    --mount "type=bind,src=$repo_dir/backup-postgres.sh,dst=/usr/local/bin/backup-postgres.sh,readonly" \
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

for migration in "$repo_dir"/alert-trigger/migrations/*.sql; do
    docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
        < "$migration"
    version="$(basename "$migration" .sql)"
    docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
        -c "INSERT INTO schema_migrations (version) VALUES ('$version') ON CONFLICT DO NOTHING" \
        >/dev/null
done

docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
INSERT INTO incidents (id, fingerprint, alert_name, summary)
VALUES (
    '00000000-0000-4000-8000-000000000001',
    repeat('a', 64),
    'backup-restore-check',
    '{"source":"synthetic"}'
);
SQL

backup_path="/backups/aiops-backup-check.dump"
docker exec "$container_name" env \
    PGHOST=127.0.0.1 PGUSER=postgres PGDATABASE=postgres \
    /usr/local/bin/backup-postgres.sh "$backup_path"

backup_mode="$(docker exec "$container_name" stat -c '%a' "$backup_path")"
if [[ "$backup_mode" != "600" ]]; then
    echo "backup file permissions were $backup_mode instead of 600" >&2
    exit 1
fi

if docker exec "$container_name" env \
    PGHOST=127.0.0.1 PGUSER=postgres PGDATABASE=postgres \
    /usr/local/bin/backup-postgres.sh "$backup_path" >/dev/null 2>&1; then
    echo "backup command unexpectedly overwrote an existing file" >&2
    exit 1
fi

docker exec "$container_name" createdb -U postgres aiops_restore_test
docker exec "$container_name" pg_restore --exit-on-error --single-transaction \
    --no-owner -U postgres -d aiops_restore_test "$backup_path"

restored_incident="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c "SELECT alert_name || ':' || fingerprint FROM incidents WHERE id = '00000000-0000-4000-8000-000000000001'")"
if [[ "$restored_incident" != "backup-restore-check:$(printf 'a%.0s' {1..64})" ]]; then
    echo "restored database did not contain the expected synthetic incident" >&2
    exit 1
fi

restored_migrations="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c 'SELECT count(*) FROM schema_migrations')"
if [[ "$restored_migrations" != "7" ]]; then
    echo "restored database contained $restored_migrations migration records instead of 7" >&2
    exit 1
fi

restored_index="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c "SELECT to_regclass('public.tasks_terminal_duration_completed_idx') IS NOT NULL")"
if [[ "$restored_index" != "t" ]]; then
    echo "restored database did not contain the task duration index" >&2
    exit 1
fi

echo "PostgreSQL backup verification passed: 7 migrations and a synthetic incident restored, overwrite protection held"
