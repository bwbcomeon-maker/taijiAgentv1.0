import builtins
import json
import inspect
import os
import stat
import sys
import types
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import taiji_license


TEST_MACHINE_CODE = "sha256:" + "a" * 64
OTHER_MACHINE_CODE = "sha256:" + "b" * 64
TEST_DEVICE_ID = "sha256:" + "1" * 64
TEST_MACHINE_FINGERPRINT = {
    "binding_type": "machine_fingerprint_v3",
    "machine_code": TEST_MACHINE_CODE,
    "machine_code_short": "aaaaaaaaaaaa",
    "device_id": TEST_DEVICE_ID,
    "device_id_short": "111111111111",
    "hardware_code": "sha256:" + "9" * 64,
    "hardware_code_short": "999999999999",
    "fingerprint_quality": "strong",
    "risk_flags": [],
    "hostname": "test-host",
    "generated_at": "2026-06-12T00:00:00Z",
    "collection_version": 3,
    "signals": [{"name": "machine_id", "available": True}],
}
LEGACY_V2_MACHINE_FINGERPRINT = {
    "binding_type": "machine_fingerprint_v2",
    "machine_code": TEST_MACHINE_CODE,
    "machine_code_short": "aaaaaaaaaaaa",
    "hostname": "test-host",
    "generated_at": "2026-06-12T00:00:00Z",
    "collection_version": 2,
    "signals": [{"name": "machine_id", "available": True}],
}
LEGACY_MACHINE_FINGERPRINT = {
    "binding_type": "machine_fingerprint_v1",
    "machine_code": TEST_MACHINE_CODE,
    "machine_code_short": "aaaaaaaaaaaa",
    "hostname": "test-host",
    "generated_at": "2026-06-12T00:00:00Z",
    "collection_version": 1,
    "signals": [{"name": "machine_id", "available": True}],
}
UNAVAILABLE_MACHINE_FINGERPRINT = {
    "binding_type": "machine_fingerprint_v2",
    "machine_code": None,
    "machine_code_short": None,
    "hostname": "test-host",
    "generated_at": "2026-06-12T00:00:00Z",
    "collection_version": 2,
    "signals": [{"name": "machine_id", "available": False}],
}


@pytest.fixture()
def signing_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_pem, public_pem


@pytest.fixture()
def installed_production_profile(monkeypatch):
    monkeypatch.setattr(
        taiji_license.taiji_runtime_profile,
        "is_installed_production",
        lambda: True,
    )
    monkeypatch.setattr(
        taiji_license.taiji_runtime_profile,
        "installation_profile",
        lambda: "installed-production",
    )


def _write_token(path, private_pem, **overrides):
    now = int(time.time())
    payload = {
        "license_id": "lic-test",
        "customer": "测试客户",
        "product": "taiji-agent",
        "aud": "taiji-agent",
        "binding_type": "machine_fingerprint_v3",
        "machine_code": TEST_MACHINE_CODE,
        "device_id": TEST_DEVICE_ID,
        "machine_label": "测试终端",
        "activation_mode": "offline_machine_file",
        "activation_id": "act-test",
        "entitlement_id": "ent-test",
        "iat": now - 60,
        "nbf": now - 60,
        "exp": now + 86400,
        "features": ["chat", "writing"],
    }
    payload.update(overrides)
    token = jwt.encode(payload, private_pem, algorithm="RS256")
    path.write_text(token, encoding="utf-8")
    return token


def _patch_source_runtime_resources(
    monkeypatch,
    tmp_path,
    public_key,
    machine_fingerprint=TEST_MACHINE_FINGERPRINT,
):
    license_path = tmp_path / "config/taiji-agent/licenses/active-license.jwt"
    state_path = tmp_path / "state/taiji-agent/license-state.json"
    device_path = tmp_path / "config/taiji-agent/license-device.json"
    real_lstat = taiji_license.Path.lstat
    normalized_temp_ancestors = {}
    for parent in tmp_path.parents:
        parent_stat = real_lstat(parent)
        if parent_stat.st_uid == 0 and stat.S_IMODE(parent_stat.st_mode) & 0o002:
            normalized_temp_ancestors[parent] = types.SimpleNamespace(
                st_mode=parent_stat.st_mode & ~0o022,
                st_uid=parent_stat.st_uid,
                st_gid=parent_stat.st_gid,
                st_nlink=parent_stat.st_nlink,
            )

    def isolated_runtime_lstat(path):
        if path in normalized_temp_ancestors:
            return normalized_temp_ancestors[path]
        return real_lstat(path)

    monkeypatch.setattr(taiji_license.Path, "lstat", isolated_runtime_lstat)
    monkeypatch.setattr(
        taiji_license.taiji_runtime_profile,
        "is_installed_production",
        lambda: False,
    )
    monkeypatch.setattr(
        taiji_license.taiji_runtime_profile,
        "installation_profile",
        lambda: "source-development",
    )
    monkeypatch.setattr(
        taiji_license,
        "PRODUCTION_LICENSE_PATH",
        license_path,
    )
    monkeypatch.setattr(taiji_license, "PRODUCTION_USER_HOME", tmp_path)
    monkeypatch.setattr(
        taiji_license,
        "PRODUCTION_LICENSE_STATE_PATH",
        state_path,
    )
    monkeypatch.setattr(
        taiji_license,
        "PRODUCTION_LICENSE_DEVICE_PATH",
        device_path,
    )
    monkeypatch.setattr(
        taiji_license,
        "_load_source_public_key",
        lambda _policy: public_key,
        raising=False,
    )
    monkeypatch.setattr(
        taiji_license,
        "_load_source_version",
        lambda: "1.0.0",
        raising=False,
    )
    monkeypatch.setattr(
        taiji_license,
        "get_machine_fingerprint",
        lambda **_kwargs: machine_fingerprint,
    )
    return license_path, state_path, device_path


def test_system_account_home_fails_closed_when_pwd_lookup_fails(
    monkeypatch, tmp_path
):
    if os.name != "posix":
        pytest.skip("POSIX account database is unavailable")
    pwd_module = pytest.importorskip("pwd")
    poisoned_home = tmp_path / "poisoned-home"
    poisoned_home.mkdir()
    monkeypatch.setenv("HOME", str(poisoned_home))

    def fail_pwd_lookup(_uid):
        raise KeyError("missing account")

    monkeypatch.setattr(pwd_module, "getpwuid", fail_pwd_lookup)

    with pytest.raises(RuntimeError, match="system account database"):
        taiji_license._system_account_home()


