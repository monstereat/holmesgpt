# Tasks

## T001: synchronize local/production database identity statements

- Allowed paths: `examples/openobserve-aiops/PRODUCTION-READINESS.md`, `ROADMAP.md`, `specs/005-postgres-readiness-doc-sync/**`.
- Verification: `rg -n 'local Compose uses one identity|local Compose stack still uses one test identity' examples/openobserve-aiops/PRODUCTION-READINESS.md` must find no matches; `git diff --check` must pass.
- Status: done
