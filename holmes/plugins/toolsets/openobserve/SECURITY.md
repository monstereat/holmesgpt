# OpenObserve integration security and deployment scope

The \`openobserve\` toolset is read-only at the application layer and is **not** a SQL firewall. Run it under a dedicated OpenObserve service account with **read-only permissions for the intended log streams**. Treat the OpenObserve server's RBAC as the authoritative security boundary.

For production, always configure \`allowed_streams\`. When configured, LLM-supplied SQL is restricted to one simple SELECT against one listed stream. Complex SQL, joins, subqueries, CTEs, and UNION are rejected until a dialect-aware SQL parser is integrated. Omission of \`allowed_streams\` permits the legacy development search path and is not appropriate for production.

\`\`\`yaml
toolsets:
  openobserve:
    enabled: true
    config:
      api_url: "https://observe.example.com"
      organization: "prod"
      username: "readonly@example.com"
      password: "{{ env.OPENOBSERVE_SERVICE_TOKEN }}"
      allowed_streams: ["frontend_logs", "backend_logs"]
      verify_ssl: true
      max_rows: 100
      max_window_seconds: 3600
\`\`\`

Other safeguards:
- Requests never follow redirects when Basic auth is attached.
- Upstream HTTP error bodies are redacted; avoid emitting API credentials in logs.
- Stream discovery returns at most 100 streams and only allowlisted streams if configured.
- Query time windows and returned rows are bounded. Configure additional server-side scan quotas and rate limits.
- Keep raw application logs free of credentials and sensitive PII before ingesting them.
