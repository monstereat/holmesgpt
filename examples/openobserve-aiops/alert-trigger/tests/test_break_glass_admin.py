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


def test_recovery_calls_restricted_database_function_as_break_glass_role():
    connection = FakeConnection([("aiops_break_glass",), (37,)])

    result = break_glass_admin.recover_admin_lockout(
        connection,
        UUID("00000000-0000-0000-0000-000000000123"),
        "operator-alice",
        "approver-bob",
        "INC-2026-123",
    )

    assert result == ("00000000-0000-0000-0000-000000000123", 37)
    assert connection.transaction_committed is True
    statement, params = connection.cursor_instance.statements[-1]
    assert "public.break_glass_reactivate_admin" in statement
    assert params == (
        "00000000-0000-0000-0000-000000000123",
        "operator-alice",
        "approver-bob",
        "INC-2026-123",
        True,
    )


def test_recovery_refuses_any_identity_other_than_break_glass_role():
    connection = FakeConnection([("aiops_runtime",)])

    with pytest.raises(ValueError, match="aiops_break_glass database identity"):
        break_glass_admin.recover_admin_lockout(
            connection,
            UUID("00000000-0000-0000-0000-000000000123"),
            "operator-alice",
            "approver-bob",
            "INC-2026-123",
        )

    assert connection.transaction_committed is False


def test_recovery_cli_requires_separate_people_and_explicit_confirmations(monkeypatch, capsys):
    monkeypatch.setenv("AIOPS_BREAK_GLASS_ENABLED", "true")
    monkeypatch.setenv("AIOPS_BREAK_GLASS_DATABASE_URL", "postgresql://not-a-real-dsn")
    monkeypatch.setattr(
        break_glass_admin.psycopg,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("database must not be contacted"),
    )

    result = break_glass_admin.main(
        [
            "--user-id",
            "00000000-0000-0000-0000-000000000123",
            "--operator-id",
            "operator-alice",
            "--approver-id",
            "operator-alice",
            "--change-reference",
            "INC-2026-123",
            "--confirm-zero-active-admins",
            "--confirm-idp-admin-membership",
        ]
    )

    assert result == 2
    assert "must be different people" in capsys.readouterr().err
