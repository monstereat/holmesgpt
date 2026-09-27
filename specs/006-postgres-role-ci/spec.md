# Spec: run PostgreSQL role verification in AIOps CI

## Goal

Ensure changes to PostgreSQL role templates, local role bootstrap files, or their isolated verifiers run the PostgreSQL 16 role and migration permission checks in the repository's AIOps workflow.

## Acceptance criteria

- AC-01: relevant role SQL/scripts are included in the workflow path filter.
- AC-02: the workflow invokes the isolated `verify-postgresql-roles.sh` check.
- AC-03: the workflow also tests local password bootstrap idempotency, role authentication, and runtime/migrator permission separation in an isolated no-network database.
- AC-04: local verifiers and workflow YAML syntax validation pass.

## Scope

Only `.github/workflows/develop-me-aiops.yml`, `examples/openobserve-aiops/bootstrap-postgresql-roles.sh`, `examples/openobserve-aiops/verify-postgresql-local-bootstrap.sh`, `ROADMAP.md`, and this spec/evidence directory. This adds local verification and CI behavior only; it does not configure a remote repository or change branch protection.
