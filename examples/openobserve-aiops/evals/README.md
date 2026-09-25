# Known-root-cause evaluation

`known_root_causes.json` contains 20 synthetic, bounded evidence bundles for the OpenObserve + HolmesGPT order-service demo. Each case records its source ID, alert input, evidence, reference diagnosis, expected findings, unsupported claims, and a safe next step. The cluster labels are a curated taxonomy of these fixtures, not measured model results.

## Run a mock report

From the repository root:

```bash
python examples/openobserve-aiops/evals/run_evals.py --mode mock --output /tmp/holmes-aiops-mock-report.json
```

Mock mode is deterministic fixture replay. It does not call Holmes or OpenObserve; `diagnosis` remains `null`, evidence is labeled `synthetic_fixture`, and scoring is `not_scored`. It does not measure model accuracy, evidence citation quality, false-remediation rate, or recovery time. The JSON output follows `report.schema.json`.

Run corpus, duplicate-key, and fixture-link checks with:

```bash
poetry run pytest -q --no-cov examples/openobserve-aiops/evals/test_known_root_causes.py
```

## Run a live Holmes investigation

Live mode sends one Holmes request per case (up to 20 requests and model charges), using the case alert and trace IDs to query the configured OpenObserve toolsets. It requires a reachable Holmes API and model credentials configured for Holmes. In this local OSS Compose stack, Holmes reaches OpenObserve through the isolated policy proxy; OpenObserve OSS itself has no native RBAC or dedicated read-only service account. Set `HOLMES_API_URL` and `HOLMES_API_KEY` in the caller's environment, and configure `DEEPSEEK_API_KEY` for the Holmes container, then run:

```bash
python examples/openobserve-aiops/evals/run_evals.py --mode live --confirm-live --output /tmp/holmes-aiops-live-report.json
```

`--confirm-live` is required because this makes external model requests. Case inputs and their reference diagnoses remain synthetic even in live mode; only retrieved evidence is labeled `live_openobserve`. A successful API response is not an accuracy score, and an error or empty evidence is recorded per case. The corpus trace IDs are fixture values, so a live OpenObserve deployment may return no matching data. A live run must not be described as production validation unless it uses representative, authorized telemetry.

The Compose example configures LiteLLM as `deepseek/deepseek-flash`; DeepSeek's current API model ID is `deepseek-flash`, which supports tool calls according to the [official model documentation](https://api-docs.deepseek.com/quick_start/pricing/). The `deepseek/` prefix selects LiteLLM's DeepSeek provider.

## Release and Runbook fixtures

`../runbooks/evaluation-contexts.json` links three release snippets to their exact corpus cases and relevant runbooks. Each snippet is copied from synthetic fixture evidence, has no invented commit SHA, and is marked `synthetic_fixture`. The order-service inventory Skill is linked only for the inventory case; no unrelated case is presented as covered by that Skill.
