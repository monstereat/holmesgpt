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
  processing fields, and appending worker audit events. Neither can create
  schema objects, alter the migration ledger, or change/delete audit history.
- A one-shot migration job uses the separate migration identity. It must finish
  successfully before application replicas start. The migration runner takes a
  PostgreSQL advisory transaction lock and records each applied migration.
- The current migrations are numbered `0001` through `0010`. They run in
  transactions. There are no automatic down migrations: do not delete migration
  records or manually reverse DDL to make an older binary start.

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

On 2026-09-28 migration `0010_worker_database_privileges` was applied to the persistent local test database. The API uses `aiops_runtime`, Celery uses `aiops_worker`, and migrations use `aiops_migrator`; a direct privilege check confirmed the worker can perform task/outbox updates and audit inserts while user reads, incident writes, and audit mutation are denied. After loading the documented mode-0600 local DeepSeek key file, Holmes and the Celery worker became healthy. A synthetic webhook then produced one incident/task; the worker completed the first attempt, persisted the matching Trace ID and OpenObserve query tool in the task result, and appended `task.completed` to audit history. Counts are 12 incidents, 12 tasks, 54 audit rows, and 12 outbox rows. This is still test-only evidence.

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
   `AIOPS_ENV=production` and `MIGRATION_DATABASE_URL` set to the dedicated
   migration identity. Do not set `DATABASE_URL` as a migration fallback. Require
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

## Backup and restore evidence

For a database session configured through libpq environment or service-file
credentials, create a new archive path with:

```bash
bash examples/openobserve-aiops/backup-postgres.sh /secure-backup-path/aiops-$(date -u +%Y%m%dT%H%M%SZ).dump
```

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
approved secret manager, decrypt to protected scratch storage, then run
`pg_restore --list` before restoring into a separate database:

```bash
openssl cms -decrypt -binary -inform DER \
  -in /secure-backup/aiops-backup.cms.der \
  -recip /run/secrets/backup-recipient.crt \
  -inkey /run/secrets/backup-recipient.key \
  -out /secure-ephemeral/aiops-restore.dump
pg_restore --list /secure-ephemeral/aiops-restore.dump
```

OpenSSL CMS supports AES-GCM as an authenticated-encryption mode; confirm the
selected platform's OpenSSL build and cryptographic policy before adopting this
format ([OpenSSL CMS command documentation](https://docs.openssl.org/3.4/man1/openssl-cms/)).

Restore only to a new isolated database or instance first:

```bash
createdb aiops_restore_check
pg_restore --exit-on-error --single-transaction --no-owner \
  --dbname=aiops_restore_check /secure-backup-path/aiops-backup.dump
```

Then verify migration versions, representative incident/task/audit rows,
constraints, indexes, application readiness, and an end-to-end synthetic task
before any owner-approved cutover. The repository verifier
[`verify-postgresql-backup.sh`](verify-postgresql-backup.sh) passed on an
isolated PostgreSQL 16.6 container with no network or persistent volume. It
restored migrations `0001`–`0009`, the task duration index, incident triage and
user session-generation/reactivation columns, and a synthetic incident; it checked mode `0600`, archive validation,
and overwrite refusal. The same verifier creates an ephemeral recipient
certificate, encrypts the archive, decrypts it, and restores it into a second
isolated database. It also checks mode `0600`, overwrite refusal, and rejection
after an encrypted artifact is tampered with. This verifies local encryption
and restore mechanics, but does not establish production key custody, off-host
retention, PITR, managed-service compatibility, or a production RPO/RTO.

The role verifier [`verify-postgresql-roles.sh`](verify-postgresql-roles.sh)
also passed in an isolated PostgreSQL 16.6 container. It confirmed runtime audit
append/read access, triage updates, and denial of audit mutation and schema
creation. Repeat those checks against the selected managed PostgreSQL service
in staging before production use.
