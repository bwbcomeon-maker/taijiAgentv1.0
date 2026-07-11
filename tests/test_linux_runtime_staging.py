from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "packaging/linux/stage-runtime-components.py"
ALLOWLIST = ROOT / "packaging/linux/product-skills.allowlist.json"
PUBLIC_KEY = ROOT / "tools/taiji-license-issuer/private/signing-public.pem"
NODE_VERSION = "22.23.1"
NODE_ARCHIVE_SHA256 = "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
PUBLIC_KEY_FINGERPRINT = "2dcff4f2b5e6f7a5e7e3f730e2f4446ad3265964431f614de7550265f7628b35"
SKILL_IDS = {
    "docx-template-skill",
    "style-modeler",
    "web-article-extractor",
    "workflow-producer",
}


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode("utf-8") + b"\0")
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0")
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            digest.update(b"\0")
        elif path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
    return digest.hexdigest()


class LinuxRuntimeStagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="taiji-runtime-stage-"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.repo = self.temp_dir / "repo"
        self.install_root = self.temp_dir / "install"
        self.node_root = self.temp_dir / "node-root"
        self.repo.mkdir()
        self._make_node_root()
        self._make_docx_engine()
        self._make_skills()
        public_key = self.repo / "tools/taiji-license-issuer/private/signing-public.pem"
        public_key.parent.mkdir(parents=True)
        shutil.copy2(PUBLIC_KEY, public_key)
        allowlist = self.repo / "packaging/linux/product-skills.allowlist.json"
        allowlist.parent.mkdir(parents=True)
        fixture_allowlist = {
            "schema_version": "taiji-product-skills-allowlist/v1",
            "skills": [
                {
                    "id": "docx-template-skill",
                    "category": "productivity",
                    "source": "hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill",
                },
                {
                    "id": "style-modeler",
                    "category": "writing",
                    "source": "hermes-local-lab/custom-skills/writing-agent/style-modeler",
                },
                {
                    "id": "web-article-extractor",
                    "category": "writing",
                    "source": "hermes-local-lab/custom-skills/writing-agent/web-article-extractor",
                },
                {
                    "id": "workflow-producer",
                    "category": "writing",
                    "source": "hermes-local-lab/custom-skills/writing-agent/workflow-producer",
                },
            ],
        }
        allowlist.write_text(json.dumps(fixture_allowlist), encoding="utf-8")

    def _write(self, relative: str, content: str = "fixture\n", *, root: Path | None = None) -> Path:
        target = (root or self.repo) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _make_node_root(self) -> None:
        node = self.node_root / "bin/node"
        node.parent.mkdir(parents=True)
        header = bytearray(64)
        header[0:4] = b"\x7fELF"
        header[4] = 2
        header[5] = 1
        header[6] = 1
        header[16:18] = (2).to_bytes(2, "little")
        header[18:20] = (62).to_bytes(2, "little")
        node.write_bytes(bytes(header))
        node.chmod(0o755)
        self._write(".taiji-node-version", NODE_VERSION + "\n", root=self.node_root)
        self._write(".taiji-node-archive-sha256", NODE_ARCHIVE_SHA256 + "\n", root=self.node_root)
        self._write("LICENSE", "Node fixture license\n", root=self.node_root)
        self._write("include/node/node.h", "development header\n", root=self.node_root)
        self._write("lib/node_modules/npm/bin/npm-cli.js", "development package manager\n", root=self.node_root)
        self._write("share/doc/node/gdbinit", "development debugger helper\n", root=self.node_root)

    def _make_docx_engine(self) -> None:
        root = "hermes-local-lab/sources/docx-engine-v2"
        self._write(f"{root}/package.json", json.dumps({"name": "docx-engine-v2", "version": "0.1.0"}))
        self._write(f"{root}/package-lock.json", json.dumps({"lockfileVersion": 3, "packages": {}}))
        self._write(
            f"{root}/template-registry.json",
            json.dumps(
                {
                    "version": 1,
                    "builtin": [
                        {"templateId": "general-proposal", "path": "templates/general-proposal"},
                        {"templateId": "meeting-minutes", "path": "templates/meeting-minutes"},
                    ],
                    "installed": [],
                }
            ),
        )
        self._write(f"{root}/src/cli/list-templates.js", "process.stdout.write('ok\\n');\n")
        for template_id in ("general-proposal", "meeting-minutes"):
            self._write(f"{root}/templates/{template_id}/manifest.json", json.dumps({"id": template_id}))
            self._write(f"{root}/templates/{template_id}/template.docx", "PK fixture")
        self._write(f"{root}/node_modules/runtime-dep/index.js", "module.exports = true;\n")
        self._write(f"{root}/node_modules/runtime-dep/tests/leak.js")
        self._write(f"{root}/node_modules/runtime-dep/__tests__/leak.js")
        self._write(f"{root}/node_modules/runtime-dep/lib/runtime.test.js")
        self._write(f"{root}/node_modules/runtime-dep/lib/runtime.spec.js")
        self._write(f"{root}/node_modules/runtime-dep/docs/leak.md")
        self._write(f"{root}/node_modules/.cache/leak")
        self._write(f"{root}/node_modules/runtime-dep/.nyc_output/leak.json")
        self._write(f"{root}/tests/source-test.js")
        self._write(f"{root}/docs/source-doc.md")
        self._write(f"{root}/.git/config")

    def _make_skills(self) -> None:
        skill_sources = {
            "docx-template-skill": "hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill",
            "style-modeler": "hermes-local-lab/custom-skills/writing-agent/style-modeler",
            "web-article-extractor": "hermes-local-lab/custom-skills/writing-agent/web-article-extractor",
            "workflow-producer": "hermes-local-lab/custom-skills/writing-agent/workflow-producer",
        }
        for skill_id, source in skill_sources.items():
            self._write(
                f"{source}/SKILL.md",
                "\n".join(
                    (
                        "---",
                        f"name: {skill_id}",
                        "description: Use this skill in the Hermes Writing Agent workflow.",
                        "version: 0.6.0-hermes.1",
                        "author: upstream, ported for Hermes Local Lab",
                        "license: MIT",
                        "metadata:",
                        "  hermes:",
                        "    source_repo: https://example.invalid/upstream/skill",
                        "    source_commit: 0123456789abcdef",
                        "---",
                        "",
                        f"# {skill_id} for Hermes",
                        "",
                        "Use the Hermes runtime tools documented by this skill.",
                        "",
                    )
                ),
            )
            self._write(
                f"{source}/scripts/runtime.js",
                "const tool = 'hermes:delegate_task';\nconst version = '0.6.0-hermes.1';\n",
            )
            self._write(
                f"{source}/scripts/runtime.json",
                json.dumps(
                    {
                        "tool": "hermes:delegate_task",
                        "version": "0.6.0-hermes.1",
                        "env": "HERMES_RUNTIME_HOME",
                    },
                    sort_keys=True,
                )
                + "\n",
            )
            self._write(f"{source}/tests/leak.js")
            self._write(f"{source}/docs/leak.md")
            self._write(f"{source}/scripts/old.js.backup")
        self._write("hermes-local-lab/custom-skills/writing-agent/not-allowlisted/SKILL.md", "name: no\n")

    def _run_stage(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(STAGER),
                "--repo-root",
                str(self.repo),
                "--install-root",
                str(self.install_root),
                "--node-root",
                str(self.node_root),
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_stage_copies_node_docx_skills_and_public_key(self) -> None:
        completed = self._run_stage()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            (self.install_root / "runtime/node/NODE_VERSION").read_text(encoding="utf-8"),
            NODE_VERSION + "\n",
        )
        self.assertTrue((self.install_root / "runtime/docx-engine-v2/src/cli/list-templates.js").is_file())
        self.assertTrue((self.install_root / "runtime/docx-engine-v2/node_modules/runtime-dep/index.js").is_file())
        self.assertTrue((self.install_root / "runtime/docx-engine-v2/templates/general-proposal/template.docx").is_file())
        self.assertEqual(
            (self.install_root / "resources/license/signing-public.pem").read_bytes(),
            PUBLIC_KEY.read_bytes(),
        )
        manifest_text = (self.install_root / "runtime/agent/skills/product-skills.json").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(manifest_text, r"(?i)hermes")
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["schema_version"], "taiji-product-skills/v1")
        self.assertEqual({item["id"] for item in manifest["skills"]}, SKILL_IDS)

    def test_packaged_node_is_an_explicit_byte_preserving_runtime_allowlist(self) -> None:
        source_node = self.node_root / "bin/node"

        completed = self._run_stage()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        staged = self.install_root / "runtime/node"
        self.assertEqual((staged / "bin/node").read_bytes(), source_node.read_bytes())
        self.assertEqual(
            {path.relative_to(staged).as_posix() for path in staged.rglob("*")},
            {
                "LICENSE",
                "NODE_ARCHIVE_SHA256",
                "NODE_VERSION",
                "bin",
                "bin/node",
            },
        )

    def test_skill_productization_only_changes_user_visible_skill_markdown(self) -> None:
        source = self.repo / "hermes-local-lab/custom-skills/writing-agent/style-modeler"
        source_js = (source / "scripts/runtime.js").read_bytes()
        source_json = (source / "scripts/runtime.json").read_bytes()
        source_hash = tree_sha256(source)

        completed = self._run_stage()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        staged = self.install_root / "runtime/agent/skills/writing/style-modeler"
        self.assertEqual((staged / "scripts/runtime.js").read_bytes(), source_js)
        self.assertEqual((staged / "scripts/runtime.json").read_bytes(), source_json)
        self.assertIn(b"hermes:delegate_task", (staged / "scripts/runtime.js").read_bytes())
        self.assertIn(b"0.6.0-hermes.1", (staged / "scripts/runtime.json").read_bytes())
        self.assertIn(b"HERMES_RUNTIME_HOME", (staged / "scripts/runtime.json").read_bytes())

        skill_text = (staged / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill_text.startswith("---\n"))
        self.assertGreaterEqual(skill_text.count("\n---\n"), 1)
        self.assertIn("name: style-modeler", skill_text)
        self.assertNotRegex(skill_text, r"(?i)hermes")

        manifest = json.loads(
            (self.install_root / "runtime/agent/skills/product-skills.json").read_text(encoding="utf-8")
        )
        entry = next(item for item in manifest["skills"] if item["id"] == "style-modeler")
        self.assertEqual(entry["source_sha256"], source_hash)
        self.assertRegex(entry["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(entry["source_repository"], "https://example.invalid/upstream/skill")
        self.assertEqual(entry["source_revision"], "0123456789abcdef")
        self.assertEqual(entry["productization"], "skill-md-visible-branding-v1")
        self.assertEqual(entry["sha256"], tree_sha256(staged))
        self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_stage_fixes_trust_anchor_directory_modes_under_permissive_umask(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                str(STAGER),
                "--repo-root",
                str(self.repo),
                "--install-root",
                str(self.install_root),
                "--node-root",
                str(self.node_root),
            ],
            text=True,
            capture_output=True,
            check=False,
            umask=0o002,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(stat.S_IMODE(self.install_root.stat().st_mode), 0o755)
        self.assertEqual(
            stat.S_IMODE((self.install_root / "resources").stat().st_mode),
            0o755,
        )
        self.assertEqual(
            stat.S_IMODE((self.install_root / "resources/license").stat().st_mode),
            0o755,
        )

    def test_stage_excludes_tests_docs_caches_backups_and_unlisted_skills(self) -> None:
        completed = self._run_stage()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        staged_paths = {
            path.relative_to(self.install_root).as_posix()
            for path in self.install_root.rglob("*")
        }
        forbidden_segments = {
            "tests",
            "test",
            "__tests__",
            "docs",
            ".git",
            ".cache",
            ".nyc_output",
            "__pycache__",
        }
        for path in staged_paths:
            self.assertTrue(forbidden_segments.isdisjoint(Path(path).parts), path)
            self.assertFalse(path.endswith(".backup"), path)
            self.assertNotRegex(path, r"\.(?:test|spec)\.[cm]?[jt]sx?$")
        self.assertFalse(any("not-allowlisted" in path for path in staged_paths))

    def test_missing_node_root_never_falls_back_to_system_path(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                str(STAGER),
                "--repo-root",
                str(self.repo),
                "--install-root",
                str(self.install_root),
            ],
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--node-root", completed.stderr)

    def test_wrong_public_key_fingerprint_fails_closed(self) -> None:
        key = self.repo / "tools/taiji-license-issuer/private/signing-public.pem"
        key.write_text(key.read_text(encoding="utf-8").replace("MIIB", "MIIC", 1), encoding="utf-8")
        completed = self._run_stage()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("public key fingerprint", completed.stderr)

    def test_skill_exclude_rejects_an_intermediate_symlink(self) -> None:
        source = self.repo / "hermes-local-lab/custom-skills/writing-agent/style-modeler"
        self._write(
            "hermes-local-lab/custom-skills/writing-agent/style-modeler/assets/keep.js",
            "module.exports = true;\n",
        )
        os.symlink("../assets", source / "scripts/alias")
        allowlist_path = self.repo / "packaging/linux/product-skills.allowlist.json"
        allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
        entry = next(item for item in allowlist["skills"] if item["id"] == "style-modeler")
        entry["exclude"] = ["scripts/alias/keep.js"]
        allowlist_path.write_text(json.dumps(allowlist), encoding="utf-8")

        completed = self._run_stage()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("symlinked path component", completed.stderr)

    def test_builtin_template_seed_is_read_only_and_has_no_user_entries(self) -> None:
        completed = self._run_stage()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        engine = self.install_root / "runtime/docx-engine-v2"
        registry = engine / "template-registry.json"
        self.assertEqual(stat.S_IMODE(registry.stat().st_mode), 0o444)
        self.assertEqual(json.loads(registry.read_text(encoding="utf-8"))["installed"], [])
        for path in (engine / "templates").rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o222, 0, path)

    def test_allowlist_is_explicit_and_contains_no_glob_sources(self) -> None:
        self.assertTrue(ALLOWLIST.is_file(), ALLOWLIST)
        allowlist = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
        self.assertEqual(allowlist["schema_version"], "taiji-product-skills-allowlist/v1")
        self.assertEqual({item["id"] for item in allowlist["skills"]}, SKILL_IDS)
        for item in allowlist["skills"]:
            self.assertNotIn("*", item["source"])
            self.assertNotIn("..", Path(item["source"]).parts)

    def test_build_scripts_require_verified_packaged_node_root_and_stage_helper(self) -> None:
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        offline = (ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh").read_text(encoding="utf-8")
        self.assertIn("stage-runtime-components.py", build)
        self.assertIn("TAIJI_PACKAGED_NODE_ROOT", build)
        self.assertIn('NODE_VERSION="22.23.1"', offline)
        self.assertIn(NODE_ARCHIVE_SHA256, offline)
        self.assertIn('TAIJI_PACKAGED_NODE_ROOT="$NODE_ROOT/current"', offline)
        self.assertNotIn('release_dir="latest-v${NODE_MAJOR}.x"', offline)

    def test_public_key_fingerprint_contract_is_exact(self) -> None:
        self.assertEqual(len(PUBLIC_KEY_FINGERPRINT), 64)
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        self.assertIn(PUBLIC_KEY_FINGERPRINT, build)
        self.assertNotIn("signing-private.pem", build)


if __name__ == "__main__":
    unittest.main()
