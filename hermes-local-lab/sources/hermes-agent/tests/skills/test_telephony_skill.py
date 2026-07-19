from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "optional-skills"
    / "productivity"
    / "telephony"
    / "scripts"
    / "telephony.py"
)

TELEPHONY_ENV_KEYS = {
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "TWILIO_PHONE_NUMBER_SID",
    "BLAND_API_KEY",
    "BLAND_DEFAULT_VOICE",
    "VAPI_API_KEY",
    "VAPI_PHONE_NUMBER_ID",
    "VAPI_VOICE_PROVIDER",
    "VAPI_VOICE_ID",
    "VAPI_MODEL",
    "PHONE_PROVIDER",
}


@pytest.fixture(autouse=True)
def isolate_telephony_environment(monkeypatch):
    for key in TELEPHONY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def load_module():
    spec = importlib.util.spec_from_file_location("telephony_skill", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_save_twilio_writes_env_and_state(tmp_path: Path, monkeypatch):
    mod = load_module()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

    result = mod.save_twilio(
        "AC123",
        "secret-token",
        phone_number="+1 (702) 555-1234",
        phone_sid="PN123",
    )

    env_text = (tmp_path / ".hermes" / ".env").read_text(encoding="utf-8")
    state = json.loads((tmp_path / ".hermes" / "telephony_state.json").read_text(encoding="utf-8"))

    assert result["success"] is True
    assert "TWILIO_ACCOUNT_SID=AC123" in env_text
    assert "TWILIO_AUTH_TOKEN=secret-token" in env_text
    assert "TWILIO_PHONE_NUMBER=+17025551234" in env_text
    assert "TWILIO_PHONE_NUMBER_SID=PN123" in env_text
    assert state["twilio"]["default_phone_number"] == "+17025551234"
    assert state["twilio"]["default_phone_sid"] == "PN123"


def test_upsert_env_updates_existing_values(tmp_path: Path):
    mod = load_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# retained comment\n"
        "TWILIO_PHONE_NUMBER=+15550000000\n"
        "OTHER=keep\n",
        encoding="utf-8",
    )
    env_path.chmod(0o644)

    mod._upsert_env_file(
        {
            "TWILIO_PHONE_NUMBER": "+15551112222",
            "TWILIO_PHONE_NUMBER_SID": "PN999",
        },
        env_path=env_path,
    )

    env_text = env_path.read_text(encoding="utf-8")
    assert "TWILIO_PHONE_NUMBER=+15551112222" in env_text
    assert "TWILIO_PHONE_NUMBER_SID=PN999" in env_text
    assert "OTHER=keep" in env_text
    assert "# retained comment" in env_text
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_upsert_env_repairs_private_mode_when_value_is_unchanged(
    tmp_path: Path,
):
    mod = load_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# retained\nTWILIO_ACCOUNT_SID=AC123\n",
        encoding="utf-8",
    )
    env_path.chmod(0o644)

    mod._upsert_env_file(
        {"TWILIO_ACCOUNT_SID": "AC123"},
        env_path=env_path,
    )

    assert env_path.read_text(encoding="utf-8") == (
        "# retained\nTWILIO_ACCOUNT_SID=AC123\n"
    )
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_upsert_env_preserves_group_shared_mode_and_group(
    tmp_path: Path,
    monkeypatch,
):
    mod = load_module()
    home = tmp_path / "shared-home"
    home.mkdir(mode=0o2770)
    home.chmod(0o2770)
    env_path = home / ".env"
    env_path.write_text(
        "# retained\nTWILIO_ACCOUNT_SID=AC123\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")

    mod._upsert_env_file(
        {"TWILIO_ACCOUNT_SID": "AC123"},
        env_path=env_path,
    )

    assert stat.S_IMODE(env_path.stat().st_mode) == 0o640
    assert env_path.stat().st_gid == home.stat().st_gid


def test_upsert_env_delegates_all_updates_to_canonical_writer_once(
    tmp_path: Path,
    monkeypatch,
):
    import agent.provider_credentials as credentials

    calls: list[tuple[dict[str, str], Path]] = []

    def fake_mutate_env_unique(updates, *, config_path, expected_values=None):
        assert expected_values is None
        calls.append((dict(updates), Path(config_path)))
        return {key: True for key in updates}

    monkeypatch.setattr(
        credentials,
        "mutate_env_unique",
        fake_mutate_env_unique,
    )
    mod = load_module()
    env_path = tmp_path / "profile" / ".env"

    result = mod._upsert_env_file(
        {
            "TWILIO_ACCOUNT_SID": "AC123",
            "TWILIO_AUTH_TOKEN": "secret",
        },
        env_path=env_path,
    )

    assert result == env_path
    assert calls == [
        (
            {
                "TWILIO_ACCOUNT_SID": "AC123",
                "TWILIO_AUTH_TOKEN": "secret",
            },
            env_path.parent / "config.yaml",
        )
    ]
    assert not env_path.exists()


