import subprocess
from pathlib import Path
from unittest.mock import patch


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_project_markers(project_root: Path) -> None:
    (project_root / "hermes_cli").mkdir(parents=True)
    (project_root / "pyproject.toml").write_text(
        "[project]\nname = 'hermes-agent'\n",
        encoding="utf-8",
    )
    (project_root / "hermes_cli" / "config.py").write_text(
        "# source marker\n",
        encoding="utf-8",
    )


def _detect(project_root: Path, hermes_home: Path) -> str:
    with (
        patch("hermes_cli.config.get_managed_system", return_value=None),
        patch("hermes_cli.config.get_hermes_home", return_value=hermes_home),
        patch("hermes_constants.is_container", return_value=False),
    ):
        from hermes_cli.config import detect_install_method

        return detect_install_method(project_root=project_root)


def test_non_git_install_detected_as_pip(tmp_path):
    """Package-shaped files without Git metadata are still a pip install."""
    project_root = tmp_path / "site-packages" / "hermes_agent"
    _write_project_markers(project_root)

    assert _detect(project_root, tmp_path / "hermes-home") == "pip"


def test_git_install_detected_at_repository_root(tmp_path):
    """A real checkout at the Git toplevel is a source install."""
    project_root = tmp_path / "hermes-agent"
    project_root.mkdir()
    _write_project_markers(project_root)
    _git(project_root, "init")
    _git(project_root, "add", "--", "pyproject.toml", "hermes_cli/config.py")

    assert _detect(project_root, tmp_path / "hermes-home") == "git"


def test_tracked_nested_project_detected_in_parent_repository(tmp_path):
    """A tracked Hermes source tree nested in a larger repository is source."""
    repository_root = tmp_path / "taiji"
    project_root = repository_root / "sources" / "hermes-agent"
    repository_root.mkdir()
    _write_project_markers(project_root)
    _git(repository_root, "init")
    _git(
        repository_root,
        "add",
        "--",
        "sources/hermes-agent/pyproject.toml",
        "sources/hermes-agent/hermes_cli/config.py",
    )

    assert _detect(project_root, tmp_path / "hermes-home") == "git"


def test_untracked_pip_install_under_unrelated_repository_stays_pip(tmp_path):
    """An unrelated parent repository must not capture a nested pip install."""
    repository_root = tmp_path / "unrelated"
    project_root = repository_root / ".venv" / "site-packages" / "hermes_agent"
    repository_root.mkdir()
    (repository_root / "README.md").write_text("unrelated\n", encoding="utf-8")
    _write_project_markers(project_root)
    _git(repository_root, "init")
    _git(repository_root, "add", "--", "README.md")

    assert _detect(project_root, tmp_path / "hermes-home") == "pip"


def test_git_worktree_detected_when_dot_git_is_a_file(tmp_path):
    """Tracked nested source works inside a linked worktree with shared Git data."""
    repository_root = tmp_path / "source"
    worktree_root = tmp_path / "linked-worktree"
    source_project_root = repository_root / "sources" / "hermes-agent"
    worktree_project_root = worktree_root / "sources" / "hermes-agent"
    repository_root.mkdir()
    _write_project_markers(source_project_root)
    _git(repository_root, "init")
    _git(repository_root, "config", "user.name", "Hermes Test")
    _git(repository_root, "config", "user.email", "hermes@example.invalid")
    _git(
        repository_root,
        "add",
        "--",
        "sources/hermes-agent/pyproject.toml",
        "sources/hermes-agent/hermes_cli/config.py",
    )
    _git(repository_root, "commit", "-m", "initial")
    _git(repository_root, "worktree", "add", "--detach", str(worktree_root))

    assert (worktree_root / ".git").is_file()
    assert _detect(worktree_project_root, tmp_path / "hermes-home") == "git"


def test_managed_install_takes_precedence(tmp_path):
    """When HERMES_MANAGED is set, that takes precedence over git detection."""
    (tmp_path / ".git").mkdir()
    with patch("hermes_cli.config.get_managed_system", return_value="NixOS"), \
         patch("hermes_cli.config.get_hermes_home", return_value=tmp_path):
        from hermes_cli.config import detect_install_method
        method = detect_install_method(project_root=tmp_path)
        assert method == "nixos"


def test_recommended_update_command_pip():
    """Pip installs recommend pip install --upgrade."""
    from hermes_cli.config import recommended_update_command_for_method
    cmd = recommended_update_command_for_method("pip")
    assert "pip install" in cmd or "uv pip install" in cmd
    assert "--upgrade" in cmd
    assert "hermes-agent" in cmd


def test_stamp_file_takes_precedence(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".install_method").write_text("docker\n")
    with patch("hermes_cli.config.get_managed_system", return_value=None), \
         patch("hermes_cli.config.get_hermes_home", return_value=tmp_path):
        from hermes_cli.config import detect_install_method
        assert detect_install_method(project_root=tmp_path) == "docker"


def test_docker_detected_via_dockerenv(tmp_path):
    with patch("hermes_cli.config.get_managed_system", return_value=None), \
         patch("hermes_cli.config.get_hermes_home", return_value=tmp_path), \
         patch("hermes_constants.is_container", return_value=True):
        from hermes_cli.config import detect_install_method
        assert detect_install_method(project_root=tmp_path) == "docker"


def test_recommended_update_command_docker():
    from hermes_cli.config import recommended_update_command_for_method
    assert "docker pull" in recommended_update_command_for_method("docker")
