# Tasks

## T001: wire isolated PostgreSQL role verifier into AIOps CI

- Allowed paths: `.github/workflows/develop-me-aiops.yml`, `ROADMAP.md`, `specs/006-postgres-role-ci/**`.
- Verification: `bash examples/openobserve-aiops/verify-postgresql-roles.sh`; parse workflow YAML; `git diff --check`.
- Status: done
