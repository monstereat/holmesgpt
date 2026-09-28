---
name: order-service-release-regression
description: Assess whether an order-service release is associated with an HTTP error increase using correlated traces and observed release events.
---

# Order Service Release Regression

## Scope

Use only the configured read-only OpenObserve tools. A release correlation is evidence for investigation, not proof of causation or authorization to roll back.

## Investigation

1. Find the failing request by its exact `trace_id` in `app_logs` and identify the earliest server-side exception.
2. Search `app_logs` for a matching release event with `event_type = 'release_deployed'` near the alert time. `release_deployed` is a value of the `event_type` field, not a stream name: query `FROM app_logs ... WHERE event_type = 'release_deployed'`; never query `FROM release_deployed`. Report the release, commit SHA, changed files, and timestamp only when those values appear in the returned event.
3. Compare the changed code path with a known-good trace from the previous release only if one is available in the query results.
4. State whether the evidence supports a temporal/code-path correlation, contradicts it, or is insufficient. Do not claim causality from timing alone.

## Report

Cite the exact trace and release-event fields used. Mark missing commit, file, or comparison data as unavailable rather than inferring it. Keep the investigation read-only; a rollback requires the release owner's separate approval and execution path.

For evaluation cases, retain the exact `evaluation_run_id` and `evaluation_case_id` predicates joined with `AND` on every query, including release-event searches, and stay within the supplied time window.