def test_windows_system_account_home_ignores_environment_and_closes_token(
    monkeypatch, tmp_path
):
    profile = tmp_path / "trusted-profile"
    profile.mkdir()
    token = object()
    calls = []
    monkeypatch.setattr(taiji_license, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        taiji_license.Path,
        "home",
        classmethod(lambda _cls: pytest.fail("Path.home must not be used")),
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "poisoned-userprofile"))
    monkeypatch.setenv("HOMEDRIVE", "Z:")
    monkeypatch.setenv("HOMEPATH", "\\poisoned")
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        types.SimpleNamespace(
            GetCurrentProcess=lambda: "process",
            CloseHandle=lambda handle: calls.append(("close", handle)),
        ),
    )
    monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(TOKEN_QUERY=8))
    monkeypatch.setitem(
        sys.modules,
        "win32security",
        types.SimpleNamespace(
            OpenProcessToken=lambda process, access: calls.append(
                ("open", process, access)
            )
            or token,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32profile",
        types.SimpleNamespace(
            GetUserProfileDirectory=lambda handle: calls.append(
                ("profile", handle)
            )
            or str(profile),
        ),
    )

    assert taiji_license._system_account_home() == profile.resolve()
    assert calls == [
        ("open", "process", 8),
        ("profile", token),
        ("close", token),
    ]


def test_windows_system_account_home_api_failure_closes_token_and_rejects(
    monkeypatch,
):
    token = object()
    closed = []
    monkeypatch.setattr(taiji_license, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        types.SimpleNamespace(
            GetCurrentProcess=lambda: "process",
            CloseHandle=lambda handle: closed.append(handle),
        ),
    )
    monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(TOKEN_QUERY=8))
    monkeypatch.setitem(
        sys.modules,
        "win32security",
        types.SimpleNamespace(OpenProcessToken=lambda _process, _access: token),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32profile",
        types.SimpleNamespace(
            GetUserProfileDirectory=lambda _handle: (_ for _ in ()).throw(
                OSError("profile unavailable")
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="current account home"):
        taiji_license._system_account_home()
    assert closed == [token]


def test_valid_license_returns_public_status(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    _write_token(path, private_pem)

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert status.status == "valid"
    assert status.code is None
    assert status.customer == "测试客户"
    assert status.product == "taiji-agent"
    assert status.remaining_days >= 0
    public = status.to_public_dict()
    assert public["status"] == "valid"
    assert public["customer"] == "测试客户"
    assert public["machine_bound"] is True
    assert public["machine_matched"] is True
    assert public["machine_code_short"] == "aaaaaaaaaaaa"
    assert public["bound_machine_code_short"] == "aaaaaaaaaaaa"
    assert public["machine_label"] == "测试终端"
    assert public["activation_mode"] == "offline_machine_file"
    assert public["activation_id"] == "act-test"
    assert public["entitlement_id"] == "ent-test"
    assert public["device_id_short"] == "111111111111"
    assert public["fingerprint_quality"] == "strong"
    assert "token" not in public
    assert "path" not in public
    assert TEST_MACHINE_CODE not in json.dumps(public)
    assert TEST_DEVICE_ID not in json.dumps(public)


def test_missing_required_license_has_stable_code(tmp_path, signing_keys):
    _, public_pem = signing_keys

    status = taiji_license.load_license_status(
        path=tmp_path / "missing.jwt",
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )

    assert status.status == "missing"
    assert status.code == "license_missing"
    assert "授权" in status.message


def test_default_license_path_uses_canonical_active_license_location(tmp_path):
    path = taiji_license.default_license_path(
        {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    )

    assert path == tmp_path / "config/taiji-agent/licenses/active-license.jwt"


def test_source_runtime_missing_license_is_required_and_blocked(
    monkeypatch, tmp_path, signing_keys
):
    _, public_key = signing_keys
    _patch_source_runtime_resources(monkeypatch, tmp_path, public_key)

    status = taiji_license.load_license_status()
    blocked = taiji_license.require_valid_license()

    assert status.status == "missing"
    assert status.required is True
    assert status.policy == "unified-runtime"
    assert status.machine_binding_required is True
    assert blocked is not None
    assert blocked.code == "license_missing"


def test_source_runtime_valid_license_uses_same_machine_bound_policy(
    monkeypatch, tmp_path, signing_keys
):
    private_key, public_key = signing_keys
    license_path, _, _ = _patch_source_runtime_resources(
        monkeypatch,
        tmp_path,
        public_key,
    )
    license_path.parent.mkdir(parents=True, mode=0o700)
    _write_token(license_path, private_key, max_version="1.0.0")
    license_path.chmod(0o600)

    status = taiji_license.load_license_status()
    blocked = taiji_license.require_valid_license()

    assert status.status == "valid", status.to_public_dict()
    assert status.required is True
    assert status.policy == "unified-runtime"
    assert status.machine_binding_required is True
    assert status.machine_matched is True
    assert blocked is None


def test_source_runtime_clock_rollback_cannot_use_empty_redirected_state(
    monkeypatch, tmp_path, signing_keys
):
    private_key, public_key = signing_keys
    license_path, state_path, _ = _patch_source_runtime_resources(
        monkeypatch,
        tmp_path,
        public_key,
    )
    license_path.parent.mkdir(parents=True, mode=0o700)
    token = _write_token(license_path, private_key, max_version="1.0.0")
    license_path.chmod(0o600)
    state_path.parent.mkdir(parents=True, mode=0o700)
    future = int(time.time()) + 3600
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "last_successful_validation_at": future,
                "last_successful_validation_iso": taiji_license._iso_timestamp(future),
                "license_id": "lic-test",
                "license_hash": taiji_license._license_hash(token),
            }
        ),
        encoding="utf-8",
    )
    state_path.chmod(0o600)
    redirected_state = tmp_path / "empty-state/license-state.json"
    monkeypatch.setenv("TAIJI_LICENSE_STATE_FILE", str(redirected_state))

    redirected = taiji_license.load_license_status()
    monkeypatch.delenv("TAIJI_LICENSE_STATE_FILE")
    canonical = taiji_license.load_license_status()

    assert redirected.status == "invalid"
    assert redirected.code == "license_policy_override_forbidden"
    assert canonical.status == "invalid"
    assert canonical.code == "license_clock_rollback"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TAIJI_LICENSE_REQUIRED", "0"),
        ("TAIJI_LICENSE_MACHINE_BINDING_REQUIRED", "0"),
        ("TAIJI_LICENSE_ALLOW_LEGACY_MACHINE_BINDING", "1"),
        ("TAIJI_LICENSE_PUBLIC_KEY", "attacker-controlled-key"),
        ("TAIJI_LICENSE_PUBLIC_KEY_FILE", "/tmp/attacker-public.pem"),
        ("TAIJI_LICENSE_FILE", "/tmp/attacker-license.jwt"),
        ("TAIJI_LICENSE_STATE_FILE", "/tmp/attacker-state.json"),
        ("TAIJI_LICENSE_DEVICE_FILE", "/tmp/attacker-device.json"),
    ],
)
def test_source_runtime_rejects_every_policy_override(
    monkeypatch, tmp_path, signing_keys, name, value
):
    _, public_key = signing_keys
    _patch_source_runtime_resources(monkeypatch, tmp_path, public_key)
    monkeypatch.setenv(name, value)

    status = taiji_license.load_license_status()

    assert status.status == "invalid"
    assert status.required is True
    assert status.policy == "unified-runtime"
    assert status.code == "license_policy_override_forbidden"
    assert status.machine_binding_required is True


