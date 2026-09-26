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

docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
CREATE TABLE backup_probe (id integer PRIMARY KEY, payload text NOT NULL);
INSERT INTO backup_probe (id, payload) VALUES (1, 'restore-check');
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

restored_payload="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c 'SELECT payload FROM backup_probe WHERE id = 1')"
if [[ "$restored_payload" != "restore-check" ]]; then
    echo "restored database did not contain the expected synthetic row" >&2
    exit 1
fi

echo "PostgreSQL backup verification passed: dump validated, existing output protected, isolated restore matched"
