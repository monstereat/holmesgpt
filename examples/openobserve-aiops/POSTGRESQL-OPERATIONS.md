# PostgreSQL Operations

This runbook describes database operations for the incident service. The commands
are templates until an operator selects a target environment and its secret
manager. The local Docker Compose database is test-only; never point a production
command at its credentials or volume.

## Database ownership and migration boundary

- PostgreSQL is the durable source for incidents, tasks, approvals, identity
  mappings, audit events, retrospectives, session revocations, and the outbox.
- The API uses `aiops_runtime` to perform authenticated business and OIDC
  identity lifecycle operations. The worker uses the separate `aiops_worker`
  identity, limited to reading incidents/tasks, updating task and outbox
  processing fields, and appending worker audit events. The outbox dispatcher
  runs in the Incident API process, so only `aiops_runtime` can update
  `outbox_events.dead_lettered_at`; `aiops_worker` cannot access that column.
  Neither identity can create schema objects, alter the migration ledger, or
  change/delete audit history.
- A one-shot migration job uses the separate migration identity. It must finish
  successfully before application replicas start. The migration runner takes a
  PostgreSQL advisory transaction lock and records each applied migration.
- Incident API, worker, migration, recovery, and admission-benchmark connections
  use a 3-second default connection timeout. A `connect_timeout` explicitly
  present in the PostgreSQL DSN or supplied by a caller takes precedence. The
  timeout bounds connection establishment only; it does not bound SQL execution,
  lock waits, or total request duration.
- The current migration files are numbered `0001` through `0021`. They run in
  transactions. Migration `0014_incident_resource_scope` adds the persisted
  incident service key and lookup index and is applied to the authorized local
  test database. Migration `0015_incident_timeline_index` adds a partial index
  for keyset-paged incident audit history; it has now been applied locally and
  its pagination behavior is covered by repository code. Isolated role and
  backup/restore verifiers now pass through migration 0021. It uses ordinary transactional index creation,
  so measure lock/build time on a staging-sized copy before production. There are no
  automatic down migrations: do not delete migration records or manually
  reverse DDL to make an older binary start.

Migration `0016_restrict_runtime_user_columns` removes table-level INSERT and
UPDATE grants from `aiops_runtime` on `users`, then grants only the columns used
by local test-user seeding, verified OIDC claim synchronization, and account
lifecycle routes. It blocks direct updates to OIDC issuer/subject and creation
timestamps. It does not remove runtime access to role/resource-scope or active
state columns required by those application flows, so the API credential
remains trusted. On 2026-09-28 the project Compose migration gate applied it to
the authorized local test database; read-only checks confirmed migration 0016
and the expected table/column privileges. No functional permission verifier
was run at the time of the migration; the 2026-09-29 isolated role verifier now
checks the column allowlist through migration 0021.

The isolated role verifier also checks the deny side of this column allowlist:
runtime cannot insert identity lifecycle counters/timestamps or change the
user ID/creation timestamp, in addition to being unable to change OIDC issuer
or subject. The role template is intended for a dedicated AIOps database and
normalizes the grants it owns; it does not audit external role memberships or
attributes provisioned by a managed database administrator.

Migration `0017_action_execution_recovery` adds durable approval-action
execution state and an index for operator reconciliation. Migration
`0018_outbox_dead_letter` adds the outbox dead-letter timestamp and its index.
Migration `0019_dispatcher_dead_letter_runtime_role` assigns that column's
UPDATE permission to the API runtime identity and removes the redundant worker
grant, matching the process that performs dispatch and exhaustion handling.
Migrations 0017–0021 have been applied to the authorized local test database;
the ledger and effective runtime/worker column privileges were checked with
read-only queries. The 2026-09-29 isolated role verifier passed through
migration 0021; the backup verifier also passed plain/encrypted restore into a
fresh cluster. A focused current-source integration case verifies action
recovery from persisted `dispatching` and `rollback_pending` phases. Dead-letter
dispatch behavior and manual replay remain unverified.

Migration `0020_pending_task_age_index` adds a partial index over `created_at`
for queued, running, and retrying tasks. It matches the oldest-pending-age
metrics query so PostgreSQL can seek the earliest active task without scanning
terminal task history. The index is additive; its transactional build can still
block writes while it is created, so measure it on a staging-sized copy before
production. Local index presence does not prove the query plan or representative-
scale performance.

