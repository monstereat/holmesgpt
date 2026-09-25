"""Stable task error types shared by the worker and standalone evaluation client."""

from typing import Any


class RetryableTaskError(Exception):
    def __init__(self, code: str = "upstream_unavailable"):
        super().__init__(code)
        self.code = code


class PermanentTaskError(Exception):
    def __init__(self, code: str = "investigation_failed", *, evidence: list[dict[str, Any]] | None = None):
        super().__init__(code)
        self.code = code
        self.evidence = evidence or []
