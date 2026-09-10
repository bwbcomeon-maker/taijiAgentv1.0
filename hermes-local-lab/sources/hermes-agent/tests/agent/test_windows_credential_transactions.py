"""Windows commit behavior, using isolated files and synthetic credentials."""
import json
import os
import subprocess
import sys

import pytest

from agent import provider_credentials as store


@pytest.fixture
def windows_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_credential_platform_name", lambda: "win32")
    return tmp_path / "config.yaml"


def test_windows_pair_save_update_delete(windows_store):
    path = windows_store
    store.mutate_config_env_strict(
        lambda config: config.update(model={"provider": "zai-cn", "default": "test-model"}),
        {"GLM_CN_API_KEY": "TEST_ONLY_first_key"}, config_path=path,
    )
    assert store.load_credential_snapshot(path).env["GLM_CN_API_KEY"] == "TEST_ONLY_first_key"
    store.mutate_env_unique({"GLM_CN_API_KEY": "TEST_ONLY_second_key"}, config_path=path)
    snapshot = store.load_credential_snapshot(path)
    assert snapshot.env["GLM_CN_API_KEY"] == "TEST_ONLY_second_key"
    assert snapshot.config["model"]["provider"] == "zai-cn"
    store.mutate_env_unique({"GLM_CN_API_KEY": None}, config_path=path)
    assert "GLM_CN_API_KEY" not in store.load_credential_snapshot(path).env


def _save_pair(path, value):
    store.mutate_config_env_strict(
        lambda config: config.update(test_generation=value),
        {"TEST_WINDOWS_KEY": value}, config_path=path,
    )


@pytest.mark.parametrize("fail_target", ["config.yaml", ".env"])
def test_interrupted_commit_recovers_before_read(windows_store, monkeypatch, fail_target):
    from agent import windows_credential_store as backend
    path = windows_store
    _save_pair(path, "old-synthetic")
    replace = backend._replace

    def interrupt(source, target):
        if target.name == fail_target:
            raise OSError("simulated file in use")
        replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(backend, "_replace", interrupt)
        with pytest.raises(store.CredentialRecoveryError):
            _save_pair(path, "new-synthetic")
    snapshot = store.load_credential_snapshot(path)
    assert snapshot.config["test_generation"] == "new-synthetic"
    assert snapshot.env["TEST_WINDOWS_KEY"] == "new-synthetic"
    assert not (path.parent / backend._INTENT).exists()


def test_restart_recovers_partial_commit(windows_store):
    path = windows_store
    script = '''
import os, sys
from pathlib import Path
from agent import provider_credentials as store, windows_credential_store as backend
store._credential_platform_name = lambda: "win32"
replace = backend._replace
def interrupt(source, target):
    replace(source, target)
    if target.name == "config.yaml":
        os._exit(73)
backend._replace = interrupt
store.mutate_config_env_strict(lambda c: c.update(test_generation="restart"),
    {"TEST_WINDOWS_KEY": "restart"}, config_path=Path(sys.argv[1]))
'''
    result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True)
    assert result.returncode == 73
    snapshot = store.load_credential_snapshot(path)
    assert snapshot.config["test_generation"] == snapshot.env["TEST_WINDOWS_KEY"] == "restart"


def test_external_conflict_preserves_evidence(windows_store, monkeypatch):
    from agent import windows_credential_store as backend
    path = windows_store
    _save_pair(path, "before")
    replace = backend._replace

    def interrupt(source, target):
        if target.name == ".env":
            raise OSError("simulated interrupted pair")
        replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(backend, "_replace", interrupt)
        with pytest.raises(store.CredentialRecoveryError):
            _save_pair(path, "after")
    env = path.parent / ".env"
    env.write_text("TEST_WINDOWS_KEY=external\n")
    with pytest.raises(store.CredentialRecoveryError):
        store.load_credential_snapshot(path)
    assert env.read_text() == "TEST_WINDOWS_KEY=external\n"
    assert (path.parent / backend._INTENT).exists()


@pytest.mark.parametrize("bad_manifest", ["[]", '{"schema":1,"schema":2}', '{"targets":[]}'])
def test_malformed_intent_fails_closed(windows_store, bad_manifest):
    from agent import windows_credential_store as backend
    path = windows_store
    (path.parent / backend._INTENT).write_text(bad_manifest)
    with pytest.raises(store.CredentialRecoveryError):
        store.load_credential_snapshot(path)
    assert not path.exists()


