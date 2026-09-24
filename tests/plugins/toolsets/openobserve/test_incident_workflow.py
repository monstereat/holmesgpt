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
IncidentRegistry = module.IncidentRegistry


@pytest.fixture
def incident():
    return Incident(
        incident_id="inc-101", service="order-service",
        idempotency_key="alert-101",
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


def test_owner_severity_and_idempotency_are_recorded():
    registry = IncidentRegistry()
    incident = registry.create(
        incident_id="inc-201", service="checkout", idempotency_key="alert-201",
        severity="high",
    )
    incident.assign("oncall-1", actor="dispatcher")
    duplicate = registry.create(
        incident_id="inc-202", service="checkout", idempotency_key="alert-201",
        severity="critical",
    )

    assert duplicate is incident
    report = registry.get("inc-201").redacted_report()
    assert report["severity"] == "high"
    assert report["owner_id"] == "oncall-1"
    assert report["timeline"][-1]["reason"] == "duplicate_alert_ignored"


def test_idempotency_key_cannot_merge_different_services():
    registry = IncidentRegistry()
    registry.create(incident_id="inc-301", service="orders", idempotency_key="shared-key")

    with pytest.raises(ValueError, match="another service"):
        registry.create(incident_id="inc-302", service="payments", idempotency_key="shared-key")


def test_evidence_links_reject_userinfo_and_resolved_incidents_cannot_be_reassigned(incident):
    incident.start_investigation("holmesgpt")
    with pytest.raises(ValueError, match="Evidence"):
        incident.propose(
            "holmesgpt", "rollback_order_service",
            ["https://user:password@observe.example.test/logs"],
        )

    incident.propose("holmesgpt", "rollback_order_service", ["https://observe.example.test/logs"])
    incident.decide(actor="oncall-lead", approve=True, authorize=lambda *_: True)
    incident.record_verified_result("executor", successful=True)
    with pytest.raises(ValueError, match="cannot be reassigned"):
        incident.assign("oncall-2", actor="admin")


def test_findings_distinguish_linked_evidence_from_assumptions(incident):
    incident.start_investigation("holmesgpt")
    incident.record_finding(
        actor="holmesgpt",
        claim="Inventory lookup failed on release v1.0.0",
        evidence_links=["https://observe.example.test/logs?trace=abc"],
        verified=True,
    )
    incident.record_finding(
        actor="holmesgpt", claim="A recent deployment may be related",
        evidence_links=[], verified=False,
    )

    findings = incident.redacted_report()["findings"]
    assert [finding["classification"] for finding in findings] == [
        "evidence", "assumption",
    ]
    with pytest.raises(ValueError, match="require evidence"):
        incident.record_finding(
            actor="holmesgpt", claim="Verified without a source",
            evidence_links=[], verified=True,
        )


def test_evidence_urls_redact_credentials_from_query_and_fragment(incident):
    incident.start_investigation("holmesgpt")
    incident.record_finding(
        actor="holmesgpt",
        claim="Trace search completed",
        evidence_links=[
            "https://observe.example.test/logs?trace=abc&api_key=secret-value#access_token=fragment-secret"
        ],
        verified=True,
    )
    incident.propose(
        "holmesgpt",
        "rollback_order_service",
        ["https://observe.example.test/logs?trace=abc&access_token=another-secret"],
    )

    report = incident.redacted_report()
    serialized = str(report)
    assert "secret-value" not in serialized
    assert "fragment-secret" not in serialized
    assert "another-secret" not in serialized
    assert "api_key=%5BREDACTED%5D" in serialized
    assert "access_token=%5BREDACTED%5D" in serialized
    assert "trace=abc" in serialized


def test_retrospective_draft_keeps_unknown_impact_and_root_cause_explicit(incident):
    incident.start_investigation("holmesgpt")
    incident.record_finding(
        actor="holmesgpt", claim="A deployment may be related",
        evidence_links=[], verified=False,
    )

    report = incident.retrospective_draft()

    assert report["status"] == "draft"
    assert report["root_cause"] is None
    assert report["impact_scope"] is None
    assert report["recovered_at"] is None
    assert report["time_to_recovery_seconds"] is None
    assert report["long_term_improvements"] == []
    assert "Root cause needs evidence-backed review" in report["open_questions"]
    assert "Impact scope needs operator input" in report["open_questions"]


def test_retrospective_draft_uses_only_verified_root_cause_and_recorded_timeline(incident):
    incident.start_investigation("holmesgpt")
    incident.record_finding(
        actor="holmesgpt", claim="Inventory connection was refused",
        evidence_links=["https://observe.example.test/logs?trace=abc"], verified=True,
    )
    incident.propose(
        "holmesgpt", "rollback_order_service",
        ["https://observe.example.test/logs?trace=abc"],
    )
    incident.decide(actor="oncall-lead", approve=True, authorize=lambda *_: True)
    incident.record_verified_result("executor", successful=True)

    report = incident.retrospective_draft()

    assert report["root_cause"] == "Inventory connection was refused"
    assert report["root_cause_evidence"] == [
        "https://observe.example.test/logs?trace=abc",
    ]
    assert report["recovered_at"] == incident.resolved_at
    assert report["time_to_recovery_seconds"] is not None
    assert [step["reason"] for step in report["handling_steps"]] == [
        "investigation_started", "proposal_submitted", "approved",
        "verified_recovery",
    ]
    assert "Impact scope needs operator input" in report["open_questions"]
