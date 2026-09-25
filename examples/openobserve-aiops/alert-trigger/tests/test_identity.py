import pytest

from identity import load_oidc_settings, principal_claims


def settings_env():
    return {
        "AIOPS_ENV": "staging",
        "OIDC_ISSUER": "https://id.example.com/tenant",
        "OIDC_METADATA_URL": "https://id.example.com/tenant/.well-known/openid-configuration",
        "OIDC_CLIENT_ID": "aiops-workbench",
        "OIDC_CLIENT_SECRET": "a-private-client-secret",
        "OIDC_REDIRECT_URI": "https://aiops.example.com/auth/oidc/callback",
        "AIOPS_PUBLIC_ORIGIN": "https://aiops.example.com",
        "OIDC_GROUP_MAPPINGS_JSON": '{"aiops-operators":{"role":"operator","resource_scopes":["order-service"]},"aiops-approvers":{"role":"approver","resource_scopes":["order-service"]}}',
    }


def test_oidc_settings_require_https_and_exact_application_redirect_origin():
    settings = load_oidc_settings(settings_env())
    assert settings.issuer == "https://id.example.com/tenant"
    assert settings.group_mappings["aiops-operators"] == {
        "role": "operator",
        "resource_scopes": ["order-service"],
    }

    bad_redirect = settings_env()
    bad_redirect["OIDC_REDIRECT_URI"] = "https://attacker.example/callback"
    with pytest.raises(ValueError, match="configured public origin"):
        load_oidc_settings(bad_redirect)

    insecure = settings_env()
    insecure["OIDC_METADATA_URL"] = "http://id.example.com/.well-known/openid-configuration"
    with pytest.raises(ValueError, match="HTTPS outside loopback"):
        load_oidc_settings(insecure)


def test_oidc_allows_http_loopback_only_in_local_environment():
    env = settings_env()
    env.update({
        "AIOPS_ENV": "local",
        "OIDC_ISSUER": "http://localhost:9090/issuer",
        "OIDC_METADATA_URL": "http://localhost:9090/issuer/.well-known/openid-configuration",
        "OIDC_REDIRECT_URI": "http://localhost:8081/auth/oidc/callback",
        "AIOPS_PUBLIC_ORIGIN": "http://localhost:8081",
    })
    assert load_oidc_settings(env).public_origin == "http://localhost:8081"


def test_oidc_group_mapping_fails_closed_for_unknown_or_conflicting_roles():
    settings = load_oidc_settings(settings_env())
    base = {"iss": settings.issuer, "sub": "user-123", "preferred_username": "operator", "groups": ["aiops-operators"]}
    assert principal_claims(base, settings) == (
        settings.issuer,
        "user-123",
        "operator",
        "operator",
        ["order-service"],
    )

    with pytest.raises(ValueError, match="exactly one application role"):
        principal_claims({**base, "groups": ["unknown"]}, settings)
    with pytest.raises(ValueError, match="exactly one application role"):
        principal_claims({**base, "groups": ["aiops-operators", "aiops-approvers"]}, settings)
    with pytest.raises(ValueError, match="issuer did not match"):
        principal_claims({**base, "iss": "https://wrong.example"}, settings)


def test_oidc_group_mapping_requires_explicit_resource_scope():
    env = settings_env()
    env["OIDC_GROUP_MAPPINGS_JSON"] = '{"aiops-operators":{"role":"operator","resource_scopes":[]}}'
    with pytest.raises(ValueError, match="non-empty resource scopes"):
        load_oidc_settings(env)
