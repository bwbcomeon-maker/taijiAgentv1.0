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


def test_identity_flow_status_is_typed_without_exposing_the_session_secret():
    now = [time.time()]
    private, jwk = _key_material()
    holder = {"private": private, "jwk": jwk, "token": ""}
    resolver = _resolver(now, holder)
    start = resolver.start_login(_config()["redirect_uris"][0])
    claims = {
        "iss": _config()["issuer"], "aud": _config()["audience"], "sub": "user-001", "name": "张三",
        "iat": int(now[0]), "exp": int(now[0] + 600), "nonce": start["nonce"], "jti": "flow-jti",
        "roles": ["document-approver"],
    }
    holder["token"] = jwt.encode(claims, private, algorithm="RS256", headers={"kid": jwk["kid"]})
    completed = resolver.complete_login(state=start["state"], code="authorization-code")
    status = resolver.status(completed["session_id"], start["flow_id"])
    assert status["authenticated"] is True
    assert status["identity_flow_status"] == "completed"
    assert completed["session_id"] not in json.dumps(status, ensure_ascii=False)


def test_identity_flow_completion_is_bound_to_its_own_cookie_session():
    now = [time.time()]
    private, jwk = _key_material()
    holder = {"private": private, "jwk": jwk, "token": ""}
    resolver = _resolver(now, holder)
    first = resolver.start_login(_config()["redirect_uris"][0])
    second = resolver.start_login(_config()["redirect_uris"][0])

    def complete(started, subject):
        claims = {
            "iss": _config()["issuer"], "aud": _config()["audience"], "sub": subject, "name": subject,
            "iat": int(now[0]), "exp": int(now[0] + 600), "nonce": started["nonce"],
            "jti": f"{subject}-jti", "roles": ["document-approver"],
        }
        holder["token"] = jwt.encode(claims, private, algorithm="RS256", headers={"kid": jwk["kid"]})
        return resolver.complete_login(state=started["state"], code="authorization-code")

    second_result = complete(second, "user-second")
    assert resolver.status(second_result["session_id"], second["flow_id"])["identity_flow_status"] == "completed"
    first_result = complete(first, "user-first")
    mismatched = resolver.status(first_result["session_id"], second["flow_id"])
    assert mismatched["identity_flow_status"] == "session_mismatch"
    assert mismatched["authenticated"] is True
    assert second_result["session_id"] not in json.dumps(mismatched, ensure_ascii=False)
    reverse_mismatch = resolver.status(second_result["session_id"], first["flow_id"])
    assert reverse_mismatch["identity_flow_status"] == "session_mismatch"


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


def test_shared_trusted_principal_entrypoint_is_role_exact_and_rejects_client_identity(monkeypatch):
    from api.expert_teams import trusted_identity
    from api.expert_teams.trusted_identity import TrustedIdentityError, TrustedIdentityResolver

    resolver = TrustedIdentityResolver({"enabled": False}, production=False)
    resolver._config = {"enabled": True}
    session_id = resolver.install_test_principal(
        {
            "subject": "reviewer-1",
            "display_name": "复核人甲",
            "roles": ["document-reviewer"],
            "expires_at": int(time.time()) + 3600,
            "auth_method": "oidc_pkce",
        }
    )
    monkeypatch.setattr(trusted_identity, "get_trusted_identity_resolver", lambda: resolver)

    principal = trusted_identity.resolve_trusted_principal(
        {"identity_session_id": session_id}, "document-reviewer", int(time.time())
    )
    assert principal["subject"] == "reviewer-1"
    with pytest.raises(TrustedIdentityError) as missing_role:
        trusted_identity.resolve_trusted_principal(
            {"identity_session_id": session_id}, "waiver-authorizer", int(time.time())
        )
    assert missing_role.value.code == "trusted_authorizer_required"

    with pytest.raises(TrustedIdentityError) as forged:
        trusted_identity.resolve_trusted_principal(
            {"identity_session_id": session_id, "principal": {"subject": "fake"}},
            "document-reviewer",
            int(time.time()),
        )
    assert forged.value.code == "client_identity_forbidden"

    disabled = TrustedIdentityResolver({"enabled": False}, production=True)
    monkeypatch.setattr(trusted_identity, "get_trusted_identity_resolver", lambda: disabled)
    with pytest.raises(TrustedIdentityError) as unavailable:
        trusted_identity.resolve_trusted_principal({}, "document-reviewer", int(time.time()))
    assert unavailable.value.code == "trusted_identity_provider_required"


