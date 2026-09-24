# OpenObserve + HolmesGPT AI Ops demo

The `holmes/plugins/toolsets/openobserve` toolset is registered in HolmesGPT and supports bounded log search and trace-ID log lookup. The local demo under this directory sends NestJS logs, browser error reports, and OpenTelemetry traces to OpenObserve, then starts a host-side webhook receiver that invokes the Holmes CLI. See [`DEMO.md`](DEMO.md) for setup and validation steps.

## Start with read-only monitoring access

Set up a dedicated OpenObserve service account with access to only the streams used for incident investigation. Add this to your HolmesGPT configuration:

```yaml
toolsets:
  openobserve:
    enabled: true
    config:
      api_url: "http://openobserve:5080"
      organization: "default"
      username: "holmes@example.test"
      password: "{{ env.OPENOBSERVE_SERVICE_TOKEN }}"
      verify_ssl: true
      max_rows: 100
      timeout_seconds: 15
```

Do not store real service credentials in Git.

## Demonstrate a failed order service deployment

1. A sample NestJS `POST /orders` route records a trace ID and emits an error after a simulated bad deployment.
2. OpenTelemetry exports application logs and traces; OpenObserve stores them.
3. The operator raises an incident containing `service.name=order-service` and the failing trace ID.
4. HolmesGPT discovers log streams and retrieves bounded, time-scoped evidence through `openobserve_list_log_streams` and `openobserve_search_logs`.
5. The investigation identifies the earliest failed operation and cites matching log records; it must not assert a root cause without evidence.
6. A business service outside HolmesGPT holds the incident state, remediation approval, and audit record. Avoid giving the investigation tool write credentials.

## Status and boundaries

- The OpenObserve toolset is read-only and has request/parameter tests.
- The demo browser SDK sanitizes selected credential patterns and supports configurable sampling; its default demo sampling rate is 100%.
- The alert receiver requires a shared token, accepts only explicit trace-ID fields, bounds active investigation processes, deduplicates in-memory alerts, and prints results to stdout.
- It accepts only numeric count fields and valid ISO trigger times as alert metadata, marks alert values untrusted in the Holmes prompt, and omits CLI stderr from task logs.
- `incident_workflow.py` is a process-local reference model for ownership, severity, idempotency, evidence-versus-assumption findings, approval transitions, audit events, and a retrospective draft. Verified findings require HTTPS evidence links; evidence URL query credentials are redacted. The draft leaves unknown root causes, impact, and improvement items open for review. It does not execute remediation or independently prove a claim.
- The alert receiver and incident workflow are process-local prototypes. The receiver offers an authenticated task lookup by task ID and Trace ID, but has no durable task/incident storage, restart recovery, or production approval enforcement.
- The order-service accepts normalized release events at `/internal/releases` only when signed with a runtime `RELEASE_WEBHOOK_SECRET`; it writes bounded release metadata into `app_logs`. It is a local integration point, not a connected Git/CI webhook or durable release registry.
- Real OpenObserve credentials, stream permissions, and end-to-end alert behavior still need validation in the target environment.

## Tests

```bash
poetry install --with dev
poetry run pytest -q tests/plugins/toolsets/openobserve
```
