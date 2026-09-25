import json
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

import pytest

from action_client import ActionServiceError, OrderActionClient


class Response:
    def __init__(self, payload):
        self.status = 200
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if self.error:
            raise self.error
        return self.response


def test_owner_client_sends_only_fixed_action_to_internal_service():
    opener = Opener(Response({
        "accepted": True,
        "action_id": "approval-1",
        "action": "set-chaos-mode",
        "resource": "order-service",
        "chaos_mode": "on",
        "duplicate": False,
    }))
    client = OrderActionClient("http://order-service:8080", "owner-token", opener=opener)
    result = client.set_chaos_mode(True, "approval-1")
    request = opener.request
    assert request.full_url == "http://order-service:8080/internal/test-actions"
    assert request.get_method() == "POST"
    assert request.get_header("X-order-action-token") == "owner-token"
    assert request.get_header("Idempotency-key") == "approval-1"
    assert json.loads(request.data) == {"action": "set-chaos-mode", "resource": "order-service", "enabled": True}
    assert result["chaos_mode"] == "on"


def test_owner_client_rejects_other_hosts_and_invalid_action_results():
    with pytest.raises(ValueError, match="internal order-service"):
        OrderActionClient("http://host.docker.internal:8080", "owner-token")
    client = OrderActionClient("http://order-service:8080", "owner-token", opener=Opener(Response({"accepted": True})))
    with pytest.raises(ActionServiceError, match="invalid_action_response"):
        client.set_chaos_mode(False, "approval-2")


def test_owner_http_errors_are_redacted():
    error = HTTPError("http://order-service", 401, "service token must not leak", Message(), BytesIO(b"token=secret"))
    client = OrderActionClient("http://order-service:8080", "owner-token", opener=Opener(error=error))
    with pytest.raises(ActionServiceError) as raised:
        client.state()
    assert raised.value.status_code == 401
    assert "secret" not in str(raised.value)
