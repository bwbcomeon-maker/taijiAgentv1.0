# coding: utf-8
"""RED contract tests for non-reusable profile incarnations.

All profile filesystem activity is isolated under ``tmp_path``.  These tests
intentionally describe the missing contract only; production code belongs in
the GREEN step.
"""

import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

import api.profiles as profiles_mod
from agent.image_gen_verification import (
    image_gen_fingerprint_from_material,
    resolve_image_gen_material,
)
from agent.image_runtime import (
    resolve_vision_material,
    vision_fingerprint_from_material,
)


PROFILE_INCARNATION_KEY = "_taiji_profile_incarnation"


def _read_incarnation(profile_dir: Path) -> str:
    config_path = profile_dir / "config.yaml"
    if not config_path.is_file():
        return ""
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(config_data, dict):
        return ""
    return str(config_data.get(PROFILE_INCARNATION_KEY) or "").strip()


def _install_isolated_cli(
    monkeypatch: pytest.MonkeyPatch,
    profiles_root: Path,
) -> None:
    def create_profile(
        name,
        *,
        clone_from=None,
        clone_config=False,
        **_kwargs,
    ):
        profile_dir = profiles_root / name
        profile_dir.mkdir(parents=True)
        if clone_from and clone_config:
            source_dir = profiles_root / clone_from
            for filename in ("config.yaml", ".env"):
                source = source_dir / filename
                if source.is_file():
                    shutil.copy2(source, profile_dir / filename)
        return profile_dir

    def delete_profile(name, **_kwargs):
        profile_dir = profiles_root / name
        shutil.rmtree(profile_dir)
        return profile_dir

    cli_package = ModuleType("hermes_cli")
    cli_profiles = ModuleType("hermes_cli.profiles")
    cli_profiles.create_profile = create_profile
    cli_profiles.delete_profile = delete_profile
    cli_profiles.seed_profile_skills = lambda *_args, **_kwargs: None
    cli_package.profiles = cli_profiles
    monkeypatch.setitem(sys.modules, "hermes_cli", cli_package)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", cli_profiles)


@pytest.fixture
def isolated_profile_home(tmp_path, monkeypatch):
    fake_home = tmp_path / ".hermes"
    profiles_root = fake_home / "profiles"
    profiles_root.mkdir(parents=True)
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.setenv("HERMES_BASE_HOME", str(fake_home))
    monkeypatch.setattr(profiles_mod, "_DEFAULT_HERMES_HOME", fake_home)
    monkeypatch.setattr(profiles_mod, "taiji_single_runtime_mode", lambda: False)
    monkeypatch.setattr(profiles_mod, "list_profiles_api", lambda: [])
    _install_isolated_cli(monkeypatch, profiles_root)
    return profiles_root


class TestProfileIncarnationLifecycle:
    def test_fresh_create_mints_nonempty_incarnation(
        self,
        isolated_profile_home,
    ):
        profiles_mod.create_profile_api("fresh-profile")

        incarnation = _read_incarnation(
            isolated_profile_home / "fresh-profile"
        )
        assert incarnation, (
            "每次 profile create 都必须持久化非空 incarnation；"
            "当前 fresh create 没有生成。"
        )

    def test_clone_and_same_name_recreate_never_reuse_incarnation(
        self,
        isolated_profile_home,
    ):
        source_dir = isolated_profile_home / "source-profile"
        source_dir.mkdir()
        source_dir.joinpath("config.yaml").write_text(
            yaml.safe_dump(
                {
                    PROFILE_INCARNATION_KEY: "source-incarnation",
                    "model": {"provider": "deepseek"},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        source_dir.joinpath(".env").write_text(
            "DEEPSEEK_API_KEY=cloned-secret\n",
            encoding="utf-8",
        )

        profiles_mod.create_profile_api(
            "reused-name",
            clone_from="source-profile",
            clone_config=True,
        )
        first_incarnation = _read_incarnation(
            isolated_profile_home / "reused-name"
        )

        profiles_mod.delete_profile_api("reused-name")
        profiles_mod.create_profile_api(
            "reused-name",
            clone_from="source-profile",
            clone_config=True,
        )
        second_incarnation = _read_incarnation(
            isolated_profile_home / "reused-name"
        )

        violations = []
        if not first_incarnation:
            violations.append("clone 没有 incarnation")
        elif first_incarnation == "source-incarnation":
            violations.append("clone 复用了 source incarnation")
        if not second_incarnation:
            violations.append("同名重建没有 incarnation")
        elif second_incarnation == "source-incarnation":
            violations.append("同名重建复用了 source incarnation")
        if first_incarnation == second_incarnation:
            violations.append("delete/recreate 后 incarnation 未变化")
        assert not violations, "；".join(violations)


class TestProfileIncarnationFingerprintBinding:
    @staticmethod
    def _config(incarnation: str) -> dict:
        return {PROFILE_INCARNATION_KEY: incarnation}

    def test_vision_fingerprint_changes_with_incarnation_only(self):
        vision_cfg = {
            "provider": "zai",
            "model": "glm-4.5v",
        }

        fingerprints = []
        for incarnation in ("incarnation-a", "incarnation-b"):
            resolved = resolve_vision_material(
                vision_cfg,
                self._config(incarnation),
            )
            fingerprint, _runtime_resolved = (
                vision_fingerprint_from_material(
                    resolved,
                    profile="same-profile-name",
                    secret_value="same-secret",
                    key_configured=True,
                )
            )
            fingerprints.append(fingerprint)

        assert fingerprints[0] != fingerprints[1], (
            "vision fingerprint 必须绑定 profile incarnation，"
            "否则同名重建会复活旧验证证明。"
        )

    def test_image_fingerprint_changes_with_incarnation_only(self):
        image_cfg = {
            "provider": "doubao",
            "model": "doubao-seedream-4-0-250828",
        }

        fingerprints = []
        for incarnation in ("incarnation-a", "incarnation-b"):
            resolved = resolve_image_gen_material(
                image_cfg,
                config_data=self._config(incarnation),
            )
            fingerprints.append(
                image_gen_fingerprint_from_material(
                    resolved,
                    profile="same-profile-name",
                    secret_value="same-secret",
                )
            )

        assert fingerprints[0] != fingerprints[1], (
            "image fingerprint 必须绑定 profile incarnation，"
            "否则同名重建会复活旧验证证明。"
        )
