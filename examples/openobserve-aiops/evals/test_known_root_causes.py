import json
from pathlib import Path


CORPUS_PATH = Path(__file__).with_name("known_root_causes.json")
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
