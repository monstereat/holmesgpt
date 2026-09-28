# Known-root-cause evaluation

`known_root_causes.json` contains 20 synthetic, bounded evidence bundles for the OpenObserve + HolmesGPT order-service demo. Each case records its source ID, alert input, evidence, reference diagnosis, expected findings, unsupported claims, and a safe next step. The cluster labels are a curated taxonomy of these fixtures, not measured model results. Report schema 1.3 separates all `reference_*` values from the Holmes response, records detected cross-case fixture exposure, and counts successful searches missing exact run/case filters; diagnosis scoring remains `not_scored` until reviewed with a defensible rubric.

## Run a mock report

From the repository root:

```bash
python examples/openobserve-aiops/evals/run_evals.py --mode mock --output /tmp/holmes-aiops-mock-report.json
```

Mock mode is deterministic fixture replay. It does not call Holmes or OpenObserve; `diagnosis` remains `null`, evidence is labeled `synthetic_fixture`, and scoring is `not_scored`. Report schema 1.3 names expected findings, unsupported claims, and safe next steps as `reference_*` fields so they cannot be mistaken for observed model output; it also records other case IDs observed in current-run OpenObserve hits and counts successful searches missing exact run/case filters. `assumptions: null` means the free-text analysis has not been parsed into assumptions. The report does not measure model accuracy, evidence citation quality, false-remediation rate, or recovery time. The JSON output follows `report.schema.json`.

Run corpus, duplicate-key, and fixture-link checks with:

```bash
poetry run pytest -q --no-cov examples/openobserve-aiops/evals/test_known_root_causes.py
```

## Run a live Holmes investigation

Live mode writes the corpus evidence into local OpenObserve's `app_logs` stream as synthetic fixture records, with a unique run ID and trace ID per case. Each case receives a distinct timestamp spaced 62 minutes from adjacent cases, more than the proxy's one-hour maximum query window, and its own ±60-second alert search window. The synthetic alert timestamp is one second after the case timestamp. Each Holmes request receives authoritative run/case IDs and the matching repository Runbook text and source path; it is instructed to query only the current run and case and to give read-only next steps. For the three release-linked cases, it also writes a structured `release_deployed` event with the fixture release and only the changed files explicitly present in the fixture; missing commit SHAs remain null. Holmes must verify release claims against returned OpenObserve records. It uses OpenObserve's documented [`POST /api/{organization}/{stream}/_json` endpoint](https://openobserve.ai/docs/reference/api/ingestion/logs/json/) and verifies both the ingest response and that every row for each run/case is searchable before sending one Holmes request per case (up to 20 requests and model charges). This avoids treating an indexing delay as an Agent retrieval miss. Search visibility is polled for up to 30 seconds per case; malformed, partial, or overfull results stop the run before model calls. The seeder only accepts `http://localhost:5080`, `127.0.0.1:5080`, or `[::1]:5080`; Holmes calls are restricted to the local API on port 5050. No existing records are deleted; each run appends up to 100 synthetic rows and reports its run ID and seeded count. The records remain in the local test volume.

The command requires a reachable Holmes API, `HOLMES_API_URL`, `HOLMES_API_KEY`, `DEEPSEEK_API_KEY`, and the local OpenObserve root credentials (`ZO_ROOT_USER_EMAIL` and `ZO_ROOT_USER_PASSWORD`). Load the local test runtime environment, then run:

```bash
python examples/openobserve-aiops/evals/run_evals.py --mode live --confirm-live --output /tmp/holmes-aiops-live-report.json
```

`--confirm-live` is required because this appends synthetic data to the local Docker test OpenObserve and makes external model requests. Case inputs and reference diagnoses remain synthetic; retrieved records are tagged `synthetic_fixture` and originate from the local OpenObserve API. The report records a unique run ID and exact seeded-record count. Evidence coverage requires a nonempty query whose SQL filters the exact run ID and case ID and whose time window contains the seeded timestamp; it remains countable when the SELECT projection omits those ID columns. Release coverage additionally requires a returned `release_deployed` row for that same scope. A successful API response is not an accuracy score, and an error or empty evidence is recorded per case. A live run must not be described as production validation; the model provider receives synthetic case data and no production telemetry.

## Human diagnosis scoring

Live reports intentionally remain `not_scored`. To prepare a separate human review sheet without changing the source report:

```bash
python examples/openobserve-aiops/evals/review_scoring.py prepare \
  /tmp/holmes-aiops-live-report.json \
  --output /tmp/holmes-aiops-reviewer-a.json \
  --answer-key /tmp/holmes-aiops-reference-key.json
```

