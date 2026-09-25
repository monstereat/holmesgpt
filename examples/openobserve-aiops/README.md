# OpenObserve + HolmesGPT AI Ops demo

This demo keeps HolmesGPT's existing Python/FastAPI investigation API and OpenObserve toolset. A separate Python incident service owns webhook intake, PostgreSQL incident/task state, the outbox and local user authorization. Redis/Celery transports investigation work. The NestJS order service remains a telemetry-producing demo and the only owner of its isolated test action.

The incident worker calls Holmes through the existing non-streaming `POST /api/chat` API. Holmes makes read-only OpenObserve queries using a dedicated service account. The incident API treats the alert name and metadata as untrusted values, and model prose alone is never stored as verified evidence.

## Read-only OpenObserve configuration

Use a dedicated OpenObserve service account with read-only permissions for `app_logs` and `frontend_errors`. Copy [`holmes-config/config.yaml.example`](holmes-config/config.yaml.example) into the local Holmes configuration directory mounted by Compose, then provide the runtime model and OpenObserve credentials through the local environment. The example sets the server-side `allowed_streams` list as an additional query boundary.

Do not commit real credentials or place the model key in the incident service. `HOLMES_API_KEY` authenticates the incident service to Holmes; `MODEL_API_KEY` and `OPENOBSERVE_SERVICE_TOKEN` are consumed only by Holmes.

The OpenObserve toolset is read-only at the application layer. Server-side OpenObserve RBAC is the authoritative access boundary. Keep application logs free of credentials and sensitive PII before ingestion.

## Investigation flow

1. The demo order service emits an error log and trace when its local chaos mode is enabled.
2. An authenticated OpenObserve webhook creates one incident, investigation task and outbox record in a single PostgreSQL transaction.
3. The dispatcher places the task on Redis/Celery. Database task state prevents duplicate messages from claiming the same active or completed work; outbox reconciliation recovers lost messages and expired worker leases.
4. The worker sends a bounded, non-streaming request to Holmes `/api/chat`. Holmes queries only the configured read-only OpenObserve streams.
5. The worker stores bounded successful tool results as evidence. Tool errors, missing calls and unavailable data remain explicit; unverified model statements do not become verified facts.

Holmes `/api/chat` does not accept an idempotency key. A timeout followed by retry can repeat the model call and its cost, while the incident service still keeps one task record and one final result.

## Status and boundaries

- The order-service has a token-protected `set-chaos-mode` action used only by the local demo; it has no Docker socket or host write access.
- PostgreSQL stores incident, task, test user, approval, audit and outbox records. The test-only user and role implementation is not a production identity provider.
- Release events written to `app_logs` are local fixtures, not a live Git/CI integration or release registry.
- The Holmes skill at [`skills/order-service-inventory-failure/SKILL.md`](skills/order-service-inventory-failure/SKILL.md) guides bounded read-only trace, log and release correlation. It never authorizes remediation.
- Compose service wiring, workbench, approval/action workflow and recovery walkthrough are completed in later roadmap tasks. A running Compose stack, live model investigation, production identity provider, production deployment and production remediation are not claimed here.
- Live Holmes acceptance requires the local operator to provide a model key, a Holmes API key, and a dedicated OpenObserve read-only account with stream permissions. Without these, the live investigation acceptance criterion remains blocked.

## Tests

The incident service has an isolated Docker `test` target. To exercise PostgreSQL integration cases, set `AIOPS_TEST_DATABASE_URL` to a disposable local test database only; tests reject non-local database hosts.

```bash
docker build --target test -t aiops-incident:test alert-trigger
docker run --rm aiops-incident:test python -m pytest -q tests
```

The OpenObserve toolset tests remain part of the root Holmes test suite:

```bash
poetry install --with dev
poetry run pytest -q tests/plugins/toolsets/openobserve
```
