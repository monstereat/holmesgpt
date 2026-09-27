# Spec: run PostgreSQL role verification in AIOps CI

## Goal

Ensure changes to PostgreSQL role templates, local role bootstrap files, or their isolated verifier run the PostgreSQL 16 role and migration permission check in the repository's AIOps workflow.

## Acceptance criteria

- AC-01: relevant role SQL/scripts are included in the workflow path filter.
- AC-02: the workflow invokes the isolated `verify-postgresql-roles.sh` check.
- AC-03: local PostgreSQL role verification and workflow YAML syntax validation pass.

## Scope

Only `.github/workflows/develop-me-aiops.yml`, `ROADMAP.md`, and this spec/evidence directory. This adds CI behavior only; it does not configure a remote repository or change branch protection.
