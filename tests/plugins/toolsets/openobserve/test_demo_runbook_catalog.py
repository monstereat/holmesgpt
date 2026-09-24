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

    assert len(skills) == 1
    skill = skills[0]
    assert skill.name == "order-service-inventory-failure"
    assert "HTTP 500" in skill.description
    assert "openobserve_find_trace" in skill.content
    assert "release_deployed" in skill.content
    assert "never authorizes" in skill.content
    assert "Do not execute remediation commands" in skill.content
