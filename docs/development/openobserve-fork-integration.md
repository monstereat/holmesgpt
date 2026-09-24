# OpenObserve read-only connector (fork extension)

This fork adds a first-party `openobserve` HolmesGPT toolset. The Agent can search logs by an existing Trace ID in a configured stream; it cannot submit arbitrary SQL or issue remediation commands.

## Configuration

In your Holmes configuration (toolsets section):

```yaml
toolsets:
  openobserve:
    enabled: true
    config:
      api_url: "https://observe.example.com"
      organization: "company"
      stream: "application"
      email: "robot@example.com"
      api_key: "<load from secret manager; never commit a real value>"
```

Use a read-only OpenObserve service account. Disable forwarding logs containing secrets or personal information to external LLMs.

Log records must contain a `trace_id` field; instrument frontend and NestJS services using OpenTelemetry and consistently propagate request trace context. Start with a 30-minute window around a known failed request.

### Verification

```bash
poetry install --with dev
poetry run pytest tests/plugins/toolsets/openobserve/test_openobserve.py -q
```

### Scope

Currently implements one scoped read-only log lookup tool. Planned follow-ups: metric and trace adapters, cross-release correlation, incident task persistence, human-reviewed remediation and ground-truth fault evaluations.
