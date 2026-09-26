# HolmesGPT OpenObserve AIOps demo

This project keeps HolmesGPT's existing Python/FastAPI investigation API (`server.py`) and OpenObserve toolset. A separate Python incident service owns authenticated alert intake, PostgreSQL state, Redis/Celery dispatch, approvals, audit history and the workbench. The NestJS order service emits telemetry and owns its narrowly scoped local test action. It does not expose a Docker socket or arbitrary shell execution.

## Local Docker test stack

Run from this directory. Compose binds browser/API ports to `127.0.0.1` and keeps PostgreSQL and Redis on the private Compose network. Do not use this demo configuration as a production deployment.

Live investigation requires the private DeepSeek key file. Create `~/.config/holmesgpt-aiops/deepseek.env` with mode `0600` and put `export DEEPSEEK_API_KEY='paste-key-here'` in it. Source it in the same shell before Compose; the key is not stored in this repository. If it is missing, Holmes is marked unhealthy and the incident worker waits instead of accepting investigation tasks.

```bash
export ZO_ROOT_USER_EMAIL="${ZO_ROOT_USER_EMAIL:-demo@example.test}"
export ZO_ROOT_USER_PASSWORD="$(openssl rand -hex 24)"
export OPENOBSERVE_PROXY_USERNAME="holmes-proxy"
export OPENOBSERVE_PROXY_PASSWORD="$(openssl rand -hex 32)"
export POSTGRES_PASSWORD="$(openssl rand -hex 24)"
export ALERT_WEBHOOK_TOKEN="$(openssl rand -hex 32)"
export ORDER_ACTION_TOKEN="$(openssl rand -hex 32)"
export HOLMES_API_KEY="$(openssl rand -hex 32)"
export SESSION_SIGNING_KEY="$(openssl rand -hex 32)"
export ADMIN_PASSWORD="$(openssl rand -hex 24)"
export OPERATOR_PASSWORD="$(openssl rand -hex 24)"
export APPROVER_PASSWORD="$(openssl rand -hex 24)"
export AIOPS_TEST_USERS_JSON="$(python3 -c 'import json,os; print(json.dumps([{"username":"admin","password":os.environ["ADMIN_PASSWORD"],"role":"admin","resource_scopes":["order-service"]},{"username":"operator","password":os.environ["OPERATOR_PASSWORD"],"role":"operator","resource_scopes":["order-service"]},{"username":"approver","password":os.environ["APPROVER_PASSWORD"],"role":"approver","resource_scopes":["order-service"]}]))')"

source "$HOME/.config/holmesgpt-aiops/deepseek.env"
docker compose up -d --build
```

Keep these values in the current shell or a password manager. The incident service hashes the two account passwords before storing them. Compose explicitly sets `AIOPS_ENV=local`; the service rejects `AIOPS_TEST_USERS_JSON` in every other environment. Reuse the same `ZO_ROOT_USER_PASSWORD` and `POSTGRES_PASSWORD` whenever restarting against existing volumes: generating new values does not rotate the credentials already stored inside OpenObserve or PostgreSQL. Reuse `OPENOBSERVE_PROXY_USERNAME` and `OPENOBSERVE_PROXY_PASSWORD` when recreating Holmes and the proxy together. No project `.env` file is needed. `HOLMES_API_KEY` protects the internal Holmes API and is shared only with the incident worker. The stack defaults to `deepseek/deepseek-flash`; a missing `DEEPSEEK_API_KEY` makes Holmes unhealthy and holds the incident worker until the key is loaded. The local Holmes readiness check confirms the key is present and the model is configured; it does not validate the remote provider credential.

```bash
source /tmp/holmesgpt-aiops-test-runtime.sh
source "$HOME/.config/holmesgpt-aiops/deepseek.env"
docker compose -f examples/openobserve-aiops/docker-compose.yaml up -d --force-recreate holmes-api
```

DeepSeek's API key is passed only to the Holmes container; do not commit or log it.

The Holmes container reads a read-only config file. By default it mounts the committed template `holmes-config/config.yaml.example`; to use a local config copy, set `HOLMES_CONFIG_FILE` to its absolute path before `docker compose up`. Holmes and OpenObserve share no Docker network, so Holmes can reach OpenObserve only through `openobserve-proxy` for queries and `otel-collector` for telemetry export. The query proxy requires separate client credentials, exposes only the log-stream list and search APIs, filters streams to `app_logs` and `frontend_errors`, validates a single ClickHouse SQL SELECT AST, and caps query windows, timeouts, rows and response bytes. The collector accepts OTLP/HTTP only on its private Compose network and forwards traces/metrics to OpenObserve using credentials that are not passed to Holmes. This narrows Holmes' access path, but does not add native RBAC to OpenObserve OSS or protect against compromise of the proxy, collector, Docker host, or local OpenObserve administrator. Treat this instance as test-only. OpenObserve `/healthz` is available on the host; its UI remains loopback-bound.

