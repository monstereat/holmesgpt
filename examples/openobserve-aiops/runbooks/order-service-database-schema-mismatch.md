# Order Service Database Schema Mismatch

## Goal

Investigate order-write failures where the deployed application expects a database column that the observed schema does not provide. The synthetic evaluation fixture references `delivery_window` and release `v1.2.0`; that fixture is not a claim about a live database.

## Workflow

1. Correlate the failing request trace with the database error and the application release.
2. Check the migration history and deployed schema with the database owner. Treat an absent event as “not observed,” not proof that a migration failed.
3. Compare application rollout and migration ordering, including whether old and new instances overlap.
4. Record the affected release, exact column error, time window, and any uncertainty.

## Safety

Use read-only evidence during investigation. Do not apply a migration, edit schema, or roll back a release from this runbook; route any change through the database and release owners' normal approval process.
