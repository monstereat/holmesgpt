# Production Readiness Plan

Status: preparation only. This document describes the deployment inputs, configuration boundary, and acceptance gates for a later production environment. It does not configure a production account, create production credentials, apply migrations, or deploy services. The current Docker Compose file is a local test stack and must not be promoted as a production manifest.

## Target service layout

Keep the responsibilities already exercised in the local test environment:

| Service | Production responsibility | Deployment boundary |
| --- | --- | --- |
| Holmes API | Read-only AI investigation and approved observability tool calls | Private service endpoint; outbound model access through an approved egress policy |
| Incident API | Authenticated alert intake, identity, RBAC, incidents, approvals, retrospectives, audit | Behind the organization's TLS ingress and identity layer |
| Incident worker | Durable task delivery and bounded Holmes retries | Private worker pool with bounded concurrency and queue monitoring |
| PostgreSQL | Incidents, tasks, outbox, users/identity mapping, approvals, audit, retrospectives | Managed or operator-run HA database with tested backup and restore |
| Redis/Celery | Work queue and worker coordination | Private network only; PostgreSQL outbox remains the recovery source |
| OpenObserve | Logs, traces, RUM, and alert source | Use an edition/configuration with native RBAC, or retain a separately reviewed least-privilege query proxy |
| OpenObserve policy proxy | Narrow Holmes access to approved read-only streams/searches when required | Separate private network and dedicated read-only credentials; never expose arbitrary query or write routes |
| OpenTelemetry Collector | Receive application traces/metrics and forward through an authenticated, bounded telemetry path | Private OTLP receiver; authenticated backend exporter; bounded memory/batches and export retries |
| Business services | Emit telemetry and own their operational actions | Each service remains the owner of its action API; no Docker socket or arbitrary shell runner |

The Compose network names are local implementation details, not a production network design. Production network policy must explicitly allow only alert delivery, incident-to-worker calls, worker-to-Holmes calls, Holmes-to-policy-proxy queries, service-to-telemetry writes, database/broker traffic, and approved model egress.

## Deployment configuration inputs

Do not fill in values until an owner has selected and approved each item. Store secret values in the target platform's secret manager and inject them at runtime; do not commit values or copy the local `/tmp` runtime file.

| Decision | Required production input | Local/test variable or current behavior |
| --- | --- | --- |
| Runtime platform | Container platform, region, ingress, private networking, TLS termination, DNS, autoscaling model | Docker Compose, loopback-bound ports |
| Identity | OIDC provider/issuer, group claim and group-to-role/resource mapping, session lifetime, break-glass owner | OIDC authorization-code + PKCE implementation; local operator/approver accounts remain test-only |
| Database | HA PostgreSQL endpoint, TLS/CA policy, separate app and migration identities, connection limits, backup/retention | `POSTGRES_PASSWORD`, single local PostgreSQL container; local Compose uses one identity |
| Queue | Redis-compatible managed broker, TLS/auth, persistence, visibility and retry policy | `REDIS_URL`, local Redis AOF |
| Holmes/model | Approved model/provider, `HOLMES_MODEL` (mapped to Holmes `MODEL`), secret reference for `DEEPSEEK_API_KEY` or replacement, request/cost limits, data-use approval | `HOLMES_API_KEY`, `DEEPSEEK_API_KEY`, `deepseek/deepseek-flash` |
| Telemetry | OpenObserve endpoint/org, least-privilege writer and read-only identities, tenant/stream allowlist | `ZO_ROOT_USER_EMAIL`, `ZO_ROOT_USER_PASSWORD`, proxy credentials |
| Alert intake | Public or private webhook route, signature rotation, source allowlist, rate limits | `ALERT_WEBHOOK_TOKEN` |
| Action owner | Service-specific action owner, resource allowlist, approval roles, idempotency, verification and rollback contract | `ORDER_ACTION_TOKEN`, demo-only `set-chaos-mode` action |
| Images and release | Registry, immutable image digests, SBOM/signing policy, promotion path, source revision | Local image builds from the working tree |
| Operations | Service SLOs, alert thresholds, retention, incident owner, on-call, backup RPO/RTO, metrics scrape identity, task admission capacity | `AIOPS_METRICS_TOKEN`, `AIOPS_MAX_PENDING_TASKS`, and owner-selected `AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS` are required outside local mode; capacity must come from an approved load test |

