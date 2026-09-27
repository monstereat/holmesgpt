# Verification: Compose PostgreSQL identity separation

Date: 2026-09-27 (local Docker Compose test environment)

## Results

- `docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test config --quiet`: passed.
- `docker compose ... up -d --build`: passed after correcting Compose dollar-quoting and skipping table-owned sequences during owner transfer. Bootstrap, migration, and finalize one-shot containers exited 0.
- Existing PostgreSQL volume was reused. Migration versions remain `0001`–`0008`; data counts remain incidents=11, tasks=11, audit_events=53, outbox_events=11.
- `bash examples/openobserve-aiops/verify-compose-postgresql-roles.sh`: passed. API and worker use `aiops_runtime`; migration uses `aiops_migrator`; authenticated operator workbench read and runtime denial of DDL, migration-ledger mutation, and audit mutation passed.
- Incident API and worker, PostgreSQL, and Redis were healthy after startup.
- `docker compose ... --profile test run --rm incident-test`: **123 passed, 1 upstream Starlette/AnyIO deprecation warning** (26.54s).
- `git diff --check`: passed.

Safe evidence logs and preserved row counts are in this directory. Logs contain no credential values. The pre-existing stopped `alert-trigger` orphan was left intact.

## Scope and limits

This validates the current local test database and Compose runtime only. It does not validate a managed PostgreSQL provider, production IAM/secret injection, TLS connectivity, production backup/PITR, or measured RPO/RTO. No production database or data was accessed.
