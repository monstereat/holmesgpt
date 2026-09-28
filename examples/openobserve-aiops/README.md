# HolmesGPT OpenObserve AIOps demo

PostgreSQL connections from the Incident API, worker, migration runner, recovery CLI, and admission benchmark use a shared 3-second connection-establishment timeout by default; an explicit DSN or caller `connect_timeout` takes precedence. This does not bound SQL execution or lock waits. The source change is awaiting core verification.

This project keeps HolmesGPT's existing Python/FastAPI investigation API (`server.py`) and OpenObserve toolset. A separate Python incident service owns authenticated alert intake, PostgreSQL state, Redis/Celery dispatch, approvals, audit history and the workbench. The NestJS order service emits telemetry and owns its narrowly scoped local test action. It does not expose a Docker socket or arbitrary shell execution.

## Verification status and résumé evidence

This README documents the current source/configuration and intended local test stack; it is not a claim that every described behavior has passed current-version acceptance. On 2026-09-29 selected current-source checks passed: Incident 29 cases, OpenObserve scope 15 cases, local readiness, a live synthetic DeepSeek/OpenObserve alert flow with verified matching Trace evidence, Prometheus alert rule fixtures, Collector production-config validation, PostgreSQL role checks, and plaintext/encrypted restore through migration `0021`. The full incident/workbench suite, transient broker retry/backoff and multi-instance behavior, remaining metrics behavior, browser acceptance, organization IdP, capacity/SLO, off-host restore, and production deployment remain unverified. See [RESUME-CLAIMS.md](RESUME-CLAIMS.md) for wording supported by recorded evidence and [ROADMAP.md](../../ROADMAP.md) for current gaps. Do not represent this local test stack as production-deployed or claim an RCA accuracy percentage; the human RCA score is `not_scored`.

## Local Docker test stack

Run from this directory. Compose binds browser/API ports to `127.0.0.1` and keeps PostgreSQL and Redis on the private Compose network. Do not use this demo configuration as a production deployment.

Initialize the local Compose credentials once on a clean install. The generated file is stored outside the repository at `${XDG_CONFIG_HOME:-$HOME/.config}/holmesgpt-aiops/local-compose.env` with mode `0600`; it contains the database bootstrap passwords, service tokens, signing key and local demo identities. The initializer refuses to overwrite an existing file or generate new passwords when this Compose project already has containers or volumes. Keep this file when reusing existing volumes: changing bootstrap passwords does not rotate credentials already stored in PostgreSQL or OpenObserve. If you already have this stack and its volumes, restore the matching file instead of running the initializer.

Live investigation also requires a private DeepSeek key file at `${XDG_CONFIG_HOME:-$HOME/.config}/holmesgpt-aiops/deepseek.env` with mode `0600`, containing `export DEEPSEEK_API_KEY='your-key'`. Create it without putting the key in shell history:

```bash
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/holmesgpt-aiops"
mkdir -p "$config_dir"
chmod 700 "$config_dir"
if [[ -L "$config_dir/deepseek.env" ]]; then echo "Refusing a symlink: $config_dir/deepseek.env" >&2; exit 1; fi
if [[ ! -e "$config_dir/deepseek.env" ]]; then (umask 077; : > "$config_dir/deepseek.env"); fi
chmod 600 "$config_dir/deepseek.env"
${EDITOR:-vi} "$config_dir/deepseek.env"
```

Add `export DEEPSEEK_API_KEY='your-key'` in the editor, replacing the placeholder, and save. The local Compose wrapper loads both files without adding either to this repository. If the key is missing, Holmes is marked unhealthy and the incident worker waits instead of accepting investigation tasks. If you change `XDG_CONFIG_HOME`, use the same value for initialization, editing the key file and every later `compose-local.sh` command.

```bash
bash ./init-local-runtime-env.sh   # only for a new, empty local Compose project
./compose-local.sh up -d --build
```

The incident service hashes account passwords before storing them. In local mode only, the workbench fills credentials for a selected account from `AIOPS_TEST_USERS_JSON` through a `Cache-Control: no-store` endpoint; the selector lists configured local test identities and defaults to the operator account when present, making it possible to switch roles during the demo. OIDC/production mode does not expose the endpoint. Compose explicitly sets `AIOPS_ENV=local`; the service rejects `AIOPS_TEST_USERS_JSON` in every other environment. No project `.env` file is needed. `HOLMES_API_KEY` protects the internal Holmes API and is shared only with the incident worker. The stack defaults to `deepseek/deepseek-flash`; a missing `DEEPSEEK_API_KEY` makes Holmes unhealthy and holds the incident worker until the key is loaded. The local Holmes readiness check confirms the key is present and the model is configured; it does not validate the remote provider credential.