Before generating an executable platform-specific deployment bundle, record the selected runtime platform, identity provider, public/private hostname and TLS owner, secret manager, PostgreSQL/Redis service, OpenObserve edition and tenant model, model provider/data policy, and the first production action owner. These choices are currently unconfirmed, so no cloud-specific manifest or invented production values are included.

### PostgreSQL identity bootstrap

[`postgresql-roles.psql`](postgresql-roles.psql) is a PostgreSQL-role template for a fresh application database. A database administrator runs it before the first migration; it creates `aiops_migrator` and `aiops_runtime`, grants schema creation only to the migrator, and grants the runtime role DML plus sequence access on existing and future tables owned by the migrator. Migration `0006_audit_events_append_only` removes UPDATE, DELETE, and TRUNCATE on `audit_events` from the runtime role and PUBLIC; runtime can still SELECT and INSERT audit events. This protects against mutation through the application database identity, not database owners, superusers, or privileged operators. Configure role authentication through the selected secret manager or native database identity mechanism; the script contains no passwords. Feed the migrator identity only to `MIGRATION_DATABASE_URL` and the runtime identity only to `DATABASE_URL` for the API and worker. The local Compose stack still uses one test identity.

This bootstrap template does not transfer ownership of an already migrated schema. Existing databases need an owner-reviewed ownership/privilege adoption plan before using the migration role. Some managed PostgreSQL services restrict role creation or use IAM identities; use their supported role workflow and verify equivalent privileges. The template must be executed and permission-tested against a staging database before production use.

Re-run the isolated PostgreSQL 16 role and migration check with `bash examples/openobserve-aiops/verify-postgresql-roles.sh`. It starts a no-volume, no-port, no-network container and checks bootstrap reruns, runtime audit SELECT/INSERT, denied audit mutation, and denied schema creation. This verifies the repository role template; it does not replace testing the selected managed database's identity and privilege behavior.

## Production configuration gates

The eventual platform-specific bundle must satisfy all of the following before staging acceptance:

