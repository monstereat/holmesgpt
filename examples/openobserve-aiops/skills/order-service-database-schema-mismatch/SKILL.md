---
name: order-service-database-schema-mismatch
description: Investigate order-service write failures caused by a mismatch between application releases and database schema or migration events.
---

# Order Service Database Schema Mismatch

## Scope

Use only the configured read-only OpenObserve tools. This Skill cannot inspect the database directly and does not authorize applying migrations, changing schema, or rolling back a release.

## Investigation

1. Find the failing request by its exact `trace_id` in `app_logs` using a bounded time range.
2. Identify the returned database error and the application release. Search for `release_deployed` events around the alert window; report only fields present in returned records.
3. Search the same window for an explicit migration event. An absent result means “not observed”; it does not prove a migration failed or that the schema is wrong.
4. Compare the application rollout and migration timing. Separate direct log evidence from assumptions and state when database-owner verification is required.

## Report

Include the trace ID, exact error, observed release, migration-event search result, and uncertainty. Do not invent a column, commit SHA, changed file, or migration status. Route any schema check or change through the database owner.