Migration `0021_metrics_scan_indexes` adds covering indexes for the exact task
status/retry aggregation and the queued-task-to-outbox lookup. It can reduce
heap reads, while the all-history aggregation remains O(retained tasks). The
outbox query now starts from eligible queued/retrying tasks, whose count is
bounded by the configured pending-task cap. The migration has been applied to
the authorized local test database, and read-only catalog checks confirmed both
indexes exist. Query behavior, query plans, verifier scripts, index size, write
amplification, and lock time remain unverified; assess the latter three before
a production migration.

Migration `0012_break_glass_admin_recovery` introduced the recovery function;
migration `0013_two_person_admin_recovery` replaces its shared-login invocation
with separately authenticated request and approval functions. `aiops_break_glass`
is a `NOLOGIN` group with only schema usage and function execution. The request
function records `session_user` as the requester; approval requires a different
authenticated login in that custodian group and records both database
identities in the audit event. It rejects an active administrator, a changed
target, an expired request, and missing out-of-band IdP membership attestation.
The approval step rechecks the lockout and target under an exclusive lock, then
reactivates the account, increments `session_generation`, completes the request,
and appends the audit event in one transaction. Rehearse both separately
authenticated stages and managed-identity mapping in staging; do not test it
against production data.
When upgrading from the earlier shared `aiops_break_glass LOGIN` role, a database
administrator must rerun `postgresql-roles.psql` before migration 0013. The
migration fails closed if that role can still log in; the migration identity
cannot disable database roles itself.

The repository role template is [`postgresql-roles.psql`](postgresql-roles.psql).
It is for a fresh database and does not transfer ownership of an existing
schema. Managed services may require their supported IAM or role-creation flow;
verify equivalent permissions in staging before production.

The local Compose stack uses a PostgreSQL administrator only for its idempotent
role bootstrap/finalize jobs. Incident API and worker connect as
`aiops_runtime`; the worker connects as `aiops_worker`; the migration job
connects as `aiops_migrator`. Each role has a distinct random password in the
private mode-0600 local runtime configuration. The migration job completes before the post-migration finalize
job, which re-applies grants and removes runtime DML privileges from
`schema_migrations`, including on a fresh database. This validates role wiring
against the local Compose volume; it does not prove managed-database IAM or
production secret injection.

On 2026-09-27 the existing local Compose database was switched to separate API
and migration identities without recreating its volume. At that point the
worker still shared the API identity; the dedicated `aiops_worker` grants were
added and verified on 2026-09-28 as recorded below. The local bootstrap
transferred ownership of existing `public` relations and schema to
`aiops_migrator` so later migrations can run; sequences owned by tables are
transferred with their table. Runtime DDL, migration-ledger mutation, and audit
mutation were denied, and the pre-existing incident, task, audit, and outbox
counts remained unchanged. This is evidence for this local test volume only.
The bootstrap ownership transfer is intentionally specific to the local
`aiops` database and is not a managed-production migration procedure.

On 2026-09-28 migration `0010_worker_database_privileges` was applied to the persistent local test database. The API uses `aiops_runtime`, Celery uses `aiops_worker`, and migrations use `aiops_migrator`; the Compose role verifier confirmed those live service identities, denied API DDL/migration-ledger/audit mutation, and authenticated a local operator workbench read. A direct privilege check confirmed the worker can perform task/outbox updates and audit inserts while user reads, incident writes, and audit mutation are denied. After loading the documented mode-0600 local DeepSeek key file, Holmes and the Celery worker became healthy. A synthetic webhook then produced one incident/task; the worker completed the first attempt, persisted the matching Trace ID and OpenObserve query tool in the task result, and appended `task.completed` to audit history. Counts are 12 incidents, 12 tasks, 54 audit rows, and 12 outbox rows. A custom-format dump of this current local database was encrypted and restored into a separate isolated PostgreSQL 16.6 container; all 10 migration records, business row counts, and the completed smoke-task evidence/audit matched. The temporary archive, certificate, and restore container were removed after verification. This is still test-only evidence.

Later on 2026-09-28 migration `0011_restrict_runtime_delete` was applied transactionally to the same local test database after the API runtime role was found to have unnecessary DELETE on business tables. Migrations `0012` and `0013` add function-only, two-custodian administrator recovery. The live Compose role verifier last confirmed 13 migration records and tested the request/approval path with distinct database logins, the disabled shared-role identity, and restricted API/worker grants. The PostgreSQL role and backup verifiers exercise the source snapshot from that time against isolated PostgreSQL 16. Migration 0014_incident_resource_scope has now been applied to the local test database, bringing its ledger to 14 records; the API and worker images were rebuilt and recreated without functional checks. The previously recorded business row counts predate migration 0014. Provider key custody, off-host retention, target-platform identity mapping, and managed service restore remain untested.

