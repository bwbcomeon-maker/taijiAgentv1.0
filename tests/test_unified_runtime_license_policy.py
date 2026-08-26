from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "hermes-local-lab/sources/hermes-webui"
HELPER = WEBUI / "tests/unified_license_test_fixture.js"
AGENT_DIR = ROOT / "hermes-local-lab/sources/hermes-agent"
AGENT_PYTHON = AGENT_DIR / "venv/bin/python"
TEST_SIGNER = ROOT / "tools/taiji-license-issuer/private/signing-private.pem"
REAL_RUNTIME_ROOTS = (
    ROOT / "apps/taiji-desktop/src",
    ROOT / "hermes-local-lab/scripts",
)
ELECTRON_SMOKES = tuple(sorted((WEBUI / "tests").glob("*electron*_smoke.js")))
REAL_RUNTIME_FILES = (
    WEBUI / "static/panels.js",
    WEBUI / "static/style.css",
    WEBUI / "api/onboarding.py",
    WEBUI / "api/product_diagnostics.py",
    *ELECTRON_SMOKES,
)
FORBIDDEN_LITERAL_SNIPPETS = (
    "开发环境无需授权",
    "开发源码模式无需授权",
    "not_required",
    'data-license-status="not_required"',
)
FORBIDDEN_DISABLE_PATTERNS = (
    re.compile(r"TAIJI_LICENSE_REQUIRED\s*[:=]\s*['\"]?0(?:['\"]|\b)"),
    re.compile(
        r"TAIJI_LICENSE_MACHINE_BINDING_REQUIRED\s*[:=]\s*['\"]?0(?:['\"]|\b)"
    ),
)
TEST_ACCOUNT_HOME_HOOK_ENV = "TAIJI_LICENSE_TEST_" + "ACCOUNT_HOME"


def _node_json(script: str, *, environ: dict[str, str] | None = None) -> dict:
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        env={**os.environ, **(environ or {})},
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return json.loads(result.stdout)


def _runtime_paths() -> list[Path]:
    paths = list(REAL_RUNTIME_FILES)
    for root in REAL_RUNTIME_ROOTS:
        paths.extend(
            path
            for path in sorted(root.rglob("*"))
            if path.suffix in {".js", ".py", ".sh", ".command"}
        )
    return paths


