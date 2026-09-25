---
name: order-service-inventory-failure
description: Read-only diagnosis of order-service HTTP 500 errors, inventory dependency failures, correlated browser/server traces, and nearby release events.
---

# Order Service Inventory Failure Investigation

## Goal

Diagnose an order-service HTTP 500 using read-only OpenObserve evidence. This skill is an investigation guide; it never authorizes a restart, rollback, configuration change, or other write action.

## Workflow

1. Find the exact `trace_id` in `app_logs` with `openobserve_find_trace` and a bounded window around the alert time. If no trace ID is available, search `app_logs` for `service = 'order-service'` and `level = 'error'` with an explicit short time range. Only the `app_logs` and `frontend_errors` streams are in scope.
2. Correlate the same trace ID in `frontend_errors`. Identify the earliest failing server operation from returned records; do not infer a backend cause from a browser error alone.
3. Search `app_logs` for `event_type = 'release_deployed'` and `service = 'order-service'` around the incident time. Report `release`, `commit_sha`, and `changed_files` only when a matching event is returned. A nearby timestamp alone does not prove causation.
4. Compare the first error time, release time, and trace evidence. State whether the evidence supports a release correlation, contradicts it, or is insufficient.

## Synthesize Findings

- Separate direct log and trace evidence from assumptions.
- Include source links or exact trace IDs for every verified claim.
- If a stream, event, or field is unavailable, state that it could not be verified.
- Do not repeat credentials, tokens, or sensitive payload values found in logs.

## Recommended Next Steps

Provide read-only diagnostic findings and list any proposed remediation for a human operator. Do not execute remediation commands or treat this skill as approval.
