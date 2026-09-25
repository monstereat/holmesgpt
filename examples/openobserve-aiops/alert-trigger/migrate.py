"""Explicit one-shot entry point for incident database migrations."""

import os
import sys

from migration_runner import apply_migrations


def main() -> int:
    database_url = os.getenv("MIGRATION_DATABASE_URL", "")
    if not database_url and os.getenv("AIOPS_ENV") == "local":
        database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("MIGRATION_DATABASE_URL is required to apply incident database migrations", file=sys.stderr)
        return 2
    apply_migrations(database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
