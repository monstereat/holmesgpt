"""Database connection policy for local and non-local AIOps deployments."""

import os

from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict


def validate_database_url(database_url: str, variable_name: str, *, required: bool = False) -> str:
    environment = os.getenv("AIOPS_ENV", "production")
    if not database_url:
        if required:
            raise RuntimeError(f"{variable_name} is required")
        return ""
    if environment == "local":
        return database_url
    try:
        connection_options = conninfo_to_dict(database_url)
    except (ProgrammingError, TypeError, ValueError):
        raise RuntimeError(f"{variable_name} must be a valid PostgreSQL connection string") from None
    if connection_options.get("sslmode") != "verify-full":
        raise RuntimeError(f"{variable_name} must set sslmode=verify-full outside local mode")
    return database_url
