# HolmesGPT OpenObserve AIOps demo

This project keeps HolmesGPT's existing Python/FastAPI investigation API (`server.py`) and OpenObserve toolset. A separate Python incident service owns authenticated alert intake, PostgreSQL state, Redis/Celery dispatch, approvals, audit history and the workbench. The NestJS order service emits telemetry and owns its narrowly scoped local test action. It does not expose a Docker socket or arbitrary shell execution.

## Local Docker test stack

Run from this directory. Compose binds browser/API ports to `127.0.0.1` and keeps PostgreSQL and Redis on the private Compose network. Do not use this demo configuration as a production deployment.

```bash
export ZO_ROOT_USER_EMAIL="${ZO_ROOT_USER_EMAIL:-demo@example.test}"
export ZO_ROOT_USER_PASSWORD="$(openssl rand -hex 24)"
export POSTGRES_PASSWORD="$(openssl rand -hex 24)"
export ALERT_WEBHOOK_TOKEN="$(openssl rand -hex 32)"
export ORDER_ACTION_TOKEN="$(openssl rand -hex 32)"
export HOLMES_API_KEY="$(openssl rand -hex 32)"
export SESSION_SIGNING_KEY="$(openssl rand -hex 32)"
export OPERATOR_PASSWORD="$(openssl rand -hex 24)"
export APPROVER_PASSWORD="$(openssl rand -hex 24)"
export AIOPS_TEST_USERS_JSON="$(python3 -c 'import json,os; print(json.dumps([{"username":"operator","password":os.environ["OPERATOR_PASSWORD"],"role":"operator","resource_scopes":["order-service"]},{"username":"approver","password":os.environ["APPROVER_PASSWORD"],"role":"approver","resource_scopes":["order-service"]}]))')"

docker compose up -d --build
```

Keep these values in the current shell or a password manager. The incident service hashes the two account passwords before storing them. Reuse the same `ZO_ROOT_USER_PASSWORD` and `POSTGRES_PASSWORD` whenever restarting against existing volumes: generating new values does not rotate the credentials already stored inside OpenObserve or PostgreSQL. No project `.env` file is needed. `HOLMES_API_KEY` protects the internal Holmes API and is shared only with the incident worker. `MODEL_API_KEY` is optional for starting the stack; a model request needs a valid key.

The Holmes container reads a read-only config file. By default it mounts the committed template `holmes-config/config.yaml.example`; to use a local config copy, set `HOLMES_CONFIG_FILE` to its absolute path before `docker compose up`. Do not commit credentials. Configure Holmes with a dedicated OpenObserve account that can read only `app_logs` and `frontend_errors`, and set `OPENOBSERVE_SERVICE_USER` and `OPENOBSERVE_SERVICE_TOKEN`. Do not substitute the OpenObserve root password for this account. Without model credentials and the read-only account, Holmes/API health can be checked but a live investigation is not accepted.

After startup:

- Order-service demo: [http://localhost:8080](http://localhost:8080)
- Holmes incident workbench: [http://localhost:8081](http://localhost:8081) (login with `operator` or `approver`)
- OpenObserve: [http://localhost:5080](http://localhost:5080)
- Holmes API liveness: `http://localhost:5050/healthz`; readiness: `http://localhost:5050/readyz`

The account seeded as `operator` can create/retry tasks and request the fixed `set-chaos-mode` test action. The separate `approver` can approve or reject it. The requester cannot approve their own request. Both accounts are local demo identities, not a production identity provider.

Check service state and liveness:

```bash
docker compose ps
curl -fsS http://127.0.0.1:5080/healthz
curl -fsS http://127.0.0.1:5050/healthz
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS http://127.0.0.1:8080/
```

OpenObserve's image has no shell-based health probe, so its endpoint is checked from the host. Holmes `/healthz` is liveness; `/readyz` reflects model readiness and can fail when no model is configured.

## Alert routing and end-to-end walkthrough

In OpenObserve, configure the alert destination to call `http://incident-api:8081/webhooks/openobserve` over the Compose network and send `X-Alert-Token` with the same `ALERT_WEBHOOK_TOKEN`. Use the bounded alert template shown in [`DEMO.md`](DEMO.md). Existing local OpenObserve destinations may still point to `host.docker.internal:8081`; update them if you want traffic to stay on the Compose network.

1. Enable order-service chaos mode from the demo page or the approved workbench action; send a test order and confirm its trace/logs appear in OpenObserve.
2. Trigger the OpenObserve alert. The incident API validates the token and payload, then atomically writes an incident, task and outbox event to PostgreSQL.
3. Redis/Celery delivers the task. The worker calls the Holmes API; successful allowlisted OpenObserve tool results are saved as evidence. The incident workbench shows task status, trace, findings, evidence and audit timeline.
4. In the workbench, use the operator account to request a test action. Sign in separately as approver to approve/reject. The operator can then execute an approved action; the service rechecks authorization, verifies the resulting order-service state and audits any rollback.
5. Return the demo order-service to normal mode and confirm a new order succeeds.

Live investigation requires all three of: a valid `MODEL_API_KEY`, a `HOLMES_API_KEY`, and a dedicated OpenObserve read-only account with access to the allowlisted streams. Without those, investigation tasks fail with a safe error and acceptance remains blocked; mock reports do not replace this requirement. Holmes has no request idempotency key, so a retry after an ambiguous timeout may repeat a model call and its cost, while the incident service keeps one task record.

## Persistence and recovery

PostgreSQL persists incidents, tasks, users, approvals, audit and outbox records in `aiops-postgres-data`. Redis uses AOF in `aiops-redis-data`; the PostgreSQL outbox dispatcher reconciles queued work after broker or worker interruption. Restart a service with `docker compose restart incident-api incident-worker redis` to exercise recovery without removing volumes.

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

Generate a deterministic report for the 20 synthetic root-cause cases:

```bash
python3 evals/run_evals.py --mode mock --output /tmp/holmes-aiops-mock-report.json
```

Mock mode does not call Holmes or OpenObserve; the diagnosis field is empty and scoring is `not_scored`. A live evaluation can send up to 20 model requests and requires explicit `--confirm-live`; see [`evals/README.md`](evals/README.md). The case source remains synthetic even when retrieved evidence is live.

## Test-to-production boundary

This Compose stack is a local test environment. Before preparing a separate production design, confirm the deployment platform and network/TLS boundary, external identity provider and tenant model, secret management, data retention/compliance, capacity/SLO, backup policy, OpenObserve service-account permissions, model provider/cost controls, CI/release source, action owners and allowed production actions. No production credentials, migration, deployment or remediation are configured or claimed here.
