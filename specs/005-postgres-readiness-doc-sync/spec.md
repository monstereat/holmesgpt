# Spec: sync PostgreSQL production-readiness documentation

## Goal

Make the production-readiness guide accurately describe the current local Compose PostgreSQL identities while keeping managed-production requirements explicitly unverified.

## Acceptance criteria

- AC-01: no claim remains that local Compose uses one PostgreSQL identity.
- AC-02: document the local bootstrap/runtime/migrator split and local-only ownership adoption without presenting it as a production migration procedure.
- AC-03: Roadmap and documentation diff checks pass; no credentials are added to repository files.

## Scope

Only `examples/openobserve-aiops/PRODUCTION-READINESS.md`, `ROADMAP.md`, and this spec/evidence directory. No production service or database is involved.