`0014_incident_resource_scope` preserves existing incidents as `order-service`, requires normalized lowercase resource keys, and supports filtering by service-level OIDC scopes. It has been applied only to the authorized local test database. The updated API/worker containers are running, but the feature and updated role/backup verifiers have not been functionally verified; staging and production remain gated on their own reviewed migration plan.

`0015_incident_timeline_index` supports bounded keyset pagination of incident audit history. The workbench initially loads the latest 50 events and can fetch older pages. On 2026-09-28 it was applied to the authorized local test database through the project migration service; a read-only query confirmed `current_user=aiops_migrator`, 15 ledger rows and the expected index. Pagination behavior and the updated role/backup verifiers remain untested. The index creation is transactional and may hold a table lock while it builds, so measure its duration on a staging-sized copy before production.

## Release migration sequence

1. Build and identify the immutable application image for the release. Review
   the migration SQL and confirm that the new application version is compatible
   with the current schema during rollout.
2. Confirm the selected database endpoint, verified TLS, migration identity,
   runtime identity, connection limits, backup status, and restore contact. Load
   credentials from the platform secret manager; do not put URLs or passwords in
   shell history, CI output, or a committed environment file.
3. Take the platform's encrypted, off-host backup or confirm the configured
   point-in-time recovery window. Record the backup/PITR reference and the
   operator responsible for restore.
4. Run exactly one migration job for the release with
   `AIOPS_ENV=production`, `MIGRATION_DATABASE_URL` set to the dedicated
   migration identity, and `MIGRATION_DATABASE_ROLE` set to the exact effective
   PostgreSQL `current_user`. The runner checks this identity before applying
   any migration. Do not set `DATABASE_URL` as a migration fallback. Require
   job exit code zero and verify the expected migration versions in
   `schema_migrations` before rolling out API or worker replicas.
5. Start the new application version, then verify `/readyz`, database
   connectivity, task/outbox processing, and the release-specific smoke checks.
   Keep the previous immutable application image available until acceptance.

The repository's `incident-migrate` Compose service is explicitly configured
for `AIOPS_ENV=local`; it is not the production job manifest. The target platform
must provide a one-shot job with the same command (`python migrate.py`),
dedicated secret injection, TLS policy, network access, retry/timeout limits,
and a release dependency that gates the application rollout.

## Failure and rollback handling

The migration runner applies each migration and its version record in a
transaction, so a failed migration does not record itself as applied. Stop the
release rollout if the migration job fails. Preserve the error output after
redacting connection strings and credentials, resolve the cause, and rerun only
after confirming the database state and advisory lock are clear.

Because there are no down migrations, application rollback is safe only when
the prior image is compatible with the already-applied schema. Prefer
forward-compatible expand/contract migrations: add optional structures first,
deploy code that supports both shapes, backfill separately, and remove obsolete
structures only in a later release after the rollback window. If compatibility
cannot be established, stop writes and restore to a separate database or use the
platform's point-in-time recovery procedure with the database owner. Do not
restore a backup over the live database as an automatic rollback step.

Production rollback acceptance remains incomplete until the selected platform
has rehearsed both an application rollback after a successful migration and a
database restore/PITR into an isolated target, with measured recovery time and
verified incident, task, outbox, and audit records.

### Local Docker data-disk exhaustion

If the local PostgreSQL log reports `No space left on device` while writing a
checkpoint and the container repeatedly rejects connections, inspect Docker
storage first with `docker system df`; do not delete or recreate the PostgreSQL
volume, run `docker compose down -v`, or use `docker system prune --volumes`.
The September 28, 2026 incident first recovered PostgreSQL after reclaiming
924.8 MB of BuildKit cache. Redis still could not write its AOF because the
shared Docker data disk remained full; reclaiming a further 900.2 MB of builder
cache and 1.333 GB of dangling images restored PostgreSQL, Redis, Incident API,
and the worker. No containers, volumes, or project files were removed. Both
`docker builder prune --force` and `docker image prune --force` affect shared
Docker resources across projects; inspect `docker system df` first and accept
the rebuild or image re-download cost before using them. Never use `docker
image prune --all` as part of this recovery procedure. After reclaiming space,
confirm Redis AOF `aof_last_write_status=ok`, `pg_isready` acceptance, and
healthy Compose states before using the migration service. Do not run
`pg_resetwal` or retry a migration while PostgreSQL is still in crash recovery.
The recorded recovery restored local service availability but did not validate
backup integrity or replace an isolated restore rehearsal; the Docker data
disk remained at 96% usage afterward.

