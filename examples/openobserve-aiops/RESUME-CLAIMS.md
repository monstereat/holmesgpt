# Resume claims: HolmesGPT/OpenObserve AIOps

Use the wording below only for the work and evidence currently present in this repository. The system has been exercised as a local Docker test environment; it has **not** been deployed to a production environment.

## Resume-ready project entry

**HolmesGPT/OpenObserve AIOps incident response platform (local test release)**

- Built a Python/FastAPI incident API and Celery worker around HolmesGPT, with PostgreSQL-backed incidents, idempotent alert intake, transactional outbox delivery, bounded retries, and restart recovery.
- Implemented an incident workbench with API-enforced viewer/operator/approver/admin permissions, separate approval and execution roles, audited state transitions, and an owner-side allowlisted action API with idempotency, postcondition checks, and rollback.
- Integrated Holmes investigations with DeepSeek and OpenObserve through a read-only query proxy that restricts routes, streams, query windows, result sizes, and timeouts; retained redacted tool evidence with each investigation.
- Routed Holmes OpenTelemetry traces, model-call duration, input/output token counts, and positive LiteLLM per-call cost estimates through a private Collector to OpenObserve. Verified a live DeepSeek request and queried the estimated-cost metric with model/provider labels in OpenObserve; estimates are not invoice reconciliation.
- Added provider-neutral OIDC/PKCE identity foundations, persisted login transactions, session revocation, account disablement, and an authenticated low-cardinality Prometheus endpoint for queue state, pending age, retries, admission capacity, API request latency/saturation, and rolling worker duration percentiles.
- Added Prometheus-compatible operational alerts for API scrape failure, queue-age SLO breach, queue-capacity saturation, and failed tasks; production must supply the queue-age objective, private scrape and notification routing.
- Added PostgreSQL-serialized webhook admission control across API replicas; production requires an operator-selected pending-task cap, duplicate alerts remain idempotent at saturation, and overload responses include `Retry-After` for source retries.
- Added a PostgreSQL least-privilege role bootstrap template separating application DML from schema migration privileges; exercised migrations 0001–0007 on an isolated PostgreSQL 16 database and verified the runtime role could not create schema objects or update/delete/truncate audit history.
- Added fail-closed TLS validation for production PostgreSQL and Celery Redis connections: PostgreSQL requires `sslmode=verify-full`; Redis requires authenticated `rediss://`, a verified certificate chain, and hostname verification. Local URL/config tests and runtime image builds pass; managed-service TLS handshakes have not been exercised.
- Added a PostgreSQL custom-format backup script with overwrite protection and archive validation; verified migrations 0001–0007, indexes, and a synthetic incident restore into a separate PostgreSQL 16 test database. Production off-host retention, encryption, and PITR remain unconfigured.
- Ran the isolated Docker Compose incident-service suite (**96 passed, 1 warning**) and a live 20-case synthetic retrieval run: 20/20 case-evidence matches, 3/3 release-event matches, zero successful searches without exact run/case scope, zero detected cross-case fixture hits, and zero tool errors. Added a separate reviewer-attributed rubric workflow with per-case evidence references; root-cause diagnosis remains `not_scored` until independent reviewers complete and adjudicate a score sheet.

## Interview framing

Describe this as a locally deployed, end-to-end AIOps prototype with production-oriented controls. Explain the split: Python owns Holmes investigation and incident processing; the NestJS order service is only a telemetry-producing/action-owner sample. The local Compose environment is a test deployment, not a production release.

## Claims not yet supported

Do not claim production launch, multi-tenant isolation, high availability, measured SLO/RPO/RTO, a diagnosis accuracy percentage, or automatic production remediation. Production platform and identity provider are not selected; target-specific database roles, network policy, scrape alerts, backups/restores, load/capacity exercises, independent human scoring, and production go/no-go evidence remain open.

## Evidence

- Reboot recovery check: with no queued task or pending outbox row, restarted the incident API and Celery worker; both returned healthy, API liveness/readiness returned 200, and the failure task, verified evidence, OFF approval, and audit timeline remained persisted in PostgreSQL. Database, Redis, and Holmes restart behavior was not part of this specific check.
- [Production readiness gates](PRODUCTION-READINESS.md)
- [Project progress and validation record](../../docs/develop-me-roadmap.md)
- Latest local incident-service result: 96 passed, 1 upstream deprecation warning (2026-09-26), including PostgreSQL concurrency/admission checks and production database/broker URL validation. Compose configuration check and API/worker/migration image builds passed. The current worker and incident API are healthy; because an orphaned legacy `alert-trigger` container still owns 8081, the API is loopback-bound on 8082 via `AIOPS_API_HOST_PORT`. Liveness/readiness/workbench/auth-mode routes return 200; unauthenticated incident/user APIs return 401. A fresh synthetic runtime smoke produced an order (201), flushed its trace (201), accepted a webhook (202), and completed a worker investigation with 2 saved Holmes tool evidence items, including the exact alert Trace ID; `evidence_status=verified`. This exercised evidence retrieval on a success-log alert, not RCA accuracy or the approval/action path.
- Re-ran a local 500-alert recovery exercise: an independently approved test action enabled the demo failure mode; an order returned 500, its same-Trace alert was accepted and investigated, and the failure incident retained 2 verified evidence records. The failure incident then recorded an operator OFF request, approver decision by a different user, owner postcondition verification, and `task.completed`; self-approval returned 403 and the final order-service state was OFF. The ON stimulus approval is recorded on the separate setup incident. This is local demo evidence, not production remediation or a diagnosis-accuracy score.
- Latest live synthetic report: `/tmp/holmes-aiops-live-report-final.json`, run `a6585b72bc124a579bdca7833b2ae5a6` (20/20 diagnoses and verified evidence, zero tool errors, schema 1.3 validated; diagnosis scoring remains `not_scored`). The matching blank human review sheet is `/tmp/holmes-aiops-review.json`; both files are local ephemeral artifacts and are not committed.