def test_authorizer_handoff_requires_provider_switch_and_distinct_principal():
    now = [time.time()]
    private, jwk = _key_material()
    holder = {"private": private, "jwk": jwk, "token": ""}
    resolver = _resolver(now, holder, config=_config(authorizer_handoff_mode="select_account"))
    redirect_uri = _config()["redirect_uris"][0]
    context = {
        "run_id": "run-1",
        "acceptance_sha256": "a" * 64,
        "delivery_binding_sha256": "b" * 64,
        "disallowed_principal_id": "reviewer-1",
    }
    started = resolver.start_login(
        redirect_uri,
        purpose="authorizer_handoff",
        binding_context=context,
    )
    query = parse_qs(urlparse(started["authorization_url"]).query)
    assert query["prompt"] == ["select_account"]
    claims = {
        "iss": _config()["issuer"], "aud": _config()["audience"], "sub": "reviewer-1",
        "iat": int(now[0]), "exp": int(now[0] + 600), "nonce": started["nonce"],
        "jti": "same-reviewer", "roles": ["waiver-authorizer"],
    }
    holder["token"] = jwt.encode(claims, private, algorithm="RS256", headers={"kid": jwk["kid"]})
    with pytest.raises(ValueError, match="distinct"):
        resolver.complete_login(state=started["state"], code="authorization-code")
    failed_status = resolver.status(None, started["flow_id"])
    assert failed_status["identity_flow_status"] == "authorizer_same_as_reviewer"
    assert failed_status["identity_flow_message"] == "仍是原验收人，请切换授权人账号"
    with pytest.raises(ValueError, match="state"):
        resolver.complete_login(state=started["state"], code="authorization-code")

    started = resolver.start_login(redirect_uri, purpose="authorizer_handoff", binding_context=context)
    claims.update({"sub": "authorizer-2", "nonce": started["nonce"], "jti": "new-authorizer"})
    holder["token"] = jwt.encode(claims, private, algorithm="RS256", headers={"kid": jwk["kid"]})
    completed = resolver.complete_login(state=started["state"], code="authorization-code")
    assert completed["principal"]["subject"] == "authorizer-2"
    assert completed["purpose"] == "authorizer_handoff"
    assert completed["binding_context"] == context
    resolved = resolver.resolve(completed["session_id"], required_role="waiver-authorizer")
    assert resolved["subject"] == "authorizer-2"
    first_claim = resolver.claim_authorizer_handoff(completed["session_id"], current_context=context)
    resolver.release_authorizer_handoff(completed["session_id"], first_claim)
    retry_claim = resolver.claim_authorizer_handoff(completed["session_id"], current_context=context)
    resolver.commit_authorizer_handoff(completed["session_id"], retry_claim)
    with pytest.raises(ValueError, match="used"):
        resolver.consume_authorizer_handoff(completed["session_id"], current_context=context)

    started = resolver.start_login(redirect_uri, purpose="authorizer_handoff", binding_context=context)
    claims.update({"sub": "authorizer-3", "nonce": started["nonce"], "jti": "drift-authorizer"})
    holder["token"] = jwt.encode(claims, private, algorithm="RS256", headers={"kid": jwk["kid"]})
    drifted = resolver.complete_login(state=started["state"], code="authorization-code")
    with pytest.raises(ValueError, match="identity_flow_stale"):
        resolver.consume_authorizer_handoff(
            drifted["session_id"], current_context={**context, "acceptance_sha256": "f" * 64}
        )


def test_stage_approval_uses_shared_trusted_principal_entrypoint():
    runtime_py = (__import__("pathlib").Path(__file__).resolve().parents[1] / "api/expert_teams/runtime.py").read_text()
    approval_block = runtime_py.split("def _approve_enterprise_stage", 1)[1].split("def approve_expert_team_stage", 1)[0]
    assert "resolve_trusted_principal(" in approval_block
    assert "get_trusted_identity_resolver().resolve(" not in approval_block
