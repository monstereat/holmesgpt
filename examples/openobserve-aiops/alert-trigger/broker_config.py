"""Redis broker connection policy for local and non-local AIOps deployments."""

import os
from urllib.parse import parse_qs, unquote, urlsplit


def validate_broker_url(broker_url: str, *, required: bool = False) -> str:
    environment = os.getenv("AIOPS_ENV", "production")
    if not broker_url:
        if required:
            raise RuntimeError("REDIS_URL is required")
        return ""
    if environment == "local":
        return broker_url
    try:
        parsed_url = urlsplit(broker_url)
        password = unquote(parsed_url.password or "")
        hostname = parsed_url.hostname
        port = parsed_url.port
        options = parse_qs(parsed_url.query, keep_blank_values=True)
    except ValueError:
        raise RuntimeError("REDIS_URL must be a valid Redis connection string") from None
    if parsed_url.scheme != "rediss" or not hostname or not password or (port is not None and not 1 <= port <= 65535):
        raise RuntimeError("REDIS_URL must use rediss:// and include a host and password outside local mode")
    if options.get("ssl_cert_reqs") != ["required"]:
        raise RuntimeError("REDIS_URL must set ssl_cert_reqs=required outside local mode")
    if [value.lower() for value in options.get("ssl_check_hostname", [])] != ["true"]:
        raise RuntimeError("REDIS_URL must set ssl_check_hostname=true outside local mode")
    return broker_url
