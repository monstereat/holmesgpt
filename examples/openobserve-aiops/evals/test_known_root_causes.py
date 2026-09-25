import json
from pathlib import Path


CORPUS_PATH = Path(__file__).with_name("known_root_causes.json")
EXAMPLE_PATH = CORPUS_PATH.parents[1]
REQUIRED_FIELDS = {
    "id",
    "alert",
    "service",
    "symptom",
    "evidence",
    "root_cause",
    "expected_findings",
    "unsupported_claims",
    "safe_next_step",
}


def test_known_root_cause_corpus_has_twenty_actionable_cases():
    cases = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == len(cases)

    for case in cases:
        assert REQUIRED_FIELDS <= case.keys(), case.get("id")
        for field in REQUIRED_FIELDS - {"evidence", "expected_findings", "unsupported_claims"}:
            assert isinstance(case[field], str) and case[field].strip(), (case["id"], field)
        for field in ("evidence", "expected_findings", "unsupported_claims"):
            assert isinstance(case[field], list) and case[field], (case["id"], field)
            assert all(isinstance(item, str) and item.strip() for item in case[field]), (case["id"], field)


def test_evaluation_release_and_runbook_contexts_are_grounded_in_fixture_evidence():
    cases = {case["id"]: case for case in json.loads(CORPUS_PATH.read_text(encoding="utf-8"))}
    contexts = json.loads((EXAMPLE_PATH / "runbooks" / "evaluation-contexts.json").read_text(encoding="utf-8"))

    assert contexts["source"] == "examples/openobserve-aiops/evals/known_root_causes.json"
    assert len(contexts["contexts"]) == 3
    for context in contexts["contexts"]:
        case = cases[context["case_id"]]
        assert context["mode"] == "synthetic_fixture"
        assert context["release_evidence"] in case["evidence"]
        runbook_path = EXAMPLE_PATH.parent.parent / context["runbook"]
        assert runbook_path.is_file()
        if context["skill"]:
            assert (EXAMPLE_PATH.parent.parent / context["skill"]).is_file()
