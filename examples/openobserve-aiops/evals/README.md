# Known-root-cause evaluation

`known_root_causes.json` contains 20 synthetic, bounded evidence bundles for the OpenObserve + HolmesGPT order-service demo. Each case records its source ID, alert input, evidence, reference diagnosis, expected findings, unsupported claims, and a safe next step. The cluster labels are a curated taxonomy of these fixtures, not measured model results. Report schema 1.2 separates all `reference_*` values from the Holmes response, while diagnosis scoring remains `not_scored` until reviewed with a defensible rubric.

## Run a mock report

From the repository root:

```bash
python examples/openobserve-aiops/evals/run_evals.py --mode mock --output /tmp/holmes-aiops-mock-report.json
```

Mock mode is deterministic fixture replay. It does not call Holmes or OpenObserve; `diagnosis` remains `null`, evidence is labeled `synthetic_fixture`, and scoring is `not_scored`. Report schema 1.2 names expected findings, unsupported claims, and safe next steps as `reference_*` fields so they cannot be mistaken for observed model output; `assumptions: null` means the free-text analysis has not been parsed into assumptions. The report does not measure model accuracy, evidence citation quality, false-remediation rate, or recovery time. The JSON output follows `report.schema.json`.

Run corpus, duplicate-key, and fixture-link checks with:

```bash
poetry run pytest -q --no-cov examples/openobserve-aiops/evals/test_known_root_causes.py
```

## Run a live Holmes investigation

Live mode writes the corpus evidence into local OpenObserve's `app_logs` stream as synthetic fixture records, with a unique run ID and trace ID per case. The synthetic alert timestamp is one second after ingestion. Each Holmes request receives authoritative run/case IDs and a bounded ±60-second search window, plus the matching repository Runbook text and source path; it is instructed to query only the current run and case. For the three release-linked cases, it also writes a structured `release_deployed` event with the fixture release and only the changed files explicitly present in the fixture; missing commit SHAs remain null. Holmes must verify release claims against returned OpenObserve records. It uses OpenObserve's documented [`POST /api/{organization}/{stream}/_json` endpoint](https://openobserve.ai/docs/reference/api/ingestion/logs/json/) and verifies both the ingest response and that every row for the unique run ID is searchable before sending one Holmes request per case (up to 20 requests and model charges). This avoids treating an indexing delay as an Agent retrieval miss. Search visibility is polled for up to 30 seconds; malformed, partial, or overfull results stop the run before model calls. The seeder only accepts `http://localhost:5080`, `127.0.0.1:5080`, or `[::1]:5080`; Holmes calls are restricted to the local API on port 5050. No existing records are deleted; each run appends up to 100 synthetic rows and reports its run ID and seeded count. The records remain in the local test volume.

The command requires a reachable Holmes API, `HOLMES_API_URL`, `HOLMES_API_KEY`, `DEEPSEEK_API_KEY`, and the local OpenObserve root credentials (`ZO_ROOT_USER_EMAIL` and `ZO_ROOT_USER_PASSWORD`). Load the local test runtime environment, then run:

```bash
python examples/openobserve-aiops/evals/run_evals.py --mode live --confirm-live --output /tmp/holmes-aiops-live-report.json
```

`--confirm-live` is required because this appends synthetic data to the local Docker test OpenObserve and makes external model requests. Case inputs and reference diagnoses remain synthetic; retrieved records are tagged `synthetic_fixture` and originate from the local OpenObserve API. The report records a unique run ID and exact seeded-record count. Evidence coverage requires a nonempty query whose SQL filters the exact run ID and case ID and whose time window contains the seeded timestamp; it remains countable when the SELECT projection omits those ID columns. Release coverage additionally requires a returned `release_deployed` row for that same scope. A successful API response is not an accuracy score, and an error or empty evidence is recorded per case. A live run must not be described as production validation; the model provider receives synthetic case data and no production telemetry.

The Compose example configures LiteLLM as `deepseek/deepseek-flash`; DeepSeek's current API model ID is `deepseek-flash`, which supports tool calls according to the [official model documentation](https://api-docs.deepseek.com/quick_start/pricing/). The `deepseek/` prefix selects LiteLLM's DeepSeek provider. The example Holmes config limits each investigation to 12 model steps so a single case cannot consume the default 100-step budget.

## Release and Runbook fixtures

`../runbooks/evaluation-contexts.json` links three release snippets to their exact corpus cases and relevant runbooks. Each snippet is copied from synthetic fixture evidence, has no invented commit SHA, and is marked `synthetic_fixture`. The order-service inventory Skill is linked only for the inventory case; no unrelated case is presented as covered by that Skill.
