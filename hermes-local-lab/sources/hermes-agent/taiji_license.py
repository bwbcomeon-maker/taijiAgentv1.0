"""Offline signed license validation for Taiji Agent trial builds."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import jwt


PRODUCT = "taiji-agent"
DEFAULT_LICENSE_FILENAME = "license.jwt"
DEFAULT_LICENSE_STATE_FILENAME = "license-state.json"
INTERNAL_ISSUER_PUBLIC_KEY_RELATIVE = Path(
    "tools/taiji-license-issuer/private/signing-public.pem"
)
LICENSE_REQUIRED_ENV = "TAIJI_LICENSE_REQUIRED"
LICENSE_FILE_ENV = "TAIJI_LICENSE_FILE"
LICENSE_STATE_FILE_ENV = "TAIJI_LICENSE_STATE_FILE"
LICENSE_PUBLIC_KEY_ENV = "TAIJI_LICENSE_PUBLIC_KEY"
LICENSE_PUBLIC_KEY_FILE_ENV = "TAIJI_LICENSE_PUBLIC_KEY_FILE"
LICENSE_MACHINE_BINDING_REQUIRED_ENV = "TAIJI_LICENSE_MACHINE_BINDING_REQUIRED"
VERSION_ENV = "TAIJI_AGENT_VERSION"
LICENSE_STATE_SCHEMA_VERSION = 1
LICENSE_CLOCK_ROLLBACK_TOLERANCE_SECONDS = 300
LICENSE_STATE_WRITE_THROTTLE_SECONDS = 60
MACHINE_BINDING_TYPE = "machine_fingerprint_v1"
MACHINE_FINGERPRINT_SCHEMA_VERSION = 1
MACHINE_REQUEST_SCHEMA_VERSION = 1
MACHINE_REQUEST_TYPE = "taiji_machine_license_request"
ACTIVATION_MODE_OFFLINE_MACHINE_FILE = "offline_machine_file"
ACTIVATION_MODE_ONLINE_CODE = "online_code"
ACTIVATION_MODE_QR_PROXY = "qr_proxy"

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
MESSAGE_MACHINE_MISMATCH = "授权文件与本机不匹配，请联系服务方重新签发。"
MESSAGE_MACHINE_BINDING_REQUIRED = "授权文件缺少本机绑定信息，请联系服务方重新签发。"
MESSAGE_MACHINE_FINGERPRINT_UNAVAILABLE = "无法获取本机机器码，请联系服务方处理。"
MESSAGE_ONLINE_ACTIVATION_UNAVAILABLE = "联网激活将在后续版本支持。当前请使用离线授权文件。"

_MACHINE_FINGERPRINT_CACHE: Optional[dict[str, Any]] = None


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
    machine_binding_required: Optional[bool] = None
    machine_bound: Optional[bool] = None
    machine_matched: Optional[bool] = None
    machine_code_short: Optional[str] = None
    bound_machine_code_short: Optional[str] = None
    machine_label: Optional[str] = None
    activation_mode: Optional[str] = None
    activation_id: Optional[str] = None
    entitlement_id: Optional[str] = None

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
            "machine_binding_required": self.machine_binding_required,
            "machine_bound": self.machine_bound,
            "machine_matched": self.machine_matched,
            "machine_code_short": self.machine_code_short,
            "bound_machine_code_short": self.bound_machine_code_short,
            "machine_label": self.machine_label,
            "activation_mode": self.activation_mode,
            "activation_id": self.activation_id,
            "entitlement_id": self.entitlement_id,
        }
        return {key: value for key, value in payload.items() if value is not None}


def _env_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_falsey(value: Any) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "off"}


def license_required(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return _env_truthy(env.get(LICENSE_REQUIRED_ENV, ""))


def license_machine_binding_required(
    environ: Optional[Mapping[str, str]] = None,
    *,
    required: Optional[bool] = None,
) -> bool:
    env = environ if environ is not None else os.environ
    configured = env.get(LICENSE_MACHINE_BINDING_REQUIRED_ENV)
    if configured is not None and str(configured).strip():
        return not _env_falsey(configured)
    return bool(license_required(env) if required is None else required)


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


def _clean_machine_signal(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    invalid = {
        "none",
        "null",
        "unknown",
        "not specified",
        "to be filled by o.e.m.",
        "to be filled by oem",
        "default string",
        "system serial number",
    }
    if text in invalid:
        return None
    compact = re.sub(r"[^0-9a-f]", "", text)
    if compact and set(compact) == {"0"}:
        return None
    return text


def _read_machine_file(path: Path) -> Optional[str]:
    try:
        return _clean_machine_signal(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return None


def _normalize_mac(value: Any) -> Optional[str]:
    text = re.sub(r"[^0-9a-f]", "", str(value or "").strip().lower())
    if len(text) != 12:
        return None
    if set(text) in ({"0"}, {"f"}):
        return None
    # Multicast/random locally administered bits are still useful as a weak
    # fallback, but virtual interfaces are filtered by interface name/device.
    return ":".join(text[index : index + 2] for index in range(0, 12, 2))


def _is_virtual_interface_name(name: str) -> bool:
    lowered = name.strip().lower()
    if lowered in {"lo", "sit0"}:
        return True
    return lowered.startswith(
        (
            "docker",
            "br-",
            "veth",
            "virbr",
            "vmnet",
            "vboxnet",
            "zt",
            "tun",
            "tap",
            "tailscale",
            "utun",
            "llw",
            "awdl",
            "bridge",
        )
    )


def _collect_linux_physical_macs() -> list[str]:
    root = Path("/sys/class/net")
    if not root.exists():
        return []
    macs: list[str] = []
    for iface in sorted(root.iterdir(), key=lambda item: item.name):
        if _is_virtual_interface_name(iface.name):
            continue
        # On Linux, physical NICs normally have a backing device symlink.
        if not (iface / "device").exists():
            continue
        mac = _normalize_mac(_read_machine_file(iface / "address"))
        if mac:
            macs.append(mac)
    return sorted(set(macs))


def _collect_uuid_node_mac() -> list[str]:
    try:
        node = uuid.getnode()
    except Exception:
        return []
    if not isinstance(node, int) or node <= 0:
        return []
    mac = _normalize_mac(f"{node:012x}")
    return [mac] if mac else []


def _collect_macos_platform_uuid() -> Optional[str]:
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', result.stdout or "")
    if not match:
        return None
    return _clean_machine_signal(match.group(1))


def _machine_code_short(machine_code: Any) -> Optional[str]:
    text = _optional_str(machine_code)
    if not text:
        return None
    if text.startswith("sha256:"):
        return text.split(":", 1)[1][:12]
    return text[:12]


def _valid_machine_code(machine_code: Any) -> bool:
    text = _optional_str(machine_code)
    return bool(text and re.fullmatch(r"sha256:[0-9a-f]{64}", text))


def _collect_machine_components() -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    components: list[tuple[str, str]] = []
    signals: list[dict[str, Any]] = []

    def add_file_signal(name: str, path: Path) -> None:
        value = _read_machine_file(path)
        signals.append({"name": name, "available": bool(value)})
        if value:
            components.append((name, value))

    add_file_signal("dmi_product_uuid", Path("/sys/class/dmi/id/product_uuid"))
    add_file_signal("dmi_board_serial", Path("/sys/class/dmi/id/board_serial"))

    machine_id = _read_machine_file(Path("/etc/machine-id")) or _read_machine_file(Path("/var/lib/dbus/machine-id"))
    signals.append({"name": "machine_id", "available": bool(machine_id)})
    if machine_id:
        components.append(("machine_id", machine_id))

    macos_platform_uuid = _collect_macos_platform_uuid()
    signals.append({"name": "macos_platform_uuid", "available": bool(macos_platform_uuid)})
    if macos_platform_uuid:
        components.append(("macos_platform_uuid", macos_platform_uuid))

    macs = _collect_linux_physical_macs()
    if not macs and not components and os.name != "posix":
        macs = _collect_uuid_node_mac()
    if not macs and not components and not Path("/sys/class/net").exists():
        macs = _collect_uuid_node_mac()
    signals.append({"name": "physical_mac", "available": bool(macs), "count": len(macs)})
    components.extend(("physical_mac", mac) for mac in macs)
    return sorted(components), signals


def _fingerprint_from_components(
    *,
    components: list[tuple[str, str]],
    signals: list[dict[str, Any]],
    now_ts: float,
) -> dict[str, Any]:
    hostname = socket.gethostname() or ""
    machine_code: Optional[str] = None
    if components:
        material = json.dumps(
            {
                "product": PRODUCT,
                "binding_type": MACHINE_BINDING_TYPE,
                "components": components,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        machine_code = "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    return {
        "binding_type": MACHINE_BINDING_TYPE,
        "collection_version": MACHINE_FINGERPRINT_SCHEMA_VERSION,
        "generated_at": _iso_timestamp(now_ts),
        "hostname": hostname,
        "machine_code": machine_code,
        "machine_code_short": _machine_code_short(machine_code),
        "signals": signals,
    }


def _coerce_machine_fingerprint(machine_fingerprint: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if machine_fingerprint is None:
        return get_machine_fingerprint()
    machine_code = _optional_str(machine_fingerprint.get("machine_code"))
    if machine_code and not _valid_machine_code(machine_code):
        machine_code = None
    return {
        "binding_type": _optional_str(machine_fingerprint.get("binding_type")) or MACHINE_BINDING_TYPE,
        "collection_version": machine_fingerprint.get("collection_version") or MACHINE_FINGERPRINT_SCHEMA_VERSION,
        "generated_at": _optional_str(machine_fingerprint.get("generated_at")),
        "hostname": _optional_str(machine_fingerprint.get("hostname")),
        "machine_code": machine_code,
        "machine_code_short": _optional_str(machine_fingerprint.get("machine_code_short"))
        or _machine_code_short(machine_code),
        "signals": machine_fingerprint.get("signals") if isinstance(machine_fingerprint.get("signals"), list) else [],
    }


def get_machine_fingerprint(
    *,
    now: Optional[float] = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    global _MACHINE_FINGERPRINT_CACHE
    if use_cache and now is None and _MACHINE_FINGERPRINT_CACHE is not None:
        return dict(_MACHINE_FINGERPRINT_CACHE)
    now_ts = time.time() if now is None else float(now)
    components, signals = _collect_machine_components()
    fingerprint = _fingerprint_from_components(components=components, signals=signals, now_ts=now_ts)
    if use_cache and now is None:
        _MACHINE_FINGERPRINT_CACHE = dict(fingerprint)
    return fingerprint


def build_machine_request(
    *,
    customer: str = "",
    machine_label: str = "",
    machine_fingerprint: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    now_ts = time.time() if now is None else float(now)
    fingerprint = _coerce_machine_fingerprint(machine_fingerprint) if machine_fingerprint is not None else get_machine_fingerprint(now=now_ts, use_cache=False)
    machine_code = _optional_str(fingerprint.get("machine_code"))
    if not _valid_machine_code(machine_code):
        raise RuntimeError(MESSAGE_MACHINE_FINGERPRINT_UNAVAILABLE)
    signals = fingerprint.get("signals") if isinstance(fingerprint.get("signals"), list) else []
    safe_signals: list[dict[str, Any]] = []
    for item in signals:
        if not isinstance(item, Mapping):
            continue
        safe: dict[str, Any] = {
            "name": _optional_str(item.get("name")) or "unknown",
            "available": bool(item.get("available")),
        }
        count = item.get("count")
        if isinstance(count, int):
            safe["count"] = count
        safe_signals.append(safe)
    return {
        "schema_version": MACHINE_REQUEST_SCHEMA_VERSION,
        "request_type": MACHINE_REQUEST_TYPE,
        "product": PRODUCT,
        "binding_type": MACHINE_BINDING_TYPE,
        "collection_version": MACHINE_FINGERPRINT_SCHEMA_VERSION,
        "generated_at": _iso_timestamp(now_ts),
        "customer": str(customer or "").strip(),
        "machine_label": str(machine_label or "").strip(),
        "hostname": _optional_str(fingerprint.get("hostname")) or "",
        "machine_code": machine_code,
        "machine_code_short": _machine_code_short(machine_code),
        "signals": safe_signals,
    }


def _public_key_from_env(environ: Optional[Mapping[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    inline = str(env.get(LICENSE_PUBLIC_KEY_ENV, "")).strip()
    if inline:
        return inline
    public_key_path = str(env.get(LICENSE_PUBLIC_KEY_FILE_ENV, "")).strip()
    if public_key_path:
        return Path(public_key_path).expanduser().read_text(encoding="utf-8").strip()
    checkout_key = _source_checkout_internal_issuer_public_key(env)
    if checkout_key:
        return checkout_key
    return DEFAULT_PUBLIC_KEY_PEM


def _source_checkout_internal_issuer_public_key(env: Mapping[str, str]) -> Optional[str]:
    agent_root = str(env.get("TAIJI_AGENT_ROOT", "")).strip()
    if not agent_root:
        return None
    root = Path(agent_root).expanduser()
    repo_root = root.parent if root.name == "hermes-local-lab" else root
    if not (repo_root / ".git").exists():
        return None
    public_key_path = repo_root / INTERNAL_ISSUER_PUBLIC_KEY_RELATIVE
    try:
        if public_key_path.is_file():
            return public_key_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return None


def _status(
    status: str,
    *,
    required: bool,
    code: Optional[str] = None,
    message: str = "",
    payload: Optional[Mapping[str, Any]] = None,
    now_ts: Optional[float] = None,
    machine_binding_required: Optional[bool] = None,
    machine_fingerprint: Optional[Mapping[str, Any]] = None,
    machine_matched: Optional[bool] = None,
) -> LicenseStatus:
    payload = payload or {}
    exp_ts = _claim_timestamp(payload, "exp", "expires_at")
    nbf_ts = _claim_timestamp(payload, "nbf", "not_before")
    iat_ts = _claim_timestamp(payload, "iat", "issued_at")
    remaining_days = None
    if exp_ts is not None and now_ts is not None:
        remaining_days = max(0, int(math.ceil((exp_ts - now_ts) / 86400)))
    local_machine_code = None
    local_machine_code_short = None
    if machine_fingerprint:
        local_machine_code = _optional_str(machine_fingerprint.get("machine_code"))
        local_machine_code_short = _optional_str(machine_fingerprint.get("machine_code_short")) or _machine_code_short(local_machine_code)
    bound_machine_code = _optional_str(payload.get("machine_code"))
    bound_machine_code_short = _machine_code_short(bound_machine_code)
    machine_bound = bool(bound_machine_code)
    if machine_binding_required is None and (machine_bound or machine_fingerprint):
        machine_binding_required = False
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
        machine_binding_required=machine_binding_required,
        machine_bound=machine_bound if machine_binding_required is not None or machine_bound else None,
        machine_matched=machine_matched,
        machine_code_short=local_machine_code_short,
        bound_machine_code_short=bound_machine_code_short,
        machine_label=_optional_str(payload.get("machine_label")),
        activation_mode=_optional_str(payload.get("activation_mode")),
        activation_id=_optional_str(payload.get("activation_id")),
        entitlement_id=_optional_str(payload.get("entitlement_id")),
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


def _check_machine_binding(
    *,
    required: bool,
    payload: Mapping[str, Any],
    now_ts: float,
    machine_binding_required: bool,
    machine_fingerprint: Mapping[str, Any],
) -> Optional[LicenseStatus]:
    binding_type = _optional_str(payload.get("binding_type"))
    bound_machine_code = _optional_str(payload.get("machine_code"))
    has_binding_claim = bool(binding_type or bound_machine_code)
    has_complete_binding = binding_type == MACHINE_BINDING_TYPE and _valid_machine_code(bound_machine_code)

    if machine_binding_required and not has_complete_binding:
        return _status(
            "invalid",
            required=required,
            code="license_machine_binding_required",
            message=MESSAGE_MACHINE_BINDING_REQUIRED,
            payload=payload,
            now_ts=now_ts,
            machine_binding_required=machine_binding_required,
            machine_fingerprint=machine_fingerprint,
            machine_matched=False if has_binding_claim else None,
        )

    if not has_binding_claim:
        return None

    if not has_complete_binding:
        return _status(
            "invalid",
            required=required,
            code="license_machine_binding_required",
            message=MESSAGE_MACHINE_BINDING_REQUIRED,
            payload=payload,
            now_ts=now_ts,
            machine_binding_required=machine_binding_required,
            machine_fingerprint=machine_fingerprint,
            machine_matched=False,
        )

    local_machine_code = _optional_str(machine_fingerprint.get("machine_code"))
    if not _valid_machine_code(local_machine_code):
        return _status(
            "invalid",
            required=required,
            code="license_machine_fingerprint_unavailable",
            message=MESSAGE_MACHINE_FINGERPRINT_UNAVAILABLE,
            payload=payload,
            now_ts=now_ts,
            machine_binding_required=machine_binding_required,
            machine_fingerprint=machine_fingerprint,
            machine_matched=False,
        )

    if local_machine_code != bound_machine_code:
        return _status(
            "invalid",
            required=required,
            code="license_machine_mismatch",
            message=MESSAGE_MACHINE_MISMATCH,
            payload=payload,
            now_ts=now_ts,
            machine_binding_required=machine_binding_required,
            machine_fingerprint=machine_fingerprint,
            machine_matched=False,
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
    machine_fingerprint: Optional[Mapping[str, Any]] = None,
) -> LicenseStatus:
    env = environ if environ is not None else os.environ
    required = license_required(env)
    machine_required = license_machine_binding_required(env, required=required)
    local_machine_fingerprint = _coerce_machine_fingerprint(machine_fingerprint) if machine_fingerprint is not None else get_machine_fingerprint()
    license_path = Path(path).expanduser() if path is not None else default_license_path(env)
    license_state_path = Path(state_path).expanduser() if state_path is not None else default_license_state_path(env)
    now_ts = time.time() if now is None else float(now)

    if not license_path.exists():
        return _status(
            "missing",
            required=required,
            code="license_missing",
            message=MESSAGE_MISSING,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )

    try:
        token = license_path.read_text(encoding="utf-8").strip()
    except OSError:
        return _status(
            "invalid",
            required=required,
            code="license_unreadable",
            message=MESSAGE_INVALID,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )
    if not token:
        return _status(
            "invalid",
            required=required,
            code="license_empty",
            message=MESSAGE_INVALID,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )

    try:
        key = public_key if public_key is not None else _public_key_from_env(env)
    except OSError:
        return _status(
            "invalid",
            required=required,
            code="license_public_key_missing",
            message=MESSAGE_INVALID,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )

    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=PRODUCT,
            options={"verify_exp": False, "verify_nbf": False},
        )
    except jwt.InvalidAudienceError:
        return _status(
            "invalid",
            required=required,
            code="license_invalid_audience",
            message=MESSAGE_INVALID,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )
    except jwt.InvalidSignatureError:
        return _status(
            "invalid",
            required=required,
            code="license_invalid_signature",
            message=MESSAGE_INVALID,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )
    except jwt.InvalidTokenError:
        return _status(
            "invalid",
            required=required,
            code="license_invalid",
            message=MESSAGE_INVALID,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )
    except Exception:
        return _status(
            "invalid",
            required=required,
            code="license_invalid",
            message=MESSAGE_INVALID,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )

    if not isinstance(payload, dict):
        return _status(
            "invalid",
            required=required,
            code="license_invalid",
            message=MESSAGE_INVALID,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )

    if payload.get("product") != PRODUCT:
        return _status(
            "invalid",
            required=required,
            code="license_invalid_product",
            message=MESSAGE_INVALID,
            payload=payload,
            now_ts=now_ts,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
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
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
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
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )
    if now_ts >= exp_ts:
        return _status(
            "expired",
            required=required,
            code="license_expired",
            message=MESSAGE_EXPIRED,
            payload=payload,
            now_ts=now_ts,
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
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
            machine_binding_required=machine_required,
            machine_fingerprint=local_machine_fingerprint,
        )

    machine_status = _check_machine_binding(
        required=required,
        payload=payload,
        now_ts=now_ts,
        machine_binding_required=machine_required,
        machine_fingerprint=local_machine_fingerprint,
    )
    if machine_status is not None:
        return machine_status

    if check_state:
        clock_status = _check_license_clock(
            state_path=license_state_path,
            required=required,
            payload=payload,
            now_ts=now_ts,
        )
        if clock_status is not None:
            return clock_status

    machine_matched = True if _optional_str(payload.get("machine_code")) else None
    return _status(
        "valid",
        required=required,
        payload=payload,
        now_ts=now_ts,
        machine_binding_required=machine_required,
        machine_fingerprint=local_machine_fingerprint,
        machine_matched=machine_matched,
    )


def require_valid_license(
    *,
    path: Optional[os.PathLike[str] | str] = None,
    state_path: Optional[os.PathLike[str] | str] = None,
    public_key: Optional[str] = None,
    now: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
    machine_fingerprint: Optional[Mapping[str, Any]] = None,
) -> Optional[LicenseStatus]:
    env = environ if environ is not None else os.environ
    status = load_license_status(
        path=path,
        state_path=state_path,
        public_key=public_key,
        now=now,
        environ=env,
        machine_fingerprint=machine_fingerprint,
    )
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
                machine_binding_required=status.machine_binding_required,
                machine_bound=status.machine_bound,
                machine_matched=status.machine_matched,
                machine_code_short=status.machine_code_short,
                bound_machine_code_short=status.bound_machine_code_short,
                machine_label=status.machine_label,
                activation_mode=status.activation_mode,
                activation_id=status.activation_id,
                entitlement_id=status.entitlement_id,
            )
        return None
    return status