- Pin each application image by immutable digest; build and scan in CI, then promote the same artifact between environments. Do not use floating tags.
- Run application containers as non-root, with a read-only root filesystem, all unnecessary Linux capabilities dropped, privilege escalation disabled, and only bounded writable temporary storage. Local Compose applies these controls to Holmes (UID 10001), incident API/worker (`app`), order-service (`node`), and OTel Collector (UID 10001). Validate equivalent controls and writable paths on the selected platform.
- Terminate TLS at the approved ingress; expose only required API/UI routes. Keep worker, PostgreSQL, Redis, Holmes, proxy, and internal action-owner endpoints private.
- Use external secret references for database, session-signing, webhook, Holmes internal API, telemetry, proxy, model, and action-owner credentials. Define rotation and revocation procedures.
- Use OIDC authorization-code flow with PKCE S256, exact issuer and redirect-origin validation, one-time server-side state/nonce/verifier records, a short-lived HttpOnly session cookie, exact-origin checks for cookie-authenticated mutations, and explicit group-to-role/resource-scope mapping. Configure one trusted issuer per deployment. Test authorization on every mutating API, not only by hiding UI controls. Tenant isolation is not implemented by this example and must be supplied by the selected deployment and data model before multi-tenant use.
- Use separate runtime and migration database identities with least privilege. The migration command now requires `MIGRATION_DATABASE_URL` outside local mode and never falls back to the runtime `DATABASE_URL`; a PostgreSQL role template is provided, while local Compose explicitly uses its single test identity. The runtime role cannot update/delete/truncate audit history after migration 0006. Review migrations, run them as a gated release step, and test rollback/forward recovery against a restored staging copy.
- Disable demo-only OpenObserve SSRF bypasses. Use native tenant/RBAC controls where available; otherwise document the residual trust placed in the read-only proxy and its credentials.
- Keep the Holmes tool path read-only, stream-scoped, time-bounded, size-bounded, and audited. Explicitly approve which telemetry may leave the environment for model inference.
- Keep production remediation disabled by default. The existing demo `set-chaos-mode` action now requires an explicit `AIOPS_DEMO_ACTIONS_ENABLED=true`; the application default is disabled. Add a production action only after its owner provides a narrow API, independent approval policy, idempotency key, postcondition, rollback behavior, and audit evidence.
- Configure resource requests/limits, liveness/readiness probes, graceful shutdown, worker concurrency, queue backpressure, log/trace correlation, and alert routing from measured load. Outside local mode, `AIOPS_MAX_PENDING_TASKS` is required; PostgreSQL advisory locking makes admission atomic across API replicas, duplicate alerts remain idempotent at capacity, and new alerts receive `503` plus `Retry-After` when the pending queue is full. The capacity value must come from an owner-approved load test; `AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS` supplies the owner-selected maximum age for queued, running, or retrying tasks. Authenticated Prometheus metrics include task states, oldest pending age, retry attempts, configured queue capacity and pending-age objective, process-local API request histograms/in-flight gauge, and worker task duration p50/p95 from the rolling 24-hour sample at `/_internal/metrics`; keep that route private to the approved scraper. Holmes model-call duration, input/output tokens, and positive LiteLLM estimated cost are exported through the private Collector to OpenObserve. Prometheus-compatible rules cover incident API availability, pending-task age SLO, capacity saturation and failed tasks in [`monitoring/aiops-alerts.yml`](monitoring/aiops-alerts.yml). The deployment still needs an approved scraper, notification receiver/routing, and capacity-tested SLOs.
- [`backup-postgres.sh`](backup-postgres.sh) creates a mode-0600 custom-format PostgreSQL dump, validates it with `pg_restore --list`, and refuses to overwrite an existing output. [`verify-postgresql-backup.sh`](verify-postgresql-backup.sh) applied migrations 0001–0007, backed up a synthetic incident, then verified all migration records, the task-duration index, and incident data after isolated restore in a temporary PostgreSQL 16 container. This is a local recovery-mechanics check; configure production encryption, off-host retention, PITR/WAL archiving, restore ownership, and measured RPO/RTO against the selected provider before go-live.

## Staged acceptance plan

Each gate must have a named owner and retained evidence. Production data and actions are out of scope for staging acceptance unless separately approved.

| Gate | Acceptance evidence | Stop condition |
| --- | --- | --- |
| 1. Artifact and configuration | Image digests map to reviewed source; secret references resolve without printing values; no demo-only flags or public internal ports | Mutable image tags, missing secret owner, or `ZO_SKIP_SSRF_CHECKS` enabled |
| 2. Identity and authorization | OIDC login, tenant separation, viewer/operator/approver/admin matrix, self-approval rejection, API-level negative tests | Any cross-tenant read/write or requester self-approval |
| 3. Persistence and recovery | Reviewed migration on restored staging data; duplicate alert idempotency; worker/broker/database restart recovery; measured backup restore | Lost task/incident/audit data or untested recovery procedure |
| 4. Read-only investigation | Synthetic alert calls Holmes; tool traces show only approved read routes and streams; evidence links resolve; unsupported conclusions remain explicit | Any telemetry write, disallowed stream/query, secret leakage, or ungrounded release claim |
| 5. Workbench and approvals | Incident timeline, assignment/status, approval separation, reviewable retrospective, immutable audit events | UI-only authorization or untraceable state transition |
| 6. Action safety | Staging-only action owner; explicit allowlist; approved request; idempotent execution; verified postcondition; rollback test | Arbitrary shell/host access, missing approver, unverifiable state, or failed rollback |
| 7. Reliability and capacity | Load test establishes approved SLOs; worker duration, retries, dead-letter/failure state, API saturation, and provider limits are observable | SLO not agreed, retry storm, unbounded queue, or missing cost guard |
| 8. Evaluation and release | Reproducible 20-case live report against synthetic staging evidence; per-case evidence/release matches; reviewed failure cases; rollback rehearsal | Mock report presented as model accuracy, missing per-case artifacts, or unreviewed regression |
| 9. Go/no-go | Security, data/privacy, operations, application, and action owners sign the evidence bundle; rollback and incident contacts are reachable | Any open critical finding, missing owner, or absent rollback decision |