def test_linked_env_is_rejected(windows_store):
    path = windows_store
    external = path.parent / "external"
    external.write_text("TEST_WINDOWS_KEY=external\n")
    os.link(external, path.parent / ".env")
    with pytest.raises((store.CredentialRecoveryError, ValueError)):
        _save_pair(path, "new")
    assert external.read_text() == "TEST_WINDOWS_KEY=external\n"


def test_concurrent_processes_keep_pair_consistent(windows_store):
    path = windows_store
    script = '''
import sys
from pathlib import Path
from agent import provider_credentials as store
store._credential_platform_name = lambda: "win32"
for i in range(5):
    value = sys.argv[2] + str(i)
    store.mutate_config_env_strict(lambda c: c.update(test_generation=value),
        {"TEST_WINDOWS_KEY": value}, config_path=Path(sys.argv[1]))
'''
    children = [subprocess.Popen([sys.executable, "-c", script, str(path), str(i)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(3)]
    for child in children:
        _, stderr = child.communicate(timeout=20)
        assert child.returncode == 0, stderr.decode()[-500:]
    snapshot = store.load_credential_snapshot(path)
    assert snapshot.config["test_generation"] == snapshot.env["TEST_WINDOWS_KEY"]


def test_journal_publish_failure_keeps_old_pair(windows_store, monkeypatch):
    from agent import windows_credential_store as backend
    path = windows_store
    _save_pair(path, "before")
    before = (path.read_bytes(), (path.parent / ".env").read_bytes())
    replace = backend._replace

    def interrupt(source, target):
        if target.name == backend._INTENT:
            raise PermissionError("simulated denied")
        replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(backend, "_replace", interrupt)
        with pytest.raises(store.CredentialRecoveryError):
            _save_pair(path, "after")
    assert before == (path.read_bytes(), (path.parent / ".env").read_bytes())
    assert not list(path.parent.glob(".taiji-credential-win-*.stage"))


def test_windows_intent_is_reserved_config_name(windows_store):
    from agent import windows_credential_store as backend
    with pytest.raises(ValueError, match="reserved"):
        store.load_credential_snapshot(windows_store.parent / backend._INTENT)


@pytest.mark.parametrize("prefix", ["", "profiles/coder/"])
def test_windows_intent_excluded_from_backups(prefix):
    from pathlib import Path
    from hermes_cli.backup import _should_exclude, _is_credential_transaction_artifact
    path = Path(prefix + ".taiji-credential-windows-intent.json")
    assert _should_exclude(path)
    assert _is_credential_transaction_artifact(path)


@pytest.mark.parametrize("damage", ["missing", "changed", "outside", "oversize"])
def test_damaged_recovery_evidence_is_retained(windows_store, monkeypatch, damage):
    from agent import windows_credential_store as backend
    path = windows_store
    replace = backend._replace
    def stop_after_intent(source, target):
        if target.name == "config.yaml":
            raise OSError("in use")
        replace(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(backend, "_replace", stop_after_intent)
        with pytest.raises(store.CredentialRecoveryError):
            _save_pair(path, "synthetic-value")
    journal = path.parent / backend._INTENT
    manifest = json.loads(journal.read_text())
    stage = path.parent / manifest["targets"][0]["stage"]
    if damage == "missing":
        stage.unlink()
    elif damage == "changed":
        stage.write_bytes(b"different")
    elif damage == "outside":
        manifest["targets"][0]["stage"] = "../external"
        journal.write_text(json.dumps(manifest))
    else:
        journal.write_bytes(b" " * (store._MAX_CREDENTIAL_JOURNAL_BYTES + 1))
    with pytest.raises(store.CredentialRecoveryError):
        store.load_credential_snapshot(path)
    assert journal.exists()
    assert not path.exists()


def test_projection_failure_recovers_committed_pair(windows_store, monkeypatch):
    path = windows_store
    with monkeypatch.context() as patch:
        def fail(*_):
            raise OSError("projection interrupted")
        patch.setattr(store, "_sync_recovered_process_env", fail)
        with pytest.raises(store.CredentialRecoveryError):
            _save_pair(path, "synthetic-value")
    assert store.load_credential_snapshot(path).env["TEST_WINDOWS_KEY"] == "synthetic-value"


def test_foreign_posix_intent_is_not_replayed(windows_store):
    path = windows_store
    (path.parent / store._CREDENTIAL_JOURNAL_NAME).write_text("{}")
    with pytest.raises(store.CredentialRecoveryError):
        _save_pair(path, "synthetic-value")
    assert not path.exists()
