# Live evaluation reports

This directory stores immutable output from live AIOps evaluation runs against synthetic cases and local test telemetry. Name reports `live-YYYY-MM-DD.json`; keep the original run output unchanged so its SHA-256 remains a stable reference for scoring sheets.

Reports must not contain production telemetry or credentials. The report schema intentionally keeps diagnosis scoring at `not_scored`; retrieval coverage does not establish root-cause accuracy.

Create separate review sheets for independent reviewers from a saved report:

```bash
python examples/openobserve-aiops/evals/review_scoring.py prepare \
  examples/openobserve-aiops/evals/reports/live-YYYY-MM-DD.json \
  --output /tmp/holmes-aiops-reviewer-a.json
```

Generate a second independent sheet with a different output path. Do not prefill scores or share one reviewer's completed sheet with another before both reviews are finished. Keep reviewer identities and adjudicated summaries separate from the source report.
