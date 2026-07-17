"""Offline signed license validation for Taiji Agent trial builds."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import jwt
from cryptography.hazmat.primitives import serialization

import taiji_runtime_profile


SYSTEM_ACCOUNT_HOME_ERROR = (
    "Taiji Agent could not resolve the current account home "
    "from the system account database."
)


def _system_account_home() -> Path:
    if os.name != "posix":
        # The POSIX account database is unavailable on non-POSIX platforms.
        # This compatibility path is intentionally not used on Linux or macOS.
        return Path.home().resolve()

    try:
        import pwd

        raw_home = str(pwd.getpwuid(os.getuid()).pw_dir or "").strip()
    except (ImportError, KeyError, OSError) as exc:
        raise RuntimeError(SYSTEM_ACCOUNT_HOME_ERROR) from exc

    account_home = Path(raw_home)
    if not raw_home or not account_home.is_absolute() or not account_home.is_dir():
        raise RuntimeError(SYSTEM_ACCOUNT_HOME_ERROR)
    return account_home.resolve()


PRODUCT = "taiji-agent"
DEFAULT_LICENSE_FILENAME = "active-license.jwt"
DEFAULT_LICENSE_STATE_FILENAME = "license-state.json"
DEFAULT_LICENSE_DEVICE_FILENAME = "license-device.json"
INTERNAL_ISSUER_PUBLIC_KEY_RELATIVE = Path(
    "tools/taiji-license-issuer/private/signing-public.pem"
)
LICENSE_REQUIRED_ENV = "TAIJI_LICENSE_REQUIRED"
LICENSE_FILE_ENV = "TAIJI_LICENSE_FILE"
LICENSE_STATE_FILE_ENV = "TAIJI_LICENSE_STATE_FILE"
LICENSE_PUBLIC_KEY_ENV = "TAIJI_LICENSE_PUBLIC_KEY"
LICENSE_PUBLIC_KEY_FILE_ENV = "TAIJI_LICENSE_PUBLIC_KEY_FILE"
LICENSE_MACHINE_BINDING_REQUIRED_ENV = "TAIJI_LICENSE_MACHINE_BINDING_REQUIRED"
LICENSE_DEVICE_FILE_ENV = "TAIJI_LICENSE_DEVICE_FILE"
LICENSE_ALLOW_LEGACY_MACHINE_BINDING_ENV = "TAIJI_LICENSE_ALLOW_LEGACY_MACHINE_BINDING"
VERSION_ENV = "TAIJI_AGENT_VERSION"
PRODUCTION_LICENSE_POLICY_VERSION = 1
PRODUCTION_PUBLIC_KEY_PATH = Path("/opt/taiji-agent/resources/license/signing-public.pem")
PRODUCTION_PUBLIC_KEY_FINGERPRINT = "2dcff4f2b5e6f7a5e7e3f730e2f4446ad3265964431f614de7550265f7628b35"
PRODUCTION_VERSION_PATH = Path("/opt/taiji-agent/VERSION")
PRODUCTION_USER_HOME = _system_account_home()
PRODUCTION_LICENSE_PATH = (
    PRODUCTION_USER_HOME / ".config/taiji-agent/licenses/active-license.jwt"
)
PRODUCTION_LICENSE_DEVICE_PATH = (
    PRODUCTION_USER_HOME / ".config/taiji-agent/license-device.json"
)
PRODUCTION_LICENSE_STATE_PATH = (
    PRODUCTION_USER_HOME / ".local/state/taiji-agent/license-state.json"
)
PRODUCTION_SECURITY_OVERRIDE_ENVS = frozenset(
    {
        LICENSE_REQUIRED_ENV,
        LICENSE_PUBLIC_KEY_ENV,
        LICENSE_PUBLIC_KEY_FILE_ENV,
        LICENSE_MACHINE_BINDING_REQUIRED_ENV,
        LICENSE_ALLOW_LEGACY_MACHINE_BINDING_ENV,
    }
)
LICENSE_STATE_SCHEMA_VERSION = 1
LICENSE_DEVICE_SCHEMA_VERSION = 1
LICENSE_CLOCK_ROLLBACK_TOLERANCE_SECONDS = 300
LICENSE_STATE_WRITE_THROTTLE_SECONDS = 60
LEGACY_MACHINE_BINDING_TYPE_V1 = "machine_fingerprint_v1"
LEGACY_MACHINE_BINDING_TYPE_V2 = "machine_fingerprint_v2"
MACHINE_BINDING_TYPE = "machine_fingerprint_v3"
SUPPORTED_MACHINE_BINDING_TYPES = {
    LEGACY_MACHINE_BINDING_TYPE_V1,
    LEGACY_MACHINE_BINDING_TYPE_V2,
    MACHINE_BINDING_TYPE,
}
MACHINE_FINGERPRINT_SCHEMA_VERSION = 3
MACHINE_REQUEST_SCHEMA_VERSION = 3
MACHINE_REQUEST_TYPE = "taiji_machine_license_request"
ACTIVATION_MODE_OFFLINE_MACHINE_FILE = "offline_machine_file"
ACTIVATION_MODE_ONLINE_CODE = "online_code"
ACTIVATION_MODE_QR_PROXY = "qr_proxy"

MESSAGE_MISSING = "未安装有效授权，请联系服务方获取授权文件。"
MESSAGE_EXPIRED = "授权已到期，请联系服务方更新授权。"
MESSAGE_INVALID = "授权文件无效，请联系服务方更新授权。"
MESSAGE_NOT_YET_VALID = "授权尚未生效，请联系服务方确认授权时间。"
MESSAGE_VERSION_EXCEEDED = "当前版本不在授权范围内，请联系服务方更新授权。"
MESSAGE_CLOCK_ROLLBACK = "检测到系统时间异常，请校准本机时间后重试。"
MESSAGE_MACHINE_MISMATCH = "授权文件与本机不匹配，请联系服务方重新签发。"
MESSAGE_MACHINE_BINDING_REQUIRED = "授权文件缺少本机绑定信息，请联系服务方重新签发。"
MESSAGE_MACHINE_FINGERPRINT_UNAVAILABLE = "无法获取本机机器码，请联系服务方处理。"
MESSAGE_LEGACY_MACHINE_BINDING = "授权文件使用旧版机器绑定，请联系服务方使用新版机器码重新签发。"
MESSAGE_ONLINE_ACTIVATION_UNAVAILABLE = "联网激活将在后续版本支持。当前请使用离线授权文件。"
MESSAGE_POLICY_OVERRIDE_FORBIDDEN = "检测到不允许的授权策略覆盖，已停止执行。"
MESSAGE_PUBLIC_KEY_UNTRUSTED = "产品授权验签材料不可信，请联系管理员修复安装。"
MESSAGE_STATUS_UNAVAILABLE = "授权校验不可用，请联系管理员修复安装。"
MESSAGE_FILE_UNTRUSTED = "授权文件安全属性不符合要求，请重新导入授权。"
MESSAGE_STATE_UNTRUSTED = "授权状态文件安全属性不符合要求，请联系管理员处理。"
MESSAGE_DEVICE_UNTRUSTED = "设备身份文件安全属性不符合要求，请联系管理员处理。"
MESSAGE_VERSION_UNTRUSTED = "产品版本信息不可信，请联系管理员修复安装。"

_MACHINE_FINGERPRINT_CACHE: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class LicensePolicy:
    name: str
    version: int
    required: bool
    machine_binding_required: bool
    allow_legacy_machine_binding: bool
    public_key_path: Optional[Path] = None
    public_key_fingerprint: Optional[str] = None
    reject_environment_overrides: bool = False


def production_license_policy() -> LicensePolicy:
    """Return the immutable policy used by production entry points."""
    return LicensePolicy(
        name="production",
        version=PRODUCTION_LICENSE_POLICY_VERSION,
        required=True,
        machine_binding_required=True,
        allow_legacy_machine_binding=False,
        public_key_path=PRODUCTION_PUBLIC_KEY_PATH,
        public_key_fingerprint=PRODUCTION_PUBLIC_KEY_FINGERPRINT,
        reject_environment_overrides=True,
    )


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
    device_id_short: Optional[str] = None
    bound_device_id_short: Optional[str] = None
    fingerprint_quality: Optional[str] = None
    risk_flags: list[str] = field(default_factory=list)
    machine_label: Optional[str] = None
    activation_mode: Optional[str] = None
    activation_id: Optional[str] = None
    entitlement_id: Optional[str] = None
    policy: Optional[str] = None
    policy_version: Optional[int] = None
    signing_key_fingerprint_short: Optional[str] = None

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
            "device_id_short": self.device_id_short,
            "bound_device_id_short": self.bound_device_id_short,
            "fingerprint_quality": self.fingerprint_quality,
            "risk_flags": list(self.risk_flags),
            "machine_label": self.machine_label,
            "activation_mode": self.activation_mode,
            "activation_id": self.activation_id,
            "entitlement_id": self.entitlement_id,
            "policy": self.policy,
            "policy_version": self.policy_version,
            "signing_key_fingerprint_short": self.signing_key_fingerprint_short,
        }
        return {key: value for key, value in payload.items() if value is not None}


class LicenseExecutionBlocked(RuntimeError):
    """Stable error raised when a final execution guard denies a turn."""

    status_code = 403

    def __init__(self, status: LicenseStatus) -> None:
        self.status = status
        self.code = status.code or "license_invalid"
        self.message = status.message or MESSAGE_INVALID
        super().__init__(self.message)


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


def legacy_machine_binding_allowed(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return _env_truthy(env.get(LICENSE_ALLOW_LEGACY_MACHINE_BINDING_ENV, ""))


def default_license_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = environ if environ is not None else os.environ
    override = str(env.get(LICENSE_FILE_ENV, "")).strip()
    if override:
        return Path(override).expanduser()
    config_home = str(env.get("XDG_CONFIG_HOME", "")).strip()
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / PRODUCT / "licenses" / DEFAULT_LICENSE_FILENAME


def runtime_license_path() -> Path:
    if taiji_runtime_profile.is_installed_production():
        return PRODUCTION_LICENSE_PATH
    return default_license_path()


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


def default_license_device_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    if taiji_runtime_profile.is_installed_production():
        return PRODUCTION_LICENSE_DEVICE_PATH
    env = environ if environ is not None else os.environ
    override = str(env.get(LICENSE_DEVICE_FILE_ENV, "")).strip()
    if override:
        return Path(override).expanduser()
    config_home = str(env.get("XDG_CONFIG_HOME", "")).strip()
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / PRODUCT / DEFAULT_LICENSE_DEVICE_FILENAME


def _hash_id(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_filename_part(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-.")
    return (text or fallback)[:72]


def _filename_timestamp(value: Any) -> str:
    timestamp = _claim_timestamp({"value": value}, "value")
    if timestamp is None:
        timestamp = time.time()
    stamp = _iso_timestamp(timestamp) or "unknown"
    return stamp.replace("-", "").replace(":", "").replace("T", "-")


def machine_request_filename(request: Mapping[str, Any]) -> str:
    customer = _safe_filename_part(request.get("customer"), "customer")
    label = _safe_filename_part(
        request.get("machine_label") or request.get("hostname"),
        "terminal",
    )
    short = _safe_filename_part(request.get("machine_code_short"), "machine")
    generated = _filename_timestamp(request.get("generated_at"))
    return f"taiji-machine-request-{customer}-{label}-{short}-{generated}.json"


def _read_license_device(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != LICENSE_DEVICE_SCHEMA_VERSION:
        return None
    secret = _optional_str(data.get("device_secret"))
    device_id = _optional_str(data.get("device_id"))
    instance_id = _optional_str(data.get("device_instance_id"))
    if not secret or not device_id or device_id != _hash_id(f"{PRODUCT}:device:{secret}"):
        return None
    return {
        "device_secret": secret,
        "device_id": device_id,
        "device_id_short": _machine_code_short(device_id),
        "device_instance_id": instance_id or "",
        "created_at": _optional_str(data.get("created_at")),
    }


def _write_license_device(path: Path, *, now_ts: float) -> dict[str, Any]:
    secret = secrets.token_hex(32)
    device_id = _hash_id(f"{PRODUCT}:device:{secret}")
    data = {
        "schema_version": LICENSE_DEVICE_SCHEMA_VERSION,
        "product": PRODUCT,
        "device_secret": secret,
        "device_id": device_id,
        "device_instance_id": f"dev-{uuid.uuid4().hex}",
        "created_at": _iso_timestamp(now_ts),
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{int(time.time() * 1000000)}.tmp")
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
    return _read_license_device(path) or {
        "device_secret": secret,
        "device_id": device_id,
        "device_id_short": _machine_code_short(device_id),
        "device_instance_id": data["device_instance_id"],
        "created_at": data["created_at"],
    }


def _load_or_create_license_device(
    *,
    environ: Optional[Mapping[str, str]],
    now_ts: float,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    path = default_license_device_path(environ)
    existing = _read_license_device(path)
    if existing:
        return existing, None
    try:
        return _write_license_device(path, now_ts=now_ts), None
    except OSError as exc:
        return None, str(exc)


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


def _collect_virtualization_risk_flags() -> list[str]:
    dmi_paths = [
        Path("/sys/class/dmi/id/sys_vendor"),
        Path("/sys/class/dmi/id/product_name"),
        Path("/sys/class/dmi/id/board_vendor"),
        Path("/sys/class/dmi/id/chassis_vendor"),
    ]
    values = [value for value in (_read_machine_file(path) for path in dmi_paths) if value]
    text = " ".join(values).lower()
    markers = (
        "vmware",
        "virtualbox",
        "kvm",
        "qemu",
        "bochs",
        "xen",
        "parallels",
        "hyper-v",
        "openstack",
        "cloud",
        "virtual",
    )
    return ["virtualized_environment_detected"] if any(marker in text for marker in markers) else []


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


def _machine_code_for_binding(
    machine_fingerprint: Mapping[str, Any],
    binding_type: Optional[str],
) -> Optional[str]:
    if not binding_type:
        return _optional_str(machine_fingerprint.get("machine_code"))
    if _optional_str(machine_fingerprint.get("binding_type")) == binding_type:
        machine_code = _optional_str(machine_fingerprint.get("machine_code"))
        if _valid_machine_code(machine_code):
            return machine_code
    alternates = machine_fingerprint.get("alternate_machine_codes")
    if isinstance(alternates, list):
        for item in alternates:
            if not isinstance(item, Mapping):
                continue
            if _optional_str(item.get("binding_type")) != binding_type:
                continue
            machine_code = _optional_str(item.get("machine_code"))
            if _valid_machine_code(machine_code):
                return machine_code
    return None


def _collect_machine_components() -> tuple[list[tuple[str, str]], list[dict[str, Any]], list[str], list[str]]:
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
    risk_flags = _collect_virtualization_risk_flags()
    component_names = {name for name, _ in components}
    if not components:
        risk_flags.append("no_stable_hardware")
    elif component_names == {"machine_id"}:
        risk_flags.append("machine_id_only")
    return sorted(components), signals, sorted(set(macs)), sorted(set(risk_flags))


def _machine_code_from_components(*, binding_type: str, components: list[tuple[str, str]]) -> Optional[str]:
    if not components:
        return None
    material = json.dumps(
        {
            "product": PRODUCT,
            "binding_type": binding_type,
            "components": sorted(components),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _fingerprint_from_components(
    *,
    components: list[tuple[str, str]],
    signals: list[dict[str, Any]],
    now_ts: float,
    device: Optional[Mapping[str, Any]],
    risk_flags: list[str],
) -> dict[str, Any]:
    hostname = socket.gethostname() or ""
    device_secret = _optional_str((device or {}).get("device_secret"))
    device_id = _optional_str((device or {}).get("device_id"))
    device_components = list(components)
    if device_secret:
        device_components.append(("license_device_secret", device_secret))
        signals = [*signals, {"name": "license_device_secret", "available": True}]
    else:
        signals = [*signals, {"name": "license_device_secret", "available": False}]
        risk_flags = [*risk_flags, "no_device_secret"]
    hardware_code = _machine_code_from_components(
        binding_type="hardware_fingerprint_v3",
        components=components,
    )
    machine_code = _machine_code_from_components(
        binding_type=MACHINE_BINDING_TYPE,
        components=device_components,
    )
    stable_hardware_available = any(name in {"dmi_product_uuid", "dmi_board_serial", "machine_id", "macos_platform_uuid"} for name, _ in components)
    fingerprint_quality = "strong" if device_secret and stable_hardware_available else "weak"
    return {
        "binding_type": MACHINE_BINDING_TYPE,
        "collection_version": MACHINE_FINGERPRINT_SCHEMA_VERSION,
        "generated_at": _iso_timestamp(now_ts),
        "hostname": hostname,
        "machine_code": machine_code,
        "machine_code_short": _machine_code_short(machine_code),
        "device_id": device_id,
        "device_id_short": _machine_code_short(device_id),
        "hardware_code": hardware_code,
        "hardware_code_short": _machine_code_short(hardware_code),
        "fingerprint_quality": fingerprint_quality,
        "risk_flags": sorted(set(risk_flags)),
        "signals": signals,
    }


def _coerce_machine_fingerprint(machine_fingerprint: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if machine_fingerprint is None:
        return get_machine_fingerprint()
    machine_code = _optional_str(machine_fingerprint.get("machine_code"))
    if machine_code and not _valid_machine_code(machine_code):
        machine_code = None
    alternate_machine_codes: list[dict[str, Any]] = []
    alternates = machine_fingerprint.get("alternate_machine_codes")
    if isinstance(alternates, list):
        for item in alternates:
            if not isinstance(item, Mapping):
                continue
            alternate_binding_type = _optional_str(item.get("binding_type"))
            alternate_machine_code = _optional_str(item.get("machine_code"))
            if alternate_binding_type not in SUPPORTED_MACHINE_BINDING_TYPES:
                continue
            if not _valid_machine_code(alternate_machine_code):
                continue
            alternate_machine_codes.append(
                {
                    "binding_type": alternate_binding_type,
                    "machine_code": alternate_machine_code,
                    "machine_code_short": _optional_str(item.get("machine_code_short"))
                    or _machine_code_short(alternate_machine_code),
                }
            )
    return {
        "binding_type": _optional_str(machine_fingerprint.get("binding_type")) or MACHINE_BINDING_TYPE,
        "collection_version": machine_fingerprint.get("collection_version") or MACHINE_FINGERPRINT_SCHEMA_VERSION,
        "generated_at": _optional_str(machine_fingerprint.get("generated_at")),
        "hostname": _optional_str(machine_fingerprint.get("hostname")),
        "machine_code": machine_code,
        "machine_code_short": _optional_str(machine_fingerprint.get("machine_code_short"))
        or _machine_code_short(machine_code),
        "device_id": _optional_str(machine_fingerprint.get("device_id")),
        "device_id_short": _optional_str(machine_fingerprint.get("device_id_short"))
        or _machine_code_short(machine_fingerprint.get("device_id")),
        "hardware_code": _optional_str(machine_fingerprint.get("hardware_code")),
        "hardware_code_short": _optional_str(machine_fingerprint.get("hardware_code_short"))
        or _machine_code_short(machine_fingerprint.get("hardware_code")),
        "fingerprint_quality": _optional_str(machine_fingerprint.get("fingerprint_quality")),
        "risk_flags": [
            str(item).strip()
            for item in machine_fingerprint.get("risk_flags", [])
            if str(item).strip()
        ]
        if isinstance(machine_fingerprint.get("risk_flags"), list)
        else [],
        "signals": machine_fingerprint.get("signals") if isinstance(machine_fingerprint.get("signals"), list) else [],
        "alternate_machine_codes": alternate_machine_codes,
    }


def get_machine_fingerprint(
    *,
    now: Optional[float] = None,
    use_cache: bool = True,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    global _MACHINE_FINGERPRINT_CACHE
    if use_cache and now is None and environ is None and _MACHINE_FINGERPRINT_CACHE is not None:
        return dict(_MACHINE_FINGERPRINT_CACHE)
    now_ts = time.time() if now is None else float(now)
    components, signals, macs, risk_flags = _collect_machine_components()
    device, device_error = _load_or_create_license_device(environ=environ, now_ts=now_ts)
    if device_error:
        risk_flags = [*risk_flags, "device_secret_unavailable"]
    fingerprint = _fingerprint_from_components(
        components=components,
        signals=signals,
        now_ts=now_ts,
        device=device,
        risk_flags=risk_flags,
    )
    legacy_components = sorted([*components, *(("physical_mac", mac) for mac in macs)])
    legacy_machine_code = _machine_code_from_components(
        binding_type=LEGACY_MACHINE_BINDING_TYPE_V1,
        components=legacy_components,
    )
    legacy_v2_machine_code = _machine_code_from_components(
        binding_type=LEGACY_MACHINE_BINDING_TYPE_V2,
        components=components,
    )
    fingerprint["alternate_machine_codes"] = []
    if _valid_machine_code(legacy_machine_code):
        fingerprint["alternate_machine_codes"].append(
            {
                "binding_type": LEGACY_MACHINE_BINDING_TYPE_V1,
                "machine_code": legacy_machine_code,
                "machine_code_short": _machine_code_short(legacy_machine_code),
            }
        )
    if _valid_machine_code(legacy_v2_machine_code):
        fingerprint["alternate_machine_codes"].append(
            {
                "binding_type": LEGACY_MACHINE_BINDING_TYPE_V2,
                "machine_code": legacy_v2_machine_code,
                "machine_code_short": _machine_code_short(legacy_v2_machine_code),
            }
        )
    if use_cache and now is None and environ is None:
        _MACHINE_FINGERPRINT_CACHE = dict(fingerprint)
    return fingerprint


def build_machine_request(
    *,
    customer: str = "",
    machine_label: str = "",
    machine_fingerprint: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    now_ts = time.time() if now is None else float(now)
    fingerprint = _coerce_machine_fingerprint(machine_fingerprint) if machine_fingerprint is not None else get_machine_fingerprint(now=now_ts, use_cache=False, environ=environ)
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
    request = {
        "schema_version": MACHINE_REQUEST_SCHEMA_VERSION,
        "request_id": f"mreq-{uuid.uuid4().hex}",
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
        "device_id": _optional_str(fingerprint.get("device_id")) or "",
        "device_id_short": _optional_str(fingerprint.get("device_id_short"))
        or _machine_code_short(fingerprint.get("device_id")),
        "hardware_code_short": _optional_str(fingerprint.get("hardware_code_short"))
        or _machine_code_short(fingerprint.get("hardware_code")),
        "fingerprint_quality": _optional_str(fingerprint.get("fingerprint_quality")) or "unknown",
        "risk_flags": list(fingerprint.get("risk_flags") or []),
        "signals": safe_signals,
    }
    request["suggested_filename"] = machine_request_filename(request)
    return request


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
    raise OSError("license public key is unavailable")


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
    bound_binding_type = _optional_str(payload.get("binding_type"))
    if machine_fingerprint:
        local_machine_code = _machine_code_for_binding(machine_fingerprint, bound_binding_type) or _optional_str(
            machine_fingerprint.get("machine_code")
        )
        local_machine_code_short = _machine_code_short(local_machine_code) or _optional_str(
            machine_fingerprint.get("machine_code_short")
        )
    bound_machine_code = _optional_str(payload.get("machine_code"))
    bound_machine_code_short = _machine_code_short(bound_machine_code)
    local_device_id = _optional_str(machine_fingerprint.get("device_id")) if machine_fingerprint else None
    bound_device_id = _optional_str(payload.get("device_id"))
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
        device_id_short=_machine_code_short(local_device_id),
        bound_device_id_short=_machine_code_short(bound_device_id),
        fingerprint_quality=_optional_str(machine_fingerprint.get("fingerprint_quality")) if machine_fingerprint else None,
        risk_flags=list(machine_fingerprint.get("risk_flags") or []) if machine_fingerprint else [],
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
    allow_legacy_machine_binding: bool,
) -> Optional[LicenseStatus]:
    binding_type = _optional_str(payload.get("binding_type"))
    bound_machine_code = _optional_str(payload.get("machine_code"))
    bound_device_id = _optional_str(payload.get("device_id"))
    has_binding_claim = bool(binding_type or bound_machine_code)
    has_complete_binding = binding_type in SUPPORTED_MACHINE_BINDING_TYPES and _valid_machine_code(bound_machine_code)
    if binding_type == MACHINE_BINDING_TYPE and not _valid_machine_code(bound_device_id):
        has_complete_binding = False

    if binding_type in {LEGACY_MACHINE_BINDING_TYPE_V1, LEGACY_MACHINE_BINDING_TYPE_V2} and not allow_legacy_machine_binding:
        return _status(
            "invalid",
            required=required,
            code="license_legacy_machine_binding",
            message=MESSAGE_LEGACY_MACHINE_BINDING,
            payload=payload,
            now_ts=now_ts,
            machine_binding_required=machine_binding_required,
            machine_fingerprint=machine_fingerprint,
            machine_matched=False,
        )

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

    local_machine_code = _machine_code_for_binding(machine_fingerprint, binding_type)
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

    if binding_type == MACHINE_BINDING_TYPE:
        local_device_id = _optional_str(machine_fingerprint.get("device_id"))
        if not _valid_machine_code(local_device_id):
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
        if bound_device_id != local_device_id:
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


class _LicensePublicKeyError(Exception):
    pass


class _LicenseUserResourceError(Exception):
    pass


class _LicenseVersionError(Exception):
    pass


def _validate_production_user_file(path: Path, *, required: bool) -> bool:
    """Validate a canonical user-owned resource without following links."""
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        if required:
            raise _LicenseUserResourceError from None
        return False
    except OSError:
        raise _LicenseUserResourceError from None

    uid = os.getuid()
    if (
        stat.S_ISLNK(file_stat.st_mode)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != uid
        or stat.S_IMODE(file_stat.st_mode) != 0o600
        or file_stat.st_nlink != 1
    ):
        raise _LicenseUserResourceError
    try:
        if path.resolve(strict=True) != path.absolute():
            raise _LicenseUserResourceError
        for parent in path.parents:
            parent_stat = parent.lstat()
            if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
                raise _LicenseUserResourceError
            if parent_stat.st_uid not in {0, uid}:
                raise _LicenseUserResourceError
            if parent_stat.st_mode & 0o022:
                raise _LicenseUserResourceError
    except OSError:
        raise _LicenseUserResourceError from None
    return True


def _public_key_fingerprint(public_key_pem: str) -> str:
    key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    der = key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def _load_production_public_key(policy: LicensePolicy) -> str:
    path = policy.public_key_path
    expected = str(policy.public_key_fingerprint or "").lower()
    if path != PRODUCTION_PUBLIC_KEY_PATH or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise _LicensePublicKeyError
    try:
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise _LicensePublicKeyError
        if file_stat.st_uid != 0 or stat.S_IMODE(file_stat.st_mode) != 0o644:
            raise _LicensePublicKeyError
        for parent in path.parents:
            parent_stat = parent.lstat()
            if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
                raise _LicensePublicKeyError
            if parent_stat.st_uid != 0 or parent_stat.st_mode & 0o022:
                raise _LicensePublicKeyError
        public_key_pem = path.read_text(encoding="utf-8").strip()
        actual = _public_key_fingerprint(public_key_pem)
    except (OSError, ValueError, TypeError):
        raise _LicensePublicKeyError from None
    if not hmac.compare_digest(actual, expected):
        raise _LicensePublicKeyError
    return public_key_pem


def _load_production_version() -> str:
    path = PRODUCTION_VERSION_PATH
    try:
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise _LicenseVersionError
        if file_stat.st_uid != 0 or stat.S_IMODE(file_stat.st_mode) != 0o644:
            raise _LicenseVersionError
        for parent in path.parents:
            parent_stat = parent.lstat()
            if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
                raise _LicenseVersionError
            if parent_stat.st_uid != 0 or parent_stat.st_mode & 0o022:
                raise _LicenseVersionError
        version = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise _LicenseVersionError from None
    if re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version) is None:
        raise _LicenseVersionError
    return version


def _explicit_license_policy(env: Mapping[str, str]) -> LicensePolicy:
    required = license_required(env)
    return LicensePolicy(
        name="explicit",
        version=PRODUCTION_LICENSE_POLICY_VERSION,
        required=required,
        machine_binding_required=license_machine_binding_required(env, required=required),
        allow_legacy_machine_binding=legacy_machine_binding_allowed(env),
    )


def _source_development_status() -> LicenseStatus:
    policy = LicensePolicy(
        name=taiji_runtime_profile.installation_profile(),
        version=PRODUCTION_LICENSE_POLICY_VERSION,
        required=False,
        machine_binding_required=False,
        allow_legacy_machine_binding=False,
    )
    return _attach_policy(
        LicenseStatus(
            status="not_required",
            required=False,
            code="license_not_required",
            machine_binding_required=False,
        ),
        policy,
    )


def _attach_policy(status: LicenseStatus, policy: LicensePolicy) -> LicenseStatus:
    fingerprint = str(policy.public_key_fingerprint or "")
    return replace(
        status,
        policy=policy.name,
        policy_version=policy.version,
        signing_key_fingerprint_short=fingerprint[:12] or None,
    )


def _policy_error_status(policy: LicensePolicy, *, code: str, message: str) -> LicenseStatus:
    return _attach_policy(
        LicenseStatus(
            status="invalid",
            required=policy.required,
            code=code,
            message=message,
            machine_binding_required=policy.machine_binding_required,
        ),
        policy,
    )


def _is_implicit_production_request(
    *,
    path: Optional[os.PathLike[str] | str],
    state_path: Optional[os.PathLike[str] | str],
    public_key: Optional[str],
    now: Optional[float],
    environ: Optional[Mapping[str, str]],
    check_state: bool,
    machine_fingerprint: Optional[Mapping[str, Any]],
) -> bool:
    return (
        path is None
        and state_path is None
        and public_key is None
        and now is None
        and environ is None
        and check_state
        and machine_fingerprint is None
    )


def _load_license_status_impl(
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
    allow_legacy_binding = legacy_machine_binding_allowed(env)
    local_machine_fingerprint = _coerce_machine_fingerprint(machine_fingerprint) if machine_fingerprint is not None else get_machine_fingerprint(environ=env)
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
        allow_legacy_machine_binding=allow_legacy_binding,
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
    production = _is_implicit_production_request(
        path=path,
        state_path=state_path,
        public_key=public_key,
        now=now,
        environ=environ,
        check_state=check_state,
        machine_fingerprint=machine_fingerprint,
    )
    if production and not taiji_runtime_profile.is_installed_production():
        return _source_development_status()
    policy = production_license_policy() if production else _explicit_license_policy(env)

    if production:
        if policy.reject_environment_overrides and any(
            name in env for name in PRODUCTION_SECURITY_OVERRIDE_ENVS
        ):
            return _policy_error_status(
                policy,
                code="license_policy_override_forbidden",
                message=MESSAGE_POLICY_OVERRIDE_FORBIDDEN,
            )
        license_path = PRODUCTION_LICENSE_PATH
        try:
            license_exists = _validate_production_user_file(license_path, required=False)
        except _LicenseUserResourceError:
            return _policy_error_status(
                policy,
                code="license_file_untrusted",
                message=MESSAGE_FILE_UNTRUSTED,
            )
        if not license_exists:
            return _attach_policy(
                LicenseStatus(
                    status="missing",
                    required=True,
                    code="license_missing",
                    message=MESSAGE_MISSING,
                    machine_binding_required=True,
                ),
                policy,
            )
        try:
            _validate_production_user_file(PRODUCTION_LICENSE_STATE_PATH, required=False)
        except _LicenseUserResourceError:
            return _policy_error_status(
                policy,
                code="license_state_untrusted",
                message=MESSAGE_STATE_UNTRUSTED,
            )
        try:
            _validate_production_user_file(PRODUCTION_LICENSE_DEVICE_PATH, required=False)
        except _LicenseUserResourceError:
            return _policy_error_status(
                policy,
                code="license_device_untrusted",
                message=MESSAGE_DEVICE_UNTRUSTED,
            )
        try:
            resolved_public_key = _load_production_public_key(policy)
        except _LicensePublicKeyError:
            return _policy_error_status(
                policy,
                code="license_public_key_untrusted",
                message=MESSAGE_PUBLIC_KEY_UNTRUSTED,
            )
        try:
            product_version = _load_production_version()
        except _LicenseVersionError:
            return _policy_error_status(
                policy,
                code="license_product_version_untrusted",
                message=MESSAGE_VERSION_UNTRUSTED,
            )
        validation_env = dict(env)
        validation_env[LICENSE_REQUIRED_ENV] = "1"
        validation_env[LICENSE_MACHINE_BINDING_REQUIRED_ENV] = "1"
        validation_env[LICENSE_ALLOW_LEGACY_MACHINE_BINDING_ENV] = "0"
        validation_env[VERSION_ENV] = product_version
        validation_env[LICENSE_DEVICE_FILE_ENV] = str(PRODUCTION_LICENSE_DEVICE_PATH)
        validation_env.pop(LICENSE_PUBLIC_KEY_ENV, None)
        validation_env.pop(LICENSE_PUBLIC_KEY_FILE_ENV, None)
        validation_env.pop(LICENSE_FILE_ENV, None)
        validation_env.pop(LICENSE_STATE_FILE_ENV, None)
        status = _load_license_status_impl(
            path=license_path,
            state_path=PRODUCTION_LICENSE_STATE_PATH,
            public_key=resolved_public_key,
            now=now,
            environ=validation_env,
            check_state=check_state,
            machine_fingerprint=machine_fingerprint,
        )
    else:
        if public_key:
            try:
                policy = replace(policy, public_key_fingerprint=_public_key_fingerprint(public_key))
            except (ValueError, TypeError):
                pass
        status = _load_license_status_impl(
            path=path,
            state_path=state_path,
            public_key=public_key,
            now=now,
            environ=env,
            check_state=check_state,
            machine_fingerprint=machine_fingerprint,
        )
    return _attach_policy(status, policy)


def validate_license_candidate(path: os.PathLike[str] | str) -> LicenseStatus:
    """Validate an import candidate without allowing it to select runtime policy."""
    candidate = Path(path).expanduser()
    if not taiji_runtime_profile.is_installed_production():
        validation_env = dict(os.environ)
        validation_env[LICENSE_REQUIRED_ENV] = "1"
        validation_env[LICENSE_MACHINE_BINDING_REQUIRED_ENV] = "1"
        validation_env[LICENSE_ALLOW_LEGACY_MACHINE_BINDING_ENV] = "0"
        return load_license_status(
            path=candidate,
            check_state=False,
            environ=validation_env,
        )

    policy = production_license_policy()
    if any(name in os.environ for name in PRODUCTION_SECURITY_OVERRIDE_ENVS):
        return _policy_error_status(
            policy,
            code="license_policy_override_forbidden",
            message=MESSAGE_POLICY_OVERRIDE_FORBIDDEN,
        )
    try:
        _validate_production_user_file(candidate, required=True)
    except _LicenseUserResourceError:
        return _policy_error_status(
            policy,
            code="license_file_untrusted",
            message=MESSAGE_FILE_UNTRUSTED,
        )
    try:
        _validate_production_user_file(PRODUCTION_LICENSE_DEVICE_PATH, required=False)
    except _LicenseUserResourceError:
        return _policy_error_status(
            policy,
            code="license_device_untrusted",
            message=MESSAGE_DEVICE_UNTRUSTED,
        )
    try:
        public_key = _load_production_public_key(policy)
    except _LicensePublicKeyError:
        return _policy_error_status(
            policy,
            code="license_public_key_untrusted",
            message=MESSAGE_PUBLIC_KEY_UNTRUSTED,
        )
    try:
        product_version = _load_production_version()
    except _LicenseVersionError:
        return _policy_error_status(
            policy,
            code="license_product_version_untrusted",
            message=MESSAGE_VERSION_UNTRUSTED,
        )
    validation_env = dict(os.environ)
    validation_env[LICENSE_REQUIRED_ENV] = "1"
    validation_env[LICENSE_MACHINE_BINDING_REQUIRED_ENV] = "1"
    validation_env[LICENSE_ALLOW_LEGACY_MACHINE_BINDING_ENV] = "0"
    validation_env[VERSION_ENV] = product_version
    validation_env[LICENSE_DEVICE_FILE_ENV] = str(PRODUCTION_LICENSE_DEVICE_PATH)
    validation_env.pop(LICENSE_PUBLIC_KEY_ENV, None)
    validation_env.pop(LICENSE_PUBLIC_KEY_FILE_ENV, None)
    validation_env.pop(LICENSE_FILE_ENV, None)
    validation_env.pop(LICENSE_STATE_FILE_ENV, None)
    status = _load_license_status_impl(
        path=candidate,
        state_path=PRODUCTION_LICENSE_STATE_PATH,
        public_key=public_key,
        environ=validation_env,
        check_state=False,
    )
    return _attach_policy(status, policy)


def require_license_for_validation(
    *,
    path: Optional[os.PathLike[str] | str] = None,
    state_path: Optional[os.PathLike[str] | str] = None,
    public_key: Optional[str] = None,
    now: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
    machine_fingerprint: Optional[Mapping[str, Any]] = None,
) -> Optional[LicenseStatus]:
    status = load_license_status(
        path=path,
        state_path=state_path,
        public_key=public_key,
        now=now,
        environ=environ,
        machine_fingerprint=machine_fingerprint,
    )
    env = environ if environ is not None else os.environ
    if not status.required:
        return None
    if status.status == "valid":
        if status.policy == "production":
            license_path = PRODUCTION_LICENSE_PATH
            license_state_path = PRODUCTION_LICENSE_STATE_PATH
        else:
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
            return replace(
                status,
                status="invalid",
                code="license_state_invalid",
                message=MESSAGE_CLOCK_ROLLBACK,
            )
        return None
    return status


def require_valid_license() -> Optional[LicenseStatus]:
    """Apply the build-selected runtime policy without caller-controlled inputs."""
    return require_license_for_validation()
