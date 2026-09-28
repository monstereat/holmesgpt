"""Database connection policy for local and non-local AIOps deployments."""

import os
from typing import Any

import psycopg
from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def connect_database(database_url: str, **options: Any) -> psycopg.Connection[Any]:
    """Connect with a short default timeout while honoring an explicit DSN value."""
    try:
        connection_options = conninfo_to_dict(database_url)
    except (ProgrammingError, TypeError, ValueError):
        raise RuntimeError("Invalid PostgreSQL connection string") from None
    connect_timeout = options.pop("connect_timeout", connection_options.get("connect_timeout", "3"))
    connection_options["connect_timeout"] = str(connect_timeout)
    return psycopg.connect(make_conninfo(**connection_options), **options)


def validate_database_url(database_url: str, variable_name: str, *, required: bool = False) -> str:
    environment = os.getenv("AIOPS_ENV", "production")
    if not database_url:
        if required:
            raise RuntimeError(f"{variable_name} is required")
        return ""
    try:
        connection_options = conninfo_to_dict(database_url)
    except (ProgrammingError, TypeError, ValueError):
        raise RuntimeError(f"{variable_name} must be a valid PostgreSQL connection string") from None
    if environment != "local" and connection_options.get("sslmode") != "verify-full":
        raise RuntimeError(f"{variable_name} must set sslmode=verify-full outside local mode")
    return database_url
