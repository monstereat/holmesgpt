"""Unit-level reference checks for the optional AI Ops incident service."""
import importlib.util
from pathlib import Path

import pytest

FILE = (
    Path(__file__).resolve().parents[4]
    / "examples"
    / "openobserve-aiops"
    / "incident_workflow.py"
)
spec = importlib.util.spec_from_file_location("incident_workflow", FILE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Incident = module.Incident


@pytest.fixture
def incident():
    return Incident(
        incident_id="inc-101", service="order-service",
        approver_ids=frozenset({"oncall-lead"}),
    )


def test_unapproved_remediation_is_forbidden(incident):
    with pytest.raises(ValueError, match="not approved"):
        incident.record_verified_result("executor", successful=True)
    incident.start_investigation("holmesgpt")
    incident.propose("holmesgpt", "rollback_order_service",
                     ["https://observe.example.test/logs?trace=abc"])
    with pytest.raises(ValueError, match="not approved"):
        incident.record_verified_result("executor", successful=True)


def test_investigation_approval_and_verified_recovery(incident):
    incident.start_investigation("holmesgpt")
    incident.propose("holmesgpt", "rollback_order_service",
                     ["https://observe.example.test/logs?trace=abc"])
    incident.decide(
        actor="oncall-lead", approve=True,
        authorize=lambda user, service, action: (
            user == "oncall-lead" and service == "order-service"
            and action == "incident:approve"
        ),
    )
    assert incident.status == "remediating"
    assert incident.approved_by == "oncall-lead"
    incident.record_verified_result("executor", successful=True)
    assert incident.status == "resolved"
    assert incident.redacted_report()["timeline"][-1]["reason"] == "verified_recovery"
    with pytest.raises(ValueError, match="Illegal transition"):
        incident.start_investigation("holmesgpt")


def test_unauthorized_approval_is_audited_without_progress(incident):
    incident.start_investigation("holmesgpt")
    incident.propose("holmesgpt", "rollback_order_service",
                     ["https://observe.example.test/logs"])
    with pytest.raises(PermissionError):
        incident.decide(actor="developer", approve=True,
                        authorize=lambda *args: True)
    assert incident.status == "awaiting_approval"
    assert incident.events[-1]["reason"] == "approval_denied"


def test_rejected_action_returns_to_investigation(incident):
    incident.start_investigation("holmesgpt")
    incident.propose("holmesgpt", "restart_order_service",
                     ["https://observe.example.test/logs"])
    incident.decide(actor="oncall-lead", approve=False,
                    authorize=lambda *args: True)
    assert incident.status == "investigating"
    assert incident.proposed_action is None


def test_rejects_unknown_remediation_and_missing_evidence(incident):
    incident.start_investigation("holmesgpt")
    with pytest.raises(ValueError, match="allowlist"):
        incident.propose("holmesgpt", "rm -rf /", ["https://example.test"])
    with pytest.raises(ValueError, match="Evidence"):
        incident.propose("holmesgpt", "rollback_order_service", [])
