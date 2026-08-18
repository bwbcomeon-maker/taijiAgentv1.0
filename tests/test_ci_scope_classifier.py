import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "classify-ci-scope.py"


def load_classifier():
    spec = importlib.util.spec_from_file_location("classify_ci_scope", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CiScopeClassifierTest(unittest.TestCase):
    def test_docs_only_uses_fast_lane(self):
        result = load_classifier().classify_paths(["README.md", "docs/ci.md"])
        self.assertEqual("docs", result["risk"])
        self.assertTrue(result["docs_only"])
        self.assertFalse(any(result[key] for key in result if key.startswith("run_")))

    def test_module_change_runs_root_and_affected_suite(self):
        result = load_classifier().classify_paths(
            ["apps/taiji-desktop/src/main.js"]
        )
        self.assertEqual("normal", result["risk"])
        self.assertTrue(result["run_root"])
        self.assertTrue(result["run_desktop"])
        self.assertFalse(result["run_agent"])
        self.assertFalse(result["run_webui"])

    def test_high_risk_path_runs_every_suite(self):
        result = load_classifier().classify_paths(
            ["hermes-local-lab/sources/hermes-agent/agent/provider_credentials.py"]
        )
        self.assertEqual("high", result["risk"])
        self.assertFalse(result["docs_only"])
        for key in (
            "run_root",
            "run_desktop",
            "run_docx",
            "run_agent",
            "run_webui",
        ):
            self.assertTrue(result[key], key)

    def test_linux_packaging_contract_paths_select_linux_packaging_job(self):
        for path in (
            "packaging/linux/compatibility-policy.json",
            "packaging/linux/deb/preinst",
            "packaging/linux/deb/publish-single-deb.sh",
            "packaging/linux/acceptance_runner.py",
            "packaging/linux/bin/taiji-agent-acceptance",
            "scripts/produce-taiji-github-ci-evidence.py",
            "scripts/produce-taiji-negative-boundary-evidence.py",
            "scripts/produce-taiji-offline-rehearsal.py",
            "scripts/validate-taiji-release-evidence.py",
            "taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh",
        ):
            with self.subTest(path=path):
                result = load_classifier().classify_paths([path])
                self.assertEqual("high", result["risk"])
                self.assertTrue(result["run_linux_packaging"])

    def test_unrelated_high_risk_path_keeps_linux_packaging_job_unselected(self):
        result = load_classifier().classify_paths(
            ["hermes-local-lab/sources/hermes-agent/provider_credentials.py"]
        )
        self.assertEqual("high", result["risk"])
        self.assertFalse(result["run_linux_packaging"])

    def test_lockfile_and_workflow_changes_are_high_risk(self):
        for path in (
            ".github/workflows/ci.yml",
            "hermes-local-lab/sources/hermes-agent/uv.lock",
            "hermes-local-lab/sources/hermes-webui/package-lock.json",
        ):
            with self.subTest(path=path):
                self.assertEqual("high", load_classifier().classify_paths([path])["risk"])

    def test_unknown_non_docs_path_falls_back_to_root_suite(self):
        result = load_classifier().classify_paths(["new-area/example.txt"])
        self.assertEqual("normal", result["risk"])
        self.assertTrue(result["run_root"])

    def test_full_ci_label_upgrades_docs_change(self):
        result = load_classifier().classify_paths(
            ["README.md"], labels=["full-ci"]
        )
        self.assertEqual("high", result["risk"])
        self.assertTrue(result["run_agent"])

    def test_cli_writes_github_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "github-output"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--path",
                    "hermes-local-lab/sources/docx-engine-v2/src/render.js",
                    "--github-output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual("normal", payload["risk"])
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual("true", values["run_docx"])
            self.assertEqual("true", values["run_root"])

    def test_workflow_gate_requires_every_selected_job(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        for suite in ("ROOT", "DESKTOP", "DOCX", "AGENT", "WEBUI", "LINUX_PACKAGING"):
            self.assertIn(f"RUN_{suite}:", workflow)
        self.assertIn("linux_packaging:", workflow)
        self.assertIn("test_linux_compatibility_policy", workflow)
        self.assertIn('selected and result != "success"', workflow)
        self.assertGreaterEqual(workflow.count("UV_PROJECT_ENVIRONMENT"), 2)
        action_refs = re.findall(r"uses: [^@\s]+@([0-9a-f]+)", workflow)
        self.assertTrue(action_refs)
        self.assertTrue(all(len(ref) == 40 for ref in action_refs))

    def test_root_contracts_use_uv_managed_python_fixture(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        root_job = workflow[workflow.index("  root:") : workflow.index("  desktop:")]
        self.assertIn("UV_PYTHON_PREFERENCE: only-managed", root_job)
        prelude, body = root_job.split("    steps:", 1)
        self.assertNotIn(
            "UV_PROJECT_ENVIRONMENT: ${{ runner.temp }}/taiji-root-venv",
            prelude,
            "runner.temp venv must not be declared in root job prelude",
        )
        self.assertEqual(
            2,
            body.count("UV_PROJECT_ENVIRONMENT: ${{ runner.temp }}/taiji-root-venv"),
            "runner.temp venv must be declared exactly twice in root job steps",
        )
        self.assertIn(
            "      - name: Prepare the canonical Agent venv required by root contracts\n"
            "        env:\n"
            "          UV_PROJECT_ENVIRONMENT: ${{ runner.temp }}/taiji-root-venv",
            root_job,
        )
        self.assertIn(
            "Run Taiji root contracts with uv-managed Python",
            root_job,
        )
        self.assertIn(
            "      - name: Run Taiji root contracts with uv-managed Python\n"
            "        env:\n"
            "          UV_PROJECT_ENVIRONMENT: ${{ runner.temp }}/taiji-root-venv",
            root_job,
        )
        self.assertIn("uv python install 3.11", root_job)
        self.assertIn(
            '"$UV_PROJECT_ENVIRONMENT/bin/python" -m unittest',
            root_job,
        )
        self.assertNotIn(
            "hermes-local-lab/sources/hermes-agent/venv/bin/python",
            root_job,
        )

    def test_linux_packaging_job_executes_the_real_python38_compatibility_gate(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        linux_job = workflow[
            workflow.index("  linux_packaging:") : workflow.index("  root:")
        ]
        self.assertIn("python-version: '3.8'", linux_job)
        self.assertIn(
            "python tests/python38_linux_packaging_gate.py",
            linux_job,
        )
        for contract in (
            "tests.test_acceptance_tools_integrity",
            "tests.test_installed_acceptance_trust_anchor",
            "tests.test_github_ci_evidence_producer",
            "tests.test_negative_boundary_evidence_producer",
            "tests.test_offline_rehearsal_producer",
            "tests.test_environment_evidence_v2_contract",
            "tests.test_target_evidence_v2_contract",
            "tests.test_release_evidence_schema_v3",
            "tests.test_strict_build_toolchain_contract",
        ):
            self.assertIn(contract, linux_job)
        self.assertIn(
            "packaging/linux/bin/taiji-agent-acceptance",
            linux_job,
        )
        self.assertIn(
            "node --test tools/taiji-desktop-acceptance/run-installed-electron-acceptance.test.js",
            linux_job,
        )
        self.assertIn(
            "python -B tools/taiji-desktop-acceptance/test_observe_single_deb_install.py",
            linux_job,
        )


if __name__ == "__main__":
    unittest.main()