def test_runtime_policy_is_fixed_and_rejects_disable_override(
    monkeypatch, tmp_path, installed_production_profile
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("TAIJI_LICENSE_REQUIRED", "0")

    blocked = taiji_license.require_valid_license()

    assert blocked is not None
    assert blocked.required is True
    assert blocked.code == "license_policy_override_forbidden"
    public = blocked.to_public_dict()
    assert public["policy"] == "unified-runtime"
    assert public["policy_version"] == 1
    assert public["signing_key_fingerprint_short"] == "2dcff4f2b5e6"
    assert "TAIJI_LICENSE_REQUIRED" not in json.dumps(public)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TAIJI_LICENSE_REQUIRED", "1"),
        ("TAIJI_LICENSE_MACHINE_BINDING_REQUIRED", "0"),
        ("TAIJI_LICENSE_ALLOW_LEGACY_MACHINE_BINDING", "1"),
        ("TAIJI_LICENSE_PUBLIC_KEY", "attacker-controlled-key"),
        ("TAIJI_LICENSE_PUBLIC_KEY_FILE", "/tmp/attacker-public.pem"),
    ],
)
def test_runtime_policy_rejects_every_security_override_intent(
    monkeypatch, tmp_path, name, value, installed_production_profile
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv(name, value)

    status = taiji_license.load_license_status()

    assert status.status == "invalid"
    assert status.required is True
    assert status.code == "license_policy_override_forbidden"
    assert status.machine_binding_required is True


def test_runtime_policy_pins_installed_public_key_and_fingerprint():
    policy = taiji_license.runtime_license_policy()

    assert policy.required is True
    assert policy.machine_binding_required is True
    assert policy.allow_legacy_machine_binding is False
    assert policy.public_key_path == taiji_license.Path(
        "/opt/taiji-agent/resources/license/signing-public.pem"
    )
    assert policy.public_key_fingerprint == (
        "2dcff4f2b5e6f7a5e7e3f730e2f4446ad3265964431f614de7550265f7628b35"
    )


def test_runtime_module_contains_no_embedded_fallback_public_key():
    source = taiji_license.Path(taiji_license.__file__).read_text(encoding="utf-8")

    assert "DEFAULT_PUBLIC_KEY_PEM" not in source
    assert "-----BEGIN PUBLIC KEY-----" not in source


def test_production_execution_factory_accepts_no_policy_or_key_parameters():
    assert inspect.signature(taiji_license.require_valid_license).parameters == {}


def test_runtime_license_path_uses_build_profile(
    monkeypatch, tmp_path, installed_production_profile
):
    canonical = tmp_path / "canonical/active-license.jwt"
    monkeypatch.setattr(taiji_license, "PRODUCTION_LICENSE_PATH", canonical)
    monkeypatch.setenv("TAIJI_LICENSE_FILE", str(tmp_path / "redirected.jwt"))

    assert taiji_license.runtime_license_path() == canonical


@pytest.mark.parametrize(
    "name",
    [
        "TAIJI_STATE_DIR",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "HOME",
    ],
)
def test_source_runtime_paths_ignore_location_environment(
    monkeypatch, tmp_path, name
):
    monkeypatch.setattr(
        taiji_license.taiji_runtime_profile,
        "is_installed_production",
        lambda: False,
    )
    license_path = tmp_path / "canonical/config/active-license.jwt"
    state_path = tmp_path / "canonical/state/license-state.json"
    device_path = tmp_path / "canonical/config/license-device.json"
    monkeypatch.setattr(taiji_license, "PRODUCTION_LICENSE_PATH", license_path)
    monkeypatch.setattr(taiji_license, "PRODUCTION_LICENSE_STATE_PATH", state_path)
    monkeypatch.setattr(taiji_license, "PRODUCTION_LICENSE_DEVICE_PATH", device_path)
    monkeypatch.setenv(name, str(tmp_path / "redirected"))

    assert taiji_license.runtime_license_path() == license_path
    assert taiji_license.runtime_license_state_path() == state_path
    assert taiji_license.runtime_license_device_path() == device_path


def test_installed_device_identity_ignores_environment_redirect(
    monkeypatch, tmp_path, installed_production_profile
):
    canonical = tmp_path / "canonical/license-device.json"
    monkeypatch.setattr(taiji_license, "PRODUCTION_LICENSE_DEVICE_PATH", canonical)
    redirected = tmp_path / "redirected/license-device.json"
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "TAIJI_LICENSE_DEVICE_FILE": str(redirected),
    }

    assert taiji_license.default_license_device_path(env) == canonical


def test_production_rejects_license_path_environment_redirect(
    monkeypatch, tmp_path, installed_production_profile
):
    canonical = tmp_path / "canonical/licenses/active-license.jwt"
    attacker = tmp_path / "attacker.jwt"
    attacker.write_text("attacker-token\n", encoding="utf-8")
    attacker.chmod(0o600)
    monkeypatch.setattr(taiji_license, "PRODUCTION_LICENSE_PATH", canonical)
    monkeypatch.setattr(
        taiji_license,
        "PRODUCTION_LICENSE_STATE_PATH",
        tmp_path / "canonical-state/license-state.json",
    )
    monkeypatch.setattr(
        taiji_license,
        "PRODUCTION_LICENSE_DEVICE_PATH",
        tmp_path / "canonical/license-device.json",
    )
    monkeypatch.setenv("TAIJI_LICENSE_FILE", str(attacker))
    monkeypatch.setenv("TAIJI_LICENSE_STATE_FILE", str(tmp_path / "attacker-state.json"))
    monkeypatch.setenv("TAIJI_LICENSE_DEVICE_FILE", str(tmp_path / "attacker-device.json"))

    status = taiji_license.load_license_status()

    assert status.status == "invalid"
    assert status.code == "license_policy_override_forbidden"


@pytest.mark.parametrize("shape", ["wide_mode", "symlink", "hardlink"])
def test_production_rejects_untrusted_license_file_shape(
    monkeypatch, tmp_path, installed_production_profile, shape
):
    canonical = tmp_path / "config/taiji-agent/licenses/active-license.jwt"
    canonical.parent.mkdir(parents=True)
    canonical.parent.chmod(0o700)
    if shape == "wide_mode":
        canonical.write_text("token\n", encoding="utf-8")
        canonical.chmod(0o644)
    elif shape == "symlink":
        outside = tmp_path / "outside.jwt"
        outside.write_text("token\n", encoding="utf-8")
        outside.chmod(0o600)
        canonical.symlink_to(outside)
    else:
        canonical.write_text("token\n", encoding="utf-8")
        canonical.chmod(0o600)
        canonical.with_name("second-link.jwt").hardlink_to(canonical)
    monkeypatch.setattr(taiji_license, "PRODUCTION_LICENSE_PATH", canonical)

    status = taiji_license.load_license_status()

    assert status.status == "invalid"
    assert status.code == "license_file_untrusted"


def test_production_user_file_accepts_root_group_writable_root_parent(
    monkeypatch, tmp_path
):
    candidate = tmp_path / "config/taiji-agent/licenses/active-license.jwt"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("signed-token\n", encoding="utf-8")
    candidate.chmod(0o600)
    real_lstat = taiji_license.Path.lstat

    def kylin_root_lstat(path):
        result = real_lstat(path)
        if path == taiji_license.Path("/"):
            return types.SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o775,
                st_uid=0,
                st_gid=0,
                st_nlink=result.st_nlink,
            )
        if path in candidate.parents:
            return types.SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o700,
                st_uid=os.getuid(),
                st_gid=os.getgid(),
                st_nlink=result.st_nlink,
            )
        return result

    monkeypatch.setattr(taiji_license.Path, "lstat", kylin_root_lstat)

    assert taiji_license._validate_production_user_file(candidate, required=True)


