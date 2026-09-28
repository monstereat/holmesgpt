# HolmesGPT OpenObserve AIOps demo

This project keeps HolmesGPT's existing Python/FastAPI investigation API (`server.py`) and OpenObserve toolset. A separate Python incident service owns authenticated alert intake, PostgreSQL state, Redis/Celery dispatch, approvals, audit history and the workbench. The NestJS order service emits telemetry and owns its narrowly scoped local test action. It does not expose a Docker socket or arbitrary shell execution.

## Local Docker test stack

Run from this directory. Compose binds browser/API ports to `127.0.0.1` and keeps PostgreSQL and Redis on the private Compose network. Do not use this demo configuration as a production deployment.

Initialize the local Compose credentials once on a clean install. The generated file is stored outside the repository at `~/.config/holmesgpt-aiops/local-compose.env` with mode `0600`; it contains the database bootstrap passwords, service tokens, signing key and local demo identities. The initializer refuses to overwrite an existing file or generate new passwords when this Compose project already has containers or volumes. Keep this file when reusing existing volumes: changing bootstrap passwords does not rotate credentials already stored in PostgreSQL or OpenObserve. If you already have this stack and its volumes, restore the matching file instead of running the initializer.

Live investigation also requires the private DeepSeek key file at `~/.config/holmesgpt-aiops/deepseek.env` with mode `0600`, containing `export DEEPSEEK_API_KEY='paste-key-here'`. The local Compose wrapper loads both files without adding either to this repository. If the key is missing, Holmes is marked unhealthy and the incident worker waits instead of accepting investigation tasks.

```bash
bash ./init-local-runtime-env.sh   # only for a new, empty local Compose project
./compose-local.sh up -d --build
```

The incident service hashes account passwords before storing them. In local mode only, the workbench fills the operator login from `AIOPS_TEST_USERS_JSON` through a `Cache-Control: no-store` endpoint; OIDC/production mode does not expose the endpoint. Compose explicitly sets `AIOPS_ENV=local`; the service rejects `AIOPS_TEST_USERS_JSON` in every other environment. No project `.env` file is needed. `HOLMES_API_KEY` protects the internal Holmes API and is shared only with the incident worker. The stack defaults to `deepseek/deepseek-flash`; a missing `DEEPSEEK_API_KEY` makes Holmes unhealthy and holds the incident worker until the key is loaded. The local Holmes readiness check confirms the key is present and the model is configured; it does not validate the remote provider credential.

```bash
./compose-local.sh up -d --force-recreate holmes-api
```

DeepSeek's API key is passed only to the Holmes container; do not commit or log it.

The Holmes container reads a read-only config file. By default it mounts the committed template `holmes-config/config.yaml.example`; to use a local config copy, set `HOLMES_CONFIG_FILE` to its absolute path before `docker compose up`. Holmes and OpenObserve share no Docker network, so Holmes can reach OpenObserve only through `openobserve-proxy` for queries and `otel-collector` for telemetry export. The query proxy requires separate client credentials, exposes only the log-stream list and search APIs, filters streams to `app_logs` and `frontend_errors`, validates a single ClickHouse SQL SELECT AST, and caps query windows, timeouts, rows and response bytes. It also requires a per-stream `OPENOBSERVE_ALLOWED_FIELDS_JSON` policy, expands wildcard projections to those fields, rejects references to other fields, database/catalog qualification, SQL functions and nested field access, filters stream schemas, removes unlisted fields from returned records, and drops object-valued/nested fields. This does not inspect free-text values for embedded secrets or personal data, and it does not add native RBAC to OpenObserve OSS. The collector accepts OTLP/HTTP only on its private Compose network and forwards traces/metrics to OpenObserve using credentials that are not passed to Holmes. These controls do not protect against compromise of the proxy, collector, Docker host, or local OpenObserve administrator. Treat this instance as test-only. OpenObserve `/healthz` is available on the host; its UI remains loopback-bound.

The model is selected through Holmes' `MODEL` environment variable, and LiteLLM reads the provider key from `DEEPSEEK_API_KEY`; the config template contains toolset settings and caps each investigation at 12 model steps.

The local OpenObserve container permits up to 24-hour-old log ingestion so the live synthetic evaluation can place cases farther apart than the proxy's one-hour query limit. OpenObserve defaults to five hours. This setting is only for the local test Compose stack and must not be copied into production without an explicit data-ingestion policy review.

After startup:

