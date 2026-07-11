import json
import inspect
import stat
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


def test_source_development_noarg_guard_never_reads_user_license(monkeypatch):
    monkeypatch.setenv("TAIJI_LICENSE_REQUIRED", "1")
    monkeypatch.setenv("TAIJI_LICENSE_PUBLIC_KEY", "attacker-controlled-key")
    monkeypatch.setattr(
        taiji_license,
        "default_license_path",
        lambda *_args, **_kwargs: pytest.fail("source-development guard read user license"),
    )

    status = taiji_license.load_license_status()
    blocked = taiji_license.require_valid_license()

    assert status.status == "not_required"
    assert status.required is False
    assert status.policy == "source-development"
    assert blocked is None


def test_production_policy_is_fixed_and_rejects_disable_override(
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
    assert public["policy"] == "production"
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
def test_production_policy_rejects_every_security_override_intent(
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


def test_production_policy_pins_installed_public_key_and_fingerprint():
    policy = taiji_license.production_license_policy()

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


def test_production_ignores_license_path_environment_redirect(
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

    assert status.status == "missing"
    assert status.code == "license_missing"


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


def test_installed_candidate_validation_uses_production_policy(
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
    assert status.policy == "production"
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
    policy = taiji_license.production_license_policy()

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
