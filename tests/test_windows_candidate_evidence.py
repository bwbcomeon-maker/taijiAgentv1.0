"""Static contracts for Windows candidate evidence publication."""

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from packaging.pipeline.core.models import new_run_state, validate_v2_state
from tests import windows_pipeline_fixtures


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "packaging/windows/candidate_evidence.py"


def read_module():
    if not MODULE_PATH.is_file():
        raise AssertionError("missing candidate evidence helper: {}".format(MODULE_PATH))
    data = MODULE_PATH.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("candidate evidence helper contains a UTF-8 BOM")
    try:
        return data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise AssertionError("candidate evidence helper is not strict UTF-8: {}".format(exc))


def load_module():
    if not MODULE_PATH.is_file():
        raise AssertionError("missing candidate evidence helper: {}".format(MODULE_PATH))
    spec = importlib.util.spec_from_file_location("candidate_evidence", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load candidate evidence helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsCandidateEvidenceContractTests(unittest.TestCase):
    def test_candidate_evidence_module_exists_with_fixed_api(self):
        module = load_module()
        for name in (
            "build_evidence_payload",
            "render_handoff",
            "publish_evidence_bundle",
            "main",
        ):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(module, name, None)))

    def test_candidate_evidence_module_is_self_contained_and_windows_only(self):
        text = read_module()
        for forbidden in (
            "kylin",
            "common core",
            "common_core",
            "kylin-amd64",
        ):
            self.assertNotIn(forbidden, text.lower())
        for literal in (
            "windows-x64",
            "CANDIDATE_BUILT",
            "CURRENT_VERIFIED",
            "NOT_VERIFIED",
            "NOT_COMPLETED",
            "NOT_EXECUTED",
            "windows-candidate-evidence.json",
            "windows-candidate-handoff.md",
            "EVIDENCE_READY",
            "LOCAL_OUTPUT_OCCUPIED",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)

    def test_candidate_evidence_module_enforces_atomic_publication_contract(self):
        text = read_module()
        for literal in (
            "0700",
            "0600",
            "fsync",
            "os.rename",
            "symlink",
            "hardlink",
            "/usr/bin/python3",
            "-I",
            "-B",
            "--help",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)

    def test_candidate_evidence_helper_has_exact_entry_points(self):
        text = read_module()
        functions = re.findall(r"^def ([a-z_][a-z0-9_]*)\(", text, flags=re.M)
        for required in (
            "build_evidence_payload",
            "render_handoff",
            "publish_evidence_bundle",
            "main",
        ):
            self.assertIn(required, functions)

    def _write_state_root(self, root, plan, review_dir, remote_log_path):
        state_root = Path(root) / "state"
        runs_dir = state_root / "runs"
        run_dir = runs_dir / plan["run_id"]
        for directory in (state_root, runs_dir, run_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        source_review_dir = Path(review_dir)
        mirrored_review_dir = run_dir / "review"
        shutil.copytree(source_review_dir, mirrored_review_dir, symlinks=True)
        for entry in mirrored_review_dir.iterdir():
            if not entry.is_symlink():
                entry.chmod(0o600)
        mirrored_review_dir.chmod(0o700)
        report_path = mirrored_review_dir / "构建报告.txt"
        report_path.write_text(
            "Windows candidate review PASS\nrun={}\n".format(plan["run_id"]),
            encoding="utf-8",
        )
        report_path.chmod(0o600)
        marker_path = mirrored_review_dir / ".build-success"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        report_bytes = report_path.read_bytes()
        marker["report_bytes"] = len(report_bytes)
        marker["report_sha256"] = windows_pipeline_fixtures.sha256_bytes(report_bytes)
        marker_path.write_text(
            json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        marker_path.chmod(0o600)
        mirrored_remote_log = run_dir / "remote-build.log"
        remote_lines = (
            "remote build started",
            "source-session-identity PASS exit=0",
            "offline-npm-ci PASS exit=0",
            "electron-win32-x64 PASS exit=0",
            "payload-import-menu-policy PASS exit=0",
            "payload-hygiene-closure PASS exit=0",
            "inno-compile PASS exit=0",
            "installer-pe-version-authenticode PASS exit=0",
            "SUMMARY PASS checks=7",
        )
        mirrored_remote_log.write_text("\n".join(remote_lines) + "\n", encoding="utf-8")
        mirrored_remote_log.chmod(0o600)
        artifact_path = mirrored_review_dir / ("TaijiAgent-Setup-{}-win-x64.exe".format(plan["version"]))
        if "controller_bootstrap" not in plan:
            plan["controller_bootstrap"] = {
                "safe_tar": {
                    "source_path": str(MODULE_PATH),
                    "remote_path": "safe-tar.exe",
                    "bytes": 1,
                    "sha256": "b" * 64,
                    "python_path": plan["target_config"]["python"],
                }
            }

        class StateAdapter:
            not_built_label = "Windows adapter 已实现，真实 Windows 未验证，候选 EXE 未构建"

            @staticmethod
            def initial_state_patch(frozen_plan, online):
                del online
                return {
                    "identity": {
                        "asset_provenance_sha256": frozen_plan["asset_provenance_sha256"],
                        "cache_requirements_sha256": frozen_plan["cache_requirements_sha256"],
                        "cache_observation_sha256": frozen_plan["cache_observation_sha256"],
                    },
                    "policy": None,
                }

        state = new_run_state(
            plan,
            {"host_facts_sha256": plan["host_facts_sha256"]},
            StateAdapter(),
        )
        state.update({
            "stage": "CANDIDATE_BUILT",
            "status_label": "Windows candidate built; installation and GUI remain unverified",
            "artifact": {
                "kind": "exe",
                "basename": artifact_path.name,
                "bytes": artifact_path.stat().st_size,
                "sha256": windows_pipeline_fixtures.sha256_bytes(artifact_path.read_bytes()),
                "path": str(artifact_path.resolve()),
                "relative_path": artifact_path.name,
            },
            "remote_build_succeeded": True,
            "fetch_allowed": False,
        })
        validate_v2_state(state)
        state_path = run_dir / "run-state.json"
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        state_path.chmod(0o600)
        return state_root, run_dir, state

    def test_write_cli_publishes_exact_two_files_from_state_root_and_run(self):
        module = load_module()
        with tempfile.TemporaryDirectory(prefix="windows-evidence-write-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            review_dir, remote_log, _inspector = windows_pipeline_fixtures.make_windows_review(
                Path(temporary) / "run", plan
            )
            state_root, run_dir, _state = self._write_state_root(
                temporary, plan, review_dir, remote_log
            )
            result = module.main(
                ["write", "--state-root", str(state_root), "--run", plan["run_id"]]
            )
            self.assertEqual(result, 0)
            evidence_dir = run_dir / "evidence"
            self.assertTrue(evidence_dir.is_dir())
            self.assertEqual(
                sorted(item.name for item in evidence_dir.iterdir()),
                ["windows-candidate-evidence.json", "windows-candidate-handoff.md"],
            )
            self.assertEqual(stat.S_IMODE(evidence_dir.stat().st_mode), 0o700)
            for name in ("windows-candidate-evidence.json", "windows-candidate-handoff.md"):
                self.assertEqual(stat.S_IMODE((evidence_dir / name).stat().st_mode), 0o600)

    def test_publish_rejects_review_symlink_before_following_path(self):
        module = load_module()
        with tempfile.TemporaryDirectory(prefix="windows-evidence-symlink-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            review_dir, remote_log, _inspector = windows_pipeline_fixtures.make_windows_review(
                Path(temporary) / "run", plan
            )
            _state_root, run_dir, state = self._write_state_root(
                temporary, plan, review_dir, remote_log
            )
            artifact_path = Path(state["artifact"]["path"])
            target_path = run_dir / "symlink-target.bin"
            target_path.write_bytes(artifact_path.read_bytes())
            target_path.chmod(0o600)
            artifact_path.unlink()
            os.symlink(target_path, artifact_path)
            with self.assertRaises(module.EvidenceError) as context:
                module.publish_evidence_bundle(run_dir, state)
            self.assertEqual(context.exception.category, "LOCAL_REVIEW_INVALID")

    def test_publish_rejects_run_directory_symlink_before_resolution(self):
        module = load_module()
        with tempfile.TemporaryDirectory(prefix="windows-evidence-run-link-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            review_dir, remote_log, _inspector = windows_pipeline_fixtures.make_windows_review(
                Path(temporary) / "run", plan
            )
            _state_root, run_dir, state = self._write_state_root(
                temporary, plan, review_dir, remote_log
            )
            run_link = Path(temporary) / "run-link"
            os.symlink(run_dir, run_link)
            with self.assertRaises(module.EvidenceError) as context:
                module.publish_evidence_bundle(run_link, state)
            self.assertEqual(context.exception.category, "LOCAL_REVIEW_INVALID")

    def test_publish_rejects_private_mode_drift_and_review_junk(self):
        module = load_module()
        with tempfile.TemporaryDirectory(prefix="windows-evidence-mode-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            review_dir, remote_log, _inspector = windows_pipeline_fixtures.make_windows_review(
                Path(temporary) / "run", plan, corruption="extra-review-file"
            )
            _state_root, run_dir, state = self._write_state_root(
                temporary, plan, review_dir, remote_log
            )
            (run_dir / "review").chmod(0o755)
            with self.assertRaises(module.EvidenceError) as context:
                module.publish_evidence_bundle(run_dir, state)
            self.assertEqual(context.exception.category, "LOCAL_REVIEW_INVALID")

    def test_existing_exact_bundle_is_idempotent_but_conflict_is_occupied(self):
        module = load_module()
        with tempfile.TemporaryDirectory(prefix="windows-evidence-idempotent-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            review_dir, remote_log, _inspector = windows_pipeline_fixtures.make_windows_review(
                Path(temporary) / "run", plan
            )
            _state_root, run_dir, state = self._write_state_root(
                temporary, plan, review_dir, remote_log
            )
            first = module.publish_evidence_bundle(run_dir, state)
            second = module.publish_evidence_bundle(run_dir, state)
            self.assertEqual(first["status"], "EVIDENCE_READY")
            self.assertEqual(second["status"], "EVIDENCE_READY")
            handoff = Path(second["directory"]) / "windows-candidate-handoff.md"
            handoff.write_text("conflict\n", encoding="utf-8")
            handoff.chmod(0o600)
            with self.assertRaises(module.EvidenceError) as context:
                module.publish_evidence_bundle(run_dir, state)
            self.assertEqual(context.exception.category, "LOCAL_OUTPUT_OCCUPIED")

    def test_failed_publish_keeps_staging_directory_under_run(self):
        module = load_module()
        with tempfile.TemporaryDirectory(prefix="windows-evidence-staging-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            review_dir, remote_log, _inspector = windows_pipeline_fixtures.make_windows_review(
                Path(temporary) / "run", plan
            )
            _state_root, run_dir, state = self._write_state_root(
                temporary, plan, review_dir, remote_log
            )
            def competing_publish(source, destination):
                del source
                destination = Path(destination)
                destination.mkdir(mode=0o700)
                sentinel = destination / "competitor.txt"
                sentinel.write_text("competitor\n", encoding="utf-8")
                sentinel.chmod(0o600)
                raise OSError("boom")

            with mock.patch.object(module.os, "rename", side_effect=competing_publish):
                with self.assertRaises(Exception):
                    module.publish_evidence_bundle(run_dir, state)
            staging_dirs = [
                entry for entry in run_dir.iterdir()
                if entry.is_dir() and entry.name.startswith(".evidence-")
            ]
            self.assertTrue(staging_dirs)
            self.assertEqual((run_dir / "evidence" / "competitor.txt").read_text(), "competitor\n")

    def test_publish_rejects_incomplete_v2_state_before_claiming_current_verified(self):
        module = load_module()
        with tempfile.TemporaryDirectory(prefix="windows-evidence-state-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            review_dir, remote_log, _inspector = windows_pipeline_fixtures.make_windows_review(
                Path(temporary) / "run", plan
            )
            _state_root, run_dir, state = self._write_state_root(
                temporary, plan, review_dir, remote_log
            )
            state.pop("identity")
            with self.assertRaises(module.EvidenceError):
                module.publish_evidence_bundle(run_dir, state)
            self.assertFalse((run_dir / "evidence").exists())

    def test_publish_rejects_forged_remote_log_and_marker_fields(self):
        module = load_module()
        with tempfile.TemporaryDirectory(prefix="windows-evidence-forged-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            review_dir, remote_log, _inspector = windows_pipeline_fixtures.make_windows_review(
                Path(temporary) / "run", plan
            )
            _state_root, run_dir, state = self._write_state_root(
                temporary, plan, review_dir, remote_log
            )
            remote_log_path = run_dir / "remote-build.log"
            remote_log_path.write_text("SUMMARY PASS checks=7\n", encoding="utf-8")
            remote_log_path.chmod(0o600)
            with self.assertRaises(module.EvidenceError):
                module.publish_evidence_bundle(run_dir, state)

            remote_log_path.write_text(
                "remote build started\n"
                "source-session-identity PASS exit=0\n"
                "offline-npm-ci PASS exit=0\n"
                "electron-win32-x64 PASS exit=0\n"
                "payload-import-menu-policy PASS exit=0\n"
                "payload-hygiene-closure PASS exit=0\n"
                "inno-compile PASS exit=0\n"
                "installer-pe-version-authenticode PASS exit=0\n"
                "SUMMARY PASS checks=7\n",
                encoding="utf-8",
            )
            marker_path = run_dir / "review" / ".build-success"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["unexpected"] = "forged"
            marker_path.write_text(
                json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            marker_path.chmod(0o600)
            with self.assertRaises(module.EvidenceError):
                module.publish_evidence_bundle(run_dir, state)

    def test_render_handoff_explicitly_states_unverified_install_launch_gui_signing_and_release(self):
        module = load_module()
        with tempfile.TemporaryDirectory(prefix="windows-evidence-handoff-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            review_dir, remote_log, _inspector = windows_pipeline_fixtures.make_windows_review(
                Path(temporary) / "run", plan
            )
            _state_root, _run_dir, state = self._write_state_root(
                temporary, plan, review_dir, remote_log
            )
            handoff = module.render_handoff(state)
            for literal in (
                "未安装",
                "未启动",
                "未GUI验收",
                "未production授权",
                "未签名",
                "未发布",
            ):
                self.assertIn(literal, handoff)

    def test_candidate_evidence_help_runs_isolated_from_external_cwd(self):
        with tempfile.TemporaryDirectory(prefix="windows-evidence-external-cwd-") as temporary:
            result = subprocess.run(
                ["/usr/bin/python3", "-I", "-B", str(MODULE_PATH), "--help"],
                cwd=temporary,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertNotIn(temporary, result.stdout + result.stderr)