def test_windows_user_file_validation_uses_acl_without_posix_uid(
    monkeypatch, tmp_path
):
    candidate = tmp_path / "active-license.jwt"
    candidate.write_text("signed-token\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(taiji_license, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(taiji_license, "PRODUCTION_USER_HOME", tmp_path)
    monkeypatch.setattr(
        taiji_license,
        "_validate_windows_path_security",
        lambda path, **kwargs: calls.append((path, kwargs)) or True,
        raising=False,
    )

    assert taiji_license._validate_production_user_file(candidate, required=True)
    assert calls == [
        (
            candidate,
            {
                "required": True,
                "require_current_user_owner": True,
                "ancestor_stop": tmp_path,
            },
        )
    ]


@pytest.mark.parametrize("profile_shape", ["reparse", "untrusted_acl"])
def test_windows_path_security_rejects_untrusted_profile_root_without_drive_scan(
    monkeypatch, tmp_path, profile_shape
):
    drive_root = tmp_path / "drive"
    profile = drive_root / "Users/current"
    candidate = profile / ".config/taiji-agent/license-device.json"
    profile.mkdir(parents=True)
    real_lstat = taiji_license.Path.lstat
    snapshots = []

    def profile_lstat(path):
        result = real_lstat(path)
        if path == profile and profile_shape == "reparse":
            return types.SimpleNamespace(
                st_mode=result.st_mode,
                st_nlink=result.st_nlink,
                st_file_attributes=0x0400,
            )
        return result

    def security_snapshot(path):
        snapshots.append(path)
        entries = (
            [(0, 0, 0x0040, "S-1-5-21-other")]
            if path == profile and profile_shape == "untrusted_acl"
            else [(0, 0, 0x0002, "S-1-5-21-current")]
        )
        return "S-1-5-21-current", "S-1-5-21-current", entries

    monkeypatch.setattr(taiji_license.Path, "lstat", profile_lstat)
    monkeypatch.setattr(
        taiji_license,
        "_windows_security_snapshot",
        security_snapshot,
    )

    with pytest.raises(taiji_license._LicenseUserResourceError):
        taiji_license._validate_windows_path_security(
            candidate,
            required=False,
            require_current_user_owner=True,
            ancestor_stop=profile,
        )
    assert drive_root not in snapshots


def test_windows_security_snapshot_uses_pywin32_owner_and_dacl_contract(
    monkeypatch, tmp_path
):
    calls = []
    token = object()

    class FakeDacl:
        def GetAceCount(self):
            return 1

        def GetAce(self, index):
            assert index == 0
            return ((0, 0), 0x0002, "current-object")

    class FakeDescriptor:
        def GetSecurityDescriptorOwner(self):
            return "owner-object"

        def GetSecurityDescriptorDacl(self):
            return FakeDacl()

    fake_win32api = types.SimpleNamespace(
        GetCurrentProcess=lambda: "process",
        CloseHandle=lambda handle: calls.append(("close", handle)),
    )

    def open_process_token(process, access):
        calls.append(("open", process, access))
        return token

    def get_token_information(handle, information_class):
        calls.append(("token", handle, information_class))
        return ("current-object", 0)

    def get_file_security(path, information):
        calls.append(("security", path, information))
        return FakeDescriptor()

    fake_win32security = types.SimpleNamespace(
        TokenUser=3,
        OWNER_SECURITY_INFORMATION=1,
        DACL_SECURITY_INFORMATION=2,
        OpenProcessToken=open_process_token,
        GetTokenInformation=get_token_information,
        GetFileSecurity=get_file_security,
        ConvertSidToStringSid=lambda sid: f"sid:{sid}",
    )
    monkeypatch.setitem(sys.modules, "win32api", fake_win32api)
    monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(TOKEN_QUERY=8))
    monkeypatch.setitem(sys.modules, "win32security", fake_win32security)
    candidate = tmp_path / "active-license.jwt"

    assert taiji_license._windows_security_snapshot(candidate) == (
        "sid:owner-object",
        "sid:current-object",
        [(0, 0, 0x0002, "sid:current-object")],
    )
    assert calls == [
        ("open", "process", 8),
        ("token", token, 3),
        ("close", token),
        ("security", str(candidate), 3),
    ]


