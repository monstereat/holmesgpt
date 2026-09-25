from pathlib import Path

from holmes.plugins.skills.skill_loader import scan_skill_directory

SKILL_DIR = (
    Path(__file__).resolve().parents[4]
    / "examples"
    / "openobserve-aiops"
    / "skills"
)


def test_demo_order_service_skill_loads_through_holmes_skill_scanner():
    skills = scan_skill_directory(SKILL_DIR)

    assert {skill.name for skill in skills} == {
        "order-service-inventory-failure",
        "order-service-database-schema-mismatch",
        "order-service-release-regression",
    }
    inventory = next(skill for skill in skills if skill.name == "order-service-inventory-failure")
    database = next(skill for skill in skills if skill.name == "order-service-database-schema-mismatch")
    release = next(skill for skill in skills if skill.name == "order-service-release-regression")

    assert "HTTP 500" in inventory.description
    assert "openobserve_find_trace" in inventory.content
    assert "release_deployed" in inventory.content
    assert "never authorizes" in inventory.content
    assert "Do not execute remediation commands" in inventory.content
    assert "An absent result means" in database.content
    assert "Do not claim causality from timing alone" in release.content
