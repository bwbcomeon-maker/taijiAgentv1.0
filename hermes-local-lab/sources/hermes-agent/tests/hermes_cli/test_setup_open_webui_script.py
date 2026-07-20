from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "setup_open_webui.sh"


def _source_only_copy(tmp_path: Path) -> Path:
    """Create a sourceable copy without ever invoking the install workflow."""
    content = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'main "$@"' in content
    copy = tmp_path / "setup_open_webui_source_only.sh"
    copy.write_text(content.replace('main "$@"', ":"), encoding="utf-8")
    return copy


def _script_env(env_path: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HERMES_ENV_FILE": str(env_path),
            "HERMES_API_HOST": "0.0.0.0",
            "HERMES_API_PORT": "9753",
            "HERMES_API_MODEL_NAME": "Hermes Test",
            "HERMES_PYTHON": sys.executable,
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    [
                        str(REPO_ROOT),
                        env.get("PYTHONPATH", ""),
                    ],
                )
            ),
        }
    )
    env.update(overrides)
    return env


def _run_configure(
    source_script: Path,
    env_path: Path,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; configure_hermes_api_env',
            "bash",
            str(source_script),
        ],
        cwd=source_script.parent,
        env=_script_env(env_path, **overrides),
        capture_output=True,
        text=True,
        check=False,
    )


def test_open_webui_env_update_is_one_canonical_five_key_commit(
    tmp_path: Path,
):
    fake_root = tmp_path / "fake"
    fake_agent = fake_root / "agent"
    fake_agent.mkdir(parents=True)
    (fake_agent / "__init__.py").write_text("", encoding="utf-8")
    (fake_agent / "provider_credentials.py").write_text(
        """
import json
import os
from contextlib import contextmanager
from pathlib import Path

class _Snapshot:
    env = {"API_SERVER_KEY": "existing-secret"}

@contextmanager
def credential_transaction(config_path):
    yield

def load_credential_snapshot(config_path):
    return _Snapshot()

def mutate_env_unique(updates, *, config_path):
    log_path = Path(os.environ["TAIJI_WRITER_CALL_LOG"])
    calls = json.loads(log_path.read_text()) if log_path.exists() else []
    calls.append({"updates": dict(updates), "config_path": str(config_path)})
    log_path.write_text(json.dumps(calls))
    return {key: True for key in updates}

def _credential_data_mode():
    return 0o600

def _enforce_active_credential_fd_policy(
    file_fd,
    target_path,
    *,
    expected_mode,
    label,
):
    return None
""",
        encoding="utf-8",
    )
    source_script = _source_only_copy(tmp_path)
    env_path = tmp_path / "profile" / ".env"
    call_log = tmp_path / "calls.json"
    result = _run_configure(
        source_script,
        env_path,
        PYTHONPATH=str(fake_root),
        TAIJI_WRITER_CALL_LOG=str(call_log),
    )

    assert result.returncode == 0, result.stderr
    calls = json.loads(call_log.read_text(encoding="utf-8"))
    assert calls == [
        {
            "updates": {
                "API_SERVER_ENABLED": "true",
                "API_SERVER_HOST": "0.0.0.0",
                "API_SERVER_PORT": "9753",
                "API_SERVER_MODEL_NAME": "Hermes Test",
                "API_SERVER_KEY": "existing-secret",
            },
            "config_path": str(env_path.parent / "config.yaml"),
        }
    ]


