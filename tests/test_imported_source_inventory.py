import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = REPO_ROOT / "hermes-local-lab" / "sources" / "hermes-agent"
WEBUI_ROOT = REPO_ROOT / "hermes-local-lab" / "sources" / "hermes-webui"

EXPECTED_CONCEPT_EXAMPLES = {
    "apartment-floor-plan-conversion.md",
    "automated-password-reset-flow.md",
    "autonomous-llm-research-agent-flow.md",
    "banana-journey-tree-to-smoothie.md",
    "commercial-aircraft-structure.md",
    "cpu-ooo-microarchitecture.md",
    "electricity-grid-flow.md",
    "feature-film-production-pipeline.md",
    "hospital-emergency-department-flow.md",
    "ml-benchmark-grouped-bar-chart.md",
    "place-order-uml-sequence.md",
    "smart-city-infrastructure.md",
    "smartphone-layer-anatomy.md",
    "sn2-reaction-mechanism.md",
    "wind-turbine-structure.md",
}

EXPECTED_FONTS = {
    "Collapse-Bold.woff2",
    "Collapse-Regular.woff2",
    "Mondwest-Regular.woff2",
    "RulesCompressed-Medium.woff2",
    "RulesCompressed-Regular.woff2",
    "RulesExpanded-Bold.woff2",
    "RulesExpanded-Regular.woff2",
}

EXPECTED_WEBUI_IMAGES = {
    "pr-2458-board-selector-after.png",
    "pr-2458-board-selector-before.png",
    "pr-2919-clarify-after-desktop.png",
    "pr-2919-clarify-before-desktop.png",
    "update-banner-whats-new-after-summary-off.png",
    "update-banner-whats-new-after-summary-on.png",
    "update-banner-whats-new-before.png",
}

EXPECTED_WEBUI_PR_ASSETS = {
    "restore-top-titlebar-after.png",
    "restore-top-titlebar-before.png",
}

EXPECTED_RESTORED_INVENTORY_SHA256 = (
    "83c5f87f0a00125766ec2e641a6564ebfa19d591d369a6fc89ff4feff58d802f"
)


def tracked(relative_path: Path) -> bool:
    env = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(name, None)
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", "--", str(relative_path)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


