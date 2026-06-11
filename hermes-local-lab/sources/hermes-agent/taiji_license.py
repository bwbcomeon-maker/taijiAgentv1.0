"""Offline signed license validation for Taiji Agent trial builds."""

from __future__ import annotations

import hashlib
import json
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
DEFAULT_LICENSE_STATE_FILENAME = "license-state.json"
LICENSE_REQUIRED_ENV = "TAIJI_LICENSE_REQUIRED"
LICENSE_FILE_ENV = "TAIJI_LICENSE_FILE"
LICENSE_STATE_FILE_ENV = "TAIJI_LICENSE_STATE_FILE"
LICENSE_PUBLIC_KEY_ENV = "TAIJI_LICENSE_PUBLIC_KEY"
LICENSE_PUBLIC_KEY_FILE_ENV = "TAIJI_LICENSE_PUBLIC_KEY_FILE"
VERSION_ENV = "TAIJI_AGENT_VERSION"
LICENSE_STATE_SCHEMA_VERSION = 1
LICENSE_CLOCK_ROLLBACK_TOLERANCE_SECONDS = 300
LICENSE_STATE_WRITE_THROTTLE_SECONDS = 60

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
MESSAGE_CLOCK_ROLLBACK = "检测到系统时间异常，请校准本机时间后重试。"


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


def default_license_state_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = environ if environ is not None else os.environ
    override = str(env.get(LICENSE_STATE_FILE_ENV, "")).strip()
    if override:
        return Path(override).expanduser()
    state_dir = str(env.get("TAIJI_STATE_DIR", "")).strip()
    if state_dir:
        return Path(state_dir).expanduser() / DEFAULT_LICENSE_STATE_FILENAME
    state_home = str(env.get("XDG_STATE_HOME", "")).strip()
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / PRODUCT / DEFAULT_LICENSE_STATE_FILENAME


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


class _LicenseStateError(Exception):
    pass


def _license_hash(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read_license_state(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise _LicenseStateError from exc
    if not isinstance(data, dict):
        raise _LicenseStateError
    if data.get("schema_version") != LICENSE_STATE_SCHEMA_VERSION:
        raise _LicenseStateError
    last_value = data.get("last_successful_validation_at")
    if not isinstance(last_value, (int, float)) or not math.isfinite(float(last_value)):
        raise _LicenseStateError
    return data


def _last_successful_validation_at(state: Optional[Mapping[str, Any]]) -> Optional[float]:
    if state is None:
        return None
    value = state.get("last_successful_validation_at")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    raise _LicenseStateError


def _license_state_invalid_status(
    *,
    required: bool,
    payload: Optional[Mapping[str, Any]],
    now_ts: float,
    code: str,
) -> LicenseStatus:
    return _status(
        "invalid",
        required=required,
        code=code,
        message=MESSAGE_CLOCK_ROLLBACK,
        payload=payload,
        now_ts=now_ts,
    )


def _check_license_clock(
    *,
    state_path: Path,
    required: bool,
    payload: Mapping[str, Any],
    now_ts: float,
) -> Optional[LicenseStatus]:
    try:
        state = _read_license_state(state_path)
        last_ts = _last_successful_validation_at(state)
    except _LicenseStateError:
        return _license_state_invalid_status(
            required=required,
            payload=payload,
            now_ts=now_ts,
            code="license_state_invalid",
        )
    if last_ts is not None and now_ts < last_ts - LICENSE_CLOCK_ROLLBACK_TOLERANCE_SECONDS:
        return _license_state_invalid_status(
            required=required,
            payload=payload,
            now_ts=now_ts,
            code="license_clock_rollback",
        )
    return None


def _write_license_state(
    *,
    path: Path,
    now_ts: float,
    license_id: Optional[str],
    token: str,
) -> None:
    try:
        state = _read_license_state(path)
        last_ts = _last_successful_validation_at(state)
    except _LicenseStateError:
        raise

    now_int = int(now_ts)
    if last_ts is not None:
        if now_ts <= last_ts:
            return
        if now_ts - last_ts < LICENSE_STATE_WRITE_THROTTLE_SECONDS:
            return

    data = {
        "schema_version": LICENSE_STATE_SCHEMA_VERSION,
        "last_successful_validation_at": now_int,
        "last_successful_validation_iso": _iso_timestamp(now_int),
        "license_id": license_id,
        "license_hash": _license_hash(token),
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{int(time.time() * 1000000)}.tmp")
    try:
        tmp_path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise _LicenseStateError from exc


def load_license_status(
    *,
    path: Optional[os.PathLike[str] | str] = None,
    state_path: Optional[os.PathLike[str] | str] = None,
    public_key: Optional[str] = None,
    now: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
    check_state: bool = True,
) -> LicenseStatus:
    env = environ if environ is not None else os.environ
    required = license_required(env)
    license_path = Path(path).expanduser() if path is not None else default_license_path(env)
    license_state_path = Path(state_path).expanduser() if state_path is not None else default_license_state_path(env)
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

    if check_state:
        clock_status = _check_license_clock(
            state_path=license_state_path,
            required=required,
            payload=payload,
            now_ts=now_ts,
        )
        if clock_status is not None:
            return clock_status

    return _status("valid", required=required, payload=payload, now_ts=now_ts)


def require_valid_license(
    *,
    path: Optional[os.PathLike[str] | str] = None,
    state_path: Optional[os.PathLike[str] | str] = None,
    public_key: Optional[str] = None,
    now: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[LicenseStatus]:
    env = environ if environ is not None else os.environ
    status = load_license_status(path=path, state_path=state_path, public_key=public_key, now=now, environ=env)
    if not status.required:
        return None
    if status.status == "valid":
        license_path = Path(path).expanduser() if path is not None else default_license_path(env)
        license_state_path = Path(state_path).expanduser() if state_path is not None else default_license_state_path(env)
        try:
            token = license_path.read_text(encoding="utf-8").strip()
            _write_license_state(
                path=license_state_path,
                now_ts=time.time() if now is None else float(now),
                license_id=status.license_id,
                token=token,
            )
        except (OSError, _LicenseStateError):
            return LicenseStatus(
                status="invalid",
                required=status.required,
                code="license_state_invalid",
                message=MESSAGE_CLOCK_ROLLBACK,
                license_id=status.license_id,
                customer=status.customer,
                product=status.product,
                issued_at=status.issued_at,
                not_before=status.not_before,
                expires_at=status.expires_at,
                remaining_days=status.remaining_days,
                features=list(status.features),
                max_version=status.max_version,
            )
        return None
    return status
