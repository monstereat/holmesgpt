---
name: order-service-inventory-failure
description: Read-only diagnosis of order-service HTTP 500 errors, inventory dependency failures, correlated browser/server traces, and nearby release events.
---

# Order Service Inventory Failure Investigation

## Goal

Diagnose an order-service HTTP 500 using read-only OpenObserve evidence. This skill is an investigation guide; it never authorizes a restart, rollback, configuration change, or other write action.

## Workflow

1. Find the exact `trace_id` once in `app_logs` with `openobserve_find_trace` and a tight window around the alert time. If no trace ID is available, run one `app_logs` search for `service = 'order-service'` and `level = 'error'` with an explicit short time range. Only the `app_logs` and `frontend_errors` streams are in scope.
2. Query the same trace in `frontend_errors` only when the alert or returned evidence indicates a browser-side error. Identify the earliest failing server operation from returned records; do not infer a backend cause from a browser error alone.
3. When an alert time is present, make at most one `app_logs` search for `event_type = 'release_deployed'` and `service = 'order-service'` near that time. Report `release`, `commit_sha`, and `changed_files` only when a matching event is returned. A nearby timestamp alone does not prove causation.
4. Stop once the exact trace and any relevant release evidence have been checked. Do not repeat a successful query, broaden the time window, call shell/bash tools, or read local files. Use no more than three OpenObserve calls.

## Synthesize Findings

- Separate direct log and trace evidence from assumptions.
- Include source links or exact trace IDs for every verified claim.
- If a stream, event, or field is unavailable, state that it could not be verified.
- Do not repeat credentials, tokens, or sensitive payload values found in logs.
- End with a concise human-readable finding, exact evidence, uncertainty, and one safe next step. Do not expose tool-call markup as the diagnosis.

## Recommended Next Steps

Provide read-only diagnostic findings and list any proposed remediation for a human operator. Do not execute remediation commands or treat this skill as approval.
