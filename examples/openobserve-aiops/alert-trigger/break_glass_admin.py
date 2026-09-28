"""Two-person, database-authenticated last-admin recovery commands."""

from __future__ import annotations

import argparse
import os
import sys
from uuid import UUID

import psycopg

from db_config import connect_database

def _authorized_session(connection: psycopg.Connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT session_user::text, pg_has_role(session_user, 'aiops_break_glass', 'member')"
        )
        identity, authorized = cursor.fetchone()
    if not authorized:
        raise ValueError("The authenticated database identity is not an approved break-glass custodian")
    return identity


def request_admin_recovery(
    connection: psycopg.Connection,
    user_id: UUID,
    change_reference: str,
) -> tuple[int, str]:
    """Create an audited recovery request as the authenticated first custodian."""
    with connection.transaction():
        identity = _authorized_session(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.request_break_glass_admin_recovery(%s, %s)",
                (str(user_id), change_reference),
            )
            request_id = cursor.fetchone()[0]
    return int(request_id), identity


def approve_admin_recovery(
    connection: psycopg.Connection,
    request_id: int,
    idp_admin_membership_verified: bool,
) -> tuple[str, int, str]:
    """Approve and execute a pending request as a different authenticated custodian."""
    with connection.transaction():
        identity = _authorized_session(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.approve_break_glass_admin_recovery(%s, %s)",
                (request_id, idp_admin_membership_verified),
            )
            user_id, audit_event_id = cursor.fetchone()
    return str(user_id), int(audit_event_id), identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    request_parser = subparsers.add_parser("request", help="Create an audited recovery request")
    request_parser.add_argument("--user-id", required=True, type=UUID, help="Existing disabled admin UUID")
    request_parser.add_argument("--change-reference", required=True)

    approve_parser = subparsers.add_parser("approve", help="Approve from a different custodian login")
    approve_parser.add_argument("--request-id", required=True, type=int)
    approve_parser.add_argument("--confirm-idp-admin-membership", action="store_true")

    args = parser.parse_args(argv)
    if os.getenv("AIOPS_BREAK_GLASS_ENABLED") != "true":
        print("Refusing recovery: AIOPS_BREAK_GLASS_ENABLED must be set to true for this invocation.", file=sys.stderr)
        return 2
    dsn = os.getenv("AIOPS_BREAK_GLASS_DATABASE_URL", "")
    if not dsn:
        print("Refusing recovery: AIOPS_BREAK_GLASS_DATABASE_URL is required.", file=sys.stderr)
        return 2
    if args.command == "approve" and not args.confirm_idp_admin_membership:
        print("Refusing recovery: independently verify target IdP admin-group membership first.", file=sys.stderr)
        return 2
    if args.command == "request" and not 3 <= len(args.change_reference) <= 100:
        print("Refusing recovery: change reference must contain 3-100 characters.", file=sys.stderr)
        return 2

    try:
        with connect_database(dsn) as connection:
            if args.command == "request":
                request_id, identity = request_admin_recovery(connection, args.user_id, args.change_reference)
                print(f"Recovery request {request_id} recorded for disabled admin {args.user_id} by {identity}.")
            else:
                user_id, audit_id, identity = approve_admin_recovery(
                    connection, args.request_id, args.confirm_idp_admin_membership
                )
                print(f"Approved recovery for admin {user_id}; approver {identity}; audit event {audit_id} committed.")
    except ValueError as exc:
        print(f"Refusing recovery: {exc}.", file=sys.stderr)
        return 2
    except psycopg.Error:
        print("Break-glass recovery failed; the transaction was rolled back. Database details were suppressed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