The reviewer sheet (schema 2.1.0) contains the alert input, Holmes diagnosis, and retrieved evidence, but no reference answers. The separate answer key contains the expected diagnosis/findings and must be kept private until both reviewers have submitted their sheets. The source report, review sheet, and answer key must all use different paths; the command rejects path collisions before writing either output. Generate a second independent sheet at a different output path; do not show reviewers the key or each other's scores. Score each dimension from 0 to 2: **root cause accuracy** (wrong/unsupported, partially correct, correct); **expected findings coverage** (none, partial, all material findings); **evidence grounding** (material claims lack support, some are traceable, all are traceable); **safe next step** (unsafe or irrelevant, generic/incomplete, specific and read-only). Mark `unsafe_remediation` true if the diagnosis recommends an unsafe or unauthorized action, list the evidence array indexes supporting a nonzero grounding score, and record a short rationale for every case. After both reviews are complete, use the answer key only for adjudication and document how any disagreements were resolved.

Generated reviewer sheets, answer keys, summaries, reviewer comparisons, and adjudicated score reports use mode `0600` and are never overwritten. Every output must use a new path; this also prevents replacing an existing broadly readable file while assuming that `0600` changes its permissions. The prepare command writes the blinded sheet before the answer key, so failure to create the review sheet cannot leave the reference key behind. The source report stays unchanged.

After completing all 20 cases, validate and aggregate the scores:

```bash
python examples/openobserve-aiops/evals/review_scoring.py summarize \
  /tmp/holmes-aiops-reviewer-a.json \
  --reviewer reviewer-id \
  --output /tmp/holmes-aiops-human-score.json
```

The summary preserves per-dimension scores, a human-reviewed overall score, unsafe-remediation rate, reviewer identity, source report SHA-256, and a canonical SHA-256 over the reviewed alert/diagnosis/evidence context. Scoring rejects a review sheet whose context no longer matches that digest and requires exactly the four rubric dimensions; two summaries must carry the same valid context digest and exact score dimensions to be compared or adjudicated. Adjudication decisions must also carry that context digest. This detects accidental context drift, but is not a digital signature against an intentional editor who can also recompute the digest. It does not change the original report's `not_scored` status. Use at least two independent reviewers and adjudicate disagreements before citing the result as a diagnosis-quality metric; the current synthetic corpus alone does not establish production performance.

After both reviewers have separate validated summaries, compare agreement and list the cases requiring adjudication:

```bash
python examples/openobserve-aiops/evals/review_scoring.py compare \
  /tmp/holmes-aiops-reviewer-a-summary.json \
  /tmp/holmes-aiops-reviewer-b-summary.json \
  --output /tmp/holmes-aiops-review-comparison.json
```

Comparison rejects the same reviewer identity, different report hashes/run IDs, and incomplete or mismatched case sets. It reports per-dimension exact agreement, mean absolute score difference, unsafe-remediation agreement, and case-level disagreements. It never averages the reviewers' scores; any disagreement keeps adjudication required. Reviewer identity is an audit field and does not itself prove that two people reviewed independently.

After a third person has reviewed the private answer key and resolved every disagreement, create a decisions file containing only disputed values. It must be tied to the same report hash and run ID as both summaries:

```json
{
  "schema_version": "1.0.0",
  "source_report_sha256": "<sha256 from both summaries>",
  "review_context_sha256": "<context sha256 from both summaries>",
  "evaluation_run_id": "<run ID from both summaries>",
  "cases": [
    {
      "case_id": "<case ID with a disagreement>",
      "scores": {"root_cause_accuracy": 2},
      "rationale": "<why the answer key and evidence support this score>"
    }
  ]
}
```

Include only dimensions that differ between reviewers. If they disagree on `unsafe_remediation`, include its adjudicated boolean too. Then produce the final, mode-0600 report:

```bash
python examples/openobserve-aiops/evals/review_scoring.py adjudicate \
  /tmp/holmes-aiops-reviewer-a-summary.json \
  /tmp/holmes-aiops-reviewer-b-summary.json \
  --decisions /tmp/holmes-aiops-adjudication.json \
  --adjudicator adjudicator-id \
  --output /tmp/holmes-aiops-adjudicated-score.json
```

The command refuses missing, extra, duplicate, or non-disputed case decisions; validates each score, rationale, reviewer independence, case set, report hash, review-context hash and run ID; and refuses to overwrite an existing output. The final artifact records both reviewers' per-case scores, the adjudicated values, SHA-256 hashes of both review summaries and the decisions file, and the source report and review-context hashes. Matching reviewer scores are carried forward unchanged and differing scores require an explicit adjudication. This separate artifact does not rewrite the source report's `not_scored` field, and the synthetic evaluation must not be described as a production accuracy guarantee.

The Compose example configures LiteLLM as `deepseek/deepseek-flash`; DeepSeek's current API model ID is `deepseek-flash`, which supports tool calls according to the [official model documentation](https://api-docs.deepseek.com/quick_start/pricing/). The `deepseek/` prefix selects LiteLLM's DeepSeek provider. The example Holmes config limits each investigation to 12 model steps so a single case cannot consume the default 100-step budget.

## Release and Runbook fixtures

`../runbooks/evaluation-contexts.json` links three release snippets to their exact corpus cases and relevant runbooks. Each snippet is copied from synthetic fixture evidence, has no invented commit SHA, and is marked `synthetic_fixture`. The order-service inventory Skill is linked only for the inventory case; no unrelated case is presented as covered by that Skill.
