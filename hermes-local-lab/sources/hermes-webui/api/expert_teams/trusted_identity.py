"""Fail-closed OIDC Authorization Code + PKCE identity for expert approvals."""

from __future__ import annotations

from copy import deepcopy
import base64
import hashlib
import json
import secrets
import threading
import time
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import jwt


PRINCIPAL_SCHEMA_VERSION = "trusted-principal/v1"
_REQUIRED_CONFIG = {
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "client_id",
    "audience",
    "redirect_uris",
    "algorithms",
    "role_claim",
    "allowed_roles",
}
IDENTITY_COOKIE_NAME = "taiji_expert_identity"
_GLOBAL_LOCK = threading.Lock()
_GLOBAL_RESOLVER: "TrustedIdentityResolver | None" = None
_GLOBAL_CONFIG_SHA256 = ""
_ROLE_ERROR_CODES = {
    "document-approver": "trusted_approver_required",
    "document-reviewer": "trusted_reviewer_required",
    "waiver-authorizer": "trusted_authorizer_required",
}


class TrustedIdentityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _default_token_client(*, config: dict, code: str, code_verifier: str, redirect_uri: str) -> dict:
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": config["client_id"],
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = Request(
        config["token_endpoint"],
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - endpoint is administrator allowlisted
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("OIDC token endpoint returned an invalid response")
    return payload


def _default_jwks_loader(*, config: dict) -> dict:
    uri = str(config.get("jwks_uri") or "").strip()
    if not uri:
        raise ValueError("OIDC JWKS endpoint is not configured")
    request = Request(uri, headers={"Accept": "application/json"})
    with urlopen(request, timeout=10) as response:  # noqa: S310 - endpoint is administrator allowlisted
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("OIDC JWKS endpoint returned an invalid response")
    return payload


class TrustedIdentityResolver:
    """The only interpreter of human approval identity in the expert-team path."""

    def __init__(
        self,
        config: dict | None,
        *,
        clock: Callable[[], float] | None = None,
        token_client: Callable[..., dict] | None = None,
        jwks_loader: Callable[..., dict] | None = None,
        production: bool = True,
    ):
        self._config = deepcopy(config) if isinstance(config, dict) else {"enabled": False}
        self._clock = clock or time.time
        self._token_client = token_client or _default_token_client
        self._jwks_loader = jwks_loader or _default_jwks_loader
        self._production = bool(production)
        self._flows: dict[str, dict] = {}
        self._sessions: dict[str, dict] = {}
        self._authorizer_handoffs: dict[str, dict] = {}
        self._authorizer_handoff_claims: dict[str, dict] = {}
        self._consumed_authorizer_handoffs: set[str] = set()
        self._lock = threading.RLock()
        if self._config.get("enabled"):
            self._validate_config()

    def _validate_config(self) -> None:
        if self._config.get("provider") != "oidc_pkce":
            raise ValueError("trusted identity provider must be oidc_pkce")
        missing = sorted(key for key in _REQUIRED_CONFIG if not self._config.get(key))
        if missing:
            raise ValueError(f"trusted identity config is missing {missing[0]}")
        algorithms = self._config.get("algorithms")
        if not isinstance(algorithms, list) or not algorithms or any(
            algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
            for algorithm in algorithms
        ):
            raise ValueError("trusted identity algorithms are not an exact safe allowlist")
        for field in ("redirect_uris", "allowed_roles"):
            value = self._config.get(field)
            if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"trusted identity {field} must be a non-empty string allowlist")

    def _require_enabled(self) -> None:
        if not self._config.get("enabled"):
            raise ValueError("trusted identity is disabled")

    def start_login(
        self,
        redirect_uri: str,
        *,
        purpose: str = "login",
        binding_context: dict | None = None,
    ) -> dict:
        self._require_enabled()
        redirect_uri = str(redirect_uri or "").strip()
        if redirect_uri not in self._config["redirect_uris"]:
            raise ValueError("OIDC redirect URI is not allowlisted")
        now = float(self._clock())
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        purpose = str(purpose or "login")
        context = deepcopy(binding_context) if isinstance(binding_context, dict) else {}
        if purpose == "authorizer_handoff":
            required = {"run_id", "acceptance_sha256", "delivery_binding_sha256", "disallowed_principal_id"}
            if set(context) != required or any(not str(context.get(key) or "").strip() for key in required):
                raise ValueError("authorizer handoff binding context is incomplete")
            if self._config.get("authorizer_handoff_mode") != "select_account":
                raise ValueError("authorizer handoff provider cannot switch accounts")
        elif purpose != "login" or context:
            raise ValueError("trusted identity login purpose is invalid")
        with self._lock:
            self._prune(now)
            self._flows[state] = {
                "nonce": nonce,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "expires_at": now + int(self._config.get("flow_ttl_seconds") or 300),
                "purpose": purpose,
                "binding_context": context,
            }
        params = {
            "response_type": "code",
            "client_id": self._config["client_id"],
            "redirect_uri": redirect_uri,
            "scope": "openid profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if purpose == "authorizer_handoff":
            params["prompt"] = "select_account"
        return {
            "authorization_url": f"{self._config['authorization_endpoint']}?{urlencode(params)}",
            "state": state,
            "nonce": nonce,
            "expires_in": int(self._config.get("flow_ttl_seconds") or 300),
        }

    def complete_login(self, *, state: str, code: str) -> dict:
        self._require_enabled()
        now = float(self._clock())
        with self._lock:
            self._prune(now)
            flow = self._flows.pop(str(state or ""), None)
        if not isinstance(flow, dict):
            raise ValueError("OIDC state is invalid or expired")
        if not str(code or "").strip():
            raise ValueError("OIDC authorization code is required")
        token_response = self._token_client(
            config=deepcopy(self._config),
            code=str(code),
            code_verifier=flow["code_verifier"],
            redirect_uri=flow["redirect_uri"],
        )
        token = str((token_response or {}).get("id_token") or "")
        if not token:
            raise ValueError("OIDC token response has no ID token")
        principal = self._verify_id_token(token, expected_nonce=flow["nonce"], now=now)
        if flow.get("purpose") == "authorizer_handoff":
            context = flow.get("binding_context") if isinstance(flow.get("binding_context"), dict) else {}
            if "waiver-authorizer" not in principal.get("roles", []):
                raise ValueError("trusted authorizer role is required")
            if principal.get("subject") == context.get("disallowed_principal_id"):
                raise ValueError("authorizer must be distinct from reviewer")
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[session_id] = deepcopy(principal)
            if flow.get("purpose") == "authorizer_handoff":
                context = deepcopy(flow.get("binding_context") or {})
                self._authorizer_handoffs[session_id] = {
                    "context": context,
                    "context_sha256": _digest(_canonical(context)),
                }
        result = {"session_id": session_id, "redirect_uri": flow["redirect_uri"], "principal": deepcopy(principal)}
        if flow.get("purpose") == "authorizer_handoff":
            result.update({
                "purpose": "authorizer_handoff",
                "binding_context": deepcopy(flow.get("binding_context") or {}),
            })
        return result

    def _verify_id_token(self, token: str, *, expected_nonce: str, now: float) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise ValueError("OIDC token header is invalid") from exc
        algorithm = str(header.get("alg") or "")
        kid = str(header.get("kid") or "")
        if algorithm not in self._config["algorithms"] or not kid:
            raise ValueError("OIDC token algorithm or key identity is not allowed")
        jwks = self._jwks_loader(config=deepcopy(self._config))
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        matches = [key for key in keys or [] if isinstance(key, dict) and key.get("kid") == kid]
        if len(matches) != 1 or matches[0].get("alg") not in (None, algorithm):
            raise ValueError("OIDC signing key is unavailable or ambiguous")
        jwk = matches[0]
        try:
            signing_key = jwt.PyJWK.from_dict(jwk, algorithm=algorithm).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[algorithm],
                audience=self._config["audience"],
                issuer=self._config["issuer"],
                leeway=int(self._config.get("clock_skew_seconds") or 0),
                options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce", "jti"]},
            )
        except jwt.PyJWTError as exc:
            raise ValueError("OIDC signature, issuer, audience, or time validation failed") from exc
        if not secrets.compare_digest(str(claims.get("nonce") or ""), str(expected_nonce)):
            raise ValueError("OIDC nonce validation failed")
        roles = claims.get(self._config["role_claim"])
        if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
            raise ValueError("OIDC role claim is invalid")
        allowed = set(self._config["allowed_roles"])
        if not roles or not set(roles).issubset(allowed):
            raise ValueError("OIDC role is not in the exact allowlist")
        principal = {
            "schema_version": PRINCIPAL_SCHEMA_VERSION,
            "subject": str(claims["sub"]),
            "display_name": str(claims.get("name") or claims["sub"]),
            "issuer": str(claims["iss"]),
            "audience": deepcopy(claims["aud"]),
            "roles": sorted(set(roles)),
            "authenticated_at": int(claims["iat"]),
            "expires_at": int(claims["exp"]),
            "credential_jti_sha256": _digest(claims["jti"]),
            "key_fingerprint": _digest(_canonical(jwk)),
            "auth_method": "oidc_pkce",
        }
        principal["identity_snapshot_sha256"] = _digest(_canonical(principal))
        if principal["expires_at"] <= now:
            raise ValueError("trusted identity is expired")
        return principal

    def resolve(self, session_id: str, *, required_role: str) -> dict:
        self._require_enabled()
        now = float(self._clock())
        with self._lock:
            principal = deepcopy(self._sessions.get(str(session_id or "")))
        if not isinstance(principal, dict):
            raise ValueError("trusted identity session is missing")
        if principal.get("expires_at", 0) <= now:
            with self._lock:
                self._sessions.pop(str(session_id or ""), None)
            raise ValueError("trusted identity session is expired")
        if str(required_role or "") not in principal.get("roles", []):
            raise ValueError("trusted identity role is not authorized")
        return principal

    def status(self, session_id: str | None) -> dict:
        if not self._config.get("enabled"):
            return {"enabled": False, "authenticated": False, "provider": "disabled"}
        try:
            with self._lock:
                self._prune(float(self._clock()))
                principal = deepcopy(self._sessions.get(str(session_id or "")))
        except Exception:
            principal = None
        return {
            "enabled": True,
            "authenticated": isinstance(principal, dict),
            "provider": "oidc_pkce",
            "authorizer_handoff_ready": self._config.get("authorizer_handoff_mode") == "select_account",
            "principal": principal if isinstance(principal, dict) else None,
        }

    def logout(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(str(session_id or ""), None)
            self._authorizer_handoffs.pop(str(session_id or ""), None)
            self._authorizer_handoff_claims.pop(str(session_id or ""), None)

    def claim_authorizer_handoff(self, session_id: str, *, current_context: dict) -> str:
        key = str(session_id or "")
        context = deepcopy(current_context) if isinstance(current_context, dict) else {}
        with self._lock:
            if key in self._consumed_authorizer_handoffs:
                raise ValueError("authorizer handoff was already used")
            handoff = self._authorizer_handoffs.get(key)
            if not isinstance(handoff, dict):
                raise ValueError("authorizer handoff is missing or was already used")
            if handoff.get("context_sha256") != _digest(_canonical(context)) or handoff.get("context") != context:
                raise ValueError("identity_flow_stale")
            existing = self._authorizer_handoff_claims.get(key)
            if isinstance(existing, dict):
                if existing.get("context") != context:
                    raise ValueError("identity_flow_stale")
                return str(existing["claim_id"])
            claim_id = "handoff-claim-" + secrets.token_urlsafe(16)
            self._authorizer_handoff_claims[key] = {"claim_id": claim_id, "context": context}
            return claim_id

    def commit_authorizer_handoff(self, session_id: str, claim_id: str) -> dict:
        key = str(session_id or "")
        with self._lock:
            claim = self._authorizer_handoff_claims.get(key)
            if not isinstance(claim, dict) or claim.get("claim_id") != str(claim_id or ""):
                raise ValueError("authorizer handoff claim is missing")
            handoff = self._authorizer_handoffs.pop(key, None)
            if not isinstance(handoff, dict):
                raise ValueError("authorizer handoff is missing or was already used")
            self._authorizer_handoff_claims.pop(key, None)
            self._consumed_authorizer_handoffs.add(key)
            return deepcopy(handoff)

    def release_authorizer_handoff(self, session_id: str, claim_id: str) -> None:
        key = str(session_id or "")
        with self._lock:
            claim = self._authorizer_handoff_claims.get(key)
            if isinstance(claim, dict) and claim.get("claim_id") == str(claim_id or ""):
                self._authorizer_handoff_claims.pop(key, None)

    def consume_authorizer_handoff(self, session_id: str, *, current_context: dict) -> dict:
        claim_id = self.claim_authorizer_handoff(session_id, current_context=current_context)
        return self.commit_authorizer_handoff(session_id, claim_id)

    def install_test_principal(self, principal: dict) -> str:
        if self._production:
            raise RuntimeError("test trusted identity resolver is forbidden in production")
        session_id = "test-" + secrets.token_urlsafe(16)
        value = deepcopy(principal)
        value.setdefault("schema_version", PRINCIPAL_SCHEMA_VERSION)
        value.setdefault("expires_at", int(self._clock()) + 3600)
        value.setdefault("identity_snapshot_sha256", _digest(_canonical(value)))
        with self._lock:
            self._sessions[session_id] = value
        return session_id

    def _prune(self, now: float) -> None:
        self._flows = {
            key: value for key, value in self._flows.items() if float(value.get("expires_at") or 0) > now
        }
        self._sessions = {
            key: value for key, value in self._sessions.items() if float(value.get("expires_at") or 0) > now
        }
        self._authorizer_handoffs = {
            key: value for key, value in self._authorizer_handoffs.items() if key in self._sessions
        }
        self._authorizer_handoff_claims = {
            key: value for key, value in self._authorizer_handoff_claims.items() if key in self._authorizer_handoffs
        }


def load_trusted_identity_config() -> dict:
    try:
        from api.config import _get_config_path, _load_yaml_config_file

        path = _get_config_path()
        config = _load_yaml_config_file(path) if path.exists() else {}
    except Exception:
        return {"enabled": False}
    value = config.get("expert_team_trusted_identity") if isinstance(config, dict) else None
    return deepcopy(value) if isinstance(value, dict) else {"enabled": False}


def get_trusted_identity_resolver() -> TrustedIdentityResolver:
    global _GLOBAL_CONFIG_SHA256, _GLOBAL_RESOLVER
    config = load_trusted_identity_config()
    fingerprint = _digest(_canonical(config))
    with _GLOBAL_LOCK:
        if _GLOBAL_RESOLVER is None or _GLOBAL_CONFIG_SHA256 != fingerprint:
            _GLOBAL_RESOLVER = TrustedIdentityResolver(config, production=True)
            _GLOBAL_CONFIG_SHA256 = fingerprint
        return _GLOBAL_RESOLVER


def identity_session_from_cookie_header(cookie_header: str) -> str:
    for part in str(cookie_header or "").split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name == IDENTITY_COOKIE_NAME:
            return value.strip()
    return ""


def resolve_trusted_principal(request_context: dict, required_role: str, now: int | float) -> dict:
    """Resolve every enterprise human action through one fail-closed identity boundary."""

    context = deepcopy(request_context) if isinstance(request_context, dict) else {}
    forbidden = {
        "principal", "role", "roles", "headers", "authorization", "bearer_token",
        "reviewer", "authorizer", "approver",
    }
    if set(context) & forbidden:
        raise TrustedIdentityError("client_identity_forbidden", "客户端不得提交或覆盖可信身份")
    role = str(required_role or "").strip()
    if role not in _ROLE_ERROR_CODES:
        raise TrustedIdentityError("trusted_role_not_released", "请求的可信身份角色尚未开放")
    resolver = get_trusted_identity_resolver()
    status = resolver.status(None)
    if status.get("enabled") is not True:
        raise TrustedIdentityError("trusted_identity_provider_required", "企业可信身份提供方尚未配置")
    session_id = str(context.get("identity_session_id") or "").strip()
    if not session_id:
        raise TrustedIdentityError(_ROLE_ERROR_CODES[role], "缺少可信身份会话")
    try:
        principal = resolver.resolve(session_id, required_role=role)
    except ValueError as exc:
        raise TrustedIdentityError(_ROLE_ERROR_CODES[role], str(exc)) from exc
    if int(principal.get("expires_at") or 0) <= int(now):
        raise TrustedIdentityError(_ROLE_ERROR_CODES[role], "可信身份已过期")
    return deepcopy(principal)
