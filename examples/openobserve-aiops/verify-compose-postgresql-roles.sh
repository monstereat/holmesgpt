#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir/../.."
compose=(docker compose -f "$repo_dir/docker-compose.yaml")

"${compose[@]}" exec -T incident-api python - <<'PY'
import json
import os
from urllib.request import Request, urlopen

import psycopg
from psycopg.errors import InsufficientPrivilege

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    current_user, migration_count, can_create, can_update_audit, can_update_ledger, can_insert_ledger = conn.execute(
        """SELECT current_user,
                  (SELECT count(*) FROM schema_migrations),
                  has_schema_privilege(current_user, 'public', 'CREATE'),
                  has_table_privilege(current_user, 'audit_events', 'UPDATE'),
                  has_table_privilege(current_user, 'schema_migrations', 'UPDATE'),
                  has_table_privilege(current_user, 'schema_migrations', 'INSERT')"""
    ).fetchone()
    assert current_user == "aiops_runtime", "Incident API is not using the runtime role"
    assert migration_count == 10, f"expected 10 migrations, found {migration_count}"
    assert not can_create and not can_update_audit and not can_update_ledger and not can_insert_ledger
    identity_columns = conn.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name IN ('session_generation', 'reactivation_requested_at')"
    ).fetchone()[0]
    assert identity_columns == 2, "user reactivation migration columns are missing"
    assert conn.execute("SELECT has_column_privilege(current_user, 'users', 'session_generation', 'UPDATE')").fetchone()[0]
    assert conn.execute("SELECT has_column_privilege(current_user, 'users', 'reactivation_requested_at', 'UPDATE')").fetchone()[0]
    assert conn.execute("SELECT has_table_privilege(current_user, 'users', 'UPDATE')").fetchone()[0]

    denied_statements = (
        "CREATE TABLE public.compose_role_probe (id integer)",
        "UPDATE audit_events SET event_type = event_type WHERE false",
        "UPDATE schema_migrations SET version = version WHERE false",
        "DELETE FROM schema_migrations WHERE false",
        "TRUNCATE schema_migrations",
    )
    for statement in denied_statements:
        try:
            with conn.transaction():
                conn.execute(statement)
                raise AssertionError(f"runtime unexpectedly permitted: {statement.split()[0]}")
        except InsufficientPrivilege:
            pass

users = json.loads(os.environ["AIOPS_TEST_USERS_JSON"])
operator = next(user for user in users if user.get("role") == "operator")
api_base = "http://127.0.0.1:8081"
login = Request(
    f"{api_base}/auth/login",
    data=json.dumps({"username": operator["username"], "password": operator["password"]}).encode(),
    headers={"Content-Type": "application/json"},
)
with urlopen(login, timeout=5) as response:
    assert response.status == 200
    token = json.load(response)["access_token"]
request = Request(f"{api_base}/api/incidents?limit=1", headers={"Authorization": f"Bearer {token}"})
with urlopen(request, timeout=5) as response:
    result = json.load(response)
    assert response.status == 200 and isinstance(result.get("items"), list)
print("Incident API runtime identity, denied DDL/ledger/audit mutation, and authenticated workbench read passed.")
PY

worker_user="$("${compose[@]}" exec -T incident-worker python -c 'import os,psycopg; conn=psycopg.connect(os.environ["WORKER_DATABASE_URL"]); print(conn.execute("SELECT current_user").fetchone()[0]); conn.close()')"
if [[ "$worker_user" != "aiops_worker" ]]; then
    echo "Incident worker is not using the worker role" >&2
    exit 1
fi

migrator_user="$("${compose[@]}" run --rm --no-deps incident-migrate python -c 'import os,psycopg; conn=psycopg.connect(os.environ["MIGRATION_DATABASE_URL"]); print(conn.execute("SELECT current_user").fetchone()[0]); conn.close()' 2>/dev/null)"
if [[ "$migrator_user" != "aiops_migrator" ]]; then
    echo "Migration service is not using the migrator role" >&2
    exit 1
fi

echo "Compose PostgreSQL role verification passed: 10 migrations; API=aiops_runtime, worker=aiops_worker, migrations=aiops_migrator."
