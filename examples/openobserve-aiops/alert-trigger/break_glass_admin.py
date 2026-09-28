"""One-shot, audited last-admin recovery for a database operator."""

from __future__ import annotations

import argparse
import os
import re
import sys
from uuid import UUID

import psycopg


def recover_admin_lockout(
    connection: psycopg.Connection,
    user_id: UUID,
    operator_id: str,
    approver_id: str,
    change_reference: str,
) -> tuple[str, int]:
    """Invoke the restricted database function that performs the audited recovery."""
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            database_role = cursor.fetchone()[0]
            if database_role != "aiops_break_glass":
                raise ValueError("Connect as the separately controlled aiops_break_glass database identity")
            cursor.execute(
                "SELECT public.break_glass_reactivate_admin(%s, %s, %s, %s, %s)",
                (str(user_id), operator_id, approver_id, change_reference, True),
            )
            audit_id = cursor.fetchone()[0]
    return str(user_id), int(audit_id)


def _identifier(value: str, option: str) -> str:
    value = value.strip()
    if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        raise argparse.ArgumentTypeError(f"{option} must be 1-128 printable characters")
    return value


def _change_reference(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._:/-]{3,100}", value):
        raise argparse.ArgumentTypeError("--change-reference must be 3-100 letters, digits, or ._:/-")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, type=UUID, help="Existing disabled admin UUID")
    parser.add_argument("--operator-id", required=True, type=lambda value: _identifier(value, "--operator-id"))
    parser.add_argument("--approver-id", required=True, type=lambda value: _identifier(value, "--approver-id"))
    parser.add_argument("--change-reference", required=True, type=_change_reference)
    parser.add_argument("--confirm-zero-active-admins", action="store_true")
    parser.add_argument("--confirm-idp-admin-membership", action="store_true")
    args = parser.parse_args(argv)

    if os.getenv("AIOPS_BREAK_GLASS_ENABLED") != "true":
        print("Refusing recovery: AIOPS_BREAK_GLASS_ENABLED must be set to true for this invocation.", file=sys.stderr)
        return 2
    dsn = os.getenv("AIOPS_BREAK_GLASS_DATABASE_URL", "")
    if not dsn:
        print("Refusing recovery: AIOPS_BREAK_GLASS_DATABASE_URL is required.", file=sys.stderr)
        return 2
    if args.operator_id == args.approver_id:
        print("Refusing recovery: operator and approver must be different people.", file=sys.stderr)
        return 2
    if not args.confirm_zero_active_admins or not args.confirm_idp_admin_membership:
        print(
            "Refusing recovery: confirm zero active admins and independently verified IdP admin membership.",
            file=sys.stderr,
        )
        return 2

    try:
        with psycopg.connect(dsn) as connection:
            user_id, audit_id = recover_admin_lockout(
                connection,
                args.user_id,
                args.operator_id,
                args.approver_id,
                args.change_reference,
            )
    except ValueError as exc:
        print(f"Refusing recovery: {exc}.", file=sys.stderr)
        return 2
    except psycopg.Error:
        print("Break-glass recovery failed; the transaction was rolled back. Database details were suppressed.", file=sys.stderr)
        return 1

    print(f"Reactivated disabled admin {user_id}; audit event {audit_id} committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
