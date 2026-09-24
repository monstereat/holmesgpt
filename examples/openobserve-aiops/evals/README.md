# Known-root-cause evidence corpus

`known_root_causes.json` contains 20 synthetic, bounded evidence bundles for the OpenObserve + HolmesGPT order-service demo. Each case records the alert symptom, observed evidence, expected diagnosis, claims the evidence does not support, and a safe follow-up.

The corpus is deterministic input for offline review and future replay adapters. It does not inject faults into a running service, call Holmes, or measure model accuracy, evidence citation quality, false-remediation rate, or recovery time. Do not report those evaluation metrics from this dataset alone. Live evaluation still needs a Holmes runtime, model credentials, and an OpenObserve environment with the required read-only stream access.

Validate the corpus from the repository root:

```bash
poetry run pytest -q --no-cov examples/openobserve-aiops/evals/test_known_root_causes.py
```
