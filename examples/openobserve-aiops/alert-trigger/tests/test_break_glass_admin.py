from uuid import UUID

import pytest

import break_glass_admin


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchone(self):
        return self.rows.pop(0)


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.transaction_started = True
        return self

    def __exit__(self, exc_type, *_args):
        self.connection.transaction_committed = exc_type is None
        return False


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)
        self.transaction_started = False
        self.transaction_committed = False

    def transaction(self):
        return FakeTransaction(self)

    def cursor(self):
        return self.cursor_instance


def test_request_uses_authenticated_database_identity_and_persisted_request_function():
    connection = FakeConnection([("operator-alice", True), (37,)])

    result = break_glass_admin.request_admin_recovery(
        connection,
        UUID("00000000-0000-0000-0000-000000000123"),
        "INC-2026-123",
    )

    assert result == (37, "operator-alice")
    assert connection.transaction_committed is True
    assert connection.cursor_instance.statements[0] == (
        "SELECT session_user::text, pg_has_role(session_user, 'aiops_break_glass', 'member')",
        None,
    )
    statement, params = connection.cursor_instance.statements[-1]
    assert "public.request_break_glass_admin_recovery" in statement
    assert params == ("00000000-0000-0000-0000-000000000123", "INC-2026-123")


def test_approval_uses_separate_authenticated_session_and_records_audit_id():
    connection = FakeConnection([("approver-bob", True), ("00000000-0000-0000-0000-000000000123", 41)])

    result = break_glass_admin.approve_admin_recovery(connection, 37, True)

    assert result == ("00000000-0000-0000-0000-000000000123", 41, "approver-bob")
    assert connection.transaction_committed is True
    statement, params = connection.cursor_instance.statements[-1]
    assert "public.approve_break_glass_admin_recovery" in statement
    assert params == (37, True)


def test_recovery_refuses_database_identity_without_custodian_membership():
    connection = FakeConnection([("unapproved-user", False)])

    with pytest.raises(ValueError, match="not an approved break-glass custodian"):
        break_glass_admin.request_admin_recovery(
            connection,
            UUID("00000000-0000-0000-0000-000000000123"),
            "INC-2026-123",
        )

    assert connection.transaction_committed is False


def test_cli_requires_out_of_band_idp_check_for_approval(monkeypatch, capsys):
    monkeypatch.setenv("AIOPS_BREAK_GLASS_ENABLED", "true")
    monkeypatch.setenv("AIOPS_BREAK_GLASS_DATABASE_URL", "postgresql://not-a-real-dsn")
    monkeypatch.setattr(
        break_glass_admin.psycopg,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("database must not be contacted"),
    )

    result = break_glass_admin.main(["approve", "--request-id", "37"])

    assert result == 2
    assert "independently verify target IdP admin-group membership" in capsys.readouterr().err
