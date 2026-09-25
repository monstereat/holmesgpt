# Order Service Release Regression

## Goal

Assess whether a recent release is associated with an order-service error increase. Synthetic fixture `order-release-regression` names release `v2.4.1` and `src/orders/mapper.ts`; these are test data, not a live release record.

## Workflow

1. Find failing traces and identify the earliest server-side exception.
2. Retrieve a matching release event and report its fields only when returned by the source.
3. Compare the changed file and error with a known-good trace from the prior release.
4. State whether the evidence supports a correlation, contradicts it, or is insufficient. Correlation alone does not establish causation.

## Safety

Keep the investigation read-only. A rollback or code change requires the release owner's approval and a separate execution path.