- Order-service demo: [http://localhost:8080](http://localhost:8080)
- Holmes incident workbench: [http://localhost:8081](http://localhost:8081) (local test logins: `admin`, `operator`, `approver`)
- OpenObserve: [http://localhost:5080](http://localhost:5080)
- Incident API liveness: `http://localhost:8081/healthz`; readiness: `http://localhost:8081/readyz`
- Holmes API liveness: `http://localhost:5050/healthz`; readiness: `http://localhost:5050/readyz`

The account seeded as `operator` can create/retry tasks and request the fixed `set-chaos-mode` test action; Compose explicitly enables it with `AIOPS_DEMO_ACTIONS_ENABLED=true`. The action is disabled by default in every other environment, including for already approved requests. The separate `approver` can approve or reject it and edit/review incident retrospectives. The requester cannot approve their own request. All three accounts are local demo identities, not a production identity provider.

The incident API's host port defaults to `8081`; if another local container owns that port, update `AIOPS_API_HOST_PORT` in the private `~/.config/holmesgpt-aiops/local-compose.env` file before starting the stack. The service remains on port `8081` inside the private Compose network, so the OpenObserve webhook destination does not change.

The local `admin` can inspect application identity mappings, disable workbench access, and approve a pending restore request. A disabled user must complete a valid OIDC sign-in first; the callback refreshes IdP group mappings, creates one pending request, returns `403`, and does not issue a session. Admin restore does not change roles/scopes and writes an audit event with actor and target. Disabling increments a per-user session generation, so old sessions do not revive after restore; users must sign in again. During mixed-version deployments, wait until every old API instance is stopped before restoring accounts. This does not change the user's identity-provider account. No local admin credential is committed.

Logout revokes the current OIDC cookie or local bearer session server-side by storing only its token hash and expiry. The API checks revocation on authenticated requests. If logout cannot reach PostgreSQL, the workbench keeps the current session visible and reports the error instead of silently treating the token as revoked.

Check service state and liveness:

```bash
./compose-local.sh ps
curl -fsS http://127.0.0.1:5080/healthz
curl -fsS http://127.0.0.1:5050/healthz
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS http://127.0.0.1:8081/readyz
curl -fsS http://127.0.0.1:8080/
```

The incident API `/healthz` is liveness; `/readyz` checks PostgreSQL connectivity. The incident worker health check requires a Celery ping response through Redis. Holmes `/healthz` is liveness; `/readyz` reflects model readiness and can fail when no model is configured. OpenObserve's image has no shell-based health probe, so its endpoint is checked from the host.

The Holmes API runs as UID 10001; incident API/worker images run as the non-root `app` user; the order-service uses the built-in non-root `node` user; and the OTel Collector image runs as UID 10001. Local Compose gives these services a read-only root filesystem, drops Linux capabilities, disables privilege escalation, and provides bounded temporary storage. Holmes config and cache paths live in its 64 MiB `/tmp` tmpfs; the config file itself is mounted read-only. Before each model request, the worker checks Holmes `/api/info?detail=full` for an enabled OpenObserve toolset; temporary toolset unavailability uses the existing bounded task retry policy. These controls are for the local Docker runtime; validate the equivalent policy in the selected production platform.

Holmes exports OpenTelemetry traces and metrics through the local Collector using OTLP/HTTP protobuf. The Collector forwards both signals to OpenObserve's `/api/default/v1/traces` and `/api/default/v1/metrics` ingestion paths with a Basic Auth client authenticator. The Holmes API's OTel metrics include model-call duration and input/output token usage, labeled with the configured model and LiteLLM system; positive per-call cost estimates reported by LiteLLM are emitted as a cumulative metric. Unknown or zero estimates are omitted, and positive estimates are not provider invoice reconciliation. The trace also retains the per-investigation cost attribute. The local path has been smoke-tested with a DeepSeek request and OpenObserve queries. Do not expose the Collector receiver outside the private Compose networks.

For later production deployment, [`otel-collector-production.yaml`](otel-collector-production.yaml) is a separate configuration template. It requires TLS certificate/key and bearer-token files for incoming OTLP, file-mounted write-only OpenObserve credentials, an HTTPS upstream endpoint, and owner-selected resource/queue limits. It enables a bounded file-backed exporter queue; mount its data directory on encrypted persistent storage writable by the Collector UID and apply the target's access/retention controls because queued records contain telemetry. The upstream File Storage extension is marked beta and needs explicit production-owner acceptance or replacement. Validate the component configuration with `bash examples/openobserve-aiops/verify-production-otel-config.sh`; the check uses temporary credentials and does not contact OpenObserve. It does not replace target-platform network, certificate, secret rotation, or capacity acceptance.

The incident API exposes Prometheus text metrics at `/_internal/metrics` only when `AIOPS_METRICS_TOKEN` is set. The scraper must send `Authorization: Bearer <token>`; keep this endpoint private and store the token in the target's secret manager. It reports task counts by state, oldest pending task age, persisted retry attempts, pending-task admission cap, process-local API request counts/latency buckets/in-flight requests, and rolling 24-hour worker task-duration p50/p95. API counters reset on process restart; worker timings include Holmes and are not provider-only latency or model cost. API labels use route templates, method, and status class rather than user, incident, task, or alert IDs. Outside local mode, set `AIOPS_MAX_PENDING_TASKS` to a positive capacity chosen from an approved load test and `AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS` to the owner-approved maximum age for queued, running, or retrying tasks; the API exports that threshold for alert evaluation. PostgreSQL advisory locking enforces admission capacity across API replicas; duplicate alerts still resolve to the existing incident, while new alerts at capacity receive `503` with `Retry-After: 30`. Local Compose leaves both values unset. The local Compose metrics token is optional and the endpoint stays disabled when it is unset.

Prometheus-compatible alert rules for API scrape failure, oldest pending-task age beyond the configured objective, admission capacity reached, and failed tasks are in [`monitoring/aiops-alerts.yml`](monitoring/aiops-alerts.yml). The age threshold is supplied by `AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS`, not hard-coded. Validate rule syntax and firing behavior with `bash verify-aiops-alerts.sh`; the target deployment must still configure the private scraper and notification receiver. If aggregating multiple clusters, attach a stable `cluster` label to the scraped metrics.

## Alert routing and end-to-end walkthrough

In OpenObserve, configure the alert destination to call `http://incident-api:8081/webhooks/openobserve` over the Compose network and send `X-Alert-Token` with the same `ALERT_WEBHOOK_TOKEN`. Use the bounded alert template shown in [`DEMO.md`](DEMO.md). Existing local OpenObserve destinations may still point to `host.docker.internal:8081`; update them if you want traffic to stay on the Compose network.

After a successful deployment, any CI provider can publish normalized release metadata with [`publish-release-event.mjs`](demo/order-service/scripts/publish-release-event.mjs). This repository also includes a reusable GitHub Actions adapter at [`.github/workflows/publish-aiops-release-event.yml`](../../.github/workflows/publish-aiops-release-event.yml). Keep `RELEASE_WEBHOOK_SECRET` in the CI secret store and use the same value configured on the order-service event receiver. The publisher signs the exact JSON body with HMAC-SHA256, requires HTTPS for non-loopback targets, rejects redirects, bounds event metadata, and does not include credentials in the payload. Configure `AIOPS_RELEASE_WEBHOOK_URL`, `RELEASE_VERSION`, `RELEASE_COMMIT_SHA`, and `RELEASE_CHANGED_FILES_JSON` in the post-deploy step; `GITHUB_SHA` or `CI_COMMIT_SHA` can supply the commit when `RELEASE_COMMIT_SHA` is omitted. The workflow does not deploy the workload. A deployment workflow still needs to call this reusable workflow after its deployment job and bind the protected URL/secret.

Example invocation after the provider's deploy job succeeds:

```bash
AIOPS_RELEASE_WEBHOOK_URL="https://order-service.internal/internal/releases" \
RELEASE_VERSION="$DEPLOYED_VERSION" \
RELEASE_COMMIT_SHA="$GITHUB_SHA" \
RELEASE_CHANGED_FILES_JSON="$CHANGED_FILES_JSON" \
node demo/order-service/scripts/publish-release-event.mjs
```

Inject `RELEASE_WEBHOOK_SECRET` into the job environment from the provider's secret store; do not put the secret in the command, workflow source, or event payload. A provider-specific deployment workflow and real secret-backed acceptance remain to be configured for the selected pipeline.

For GitHub Actions, add this job to the deployment workflow after the deploy job succeeds:

```yaml
  publish-aiops-release-event:
    needs: deploy
    uses: ./.github/workflows/publish-aiops-release-event.yml
    with:
      release_version: ${{ needs.deploy.outputs.version }}
      changed_files_json: ${{ needs.deploy.outputs.changed_files_json }}
    secrets:
      aiops_release_webhook_url: ${{ secrets.AIOPS_RELEASE_WEBHOOK_URL }}
      release_webhook_secret: ${{ secrets.RELEASE_WEBHOOK_SECRET }}
```

The caller must expose the deployed version and changed paths as job outputs, and configure the two repository/environment secrets. This example is an integration template; no remote workflow or secret was changed or run.

To exercise the sender, NestJS receiver, and OpenObserve persistence together in the local test stack, load the private runtime variables and run:

```bash
source "$HOME/.config/holmesgpt-aiops/local-compose.env"
AIOPS_COMPOSE_PROJECT=holmesgpt-aiops-goal bash examples/openobserve-aiops/verify-release-event-e2e.sh
```

The check creates a one-off loopback-only order-service container with a random in-memory test secret, sends one signed synthetic release event, flushes it, verifies the exact version/commit in local OpenObserve, and stops the temporary container. It appends a synthetic `release_deployed` record to the local test volume; it does not run a deployment job.

1. Enable order-service chaos mode from the demo page or the approved workbench action; send a test order and confirm its trace/logs appear in OpenObserve.
2. Trigger the OpenObserve alert. The incident API validates the token and payload, then atomically writes an incident, task and outbox event to PostgreSQL.
3. Redis/Celery delivers the task. The worker calls the Holmes API; successful allowlisted OpenObserve tool results are saved as evidence. The incident workbench shows task status, trace, findings, evidence and audit timeline.
4. In the workbench, use the operator account to request a test action. Sign in separately as approver to approve/reject. The operator can then execute an approved action; the service rechecks authorization, verifies the resulting order-service state and audits any rollback.
5. Use the approver account to write and review the incident retrospective. The report is stored per incident in PostgreSQL, and save/review actions are added to the audit timeline. Operators and viewers can read it but cannot edit it.
6. Return the demo order-service to normal mode and confirm a new order succeeds.

Live model investigation requires the private `DEEPSEEK_API_KEY` and the local `HOLMES_API_KEY`. Source the private key file before recreating `holmes-api`; never send it through the incident API or put it in this repository. Without a model key, the worker remains gated by Holmes health and live acceptance is unavailable; mock reports do not replace this requirement. Proxy-level read-only enforcement is active for Holmes, while OpenObserve OSS itself still has no native RBAC; use an RBAC edition for server-native user and tenant isolation. Holmes has no request idempotency key. A `/api/chat` timeout, transport failure, or HTTP 5xx now ends the task as `holmes_outcome_unknown` without automatic retry, because the provider may have completed and charged the request; an authorized manual retry can still repeat the call and its cost. HTTP 429 remains automatically retryable.

The sample Holmes config sets `max_steps: 12`, which caps model iterations but does not enforce a currency spend limit. Per-call cost telemetry is a LiteLLM estimate emitted after a response and may be zero or unavailable. Before production, configure and test a hard spend limit and budget alert with the selected provider or approved model gateway; do not treat Holmes telemetry as spend enforcement or invoice reconciliation.

Live 20-case evaluation seeds at most 100 synthetic evidence rows into the local `app_logs` stream, then queries them through Holmes and the read-only proxy. Three release-linked cases also seed typed `release_deployed` events; Holmes receives the linked repository Runbook and source path, and must ground release claims in returned telemetry. Seeded rows remain in the local Docker OpenObserve volume, carry unique run/trace IDs where applicable and are labeled `synthetic_fixture`; the seeder refuses non-loopback endpoints. The evaluation also refuses non-local Holmes API targets. Details and the command are in [`evals/README.md`](evals/README.md).

## Persistence and recovery

PostgreSQL persists incidents, triage severity/assignee, tasks, users, revoked session hashes, reactivation requests, approvals, retrospectives, audit and outbox records in `aiops-postgres-data`. The one-shot `incident-migrate` service applies versioned SQL migrations through `0011_restrict_runtime_delete` before the API and worker start; the API processes never apply schema changes during startup. The API runtime role can delete only expiring OIDC login transactions and revoked sessions; business, identity, task, approval, audit, and outbox records are protected from API-role deletion. Operators and admins can set severity and assign incidents to active in-scope operators/admins; the API rechecks authorization and records each change in the append-only audit timeline. Incident status transitions are API-enforced and audited: `open` → `investigating` or `closed`; `investigating` → `awaiting_approval`, `resolved`, or `closed`; `awaiting_approval` → `investigating`, `resolved`, or `closed`; `resolved` → `investigating` or `closed`; `closed` is terminal. Resolved and closed incidents cannot be triaged. The API uses `aiops_runtime`; the worker uses `aiops_worker`, which can read incidents/tasks/outbox, update only task/outbox processing columns, and append worker audit events, but cannot access users or change incidents/audit history. Redis uses AOF in `aiops-redis-data`; the PostgreSQL outbox dispatcher re-enqueues unclaimed work after broker interruption. If a worker lease expires after a task was claimed, the outcome is treated as unknown and the task fails without automatic redelivery; an authorized manual retry is required and may repeat a model charge. For unknown outcomes, the retry API rejects requests unless they explicitly acknowledge possible duplicate model charges. The workbench warns and asks for confirmation, and the append-only retry audit records both the original error code and the acknowledgement. Restart a service with `docker compose restart incident-api incident-worker redis` to exercise recovery without removing volumes.

Database migration sequencing, backup/restore operations, and rollback limits are documented in [`POSTGRESQL-OPERATIONS.md`](POSTGRESQL-OPERATIONS.md). The local verifier now exercises recipient-certificate encryption and restore, but does not establish managed production database compatibility, production key custody, encrypted off-host retention, or PITR.

Create a private custom-format backup with the PostgreSQL client utilities installed. Configure the normal `PG*` connection variables and point `PGPASSFILE` at a secret-manager-provided passfile; the script does not accept or print a database password. It refuses to overwrite an existing file, writes with mode `0600`, and validates the archive before publishing it:

```bash
PGHOST=db.example.internal PGPORT=5432 PGDATABASE=aiops PGUSER=aiops_backup \
PGSSLMODE=verify-full PGPASSFILE=/run/secrets/pgpass \
  ./backup-postgres.sh /secure-backups/aiops-$(date -u +%Y%m%dT%H%M%SZ).dump
```

To produce an additional CMS/AES-256-GCM encrypted artifact, pass the archive,
new destination, and platform-approved recipient certificate to
`encrypt-postgres-backup.sh`. The input archive remains in place, so use
approved encrypted or ephemeral scratch storage and follow the platform's
retention policy:

```bash
bash ./encrypt-postgres-backup.sh \
  /secure-ephemeral/aiops-backup.dump \
  /secure-backups/aiops-backup.cms.der \
  /run/secrets/backup-recipient.crt
```

Restore encrypted files by decrypting to protected scratch storage with the matching private key, then restore into a newly provisioned, empty database using `pg_restore --exit-on-error --single-transaction --no-owner --dbname=<restore-database> <backup-file>`. Validate application data before changing any service connection. The scripts intentionally do not create or replace databases. Run `bash verify-postgresql-backup.sh` to apply migrations 0001–0011, create a synthetic incident, check archive and encryption behavior, then restore both plaintext and encrypted archives into separate databases in an isolated PostgreSQL 16 container. It verifies all migration records, the task-duration index, triage and user-lifecycle columns, worker-role grants, mode `0600`, overwrite refusal, and rejection of tampered ciphertext. This verifies local mechanics only; production still needs managed or approved key custody, encrypted off-host storage, retention, PITR/WAL archiving, access ownership, and a measured recovery-time drill. Do not run `docker compose down -v` unless you intend to destroy all local demo data.

## Tests and evaluation

Run service tests against an isolated, ephemeral PostgreSQL instance (Compose starts it with a temporary filesystem):

```bash
docker compose --profile test run --rm incident-test
```

Run the OpenObserve policy proxy checks:

```bash
docker build -t aiops-openobserve-proxy:test openobserve-proxy
docker run --rm aiops-openobserve-proxy:test python -m unittest discover -s tests -v
```

Run a live compatibility check against an isolated, ephemeral OpenObserve v1.0.3 instance. The script uses an internal Docker network, test-only credentials and temporary storage, then removes both containers and the network:

```bash
bash verify-openobserve-proxy-live.sh
```

If the default test subnet conflicts with a local route, set `AIOPS_TEST_DOCKER_SUBNET` to an unused private `/29`. The script does not use or modify the running demo's OpenObserve data.

Generate a deterministic report for the 20 synthetic root-cause cases:

```bash
python3 evals/run_evals.py --mode mock --output /tmp/holmes-aiops-mock-report.json
```

Mock mode does not call Holmes or OpenObserve; the diagnosis field is empty and scoring is `not_scored`. A live evaluation can send up to 20 model requests and requires explicit `--confirm-live`; see [`evals/README.md`](evals/README.md). The case source remains synthetic even when retrieved evidence is live.

## Test-to-production boundary

This Compose stack is a local test environment on the current macOS Docker Desktop host. It is not the selected production runtime. Before production acceptance, select a platform and confirm its network/TLS boundary, external identity provider and tenant model, secret handling, data retention/compliance, capacity/SLO, backup policy, OpenObserve service-account permissions, model provider/cost controls, release source, action owners and allowed production actions. No production credentials, migration, deployment or remediation are configured or claimed here.

The platform-neutral production decisions, deployment configuration gates, staged acceptance plan, and current open decisions are tracked in [`PRODUCTION-READINESS.md`](PRODUCTION-READINESS.md). It is preparation guidance, not an executable production manifest.