The 20-case suite is a regression signal, not by itself proof of production readiness or an accuracy guarantee. [`evals/review_scoring.py`](evals/review_scoring.py) creates a separate human scoring sheet and validates per-case scores, evidence references, reviewer notes, and source report identity; the machine-generated report remains `not_scored`. Require two independent reviewers, adjudicate disagreements, and establish thresholds before using diagnosis scores as a release gate.

The authenticated metrics endpoint reports API request counts, status-class labels, latency buckets, and in-flight requests per API process; these counters reset on process restart. Worker task p50/p95 duration is calculated from completed/failed tasks in the preceding 24 hours. Migration `0007_task_duration_metrics_index` adds a partial index to support that time-bounded query as task history grows. These timings include the Holmes call and are not provider-only latency or model cost. Metric labels use route templates, HTTP methods, and status classes; they omit user, incident, task, and alert identifiers.

## Current gaps and next decisions

- The DeepSeek investigation path and 20-case live evaluation have been exercised against synthetic local telemetry. The latest isolated run contains 20 cases and 40 seeded rows, with 20/20 case-evidence matches, 3/3 release-event matches, zero successful searches without exact run/case scope, zero detected cross-case fixture hits, and zero tool errors. The schema 1.3 report passed Draft 2020-12 validation. These are retrieval and query-scope checks, not a diagnosis score; root-cause diagnosis scoring remains `not_scored` and production accuracy is unestablished. Earlier reports used a matcher that missed SQL results whose projection omitted the filter columns, or did not enforce exact run/case scope; do not reuse their retrieval counts.
- The API now requires a positive `AIOPS_MAX_PENDING_TASKS` outside local mode and atomically rejects new unique alerts when queued/running/retrying work reaches that cap, while duplicate fingerprints still return the existing incident. The local Compose environment leaves the cap unset; select the production value only after load and worker-capacity testing. Overload returns `503` with `Retry-After: 30` so the alert source can retry.
- Production platform, identity provider, hostname/TLS ownership, managed data services, OpenObserve edition/tenant model, retention, SLO/RPO/RTO, and first action owner are pending decisions.
- `monitoring/aiops-alerts.yml` contains Prometheus-compatible rules for incident API scrape failure, owner-configured oldest pending-task age SLO breach, admission-capacity saturation, and persistent failed tasks. `AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS` is required outside local mode and exported as a metric; no age threshold is guessed. Rule syntax and firing behavior pass the pinned Prometheus `promtool` verifier. The target still needs a private metrics scrape, notification receiver/routing, and any platform-specific alert objects.
- The workbench now has provider-neutral OIDC authorization-code login with PKCE S256, exact issuer and redirect-origin checks, short-lived server-side login transactions, Authlib ID-token signature/nonce validation, stable `(issuer, subject)` user mapping, fail-closed group-to-role/resource-scope mapping, and a 15-minute HttpOnly browser cookie. OIDC is the default outside `AIOPS_ENV=local`; local password login is rejected outside local mode. A local signed-provider integration test verifies the ID-token/JWKS path. No organization identity provider has been configured or exercised, so IdP-specific claim shape, group membership, logout, key rotation, and availability remain to be validated in staging.
- Identity lifecycle is partially implemented: admins can list application identity mappings and disable a user's application access through the workbench/API; each request checks that the user remains active, and OIDC login does not reactivate an inactive account. Role and resource assignments remain owned by IdP group mappings. The admin cannot create users, change roles/scopes, or reactivate users. Session tokens are individually revoked on logout using a database table of token hashes; OIDC sessions last at most 15 minutes. The current sample supports one configured issuer and resource scopes, not tenant isolation or tenant-specific identity policy. Do not treat local RBAC tests as production identity governance.
- OpenObserve OSS does not provide the required native user/tenant RBAC; the local proxy narrows Holmes access but does not prove production tenant isolation.
- The repository Helm chart deploys the Holmes API only. It does not deploy the AIOps incident API, worker, workbench, PostgreSQL, Redis, or OpenObserve policy proxy. The AIOps example has only a local Docker Compose stack; the local machine currently has no configured Kubernetes context. No complete AIOps production deployment manifest or production credential configuration exists. Generate a platform-specific bundle only after the platform and deployment scope are selected.
- `AIOPS_TEST_USERS_JSON` is rejected unless `AIOPS_ENV=local`. Migration `0004_oidc_identities.sql` adds stable OIDC subject columns and one-time login transaction storage; `0005_revoked_sessions.sql` adds hashed-token revocation records; `0006_audit_events_append_only.sql` makes audit history append-only for the runtime role; `0007_task_duration_metrics_index.sql` indexes the rolling worker-duration query. API startup no longer applies migrations; the repository includes an explicit one-shot migration command, local Compose completion gate, and [`postgresql-roles.psql`](postgresql-roles.psql) least-privilege role template. The role template and migrations 0001–0007 were exercised on an isolated PostgreSQL 16 test database; the runtime role's DML/sequence access, denied schema creation, and denied audit mutation were verified. A target-platform migration Job/release gate and managed-database identity mapping are still not configured or staging-tested.
- Incident API now separates process liveness (`/healthz`) from PostgreSQL readiness (`/readyz`); the local Compose health probe uses readiness. Platform-specific probe timings and dependency behavior still need to be set against the selected runtime and measured load.
- The local incident worker health check now requires a Celery ping response through Redis. Queue state/age/retries, rolling 24-hour worker duration percentiles, and process-local API saturation/latency metrics are available to an authenticated scraper. Holmes emits model-call duration and input/output token OTel metrics with configured-model and LiteLLM-system dimensions; positive LiteLLM cost estimates are emitted as a cumulative metric. Local Compose routes them through a private Collector into OpenObserve. After rebuilding the Holmes image, a live DeepSeek request returned HTTP 200 and OpenObserve Prometheus API returned `holmesgpt_llm_cost_estimated=0.000243936` with `deepseek/deepseek-flash` and `litellm` labels. Cost remains an estimate, with zero/unknown values omitted and no invoice reconciliation. Alert rules, dead-letter alerting, and an agreed worker SLO/concurrency capacity measurement remain outstanding.
- The portable PostgreSQL backup script creates a mode-0600, non-overwriting custom-format archive and validates its catalog; an isolated PostgreSQL 16 test restored migrations 0001–0007, the task-duration index, and a synthetic incident into a separate database. Production still needs encrypted off-host retention, PITR/WAL policy, and measured restore timing.
- Investigation results retain redacted allowlisted tool calls; permanent tool-only failures now retain the same bounded evidence on the failed task, and a new attempt clears the prior attempt's result. Production still needs an owner-approved retention/access policy for investigation evidence and audit data.
- Any production data migration, production deployment, or production remediation requires separate explicit authorization and a production-specific review.