def test_upsert_env_rejects_duplicate_key_without_partial_write(
    tmp_path: Path,
):
    mod = load_module()
    env_path = tmp_path / ".env"
    original = (
        b"# retained\n"
        b"TWILIO_ACCOUNT_SID=first\n"
        b"TWILIO_ACCOUNT_SID=second\n"
        b"OTHER=keep\n"
    )
    env_path.write_bytes(original)

    with pytest.raises(ValueError, match="duplicate"):
        mod._upsert_env_file(
            {
                "TWILIO_ACCOUNT_SID": "new",
                "TWILIO_AUTH_TOKEN": "secret",
            },
            env_path=env_path,
        )

    assert env_path.read_bytes() == original


def test_load_state_rejects_corrupt_json_without_erasing_it(tmp_path: Path):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    original = b'{"version": 1, "twilio": '
    state_path.write_bytes(original)

    with pytest.raises(mod.TelephonyError, match="telephony state"):
        mod._load_state(state_path)

    assert state_path.read_bytes() == original


def test_save_state_replace_failure_preserves_previous_file(
    tmp_path: Path,
    monkeypatch,
):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    original = b'{"version": 1, "twilio": {"default_phone_sid": "PN-old"}}\n'
    state_path.write_bytes(original)
    real_replace = mod.os.replace

    def fail_state_replace(source, target):
        if Path(target) == state_path:
            raise OSError("simulated atomic replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(mod.os, "replace", fail_state_replace)

    with pytest.raises(OSError, match="simulated atomic replace failure"):
        mod._save_state(
            {
                "version": 1,
                "twilio": {"default_phone_sid": "PN-new"},
            },
            state_path,
        )

    assert state_path.read_bytes() == original


@pytest.mark.parametrize(
    ("group_shared", "managed", "expected_mode"),
    [
        ("0", None, 0o600),
        ("1", None, 0o640),
        ("1", "nixos", 0o640),
    ],
)
def test_atomic_state_write_uses_active_credential_access_policy(
    tmp_path: Path,
    monkeypatch,
    group_shared: str,
    managed: str | None,
    expected_mode: int,
):
    mod = load_module()
    home = tmp_path / f"home-{group_shared}-{managed or 'local'}"
    if group_shared == "1":
        home.mkdir(mode=0o2770)
        home.chmod(0o2770)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", group_shared)
    if managed is None:
        monkeypatch.delenv("HERMES_MANAGED", raising=False)
    else:
        monkeypatch.setenv("HERMES_MANAGED", managed)
    state_path = home / "telephony_state.json"

    mod._save_state({"version": 1, "provider": "twilio"}, state_path)

    assert stat.S_IMODE(state_path.stat().st_mode) == expected_mode
    if group_shared == "1":
        assert state_path.stat().st_gid == home.stat().st_gid


def test_atomic_state_post_replace_failure_is_marked_committed(
    tmp_path: Path,
    monkeypatch,
):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    state_path.write_text('{"version": 1}\n', encoding="utf-8")
    real_fsync = mod.os.fsync

    def fail_state_directory_fsync(file_descriptor):
        if stat.S_ISDIR(mod.os.fstat(file_descriptor).st_mode):
            raise OSError("simulated state directory fsync failure")
        return real_fsync(file_descriptor)

    monkeypatch.setattr(mod.os, "fsync", fail_state_directory_fsync)

    with pytest.raises(
        OSError,
        match="simulated state directory fsync failure",
    ) as failure:
        mod._atomic_write_state_unlocked(
            {"version": 1, "twilio": {"default_phone_sid": "PN-new"}},
            state_path,
        )

    assert getattr(failure.value, "state_committed", False) is True
    state = json.loads(state_path.read_bytes())
    assert state["twilio"]["default_phone_sid"] == "PN-new"


def test_concurrent_remember_operations_do_not_overwrite_stale_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    state_path.write_text('{"version": 1}\n', encoding="utf-8")
    original_read_text = Path.read_text
    both_snapshots_loaded = threading.Barrier(2)

    def synchronized_read_text(path, *args, **kwargs):
        payload = original_read_text(path, *args, **kwargs)
        if path == state_path:
            both_snapshots_loaded.wait(timeout=5)
        return payload

    monkeypatch.setattr(Path, "read_text", synchronized_read_text)
    failures: list[BaseException] = []

    def remember_twilio():
        try:
            mod._remember_twilio_number(
                phone_number="+17025550123",
                phone_sid="PN111",
                state_path=state_path,
            )
        except BaseException as exc:
            failures.append(exc)

    def remember_vapi():
        try:
            mod._remember_vapi_number(
                phone_number_id="vapi-phone-xyz",
                state_path=state_path,
            )
        except BaseException as exc:
            failures.append(exc)

    workers = [
        threading.Thread(target=remember_twilio),
        threading.Thread(target=remember_vapi),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(not worker.is_alive() for worker in workers)
    assert failures == []
    state = json.loads(state_path.read_bytes())
    assert state["twilio"]["default_phone_sid"] == "PN111"
    assert state["vapi"]["phone_number_id"] == "vapi-phone-xyz"


def test_remember_env_failure_leaves_state_unchanged(
    tmp_path: Path,
    monkeypatch,
):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    env_path = tmp_path / ".env"
    original_state = b'{"version": 1, "unrelated": {"keep": true}}\n'
    original_env = b"OTHER=keep\n"
    state_path.write_bytes(original_state)
    env_path.write_bytes(original_env)

    def fail_env_write(updates, env_path=None):
        raise OSError("simulated env write failure")

    monkeypatch.setattr(mod, "_upsert_env_file", fail_env_write)

    with pytest.raises(OSError, match="simulated env write failure"):
        mod._remember_twilio_number(
            phone_number="+17025550123",
            phone_sid="PN111",
            save_env=True,
            state_path=state_path,
            env_path=env_path,
        )

    assert state_path.read_bytes() == original_state
    assert env_path.read_bytes() == original_env


def test_remember_state_failure_rolls_back_env(
    tmp_path: Path,
    monkeypatch,
):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    env_path = tmp_path / ".env"
    original_state = b'{"version": 1, "unrelated": {"keep": true}}\n'
    original_env = b"# retained\nTWILIO_PHONE_NUMBER=+15550000000\nOTHER=keep\n"
    state_path.write_bytes(original_state)
    env_path.write_bytes(original_env)

    def fail_state_write(state, path):
        raise OSError("simulated state write failure")

    monkeypatch.setattr(
        mod,
        "_atomic_write_state_unlocked",
        fail_state_write,
        raising=False,
    )

    with pytest.raises(OSError, match="simulated state write failure"):
        mod._remember_twilio_number(
            phone_number="+17025550123",
            phone_sid="PN111",
            save_env=True,
            state_path=state_path,
            env_path=env_path,
        )

    assert state_path.read_bytes() == original_state
    assert env_path.read_bytes() == original_env


def test_remember_post_replace_failure_keeps_state_and_env_consistent(
    tmp_path: Path,
    monkeypatch,
):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    env_path = tmp_path / ".env"
    state_path.write_text('{"version": 1}\n', encoding="utf-8")
    env_path.write_text(
        "TWILIO_PHONE_NUMBER=+15550000000\n",
        encoding="utf-8",
    )
    real_fsync = mod.os.fsync

    def fail_state_directory_fsync(file_descriptor):
        descriptor_mode = mod.os.fstat(file_descriptor).st_mode
        state_was_replaced = (
            state_path.exists()
            and b"+17025550123" in state_path.read_bytes()
        )
        if stat.S_ISDIR(descriptor_mode) and state_was_replaced:
            raise OSError("simulated state directory fsync failure")
        return real_fsync(file_descriptor)

    monkeypatch.setattr(mod.os, "fsync", fail_state_directory_fsync)

    with pytest.raises(
        OSError,
        match="simulated state directory fsync failure",
    ):
        mod._remember_twilio_number(
            phone_number="+17025550123",
            phone_sid="PN111",
            save_env=True,
            state_path=state_path,
            env_path=env_path,
        )

    state = json.loads(state_path.read_bytes())
    env_text = env_path.read_text(encoding="utf-8")
    assert state["twilio"]["default_phone_number"] == "+17025550123"
    assert "TWILIO_PHONE_NUMBER=+17025550123" in env_text
    assert "TWILIO_PHONE_NUMBER_SID=PN111" in env_text


def test_messages_after_checkpoint_returns_only_newer_items():
    mod = load_module()
    messages = [
        {"sid": "SM3", "body": "newest"},
        {"sid": "SM2", "body": "middle"},
        {"sid": "SM1", "body": "oldest"},
    ]

    assert mod._messages_after_checkpoint(messages, "") == messages
    assert mod._messages_after_checkpoint(messages, "SM2") == [{"sid": "SM3", "body": "newest"}]
    assert mod._messages_after_checkpoint(messages, "SM3") == []


def test_twilio_buy_number_saves_env_and_state(tmp_path: Path):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    env_path = tmp_path / ".env"

    mod._twilio_request = lambda method, path, params=None, form=None: {
        "sid": "PN111",
        "phone_number": "+17025550123",
        "friendly_name": "Test Number",
        "capabilities": {"voice": True, "sms": True},
    }

    result = mod._twilio_buy_number(
        "+17025550123",
        save_env=True,
        state_path=state_path,
        env_path=env_path,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    env_text = env_path.read_text(encoding="utf-8")

    assert result["phone_sid"] == "PN111"
    assert state["twilio"]["default_phone_number"] == "+17025550123"
    assert state["twilio"]["default_phone_sid"] == "PN111"
    assert "TWILIO_PHONE_NUMBER=+17025550123" in env_text
    assert "TWILIO_PHONE_NUMBER_SID=PN111" in env_text


def test_twilio_inbox_marks_seen_checkpoint(tmp_path: Path):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    mod._save_state(
        {
            "version": 1,
            "twilio": {
                "default_phone_number": "+17025550123",
                "default_phone_sid": "PN111",
                "last_inbound_message_sid": "SM1",
            },
        },
        state_path,
    )

    mod._twilio_owned_numbers = lambda limit=50: [
        mod.OwnedTwilioNumber(
            sid="PN111",
            phone_number="+17025550123",
            friendly_name="Main",
            capabilities={"voice": True, "sms": True},
        )
    ]
    mod._twilio_request = lambda method, path, params=None, form=None: {
        "messages": [
            {
                "sid": "SM3",
                "direction": "inbound",
                "status": "received",
                "from": "+15551230000",
                "to": "+17025550123",
                "date_sent": "Tue, 14 Mar 2026 09:00:00 +0000",
                "body": "new message",
                "num_media": "0",
            },
            {
                "sid": "SM1",
                "direction": "inbound",
                "status": "received",
                "from": "+15551110000",
                "to": "+17025550123",
                "date_sent": "Tue, 14 Mar 2026 08:00:00 +0000",
                "body": "old message",
                "num_media": "0",
            },
        ]
    }

    result = mod._twilio_inbox(limit=10, since_last=True, mark_seen=True, state_path=state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert result["count"] == 1
    assert result["messages"][0]["sid"] == "SM3"
    assert state["twilio"]["last_inbound_message_sid"] == "SM3"


def test_vapi_import_twilio_number_saves_phone_number_id(tmp_path: Path):
    mod = load_module()
    state_path = tmp_path / "telephony_state.json"
    env_path = tmp_path / ".env"

    mod._vapi_api_key = lambda: "vapi-key"
    mod._twilio_creds = lambda: ("AC123", "token123")
    mod._resolve_twilio_number = lambda identifier=None: mod.OwnedTwilioNumber(
        sid="PN111",
        phone_number="+17025550123",
        friendly_name="Main",
        capabilities={"voice": True, "sms": True},
    )
    mod._json_request = lambda method, url, headers=None, params=None, form=None, json_body=None: {
        "id": "vapi-phone-xyz"
    }

    result = mod._vapi_import_twilio_number(
        save_env=True,
        state_path=state_path,
        env_path=env_path,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    env_text = env_path.read_text(encoding="utf-8")

    assert result["phone_number_id"] == "vapi-phone-xyz"
    assert state["vapi"]["phone_number_id"] == "vapi-phone-xyz"
    assert "VAPI_PHONE_NUMBER_ID=vapi-phone-xyz" in env_text


def test_diagnose_includes_decision_tree_and_saved_state(tmp_path: Path, monkeypatch):
    mod = load_module()
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    mod._save_state(
        {
            "version": 1,
            "twilio": {
                "default_phone_number": "+17025550123",
                "last_inbound_message_sid": "SM123",
            },
            "vapi": {
                "phone_number_id": "vapi-abc",
            },
        },
        hermes_home / "telephony_state.json",
    )
    (hermes_home / ".env").parent.mkdir(parents=True, exist_ok=True)
    (hermes_home / ".env").write_text(
        "TWILIO_ACCOUNT_SID=AC123\nTWILIO_AUTH_TOKEN=token\nBLAND_API_KEY=bland\n",
        encoding="utf-8",
    )

    result = mod.diagnose()

    assert result["providers"]["twilio"]["default_phone_number"] == "+17025550123"
    assert result["providers"]["twilio"]["last_inbound_message_sid"] == "SM123"
    assert result["providers"]["bland"]["configured"] is True
    assert result["providers"]["vapi"]["phone_number_id"] == "vapi-abc"
    assert any(item["use"] == "Twilio" for item in result["decision_tree"])
