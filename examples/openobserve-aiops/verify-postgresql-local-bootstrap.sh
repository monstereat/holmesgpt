#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
postgres_image="postgres:16.6-alpine"
suffix="$(openssl rand -hex 4)"
db_name="holmes-aiops-role-db-${suffix}"
export POSTGRES_ADMIN_PASSWORD="$(openssl rand -hex 32)"
export POSTGRES_PASSWORD="$POSTGRES_ADMIN_PASSWORD"
export AIOPS_DB_RUNTIME_PASSWORD="$(openssl rand -hex 32)"
export AIOPS_DB_MIGRATOR_PASSWORD="$(openssl rand -hex 32)"
export AIOPS_DB_WORKER_PASSWORD="$(openssl rand -hex 32)"

cleanup() {
    docker rm --force "$db_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ "$AIOPS_DB_RUNTIME_PASSWORD" == "$AIOPS_DB_MIGRATOR_PASSWORD" ]]; then
    echo "generated database role passwords unexpectedly match" >&2
    exit 1
fi

docker run --detach --name "$db_name" \
    --network none --hostname postgres \
    --tmpfs /var/lib/postgresql/data:rw,size=256m \
    --env POSTGRES_USER=aiops \
    --env POSTGRES_PASSWORD \
    --env POSTGRES_DB=aiops \
    "$postgres_image" >/dev/null

ready=false
for _ in $(seq 1 30); do
    if docker exec "$db_name" pg_isready -U aiops -d aiops >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    echo "temporary PostgreSQL did not become ready" >&2
    exit 1
fi

run_bootstrap() {
    docker run --rm --network "container:$db_name" --read-only \
        --cap-drop ALL --security-opt no-new-privileges \
        --tmpfs /tmp:rw,noexec,nosuid,size=16m \
        --entrypoint /bin/sh \
        --env POSTGRES_ADMIN_PASSWORD \
        --env POSTGRES_HOST=127.0.0.1 \
        --env AIOPS_DB_RUNTIME_PASSWORD \
        --env AIOPS_DB_MIGRATOR_PASSWORD \
        --env AIOPS_DB_WORKER_PASSWORD \
        --volume "$repo_dir/bootstrap-postgresql-roles.sh:/bootstrap/bootstrap-postgresql-roles.sh:ro" \
        --volume "$repo_dir/postgresql-roles.psql:/bootstrap/postgresql-roles.psql:ro" \
        --volume "$repo_dir/postgresql-local-role-passwords.psql:/bootstrap/postgresql-local-role-passwords.psql:ro" \
        "$postgres_image" /bootstrap/bootstrap-postgresql-roles.sh >/dev/null
}

run_bootstrap
run_bootstrap

psql_as() {
    local role="$1"
    local password="$2"
    local statement="$3"
    PGPASSWORD="$password" DB_ROLE="$role" SQL_STATEMENT="$statement" \
    docker run --rm --network "container:$db_name" --read-only \
        --tmpfs /tmp:rw,noexec,nosuid,size=16m \
        --entrypoint /bin/sh \
        --env PGPASSWORD \
        --env POSTGRES_HOST=127.0.0.1 \
        --env DB_ROLE="$role" \
        --env SQL_STATEMENT="$statement" \
        "$postgres_image" -ec \
        'psql --no-psqlrc --no-align --tuples-only --set ON_ERROR_STOP=1 --host "$POSTGRES_HOST" --username "$DB_ROLE" --dbname aiops --command "$SQL_STATEMENT"'
}

runtime_user="$(psql_as aiops_runtime "$AIOPS_DB_RUNTIME_PASSWORD" 'SELECT current_user')"
migrator_user="$(psql_as aiops_migrator "$AIOPS_DB_MIGRATOR_PASSWORD" 'SELECT current_user')"
worker_user="$(psql_as aiops_worker "$AIOPS_DB_WORKER_PASSWORD" 'SELECT current_user')"
if [[ "$runtime_user" != "aiops_runtime" || "$migrator_user" != "aiops_migrator" || "$worker_user" != "aiops_worker" ]]; then
    echo "database role password authentication returned an unexpected identity" >&2
    exit 1
fi

psql_as aiops_migrator "$AIOPS_DB_MIGRATOR_PASSWORD" \
    'CREATE TABLE public.bootstrap_probe (id integer PRIMARY KEY); INSERT INTO public.bootstrap_probe VALUES (1)' >/dev/null
psql_as aiops_runtime "$AIOPS_DB_RUNTIME_PASSWORD" \
    'INSERT INTO public.bootstrap_probe VALUES (2)' >/dev/null
probe_count="$(psql_as aiops_runtime "$AIOPS_DB_RUNTIME_PASSWORD" 'SELECT count(*) FROM public.bootstrap_probe')"
if [[ "$probe_count" != "2" ]]; then
    echo "runtime role could not use migrator-owned business table" >&2
    exit 1
fi

if psql_as aiops_runtime "$AIOPS_DB_RUNTIME_PASSWORD" \
    'CREATE TABLE public.runtime_must_not_create (id integer)' >/dev/null 2>&1; then
    echo "runtime role unexpectedly created a schema object" >&2
    exit 1
fi

if POSTGRES_ADMIN_PASSWORD="$POSTGRES_ADMIN_PASSWORD" \
    AIOPS_DB_RUNTIME_PASSWORD="$AIOPS_DB_RUNTIME_PASSWORD" \
    AIOPS_DB_MIGRATOR_PASSWORD="$AIOPS_DB_RUNTIME_PASSWORD" \
    AIOPS_DB_WORKER_PASSWORD="$AIOPS_DB_WORKER_PASSWORD" \
    docker run --rm --network "container:$db_name" --read-only \
    --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    --entrypoint /bin/sh \
    --env POSTGRES_ADMIN_PASSWORD \
    --env POSTGRES_HOST=127.0.0.1 \
    --env AIOPS_DB_RUNTIME_PASSWORD \
    --env AIOPS_DB_MIGRATOR_PASSWORD \
    --env AIOPS_DB_WORKER_PASSWORD \
    --volume "$repo_dir/bootstrap-postgresql-roles.sh:/bootstrap/bootstrap-postgresql-roles.sh:ro" \
    --volume "$repo_dir/postgresql-roles.psql:/bootstrap/postgresql-roles.psql:ro" \
    --volume "$repo_dir/postgresql-local-role-passwords.psql:/bootstrap/postgresql-local-role-passwords.psql:ro" \
    "$postgres_image" /bootstrap/bootstrap-postgresql-roles.sh >/dev/null 2>&1; then
    echo "bootstrap unexpectedly accepted identical runtime and migration passwords" >&2
    exit 1
fi

echo "PostgreSQL local bootstrap verification passed: idempotent setup, distinct role passwords, runtime DML, and schema creation denial."
