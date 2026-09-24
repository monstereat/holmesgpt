# OpenObserve + HolmesGPT AI Ops MVP

The existing `holmes/plugins/toolsets/openobserve` toolset is connected to the HolmesGPT registry on the `feature/openobserve-aiops` branch. This example documents the reproducible demonstration, without requiring a production environment.

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

## Status

The current branch implements a read-only OpenObserve logs toolset and mocked tool integration tests. Trace fetching, deploy history correlation, incident API, approval workflow and production-ready dashboards are separate milestones and are not complete yet.

## Tests

```bash
poetry install --with dev
poetry run pytest -q tests/plugins/toolsets/openobserve
```
