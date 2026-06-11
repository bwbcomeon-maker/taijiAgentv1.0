"""Offline signed license validation for Taiji Agent trial builds."""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import jwt


PRODUCT = "taiji-agent"
DEFAULT_LICENSE_FILENAME = "license.jwt"
LICENSE_REQUIRED_ENV = "TAIJI_LICENSE_REQUIRED"
LICENSE_FILE_ENV = "TAIJI_LICENSE_FILE"
LICENSE_PUBLIC_KEY_ENV = "TAIJI_LICENSE_PUBLIC_KEY"
LICENSE_PUBLIC_KEY_FILE_ENV = "TAIJI_LICENSE_PUBLIC_KEY_FILE"
VERSION_ENV = "TAIJI_AGENT_VERSION"

DEFAULT_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuDxQwbVMj7rey/sh63tG
0bx2ayLLm+9pKdPQiGfeRq3UMblwXwiZCulwsuelLjBEfco/dQcF51Zg/0GZVrMk
CDZ3PymkvgJQmDEeDYl7T4jlZY+86O/bvvYr0Pwa+GzlThIZn87Z5DU4Sm0usZmJ
lK5OjGA8Zmdfy8Zb+Lpuhpbx88B3M0Ok32kQOqCBPRC0UNdpny+9ltTBGIDiVOeR
jnchmZEgjSaML8a0WdMfoaJlT2nGqyhJlYw48QQMAjvrNSxYfhLsY4OQ/WTSH/8a
cl4Xsn82KQeIPjcs+lWyzOh81VjSklcENvkaPgu48amjpUcjOKben+u+pu5U7WvC
ywIDAQAB
-----END PUBLIC KEY-----"""


MESSAGE_MISSING = "未安装有效授权，请联系服务方获取授权文件。"
MESSAGE_EXPIRED = "授权已到期，请联系服务方更新授权。"
MESSAGE_INVALID = "授权文件无效，请联系服务方更新授权。"
MESSAGE_NOT_YET_VALID = "授权尚未生效，请联系服务方确认授权时间。"
MESSAGE_VERSION_EXCEEDED = "当前版本不在授权范围内，请联系服务方更新授权。"


@dataclass(frozen=True)
class LicenseStatus:
    status: str
    required: bool
    code: Optional[str] = None
    message: str = ""
    license_id: Optional[str] = None
    customer: Optional[str] = None
    product: Optional[str] = None
    issued_at: Optional[str] = None
    not_before: Optional[str] = None
    expires_at: Optional[str] = None
    remaining_days: Optional[int] = None
    features: list[str] = field(default_factory=list)
    max_version: Optional[str] = None

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "required": self.required,
            "code": self.code,
            "message": self.message,
            "license_id": self.license_id,
            "customer": self.customer,
            "product": self.product,
            "issued_at": self.issued_at,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "remaining_days": self.remaining_days,
            "features": list(self.features),
            "max_version": self.max_version,
        }
        return {key: value for key, value in payload.items() if value is not None}


def license_required(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(LICENSE_REQUIRED_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def default_license_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = environ if environ is not None else os.environ
    override = str(env.get(LICENSE_FILE_ENV, "")).strip()
    if override:
        return Path(override).expanduser()
    config_home = str(env.get("XDG_CONFIG_HOME", "")).strip()
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / PRODUCT / DEFAULT_LICENSE_FILENAME


def _public_key_from_env(environ: Optional[Mapping[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    inline = str(env.get(LICENSE_PUBLIC_KEY_ENV, "")).strip()
    if inline:
        return inline
    public_key_path = str(env.get(LICENSE_PUBLIC_KEY_FILE_ENV, "")).strip()
    if public_key_path:
        return Path(public_key_path).expanduser().read_text(encoding="utf-8").strip()
    return DEFAULT_PUBLIC_KEY_PEM


def _status(
    status: str,
    *,
    required: bool,
    code: Optional[str] = None,
    message: str = "",
    payload: Optional[Mapping[str, Any]] = None,
    now_ts: Optional[float] = None,
) -> LicenseStatus:
    payload = payload or {}
    exp_ts = _claim_timestamp(payload, "exp", "expires_at")
    nbf_ts = _claim_timestamp(payload, "nbf", "not_before")
    iat_ts = _claim_timestamp(payload, "iat", "issued_at")
    remaining_days = None
    if exp_ts is not None and now_ts is not None:
        remaining_days = max(0, int(math.ceil((exp_ts - now_ts) / 86400)))
    return LicenseStatus(
        status=status,
        required=required,
        code=code,
        message=message,
        license_id=_optional_str(payload.get("license_id")),
        customer=_optional_str(payload.get("customer")),
        product=_optional_str(payload.get("product")),
        issued_at=_iso_timestamp(iat_ts),
        not_before=_iso_timestamp(nbf_ts),
        expires_at=_iso_timestamp(exp_ts),
        remaining_days=remaining_days,
        features=_features(payload.get("features")),
        max_version=_optional_str(payload.get("max_version")),
    )


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _features(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _claim_timestamp(payload: Mapping[str, Any], *names: str) -> Optional[float]:
    for name in names:
        value = payload.get(name)
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            try:
                return float(text)
            except ValueError:
                pass
            try:
                normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
                return datetime.fromisoformat(normalized).timestamp()
            except ValueError:
                return None
    return None


def _iso_timestamp(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return None


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for raw in str(value).replace("-", ".").split("."):
        if not raw:
            continue
        digits = ""
        for ch in raw:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def _version_exceeds(current: str, maximum: str) -> bool:
    current_tuple = _version_tuple(current)
    maximum_tuple = _version_tuple(maximum)
    if not current_tuple or not maximum_tuple:
        return False
    length = max(len(current_tuple), len(maximum_tuple))
    return current_tuple + (0,) * (length - len(current_tuple)) > maximum_tuple + (0,) * (length - len(maximum_tuple))


def load_license_status(
    *,
    path: Optional[os.PathLike[str] | str] = None,
    public_key: Optional[str] = None,
    now: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> LicenseStatus:
    env = environ if environ is not None else os.environ
    required = license_required(env)
    license_path = Path(path).expanduser() if path is not None else default_license_path(env)
    now_ts = time.time() if now is None else float(now)

    if not license_path.exists():
        return _status("missing", required=required, code="license_missing", message=MESSAGE_MISSING)

    try:
        token = license_path.read_text(encoding="utf-8").strip()
    except OSError:
        return _status("invalid", required=required, code="license_unreadable", message=MESSAGE_INVALID)
    if not token:
        return _status("invalid", required=required, code="license_empty", message=MESSAGE_INVALID)

    try:
        key = public_key if public_key is not None else _public_key_from_env(env)
    except OSError:
        return _status("invalid", required=required, code="license_public_key_missing", message=MESSAGE_INVALID)

    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=PRODUCT,
            options={"verify_exp": False, "verify_nbf": False},
        )
    except jwt.InvalidAudienceError:
        return _status("invalid", required=required, code="license_invalid_audience", message=MESSAGE_INVALID)
    except jwt.InvalidSignatureError:
        return _status("invalid", required=required, code="license_invalid_signature", message=MESSAGE_INVALID)
    except jwt.InvalidTokenError:
        return _status("invalid", required=required, code="license_invalid", message=MESSAGE_INVALID)
    except Exception:
        return _status("invalid", required=required, code="license_invalid", message=MESSAGE_INVALID)

    if not isinstance(payload, dict):
        return _status("invalid", required=required, code="license_invalid", message=MESSAGE_INVALID)

    if payload.get("product") != PRODUCT:
        return _status(
            "invalid",
            required=required,
            code="license_invalid_product",
            message=MESSAGE_INVALID,
            payload=payload,
            now_ts=now_ts,
        )

    nbf_ts = _claim_timestamp(payload, "nbf", "not_before")
    if nbf_ts is not None and now_ts < nbf_ts:
        return _status(
            "invalid",
            required=required,
            code="license_not_yet_valid",
            message=MESSAGE_NOT_YET_VALID,
            payload=payload,
            now_ts=now_ts,
        )

    exp_ts = _claim_timestamp(payload, "exp", "expires_at")
    if exp_ts is None:
        return _status(
            "invalid",
            required=required,
            code="license_missing_expiry",
            message=MESSAGE_INVALID,
            payload=payload,
            now_ts=now_ts,
        )
    if now_ts >= exp_ts:
        return _status(
            "expired",
            required=required,
            code="license_expired",
            message=MESSAGE_EXPIRED,
            payload=payload,
            now_ts=now_ts,
        )

    max_version = _optional_str(payload.get("max_version"))
    current_version = _optional_str(env.get(VERSION_ENV))
    if max_version and current_version and _version_exceeds(current_version, max_version):
        return _status(
            "invalid",
            required=required,
            code="license_version_exceeded",
            message=MESSAGE_VERSION_EXCEEDED,
            payload=payload,
            now_ts=now_ts,
        )

    return _status("valid", required=required, payload=payload, now_ts=now_ts)


def require_valid_license(
    *,
    path: Optional[os.PathLike[str] | str] = None,
    public_key: Optional[str] = None,
    now: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[LicenseStatus]:
    status = load_license_status(path=path, public_key=public_key, now=now, environ=environ)
    if not status.required or status.status == "valid":
        return None
    return status
