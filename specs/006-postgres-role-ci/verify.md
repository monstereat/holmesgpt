# Verification: PostgreSQL role CI gate

Date: 2026-09-27.

- The AIOps workflow path filter now includes the role SQL template, local password SQL, bootstrap script, and isolated role verifier.
- The workflow runs `bash examples/openobserve-aiops/verify-postgresql-roles.sh` as a separate step.
- Local run passed: PostgreSQL 16.6, 8 migrations applied; runtime business access and audit append allowed; audit/ledger mutation and schema creation denied.
- Ruby Psych parsed `.github/workflows/develop-me-aiops.yml`; `git diff --check` passed.
- The hosted GitHub workflow and repository branch protection were not exercised or changed.
