"""Stable task error types shared by the worker and standalone evaluation client."""


class RetryableTaskError(Exception):
    def __init__(self, code: str = "upstream_unavailable"):
        super().__init__(code)
        self.code = code


class PermanentTaskError(Exception):
    def __init__(self, code: str = "investigation_failed"):
        super().__init__(code)
        self.code = code