## Backup and restore evidence

### Local Compose test database

The Compose PostgreSQL service has no published host port. To create a protected
custom-format archive of its `aiops` database, use the local helper:

```bash
bash examples/openobserve-aiops/backup-local-postgres.sh \
  "$HOME/secure-backups/aiops-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

It runs `pg_dump` inside the PostgreSQL container through
[`compose-local.sh`](compose-local.sh), streams the archive to a mode-`0600`
temporary file beside the requested destination, validates it with the
container's `pg_restore`, then publishes it atomically without overwriting an
existing path. A failure removes the temporary file. No PostgreSQL port is
exposed and the script does not print a connection string or password.

The archive covers only the `aiops` database: schema, migration ledger, and
database rows (including local account hashes, incidents, tasks, approvals,
audit history, and outbox records). It excludes cluster-level roles and all
other Compose state, including OpenObserve telemetry (`oo-data`), Redis AOF
(`aiops-redis-data`), the order-service action journal (`order-action-data`),
and local credentials/API keys stored under
`${XDG_CONFIG_HOME:-$HOME/.config}/holmesgpt-aiops`. Store
the archive on protected storage. This helper does not encrypt or upload it and
does not provide volume backups, off-host retention, or PITR. Restore only into
a separate local test database/instance using the reviewed restore procedure;
do not overwrite the active Compose database.

On 2026-09-29 the helper produced an 82,192-byte mode-`0600` archive from the
local database and restored it into an isolated PostgreSQL 16.6 container with
no network and a temporary data filesystem. The restored `schema_migrations`
set matched all 21 source migrations; row counts were 13 incidents, 13 tasks,
55 audit events and 13 outbox events, and all core tables including
`action_executions` existed. The source had no action-execution rows. Restore
used `--no-owner --no-acl`, so this drill does not verify role or ACL recovery.
The temporary archive and container were removed; the active Compose database
was only read by `pg_dump`.

For a database session configured through libpq environment or service-file
credentials, create a new archive path with:

```bash
bash examples/openobserve-aiops/backup-postgres.sh /secure-backup-path/aiops-$(date -u +%Y%m%dT%H%M%SZ).dump
```

The configured backup identity must be distinct from the application runtime
identity and have `CONNECT`, schema `USAGE`, and read access to every table and
sequence in the application database that the archive must preserve. Provision
those grants through the database owner's supported process, including access
to objects created by future migrations; the repository's
`postgresql-roles.psql` template intentionally does not create a backup login.
Do not grant cluster-wide read access on a shared PostgreSQL cluster merely to
make this helper work. Prefer the selected provider's database backup/PITR
service when it offers stronger isolation and managed retention.

The helper uses custom format, mode `0600`, validates the archive catalog, and
refuses to overwrite an existing file. It does not encrypt or copy the archive
off host. Production must use platform-managed encryption, off-host retention,
access logging, retention/deletion policy, and PITR/WAL archiving approved by the
data owner. Never treat the local helper alone as a production backup service.

Where a platform-approved recipient certificate is used, the optional
[`encrypt-postgres-backup.sh`](encrypt-postgres-backup.sh) helper can encrypt an
existing archive as CMS DER with AES-256-GCM and atomically create a new mode-
`0600` file without overwriting an existing artifact:

```bash
bash examples/openobserve-aiops/encrypt-postgres-backup.sh \
  /secure-ephemeral/aiops-backup.dump \
  /secure-backup/aiops-backup.cms.der \
  /run/secrets/backup-recipient.crt
