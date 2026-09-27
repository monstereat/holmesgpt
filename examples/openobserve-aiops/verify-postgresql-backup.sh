#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
container_name="holmesgpt-aiops-pg-backup-check-$$"
backup_dir="$(mktemp -d "${TMPDIR:-/tmp}/holmesgpt-pg-backup-check.XXXXXX")"
postgres_image="postgres:16.6-alpine"
stage="initialize verifier"

cleanup() {
    local result=$?
    if [[ "$result" -ne 0 ]]; then
        echo "PostgreSQL backup verification failed during: $stage" >&2
        echo "::error title=PostgreSQL backup verification failed::$stage (exit $result)"
        if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
            printf 'PostgreSQL backup verification failed during: `%s` (exit %s).\n' \
                "$stage" "$result" >> "$GITHUB_STEP_SUMMARY"
        fi
        docker logs "$container_name" >&2 || true
    fi
    docker stop "$container_name" >/dev/null 2>&1 || true
    rm -rf -- "$backup_dir"
    return "$result"
}
trap cleanup EXIT

stage="create temporary recipient certificate"
openssl req -x509 -newkey rsa:3072 -nodes -keyout "$backup_dir/recipient.key" \
    -out "$backup_dir/recipient.crt" -days 1 -subj '/CN=postgres-backup-check' \
    >/dev/null 2>&1
chmod 600 "$backup_dir/recipient.key"

stage="start isolated PostgreSQL"
docker run --detach --rm --network none --name "$container_name" \
    --tmpfs /var/lib/postgresql/data:rw,size=256m \
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

stage="apply repository migrations"
for migration in "$repo_dir"/alert-trigger/migrations/*.sql; do
    docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
        < "$migration"
    version="$(basename "$migration" .sql)"
    docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
        -c "INSERT INTO schema_migrations (version) VALUES ('$version') ON CONFLICT DO NOTHING" \
        >/dev/null
done

stage="insert synthetic incident and create plaintext backup"
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

stage="encrypt and decrypt the backup"
encrypted_backup_path="$backup_dir/aiops-backup-check.cms.der"
bash "$repo_dir/encrypt-postgres-backup.sh" "$backup_dir/aiops-backup-check.dump" \
    "$encrypted_backup_path" "$backup_dir/recipient.crt"
openssl cms -decrypt -binary -inform DER -in "$encrypted_backup_path" \
    -recip "$backup_dir/recipient.crt" -inkey "$backup_dir/recipient.key" \
    -out "$backup_dir/aiops-encrypted-restore-check.dump"

stage="verify encrypted backup permissions and overwrite protection"
encrypted_mode="$(docker exec "$container_name" stat -c '%a' \
    /backups/aiops-backup-check.cms.der)"
if [[ "$encrypted_mode" != "600" ]]; then
    echo "encrypted backup file permissions were $encrypted_mode instead of 600" >&2
    exit 1
fi

if bash "$repo_dir/encrypt-postgres-backup.sh" \
    "$backup_dir/aiops-backup-check.dump" "$encrypted_backup_path" \
    "$backup_dir/recipient.crt" >/dev/null 2>&1; then
    echo "encrypted backup command unexpectedly overwrote an existing file" >&2
    exit 1
fi

stage="verify tamper rejection and plaintext archive permissions"
python3 - "$encrypted_backup_path" "$backup_dir/aiops-backup-tampered.cms.der" <<'PY'
import sys

with open(sys.argv[1], "rb") as encrypted_file:
    contents = bytearray(encrypted_file.read())
contents[-1] ^= 1
with open(sys.argv[2], "wb") as tampered_file:
    tampered_file.write(contents)
PY
if openssl cms -decrypt -binary -inform DER \
    -in "$backup_dir/aiops-backup-tampered.cms.der" \
    -recip "$backup_dir/recipient.crt" -inkey "$backup_dir/recipient.key" \
    -out "$backup_dir/aiops-tampered-restore-check.dump" >/dev/null 2>&1; then
    echo "tampered encrypted backup unexpectedly passed authentication" >&2
    exit 1
fi

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

stage="restore plaintext and encrypted archives"
docker exec "$container_name" createdb -U postgres aiops_restore_test
docker exec "$container_name" pg_restore --exit-on-error --single-transaction \
    --no-owner -U postgres -d aiops_restore_test "$backup_path"

docker exec "$container_name" createdb -U postgres aiops_encrypted_restore_test
docker exec "$container_name" pg_restore --exit-on-error --single-transaction \
    --no-owner -U postgres -d aiops_encrypted_restore_test \
    /backups/aiops-encrypted-restore-check.dump

stage="verify restored incident and migrations"
restored_incident="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c "SELECT alert_name || ':' || fingerprint FROM incidents WHERE id = '00000000-0000-4000-8000-000000000001'")"
if [[ "$restored_incident" != "backup-restore-check:$(printf 'a%.0s' {1..64})" ]]; then
    echo "restored database did not contain the expected synthetic incident" >&2
    exit 1
fi

restored_migrations="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c 'SELECT count(*) FROM schema_migrations')"
if [[ "$restored_migrations" != "9" ]]; then
    echo "restored database contained $restored_migrations migration records instead of 9" >&2
    exit 1
fi

restored_identity_columns="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c "SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND column_name IN ('session_generation', 'reactivation_requested_at') ORDER BY column_name")"
if [[ "$restored_identity_columns" != $'reactivation_requested_at\nsession_generation' ]]; then
    echo "restored database did not contain user reactivation columns" >&2
    exit 1
fi

restored_index="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c "SELECT to_regclass('public.tasks_terminal_duration_completed_idx') IS NOT NULL")"
if [[ "$restored_index" != "t" ]]; then
    echo "restored database did not contain the task duration index" >&2
    exit 1
fi

restored_triage="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c "SELECT column_name FROM information_schema.columns WHERE table_name = 'incidents' AND column_name IN ('severity', 'assignee_user_id') ORDER BY column_name")"
if [[ "$restored_triage" != $'assignee_user_id\nseverity' ]]; then
    echo "restored database did not contain incident triage columns" >&2
    exit 1
fi

encrypted_restored_incident="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_encrypted_restore_test -c "SELECT alert_name || ':' || fingerprint FROM incidents WHERE id = '00000000-0000-4000-8000-000000000001'")"
if [[ "$encrypted_restored_incident" != "$restored_incident" ]]; then
    echo "encrypted backup restore did not contain the expected synthetic incident" >&2
    exit 1
fi

encrypted_migrations="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_encrypted_restore_test -c 'SELECT count(*) FROM schema_migrations')"
if [[ "$encrypted_migrations" != "9" ]]; then
    echo "encrypted backup restore contained $encrypted_migrations migration records instead of 9" >&2
    exit 1
fi

encrypted_identity_columns="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_encrypted_restore_test -c "SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND column_name IN ('session_generation', 'reactivation_requested_at') ORDER BY column_name")"
if [[ "$encrypted_identity_columns" != $'reactivation_requested_at\nsession_generation' ]]; then
    echo "encrypted backup restore did not contain user reactivation columns" >&2
    exit 1
fi

echo "PostgreSQL backup verification passed: plaintext and AES-256-GCM encrypted archives both restored 9 migrations, user reactivation columns, and a synthetic incident; overwrite protection held"
