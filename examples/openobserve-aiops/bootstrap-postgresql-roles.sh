#!/bin/sh
set -eu

: "${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD is required}"
: "${AIOPS_DB_RUNTIME_PASSWORD:?AIOPS_DB_RUNTIME_PASSWORD is required}"
: "${AIOPS_DB_MIGRATOR_PASSWORD:?AIOPS_DB_MIGRATOR_PASSWORD is required}"

case "$AIOPS_DB_RUNTIME_PASSWORD" in
    *[!A-Za-z0-9_-]*|'') echo "runtime password must be URL-safe" >&2; exit 1 ;;
esac
case "$AIOPS_DB_MIGRATOR_PASSWORD" in
    *[!A-Za-z0-9_-]*|'') echo "migrator password must be URL-safe" >&2; exit 1 ;;
esac
if [ "${#AIOPS_DB_RUNTIME_PASSWORD}" -lt 32 ] || [ "${#AIOPS_DB_MIGRATOR_PASSWORD}" -lt 32 ]; then
    echo "database role passwords must be at least 32 characters" >&2
    exit 1
fi
if [ "$AIOPS_DB_RUNTIME_PASSWORD" = "$AIOPS_DB_MIGRATOR_PASSWORD" ]; then
    echo "database role passwords must be distinct" >&2
    exit 1
fi

export PGPASSWORD="$POSTGRES_ADMIN_PASSWORD"
psql --host "${POSTGRES_HOST:-postgres}" --port 5432 --username aiops --dbname aiops \
    --set ON_ERROR_STOP=1 --file /bootstrap/postgresql-roles.psql >/dev/null
psql --host "${POSTGRES_HOST:-postgres}" --port 5432 --username aiops --dbname aiops \
    --set ON_ERROR_STOP=1 \
    --set runtime_password="$AIOPS_DB_RUNTIME_PASSWORD" \
    --set migrator_password="$AIOPS_DB_MIGRATOR_PASSWORD" \
    --file /bootstrap/postgresql-local-role-passwords.psql >/dev/null
echo "PostgreSQL application roles are configured."
