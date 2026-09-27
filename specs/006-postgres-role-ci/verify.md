# Verification: PostgreSQL role CI gate

Date: 2026-09-27.

- The AIOps workflow path filter now includes the role SQL template, local password SQL, bootstrap script, and isolated role verifier.
- The workflow runs both `verify-postgresql-roles.sh` and `verify-postgresql-local-bootstrap.sh` as separate steps.
- Local run passed: PostgreSQL 16.6, 8 migrations applied; runtime business access and audit append allowed; audit/ledger mutation and schema creation denied.
- Local bootstrap run passed twice against a fresh PostgreSQL 16.6 container on `--network none`; it verified idempotency, distinct runtime/migrator password authentication, runtime DML to migrator-owned objects, schema-creation denial, and identical-password rejection. Temporary role credentials are generated in-process and are not written to logs.
- Ruby Psych parsed `.github/workflows/develop-me-aiops.yml`; `git diff --check` passed.
- The hosted GitHub workflow and repository branch protection were not exercised or changed.
