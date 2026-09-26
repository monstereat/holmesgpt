# Resume claims: HolmesGPT/OpenObserve AIOps

Use the wording below only for the work and evidence currently present in this repository. The system has been exercised as a local Docker test environment; it has **not** been deployed to a production environment.

## Resume-ready project entry

**HolmesGPT/OpenObserve AIOps incident response platform (local test release)**

- Built a Python/FastAPI incident API and Celery worker around HolmesGPT, with PostgreSQL-backed incidents, idempotent alert intake, transactional outbox delivery, bounded retries, and restart recovery.
- Implemented an incident workbench with API-enforced viewer/operator/approver/admin permissions, separate approval and execution roles, audited state transitions, and an owner-side allowlisted action API with idempotency, postcondition checks, and rollback.
- Integrated Holmes investigations with DeepSeek and OpenObserve through a read-only query proxy that restricts routes, streams, query windows, result sizes, and timeouts; retained redacted tool evidence with each investigation.
- Routed Holmes OpenTelemetry traces, model-call duration, input/output token counts, and positive LiteLLM per-call cost estimates through a private Collector to OpenObserve. Verified a live DeepSeek request and queried the estimated-cost metric with model/provider labels in OpenObserve; estimates are not invoice reconciliation.
- Added provider-neutral OIDC/PKCE identity foundations, persisted login transactions, session revocation, account disablement, and an authenticated low-cardinality Prometheus endpoint for queue state, pending age, retries, admission capacity, API request latency/saturation, and rolling worker duration percentiles.
- Added PostgreSQL-serialized webhook admission control across API replicas; production requires an operator-selected pending-task cap, duplicate alerts remain idempotent at saturation, and overload responses include `Retry-After` for source retries.
- Added a PostgreSQL least-privilege role bootstrap template separating application DML from schema migration privileges; exercised migrations 0001–0007 on an isolated PostgreSQL 16 database and verified the runtime role could not create schema objects or update/delete/truncate audit history.
- Added a PostgreSQL custom-format backup script with overwrite protection and archive validation; verified an isolated restore into a separate PostgreSQL 16 test database using synthetic data. Production off-host retention, encryption, and PITR remain unconfigured.
- Ran the isolated Docker Compose incident-service suite (**87 passed**) and a live 20-case synthetic retrieval run: 20/20 case-evidence matches, 3/3 release-event matches, zero successful searches without exact run/case scope, zero detected cross-case fixture hits, and zero tool errors. Added a separate reviewer-attributed rubric workflow with per-case evidence references; root-cause diagnosis remains `not_scored` until independent reviewers complete and adjudicate a score sheet.

## Interview framing

Describe this as a locally deployed, end-to-end AIOps prototype with production-oriented controls. Explain the split: Python owns Holmes investigation and incident processing; the NestJS order service is only a telemetry-producing/action-owner sample. The local Compose environment is a test deployment, not a production release.

## Claims not yet supported

Do not claim production launch, multi-tenant isolation, high availability, measured SLO/RPO/RTO, a diagnosis accuracy percentage, or automatic production remediation. Production platform and identity provider are not selected; target-specific database roles, network policy, scrape alerts, backups/restores, load/capacity exercises, independent human scoring, and production go/no-go evidence remain open.

## Evidence

- [Production readiness gates](PRODUCTION-READINESS.md)
- [Project progress and validation record](../../docs/develop-me-roadmap.md)
- Latest local incident-service result: 87 passed, 1 upstream deprecation warning (2026-09-26), including a real PostgreSQL concurrent admission-cap check.
- Latest live synthetic report: `/tmp/holmes-aiops-live-report-final.json`, run `a6585b72bc124a579bdca7833b2ae5a6` (20/20 diagnoses and verified evidence, zero tool errors, schema 1.3 validated; diagnosis scoring remains `not_scored`). The matching blank human review sheet is `/tmp/holmes-aiops-review.json`; both files are local ephemeral artifacts and are not committed.
