"""Dependency-free, server-side incident approval example.

HolmesGPT investigates using read-only tools. This workflow stores decisions;
it NEVER executes a remediation command. A separate operator-controlled executor
must recheck the approval, action allowlist and idempotency before doing anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALLOWED_TRANSITIONS = {
    "detected": frozenset({"investigating"}),
    "investigating": frozenset({"awaiting_approval", "failed"}),
    "awaiting_approval": frozenset({"remediating", "investigating"}),
    "remediating": frozenset({"resolved", "failed"}),
    "failed": frozenset({"investigating"}),
    "resolved": frozenset(),
}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Incident:
    incident_id: str
    service: str
    idempotency_key: str
    severity: str = "medium"
    owner_id: str | None = None
    status: str = "detected"
    evidence_links: list[str] = field(default_factory=list)
    proposed_action: str | None = None
    approver_ids: frozenset[str] = field(default_factory=frozenset)
    events: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    approved_by: str | None = None
    created_at: str = field(default_factory=timestamp)
    resolved_at: str | None = None
    impact_scope: str | None = None
    long_term_improvements: list[str] = field(default_factory=list)
    improvements_reviewed: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.incident_id, str)
            or not self.incident_id.strip()
            or not isinstance(self.service, str)
            or not self.service.strip()
        ):
            raise ValueError("incident_id and service are required")
        if (
            not isinstance(self.idempotency_key, str)
            or not self.idempotency_key.strip()
            or len(self.idempotency_key) > 200
        ):
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        if self.severity not in {"critical", "high", "medium", "low"}:
            raise ValueError("severity must be critical, high, medium, or low")

    def _transition(self, next_status: str, actor: str, reason: str) -> None:
        if next_status not in ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"Illegal transition: {self.status} -> {next_status}")
        self.events.append({
            "time": timestamp(), "actor": actor, "from": self.status,
            "to": next_status, "reason": reason,
        })
        self.status = next_status
        if next_status == "resolved":
            self.resolved_at = self.events[-1]["time"]

    def assign(self, owner_id: str, *, actor: str) -> None:
        if self.status == "resolved":
            raise ValueError("Resolved incidents cannot be reassigned")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id is required")
        previous_owner = self.owner_id
        self.owner_id = owner_id
        self.events.append({
            "time": timestamp(), "actor": actor, "from": self.status,
            "to": self.status, "reason": "owner_assigned",
            "previous_owner": previous_owner, "owner_id": owner_id,
        })

    def record_finding(
        self, *, actor: str, claim: str, evidence_links: list[str], verified: bool,
    ) -> None:
        if self.status != "investigating":
            raise ValueError("Findings can only be recorded during investigation")
        if not isinstance(claim, str) or not claim.strip() or len(claim) > 1000:
            raise ValueError("Finding claim must contain 1 to 1000 characters")
        if len(self.findings) >= 50:
            raise ValueError("Incident cannot contain more than 50 findings")
        if not isinstance(verified, bool):
            raise ValueError("verified must be a boolean")
        if not isinstance(evidence_links, list) or len(evidence_links) > 10 or any(
            self._sanitize_evidence_link(link) is None for link in evidence_links
        ):
            raise ValueError("Finding evidence must contain up to 10 HTTPS links")
        if verified and not evidence_links:
            raise ValueError("Verified findings require evidence links")

        classification = "evidence" if verified else "assumption"
        self.findings.append({
            "claim": claim.strip(),
            "classification": classification,
            "evidence_links": [
                self._sanitize_evidence_link(link) for link in evidence_links
            ],
            "actor": actor,
            "time": timestamp(),
        })
        self.events.append({
            "time": timestamp(), "actor": actor, "from": self.status,
            "to": self.status, "reason": "finding_recorded",
            "classification": classification,
        })

    def start_investigation(self, actor: str) -> None:
        self._transition("investigating", actor, "investigation_started")

    def propose(self, actor: str, action: str, evidence_links: list[str]) -> None:
        if self.status != "investigating":
            raise ValueError("Incident must be under investigation")
        if action not in {"rollback_order_service", "restart_order_service"}:
            raise ValueError("Remediation action is not on the allowlist")
        if not evidence_links or len(evidence_links) > 20 or any(
            self._sanitize_evidence_link(link) is None for link in evidence_links
        ):
            raise ValueError("Evidence must contain HTTPS links")
        self.proposed_action = action
        self.evidence_links = [
            self._sanitize_evidence_link(link) for link in evidence_links
        ]
        self.approved_by = None
        self._transition("awaiting_approval", actor, "proposal_submitted")

    @staticmethod
    def _sanitize_evidence_link(link: object) -> str | None:
        if not isinstance(link, str):
            return None
        if len(link) > 2048:
            return None
        try:
            parsed = urlsplit(link)
            if (
                parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password
            ):
                return None
            query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=50)
            safe_query = [
                (key, "[REDACTED]" if any(
                    marker in key.lower().replace("_", "").replace("-", "")
                    for marker in (
                        "token", "password", "secret", "apikey", "authorization",
                        "accesskey", "credential", "signature",
                    )
                ) else value)
                for key, value in query
            ]
            return urlunsplit((
                parsed.scheme, parsed.netloc, parsed.path,
                urlencode(safe_query), "",
            ))
        except ValueError:
            return None

    def decide(
        self, *, actor: str, approve: bool,
        authorize: Callable[[str, str, str], bool],
    ) -> None:
        if self.status != "awaiting_approval":
            raise ValueError("Incident is not awaiting approval")
        if actor not in self.approver_ids or not authorize(
            actor, self.service, "incident:approve"
        ):
            self.events.append({
                "time": timestamp(), "actor": actor, "from": self.status,
                "to": self.status, "reason": "approval_denied",
            })
            raise PermissionError("Reviewer is not authorized")
        if approve:
            self.approved_by = actor
            self._transition("remediating", actor, "approved")
        else:
            self.proposed_action = None
            self.approved_by = None
            self._transition("investigating", actor, "rejected")

    def record_verified_result(self, actor: str, *, successful: bool) -> None:
        if self.status != "remediating" or self.approved_by is None:
            raise ValueError("Remediation is not approved or in progress")
        self._transition(
            "resolved" if successful else "failed", actor,
            "verified_recovery" if successful else "verification_failed",
        )

    def record_impact_scope(self, actor: str, scope: str) -> None:
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 1000:
            raise ValueError("Impact scope must contain 1 to 1000 characters")
        self.impact_scope = scope.strip()
        self.events.append({
            "time": timestamp(), "actor": actor, "from": self.status,
            "to": self.status, "reason": "impact_scope_recorded",
        })

    def record_long_term_improvements(
        self, actor: str, items: list[str],
    ) -> None:
        if not isinstance(items, list) or len(items) > 20 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 500
            for item in items
        ):
            raise ValueError("Provide up to 20 improvement items of 1 to 500 characters")
        self.long_term_improvements = list(dict.fromkeys(item.strip() for item in items))
        self.improvements_reviewed = True
        self.events.append({
            "time": timestamp(), "actor": actor, "from": self.status,
            "to": self.status, "reason": "improvements_reviewed",
            "item_count": len(self.long_term_improvements),
        })

    def redacted_report(self) -> dict:
        """Return state and sanitized evidence links, never raw logs."""
        return {
            "incident_id": self.incident_id,
            "idempotency_key": self.idempotency_key,
            "service": self.service,
            "severity": self.severity,
            "owner_id": self.owner_id,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "impact_scope": self.impact_scope,
            "long_term_improvements": self.long_term_improvements.copy(),
            "improvements_reviewed": self.improvements_reviewed,
            "evidence_links": self.evidence_links.copy(),
            "findings": [finding.copy() for finding in self.findings],
            "proposed_action": self.proposed_action,
            "approved_by": self.approved_by,
            "timeline": [event.copy() for event in self.events],
        }

    def retrospective_draft(self) -> dict:
        """Build a review draft from recorded facts without inventing missing data."""
        verified_findings = [
            finding for finding in self.findings
            if finding["classification"] == "evidence"
        ]
        process_steps = [
            event.copy() for event in self.events
            if event["reason"] in {
                "investigation_started", "proposal_submitted", "approved",
                "rejected", "verified_recovery", "verification_failed",
            }
        ]
        recovery_seconds: float | None = None
        if self.resolved_at:
            detected = datetime.fromisoformat(self.created_at)
            recovered = datetime.fromisoformat(self.resolved_at)
            recovery_seconds = max(0.0, (recovered - detected).total_seconds())

        open_questions = []
        if not verified_findings:
            open_questions.append("Root cause needs evidence-backed review")
        if self.status != "resolved":
            open_questions.append("Recovery time is not confirmed")
        if not process_steps:
            open_questions.append("Incident handling steps need review")
        if self.impact_scope is None:
            open_questions.append("Impact scope needs operator input")
        if not self.improvements_reviewed:
            open_questions.append("Long-term improvement items need operator input")

        return {
            "incident_id": self.incident_id,
            "service": self.service,
            "status": "ready_for_review" if not open_questions else "draft",
            "root_cause": verified_findings[0]["claim"] if verified_findings else None,
            "root_cause_evidence": [
                link for finding in verified_findings
                for link in finding["evidence_links"]
            ],
            "impact_scope": self.impact_scope,
            "detected_at": self.created_at,
            "recovered_at": self.resolved_at,
            "time_to_recovery_seconds": recovery_seconds,
            "handling_steps": process_steps,
            "long_term_improvements": self.long_term_improvements.copy(),
            "improvements_reviewed": self.improvements_reviewed,
            "open_questions": open_questions,
        }


class IncidentRegistry:
    """Process-local idempotency and lookup for the approval example."""

    def __init__(self) -> None:
        self._incidents: dict[str, Incident] = {}
        self._idempotency: dict[str, str] = {}

    def create(
        self, *, incident_id: str, service: str, idempotency_key: str,
        severity: str = "medium",
    ) -> Incident:
        existing_id = self._idempotency.get(idempotency_key)
        if existing_id:
            incident = self._incidents[existing_id]
            if incident.service != service:
                raise ValueError("Idempotency key was already used for another service")
            incident.events.append({
                "time": timestamp(), "actor": "system", "from": incident.status,
                "to": incident.status, "reason": "duplicate_alert_ignored",
            })
            return incident
        if incident_id in self._incidents:
            raise ValueError("incident_id already exists")
        incident = Incident(
            incident_id=incident_id,
            service=service,
            idempotency_key=idempotency_key,
            severity=severity,
        )
        self._incidents[incident_id] = incident
        self._idempotency[idempotency_key] = incident_id
        return incident

    def get(self, incident_id: str) -> Incident:
        return self._incidents[incident_id]