```bash
./compose-local.sh up -d --force-recreate holmes-api
```

DeepSeek's API key is passed only to the Holmes container; do not commit or log it.

The Holmes container reads a read-only config file. By default it mounts the committed template `holmes-config/config.yaml.example`; to use a local config copy, set `HOLMES_CONFIG_FILE` to its absolute path before `docker compose up`. Holmes and OpenObserve share no Docker network, so Holmes can reach OpenObserve only through `openobserve-proxy` for queries and `otel-collector` for telemetry export. The query proxy requires separate client credentials, exposes only the log-stream list and search APIs, filters streams to `app_logs` and `frontend_errors`, validates a single ClickHouse SQL SELECT AST, and caps query windows, timeouts, rows, response bytes, and active requests (local default 16; accepted requests above the active cap receive HTTP 503 with `Retry-After: 1`; the listener backlog is also bounded). It also requires a per-stream `OPENOBSERVE_ALLOWED_FIELDS_JSON` policy, rejects duplicate JSON object keys, expands wildcard projections to those fields, rejects references to other fields, database/catalog qualification, SQL functions and nested field access, filters stream schemas, removes unlisted fields from returned records, and drops object-valued/nested fields. This does not inspect free-text values for embedded secrets or personal data, and it does not add native RBAC to OpenObserve OSS. Its private `/_internal/metrics` endpoint reports active handlers, capacity rejections and response counts by status class; it is disabled locally without `OPENOBSERVE_PROXY_METRICS_TOKEN` and requires a separate bearer token outside local mode. The collector accepts OTLP/HTTP only on its private Compose network and forwards traces/metrics to OpenObserve using credentials that are not passed to Holmes. These controls do not protect against compromise of the proxy, collector, Docker host, or local OpenObserve administrator. Treat this instance as test-only. OpenObserve `/healthz` is available on the host; its UI remains loopback-bound.

The model is selected through Holmes' `MODEL` environment variable, and LiteLLM reads the provider key from `DEEPSEEK_API_KEY`; the config template contains toolset settings and caps each investigation at 12 model steps. The Compose `app` network is internal and used for the incident API, worker, database, queue, and demo action owner. Only Holmes joins the separate `model-egress` network for model-provider calls; the local network arrangement has not been validated as a production network policy.

The local OpenObserve container permits up to 24-hour-old log ingestion so the live synthetic evaluation can place cases farther apart than the proxy's one-hour query limit. OpenObserve defaults to five hours. This setting is only for the local test Compose stack and must not be copied into production without an explicit data-ingestion policy review.

After startup:

