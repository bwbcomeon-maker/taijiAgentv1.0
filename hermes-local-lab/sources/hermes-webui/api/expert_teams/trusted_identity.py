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

    def start_login(self, redirect_uri: str) -> dict:
        self._require_enabled()
        redirect_uri = str(redirect_uri or "").strip()
        if redirect_uri not in self._config["redirect_uris"]:
            raise ValueError("OIDC redirect URI is not allowlisted")
        now = float(self._clock())
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        with self._lock:
            self._prune(now)
            self._flows[state] = {
                "nonce": nonce,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "expires_at": now + int(self._config.get("flow_ttl_seconds") or 300),
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
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[session_id] = deepcopy(principal)
        return {"session_id": session_id, "redirect_uri": flow["redirect_uri"], "principal": deepcopy(principal)}

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
            "principal": principal if isinstance(principal, dict) else None,
        }

    def logout(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(str(session_id or ""), None)

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
