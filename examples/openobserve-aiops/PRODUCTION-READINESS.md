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
| Business services | Emit telemetry and own their operational actions | Each service remains the owner of its action API; no Docker socket or arbitrary shell runner |

The Compose network names are local implementation details, not a production network design. Production network policy must explicitly allow only alert delivery, incident-to-worker calls, worker-to-Holmes calls, Holmes-to-policy-proxy queries, service-to-telemetry writes, database/broker traffic, and approved model egress.

## Deployment configuration inputs

Do not fill in values until an owner has selected and approved each item. Store secret values in the target platform's secret manager and inject them at runtime; do not commit values or copy the local `/tmp` runtime file.

| Decision | Required production input | Local/test variable or current behavior |
| --- | --- | --- |
| Runtime platform | Container platform, region, ingress, private networking, TLS termination, DNS, autoscaling model | Docker Compose, loopback-bound ports |
| Identity | OIDC/SAML provider, tenant mapping, group-to-role mapping, session lifetime, break-glass owner | `AIOPS_TEST_USERS_JSON`, local operator/approver accounts |
| Database | HA PostgreSQL endpoint, TLS/CA policy, app and migration identities, connection limits, backup/retention | `POSTGRES_PASSWORD`, single local PostgreSQL container |
| Queue | Redis-compatible managed broker, TLS/auth, persistence, visibility and retry policy | `REDIS_URL`, local Redis AOF |
| Holmes/model | Approved model/provider, `HOLMES_MODEL` (mapped to Holmes `MODEL`), secret reference for `DEEPSEEK_API_KEY` or replacement, request/cost limits, data-use approval | `HOLMES_API_KEY`, `DEEPSEEK_API_KEY`, `deepseek/deepseek-flash` |
| Telemetry | OpenObserve endpoint/org, least-privilege writer and read-only identities, tenant/stream allowlist | `ZO_ROOT_USER_EMAIL`, `ZO_ROOT_USER_PASSWORD`, proxy credentials |
| Alert intake | Public or private webhook route, signature rotation, source allowlist, rate limits | `ALERT_WEBHOOK_TOKEN` |
| Action owner | Service-specific action owner, resource allowlist, approval roles, idempotency, verification and rollback contract | `ORDER_ACTION_TOKEN`, demo-only `set-chaos-mode` action |
| Images and release | Registry, immutable image digests, SBOM/signing policy, promotion path, source revision | Local image builds from the working tree |
| Operations | Service SLOs, alert thresholds, retention, incident owner, on-call, backup RPO/RTO | Not defined for production |

Before generating an executable platform-specific deployment bundle, record the selected runtime platform, identity provider, public/private hostname and TLS owner, secret manager, PostgreSQL/Redis service, OpenObserve edition and tenant model, model provider/data policy, and the first production action owner. These choices are currently unconfirmed, so no cloud-specific manifest or invented production values are included.

## Production configuration gates

The eventual platform-specific bundle must satisfy all of the following before staging acceptance:

- Pin each application image by immutable digest; build and scan in CI, then promote the same artifact between environments. Do not use floating tags.
- Terminate TLS at the approved ingress; expose only required API/UI routes. Keep worker, PostgreSQL, Redis, Holmes, proxy, and internal action-owner endpoints private.
- Use external secret references for database, session-signing, webhook, Holmes internal API, telemetry, proxy, model, and action-owner credentials. Define rotation and revocation procedures.
- Replace local test accounts with federated identities and enforce tenant, role, and resource scopes on the server. Test authorization on every mutating API, not only by hiding UI controls.
- Use separate runtime and migration database identities with least privilege. Review migrations, run them as a gated release step, and test rollback/forward recovery against a restored staging copy.
- Disable demo-only OpenObserve SSRF bypasses. Use native tenant/RBAC controls where available; otherwise document the residual trust placed in the read-only proxy and its credentials.
- Keep the Holmes tool path read-only, stream-scoped, time-bounded, size-bounded, and audited. Explicitly approve which telemetry may leave the environment for model inference.
- Leave production remediation disabled by default. Add an action only after its owner provides a narrow API, independent approval policy, idempotency key, postcondition, rollback behavior, and audit evidence.
- Configure resource requests/limits, liveness/readiness probes, graceful shutdown, worker concurrency, queue backpressure, log/trace correlation, and alerting from measured load. Set SLO values from an owner-approved capacity exercise rather than guessing.
- Configure PostgreSQL backup/PITR and retention, Redis recovery expectations, OpenObserve retention, restore ownership, and a measured restore drill. Do not treat a successful backup command as a recovery objective.

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
| 7. Reliability and capacity | Load test establishes approved SLOs; task latency, retries, dead-letter/failure state, API saturation, and provider limits are observable | SLO not agreed, retry storm, unbounded queue, or missing cost guard |
| 8. Evaluation and release | Reproducible 20-case live report against synthetic staging evidence; per-case evidence/release matches; reviewed failure cases; rollback rehearsal | Mock report presented as model accuracy, missing per-case artifacts, or unreviewed regression |
| 9. Go/no-go | Security, data/privacy, operations, application, and action owners sign the evidence bundle; rollback and incident contacts are reachable | Any open critical finding, missing owner, or absent rollback decision |

The 20-case suite is a regression signal, not by itself proof of production readiness or an accuracy guarantee. Establish evaluation thresholds and independent review criteria before using it as a release gate.

## Current gaps and next decisions

- Local verification is complete for the DeepSeek investigation path and the 20-case live evaluation: 20/20 requests returned diagnoses, 14/20 cases matched current-run evidence, all 3/3 release-event cases matched, and there were no API call errors. Root-cause diagnosis scoring remains `not_scored`; this does not establish production accuracy.
- Production platform, identity provider, hostname/TLS ownership, managed data services, OpenObserve edition/tenant model, retention, SLO/RPO/RTO, and first action owner are pending decisions.
- OpenObserve OSS does not provide the required native user/tenant RBAC; the local proxy narrows Holmes access but does not prove production tenant isolation.
- The repository Helm chart deploys the Holmes API only. It does not deploy the AIOps incident API, worker, workbench, PostgreSQL, Redis, or OpenObserve policy proxy. The AIOps example has only a local Docker Compose stack; the local machine currently has no configured Kubernetes context. No complete AIOps production deployment manifest or production credential configuration exists. Generate a platform-specific bundle only after the platform and deployment scope are selected.
- The AIOps workbench currently seeds local test identities from `AIOPS_TEST_USERS_JSON`. API startup no longer applies migrations; the repository now includes an explicit one-shot migration command and a local Compose completion gate. A production migration Job/release gate with a separate least-privilege migration identity is still not configured. Production identity federation is not implemented.
- Any production data migration, production deployment, or production remediation requires separate explicit authorization and a production-specific review.