class ImportedSourceInventoryTests(unittest.TestCase):
    def assert_present_nonempty_and_tracked(self, path: Path) -> None:
        self.assertTrue(path.is_file(), path)
        self.assertGreater(path.stat().st_size, 0, path)
        self.assertTrue(tracked(path.relative_to(REPO_ROOT)), path)

    def test_agent_assets_silently_ignored_during_original_import_are_tracked(self):
        examples = (
            AGENT_ROOT
            / "optional-skills"
            / "creative"
            / "concept-diagrams"
            / "examples"
        )
        self.assertEqual(
            {path.name for path in examples.glob("*.md")},
            EXPECTED_CONCEPT_EXAMPLES,
        )
        for name in EXPECTED_CONCEPT_EXAMPLES:
            self.assert_present_nonempty_and_tracked(examples / name)

        dashboard = (
            AGENT_ROOT / "plugins" / "hermes-achievements" / "dashboard"
        )
        manifest = json.loads((dashboard / "manifest.json").read_text(encoding="utf-8"))
        for manifest_key in ("entry", "css"):
            self.assert_present_nonempty_and_tracked(dashboard / manifest[manifest_key])

        for relative_path in (
            Path("skills/creative/p5js/references/export-pipeline.md"),
            Path("skills/creative/p5js/scripts/export-frames.js"),
        ):
            self.assert_present_nonempty_and_tracked(AGENT_ROOT / relative_path)

        fonts = AGENT_ROOT / "web" / "public" / "fonts"
        self.assertEqual(
            {path.name for path in fonts.glob("*.woff2")},
            EXPECTED_FONTS,
        )
        for name in EXPECTED_FONTS:
            self.assert_present_nonempty_and_tracked(fonts / name)

        stories_path = AGENT_ROOT / "website" / "src" / "data" / "userStories.json"
        stories = json.loads(stories_path.read_text(encoding="utf-8"))
        self.assertIsInstance(stories, list)
        self.assertGreater(len(stories), 200)
        self.assert_present_nonempty_and_tracked(stories_path)

    def test_webui_upstream_pr_evidence_is_complete_and_tracked(self):
        images = WEBUI_ROOT / "docs" / "images"
        for name in EXPECTED_WEBUI_IMAGES:
            self.assert_present_nonempty_and_tracked(images / name)

        pr_assets = WEBUI_ROOT / "docs" / "pr-assets"
        self.assertEqual(
            {path.name for path in pr_assets.iterdir() if path.is_file()},
            EXPECTED_WEBUI_PR_ASSETS,
        )
        for name in EXPECTED_WEBUI_PR_ASSETS:
            self.assert_present_nonempty_and_tracked(pr_assets / name)

        pr_media = WEBUI_ROOT / "docs" / "pr-media"
        pr_media_files = sorted(path for path in pr_media.rglob("*") if path.is_file())
        self.assertEqual(len(pr_media_files), 157)
        for path in pr_media_files:
            self.assert_present_nonempty_and_tracked(path)
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))

        restored_files = (
            [images / name for name in EXPECTED_WEBUI_IMAGES]
            + [pr_assets / name for name in EXPECTED_WEBUI_PR_ASSETS]
            + pr_media_files
        )
        self.assertEqual(len(restored_files), 166)

    def test_full_restored_inventory_has_exact_paths_modes_and_content(self):
        agent_files = sorted(
            (
                AGENT_ROOT
                / "optional-skills"
                / "creative"
                / "concept-diagrams"
                / "examples"
            ).glob("*.md")
        )
        agent_files.extend(
            [
                AGENT_ROOT
                / "plugins"
                / "hermes-achievements"
                / "dashboard"
                / "dist"
                / "index.js",
                AGENT_ROOT
                / "plugins"
                / "hermes-achievements"
                / "dashboard"
                / "dist"
                / "style.css",
                AGENT_ROOT / "skills/creative/p5js/references/export-pipeline.md",
                AGENT_ROOT / "skills/creative/p5js/scripts/export-frames.js",
            ]
        )
        agent_files.extend(sorted((AGENT_ROOT / "web/public/fonts").glob("*.woff2")))
        agent_files.append(AGENT_ROOT / "website/src/data/userStories.json")

        webui_files = [
            WEBUI_ROOT / "docs" / "images" / name
            for name in EXPECTED_WEBUI_IMAGES
        ]
        webui_files.extend(
            WEBUI_ROOT / "docs" / "pr-assets" / name
            for name in EXPECTED_WEBUI_PR_ASSETS
        )
        webui_files.extend(
            sorted(
                path
                for path in (WEBUI_ROOT / "docs" / "pr-media").rglob("*")
                if path.is_file()
            )
        )

        restored_files = sorted(agent_files + webui_files)
        self.assertEqual(len(restored_files), 193)
        digest = hashlib.sha256()
        for path in restored_files:
            relative_path = path.relative_to(REPO_ROOT).as_posix().encode()
            executable = b"x" if path.stat().st_mode & 0o111 else b"-"
            content_sha = hashlib.sha256(path.read_bytes()).hexdigest().encode()
            digest.update(
                relative_path + b"\0" + executable + b"\0" + content_sha + b"\n"
            )
        self.assertEqual(digest.hexdigest(), EXPECTED_RESTORED_INVENTORY_SHA256)

    def test_tracked_probe_ignores_ambient_git_locator_variables(self):
        expected = Path(
            "hermes-local-lab/sources/hermes-agent/"
            "website/src/data/userStories.json"
        )
        with mock.patch.dict(
            os.environ,
            {
                "GIT_DIR": "/definitely/not/the/taiji/repository",
                "GIT_INDEX_FILE": "/definitely/not/the/taiji/index",
            },
        ):
            self.assertTrue(tracked(expected))


if __name__ == "__main__":
    unittest.main()
