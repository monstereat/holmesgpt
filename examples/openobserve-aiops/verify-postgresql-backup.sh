#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
expected_migrations=$(find "$repo_dir/alert-trigger/migrations" -maxdepth 1 -type f -name '*.sql' | wc -l | tr -d ' ')
expected_migration_versions=$(find "$repo_dir/alert-trigger/migrations" -maxdepth 1 -type f -name '*.sql' \
    -exec basename {} .sql \; | sort | paste -sd, -)
container_name="holmesgpt-aiops-pg-backup-check-$$"
fresh_container_name="holmesgpt-aiops-pg-fresh-restore-check-$$"
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
        docker logs "$fresh_container_name" >&2 || true
    fi
    docker stop "$container_name" >/dev/null 2>&1 || true
    docker stop "$fresh_container_name" >/dev/null 2>&1 || true
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
docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
    < "$repo_dir/postgresql-roles.psql"
for migration in "$repo_dir"/alert-trigger/migrations/*.sql; do
    docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_migrator -d postgres \
        < "$migration"
    version="$(basename "$migration" .sql)"
    docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U aiops_migrator -d postgres \
        -c "INSERT INTO schema_migrations (version) VALUES ('$version') ON CONFLICT DO NOTHING" \
        >/dev/null
done

stage="provision a database-scoped read-only backup identity"
docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
CREATE ROLE aiops_backup LOGIN;
GRANT CONNECT ON DATABASE postgres TO aiops_backup;
GRANT USAGE ON SCHEMA public TO aiops_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO aiops_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO aiops_backup;
SQL

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

backup_path="/tmp/aiops-backup-check.dump"
host_backup_path="$backup_dir/aiops-backup-check.dump"
docker exec "$container_name" env \
    PGHOST=127.0.0.1 PGUSER=aiops_backup PGDATABASE=postgres \
    /usr/local/bin/backup-postgres.sh "$backup_path"

stage="copy the mode-restricted dump to the host for encryption"
docker cp "$container_name:$backup_path" "$host_backup_path"
python3 - "$host_backup_path" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
metadata = os.stat(path)
if stat.S_IMODE(metadata.st_mode) != 0o600 or not os.access(path, os.R_OK):
    raise SystemExit("host-side backup copy must be readable and mode 0600")
if metadata.st_uid != os.getuid():
    raise SystemExit("host-side backup copy must belong to the verifier process")
PY

stage="encrypt the backup with CMS AES-256-GCM"
encrypted_backup_path="$backup_dir/aiops-backup-check.cms.der"
if ! bash "$repo_dir/encrypt-postgres-backup.sh" "$host_backup_path" \
    "$encrypted_backup_path" "$backup_dir/recipient.crt" \
    >"$backup_dir/encrypt.stdout" 2>"$backup_dir/encrypt.stderr"; then
    error_detail="$(tr '\n' ' ' < "$backup_dir/encrypt.stderr" | cut -c 1-1000)"
    echo "::error title=OpenSSL CMS encryption error::$error_detail"
    cat "$backup_dir/encrypt.stderr" >&2
    exit 1
fi
stage="decrypt the CMS AES-256-GCM backup"
decrypted_backup_path="$backup_dir/aiops-encrypted-restore-check.dump"
if ! bash "$repo_dir/decrypt-postgres-backup.sh" "$encrypted_backup_path" \
    "$decrypted_backup_path" "$backup_dir/recipient.crt" "$backup_dir/recipient.key" \
    >"$backup_dir/decrypt.stdout" 2>"$backup_dir/decrypt.stderr"; then
    error_detail="$(tr '\n' ' ' < "$backup_dir/decrypt.stderr" | cut -c 1-1000)"
    echo "::error title=PostgreSQL backup decryption error::$error_detail"
    cat "$backup_dir/decrypt.stderr" >&2
    exit 1
fi

stage="verify encrypted and decrypted backup permissions and overwrite protection"
encrypted_mode="$(docker exec "$container_name" stat -c '%a' \
    /backups/aiops-backup-check.cms.der)"
if [[ "$encrypted_mode" != "600" ]]; then
    echo "encrypted backup file permissions were $encrypted_mode instead of 600" >&2
    exit 1
fi

if bash "$repo_dir/encrypt-postgres-backup.sh" \
    "$host_backup_path" "$encrypted_backup_path" \
    "$backup_dir/recipient.crt" >/dev/null 2>&1; then
    echo "encrypted backup command unexpectedly overwrote an existing file" >&2
    exit 1
fi

python3 - "$host_backup_path" "$decrypted_backup_path" <<'PY'
import hashlib
import os
import stat
import sys

source_path, decrypted_path = sys.argv[1:]
if stat.S_IMODE(os.stat(decrypted_path).st_mode) != 0o600:
    raise SystemExit("decrypted backup file permissions must be 0600")

def digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.digest()

if digest(source_path) != digest(decrypted_path):
    raise SystemExit("decrypted backup did not match the original archive")
PY
if bash "$repo_dir/decrypt-postgres-backup.sh" "$encrypted_backup_path" \
    "$decrypted_backup_path" "$backup_dir/recipient.crt" "$backup_dir/recipient.key" \
    >/dev/null 2>&1; then
    echo "decrypt command unexpectedly overwrote an existing file" >&2
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
if bash "$repo_dir/decrypt-postgres-backup.sh" \
    "$backup_dir/aiops-backup-tampered.cms.der" \
    "$backup_dir/aiops-tampered-restore-check.dump" \
    "$backup_dir/recipient.crt" "$backup_dir/recipient.key" >/dev/null 2>&1; then
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
if [[ "$restored_migrations" != "$expected_migrations" ]]; then
    echo "restored database contained $restored_migrations migration records instead of $expected_migrations" >&2
    exit 1
fi
restored_migration_versions="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c "SELECT string_agg(version, ',' ORDER BY version) FROM schema_migrations")"
if [[ "$restored_migration_versions" != "$expected_migration_versions" ]]; then
    echo "restored database contained an unexpected migration version set: $restored_migration_versions" >&2
    exit 1
fi

restored_resource="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c "SELECT resource FROM incidents WHERE id = '00000000-0000-4000-8000-000000000001'")"
if [[ "$restored_resource" != "order-service" ]]; then
    echo "restored incident did not contain the legacy-compatible resource scope" >&2
    exit 1
fi

restored_recovery_schema="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_restore_test -c "SELECT to_regclass('public.break_glass_recovery_requests') IS NOT NULL AND to_regprocedure('public.request_break_glass_admin_recovery(uuid,text)') IS NOT NULL AND to_regprocedure('public.approve_break_glass_admin_recovery(bigint,boolean)') IS NOT NULL")"
if [[ "$restored_recovery_schema" != "t" ]]; then
    echo "restored database did not contain two-person break-glass recovery schema and functions" >&2
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

for database in aiops_restore_test aiops_encrypted_restore_test; do
    restored_action_contract="$(docker exec "$container_name" psql -At -U postgres -d "$database" -c "
        SELECT to_regclass('public.action_executions') IS NOT NULL
           AND to_regclass('public.action_executions_recovery_idx') IS NOT NULL
           AND to_regclass('public.tasks_pending_created_at_idx') IS NOT NULL
           AND to_regclass('public.tasks_metrics_status_attempt_idx') IS NOT NULL
           AND to_regclass('public.outbox_investigation_task_available_idx') IS NOT NULL
           AND to_regclass('public.outbox_dead_lettered_idx') IS NOT NULL
           AND EXISTS (
               SELECT 1 FROM pg_constraint
               WHERE conrelid = 'public.action_executions'::regclass
                 AND contype = 'c'
                 AND pg_get_constraintdef(oid) LIKE '%dispatching%rollback_pending%'
           )
           AND has_table_privilege('aiops_runtime', 'action_executions', 'SELECT')
           AND has_table_privilege('aiops_runtime', 'action_executions', 'INSERT')
           AND has_table_privilege('aiops_runtime', 'action_executions', 'UPDATE')
           AND NOT has_table_privilege('aiops_worker', 'action_executions', 'SELECT')
           AND NOT has_table_privilege('aiops_worker', 'action_executions', 'INSERT')
           AND NOT has_table_privilege('aiops_worker', 'action_executions', 'UPDATE')
           AND has_column_privilege('aiops_runtime', 'outbox_events', 'dead_lettered_at', 'UPDATE')
           AND NOT has_column_privilege('aiops_worker', 'outbox_events', 'dead_lettered_at', 'UPDATE')
    ")"
    if [[ "$restored_action_contract" != "t" ]]; then
        echo "$database did not preserve action recovery schema and least-privilege grants" >&2
        exit 1
    fi
done

encrypted_restored_incident="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_encrypted_restore_test -c "SELECT alert_name || ':' || fingerprint FROM incidents WHERE id = '00000000-0000-4000-8000-000000000001'")"
if [[ "$encrypted_restored_incident" != "$restored_incident" ]]; then
    echo "encrypted backup restore did not contain the expected synthetic incident" >&2
    exit 1
fi

encrypted_migrations="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_encrypted_restore_test -c 'SELECT count(*) FROM schema_migrations')"
if [[ "$encrypted_migrations" != "$expected_migrations" ]]; then
    echo "encrypted backup restore contained $encrypted_migrations migration records instead of $expected_migrations" >&2
    exit 1
fi
encrypted_migration_versions="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_encrypted_restore_test -c "SELECT string_agg(version, ',' ORDER BY version) FROM schema_migrations")"
if [[ "$encrypted_migration_versions" != "$expected_migration_versions" ]]; then
    echo "encrypted backup restore contained an unexpected migration version set: $encrypted_migration_versions" >&2
    exit 1
fi

encrypted_identity_columns="$(docker exec "$container_name" psql -At -U postgres \
    -d aiops_encrypted_restore_test -c "SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND column_name IN ('session_generation', 'reactivation_requested_at') ORDER BY column_name")"
if [[ "$encrypted_identity_columns" != $'reactivation_requested_at\nsession_generation' ]]; then
    echo "encrypted backup restore did not contain user reactivation columns" >&2
    exit 1
fi

stage="start an isolated empty PostgreSQL cluster for fresh-cluster recovery"
docker run --detach --rm --network none --name "$fresh_container_name" \
    --tmpfs /var/lib/postgresql/data:rw,size=256m \
    -e POSTGRES_HOST_AUTH_METHOD=trust "$postgres_image" >/dev/null

fresh_ready=false
for _ in $(seq 1 30); do
    if docker exec "$fresh_container_name" pg_isready -U postgres >/dev/null 2>&1; then
        fresh_ready=true
        break
    fi
    sleep 1
done
if [[ "$fresh_ready" != true ]]; then
    echo "fresh-cluster PostgreSQL did not become ready" >&2
    exit 1
fi

stage="verify the fresh cluster has no application roles"
fresh_role_count="$(docker exec "$fresh_container_name" psql -At -U postgres -d postgres \
    -c "SELECT count(*) FROM pg_roles WHERE rolname IN ('aiops_runtime', 'aiops_migrator', 'aiops_worker', 'aiops_break_glass')")"
if [[ "$fresh_role_count" != "0" ]]; then
    echo "fresh PostgreSQL cluster unexpectedly already contains AIOps roles" >&2
    exit 1
fi

stage="provision fresh-cluster database identities and restore as migrator"
docker exec "$fresh_container_name" createdb -U postgres aiops_fresh_restore
docker exec -i "$fresh_container_name" psql -v ON_ERROR_STOP=1 -U postgres -d aiops_fresh_restore \
    < "$repo_dir/postgresql-roles.psql"
docker cp "$host_backup_path" "$fresh_container_name:/tmp/aiops-fresh-restore.dump" >/dev/null
docker exec "$fresh_container_name" pg_restore --exit-on-error --single-transaction \
    --no-owner --no-acl --username=aiops_migrator --dbname=aiops_fresh_restore \
    /tmp/aiops-fresh-restore.dump
docker exec -i "$fresh_container_name" psql -v ON_ERROR_STOP=1 -U postgres -d aiops_fresh_restore \
    < "$repo_dir/postgresql-roles.psql"

stage="verify fresh-cluster data, object ownership and reconstructed privileges"
fresh_incident="$(docker exec "$fresh_container_name" psql -At -U postgres \
    -d aiops_fresh_restore -c "SELECT alert_name || ':' || fingerprint FROM incidents WHERE id = '00000000-0000-4000-8000-000000000001'")"
if [[ "$fresh_incident" != "$restored_incident" ]]; then
    echo "fresh-cluster restore did not contain the expected synthetic incident" >&2
    exit 1
fi
fresh_migrations="$(docker exec "$fresh_container_name" psql -At -U postgres \
    -d aiops_fresh_restore -c 'SELECT count(*) FROM schema_migrations')"
if [[ "$fresh_migrations" != "$expected_migrations" ]]; then
    echo "fresh-cluster restore contained $fresh_migrations migration records instead of $expected_migrations" >&2
    exit 1
fi
fresh_migration_versions="$(docker exec "$fresh_container_name" psql -At -U postgres \
    -d aiops_fresh_restore -c "SELECT string_agg(version, ',' ORDER BY version) FROM schema_migrations")"
if [[ "$fresh_migration_versions" != "$expected_migration_versions" ]]; then
    echo "fresh-cluster restore contained an unexpected migration version set: $fresh_migration_versions" >&2
    exit 1
fi
fresh_role_contract="$(docker exec -i "$fresh_container_name" psql -At -F '|' -U postgres \
    -d aiops_fresh_restore <<'SQL'
SELECT
    NOT EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname = 'public' AND tableowner <> 'aiops_migrator'
    ),
    NOT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'S' AND pg_get_userbyid(c.relowner) <> 'aiops_migrator'
    ),
    NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND pg_get_userbyid(p.proowner) <> 'aiops_migrator'
    ),
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'incidents'
          AND column_name = 'resource' AND is_nullable = 'NO'
    ),
    EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.incidents'::regclass AND conname = 'incidents_resource_format'
    ),
    to_regclass('public.incidents_resource_created_idx') IS NOT NULL,
    to_regclass('public.audit_events_incident_timeline_idx') IS NOT NULL,
    NOT has_table_privilege('aiops_runtime', 'users', 'INSERT'),
    NOT has_table_privilege('aiops_runtime', 'users', 'UPDATE'),
    has_column_privilege('aiops_runtime', 'users', 'role', 'UPDATE'),
    NOT has_column_privilege('aiops_runtime', 'users', 'oidc_subject', 'UPDATE'),
    NOT (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'aiops_break_glass'),
    has_function_privilege('aiops_break_glass', 'public.request_break_glass_admin_recovery(uuid,text)', 'EXECUTE'),
    has_function_privilege('aiops_break_glass', 'public.approve_break_glass_admin_recovery(bigint,boolean)', 'EXECUTE'),
    NOT has_function_privilege('aiops_runtime', 'public.approve_break_glass_admin_recovery(bigint,boolean)', 'EXECUTE'),
    has_table_privilege('aiops_worker', 'incidents', 'SELECT'),
    NOT has_table_privilege('aiops_worker', 'incidents', 'UPDATE'),
    has_column_privilege('aiops_worker', 'tasks', 'status', 'UPDATE'),
    NOT has_column_privilege('aiops_worker', 'tasks', 'id', 'UPDATE'),
    has_column_privilege('aiops_worker', 'audit_events', 'details', 'INSERT'),
    NOT has_table_privilege('aiops_worker', 'audit_events', 'UPDATE'),
    has_schema_privilege('aiops_migrator', 'public', 'CREATE'),
    to_regclass('public.action_executions') IS NOT NULL,
    to_regclass('public.action_executions_recovery_idx') IS NOT NULL
        AND to_regclass('public.tasks_pending_created_at_idx') IS NOT NULL
        AND to_regclass('public.tasks_metrics_status_attempt_idx') IS NOT NULL
        AND to_regclass('public.outbox_investigation_task_available_idx') IS NOT NULL,
    to_regclass('public.outbox_dead_lettered_idx') IS NOT NULL,
    EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.action_executions'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%dispatching%rollback_pending%'
    ),
    has_table_privilege('aiops_runtime', 'action_executions', 'SELECT')
        AND has_table_privilege('aiops_runtime', 'action_executions', 'INSERT')
        AND has_table_privilege('aiops_runtime', 'action_executions', 'UPDATE'),
    NOT has_table_privilege('aiops_worker', 'action_executions', 'SELECT')
        AND NOT has_table_privilege('aiops_worker', 'action_executions', 'INSERT')
        AND NOT has_table_privilege('aiops_worker', 'action_executions', 'UPDATE'),
    has_column_privilege('aiops_runtime', 'outbox_events', 'dead_lettered_at', 'UPDATE')
        AND NOT has_column_privilege('aiops_worker', 'outbox_events', 'dead_lettered_at', 'UPDATE');
SQL
)"
if [[ "$fresh_role_contract" != "t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t" ]]; then
    echo "fresh-cluster restore did not preserve the expected migrator ownership and role grants: $fresh_role_contract" >&2
    exit 1
fi

echo "PostgreSQL backup verification passed: plaintext and AES-256-GCM archives restored $expected_migrations migrations; a second empty PostgreSQL cluster was provisioned with application roles, restored as aiops_migrator with --no-owner --no-acl, and had ownership/grants reconstructed; overwrite protection held"
