import json
import types
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import taiji_license


TEST_MACHINE_CODE = "sha256:" + "a" * 64
OTHER_MACHINE_CODE = "sha256:" + "b" * 64
TEST_MACHINE_FINGERPRINT = {
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


def _write_token(path, private_pem, **overrides):
    now = int(time.time())
    payload = {
        "license_id": "lic-test",
        "customer": "测试客户",
        "product": "taiji-agent",
        "aud": "taiji-agent",
        "binding_type": "machine_fingerprint_v2",
        "machine_code": TEST_MACHINE_CODE,
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
    assert "token" not in public
    assert "path" not in public
    assert TEST_MACHINE_CODE not in json.dumps(public)


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


def test_machine_fingerprint_v2_ignores_physical_mac_changes(monkeypatch):
    mac_sets = iter(
        [
            ["00:11:22:33:44:55"],
            ["66:77:88:99:aa:bb"],
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

    wireless = taiji_license.get_machine_fingerprint(use_cache=False, now=1_000_000)
    wired = taiji_license.get_machine_fingerprint(use_cache=False, now=1_000_001)
    disconnected = taiji_license.get_machine_fingerprint(use_cache=False, now=1_000_002)

    assert wireless["binding_type"] == "machine_fingerprint_v2"
    assert wireless["collection_version"] == 2
    assert wireless["machine_code"] == wired["machine_code"] == disconnected["machine_code"]
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

    blocked = taiji_license.require_valid_license(
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

    taiji_license.require_valid_license(
        path=path,
        public_key=public_pem,
        now=1_000_000,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )
    taiji_license.require_valid_license(
        path=path,
        public_key=public_pem,
        now=1_000_030,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )
    throttled = json.loads(state_path.read_text(encoding="utf-8"))
    assert throttled["last_successful_validation_at"] == 1_000_000

    taiji_license.require_valid_license(
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

    blocked = taiji_license.require_valid_license(
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

    blocked = taiji_license.require_valid_license(
        path=path,
        public_key=public_pem,
        now=999_000,
        state_path=state_path,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
        machine_fingerprint=TEST_MACHINE_FINGERPRINT,
    )
    assert blocked is not None

    recovered = taiji_license.require_valid_license(
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

    blocked = taiji_license.require_valid_license(
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
    assert status.code == "license_invalid_signature"


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

    status = taiji_license.load_license_status(
        path=path,
        public_key=public_pem,
        environ={"TAIJI_LICENSE_REQUIRED": "1"},
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
    assert request["binding_type"] == "machine_fingerprint_v2"
    assert request["collection_version"] == 2
    assert request["machine_code"] == TEST_MACHINE_CODE
    assert request["machine_code_short"] == "aaaaaaaaaaaa"
    raw = json.dumps(request, ensure_ascii=False)
    assert "PRIVATE KEY" not in raw
    assert "00:11" not in raw
