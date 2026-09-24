"""Dependency-free, server-side incident approval example.

HolmesGPT investigates using read-only tools. This workflow stores decisions;
it NEVER executes a remediation command. A separate operator-controlled executor
must recheck the approval, action allowlist and idempotency before doing anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

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
    status: str = "detected"
    evidence_links: list[str] = field(default_factory=list)
    proposed_action: str | None = None
    approver_ids: frozenset[str] = field(default_factory=frozenset)
    events: list[dict] = field(default_factory=list)
    approved_by: str | None = None

    def _transition(self, next_status: str, actor: str, reason: str) -> None:
        if next_status not in ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"Illegal transition: {self.status} -> {next_status}")
        self.events.append({
            "time": timestamp(), "actor": actor, "from": self.status,
            "to": next_status, "reason": reason,
        })
        self.status = next_status

    def start_investigation(self, actor: str) -> None:
        self._transition("investigating", actor, "investigation_started")

    def propose(self, actor: str, action: str, evidence_links: list[str]) -> None:
        if self.status != "investigating":
            raise ValueError("Incident must be under investigation")
        if action not in {"rollback_order_service", "restart_order_service"}:
            raise ValueError("Remediation action is not on the allowlist")
        if not evidence_links or not all(
            isinstance(link, str) and link.startswith("https://") for link in evidence_links
        ):
            raise ValueError("Evidence must contain HTTPS links")
        self.proposed_action = action
        self.evidence_links = evidence_links[:20]
        self.approved_by = None
        self._transition("awaiting_approval", actor, "proposal_submitted")

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

    def redacted_report(self) -> dict:
        """Only return evidence URLs and state, never raw logs or credentials."""
        return {
            "incident_id": self.incident_id,
            "service": self.service,
            "status": self.status,
            "evidence_links": self.evidence_links.copy(),
            "proposed_action": self.proposed_action,
            "approved_by": self.approved_by,
            "timeline": [event.copy() for event in self.events],
        }
