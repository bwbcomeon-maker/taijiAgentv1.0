"""
Regression tests for pytest-process state isolation.

Some tests import api.config/api.models during collection and directly write
sessions from the pytest process. conftest must publish the test state env vars
before those imports, not only for the server subprocess.
"""

from pathlib import Path


def test_api_config_uses_pytest_state_dir():
    import api.config as config
    from tests.conftest import TEST_STATE_DIR

    test_state_dir = TEST_STATE_DIR.resolve()
    production_state_dir = (Path.home() / ".hermes" / "webui").resolve()

    assert config.STATE_DIR == test_state_dir
    assert config.SESSION_DIR == test_state_dir / "sessions"
    assert config.STATE_DIR != production_state_dir
    assert production_state_dir not in config.SESSION_DIR.resolve().parents


def test_auto_test_resources_are_scoped_per_pytest_process():
    """Concurrent pytest runs in one worktree must not share server resources."""
    from tests import conftest

    repo_root = Path(conftest.REPO_ROOT)
    first_pid = 101
    second_pid = 102

    assert conftest._auto_test_port(repo_root, process_id=first_pid) != conftest._auto_test_port(
        repo_root,
        process_id=second_pid,
    )
    assert conftest._auto_state_dir_name(
        repo_root,
        process_id=first_pid,
    ) != conftest._auto_state_dir_name(repo_root, process_id=second_pid)


def test_test_server_never_kills_an_existing_port_owner():
    """A test run must fail closed instead of signalling another running task."""
    from tests import conftest

    source = Path(conftest.__file__).read_text(encoding="utf-8")

    assert "['fuser', '-k'" not in source