class UnifiedRuntimeLicensePolicyTests(unittest.TestCase):
    maxDiff = None

    def test_real_runtime_sources_do_not_disable_or_exempt_authorization(self):
        failures = []
        for path in _runtime_paths():
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(snippet in text for snippet in FORBIDDEN_LITERAL_SNIPPETS) or any(
                pattern.search(text) for pattern in FORBIDDEN_DISABLE_PATTERNS
            ):
                failures.append(
                    f"{path.relative_to(ROOT)} contains a forbidden runtime exemption"
                )
        self.assertEqual(failures, [])

    def test_account_home_hook_is_confined_to_the_shared_test_fixture(self):
        failures = []
        for path in _runtime_paths():
            text = path.read_text(encoding="utf-8", errors="ignore")
            if TEST_ACCOUNT_HOME_HOOK_ENV in text:
                failures.append(
                    f"{path.relative_to(ROOT)} knows the test-only account-home hook"
                )
        self.assertEqual(failures, [])

    def test_electron_smoke_inventory_is_discovered_by_glob(self):
        self.assertGreaterEqual(len(ELECTRON_SMOKES), 7)
        self.assertEqual(ELECTRON_SMOKES, tuple(sorted(ELECTRON_SMOKES)))
        self.assertTrue(
            all(path.match("*electron*_smoke.js") for path in ELECTRON_SMOKES)
        )

    def test_prepared_electron_smokes_cleanup_the_fixture(self):
        prepared_smokes = []
        failures = []
        for path in ELECTRON_SMOKES:
            text = path.read_text(encoding="utf-8")
            if "prepareUnifiedLicenseFixture" not in text:
                continue
            prepared_smokes.append(path)
            if (
                "cleanupUnifiedLicenseFixture" not in text
                or "cleanupUnifiedLicenseFixture({ runtimeEnv" not in text
            ):
                failures.append(str(path.relative_to(ROOT)))
        self.assertGreaterEqual(len(prepared_smokes), 7)
        self.assertEqual(failures, [])

    def test_prepared_electron_smokes_guard_launch_with_cleanup_finally(self):
        prepared_smokes = []
        failures = []
        for path in ELECTRON_SMOKES:
            text = path.read_text(encoding="utf-8")
            main_start = text.find("async function main()")
            prepare_at = text.find("prepareUnifiedLicenseFixture", main_start)
            if prepare_at < 0:
                continue
            prepared_smokes.append(path)
            launch_candidates = [
                position
                for position in (
                    text.find("await _electron.launch", prepare_at),
                    text.find("await launchDesktop", prepare_at),
                )
                if position >= 0
            ]
            launch_at = min(launch_candidates, default=-1)
            cleanup_at = text.find("cleanupUnifiedLicenseFixture({ runtimeEnv", launch_at)
            try_at = text.rfind("try {", main_start, launch_at)
            finally_at = text.rfind("} finally {", launch_at, cleanup_at)
            app_declaration = max(
                text.rfind("let app = null", main_start, try_at),
                text.rfind("let app;", main_start, try_at),
            )
            if not (
                main_start >= 0
                and prepare_at >= 0
                and app_declaration >= main_start
                and try_at > app_declaration
                and try_at < launch_at < finally_at < cleanup_at
            ):
                failures.append(str(path.relative_to(ROOT)))
        self.assertGreaterEqual(len(prepared_smokes), 7)
        self.assertEqual(failures, [])

    def test_runtime_environment_sanitization_is_case_insensitive(self):
        script = textwrap.dedent(
            f"""
            const helper = require({json.dumps(str(HELPER))});
            const sanitized = helper.sanitizeUnifiedLicenseRuntimeEnv({{
              safe_value: "kept",
              taiji_license_required: "0",
              TaIjI_LiCeNsE_pUbLiC_kEy: "attacker",
              taiji_license_test_private_key_file: "/secret/key",
              Taiji_License_Test_Account_Home: "/secret/profile",
              pythonhome: "/secret/python",
              PyThOnPaTh: "/secret/modules",
            }});
            console.log(JSON.stringify(sanitized));
            """
        )
        self.assertEqual(_node_json(script), {"safe_value": "kept"})

    def test_signer_file_error_is_stable_and_does_not_disclose_path(self):
        with tempfile.TemporaryDirectory() as td:
            secret_path = str(Path(td) / "do-not-disclose-signing-key.pem")
            script = textwrap.dedent(
                f"""
                const helper = require({json.dumps(str(HELPER))});
                let message = "NO_ERROR";
                try {{
                  helper.loadUnifiedLicenseTestSigner({{
                    repoRoot: {json.dumps(str(ROOT))},
                    agentDir: {json.dumps(str(AGENT_DIR))},
                    pythonBin: {json.dumps(str(AGENT_PYTHON))},
                    environ: {{ TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE: {json.dumps(secret_path)} }},
                  }});
                }} catch (error) {{
                  message = String(error && error.message || error);
                }}
                console.log(JSON.stringify({{ message }}));
                """
            )
            message = _node_json(script)["message"]
            self.assertEqual(
                message,
                "The unified license test signer is unavailable or unsafe",
            )
            self.assertNotIn(secret_path, message)

    @unittest.skipUnless(TEST_SIGNER.is_file(), "repository test signer is unavailable")
    def test_cleanup_is_idempotent_after_success_and_simulated_failure(self):
        script = textwrap.dedent(
            f"""
            const fs = require("node:fs");
            const os = require("node:os");
            const path = require("node:path");
            const helper = require({json.dumps(str(HELPER))});
            const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-license-cleanup-test-"));
            function prepare(name) {{
              const runtimeEnv = helper.sanitizeUnifiedLicenseRuntimeEnv({{
                ...process.env,
                TAIJI_RUNTIME_HOME: path.join(root, name),
              }});
              helper.prepareUnifiedLicenseFixture({{
                repoRoot: {json.dumps(str(ROOT))},
                agentDir: {json.dumps(str(AGENT_DIR))},
                pythonBin: {json.dumps(str(AGENT_PYTHON))},
                runtimeEnv,
              }});
              return {{ runtimeEnv, profile: path.dirname(runtimeEnv.TAIJI_AGENT_PYTHON) }};
            }}
            const success = prepare("success");
            helper.cleanupUnifiedLicenseFixture({{ runtimeEnv: success.runtimeEnv }});
            helper.cleanupUnifiedLicenseFixture({{ runtimeEnv: success.runtimeEnv }});
            let failure;
            try {{
              failure = prepare("failure");
              throw new Error("simulated smoke failure");
            }} catch (_) {{
              // The smoke's finally block owns cleanup.
            }} finally {{
              if (failure) helper.cleanupUnifiedLicenseFixture({{ runtimeEnv: failure.runtimeEnv }});
            }}
            const result = {{
              success_removed: !fs.existsSync(success.profile),
              failure_removed: Boolean(failure) && !fs.existsSync(failure.profile),
            }};
            fs.rmSync(root, {{ recursive: true, force: true }});
            console.log(JSON.stringify(result));
            """
        )
        self.assertEqual(
            _node_json(
                script,
                environ={"TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE": str(TEST_SIGNER)},
            ),
            {"success_removed": True, "failure_removed": True},
        )

    @unittest.skipUnless(TEST_SIGNER.is_file(), "repository test signer is unavailable")
    def test_cleanup_failure_is_stable_path_free_and_retriable(self):
        script = textwrap.dedent(
            f"""
            const fs = require("node:fs");
            const os = require("node:os");
            const path = require("node:path");
            const helper = require({json.dumps(str(HELPER))});
            const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-license-cleanup-error-"));
            const runtimeEnv = helper.sanitizeUnifiedLicenseRuntimeEnv({{
              ...process.env,
              TAIJI_RUNTIME_HOME: root,
            }});
            helper.prepareUnifiedLicenseFixture({{
              repoRoot: {json.dumps(str(ROOT))},
              agentDir: {json.dumps(str(AGENT_DIR))},
              pythonBin: {json.dumps(str(AGENT_PYTHON))},
              runtimeEnv,
            }});
            const profile = path.dirname(runtimeEnv.TAIJI_AGENT_PYTHON);
            const originalRmSync = fs.rmSync;
            let message = "NO_ERROR";
            try {{
              fs.rmSync = target => {{
                if (path.resolve(target) === path.resolve(profile)) {{
                  throw new Error(`simulated removal failure at ${{profile}}`);
                }}
                return originalRmSync(target, {{ recursive: true, force: true }});
              }};
              helper.cleanupUnifiedLicenseFixture({{ runtimeEnv }});
            }} catch (error) {{
              message = String(error && error.message || error);
            }} finally {{
              fs.rmSync = originalRmSync;
            }}
            const retainedForRetry = fs.existsSync(profile);
            helper.cleanupUnifiedLicenseFixture({{ runtimeEnv }});
            const removedAfterRetry = !fs.existsSync(profile);
            fs.rmSync(root, {{ recursive: true, force: true }});
            console.log(JSON.stringify({{
              message,
              message_path_free: !message.includes(profile),
              retained_for_retry: retainedForRetry,
              removed_after_retry: removedAfterRetry,
            }}));
            """
        )
        self.assertEqual(
            _node_json(
                script,
                environ={"TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE": str(TEST_SIGNER)},
            ),
            {
                "message": "The unified license fixture profile could not be removed",
                "message_path_free": True,
                "retained_for_retry": True,
                "removed_after_retry": True,
            },
        )

    @unittest.skipUnless(TEST_SIGNER.is_file(), "repository test signer is unavailable")
    def test_process_exit_fallback_removes_unclosed_profile(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_home = Path(td) / "runtime"
            script = textwrap.dedent(
                f"""
                const helper = require({json.dumps(str(HELPER))});
                const runtimeEnv = helper.sanitizeUnifiedLicenseRuntimeEnv({{
                  ...process.env,
                  TAIJI_RUNTIME_HOME: {json.dumps(str(runtime_home))},
                }});
                helper.prepareUnifiedLicenseFixture({{
                  repoRoot: {json.dumps(str(ROOT))},
                  agentDir: {json.dumps(str(AGENT_DIR))},
                  pythonBin: {json.dumps(str(AGENT_PYTHON))},
                  runtimeEnv,
                }});
                """
            )
            subprocess.run(
                ["node", "-e", script],
                cwd=ROOT,
                env={
                    **os.environ,
                    "TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE": str(TEST_SIGNER),
                },
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(list(runtime_home.glob("license-account-*")), [])

    @unittest.skipUnless(TEST_SIGNER.is_file(), "repository test signer is unavailable")
    def test_wrapper_reinjects_the_account_profile_across_two_python_layers(self):
        script = textwrap.dedent(
            f"""
            const fs = require("node:fs");
            const os = require("node:os");
            const path = require("node:path");
            const {{ spawnSync }} = require("node:child_process");
            const helper = require({json.dumps(str(HELPER))});
            const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-license-two-layer-"));
            const runtimeEnv = helper.sanitizeUnifiedLicenseRuntimeEnv({{
              ...process.env,
              taiji_license_required: "0",
              TaIjI_LiCeNsE_tEsT_pRiVaTe_KeY_fIlE: "/not/in/product/env",
              PyThOnPaTh: "/not/in/product/env",
              TAIJI_RUNTIME_HOME: root,
            }});
            const fixture = helper.prepareUnifiedLicenseFixture({{
              repoRoot: {json.dumps(str(ROOT))},
              agentDir: {json.dumps(str(AGENT_DIR))},
              pythonBin: {json.dumps(str(AGENT_PYTHON))},
              runtimeEnv,
            }});
            const wrapper = runtimeEnv.TAIJI_AGENT_PYTHON;
            const profile = path.dirname(wrapper);
            const inner = [
              "import json, os, taiji_license",
              "status = taiji_license.load_license_status()",
              "request = taiji_license.build_machine_request()",
              "print(json.dumps(dict(",
              "  valid=status.status == 'valid',",
              "  schema=request.get('schema_version'),",
              "  quality=request.get('fingerprint_quality'),",
              "  risk_count=len(request.get('risk_flags') or []),",
              "  hook_popped='TAIJI_LICENSE_TEST_ACCOUNT_HOME' not in os.environ,",
              ")))",
            ].join("\\n");
            const outer = [
              "import json, os, subprocess, sys, taiji_license",
              "status = taiji_license.load_license_status()",
              "request = taiji_license.build_machine_request()",
              "child_env = dict(os.environ)",
              "child_env.pop('PYTHONHOME', None)",
              "child_env.pop('PYTHONPATH', None)",
              "child = subprocess.run([os.environ['TAIJI_AGENT_PYTHON'], '-c', sys.argv[1]], env=child_env, text=True, capture_output=True)",
              "print(json.dumps(dict(",
              "  first=dict(",
              "    valid=status.status == 'valid',",
              "    schema=request.get('schema_version'),",
              "    quality=request.get('fingerprint_quality'),",
              "    risk_count=len(request.get('risk_flags') or []),",
              "    hook_popped='TAIJI_LICENSE_TEST_ACCOUNT_HOME' not in os.environ,",
              "  ),",
              "  second=json.loads(child.stdout) if child.returncode == 0 else dict(),",
              "  child_exit=child.returncode,",
              ")))",
            ].join("\\n");
            const completed = spawnSync(wrapper, ["-c", outer, inner], {{
              cwd: {json.dumps(str(AGENT_DIR))},
              env: runtimeEnv,
              encoding: "utf8",
            }});
            const badEnvKeys = Object.keys(runtimeEnv).filter(name => [
              "TAIJI_LICENSE_REQUIRED",
              "TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE",
              "TAIJI_LICENSE_TEST_ACCOUNT_HOME",
              "PYTHONHOME",
              "PYTHONPATH",
            ].includes(name.toUpperCase()));
            const layers = completed.status === 0 ? JSON.parse(completed.stdout) : {{}};
            helper.cleanupUnifiedLicenseFixture({{ runtimeEnv }});
            const result = {{
              fixture,
              layers,
              wrapper_exit: completed.status,
              bad_env_keys: badEnvKeys,
              profile_removed: !fs.existsSync(profile),
            }};
            fs.rmSync(root, {{ recursive: true, force: true }});
            console.log(JSON.stringify(result));
            """
        )
        result = _node_json(
            script,
            environ={"TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE": str(TEST_SIGNER)},
        )
        expected_probe = {
            "valid": True,
            "schema": 3,
            "quality": "strong",
            "risk_count": 0,
            "hook_popped": True,
        }
        self.assertEqual(result["wrapper_exit"], 0)
        self.assertEqual(result["bad_env_keys"], [])
        self.assertEqual(result["layers"]["child_exit"], 0)
        self.assertEqual(result["layers"]["first"], expected_probe)
        self.assertEqual(result["layers"]["second"], expected_probe)
        self.assertEqual(result["fixture"]["binding_type"], "machine_fingerprint_v3")
        self.assertEqual(result["fixture"]["fingerprint_quality"], "strong")
        self.assertEqual(result["fixture"]["risk_flags"], [])
        self.assertTrue(result["profile_removed"])

    def test_worktree_diagnostics_use_the_prepared_python_wrapper(self):
        smoke = (WEBUI / "tests/worktree_public_contract_electron_smoke.js").read_text(
            encoding="utf-8"
        )
        start = smoke.index("function collectStateDbMessages(")
        end = smoke.index("\n}\n", start) + 2
        collector = smoke[start:end]
        self.assertIn("runtimeEnv", collector)
        self.assertIn("env: runtimeEnv", collector)
        self.assertIn("pythonBin: runtimeEnv.TAIJI_AGENT_PYTHON", smoke)


if __name__ == "__main__":
    unittest.main()
