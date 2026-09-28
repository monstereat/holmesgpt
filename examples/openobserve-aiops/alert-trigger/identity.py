"""Provider-neutral OIDC settings and fail-closed group authorization mapping."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from auth import ROLE_PERMISSIONS


class _DuplicateJSONKeyError(ValueError):
    pass


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError
        result[key] = value
    return result


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    metadata_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    public_origin: str
    scopes: tuple[str, ...]
    groups_claim: str
    group_mappings: dict[str, dict[str, Any]]


def load_oidc_settings(environ: dict[str, str]) -> OIDCSettings:
    required = (
        "OIDC_ISSUER",
        "OIDC_METADATA_URL",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET",
        "OIDC_REDIRECT_URI",
        "AIOPS_PUBLIC_ORIGIN",
        "OIDC_GROUP_MAPPINGS_JSON",
    )
    missing = [key for key in required if not environ.get(key)]
    if missing:
        raise ValueError("Missing required OIDC configuration: " + ", ".join(missing))

    issuer = environ["OIDC_ISSUER"]
    metadata_url = environ["OIDC_METADATA_URL"]
    redirect_uri = environ["OIDC_REDIRECT_URI"]
    public_origin = environ["AIOPS_PUBLIC_ORIGIN"].rstrip("/")
    allow_loopback_http = environ.get("AIOPS_ENV") == "local"
    for name, value in (("OIDC_ISSUER", issuer), ("OIDC_METADATA_URL", metadata_url), ("OIDC_REDIRECT_URI", redirect_uri)):
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError(f"Invalid {name}")
        if parsed.scheme != "https" and not (allow_loopback_http and _is_loopback(parsed.hostname)):
            raise ValueError(f"{name} must use HTTPS outside loopback")
    origin = urlsplit(public_origin)
    if (
        origin.scheme not in {"https", "http"}
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path
        or origin.query
        or origin.fragment
        or (origin.scheme != "https" and not (allow_loopback_http and _is_loopback(origin.hostname)))
    ):
        raise ValueError("AIOPS_PUBLIC_ORIGIN must be an HTTPS origin")
    redirect = urlsplit(redirect_uri)
    if (redirect.scheme, redirect.netloc) != (origin.scheme, origin.netloc) or redirect.query:
        raise ValueError("OIDC_REDIRECT_URI must use the configured public origin and contain no query")

    try:
        raw_mappings = json.loads(environ["OIDC_GROUP_MAPPINGS_JSON"], object_pairs_hook=_unique_object_pairs)
    except _DuplicateJSONKeyError as exc:
        raise ValueError("OIDC_GROUP_MAPPINGS_JSON must not contain duplicate object keys") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("OIDC_GROUP_MAPPINGS_JSON must be valid JSON") from exc
    if not isinstance(raw_mappings, dict) or not raw_mappings:
        raise ValueError("OIDC_GROUP_MAPPINGS_JSON must be a non-empty object")

    mappings: dict[str, dict[str, Any]] = {}
    for group, mapping in raw_mappings.items():
        if not isinstance(group, str) or not group.strip() or group != group.strip() or not isinstance(mapping, dict):
            raise ValueError("OIDC group mappings must map non-empty group names to objects")
        role = mapping.get("role")
        scopes = mapping.get("resource_scopes")
        if (
            set(mapping) != {"role", "resource_scopes"}
            or not isinstance(role, str)
            or role not in ROLE_PERMISSIONS
            or not isinstance(scopes, list)
            or not scopes
            or not all(
                isinstance(scope, str)
                and (scope == "*" or re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", scope))
                for scope in scopes
            )
            or ("*" in scopes and len(scopes) != 1)
        ):
            raise ValueError("OIDC group mappings require a valid role and non-empty resource scopes")
        mappings[group] = {"role": role, "resource_scopes": sorted({scope.lower() for scope in scopes})}

    groups_claim = environ.get("OIDC_GROUPS_CLAIM", "groups")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", groups_claim):
        raise ValueError("OIDC_GROUPS_CLAIM is invalid")
    scopes = tuple(environ.get("OIDC_SCOPES", "openid profile email").split())
    if "openid" not in scopes or not all(re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", scope) for scope in scopes):
        raise ValueError("OIDC_SCOPES must contain openid and valid scope names")
    return OIDCSettings(
        issuer=issuer,
        metadata_url=metadata_url,
        client_id=environ["OIDC_CLIENT_ID"],
        client_secret=environ["OIDC_CLIENT_SECRET"],
        redirect_uri=redirect_uri,
        public_origin=public_origin,
        scopes=scopes,
        groups_claim=groups_claim,
        group_mappings=mappings,
    )


def principal_claims(claims: dict[str, Any], settings: OIDCSettings) -> tuple[str, str, str, str, list[str]]:
    """Return issuer, subject, display name, role, and scopes after fail-closed mapping."""
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 512:
        raise ValueError("OIDC identity has no valid subject")
    if claims.get("iss") != settings.issuer:
        raise ValueError("OIDC issuer did not match the configured issuer")
    groups = claims.get(settings.groups_claim)
    if not isinstance(groups, list) or not all(isinstance(group, str) for group in groups):
        raise ValueError("OIDC identity is missing the configured groups claim")
    mapped = [settings.group_mappings[group] for group in groups if group in settings.group_mappings]
    roles = {item["role"] for item in mapped}
    if len(roles) != 1:
        raise ValueError("OIDC groups do not resolve to exactly one application role")
    scope_set = {scope for item in mapped for scope in item["resource_scopes"]}
    scopes = ["*"] if "*" in scope_set else sorted(scope_set)
    if not scopes:
        raise ValueError("OIDC groups resolve to invalid resource scopes")
    username = claims.get("preferred_username") or claims.get("email") or subject
    if not isinstance(username, str) or not username.strip() or len(username) > 256:
        raise ValueError("OIDC identity has no valid display name")
    return settings.issuer, subject, username.strip(), roles.pop(), scopes


def _is_loopback(hostname: str) -> bool:
    return hostname.lower() in {"localhost", "127.0.0.1", "::1"}