def test_open_webui_env_mode_uses_active_canonical_fd_policy(
    tmp_path: Path,
):
    fake_root = tmp_path / "fake"
    fake_agent = fake_root / "agent"
    fake_agent.mkdir(parents=True)
    (fake_agent / "__init__.py").write_text("", encoding="utf-8")
    (fake_agent / "provider_credentials.py").write_text(
        """
import json
import os
from contextlib import contextmanager
from pathlib import Path

class _Snapshot:
    env = {"API_SERVER_KEY": "existing-secret"}

@contextmanager
def credential_transaction(config_path):
    yield

def load_credential_snapshot(config_path):
    return _Snapshot()

def mutate_env_unique(updates, *, config_path):
    return {key: True for key in updates}

def _credential_data_mode():
    return 0o640

def _enforce_active_credential_fd_policy(
    file_fd,
    target_path,
    *,
    expected_mode,
    label,
):
    log_path = Path(os.environ["TAIJI_POLICY_CALL_LOG"])
    log_path.write_text(json.dumps({
        "target_path": str(target_path),
        "expected_mode": expected_mode,
        "label": label,
    }))
""",
        encoding="utf-8",
    )
    source_script = _source_only_copy(tmp_path)
    env_path = tmp_path / "profile" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "API_SERVER_KEY=existing-secret\n",
        encoding="utf-8",
    )
    policy_log = tmp_path / "policy.json"

    result = _run_configure(
        source_script,
        env_path,
        PYTHONPATH=str(fake_root),
        TAIJI_POLICY_CALL_LOG=str(policy_log),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(policy_log.read_text(encoding="utf-8")) == {
        "target_path": str(env_path),
        "expected_mode": 0o640,
        "label": "Open WebUI env",
    }


def test_open_webui_env_update_preserves_content_and_enforces_private_mode(
    tmp_path: Path,
):
    source_script = _source_only_copy(tmp_path)
    env_path = tmp_path / "profile" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "# retained comment\n"
        "UNRELATED=keep\n"
        "API_SERVER_KEY=existing-secret\n"
        "API_SERVER_PORT=8642\n",
        encoding="utf-8",
    )
    env_path.chmod(0o644)

    result = _run_configure(source_script, env_path)

    assert result.returncode == 0, result.stderr
    written = env_path.read_text(encoding="utf-8")
    assert "# retained comment\n" in written
    assert "UNRELATED=keep\n" in written
    assert "API_SERVER_ENABLED=true\n" in written
    assert "API_SERVER_HOST=0.0.0.0\n" in written
    assert "API_SERVER_PORT=9753\n" in written
    assert "API_SERVER_MODEL_NAME='Hermes Test'\n" in written
    assert "API_SERVER_KEY=existing-secret\n" in written
    assert written.count("API_SERVER_KEY=") == 1
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_open_webui_env_update_repairs_mode_when_values_are_unchanged(
    tmp_path: Path,
):
    source_script = _source_only_copy(tmp_path)
    env_path = tmp_path / "profile" / ".env"
    env_path.parent.mkdir(parents=True)
    original = (
        "# retained\n"
        "API_SERVER_ENABLED=true\n"
        "API_SERVER_HOST=0.0.0.0\n"
        "API_SERVER_PORT=9753\n"
        "API_SERVER_MODEL_NAME='Hermes Test'\n"
        "API_SERVER_KEY=existing-secret\n"
    )
    env_path.write_text(original, encoding="utf-8")
    env_path.chmod(0o644)

    result = _run_configure(source_script, env_path)

    assert result.returncode == 0, result.stderr
    assert env_path.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_open_webui_env_update_preserves_group_shared_mode_and_group(
    tmp_path: Path,
):
    source_script = _source_only_copy(tmp_path)
    env_path = tmp_path / "profile" / ".env"
    env_path.parent.mkdir(mode=0o2770)
    if hasattr(os, "chown"):
        os.chown(env_path.parent, -1, os.getegid())
    env_path.parent.chmod(0o2770)
    env_path.write_text(
        "# retained\nAPI_SERVER_KEY=existing-secret\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)

    result = _run_configure(
        source_script,
        env_path,
        HERMES_CREDENTIAL_GROUP_SHARED="1",
    )

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(env_path.parent.stat().st_mode) == 0o2770
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o640
    assert env_path.stat().st_gid == env_path.parent.stat().st_gid


def test_open_webui_env_update_rejects_duplicates_without_partial_write(
    tmp_path: Path,
):
    source_script = _source_only_copy(tmp_path)
    env_path = tmp_path / "profile" / ".env"
    env_path.parent.mkdir(parents=True)
    original = (
        b"# retained\n"
        b"API_SERVER_KEY=first\n"
        b"API_SERVER_KEY=second\n"
        b"UNRELATED=keep\n"
    )
    env_path.write_bytes(original)

    result = _run_configure(source_script, env_path)

    assert result.returncode != 0
    assert "duplicate" in result.stderr
    assert env_path.read_bytes() == original


def test_open_webui_env_update_does_not_lose_concurrent_unrelated_key(
    tmp_path: Path,
):
    source_script = _source_only_copy(tmp_path)
    env_path = tmp_path / "profile" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("# retained\nBASE=keep\n", encoding="utf-8")
    child_script = """
from pathlib import Path
import sys
from agent.provider_credentials import mutate_env_unique

mutate_env_unique(
    {"CONCURRENT_MARKER": "kept"},
    config_path=Path(sys.argv[1]),
)
"""
    env = _script_env(env_path)
    setup_process = subprocess.Popen(
        [
            "bash",
            "-c",
            'source "$1"; configure_hermes_api_env',
            "bash",
            str(source_script),
        ],
        cwd=source_script.parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    concurrent_process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(env_path.parent / "config.yaml"),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    setup_stdout, setup_stderr = setup_process.communicate(timeout=10)
    concurrent_stdout, concurrent_stderr = concurrent_process.communicate(
        timeout=10
    )

    assert setup_process.returncode == 0, setup_stdout + setup_stderr
    assert concurrent_process.returncode == 0, (
        concurrent_stdout + concurrent_stderr
    )
    written = env_path.read_text(encoding="utf-8")
    assert "BASE=keep\n" in written
    assert "CONCURRENT_MARKER=kept\n" in written
    assert "API_SERVER_ENABLED=true\n" in written
