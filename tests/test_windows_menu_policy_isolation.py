"""Contracts that keep Windows menu policy isolated from Linux/Kylin."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SHARED_CONFIG = ROOT / "hermes-local-lab/config/taiji-default-config.yaml"
WINDOWS_CONFIG = ROOT / "packaging/windows/taiji-default-config.yaml"
LINUX_PACKAGING_ENTRYPOINTS = (
    ROOT / "taijiagent 打包交付/99_本机_准备制包输入包.sh",
    ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
    ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh",
    ROOT / "packaging/linux/deb/build-deb.sh",
)


class WindowsMenuPolicyIsolationTests(unittest.TestCase):
    def test_windows_policy_differs_only_by_profiles_visibility(self) -> None:
        self.assertTrue(
            WINDOWS_CONFIG.is_file(),
            "Windows menu policy must use a dedicated packaging config",
        )
        shared = yaml.safe_load(SHARED_CONFIG.read_text(encoding="utf-8"))
        windows = yaml.safe_load(WINDOWS_CONFIG.read_text(encoding="utf-8"))

        self.assertIs(shared["webui"]["feature_visibility"]["nav"]["profiles"], True)
        self.assertIs(windows["webui"]["feature_visibility"]["nav"]["profiles"], False)
        normalized = copy.deepcopy(windows)
        normalized["webui"]["feature_visibility"]["nav"]["profiles"] = True
        self.assertEqual(normalized, shared)

    def test_linux_packaging_does_not_reference_windows_config(self) -> None:
        windows_config = "packaging/windows/taiji-default-config.yaml"
        for entrypoint in LINUX_PACKAGING_ENTRYPOINTS:
            with self.subTest(entrypoint=entrypoint.relative_to(ROOT)):
                self.assertNotIn(
                    windows_config,
                    entrypoint.read_text(encoding="utf-8"),
                )

        build_deb = LINUX_PACKAGING_ENTRYPOINTS[-1].read_text(encoding="utf-8")
        self.assertIn(
            'DEFAULT_CONFIG="$LAB_DIR/config/taiji-default-config.yaml"',
            build_deb,
        )


if __name__ == "__main__":
    unittest.main()
