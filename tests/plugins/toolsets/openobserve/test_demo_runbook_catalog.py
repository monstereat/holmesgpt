import json
from pathlib import Path


RUNBOOK_DIR = (
    Path(__file__).resolve().parents[4]
    / "examples"
    / "openobserve-aiops"
    / "runbooks"
)


def test_demo_runbook_catalog_links_to_read_only_order_service_guide():
    catalog = json.loads((RUNBOOK_DIR / "catalog.json").read_text())
    entries = catalog["catalog"]

    assert len(entries) == 1
    assert entries[0]["id"] == "order-service-inventory-failure"
    content = (RUNBOOK_DIR / entries[0]["link"]).read_text()
    assert "openobserve_find_trace" in content
    assert "release_deployed" in content
    assert "never authorizes" in content
    assert "Do not execute remediation commands" in content