```

The helper checks that the certificate is parseable and unexpired, but does not
establish its trust, ownership, revocation state, or key-rotation policy. The
plaintext input remains in place; create it only on approved encrypted or
ephemeral storage and follow the platform's data-removal policy. This helper
does not upload or retain backups off host and does not replace managed backup
encryption/PITR. For restore, retrieve the matching private key from the
approved secret manager, then use the helper below to create a protected,
mode-`0600` plaintext copy in scratch storage. It refuses to overwrite an
existing path, validates the PostgreSQL archive before publishing the file,
and removes an incomplete temporary file on failure:

```bash
bash examples/openobserve-aiops/decrypt-postgres-backup.sh \
  /secure-backup/aiops-backup.cms.der \
  /secure-ephemeral/aiops-restore.dump \
  /run/secrets/backup-recipient.crt \
  /run/secrets/backup-recipient.key
```

Keep the plaintext copy only on approved encrypted or ephemeral storage and
remove it according to the data owner's retention policy after the isolated
restore has been accepted.

OpenSSL CMS supports AES-GCM as an authenticated-encryption mode; confirm the
selected platform's OpenSSL build and cryptographic policy before adopting this
format ([OpenSSL CMS command documentation](https://docs.openssl.org/3.4/man1/openssl-cms/)).

Restore only to a new isolated database or instance first:

For a new PostgreSQL cluster, first provision the database and the required
roles or managed-identity mappings. The archive does not create cluster-level
login roles. Run the reviewed [`postgresql-roles.psql`](postgresql-roles.psql)
template as an authorized database administrator against the empty target
database, then restore as `aiops_migrator` with ownership and ACL restoration
disabled. This makes the migration identity own the restored objects and avoids
references to source-cluster role names in archive ACLs. Re-run the role
template after restore so its table-specific grants, break-glass function
grants, and revocations apply to the restored schema as well. If the managed
service cannot create roles or execute this template, use its approved
identity/grant workflow and verify the equivalent effective privileges before
connecting an application.

```bash
createdb aiops_restore_check
psql --set ON_ERROR_STOP=1 --dbname=aiops_restore_check \
  --file=examples/openobserve-aiops/postgresql-roles.psql
pg_restore --exit-on-error --single-transaction --no-owner --no-acl \
  --username=aiops_migrator \
  --dbname=aiops_restore_check /secure-backup-path/aiops-backup.dump
psql --set ON_ERROR_STOP=1 --dbname=aiops_restore_check \
  --file=examples/openobserve-aiops/postgresql-roles.psql
```

Before starting the target release's API image against this isolated database,
compare the restored `schema_migrations` version set with that release's
migration files. If it is behind, first confirm forward-migration compatibility
from the backup's source release to the target release, then run that release's
one-shot migration image against the isolated restore database as
`aiops_migrator` (`AIOPS_ENV=production`, `MIGRATION_DATABASE_ROLE` set to the
effective database identity, and `MIGRATION_DATABASE_URL` injected from the
approved secret manager). Verify the exact migration set and application
readiness on the isolated target. Never run recovery migrations directly
against the source backup or the live database as part of this rehearsal.
If the restored ledger contains a version absent from the target release, stop:
do not start the older application against that schema. Select a compatible
application release or a recovery point whose schema is compatible with the
target, then repeat the isolated restore checks.

When restore tooling runs under an identity other than the authorized DBA,
provision its authentication through the selected secret manager rather than
putting a password on the command line. Then verify migration versions,
representative incident/task/audit rows, constraints, indexes, object ownership,
effective API/worker/migrator privileges, application readiness, and an
end-to-end synthetic task before any owner-approved cutover. The repository
backup verifier source includes a second empty PostgreSQL cluster, role
provisioning, restore as `aiops_migrator` with `--no-owner --no-acl`, reapplication
of the role template, and assertions for table, sequence, and function ownership;
exact migration-version sets, resource-scope/timeline schema objects and effective
API/worker privileges. It also exercises `decrypt-postgres-backup.sh` for mode-
`0600` output, content equality, overwrite refusal, and authenticated failure for
a tampered archive. On 2026-09-29, the isolated verifier passed both plain and
encrypted restores through migration `0021`, including a fresh cluster,
least-privilege ownership/grants, tamper rejection, and overwrite protection.
This verifies the repository script against synthetic local data only; it does
not establish production key custody,
off-host retention, PITR, managed-service compatibility, or a production RPO/RTO.

The role verifier [`verify-postgresql-roles.sh`](verify-postgresql-roles.sh)
also passed in an isolated PostgreSQL 16.6 container. It confirmed runtime audit
append/read access, triage updates, and denial of audit mutation and schema
creation. Repeat those checks against the selected managed PostgreSQL service
in staging before production use.