def test_windows_security_snapshot_fails_closed_without_pywin32(
    monkeypatch, tmp_path
):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name in {"win32api", "win32con", "win32security"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(taiji_license._LicenseUserResourceError):
        taiji_license._windows_security_snapshot(tmp_path / "active-license.jwt")


@pytest.mark.parametrize(
    ("owner_sid", "entries", "require_current_user_owner", "expected"),
    [
        (
            "S-1-5-21-current",
            [(0, 0, 0x0002, "S-1-5-21-current")],
            True,
            True,
        ),
        (
            "S-1-5-21-other",
            [(0, 0, 0x0002, "S-1-5-21-current")],
            True,
            False,
        ),
        (
            "S-1-5-21-current",
            [(0, 0, 0x0002, "S-1-5-21-other")],
            True,
            False,
        ),
        (
            "S-1-5-18",
            [(0, 0, 0x0002, "S-1-5-32-544")],
            False,
            True,
        ),
    ],
)
def test_windows_acl_trust_rejects_foreign_owner_or_writer(
    owner_sid, entries, require_current_user_owner, expected
):
    assert (
        taiji_license._windows_acl_is_trusted(
            owner_sid=owner_sid,
            entries=entries,
            current_user_sid="S-1-5-21-current",
            require_current_user_owner=require_current_user_owner,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("access_mask", "expected"),
    [
        (0x0002, True),
        (0x0004, True),
        (0x0040, False),
        (0x40000000, False),
    ],
)
def test_windows_ancestor_acl_distinguishes_creation_from_replacement(
    access_mask, expected
):
    assert (
        taiji_license._windows_acl_is_trusted(
            owner_sid="S-1-5-18",
            entries=[(0, 0, access_mask, "S-1-5-11")],
            current_user_sid="S-1-5-21-current",
            require_current_user_owner=False,
            dangerous_access_mask=(
                taiji_license._WINDOWS_ANCESTOR_REPLACEMENT_ACCESS_MASK
            ),
        )
        is expected
    )


@pytest.mark.parametrize("shape", ["reparse", "hardlink"])
def test_windows_path_security_rejects_reparse_and_hardlink(
    monkeypatch, tmp_path, shape
):
    candidate = tmp_path / "active-license.jwt"
    candidate.write_text("signed-token\n", encoding="utf-8")
    if shape == "hardlink":
        candidate.with_name("second-link.jwt").hardlink_to(candidate)
    else:
        real_lstat = taiji_license.Path.lstat

        def reparse_lstat(path):
            result = real_lstat(path)
            if path == candidate:
                return types.SimpleNamespace(
                    st_mode=result.st_mode,
                    st_nlink=result.st_nlink,
                    st_file_attributes=0x0400,
                )
            return result

        monkeypatch.setattr(taiji_license.Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        taiji_license,
        "_windows_security_snapshot",
        lambda _path: (
            "S-1-5-21-current",
            "S-1-5-21-current",
            [(0, 0, 0x0002, "S-1-5-21-current")],
        ),
        raising=False,
    )

    with pytest.raises(taiji_license._LicenseUserResourceError):
        taiji_license._validate_windows_path_security(
            candidate,
            required=True,
            require_current_user_owner=True,
        )


def test_secure_runtime_write_rejects_windows_reparse_missing_parent(
    monkeypatch, tmp_path
):
    profile = tmp_path / "profile"
    profile.mkdir()
    missing_parent = profile / "missing"
    target = missing_parent / "license-device.json"
    closed = []
    writes = []

    class FakeHandle:
        def __init__(self, path):
            self.path = taiji_license.Path(path)

        def Close(self):
            closed.append(self.path)

    def create_file(path, *_args):
        return FakeHandle(path)

    def create_directory(path, _security):
        taiji_license.Path(path).mkdir()

    def file_information(handle):
        attributes = 0x0010
        if handle.path == missing_parent:
            attributes |= 0x0400
        return (attributes, None, None, None, 1, 0, 0, 1, 0, 0)

    fake_win32file = types.SimpleNamespace(
        CreateFile=create_file,
        CreateDirectory=create_directory,
        GetFileInformationByHandle=file_information,
        WriteFile=lambda *_args: writes.append("write"),
        FlushFileBuffers=lambda *_args: None,
    )
    fake_win32con = types.SimpleNamespace(
        GENERIC_WRITE=0x40000000,
        FILE_SHARE_READ=1,
        FILE_SHARE_WRITE=2,
        OPEN_EXISTING=3,
        CREATE_NEW=1,
        FILE_ATTRIBUTE_NORMAL=0x0080,
        FILE_ATTRIBUTE_DIRECTORY=0x0010,
        FILE_ATTRIBUTE_REPARSE_POINT=0x0400,
        FILE_FLAG_BACKUP_SEMANTICS=0x02000000,
        FILE_FLAG_OPEN_REPARSE_POINT=0x00200000,
        MOVEFILE_REPLACE_EXISTING=1,
        MOVEFILE_WRITE_THROUGH=8,
    )
    monkeypatch.setattr(taiji_license, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "win32file", fake_win32file)
    monkeypatch.setitem(sys.modules, "win32con", fake_win32con)
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        types.SimpleNamespace(MoveFileEx=lambda *_args: writes.append("move")),
    )
    monkeypatch.setattr(
        taiji_license,
        "_windows_security_snapshot",
        lambda _path: (
            "S-1-5-21-current",
            "S-1-5-21-current",
            [(0, 0, 0x0002, "S-1-5-21-current")],
        ),
    )

    with pytest.raises(taiji_license._LicenseUserResourceError):
        taiji_license._secure_atomic_write_runtime_resource(
            path=target,
            text="device-secret\n",
            profile_root=profile,
        )
    assert writes == []
    assert target not in closed
    assert set(closed) == {profile, missing_parent}
    assert not target.exists()


def test_production_public_key_accepts_root_group_writable_root_parent(
    monkeypatch, tmp_path, signing_keys
):
    _, public_key = signing_keys
    key_path = tmp_path / "opt/taiji-agent/resources/license/signing-public.pem"
    key_path.parent.mkdir(parents=True)
    key_path.write_text(public_key, encoding="utf-8")
    key_path.chmod(0o644)
    real_lstat = taiji_license.Path.lstat

    def root_owned_lstat(path):
        result = real_lstat(path)
        if path == key_path:
            mode = stat.S_IFREG | 0o644
        elif path == taiji_license.Path("/"):
            mode = stat.S_IFDIR | 0o775
        else:
            mode = stat.S_IFDIR | 0o755
        return types.SimpleNamespace(
            st_mode=mode,
            st_uid=0,
            st_gid=0,
            st_nlink=result.st_nlink,
        )

    monkeypatch.setattr(taiji_license.Path, "lstat", root_owned_lstat)
    monkeypatch.setattr(taiji_license, "PRODUCTION_PUBLIC_KEY_PATH", key_path)
    policy = taiji_license.replace(
        taiji_license.runtime_license_policy(),
        public_key_path=key_path,
        public_key_fingerprint=taiji_license._public_key_fingerprint(public_key),
    )

    assert taiji_license._load_production_public_key(policy) == public_key.strip()


def test_production_version_accepts_root_group_writable_root_parent(
    monkeypatch, tmp_path
):
    version_path = tmp_path / "opt/taiji-agent/VERSION"
    version_path.parent.mkdir(parents=True)
    version_path.write_text("1.0.2\n", encoding="utf-8")
    version_path.chmod(0o644)
    real_lstat = taiji_license.Path.lstat

    def root_owned_lstat(path):
        result = real_lstat(path)
        if path == version_path:
            mode = stat.S_IFREG | 0o644
        elif path == taiji_license.Path("/"):
            mode = stat.S_IFDIR | 0o775
        else:
            mode = stat.S_IFDIR | 0o755
        return types.SimpleNamespace(
            st_mode=mode,
            st_uid=0,
            st_gid=0,
            st_nlink=result.st_nlink,
        )

    monkeypatch.setattr(taiji_license.Path, "lstat", root_owned_lstat)
    monkeypatch.setattr(taiji_license, "PRODUCTION_VERSION_PATH", version_path)

    assert taiji_license._load_production_version() == "1.0.2"


def test_source_runtime_loaders_accept_pinned_repo_resources():
    policy = taiji_license.runtime_license_policy()
    repo_root = taiji_license.Path(__file__).resolve().parents[4]

    assert taiji_license._source_repo_root() == repo_root
    assert taiji_license._load_source_public_key(policy) == (
        repo_root / taiji_license.INTERNAL_ISSUER_PUBLIC_KEY_RELATIVE
    ).read_text(encoding="utf-8").strip()
    assert taiji_license._load_source_version() == (
        repo_root / "VERSION"
    ).read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("resource", ["public_key", "version"])
@pytest.mark.parametrize(
    "shape",
    ["symlink", "hardlink", "writable_file", "writable_parent"],
)
def test_source_runtime_loaders_reject_untrusted_repo_resource_shape(
    monkeypatch, tmp_path, resource, shape
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(mode=0o700)
    (repo_root / ".git").mkdir()
    if resource == "public_key":
        path = repo_root / taiji_license.INTERNAL_ISSUER_PUBLIC_KEY_RELATIVE
        source = (
            taiji_license.Path(__file__).resolve().parents[4]
            / taiji_license.INTERNAL_ISSUER_PUBLIC_KEY_RELATIVE
        ).read_text(encoding="utf-8")
        error = taiji_license._LicensePublicKeyError
    else:
        path = repo_root / "VERSION"
        source = "1.0.0\n"
        error = taiji_license._LicenseVersionError
    path.parent.mkdir(parents=True, exist_ok=True)
    if shape == "symlink":
        outside = tmp_path / f"outside-{resource}"
        outside.write_text(source, encoding="utf-8")
        path.symlink_to(outside)
    else:
        path.write_text(source, encoding="utf-8")
        path.chmod(0o644)
        if shape == "hardlink":
            path.with_name(path.name + ".second").hardlink_to(path)
        elif shape == "writable_file":
            path.chmod(0o666)
        else:
            path.parent.chmod(0o777)
    monkeypatch.setattr(taiji_license, "_source_repo_root", lambda: repo_root)

    with pytest.raises(error):
        if resource == "public_key":
            taiji_license._load_source_public_key(
                taiji_license.runtime_license_policy()
            )
        else:
            taiji_license._load_source_version()


@pytest.mark.parametrize(
    ("uid", "gid", "mode", "user_uid"),
    [
        (0, 100, 0o775, None),
        (0, 0, 0o777, None),
        (1000, 1000, 0o770, 1000),
        (1001, 1001, 0o755, 1000),
    ],
)
def test_production_parent_trust_rejects_non_privileged_writers(
    uid, gid, mode, user_uid
):
    parent_stat = types.SimpleNamespace(
        st_mode=stat.S_IFDIR | mode,
        st_uid=uid,
        st_gid=gid,
    )

    assert not taiji_license._trusted_production_parent(
        parent_stat,
        user_uid=user_uid,
    )


def test_production_version_input_overwrites_user_environment(
    monkeypatch, tmp_path, installed_production_profile
):
    canonical = tmp_path / "config/taiji-agent/licenses/active-license.jwt"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("signed-token\n", encoding="utf-8")
    canonical.chmod(0o600)
    captured = {}
    monkeypatch.setattr(taiji_license, "PRODUCTION_LICENSE_PATH", canonical)
    monkeypatch.setattr(
        taiji_license,
        "_validate_production_user_file",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        taiji_license,
        "_load_production_public_key",
        lambda _policy: "trusted-key",
    )
    monkeypatch.setattr(taiji_license, "_load_production_version", lambda: "9.9.9")
    monkeypatch.setattr(
        taiji_license,
        "_load_license_status_impl",
        lambda **kwargs: captured.update(kwargs)
        or taiji_license.LicenseStatus(status="missing", required=True),
    )
    monkeypatch.setenv("TAIJI_AGENT_VERSION", "0.0.1")

    taiji_license.load_license_status()

    assert captured["path"] == canonical
    assert captured["environ"]["TAIJI_AGENT_VERSION"] == "9.9.9"
    assert captured["state_path"] == taiji_license.PRODUCTION_LICENSE_STATE_PATH
    assert (
        captured["environ"]["TAIJI_LICENSE_DEVICE_FILE"]
        == str(taiji_license.PRODUCTION_LICENSE_DEVICE_PATH)
    )


def test_installed_candidate_validation_uses_runtime_policy(
    monkeypatch, tmp_path, installed_production_profile
):
    candidate = tmp_path / "candidate.jwt"
    candidate.write_text("signed-token\n", encoding="utf-8")
    candidate.chmod(0o600)
    captured = {}
    monkeypatch.setattr(
        taiji_license,
        "_validate_production_user_file",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        taiji_license,
        "_load_production_public_key",
        lambda _policy: "trusted-key",
    )
    monkeypatch.setattr(taiji_license, "_load_production_version", lambda: "9.9.9")
    monkeypatch.setattr(
        taiji_license,
        "_load_license_status_impl",
        lambda **kwargs: captured.update(kwargs)
        or taiji_license.LicenseStatus(status="valid", required=True),
    )

    status = taiji_license.validate_license_candidate(candidate)

    assert status.status == "valid"
    assert status.policy == "unified-runtime"
    assert captured["path"] == candidate
    assert captured["check_state"] is False
    assert captured["environ"]["TAIJI_LICENSE_REQUIRED"] == "1"
    assert captured["environ"]["TAIJI_LICENSE_MACHINE_BINDING_REQUIRED"] == "1"
    assert captured["environ"]["TAIJI_AGENT_VERSION"] == "9.9.9"


def test_production_public_key_fingerprint_matches_issuer_and_rejects_attacker(
    monkeypatch, tmp_path, signing_keys
):
    repo_root = taiji_license.Path(__file__).resolve().parents[4]
    issuer_public_key = (
        repo_root / "tools" / "taiji-license-issuer" / "private" / "signing-public.pem"
    ).read_text(encoding="utf-8")
    assert taiji_license._public_key_fingerprint(issuer_public_key) == (
        taiji_license.PRODUCTION_PUBLIC_KEY_FINGERPRINT
    )

    _, attacker_public_key = signing_keys
    attacker_path = tmp_path / "signing-public.pem"
    attacker_path.write_text(attacker_public_key, encoding="utf-8")
    attacker_path.chmod(0o644)
    monkeypatch.setattr(taiji_license, "PRODUCTION_PUBLIC_KEY_PATH", attacker_path)

    real_lstat = taiji_license.Path.lstat

    def root_owned_lstat(path):
        real_lstat(path)
        mode = stat.S_IFREG | 0o644 if path == attacker_path else stat.S_IFDIR | 0o755
        return types.SimpleNamespace(st_mode=mode, st_uid=0)

    monkeypatch.setattr(taiji_license.Path, "lstat", root_owned_lstat)
    policy = taiji_license.runtime_license_policy()

    with pytest.raises(taiji_license._LicensePublicKeyError):
        taiji_license._load_production_public_key(policy)


def test_macos_machine_fingerprint_uses_stable_platform_uuid(monkeypatch):
    original_exists = taiji_license.Path.exists
    uuid_nodes = iter(
        [
            ["11:11:11:11:11:11"],
            ["22:22:22:22:22:22"],
        ]
    )

    def fake_exists(path):
        if str(path) == "/sys/class/net":
            return False
        return original_exists(path)

    def fake_run(*args, **kwargs):
        return types.SimpleNamespace(
            returncode=0,
            stdout='    "IOPlatformUUID" = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"\n',
            stderr="",
        )

    monkeypatch.setattr(taiji_license, "sys", types.SimpleNamespace(platform="darwin"), raising=False)
    monkeypatch.setattr(taiji_license, "subprocess", types.SimpleNamespace(run=fake_run), raising=False)
    monkeypatch.setattr(taiji_license.Path, "exists", fake_exists)
    monkeypatch.setattr(taiji_license, "_read_machine_file", lambda path: None)
    monkeypatch.setattr(taiji_license, "_collect_linux_physical_macs", lambda: [])
    monkeypatch.setattr(taiji_license, "_collect_uuid_node_mac", lambda: next(uuid_nodes))

    first = taiji_license.get_machine_fingerprint(use_cache=False)
    second = taiji_license.get_machine_fingerprint(use_cache=False)

    assert first["machine_code"] == second["machine_code"]
    assert any(
        signal["name"] == "macos_platform_uuid" and signal["available"]
        for signal in first["signals"]
    )


def test_machine_fingerprint_v3_uses_device_secret_and_ignores_physical_mac_changes(monkeypatch, tmp_path):
    mac_sets = iter(
        [
            ["00:11:22:33:44:55"],
            ["66:77:88:99:aa:bb"],
            [],
            [],
        ]
    )

    def fake_read_machine_file(path):
        lookup = {
            "/sys/class/dmi/id/product_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "/sys/class/dmi/id/board_serial": "board-serial-1",
            "/etc/machine-id": "machine-id-1",
            "/var/lib/dbus/machine-id": None,
        }
        return lookup.get(str(path))

    monkeypatch.setattr(taiji_license, "_read_machine_file", fake_read_machine_file)
    monkeypatch.setattr(taiji_license, "_collect_linux_physical_macs", lambda: next(mac_sets))
    monkeypatch.setattr(taiji_license, "_collect_macos_platform_uuid", lambda: None)

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-a")}
    wireless = taiji_license.get_machine_fingerprint(use_cache=False, now=1_000_000, environ=env)
    wired = taiji_license.get_machine_fingerprint(use_cache=False, now=1_000_001, environ=env)
    disconnected = taiji_license.get_machine_fingerprint(use_cache=False, now=1_000_002, environ=env)
    same_hardware_other_secret = taiji_license.get_machine_fingerprint(
        use_cache=False,
        now=1_000_003,
        environ={"XDG_CONFIG_HOME": str(tmp_path / "config-b")},
    )

    assert wireless["binding_type"] == "machine_fingerprint_v3"
    assert wireless["collection_version"] == 3
    assert wireless["device_id"].startswith("sha256:")
    assert wireless["hardware_code"] == same_hardware_other_secret["hardware_code"]
    assert wireless["machine_code"] == wired["machine_code"] == disconnected["machine_code"]
    assert wireless["machine_code"] != same_hardware_other_secret["machine_code"]
    assert wireless["fingerprint_quality"] == "strong"
    assert any(
        signal["name"] == "physical_mac" and signal["count"] == 1
        for signal in wireless["signals"]
    )


def test_machine_fingerprint_explicit_device_path_preserves_low_level_seam(
    tmp_path,
):
    explicit = tmp_path / "explicit/license-device.json"
    redirected = tmp_path / "redirected/license-device.json"

    fingerprint = taiji_license.get_machine_fingerprint(
        use_cache=False,
        now=1_000_000,
        environ={"TAIJI_LICENSE_DEVICE_FILE": str(redirected)},
        device_path=explicit,
    )

    explicit_data = json.loads(explicit.read_text(encoding="utf-8"))
    assert fingerprint["device_id"] == explicit_data["device_id"]
    assert not redirected.exists()


def test_expired_license_has_user_prompt(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "expired.jwt"
    now = int(time.time())
    _write_token(path, private_pem, exp=now - 10)

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )

    assert status.status == "expired"
    assert status.code == "license_expired"
    assert status.message == "授权已到期，请联系服务方更新授权。"


def test_malformed_jwt_is_invalid(tmp_path, signing_keys):
    _, public_pem = signing_keys
    path = tmp_path / "broken.jwt"
    path.write_text("not-a-jwt", encoding="utf-8")

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )

    assert status.status == "invalid"
    assert status.code == "license_invalid"


def test_not_before_and_product_mismatch_are_invalid(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    now = int(time.time())

    future_path = tmp_path / "future.jwt"
    _write_token(future_path, private_pem, nbf=now + 3600)
    future = taiji_license.load_license_status(
        path=future_path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )
    assert future.status == "invalid"
    assert future.code == "license_not_yet_valid"

    product_path = tmp_path / "wrong-product.jwt"
    _write_token(product_path, private_pem, product="other-product", aud="other-product")
    mismatch = taiji_license.load_license_status(
        path=product_path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
    )
    assert mismatch.status == "invalid"
    assert mismatch.code in {"license_invalid_product", "license_invalid_audience"}


def test_require_valid_license_writes_success_state_once(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    state_path = tmp_path / "license-state.json"
    _write_token(path, private_pem, license_id="lic-state", iat=999_900, nbf=999_900, exp=2_000_000)

    blocked = taiji_license.require_license_for_validation(
        path=path,
        public_key=public_pem,
        now=1_000_000,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert blocked is None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    assert state["last_successful_validation_at"] == 1_000_000
    assert state["last_successful_validation_iso"] == "1970-01-12T13:46:40Z"
    assert state["license_id"] == "lic-state"
    assert state["license_hash"].startswith("sha256:")


def test_success_state_write_is_throttled(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    state_path = tmp_path / "license-state.json"
    _write_token(path, private_pem, iat=900_000, nbf=900_000, exp=2_000_000)

    taiji_license.require_license_for_validation(
        path=path,
        public_key=public_pem,
        now=1_000_000,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )
    taiji_license.require_license_for_validation(
        path=path,
        public_key=public_pem,
        now=1_000_030,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )
    throttled = json.loads(state_path.read_text(encoding="utf-8"))
    assert throttled["last_successful_validation_at"] == 1_000_000

    taiji_license.require_license_for_validation(
        path=path,
        public_key=public_pem,
        now=1_000_061,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )
    updated = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated["last_successful_validation_at"] == 1_000_061


def test_clock_rollback_blocks_without_rewriting_state(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    state_path = tmp_path / "license-state.json"
    _write_token(path, private_pem, iat=900_000, nbf=900_000, exp=2_000_000)
    state_path.write_text(
        json.dumps({
            "schema_version": 1,
            "last_successful_validation_at": 1_000_000,
            "last_successful_validation_iso": "1970-01-12T13:46:40Z",
            "license_id": "lic-test",
            "license_hash": "sha256:old",
        }),
        encoding="utf-8",
    )

    blocked = taiji_license.require_license_for_validation(
        path=path,
        public_key=public_pem,
        now=999_000,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert blocked is not None
    assert blocked.status == "invalid"
    assert blocked.code == "license_clock_rollback"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_successful_validation_at"] == 1_000_000


def test_clock_rollback_recovers_after_time_is_corrected(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    state_path = tmp_path / "license-state.json"
    _write_token(path, private_pem, iat=900_000, nbf=900_000, exp=2_000_000)
    state_path.write_text(
        json.dumps({
            "schema_version": 1,
            "last_successful_validation_at": 1_000_000,
            "last_successful_validation_iso": "1970-01-12T13:46:40Z",
            "license_id": "lic-test",
            "license_hash": "sha256:old",
        }),
        encoding="utf-8",
    )

    blocked = taiji_license.require_license_for_validation(
        path=path,
        public_key=public_pem,
        now=999_000,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )
    assert blocked is not None

    recovered = taiji_license.require_license_for_validation(
        path=path,
        public_key=public_pem,
        now=1_000_120,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert recovered is None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_successful_validation_at"] == 1_000_120


def test_future_expired_license_does_not_pollute_success_state(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    state_path = tmp_path / "license-state.json"
    now = int(time.time())
    _write_token(path, private_pem, exp=now + 86400)
    state_path.write_text(
        json.dumps({
            "schema_version": 1,
            "last_successful_validation_at": now,
            "last_successful_validation_iso": taiji_license._iso_timestamp(now),
            "license_id": "lic-test",
            "license_hash": "sha256:old",
        }),
        encoding="utf-8",
    )

    blocked = taiji_license.require_license_for_validation(
        path=path,
        public_key=public_pem,
        now=now + 10 * 86400,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert blocked is not None
    assert blocked.code == "license_expired"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_successful_validation_at"] == now


def test_invalid_license_does_not_create_success_state(tmp_path, signing_keys):
    _, public_pem = signing_keys
    path = tmp_path / "broken.jwt"
    state_path = tmp_path / "license-state.json"
    path.write_text("not-a-jwt", encoding="utf-8")

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        now=1_000_000,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert status.status == "invalid"
    assert not state_path.exists()


def test_corrupted_success_state_is_invalid(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    state_path = tmp_path / "license-state.json"
    _write_token(path, private_pem, iat=999_900, nbf=999_900, exp=2_000_000)
    state_path.write_text("{not-json", encoding="utf-8")

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        now=1_000_000,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert status.status == "invalid"
    assert status.code == "license_state_invalid"
    assert "系统时间异常" in status.message


def test_import_style_validation_can_skip_local_success_state(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    state_path = tmp_path / "license-state.json"
    _write_token(path, private_pem, iat=999_900, nbf=999_900, exp=2_000_000)
    state_path.write_text("{not-json", encoding="utf-8")

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        now=1_000_000,
        state_path=state_path,
        check_state=False,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert status.status == "valid"


def test_source_checkout_uses_internal_issuer_public_key_for_gui_license(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    repo_root = tmp_path / "taiji-agentv1.0"
    lab_root = repo_root / "hermes-local-lab"
    public_key_path = repo_root / "tools" / "taiji-license-issuer" / "private" / "signing-public.pem"
    (repo_root / ".git").mkdir(parents=True)
    lab_root.mkdir(parents=True)
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_text(public_pem, encoding="utf-8")

    path = tmp_path / "license.jwt"
    _write_token(path, private_pem)

    status = taiji_license.load_license_status(
        path=path,
        now=time.time(),
        check_state=False,
        environ={
            "TAIJI_LICENSE_REQUIRED": "1",
            "TAIJI_AGENT_ROOT": str(lab_root),
        },
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert status.status == "valid"


def test_installed_runtime_does_not_trust_sibling_issuer_public_key_without_source_checkout(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    install_root = tmp_path / "opt" / "taiji-agent"
    public_key_path = tmp_path / "opt" / "tools" / "taiji-license-issuer" / "private" / "signing-public.pem"
    install_root.mkdir(parents=True)
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_text(public_pem, encoding="utf-8")

    path = tmp_path / "license.jwt"
    _write_token(path, private_pem)

    status = taiji_license.load_license_status(
        path=path,
        now=time.time(),
        check_state=False,
        environ={
            "TAIJI_LICENSE_REQUIRED": "1",
            "TAIJI_AGENT_ROOT": str(install_root),
        },
    )

    assert status.status == "invalid"
    assert status.code == "license_public_key_missing"


def test_unbound_license_is_rejected_when_machine_binding_is_required(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "unbound.jwt"
    _write_token(path, private_pem, binding_type=None, machine_code=None, machine_label=None)

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert status.status == "invalid"
    assert status.code == "license_machine_binding_required"
    assert "本机" in status.message


def test_machine_bound_license_rejects_other_machine_code(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "other-machine.jwt"
    _write_token(path, private_pem, machine_code=OTHER_MACHINE_CODE)

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )

    assert status.status == "invalid"
    assert status.code == "license_machine_mismatch"
    assert status.message == "授权文件与本机不匹配，请联系服务方重新签发。"
    public = status.to_public_dict()
    assert public["machine_bound"] is True
    assert public["machine_matched"] is False
    assert public["machine_code_short"] == "aaaaaaaaaaaa"
    assert public["bound_machine_code_short"] == "bbbbbbbbbbbb"


def test_machine_bound_license_requires_local_fingerprint(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "license.jwt"
    _write_token(path, private_pem)

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=UNAVAILABLE_MACHINE_FINGERPRINT,
    )

    assert status.status == "invalid"
    assert status.code == "license_machine_fingerprint_unavailable"
    assert "机器码" in status.message


def test_legacy_v1_machine_bound_license_is_still_accepted(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "legacy-v1.jwt"
    _write_token(path, private_pem, binding_type="machine_fingerprint_v1")

    rejected = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=LEGACY_MACHINE_FINGERPRINT,
    )
    assert rejected.status == "invalid"
    assert rejected.code == "license_legacy_machine_binding"

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1", "TAIJI_LICENSE_ALLOW_LEGACY_MACHINE_BINDING": "1"},
        machine_fingerprint=LEGACY_MACHINE_FINGERPRINT,
    )

    assert status.status == "valid"
    assert status.machine_bound is True
    assert status.machine_matched is True


def test_unbound_license_can_be_read_when_machine_binding_is_explicitly_disabled(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "legacy.jwt"
    _write_token(path, private_pem, binding_type=None, machine_code=None, machine_label=None)

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1", "TAIJI_LICENSE_MACHINE_BINDING_REQUIRED": "0"},
        check_state=False,
    )

    assert status.status == "valid"
    assert status.machine_binding_required is False
    assert status.machine_bound is False


def test_machine_request_is_redacted_and_contains_short_fingerprint():
    request = taiji_license.build_machine_request(
        customer="测试客户",
        machine_label="一号终端",
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
        now=1781179200,
    )

    assert request["request_type"] == "taiji_machine_license_request"
    assert request["product"] == "taiji-agent"
    assert request["customer"] == "测试客户"
    assert request["machine_label"] == "一号终端"
    assert request["binding_type"] == "machine_fingerprint_v3"
    assert request["collection_version"] == 3
    assert request["machine_code"] == TEST_MACHINE_CODE
    assert request["machine_code_short"] == "aaaaaaaaaaaa"
    assert request["device_id_short"] == "111111111111"
    assert request["hardware_code_short"] == "999999999999"
    assert request["fingerprint_quality"] == "strong"
    assert request["risk_flags"] == []
    assert request["suggested_filename"].startswith("taiji-machine-request-测试客户-一号终端-aaaaaaaaaaaa-20260611-120000Z")
    assert request["suggested_filename"].endswith(".json")
    raw = json.dumps(request, ensure_ascii=False)
    assert "PRIVATE KEY" not in raw
    assert "00:11" not in raw
    assert "device_secret" not in raw


def test_machine_request_noarg_uses_canonical_runtime_device_identity(
    monkeypatch, tmp_path
):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    canonical = profile / ".config/taiji-agent/license-device.json"
    redirected = tmp_path / "redirected/license-device.json"
    xdg_config_home = tmp_path / "xdg-config"
    environment_home = tmp_path / "environment-home"
    monkeypatch.setattr(
        taiji_license,
        "runtime_license_device_path",
        lambda: canonical,
    )
    monkeypatch.setattr(taiji_license, "PRODUCTION_USER_HOME", profile)
    monkeypatch.setenv("TAIJI_LICENSE_DEVICE_FILE", str(redirected))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))
    monkeypatch.setenv("HOME", str(environment_home))

    request = taiji_license.build_machine_request(now=1_000_000)

    assert canonical.is_file()
    canonical_data = json.loads(canonical.read_text(encoding="utf-8"))
    assert request["device_id"] == canonical_data["device_id"]
    assert not redirected.exists()
    assert not (
        xdg_config_home
        / taiji_license.PRODUCT
        / taiji_license.DEFAULT_LICENSE_DEVICE_FILENAME
    ).exists()
    assert not (
        environment_home
        / ".config"
        / taiji_license.PRODUCT
        / taiji_license.DEFAULT_LICENSE_DEVICE_FILENAME
    ).exists()


def test_runtime_device_creation_rejects_posix_symlink_parent(
    monkeypatch, tmp_path
):
    if os.name != "posix":
        pytest.skip("POSIX no-follow behavior")
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    (profile / ".config").symlink_to(outside, target_is_directory=True)
    canonical = profile / ".config/taiji-agent/license-device.json"
    monkeypatch.setattr(taiji_license, "PRODUCTION_USER_HOME", profile)
    monkeypatch.setattr(
        taiji_license,
        "runtime_license_device_path",
        lambda: canonical,
    )

    request = taiji_license.build_machine_request(now=1_000_000)

    assert request["device_id"] == ""
    assert "no_device_secret" in request["risk_flags"]
    assert not (outside / "taiji-agent/license-device.json").exists()


def test_runtime_state_creation_rejects_posix_symlink_parent(
    monkeypatch, tmp_path, signing_keys
):
    if os.name != "posix":
        pytest.skip("POSIX no-follow behavior")
    private_key, public_key = signing_keys
    license_path, state_path, _ = _patch_source_runtime_resources(
        monkeypatch,
        tmp_path,
        public_key,
    )
    license_path.parent.mkdir(parents=True, mode=0o700)
    _write_token(license_path, private_key, max_version="1.0.0")
    license_path.chmod(0o600)
    outside = tmp_path / "outside-state"
    outside.mkdir(mode=0o700)
    (tmp_path / "state").symlink_to(outside, target_is_directory=True)

    blocked = taiji_license.require_valid_license()

    assert blocked is not None
    assert blocked.code == "license_state_invalid"
    assert not (outside / "taiji-agent/license-state.json").exists()


def test_secure_runtime_first_device_and_state_creation_succeeds(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    device_path = profile / ".config/taiji-agent/license-device.json"
    state_path = profile / ".local/state/taiji-agent/license-state.json"

    device = taiji_license._write_license_device(
        device_path,
        now_ts=1_000_000,
        secure_root=profile,
    )
    taiji_license._write_license_state(
        path=state_path,
        now_ts=1_000_000,
        license_id="lic-secure-create",
        token="signed-token",
        secure_root=profile,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert device["device_id"] == json.loads(
        device_path.read_text(encoding="utf-8")
    )["device_id"]
    assert state["license_id"] == "lic-secure-create"
    assert stat.S_IMODE(device_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_legacy_v2_machine_bound_license_requires_explicit_compatibility(tmp_path, signing_keys):
    private_pem, public_pem = signing_keys
    path = tmp_path / "legacy-v2.jwt"
    _write_token(path, private_pem, binding_type="machine_fingerprint_v2", device_id=None)

    rejected = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=LEGACY_V2_MACHINE_FINGERPRINT,
    )
    assert rejected.status == "invalid"
    assert rejected.code == "license_legacy_machine_binding"

    accepted = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1", "TAIJI_LICENSE_ALLOW_LEGACY_MACHINE_BINDING": "1"},
        machine_fingerprint=LEGACY_V2_MACHINE_FINGERPRINT,
    )
    assert accepted.status == "valid"