The model is selected through Holmes' `MODEL` environment variable, and LiteLLM reads the provider key from `DEEPSEEK_API_KEY`; the config template contains toolset settings and caps each investigation at 12 model steps.

The local OpenObserve container permits up to 24-hour-old log ingestion so the live synthetic evaluation can place cases farther apart than the proxy's one-hour query limit. OpenObserve defaults to five hours. This setting is only for the local test Compose stack and must not be copied into production without an explicit data-ingestion policy review.

After startup:

- Order-service demo: [http://localhost:8080](http://localhost:8080)
- Holmes incident workbench: [http://localhost:8081](http://localhost:8081) (local test logins: `admin`, `operator`, `approver`)
- OpenObserve: [http://localhost:5080](http://localhost:5080)
- Incident API liveness: `http://localhost:8081/healthz`; readiness: `http://localhost:8081/readyz`
- Holmes API liveness: `http://localhost:5050/healthz`; readiness: `http://localhost:5050/readyz`

The account seeded as `operator` can create/retry tasks and request the fixed `set-chaos-mode` test action; Compose explicitly enables it with `AIOPS_DEMO_ACTIONS_ENABLED=true`. The action is disabled by default in every other environment, including for already approved requests. The separate `approver` can approve or reject it and edit/review incident retrospectives. The requester cannot approve their own request. All three accounts are local demo identities, not a production identity provider.

The local `admin` can inspect application identity mappings and disable a user's workbench access. This does not change the user's identity-provider account; role and resource assignments remain controlled by OIDC group mappings. No local admin credential is committed.

Logout revokes the current OIDC cookie or local bearer session server-side by storing only its token hash and expiry. The API checks revocation on authenticated requests. If logout cannot reach PostgreSQL, the workbench keeps the current session visible and reports the error instead of silently treating the token as revoked.

Check service state and liveness:

```bash
docker compose ps
curl -fsS http://127.0.0.1:5080/healthz
curl -fsS http://127.0.0.1:5050/healthz
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS http://127.0.0.1:8081/readyz
curl -fsS http://127.0.0.1:8080/
```

The incident API `/healthz` is liveness; `/readyz` checks PostgreSQL connectivity. The incident worker health check requires a Celery ping response through Redis. Holmes `/healthz` is liveness; `/readyz` reflects model readiness and can fail when no model is configured. OpenObserve's image has no shell-based health probe, so its endpoint is checked from the host.

The Holmes API runs as UID 10001; incident API/worker images run as the non-root `app` user; the order-service uses the built-in non-root `node` user; and the OTel Collector image runs as UID 10001. Local Compose gives these services a read-only root filesystem, drops Linux capabilities, disables privilege escalation, and provides bounded temporary storage. Holmes config and cache paths live in its 64 MiB `/tmp` tmpfs; the config file itself is mounted read-only. These controls are for the local Docker runtime; validate the equivalent policy in the selected production platform.

Holmes exports OpenTelemetry traces and metrics through the local Collector using OTLP/HTTP protobuf. The Collector forwards both signals to OpenObserve's `/api/default/v1/traces` and `/api/default/v1/metrics` ingestion paths with a Basic Auth client authenticator. The Holmes API's OTel metrics include model-call duration and input/output token usage, labeled with the configured model and LiteLLM system; positive per-call cost estimates reported by LiteLLM are emitted as a cumulative metric. Unknown or zero estimates are omitted, and positive estimates are not provider invoice reconciliation. The trace also retains the per-investigation cost attribute. The local path has been smoke-tested with a DeepSeek request and OpenObserve queries. Do not expose the Collector receiver outside the private Compose networks.

The incident API exposes Prometheus text metrics at `/_internal/metrics` only when `AIOPS_METRICS_TOKEN` is set. The scraper must send `Authorization: Bearer <token>`; keep this endpoint private and store the token in the target's secret manager. It reports task counts by state, oldest pending task age, persisted retry attempts, pending-task admission cap, process-local API request counts/latency buckets/in-flight requests, and rolling 24-hour worker task-duration p50/p95. API counters reset on process restart; worker timings include Holmes and are not provider-only latency or model cost. API labels use route templates, method, and status class rather than user, incident, task, or alert IDs. Outside local mode, set `AIOPS_MAX_PENDING_TASKS` to a positive capacity chosen from an approved load test. PostgreSQL advisory locking enforces it across API replicas; duplicate alerts still resolve to the existing incident, while new alerts at capacity receive `503` with `Retry-After: 30`. Local Compose leaves the cap unset. The local Compose metrics token is optional and the endpoint stays disabled when it is unset.

## Alert routing and end-to-end walkthrough

In OpenObserve, configure the alert destination to call `http://incident-api:8081/webhooks/openobserve` over the Compose network and send `X-Alert-Token` with the same `ALERT_WEBHOOK_TOKEN`. Use the bounded alert template shown in [`DEMO.md`](DEMO.md). Existing local OpenObserve destinations may still point to `host.docker.internal:8081`; update them if you want traffic to stay on the Compose network.

1. Enable order-service chaos mode from the demo page or the approved workbench action; send a test order and confirm its trace/logs appear in OpenObserve.
2. Trigger the OpenObserve alert. The incident API validates the token and payload, then atomically writes an incident, task and outbox event to PostgreSQL.
3. Redis/Celery delivers the task. The worker calls the Holmes API; successful allowlisted OpenObserve tool results are saved as evidence. The incident workbench shows task status, trace, findings, evidence and audit timeline.
4. In the workbench, use the operator account to request a test action. Sign in separately as approver to approve/reject. The operator can then execute an approved action; the service rechecks authorization, verifies the resulting order-service state and audits any rollback.
5. Use the approver account to write and review the incident retrospective. The report is stored per incident in PostgreSQL, and save/review actions are added to the audit timeline. Operators and viewers can read it but cannot edit it.
6. Return the demo order-service to normal mode and confirm a new order succeeds.

Live model investigation requires the private `DEEPSEEK_API_KEY` and the local `HOLMES_API_KEY`. Source the private key file before recreating `holmes-api`; never send it through the incident API or put it in this repository. Without a model key, the worker remains gated by Holmes health and live acceptance is unavailable; mock reports do not replace this requirement. Proxy-level read-only enforcement is active for Holmes, while OpenObserve OSS itself still has no native RBAC; use an RBAC edition for server-native user and tenant isolation. Holmes has no request idempotency key, so a retry after an ambiguous timeout may repeat a model call and its cost, while the incident service keeps one task record.

Live 20-case evaluation seeds at most 100 synthetic evidence rows into the local `app_logs` stream, then queries them through Holmes and the read-only proxy. Three release-linked cases also seed typed `release_deployed` events; Holmes receives the linked repository Runbook and source path, and must ground release claims in returned telemetry. Seeded rows remain in the local Docker OpenObserve volume, carry unique run/trace IDs where applicable and are labeled `synthetic_fixture`; the seeder refuses non-loopback endpoints. The evaluation also refuses non-local Holmes API targets. Details and the command are in [`evals/README.md`](evals/README.md).

## Persistence and recovery

PostgreSQL persists incidents, tasks, users, revoked session hashes, approvals, retrospectives, audit and outbox records in `aiops-postgres-data`. The one-shot `incident-migrate` service applies versioned SQL migrations (currently through `0007_task_duration_metrics_index`) before the API and worker start; the API processes never apply schema changes during startup. When configured with the `aiops_runtime` database role, the application can append/read audit events but cannot update, delete, or truncate them. Redis uses AOF in `aiops-redis-data`; the PostgreSQL outbox dispatcher reconciles queued work after broker or worker interruption. Restart a service with `docker compose restart incident-api incident-worker redis` to exercise recovery without removing volumes.

To verify a local backup and restore without overwriting the active database:

```bash
docker compose exec -T postgres pg_dump -U aiops -Fc aiops > /tmp/aiops-test.dump
docker compose exec postgres createdb -U aiops aiops_restore_test
docker compose exec -T postgres pg_restore --no-owner -U aiops -d aiops_restore_test < /tmp/aiops-test.dump
docker compose exec postgres psql -U aiops -d aiops_restore_test -c 'SELECT count(*) FROM incidents;'
```

The restore target is a separate database for inspection. These steps intentionally do not delete the backup, restore database, or named volumes. Do not run `docker compose down -v` unless you intend to destroy all local demo data.

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

Generate a deterministic report for the 20 synthetic root-cause cases:

```bash
python3 evals/run_evals.py --mode mock --output /tmp/holmes-aiops-mock-report.json
```

Mock mode does not call Holmes or OpenObserve; the diagnosis field is empty and scoring is `not_scored`. A live evaluation can send up to 20 model requests and requires explicit `--confirm-live`; see [`evals/README.md`](evals/README.md). The case source remains synthetic even when retrieved evidence is live.

## Test-to-production boundary

This Compose stack is a local test environment. Before preparing a separate production design, confirm the deployment platform and network/TLS boundary, external identity provider and tenant model, secret management, data retention/compliance, capacity/SLO, backup policy, OpenObserve service-account permissions, model provider/cost controls, CI/release source, action owners and allowed production actions. No production credentials, migration, deployment or remediation are configured or claimed here.

The platform-neutral production decisions, deployment configuration gates, staged acceptance plan, and current open decisions are tracked in [`PRODUCTION-READINESS.md`](PRODUCTION-READINESS.md). It is preparation guidance, not an executable production manifest.