- Order-service demo: [http://localhost:8080](http://localhost:8080)
- Holmes incident workbench: [http://localhost:8081](http://localhost:8081) (local test logins: `admin`, `operator`, `approver`)
- OpenObserve: [http://localhost:5080](http://localhost:5080)
- Incident API liveness: `http://localhost:8081/healthz`; readiness: `http://localhost:8081/readyz`
- Holmes API liveness: `http://localhost:5050/healthz`; readiness: `http://localhost:5050/readyz`

The account seeded as `operator` can create/retry tasks, cancel queued/retrying investigations, and request the fixed `set-chaos-mode` test action; Compose explicitly enables it with `AIOPS_DEMO_ACTIONS_ENABLED=true`. Running Holmes requests cannot be interrupted. The sample action is permitted only when `AIOPS_ENV=local`; enabling it in another environment makes the API fail startup, and its endpoints remain disabled there. The separate `approver` can approve or reject it and edit/review incident retrospectives. The requester cannot approve their own request. All three accounts are local demo identities, not a production identity provider.

The incident API's host port defaults to `8081`; if another local container owns that port, update `AIOPS_API_HOST_PORT` in the private `~/.config/holmesgpt-aiops/local-compose.env` file before starting the stack. The service remains on port `8081` inside the private Compose network, so the OpenObserve webhook destination does not change.

The browser-facing ports are published by a fixed-target Nginx ingress bound to `127.0.0.1`. The incident API and order-service remain attached only to internal networks. The local ingress itself is connected to the Compose default bridge so Docker Desktop can publish its loopback ports; as a result, the ingress container has a default outbound route. It forwards only to the two configured Compose services and is for local testing, not a production ingress or security boundary. Keep every other Compose service on an explicitly declared network so it does not join the default bridge implicitly.

The local `admin` can inspect application identity mappings, disable workbench access, and approve a pending restore request. A disabled user must complete a valid OIDC sign-in first; the callback refreshes IdP group mappings, creates one pending request, returns `403`, and does not issue a session. Admin restore does not change roles/scopes and writes an audit event with actor and target. Disabling increments a per-user session generation, so old sessions do not revive after restore; users must sign in again. During mixed-version deployments, wait until every old API instance is stopped before restoring accounts. This does not change the user's identity-provider account. No local admin credential is committed.

Logout revokes the current OIDC cookie or local bearer session server-side by storing only its token hash and expiry. The API checks revocation on authenticated requests. Transactional writes revalidate and lock the active session inside the same database transaction as their mutation; logout and account disable serialize against those writes. OIDC claim synchronization, account disable, and account reactivation also share a database lifecycle lock, so concurrent identity updates and lifecycle changes serialize. A write that obtains its session lock first may commit before revocation; one that obtains it afterward is rejected. If logout cannot reach PostgreSQL, the workbench keeps the current session visible and reports the error instead of silently treating the token as revoked. The selected local integration test verifies account disable waits for an in-flight approved action and the prior session is then rejected; it does not cover every identity/lifecycle interleaving. The IdP remains the role source of truth: removing the last administrator from its admin group can leave no active application administrator until an IdP-mapped replacement signs in; the database break-glass path only restores an existing disabled administrator and does not undo IdP role demotion.

Check service state and liveness:

```bash
bash ./verify-local-service-readiness.sh
./compose-local.sh ps
curl -fsS http://127.0.0.1:5080/healthz
curl -fsS http://127.0.0.1:5050/healthz
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS http://127.0.0.1:8081/readyz
curl -fsS http://127.0.0.1:8080/
```

`verify-local-service-readiness.sh` checks the ten required local services, including the loopback ingress, Holmes/Incident API/OpenObserve readiness, both browser-facing ingress routes, and the worker's authenticated Holmes OpenObserve-toolset gate. The incident and order URLs are checked through the published ingress rather than through unpublished application-container ports. It performs GET/readiness checks only; it does not call the model or write business data. The Collector has no local health endpoint configured, so this gate checks only that its container is running, not that it can receive or export telemetry. The local Collector keeps default internal metrics loopback-only; production scraping is configured separately in `otel-collector-production.yaml`.

The incident API `/healthz` is liveness; `/readyz` requires PostgreSQL connectivity and every migration bundled with that API image to be recorded in `schema_migrations`, so a code/schema mismatch stays out of service. The incident worker health check requires a Celery ping response through Redis. Holmes `/healthz` is liveness; `/readyz` reflects model readiness and can fail when no model is configured. OpenObserve's image has no shell-based health probe, so its endpoint is checked from the host.

The Holmes API runs as UID 10001; incident API/worker images run as the non-root `app` user; the order-service uses the built-in non-root `node` user; and the OTel Collector image runs as UID 10001. Local Compose gives these services a read-only root filesystem, drops Linux capabilities, disables privilege escalation, and provides bounded temporary storage. Holmes config and cache paths live in its 64 MiB `/tmp` tmpfs; the config file itself is mounted read-only. Before each model request, the worker checks Holmes `/api/info?detail=full` for an enabled OpenObserve toolset; temporary toolset unavailability uses the existing bounded task retry policy. These controls are for the local Docker runtime; validate the equivalent policy in the selected production platform.

Holmes exports OpenTelemetry traces and metrics through the local Collector using OTLP/HTTP protobuf. The Collector forwards both signals to OpenObserve's `/api/default/v1/traces` and `/api/default/v1/metrics` ingestion paths with a Basic Auth client authenticator. The Holmes API's OTel metrics include model-call duration and input/output token usage, labeled with the configured model and LiteLLM system; positive per-call cost estimates reported by LiteLLM are emitted as a cumulative metric. Unknown or zero estimates are omitted, and positive estimates are not provider invoice reconciliation. The trace also retains the per-investigation cost attribute. The local path has been smoke-tested with a DeepSeek request and OpenObserve queries. Do not expose the Collector receiver outside the private Compose networks.

For later production deployment, [`otel-collector-production.yaml`](otel-collector-production.yaml) is a separate configuration template. It requires TLS certificate/key and bearer-token files for incoming OTLP, file-mounted write-only OpenObserve credentials, an HTTPS upstream endpoint, and owner-selected resource/queue limits. It enables a bounded file-backed exporter queue; mount its data directory on encrypted persistent storage writable by the Collector UID and apply the target's access/retention controls because queued records contain telemetry. The upstream File Storage extension is marked beta and needs explicit production-owner acceptance or replacement. Validate the component configuration with `bash examples/openobserve-aiops/verify-production-otel-config.sh`; the check uses temporary credentials and does not contact OpenObserve. It does not replace target-platform network, certificate, secret rotation, or capacity acceptance.

The incident API exposes Prometheus text metrics at `/_internal/metrics` only when `AIOPS_METRICS_TOKEN` is set. The scraper must send `Authorization: Bearer <token>`; keep this endpoint private and store the token in the target's secret manager. It reports task counts by state, oldest pending task age, persisted retry attempts, pending-task admission cap, OIDC pending login transactions and process-local OIDC admission rejections, the oldest interrupted action awaiting operator reconciliation, process-local API request counts/latency buckets/in-flight requests, and rolling 24-hour worker task-duration p50/p95. API counters reset on process restart; worker timings include Holmes and are not provider-only latency or model cost. API labels use route templates, method, and status class rather than user, incident, task, or alert IDs. Outside local mode, set `AIOPS_MAX_PENDING_TASKS` to a positive capacity chosen from an approved load test, `AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS` to the owner-approved maximum age for queued, running, or retrying tasks, and `AIOPS_ACTION_EXECUTION_RECOVERY_AGE_SLO_SECONDS` to the owner-approved age for action reconciliation; these thresholds are exported for alert evaluation. PostgreSQL advisory locking enforces admission capacity across API replicas; duplicate alerts still resolve to the existing incident, while new alerts at capacity receive `503` with `Retry-After: 30`. OIDC login starts require the owner-selected positive `AIOPS_OIDC_MAX_PENDING_LOGINS` cap in production (local OIDC development defaults to 500) and return `503` with `Retry-After` set to the rounded-up seconds until the oldest reservation expires; a Prometheus alert tracks those rejections. The local Compose metrics token is optional and the endpoint stays disabled when it is unset.

Operators and admins can cancel queued or retrying investigations from the workbench. Cancellation locks the task row, marks it terminal, and appends `task.cancelled` to the incident audit timeline; a broker message already in flight is ignored because the worker only claims queued/retrying tasks. A task that is already running cannot be interrupted, and the API returns `409` rather than implying the Holmes request stopped. The current Incident API image includes this feature, but cancellation behavior has not been functionally verified after the paused source edits.

Manual retry keeps the failed task and its outbox dispatch event in one PostgreSQL transaction. It requires exactly one matching outbox event; a missing or duplicate dispatch record returns `503` and rolls back the task reset so the workbench does not report a retry that cannot be delivered. The attempt counter remains monotonic across manual retries, fencing late workers from earlier executions. Automatic task-failure retries use exponential delays with per-retry integer jitter from 80% to 100% of the delay ceiling, capped at 300 seconds, to spread retries during shared outages. This jitter applies to task execution retries, not outbox broker redelivery. Unknown Holmes/worker outcomes and successful model responses that fail result/evidence validation require explicit acknowledgement of possible duplicate model charges. The outbox age metric counts only available investigation events whose tasks are still queued or retrying, so cancelled work does not remain a false delivery backlog. The latest worker image includes the retry-jitter change, but its behavior is unverified because core tests are paused.

Prometheus-compatible alert rules for API scrape failure, webhook 4xx/5xx responses, OIDC login-capacity rejection, oldest pending-task age beyond the configured objective, action recovery age beyond the owner-configured objective, admission capacity reached, and failed tasks are in [`monitoring/aiops-alerts.yml`](monitoring/aiops-alerts.yml). Each rule emits the stable `service=holmes-aiops` resource label required by production webhook RBAC. The webhook rules use the API's bounded route-template/status metrics; 4xx rejections are called out because Alertmanager does not retry them. Age thresholds are owner-configured, not hard-coded. [`monitoring/alertmanager.yml.example`](monitoring/alertmanager.yml.example) shows an HTTPS Alertmanager route to the incident service; the target deployment must still configure its private scraper, TLS ingress, notification receiver and secret mount. For Prometheus rule aggregation, attach a stable `cluster` label to scraped metrics. For multi-cluster Alertmanager intake, each alert must itself carry a stable cluster/source label; `group_by` does not create a missing label, and the receiver uses `groupKey` in deduplication. The added webhook/OIDC/proxy/outbox/action recovery alert rules await the paused verification pass; an action-recovery firing fixture has not been added.

Outbox metrics distinguish all queued/retrying events' highest reserved publish attempt from the highest attempt among unpublished events. The repeated-delivery alert uses the all-pending gauge, covering queued/retrying events whether or not the broker has acknowledged them, including messages accepted by the broker but not yet claimed by a worker. The unpublished backlog count and age remain limited to events not yet accepted by the broker. Outbox redelivery applies stable per-event/per-attempt jitter from 80% to 100% of the existing exponential interval (20 seconds to a 300-second cap), and stops after `AIOPS_OUTBOX_MAX_DELIVERY_ATTEMPTS` (20 in local mode; required from the deployment owner outside local mode). Exhaustion marks the task failed with `outbox_delivery_exhausted`, records the event in a dead-letter state, and alerts through `AIOpsOutboxDeadLetteredEvents`; an authorized operator can retry the task from the incident workbench after restoring broker connectivity. The rule and fixtures are source-only until the paused Prometheus verification resumes. Interrupted action recovery is manual; the age metric and alert identify stale `dispatching`/`rollback_pending` rows but do not resume actions automatically.

[`monitoring/prometheus-scrape.yml.example`](monitoring/prometheus-scrape.yml.example) shows a private HTTPS scrape job with bearer credentials and CA certificate files mounted as secrets. Replace its example host and cluster label, mount the rule file at the configured path, and permit the scrape only over the target's private network.

## Alert routing and end-to-end walkthrough

In OpenObserve, configure the alert destination to call `http://incident-api:8081/webhooks/openobserve` over the Compose network and send `X-Alert-Token` with the same `ALERT_WEBHOOK_TOKEN`. Use the bounded alert template shown in [`DEMO.md`](DEMO.md). Existing local OpenObserve destinations may still point to `host.docker.internal:8081`; update them if you want traffic to stay on the Compose network.

Prometheus Alertmanager can send its version 4 webhook envelope to `/webhooks/prometheus-alertmanager`. Enable the endpoint by injecting a separate `ALERTMANAGER_WEBHOOK_TOKEN` into the incident API and provide the same value through Alertmanager's `authorization.credentials_file`; local Compose forwards this optional environment variable when set and leaves the endpoint disabled when it is absent. Production startup requires the OpenObserve webhook token to be at least 32 bytes and enforces the same minimum for the optional Alertmanager token. Both webhook routes stream request bodies through a 64 KB hard limit before JSON parsing. The Alertmanager adapter accepts at most 100 alerts, and the example receiver config sets `max_alerts: 100`; truncated batches are rejected. The adapter derives an idempotency key from `groupKey`, source fingerprint and `startsAt`, so retries in one group reuse an incident while distinct groups and later firing episodes remain separate. It uses the normalized `service` label as the incident resource scope, which must match OIDC `resource_scopes`; OpenObserve may send a top-level `service`. Production webhooks reject alerts without a service; local mode alone retains the legacy `order-service` fallback. This is service-level access control inside one deployment, not multi-tenant isolation. The receiver maps approved severity and `trace_id` labels and treats alert metadata as untrusted bounded input. Bounded `summary`/`description` and HTTPS-only `runbook_url` / `dashboard_url` values without query strings are read from each alert's annotations, falling back to matching `commonAnnotations` only when the per-alert key is absent; other annotations are discarded. These values are untrusted Holmes context and are not followed or fetched by the service. Resolved notifications are acknowledged but do not automatically close incidents. Capacity errors return HTTP 503 with `Retry-After` and roll back the complete notification batch. Alertmanager treats 5xx responses as retryable; malformed or truncated requests are rejected with 4xx and need configuration correction. Tune group size, retries, and failure alerting for the selected deployment before routing production alerts. See the [official Alertmanager configuration reference](https://prometheus.io/docs/alerting/latest/configuration/) for its webhook payload and `authorization.credentials_file` fields.

OIDC `resource_scopes` accepts the single value `"*"` to grant access to all incident resources; it cannot be combined with named scopes. Treat this as global incident access and assign it only to an explicitly approved IdP group.

[`monitoring/alertmanager-v4-payload.example.json`](monitoring/alertmanager-v4-payload.example.json) is a synthetic v4 payload for later acceptance work. It contains no production data or credentials and has not been sent to an Alertmanager or Incident API.

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

The workbench incident list supports combined status/severity filters, an assigned-to-me view, literal text search across alert name, Trace IDs and alert summary, and stable cursor pagination. `GET /api/incidents` accepts `status`, `severity`, `assigned_to_me`, `search`, `limit` (1–100) and the returned `next_cursor`; requests remain subject to the caller's incident-read authorization. These list enhancements are implemented in the current source but awaiting the user's requested core-test pass.
4. In the workbench, use the operator account to request a test action. Sign in separately as approver to approve/reject. The operator can then execute an approved action; the service rechecks authorization, verifies the resulting order-service state and audits any rollback.
5. Use the approver account to write and review the incident retrospective. The report is stored per incident in PostgreSQL, and save/review actions are added to the audit timeline. Operators and viewers can read it but cannot edit it.
6. Return the demo order-service to normal mode and confirm a new order succeeds.

Live model investigation requires the private `DEEPSEEK_API_KEY` and the local `HOLMES_API_KEY`. Source the private key file before recreating `holmes-api`; never send it through the incident API or put it in this repository. Without a model key, the worker remains gated by Holmes health and live acceptance is unavailable; mock reports do not replace this requirement. Proxy-level read-only enforcement is active for Holmes, while OpenObserve OSS itself still has no native RBAC; use an RBAC edition for server-native user and tenant isolation. Holmes has no request idempotency key. A `/api/chat` timeout, transport failure, or HTTP 5xx ends the task as `holmes_outcome_unknown` without automatic retry, because the provider may have completed and charged the request; successful responses that fail result/evidence validation are also treated as potentially charged. HTTP 429 remains automatically retryable. Manual retry requires explicit acknowledgement for these cases and for expired worker leases; these changes have not yet been verified.

The sample Holmes config sets `max_steps: 12`, which caps model iterations but does not enforce a currency spend limit. Per-call cost telemetry is a LiteLLM estimate emitted after a response and may be zero or unavailable. Before production, configure and test a hard spend limit and budget alert with the selected provider or approved model gateway; do not treat Holmes telemetry as spend enforcement or invoice reconciliation.

Live 20-case evaluation seeds at most 100 synthetic evidence rows into the local `app_logs` stream, then queries them through Holmes and the read-only proxy. Three release-linked cases also seed typed `release_deployed` events; Holmes receives the linked repository Runbook and source path, and must ground release claims in returned telemetry. Seeded rows remain in the local Docker OpenObserve volume, carry unique run/trace IDs where applicable and are labeled `synthetic_fixture`; the seeder refuses non-loopback endpoints. The evaluation also refuses non-local Holmes API targets. Details and the command are in [`evals/README.md`](evals/README.md).

## Persistence and recovery

PostgreSQL persists incidents, triage severity/assignee, service resource scope, tasks, users, revoked session hashes, reactivation requests, approvals, retrospectives, audit and outbox records in `aiops-postgres-data`. The one-shot `incident-migrate` service applies versioned SQL migrations, including `0014_incident_resource_scope`, `0015_incident_timeline_index`, `0016_restrict_runtime_user_columns`, `0017_action_execution_recovery`, `0018_outbox_dead_letter`, `0019_dispatcher_dead_letter_runtime_role`, `0020_pending_task_age_index`, and `0021_metrics_scan_indexes`, before the API and worker start. Migrations 0014–0021 are applied to the authorized local test database; read-only catalog checks confirmed the two indexes from migration 0021 are present, but their query plans and performance have not been verified. Read-only checks confirmed the currently applied schema objects and that `aiops_runtime` can update `outbox_events.dead_lettered_at` while `aiops_worker` cannot. The outbox dispatcher runs in the Incident API process and uses its `aiops_runtime` database identity; Celery task execution uses `aiops_worker`. API/worker/migration images were rebuilt and containers are healthy, and local health/readiness plus the order homepage returned HTTP 200. These checks do not functionally verify dead-letter transitions, pagination, action permissions/recovery, or the updated PostgreSQL role/backup verifiers. Migration 0015 supports bounded keyset pagination of audit history; its transactional index creation can hold a table lock and must be measured on a staging-sized copy before production. The API processes never apply schema changes during startup. The API runtime role can delete only expiring OIDC login transactions and revoked sessions; business, identity, task, approval, audit, and outbox records are protected from API-role deletion. Operators and admins can set severity and assign incidents to active in-scope operators/admins; the API rechecks authorization and records each change in the append-only audit timeline. Alertmanager `service` labels and OpenObserve top-level `service` values map to incident scopes; production rejects missing service labels, while local mode uses the legacy `order-service` fallback. List/detail and incident mutations enforce the current principal's resource scopes; the workbench header shows the active identity's service scopes and the list explains that incidents are filtered by account scope. Admin user-mapping management is global across service scopes and is granted by the IdP-mapped admin role, so tightly control that group. The demo action owner remains limited to `order-service`; its chaos state and idempotent operation journal now share an atomically replaced file on the `order-action-data` named volume, and the owner rejects new actions after 10,000 retained operation keys rather than evicting keys. A local-demo periodic reconciler now checks the owner journal and reconciles only confirmed, state-matching `dispatching` or `rollback_pending` operations; missing, ambiguous, or mismatched outcomes remain pending and require an operator. This is local single-instance durability, not a production-grade multi-replica action store or remediation control. Incident status transitions are API-enforced and audited: `open` → `investigating` or `closed`; `investigating` → `awaiting_approval`, `resolved`, or `closed`; `awaiting_approval` → `investigating`, `resolved`, or `closed`; `resolved` → `investigating` or `closed`; `closed` is terminal. Resolved and closed incidents cannot be triaged. API, authentication, and internal metrics responses include `Cache-Control: no-store`; responses handled by the application middleware include `nosniff`, frame-denial, referrer, and permissions headers. The API uses `aiops_runtime`; the outbox dispatcher runs in the Incident API process and writes `outbox_events.dead_lettered_at` with this identity. Migration 0019 grants that column update to `aiops_runtime` and revokes it from `aiops_worker`. Celery uses `aiops_worker` for task execution; it can read incidents/tasks/outbox, update task and dispatch-processing columns, and append worker audit events, but cannot access users, change incidents/audit history, or mark outbox rows dead-lettered. `aiops_break_glass` is a `NOLOGIN` role with only request/approval function access; separate authenticated custodian database logins must be members, and PostgreSQL rejects self-approval. Redis uses AOF in `aiops-redis-data`; Celery uses a 30-minute visibility timeout above the 20-minute database task lease. The worker rejects `HOLMES_TIMEOUT_SECONDS` values at or above the lease; the Holmes client currently caps that timeout at 900 seconds. All Celery apps sharing the broker must use a matching or longer visibility timeout because Celery selects the shortest value. The PostgreSQL outbox dispatcher reserves eligible rows in a short transaction, publishes after releasing database locks, and retries failed/unacknowledged publishes on an exponential 20–300-second ceiling with stable per-event/per-attempt jitter from 80% to 100% of that ceiling. The stable interval prevents polling loops and API replicas from changing the due time on each check; retries stop after `AIOPS_OUTBOX_MAX_DELIVERY_ATTEMPTS` (local default 20; required outside local mode). The authenticated API metrics expose the maximum reserved publish attempt across queued/retrying tasks, including events in backoff or awaiting worker claim; the outbox age SLO alert covers only events not yet accepted by the broker. Celery task claiming remains idempotent, so duplicate broker messages do not duplicate a task run. Each event stops redelivery at the configured attempt limit and enters dead-letter state; queued-task age still surfaces accepted messages that workers have not claimed. If a worker lease expires after a task was claimed, the outcome is treated as unknown and the task fails without automatic redelivery; an authorized manual retry is required and may repeat a model charge. For unknown outcomes, the retry API rejects requests unless they explicitly acknowledge possible duplicate model charges. The workbench warns and asks for confirmation, and the append-only retry audit records both the original error code and the acknowledgement. Restart a service with `docker compose restart incident-api incident-worker redis` to exercise recovery without removing volumes.

Migrations `0016_restrict_runtime_user_columns` through `0021_metrics_scan_indexes` have been applied to the authorized local test database through the Compose migration gate; read-only catalog queries confirmed the two 0021 indexes exist. Read-only catalog queries confirmed the user-column grant shape and that `aiops_runtime`, which owns the API-hosted outbox dispatcher, can update `dead_lettered_at` while `aiops_worker` cannot. OIDC role and resource-scope columns remain writable because verified claim synchronization depends on them, so the API database credential remains trusted. Current role and backup verifier sources include assertions through migration 0021, but functional permission, action-recovery, and fresh-cluster restore verification remains pending.

Database migration sequencing, backup/restore operations, and rollback limits are documented in [`POSTGRESQL-OPERATIONS.md`](POSTGRESQL-OPERATIONS.md). The backup verifier source has fresh-cluster ownership and privilege checks and derives its expected migration set from the repository, including `0021`; it also asserts the 0021 metrics indexes in restored and fresh databases. The updated verifier has not run. This does not establish managed production database compatibility, production key custody, encrypted off-host retention, or PITR.

To exercise the production PostgreSQL TLS policy locally, start the isolated test network and run the verifier:

```bash
./compose-local.sh --profile test up -d postgres-test
bash ./verify-postgresql-tls.sh
```

It creates a temporary TLS certificate authority and PostgreSQL server on the internal test network, verifies a `sslmode=verify-full` handshake from the incident service image, and confirms rejection of an untrusted CA, mismatched hostname, and weaker `sslmode`. The database uses tmpfs only and is removed after the check. This proves local Psycopg/PostgreSQL behavior with a synthetic CA; the selected managed database's real CA chain and endpoint still need staging verification.

The Celery/Redis production TLS path has a corresponding verifier:

```bash
bash ./verify-redis-tls.sh
```

It starts an authenticated Redis TLS server on the same internal network, verifies the actual Celery/redis-py handshake and rejects an untrusted CA, hostname mismatch, plaintext URL and disabled hostname verification. It uses a temporary server and generated test CA only; the chosen managed broker's endpoint, credentials, and CA chain still need staging verification.

Create a private custom-format backup with the PostgreSQL client utilities installed. Configure the normal `PG*` connection variables and point `PGPASSFILE` at a secret-manager-provided passfile; the script does not accept or print a database password. It refuses to overwrite an existing file, writes with mode `0600`, and validates the archive before publishing it:

```bash
PGHOST=db.example.internal PGPORT=5432 PGDATABASE=aiops PGUSER=aiops_backup \
PGSSLMODE=verify-full PGPASSFILE=/run/secrets/pgpass \
  ./backup-postgres.sh /secure-backups/aiops-$(date -u +%Y%m%dT%H%M%SZ).dump
```

`aiops_backup` is an example of a separately provisioned, database-scoped
read-only identity; it is not created by `postgresql-roles.psql`. The database
owner must grant the access required to dump every application table and
sequence, including objects added by later migrations.

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

Restore encrypted files with [`decrypt-postgres-backup.sh`](decrypt-postgres-backup.sh), which writes a validated mode-`0600` archive to a new path. For a new PostgreSQL cluster, provision the database roles or managed-identity mappings first, run `postgresql-roles.psql` as an authorized database administrator, and restore as `aiops_migrator` with `pg_restore --exit-on-error --single-transaction --no-owner --no-acl`; rerun the role template to apply grants to restored objects. If the restored migration ledger is behind the target release, confirm forward compatibility and run that release's migration job against the isolated restore before starting its API. The complete sequence and verification requirements are in [`POSTGRESQL-OPERATIONS.md`](POSTGRESQL-OPERATIONS.md). Validate application data and effective privileges before changing any service connection. The scripts intentionally do not create or replace databases. The backup verifier source includes fresh-cluster ownership and privilege checks and derives its expected migration set from the repository; it has not run against migrations `0014`–`0021`. The latest recorded run covered only `0001`–`0013` and restored on the same temporary PostgreSQL cluster, so current-schema or fresh-cluster recovery is not established. Production still needs managed or approved key custody, encrypted off-host storage, retention, PITR/WAL archiving, access ownership, and a measured recovery-time drill. Do not run `docker compose down -v` unless you intend to destroy all local demo data.

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

This Compose stack is a local test environment on the current macOS Docker Desktop host. Production deployment remains outside the current local-test scope. When the user resumes production work, select a platform and confirm its network/TLS boundary, external identity provider and tenant model, secret handling, data retention/compliance, capacity/SLO, backup policy, OpenObserve service-account permissions, model provider/cost controls, release source, action owners and allowed production actions. No production credentials, migration, deployment or remediation are configured or claimed here.

The platform-neutral production decisions, deployment configuration gates, staged acceptance plan, and current open decisions are tracked in [`PRODUCTION-READINESS.md`](PRODUCTION-READINESS.md). It is preparation guidance, not an executable production manifest.
