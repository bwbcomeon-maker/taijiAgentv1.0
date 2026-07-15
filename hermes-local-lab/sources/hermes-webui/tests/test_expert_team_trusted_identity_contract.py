import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64_int(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _key_material():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "enterprise-key-1",
        "use": "sig",
        "alg": "RS256",
        "n": _b64_int(numbers.n),
        "e": _b64_int(numbers.e),
    }
    return private, jwk


def _config(**overrides):
    value = {
        "enabled": True,
        "provider": "oidc_pkce",
        "issuer": "https://id.example.internal",
        "authorization_endpoint": "https://id.example.internal/authorize",
        "token_endpoint": "https://id.example.internal/token",
        "client_id": "taiji-expert-team",
        "audience": "taiji-enterprise",
        "redirect_uris": ["https://taiji.example.internal/api/expert-teams/identity/callback"],
        "algorithms": ["RS256"],
        "role_claim": "roles",
        "allowed_roles": ["document-approver", "document-reviewer", "waiver-authorizer"],
        "flow_ttl_seconds": 300,
        "clock_skew_seconds": 30,
    }
    value.update(overrides)
    return value


def _resolver(now, token_holder, *, production=True, config=None):
    from api.expert_teams.trusted_identity import TrustedIdentityResolver

    return TrustedIdentityResolver(
        config or _config(),
        clock=lambda: now[0],
        token_client=lambda **kwargs: {"id_token": token_holder["token"]},
        jwks_loader=lambda **kwargs: {"keys": [token_holder["jwk"]]},
        production=production,
    )


def _complete_login(resolver, now, token_holder, *, roles=None, nonce_override=None, redirect_uri=None):
    redirect_uri = redirect_uri or _config()["redirect_uris"][0]
    start = resolver.start_login(redirect_uri)
    query = parse_qs(urlparse(start["authorization_url"]).query)
    assert query["state"] == [start["state"]]
    assert query["nonce"] == [start["nonce"]]
    assert query["code_challenge_method"] == ["S256"]
    assert "code_verifier" not in start
    claims = {
        "iss": _config()["issuer"],
        "aud": _config()["audience"],
        "sub": "user-001",
        "name": "张三",
        "iat": int(now[0]),
        "exp": int(now[0] + 600),
        "nonce": nonce_override or start["nonce"],
        "jti": "credential-jti-001",
        "roles": roles or ["document-approver"],
    }
    token_holder["token"] = jwt.encode(
        claims,
        token_holder["private"],
        algorithm="RS256",
        headers={"kid": token_holder["jwk"]["kid"]},
    )
    return resolver.complete_login(state=start["state"], code="authorization-code")


def test_oidc_pkce_login_produces_safe_reusable_trusted_principal():
    now = [time.time()]
    private, jwk = _key_material()
    holder = {"private": private, "jwk": jwk, "token": ""}
    resolver = _resolver(now, holder)

    completed = _complete_login(resolver, now, holder)
    assert set(completed) == {"session_id", "redirect_uri", "principal"}
    assert set(completed["principal"]) == {
        "schema_version", "subject", "display_name", "issuer", "audience", "roles",
        "authenticated_at", "expires_at", "credential_jti_sha256", "key_fingerprint", "auth_method",
        "identity_snapshot_sha256",
    }
    assert completed["principal"]["credential_jti_sha256"] == hashlib.sha256(b"credential-jti-001").hexdigest()
    first = resolver.resolve(completed["session_id"], required_role="document-approver")
    second = resolver.resolve(completed["session_id"], required_role="document-approver")
    assert first == second
    serialized = json.dumps(resolver.status(completed["session_id"]), ensure_ascii=False)
    assert holder["token"] not in serialized
    assert "credential-jti-001" not in serialized
    assert "nonce" not in serialized


def test_state_nonce_role_signature_issuer_and_algorithm_fail_closed():
    now = [time.time()]
    private, jwk = _key_material()
    holder = {"private": private, "jwk": jwk, "token": ""}

    resolver = _resolver(now, holder)
    resolver.start_login(_config()["redirect_uris"][0])
    with pytest.raises(ValueError, match="state"):
        resolver.complete_login(state="forged-state", code="x")

    resolver = _resolver(now, holder)
    with pytest.raises(ValueError, match="nonce"):
        _complete_login(resolver, now, holder, nonce_override="forged-nonce")

    resolver = _resolver(now, holder)
    with pytest.raises(ValueError, match="role"):
        _complete_login(resolver, now, holder, roles=["admin"])

    bad_config = _config(issuer="https://other-issuer.internal")
    resolver = _resolver(now, holder, config=bad_config)
    with pytest.raises(ValueError):
        _complete_login(resolver, now, holder)


def test_redirect_allowlist_logout_expiry_and_restart_fail_closed():
    now = [time.time()]
    private, jwk = _key_material()
    holder = {"private": private, "jwk": jwk, "token": ""}
    resolver = _resolver(now, holder)
    with pytest.raises(ValueError, match="redirect"):
        resolver.start_login("https://evil.example/callback")

    completed = _complete_login(resolver, now, holder)
    session_id = completed["session_id"]
    resolver.logout(session_id)
    with pytest.raises(ValueError, match="session"):
        resolver.resolve(session_id, required_role="document-approver")

    completed = _complete_login(resolver, now, holder)
    now[0] += 700
    with pytest.raises(ValueError, match="expired"):
        resolver.resolve(completed["session_id"], required_role="document-approver")

    restarted = _resolver(now, holder)
    with pytest.raises(ValueError, match="session"):
        restarted.resolve(completed["session_id"], required_role="document-approver")


def test_disabled_and_production_test_principal_injection_are_rejected():
    from api.expert_teams.trusted_identity import TrustedIdentityResolver

    disabled = TrustedIdentityResolver({"enabled": False}, production=True)
    assert disabled.status(None) == {"enabled": False, "authenticated": False, "provider": "disabled"}
    with pytest.raises(ValueError, match="disabled"):
        disabled.start_login("https://taiji.example/callback")
    with pytest.raises(RuntimeError, match="production"):
        disabled.install_test_principal({"subject": "fake", "roles": ["document-approver"]})
