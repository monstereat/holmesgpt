# OpenObserve toolset

This branch adds a read-only OpenObserve data source to HolmesGPT.

## Features

- Lists log streams and their schemas.
- Searches logs via `POST /api/{organization}/_search`.
- Uses OpenObserve service-account HTTP Basic authentication.
- Requires an explicit microsecond time range.
- Rejects non-read-only and multi-statement SQL.
- Caps rows before returning data to the LLM.

## Configuration

```yaml
toolsets:
  openobserve:
    enabled: true
    config:
      api_url: "https://observe.example.com"
      organization: "default"
      username: "holmes@example.com"
      password: "{{ env.OPENOBSERVE_SERVICE_TOKEN }}"
      verify_ssl: true
      timeout_seconds: 30
      max_rows: 200
```

Use a dedicated OpenObserve service account with the minimum read permissions needed for the streams HolmesGPT investigates. Do not use an ingestion-only token for search.
