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
    assert migration_count == 13, f"expected 13 migrations, found {migration_count}"
    assert not can_create and not can_update_audit and not can_update_ledger and not can_insert_ledger
    identity_columns = conn.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name IN ('session_generation', 'reactivation_requested_at')"
    ).fetchone()[0]
    assert identity_columns == 2, "user reactivation migration columns are missing"
    assert conn.execute("SELECT has_column_privilege(current_user, 'users', 'session_generation', 'UPDATE')").fetchone()[0]
    assert conn.execute("SELECT has_column_privilege(current_user, 'users', 'reactivation_requested_at', 'UPDATE')").fetchone()[0]
    assert conn.execute("SELECT has_table_privilege(current_user, 'users', 'UPDATE')").fetchone()[0]
    assert not conn.execute("SELECT has_table_privilege(current_user, 'break_glass_recovery_requests', 'SELECT')").fetchone()[0]
    assert not conn.execute("SELECT has_sequence_privilege(current_user, 'break_glass_recovery_requests_id_seq', 'USAGE')").fetchone()[0]
    assert conn.execute("SELECT has_table_privilege(current_user, 'oidc_login_transactions', 'DELETE')").fetchone()[0]
    assert conn.execute("SELECT has_table_privilege(current_user, 'revoked_sessions', 'DELETE')").fetchone()[0]
    break_glass_privileges = conn.execute(
        """SELECT NOT (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'aiops_break_glass'),
                  has_function_privilege('aiops_break_glass', 'public.request_break_glass_admin_recovery(uuid,text)', 'EXECUTE'),
                  has_function_privilege('aiops_break_glass', 'public.approve_break_glass_admin_recovery(bigint,boolean)', 'EXECUTE'),
                  NOT has_function_privilege('aiops_break_glass', 'public.break_glass_reactivate_admin(uuid,text,text,text,boolean)', 'EXECUTE'),
                  has_schema_privilege('aiops_break_glass', 'public', 'USAGE'),
                  has_table_privilege('aiops_break_glass', 'users', 'SELECT'),
                  has_table_privilege('aiops_break_glass', 'users', 'UPDATE'),
                  has_table_privilege('aiops_break_glass', 'audit_events', 'INSERT'),
                  has_table_privilege('aiops_break_glass', 'break_glass_recovery_requests', 'SELECT')"""
    ).fetchone()
    assert break_glass_privileges == (True, True, True, True, True, False, False, False, False), "break-glass role grants are too broad or incomplete"
    assert conn.execute("SELECT to_regprocedure('public.break_glass_reactivate_admin(uuid,text,text,text,boolean)') IS NOT NULL").fetchone()[0]
    for table in ("incidents", "tasks", "outbox_events", "approvals", "incident_retrospectives", "users", "audit_events", "schema_migrations"):
        assert not conn.execute("SELECT has_table_privilege(current_user, %s, 'DELETE')", (table,)).fetchone()[0], f"runtime can delete from {table}"

    denied_statements = (
        "CREATE TABLE public.compose_role_probe (id integer)",
        "UPDATE audit_events SET event_type = event_type WHERE false",
        "UPDATE schema_migrations SET version = version WHERE false",
        "DELETE FROM schema_migrations WHERE false",
        "TRUNCATE schema_migrations",
        "DELETE FROM incidents WHERE false",
        "DELETE FROM tasks WHERE false",
        "DELETE FROM outbox_events WHERE false",
        "DELETE FROM approvals WHERE false",
        "DELETE FROM incident_retrospectives WHERE false",
        "DELETE FROM users WHERE false",
    )
    for statement in denied_statements:
        try:
            with conn.transaction():
                conn.execute(statement)
                raise AssertionError(f"runtime unexpectedly permitted: {statement.split()[0]}")
        except InsufficientPrivilege:
            pass
    conn.execute("DELETE FROM oidc_login_transactions WHERE false")
    conn.execute("DELETE FROM revoked_sessions WHERE false")

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

echo "Compose PostgreSQL role verification passed: 13 migrations; API=aiops_runtime, worker=aiops_worker, migration=aiops_migrator, break-glass=two separately authenticated custodian functions."
