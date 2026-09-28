"""Narrow HTTP client for the demo order-service-owned test action."""

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 64_000


class ActionServiceError(Exception):
    def __init__(self, code: str = "action_service_unavailable", status_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OrderActionClient:
    def __init__(self, base_url: str, service_token: str, *, timeout_seconds: int = 5, opener: Any = None):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "order-service"
            or parsed.port not in {None, 8080}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Order action URL must target the internal order-service Compose host")
        if not service_token:
            raise ValueError("Order action service token is required")
        if not 1 <= timeout_seconds <= 15:
            raise ValueError("Order action timeout must be between 1 and 15 seconds")
        self.base_url = f"http://order-service:{parsed.port or 8080}"
        self.service_token = service_token
        self.timeout_seconds = timeout_seconds
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def state(self) -> dict[str, str]:
        result = self._request("GET", "/internal/test-actions/state")
        if result.get("resource") != "order-service" or result.get("chaos_mode") not in {"on", "off"}:
            raise ActionServiceError("invalid_action_state")
        return result

    def operation_result(self, idempotency_key: str, enabled: bool) -> dict[str, Any] | None:
        if (
            not isinstance(enabled, bool)
            or not isinstance(idempotency_key, str)
            or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", idempotency_key)
        ):
            raise ValueError("Invalid action parameters")
        result = self._request("GET", f"/internal/test-actions/operations/{idempotency_key}")
        if result.get("found") is False and result.get("action_id") == idempotency_key:
            return None
        expected_mode = "on" if enabled else "off"
        if (
            result.get("found") is not True
            or result.get("accepted") is not True
            or result.get("action_id") != idempotency_key
            or result.get("action") != "set-chaos-mode"
            or result.get("resource") != "order-service"
            or result.get("chaos_mode") != expected_mode
        ):
            raise ActionServiceError("invalid_action_operation")
        return result

    def set_chaos_mode(self, enabled: bool, idempotency_key: str) -> dict[str, Any]:
        if not isinstance(enabled, bool) or not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("Invalid action parameters")
        result = self._request(
            "POST",
            "/internal/test-actions",
            body={"action": "set-chaos-mode", "resource": "order-service", "enabled": enabled},
            idempotency_key=idempotency_key,
        )
        expected_mode = "on" if enabled else "off"
        if (
            result.get("accepted") is not True
            or result.get("action_id") != idempotency_key
            or result.get("action") != "set-chaos-mode"
            or result.get("resource") != "order-service"
            or result.get("chaos_mode") != expected_mode
        ):
            raise ActionServiceError("invalid_action_response")
        return result

    def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        encoded = json.dumps(body).encode() if body is not None else None
        headers = {"X-Order-Action-Token": self.service_token, "Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(self.base_url + path, data=encoded, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise ActionServiceError("action_owner_rejected", exc.code) from None
        except (TimeoutError, urllib.error.URLError, OSError):
            raise ActionServiceError("action_owner_unavailable") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ActionServiceError("action_response_too_large")
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ActionServiceError("action_response_invalid_json") from None
        if not isinstance(result, dict):
            raise ActionServiceError("action_response_invalid")
        return result