### OIDC configuration contract

Set these values through the selected platform's runtime configuration and secret manager. This example contains placeholders only:

```text
AIOPS_ENV=production
AIOPS_AUTH_MODE=oidc
AIOPS_PUBLIC_ORIGIN=https://aiops.example.com
SESSION_SIGNING_KEY=<secret, at least 32 bytes>
SESSION_COOKIE_SECURE=true
AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS=<owner-approved-seconds>
OIDC_ISSUER=https://identity.example.com/tenant
OIDC_METADATA_URL=https://identity.example.com/tenant/.well-known/openid-configuration
OIDC_CLIENT_ID=<registered-client-id>
OIDC_CLIENT_SECRET=<secret>
OIDC_REDIRECT_URI=https://aiops.example.com/auth/oidc/callback
OIDC_GROUP_MAPPINGS_JSON={"aiops-viewers":{"role":"viewer","resource_scopes":["order-service"]},"aiops-operators":{"role":"operator","resource_scopes":["order-service"]},"aiops-approvers":{"role":"approver","resource_scopes":["order-service"]}}
OIDC_GROUPS_CLAIM=groups
OIDC_SCOPES=openid profile email
```

The IdP must publish matching discovery metadata, support `S256`, issue a signed ID token with exact `iss`, `sub`, `aud`, `exp`, `iat`, and `nonce` claims, and supply the configured groups claim. Register the callback URI exactly. Use distinct operator and approver groups; mapping multiple roles in one identity is rejected. This example has no break-glass/admin bootstrap path or role assignment UI; establish and document an operator recovery procedure before relying on OIDC for production access.
