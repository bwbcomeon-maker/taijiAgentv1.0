"""Contract tests for the thin, resumable Linux golden-release planner."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "scripts/taiji-linux-golden-orchestrator.py"
CHALLENGE_HELPER = ROOT / "scripts/taiji-challenge-envelope.py"
PYTHON38_GATE = ROOT / "tests/python38_linux_packaging_gate.py"
TARGET_SESSION_PASSTHROUGH = [
    "DBUS_SESSION_BUS_ADDRESS",
    "DESKTOP_SESSION",
    "DISPLAY",
    "LANG",
    "LANGUAGE",
    "LC_ADDRESS",
    "LC_ALL",
    "LC_COLLATE",
    "LC_CTYPE",
    "LC_IDENTIFICATION",
    "LC_MEASUREMENT",
    "LC_MESSAGES",
    "LC_MONETARY",
    "LC_NAME",
    "LC_NUMERIC",
    "LC_PAPER",
    "LC_TELEPHONE",
    "LC_TIME",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_CURRENT_DESKTOP",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_DESKTOP",
    "XDG_SESSION_ID",
    "XDG_SESSION_TYPE",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LinuxGoldenOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-golden-orchestrator-")
        self.root = Path(self.temporary.name).resolve()
        source_module = self.load_orchestrator(ORCHESTRATOR)
        self.repo = self.root / "repo"
        self.repo.mkdir(mode=0o700)
        for relative in source_module.SOURCE_TRUST_PATHS:
            source = ROOT / relative
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        subprocess.run(
            ["/usr/bin/git", "init", "-b", "main"],
            cwd=self.repo,
            env=self.git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        subprocess.run(
            ["/usr/bin/git", "config", "user.name", "Taiji Golden Test"],
            cwd=self.repo,
            env=self.git_environment(),
            check=True,
        )
        subprocess.run(
            ["/usr/bin/git", "config", "user.email", "taiji-golden@example.invalid"],
            cwd=self.repo,
            env=self.git_environment(),
            check=True,
        )
        subprocess.run(
            ["/usr/bin/git", "add", "."],
            cwd=self.repo,
            env=self.git_environment(),
            check=True,
        )
        subprocess.run(
            ["/usr/bin/git", "commit", "-m", "golden orchestrator fixture"],
            cwd=self.repo,
            env=self.git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.source_commit = subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"],
            cwd=self.repo,
            env=self.git_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        self.orchestrator = self.repo / "scripts/taiji-linux-golden-orchestrator.py"
        self.challenge_helper = self.repo / "scripts/taiji-challenge-envelope.py"
        self.input_archive = self.root / f"taijiagent-制包机输入-{self.source_commit}.tar.gz"
        self.input_manifest = self.root / f"taijiagent-制包机输入-{self.source_commit}.manifest.json"
        self.input_checksum = self.root / f"{self.input_archive.name}.sha256"
        self.input_archive.write_bytes(b"frozen-builder-input")
        self.input_manifest.write_text(
            json.dumps(
                {
                    "schema": "taiji-builder-input-package/v1",
                    "source_commit": self.source_commit,
                    "archive_basename": self.input_archive.name,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.input_checksum.write_text(
            f"{sha256(self.input_archive)}  {self.input_archive.name}\n"
            f"{sha256(self.input_manifest)}  {self.input_manifest.name}\n",
            encoding="utf-8",
        )
        self.logs_dir = self.root / "logs"
        self.logs_dir.mkdir(mode=0o700)
        self.execution_home = self.root / "execution-home"
        self.execution_home.mkdir(mode=0o700)
        self.execution_tmp = self.root / "execution-tmp"
        self.execution_tmp.mkdir(mode=0o700)
        self.review_parent = self.root / "review"
        self.review_parent.mkdir(mode=0o700)
        self.review_root = self.review_parent / "taiji-agentv1.0"
        self.state = self.root / "orchestrator-state.json"
        self.config = self.root / "orchestrator-config.json"
        self.certification_envelope = self.root / "certification-challenge-envelope.json"
        self.publication_envelope = self.root / "publication-challenge-envelope.json"
        config = {
            "schema": "taiji-linux-golden-orchestrator-config/v5",
            "source_commit": self.source_commit,
            "repo_root": str(self.repo),
            "input": {
                "archive": str(self.input_archive),
                "manifest": str(self.input_manifest),
                "checksum": str(self.input_checksum),
            },
            "remote": {
                "host": "kylin",
                "root": "/home/kylin/taiji-builds",
                "account_home": "/home/kylin",
            },
            "workspace": {
                "review_root": str(self.review_root),
                "logs_dir": str(self.logs_dir),
                "execution_home": str(self.execution_home),
                "execution_tmp": str(self.execution_tmp),
            },
            "offline": {
                "image": "taiji-offline-rehearsal:local",
                "output_dir": str(self.root / "offline-evidence"),
                "previous_deb": str(self.root / "taiji-agent_1.0.0_amd64.deb"),
                "previous_signature": str(self.root / "taiji-agent_1.0.0_amd64.deb.sig"),
                "previous_manifest": str(self.root / "previous-manifest.json"),
            },
            "target": {
                "delivery_dir": "/home/kylin/taiji-acceptance-data",
                "customer_dir": "/home/kylin/taiji-customer-deb",
                "install_observation": "/home/kylin/taiji-install/single-deb-install-observation.json",
                "method_attestation": "/home/kylin/taiji-install/single-deb-install-method-attestation.json",
                "installer_screenshot": "/home/kylin/raw-installer-success.png",
                "category_id": "kylin-v10-sp1-x86_64",
                "operator_id": "operator-001",
                "environment_observation": "/home/kylin/taiji-install/environment-observation.json",
                "target_dir": "/home/kylin/taiji-target-verification",
                "timeout_ms": 900000,
            },
            "ci": {
                "run_id": 123456789,
            },
            "release": {
                "records_dir": str(self.root / "certification-records"),
                "certification_challenge_envelope": str(self.certification_envelope),
                "publication_challenge_envelope": str(self.publication_envelope),
                "private_key": str(self.root / "offline-release-private-key.pem"),
                "customer_output": str(self.root / "customer-output"),
                "receipt_root": str(self.root / "internal-receipt"),
            },
        }
        self.config.write_text(
            json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def git_environment() -> dict:
        return {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }

    @staticmethod
    def runtime_environment() -> dict:
        environment = os.environ.copy()
        environment.update(
            {
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return environment

    def command(self, *arguments: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-B", str(self.orchestrator), *arguments],
            cwd=self.repo,
            env=self.runtime_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if check and result.returncode != 0:
            self.fail(result.stdout + result.stderr)
        return result

    def init(self) -> dict:
        result = self.command(
            "init",
            "--config",
            str(self.config),
            "--state",
            str(self.state),
            check=True,
        )
        return json.loads(result.stdout)

    def load_orchestrator(self, path=None):
        path = getattr(self, "orchestrator", ORCHESTRATOR) if path is None else path
        spec = importlib.util.spec_from_file_location(
            "taiji_linux_golden_orchestrator_test", path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load golden orchestrator")
        module = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        return module

    def plan(self, *, expected_deb: str | None = None, dry_run: bool = False):
        arguments = [
            "dry-run" if dry_run else "plan",
            "--state",
            str(self.state),
            "--expect-source-commit",
            self.source_commit,
        ]
        if expected_deb is not None:
            arguments.extend(["--expect-deb-sha256", expected_deb])
        return self.command(*arguments)

    def log(self, label: str) -> Path:
        path = self.logs_dir / f"{label}.log"
        path.write_text(f"{label}\n", encoding="utf-8")
        return path

    def checkpoint(
        self,
        stage: str,
        *,
        result: str = "pass",
        deb: Path | None = None,
        approve: bool = False,
        expected_deb: str | None = None,
        evidence: list[Path] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        log = self.log(f"{stage}-{result}")
        arguments = [
            "checkpoint",
            "--state",
            str(self.state),
            "--expect-source-commit",
            self.source_commit,
            "--stage",
            stage,
            "--result",
            result,
            "--log-path",
            str(log),
        ]
        if result == "pass":
            for path in ([log] if evidence is None else evidence):
                arguments.extend(["--evidence", str(path)])
        if expected_deb is not None:
            arguments.extend(["--expect-deb-sha256", expected_deb])
        if deb is not None:
            arguments.extend(["--deb", str(deb)])
        if approve:
            arguments.extend(["--approve-stage", stage])
        return self.command(*arguments)

    def bind_candidate(self) -> tuple[Path, str]:
        self.review_root.mkdir(parents=True, mode=0o700)
        output = self.review_root / "taijiagent 打包交付" / "生成的安装包"
        output.mkdir(parents=True)
        deb = output / "taiji-agent_1.2.3_amd64.deb"
        deb.write_bytes(b"immutable-candidate-deb")
        return deb, sha256(deb)

    def create_ci_evidence_trio(self) -> list[Path]:
        delivery = self.review_root / "taijiagent 打包交付"
        paths = [
            delivery / "github-ci-evidence.json",
            delivery / "github-ci-run-response.json",
            delivery / "github-ci-jobs-response.json",
        ]
        for path in paths:
            path.write_text('{"fixture":true}\n', encoding="utf-8")
            path.chmod(0o600)
        return paths

    def issue_challenge_envelope(
        self,
        deb: Path,
        purpose: str,
        nonce: str,
    ) -> None:
        output = {
            "certification": self.certification_envelope,
            "publication": self.publication_envelope,
        }[purpose]
        result = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                str(self.challenge_helper),
                "issue",
                "--purpose",
                purpose,
                "--source-commit",
                self.source_commit,
                "--deb",
                str(deb),
                "--output",
                str(output),
                "--ttl-seconds",
                "604800",
                "--nonce",
                nonce,
            ],
            cwd=self.repo,
            env=self.runtime_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def issue_certification_envelope(self, deb: Path) -> None:
        self.issue_challenge_envelope(deb, "certification", "d" * 64)

    def issue_publication_envelope(
        self,
        deb: Path,
        *,
        nonce: str = "e" * 64,
    ) -> None:
        self.issue_challenge_envelope(deb, "publication", nonce)

    @staticmethod
    def expire_challenge_envelope(path: Path) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["issued_at_utc"] = (now - timedelta(hours=2)).isoformat().replace(
            "+00:00", "Z"
        )
        envelope["expires_at_utc"] = (now - timedelta(hours=1)).isoformat().replace(
            "+00:00", "Z"
        )
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def advance_to_challenge_preparation(self) -> tuple[Path, str]:
        self.init()
        self.assertEqual(self.checkpoint("input_verify").returncode, 0)
        deb, digest = self.bind_candidate()
        self.assertEqual(
            self.checkpoint("remote_build", deb=deb, approve=True).returncode,
            0,
        )
        self.assertEqual(
            self.checkpoint("artifact_preflight", expected_deb=digest).returncode,
            0,
        )
        return deb, digest

    def advance_to_offline_rehearsal(self) -> tuple[Path, str]:
        deb, digest = self.advance_to_challenge_preparation()
        self.issue_certification_envelope(deb)
        prepared = self.checkpoint(
            "challenge_preparation",
            expected_deb=digest,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        return deb, digest

    def advance_to_certification_sign(self) -> tuple[Path, str]:
        deb, digest = self.advance_to_offline_rehearsal()
        for stage in ("offline_rehearsal", "target_acceptance"):
            result = self.checkpoint(
                stage,
                approve=True,
                expected_deb=digest,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        return deb, digest

    def advance_to_ci_evidence(self) -> tuple[Path, str]:
        deb, digest = self.advance_to_certification_sign()
        certified = self.checkpoint(
            "certification_sign",
            approve=True,
            expected_deb=digest,
        )
        self.assertEqual(certified.returncode, 0, certified.stderr)
        return deb, digest

    def advance_to_publication_sign(self) -> tuple[Path, str]:
        deb, digest = self.advance_to_ci_evidence()
        ci_evidence = self.create_ci_evidence_trio()
        recorded = self.checkpoint(
            "ci_evidence",
            approve=True,
            expected_deb=digest,
            evidence=ci_evidence,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        return deb, digest

    def test_init_and_plan_bind_input_trio_and_emit_local_verify_only(self):
        state = self.init()
        self.assertEqual(state["schema"], "taiji-linux-golden-orchestrator-state/v5")
        self.assertEqual(state["source_identity"]["source_commit"], self.source_commit)
        self.assertEqual(state["source_commit"], self.source_commit)
        self.assertEqual(state["current_stage"], "input_verify")
        self.assertIn("checkpoint-plan-only", state["scope"])
        self.assertIn("authoritative", state["scope"])
        self.assertEqual(state["input_identity"]["archive"]["sha256"], sha256(self.input_archive))
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["schema"], "taiji-linux-golden-orchestrator-plan/v5")
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["stage"], "input_verify")
        self.assertIn("does not execute", plan["scope_note"])
        self.assertIn("cannot replace", plan["scope_note"])
        self.assertEqual(len(plan["commands"]), 1)
        self.assertEqual(
            plan["commands"][0]["argv"][:3],
            ["/usr/bin/python3", "-I", "-B"],
        )
        self.assertEqual(
            Path(plan["commands"][0]["argv"][3]).name,
            "builder-input-package.py",
        )
        self.assertIn("verify", plan["commands"][0]["argv"])
        self.assertEqual(plan["commands"][0]["env_mode"], "replace")
        self.assertEqual(
            plan["commands"][0]["env"],
            {
                "HOME": str(self.execution_home),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": str(self.execution_tmp),
            },
        )
        self.assertEqual(plan["commands"][0]["env_passthrough"], [])
        self.assertEqual(plan["commands"][0]["env_sensitive"], [])
        self.assertFalse(self.review_root.exists())

        dry_run = self.plan(dry_run=True)
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertEqual(json.loads(dry_run.stdout), plan)

    def test_formal_cli_requires_isolated_system_python_and_ignores_pythonpath(self):
        untrusted = subprocess.run(
            ["python3", str(self.orchestrator), "--help"],
            cwd=self.repo,
            env=self.runtime_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(untrusted.returncode, 0)
        self.assertRegex(untrusted.stderr.lower(), "isolated|/usr/bin/python3|-i|-b")

        hostile = self.root / "hostile-pythonpath"
        hostile.mkdir()
        marker = self.root / "sitecustomize-ran"
        (hostile / "sitecustomize.py").write_text(
            "from pathlib import Path\nPath({!r}).write_text('ran')\n".format(str(marker)),
            encoding="utf-8",
        )
        environment = self.runtime_environment()
        environment["PYTHONPATH"] = str(hostile)
        trusted = subprocess.run(
            ["/usr/bin/python3", "-I", "-B", str(self.orchestrator), "--help"],
            cwd=self.repo,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(trusted.returncode, 0, trusted.stderr)
        self.assertFalse(marker.exists())

        source = self.orchestrator.read_text(encoding="utf-8")
        self.assertNotIn('"python3",', source)
        self.assertNotIn('"/usr/bin/python3",\n                    "-B"', source)
        self.assertGreaterEqual(source.count("*TRUSTED_PYTHON_ARGV"), 11)
        runbook = (
            ROOT / "docs/runbooks/taiji-kylin-uos-offline-delivery.md"
        ).read_text(encoding="utf-8")
        self.assertIn('/usr/bin/python3 -I -B "$ORCHESTRATOR" init', runbook)
        self.assertNotIn('python3 "$ORCHESTRATOR"', runbook.replace(
            '/usr/bin/python3 -I -B "$ORCHESTRATOR"',
            "",
        ))

    def test_resume_revalidates_the_stored_source_identity_and_index_flags(self):
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        relative = next(iter(state["source_identity"]["entries"]))
        state["source_identity"]["entries"][relative]["sha256"] = "0" * 64
        self.state.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.chmod(0o600)

        tampered = self.plan()
        self.assertNotEqual(tampered.returncode, 0)
        self.assertIn("source identity", tampered.stderr.lower())

        self.state.unlink()
        self.init()
        target = self.repo / relative
        subprocess.run(
            ["/usr/bin/git", "update-index", "--assume-unchanged", relative],
            cwd=self.repo,
            env=self.git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        target.write_bytes(target.read_bytes() + b"\n# hidden formal source drift\n")

        hidden_drift = self.plan()
        self.assertNotEqual(hidden_drift.returncode, 0)
        self.assertRegex(
            hidden_drift.stderr.lower(),
            "assume-unchanged|skip-worktree|index flag",
        )

    def test_legacy_v1_v2_v3_v4_config_and_state_are_rejected_instead_of_reinterpreted(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        for legacy in ("v1", "v2", "v3", "v4"):
            with self.subTest(config_schema=legacy):
                config["schema"] = "taiji-linux-golden-orchestrator-config/{}".format(legacy)
                self.config.write_text(
                    json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                rejected_config = self.command(
                    "init",
                    "--config",
                    str(self.config),
                    "--state",
                    str(self.state),
                )
                self.assertNotEqual(rejected_config.returncode, 0)
                self.assertIn("schema", rejected_config.stderr.lower())

        config["schema"] = "taiji-linux-golden-orchestrator-config/v5"
        self.config.write_text(
            json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.init()
        original_state = json.loads(self.state.read_text(encoding="utf-8"))
        for legacy in ("v1", "v2", "v3", "v4"):
            with self.subTest(state_schema=legacy):
                state = json.loads(json.dumps(original_state))
                state["schema"] = "taiji-linux-golden-orchestrator-state/{}".format(legacy)
                self.state.write_text(
                    json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                self.state.chmod(0o600)
                rejected_state = self.plan()
                self.assertNotEqual(rejected_state.returncode, 0)
                self.assertIn("schema", rejected_state.stderr.lower())

    def test_config_requires_exact_positive_integer_ci_run_id(self):
        original = json.loads(self.config.read_text(encoding="utf-8"))
        invalid_ci_values = ({}, {"run_id": 0}, {"run_id": True}, {"run_id": "123"}, {"run_id": 1, "extra": 2})
        for ci in invalid_ci_values:
            with self.subTest(ci=ci):
                config = json.loads(json.dumps(original))
                config["ci"] = ci
                self.config.write_text(
                    json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                result = self.command(
                    "init",
                    "--config",
                    str(self.config),
                    "--state",
                    str(self.state),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.state.exists())

        config = json.loads(json.dumps(original))
        config.pop("ci")
        self.config.write_text(
            json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        missing = self.command(
            "init",
            "--config",
            str(self.config),
            "--state",
            str(self.state),
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse(self.state.exists())

    def test_config_accepts_only_absolute_challenge_envelopes_and_canonical_bundle_paths(self):
        accepted = self.init()
        self.assertEqual(accepted["current_stage"], "input_verify")

        unsafe_fields = (
            ("offline", "challenge", "b" * 64),
            ("target", "challenge", "c" * 64),
            ("release", "certification_challenge", "d" * 64),
            ("release", "publication_challenge", "e" * 64),
            ("release", "certification_output_dir", str(self.root / "drifted-certification")),
            ("release", "ci_evidence", str(self.root / "drifted-ci-evidence.json")),
        )
        for section, key, value in unsafe_fields:
            with self.subTest(section=section, key=key):
                self.state.unlink(missing_ok=True)
                config = json.loads(self.config.read_text(encoding="utf-8"))
                config[section][key] = value
                self.config.write_text(
                    json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                result = self.command(
                    "init",
                    "--config",
                    str(self.config),
                    "--state",
                    str(self.state),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.state.exists())
                config[section].pop(key)
                self.config.write_text(
                    json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )

        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["release"]["certification_challenge_envelope"] = "relative-envelope.json"
        self.config.write_text(
            json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        relative = self.command(
            "init",
            "--config",
            str(self.config),
            "--state",
            str(self.state),
        )
        self.assertNotEqual(relative.returncode, 0)
        self.assertIn("absolute", relative.stderr.lower())

        config["release"]["certification_challenge_envelope"] = str(
            self.certification_envelope
        )
        config["release"]["publication_challenge_envelope"] = str(
            self.certification_envelope
        )
        self.config.write_text(
            json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        same_path = self.command(
            "init",
            "--config",
            str(self.config),
            "--state",
            str(self.state),
        )
        self.assertNotEqual(same_path.returncode, 0)
        self.assertIn("distinct", same_path.stderr.lower())

    def test_target_install_evidence_paths_have_one_canonical_observation_directory(self):
        original = json.loads(self.config.read_text(encoding="utf-8"))
        unsafe_paths = (
            (
                "installer_screenshot",
                "/home/kylin/taiji-install/raw-installer-success.png",
            ),
            (
                "installer_screenshot",
                "/home/kylin/taiji-install/raw/raw-installer-success.png",
            ),
            (
                "install_observation",
                "/home/kylin/taiji-install/renamed-observation.json",
            ),
            (
                "method_attestation",
                "/home/kylin/taiji-install/renamed-attestation.json",
            ),
            (
                "environment_observation",
                "/home/kylin/taiji-install/renamed-environment.json",
            ),
            (
                "method_attestation",
                "/home/kylin/other/single-deb-install-method-attestation.json",
            ),
            (
                "environment_observation",
                "/home/kylin/other/environment-observation.json",
            ),
        )
        for key, value in unsafe_paths:
            with self.subTest(key=key, value=value):
                self.state.unlink(missing_ok=True)
                config = json.loads(json.dumps(original))
                config["target"][key] = value
                self.config.write_text(
                    json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                result = self.command(
                    "init",
                    "--config",
                    str(self.config),
                    "--state",
                    str(self.state),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.state.exists())

        self.config.write_text(
            json.dumps(original, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_preparation_issues_only_certification_envelope_then_its_nonce_drives_evidence(self):
        deb, digest = self.advance_to_challenge_preparation()

        preparation = self.plan(expected_deb=digest)
        self.assertEqual(preparation.returncode, 0, preparation.stderr)
        payload = json.loads(preparation.stdout)
        self.assertEqual(payload["stage"], "challenge_preparation")
        command_arguments = [command["argv"] for command in payload["commands"]]
        self.assertEqual(len(command_arguments), 2)
        self.assertTrue(all(Path(argv[3]).name == CHALLENGE_HELPER.name for argv in command_arguments))
        self.assertIn("issue", command_arguments[0])
        self.assertIn("verify", command_arguments[1])
        self.assertIn("--require-active", command_arguments[1])
        self.assertIn(str(self.certification_envelope), command_arguments[0])
        self.assertIn(str(self.certification_envelope), command_arguments[1])
        self.assertNotIn(str(self.publication_envelope), "\n".join(" ".join(argv) for argv in command_arguments))
        self.assertFalse(self.certification_envelope.exists())
        self.assertFalse(self.publication_envelope.exists())

        self.issue_certification_envelope(deb)
        prepared = self.checkpoint(
            "challenge_preparation",
            expected_deb=digest,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        prepared_state = json.loads(prepared.stdout)
        self.assertEqual(set(prepared_state["challenge_envelopes"]), {"certification"})
        self.assertFalse(self.publication_envelope.exists())

        offline = json.loads(self.plan(expected_deb=digest).stdout)
        self.assertEqual(offline["stage"], "offline_rehearsal")
        self.assertEqual(
            offline["commands"][0]["argv"][offline["commands"][0]["argv"].index("--challenge") + 1],
            "d" * 64,
        )
        self.assertEqual(
            self.checkpoint(
                "offline_rehearsal",
                approve=True,
                expected_deb=digest,
            ).returncode,
            0,
        )

        target = json.loads(self.plan(expected_deb=digest).stdout)
        self.assertEqual(target["stage"], "target_acceptance")
        for command in (target["commands"][0], target["commands"][2], target["commands"][3]):
            argv = command["argv"]
            self.assertEqual(argv[argv.index("--challenge") + 1], "d" * 64)
        attestation_argv = target["commands"][2]["argv"]
        runner_argv = target["commands"][3]["argv"]
        self.assertEqual(
            attestation_argv[attestation_argv.index("--graphical-evidence") + 1],
            "/home/kylin/raw-installer-success.png",
        )
        self.assertEqual(
            attestation_argv[attestation_argv.index("--matrix") + 1],
            "/home/kylin/taiji-acceptance-data/验收工具/certification-matrix.json",
        )
        self.assertEqual(
            attestation_argv[attestation_argv.index("--category-id") + 1],
            "kylin-v10-sp1-x86_64",
        )
        self.assertEqual(
            attestation_argv[
                attestation_argv.index("--environment-observation") + 1
            ],
            "/home/kylin/taiji-install/environment-observation.json",
        )
        self.assertEqual(
            runner_argv[runner_argv.index("--installer-screenshot") + 1],
            "/home/kylin/taiji-install/single-deb-graphical-installer.png",
        )
        self.assertEqual(
            self.checkpoint(
                "target_acceptance",
                approve=True,
                expected_deb=digest,
            ).returncode,
            0,
        )

        certification = json.loads(self.plan(expected_deb=digest).stdout)
        self.assertEqual(certification["stage"], "certification_sign")
        assembler = certification["commands"][0]["argv"]
        self.assertIn("--challenge-envelope", assembler)
        self.assertEqual(
            assembler[assembler.index("--challenge-envelope") + 1],
            str(self.certification_envelope),
        )
        self.assertNotIn("--challenge", assembler)
        self.assertEqual(
            certification["commands"][1]["env"]["PATH"], "/usr/bin:/bin"
        )
        self.assertEqual(certification["commands"][1]["env_passthrough"], [])

    def test_ci_v2_stage_is_between_certification_and_publication_and_binds_exact_trio(self):
        deb, digest = self.advance_to_ci_evidence()

        ci_plan_result = self.plan(expected_deb=digest)
        self.assertEqual(ci_plan_result.returncode, 0, ci_plan_result.stderr)
        ci_plan = json.loads(ci_plan_result.stdout)
        self.assertEqual(ci_plan["stage"], "ci_evidence")
        self.assertTrue(ci_plan["explicit_approval_required"])
        self.assertFalse(self.publication_envelope.exists())
        self.assertEqual(len(ci_plan["commands"]), 1)
        ci_command = ci_plan["commands"][0]
        self.assertEqual(
            ci_command["argv"],
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                str(self.repo / "scripts/produce-taiji-github-ci-evidence.py"),
                "--source-commit",
                self.source_commit,
                "--run-id",
                "123456789",
                "--delivery-dir",
                str(self.review_root / "taijiagent 打包交付"),
            ],
        )
        self.assertEqual(ci_command["boundary"], "network-and-ci-human-approval")

        skipped = self.checkpoint(
            "publication_sign",
            approve=True,
            expected_deb=digest,
        )
        self.assertNotEqual(skipped.returncode, 0)
        self.assertIn("current stage", skipped.stderr.lower())

        delivery = self.review_root / "taijiagent 打包交付"
        first_two = []
        for basename in (
            "github-ci-evidence.json",
            "github-ci-run-response.json",
        ):
            path = delivery / basename
            path.write_text('{"fixture":true}\n', encoding="utf-8")
            path.chmod(0o600)
            first_two.append(path)
        incomplete = self.checkpoint(
            "ci_evidence",
            approve=True,
            expected_deb=digest,
            evidence=first_two,
        )
        self.assertNotEqual(incomplete.returncode, 0)
        self.assertIn("three", incomplete.stderr.lower())

        jobs = delivery / "github-ci-jobs-response.json"
        jobs.write_text('{"fixture":true}\n', encoding="utf-8")
        jobs.chmod(0o600)
        extra = self.root / "forged-ci-evidence.txt"
        extra.write_text("forged\n", encoding="utf-8")
        overcomplete = self.checkpoint(
            "ci_evidence",
            approve=True,
            expected_deb=digest,
            evidence=first_two + [jobs, extra],
        )
        self.assertNotEqual(overcomplete.returncode, 0)
        self.assertIn("three", overcomplete.stderr.lower())

        exact_trio = [delivery / name for name in (
            "github-ci-evidence.json",
            "github-ci-run-response.json",
            "github-ci-jobs-response.json",
        )]
        recorded = self.checkpoint(
            "ci_evidence",
            approve=True,
            expected_deb=digest,
            evidence=exact_trio,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["current_stage"], "publication_sign")
        self.assertFalse(self.publication_envelope.exists())

        self.issue_publication_envelope(deb)
        publication = self.plan(expected_deb=digest)
        self.assertEqual(publication.returncode, 0, publication.stderr)
        self.assertEqual(json.loads(publication.stdout)["stage"], "publication_sign")

    def test_ci_stage_accepts_expired_signed_certification_binding_but_rejects_early_publication(self):
        deb, digest = self.advance_to_ci_evidence()
        self.expire_challenge_envelope(self.certification_envelope)
        module = self.load_orchestrator()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["challenge_envelopes"]["certification"] = module._fingerprint(
            self.certification_envelope,
            "certification challenge envelope",
            module.MAX_CONTROL_FILE_BYTES,
        )
        self.state.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.chmod(0o600)

        historical = self.plan(expected_deb=digest)
        self.assertEqual(historical.returncode, 0, historical.stderr)
        self.assertEqual(json.loads(historical.stdout)["stage"], "ci_evidence")

        self.issue_publication_envelope(deb)
        premature = self.plan(expected_deb=digest)
        self.assertNotEqual(premature.returncode, 0)
        self.assertIn("publication_sign", premature.stderr)

    def test_ci_checkpoint_fingerprints_all_three_files_for_resume_drift_detection(self):
        _deb, digest = self.advance_to_ci_evidence()
        trio = self.create_ci_evidence_trio()
        recorded = self.checkpoint(
            "ci_evidence",
            approve=True,
            expected_deb=digest,
            evidence=trio,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)

        trio[2].write_text('{"drifted":true}\n', encoding="utf-8")
        drifted = self.plan(expected_deb=digest)
        self.assertNotEqual(drifted.returncode, 0)
        self.assertIn("ci_evidence evidence", drifted.stderr)

    def test_publication_bundle_and_signer_use_the_review_delivery_contract(self):
        _deb, digest = self.advance_to_offline_rehearsal()
        for stage in ("offline_rehearsal", "target_acceptance"):
            result = self.checkpoint(
                stage,
                approve=True,
                expected_deb=digest,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        delivery = self.review_root / "taijiagent 打包交付"
        certification_plan = json.loads(self.plan(expected_deb=digest).stdout)
        certification_assembler = certification_plan["commands"][0]["argv"]
        self.assertEqual(
            certification_assembler[certification_assembler.index("--output") + 1],
            str(delivery / "certification"),
        )
        self.assertEqual(
            certification_plan["commands"][1]["argv"],
            [
                "/bin/bash",
                "-p",
                str(self.repo / "scripts/sign-taiji-release-evidence.sh"),
                str(delivery / "certification/certification-set.json"),
                str(self.root / "offline-release-private-key.pem"),
            ],
        )
        certified = self.checkpoint(
            "certification_sign",
            approve=True,
            expected_deb=digest,
        )
        self.assertEqual(certified.returncode, 0, certified.stderr)

        ci_plan_result = self.plan(expected_deb=digest)
        self.assertEqual(ci_plan_result.returncode, 0, ci_plan_result.stderr)
        ci_plan = json.loads(ci_plan_result.stdout)
        self.assertEqual(ci_plan["stage"], "ci_evidence")
        self.assertIn("--delivery-dir", ci_plan["commands"][0]["argv"])
        ci_evidence_paths = self.create_ci_evidence_trio()
        ci_recorded = self.checkpoint(
            "ci_evidence",
            approve=True,
            expected_deb=digest,
            evidence=ci_evidence_paths,
        )
        self.assertEqual(ci_recorded.returncode, 0, ci_recorded.stderr)

        publication_plan = self.plan(expected_deb=digest)
        self.assertEqual(publication_plan.returncode, 0, publication_plan.stderr)
        publication = json.loads(publication_plan.stdout)
        self.assertEqual(publication["stage"], "publication_sign")
        issue = publication["commands"][0]["argv"]
        verify = publication["commands"][1]["argv"]
        self.assertIn("issue", issue)
        self.assertIn("--purpose", issue)
        self.assertEqual(issue[issue.index("--purpose") + 1], "publication")
        self.assertIn(str(self.publication_envelope), issue)
        self.assertIn("verify", verify)
        self.assertIn("--require-active", verify)
        self.assertIn(str(self.publication_envelope), verify)
        self.assertFalse(self.publication_envelope.exists())

        assembler_command = publication["commands"][2]
        assembler = assembler_command["argv"]
        self.assertEqual(
            assembler[assembler.index("--certification-set") + 1],
            str(delivery / "certification/certification-set.json"),
        )
        self.assertEqual(
            assembler[assembler.index("--certification-signature") + 1],
            str(delivery / "certification/certification-set.json.sig"),
        )
        self.assertEqual(
            assembler[assembler.index("--ci-evidence") + 1],
            str(delivery / "github-ci-evidence.json"),
        )
        self.assertEqual(
            assembler[assembler.index("--output") + 1],
            str(delivery / "release-evidence.json"),
        )
        self.assertEqual(
            assembler[assembler.index("--challenge-envelope") + 1],
            str(self.publication_envelope),
        )
        self.assertEqual(
            assembler_command["required_inputs"],
            [
                str(delivery / "certification/certification-set.json"),
                str(delivery / "certification/certification-set.json.sig"),
                str(delivery / "github-ci-evidence.json"),
                str(delivery / "github-ci-run-response.json"),
                str(delivery / "github-ci-jobs-response.json"),
            ],
        )

        signer = publication["commands"][3]
        self.assertEqual(
            signer["argv"],
            [
                "/bin/bash",
                "-p",
                str(self.repo / "scripts/sign-taiji-release-evidence.sh"),
                str(delivery / "release-evidence.json"),
                str(self.root / "offline-release-private-key.pem"),
            ],
        )
        self.assertNotIn("--delivery-dir", signer["argv"])
        self.assertEqual(signer["env_passthrough"], ["GITHUB_TOKEN"])
        self.assertEqual(signer["env_sensitive"], ["GITHUB_TOKEN"])

        self.issue_publication_envelope(_deb)
        resumed_plan = self.plan(expected_deb=digest)
        self.assertEqual(resumed_plan.returncode, 0, resumed_plan.stderr)
        resumed_commands = json.loads(resumed_plan.stdout)["commands"]
        self.assertEqual(len(resumed_commands), 3)
        self.assertIn("verify", resumed_commands[0]["argv"])
        self.assertNotIn(
            "issue",
            [argument for command in resumed_commands for argument in command["argv"]],
        )

    def test_publication_envelope_cannot_be_issued_before_certification_is_signed(self):
        deb, digest = self.advance_to_offline_rehearsal()
        self.issue_publication_envelope(deb)

        premature = self.plan(expected_deb=digest)

        self.assertNotEqual(premature.returncode, 0)
        self.assertIn("publication_sign", premature.stderr)
        self.assertIn("fresh path", premature.stderr.lower())

    def test_certification_checkpoint_rejects_publication_issued_after_plan_but_before_sign_completion(self):
        deb, digest = self.advance_to_certification_sign()
        certification_plan = self.plan(expected_deb=digest)
        self.assertEqual(certification_plan.returncode, 0, certification_plan.stderr)
        self.issue_publication_envelope(deb)

        raced = self.checkpoint(
            "certification_sign",
            approve=True,
            expected_deb=digest,
        )

        self.assertNotEqual(raced.returncode, 0)
        self.assertIn("publication_sign", raced.stderr)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["current_stage"], "certification_sign")

    def test_resume_and_retry_fail_closed_after_challenge_expiry(self):
        _deb, digest = self.advance_to_offline_rehearsal()
        failed = self.checkpoint(
            "offline_rehearsal",
            result="fail",
            approve=True,
            expected_deb=digest,
        )
        self.assertEqual(failed.returncode, 0, failed.stderr)

        self.expire_challenge_envelope(self.certification_envelope)
        module = self.load_orchestrator()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["challenge_envelopes"]["certification"] = module._fingerprint(
            self.certification_envelope,
            "certification challenge envelope",
            module.MAX_CONTROL_FILE_BYTES,
        )
        self.state.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.chmod(0o600)

        resumed = self.plan(expected_deb=digest)
        self.assertNotEqual(resumed.returncode, 0)
        self.assertIn("expired", resumed.stderr.lower())
        retried = self.command(
            "retry",
            "--state",
            str(self.state),
            "--expect-source-commit",
            self.source_commit,
            "--expect-deb-sha256",
            digest,
            "--stage",
            "offline_rehearsal",
        )
        self.assertNotEqual(retried.returncode, 0)
        self.assertIn("expired", retried.stderr.lower())

    def test_resume_and_retry_fail_closed_after_signer_reserves_nonce(self):
        _deb, digest = self.advance_to_offline_rehearsal()
        failed = self.checkpoint(
            "offline_rehearsal",
            result="fail",
            approve=True,
            expected_deb=digest,
        )
        self.assertEqual(failed.returncode, 0, failed.stderr)
        module = self.load_orchestrator()
        state = module._load_state(self.state)

        signer_home = self.root / "signer-home"
        reservation = (
            signer_home
            / ".local/state/taiji-release-evidence/signers"
            / "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
            / "used-nonces"
            / (("d" * 64) + ".used")
        )
        reservation.parent.mkdir(parents=True, mode=0o700)
        reservation.write_text("reserved\n", encoding="utf-8")
        reservation.chmod(0o600)

        with mock.patch.object(
            module,
            "_signer_home",
            return_value=signer_home,
            create=True,
        ):
            with self.assertRaisesRegex(module.OrchestratorError, "reserved|used"):
                module.build_plan(state)
            with self.assertRaisesRegex(module.OrchestratorError, "reserved|used"):
                module.retry(
                    self.state,
                    self.source_commit,
                    digest,
                    "offline_rehearsal",
                )

    def test_publication_resume_and_retry_fail_closed_after_envelope_expiry(self):
        deb, digest = self.advance_to_publication_sign()
        self.issue_publication_envelope(deb)
        failed = self.checkpoint(
            "publication_sign",
            result="fail",
            approve=True,
            expected_deb=digest,
        )
        self.assertEqual(failed.returncode, 0, failed.stderr)
        self.expire_challenge_envelope(self.publication_envelope)

        resumed = self.plan(expected_deb=digest)
        self.assertNotEqual(resumed.returncode, 0)
        self.assertIn("expired", resumed.stderr.lower())
        retried = self.command(
            "retry",
            "--state",
            str(self.state),
            "--expect-source-commit",
            self.source_commit,
            "--expect-deb-sha256",
            digest,
            "--stage",
            "publication_sign",
        )
        self.assertNotEqual(retried.returncode, 0)
        self.assertIn("expired", retried.stderr.lower())

    def test_publication_resume_and_retry_fail_closed_after_signer_reserves_nonce(self):
        deb, digest = self.advance_to_publication_sign()
        self.issue_publication_envelope(deb)
        failed = self.checkpoint(
            "publication_sign",
            result="fail",
            approve=True,
            expected_deb=digest,
        )
        self.assertEqual(failed.returncode, 0, failed.stderr)
        module = self.load_orchestrator()
        state = module._load_state(self.state)

        signer_home = self.root / "publication-signer-home"
        reservation = (
            signer_home
            / ".local/state/taiji-release-evidence/signers"
            / "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
            / "used-nonces"
            / (("e" * 64) + ".used")
        )
        reservation.parent.mkdir(parents=True, mode=0o700)
        reservation.write_text("reserved\n", encoding="utf-8")
        reservation.chmod(0o600)

        with mock.patch.object(module, "_signer_home", return_value=signer_home):
            with self.assertRaisesRegex(
                module.OrchestratorError,
                "reserved|used",
            ) as stopped:
                module.build_plan(state)
            guidance = str(stopped.exception).lower()
            self.assertIn("fresh", guidance)
            self.assertIn("config/state", guidance)
            self.assertIn("must not overwrite", guidance)
            with self.assertRaisesRegex(module.OrchestratorError, "reserved|used"):
                module.retry(
                    self.state,
                    self.source_commit,
                    digest,
                    "publication_sign",
                )

    def test_certification_checkpoint_can_record_signer_success_after_envelope_ttl_elapsed(self):
        _deb, digest = self.advance_to_certification_sign()
        self.expire_challenge_envelope(self.certification_envelope)
        module = self.load_orchestrator()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["challenge_envelopes"]["certification"] = module._fingerprint(
            self.certification_envelope,
            "certification challenge envelope",
            module.MAX_CONTROL_FILE_BYTES,
        )
        self.state.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.chmod(0o600)

        recorded = self.checkpoint(
            "certification_sign",
            approve=True,
            expected_deb=digest,
        )

        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["current_stage"], "ci_evidence")

    def test_publication_checkpoint_can_record_signer_success_after_envelope_ttl_elapsed(self):
        deb, digest = self.advance_to_publication_sign()
        self.issue_publication_envelope(deb)
        self.expire_challenge_envelope(self.publication_envelope)

        recorded = self.checkpoint(
            "publication_sign",
            approve=True,
            expected_deb=digest,
        )

        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["current_stage"], "release_check")

    def test_release_resume_does_not_require_signed_publication_envelope_to_still_be_active(self):
        deb, digest = self.advance_to_publication_sign()
        self.issue_publication_envelope(deb)
        published = self.checkpoint(
            "publication_sign",
            approve=True,
            expected_deb=digest,
        )
        self.assertEqual(published.returncode, 0, published.stderr)

        self.expire_challenge_envelope(self.publication_envelope)
        module = self.load_orchestrator()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["challenge_envelopes"]["publication"] = module._fingerprint(
            self.publication_envelope,
            "publication challenge envelope",
            module.MAX_CONTROL_FILE_BYTES,
        )
        self.state.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.chmod(0o600)

        resumed = self.plan(expected_deb=digest)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["stage"], "release_check")

    def test_checkpoint_is_ordered_and_external_stages_require_explicit_approval(self):
        self.init()
        skipped = self.checkpoint("artifact_preflight")
        self.assertNotEqual(skipped.returncode, 0)
        self.assertIn("current stage", skipped.stderr)

        passed = self.checkpoint("input_verify")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        remote_plan = json.loads(self.plan().stdout)
        self.assertEqual(remote_plan["stage"], "remote_build")
        flattened = "\n".join(
            " ".join(item.get("argv", [])) for item in remote_plan["commands"]
        )
        self.assertIn("ssh", flattened)
        self.assertIn("scp", flattened)
        self.assertNotIn("sed -i", flattened)
        self.assertNotIn("prepare fresh local review parent", {item["label"] for item in remote_plan["commands"]})
        build_command = next(
            command
            for command in remote_plan["commands"]
            if command["label"] == "run or resume frozen 00 builder"
        )
        self.assertEqual(
            build_command["argv"][:4],
            ["/usr/bin/python3", "-I", "-B", str(self.repo / "packaging/linux/kylin_remote_build.py")],
        )
        self.assertIn("kylin", build_command["argv"])
        self.assertIn("/home/kylin", build_command["argv"])
        self.assertIn("/home/kylin/taiji-builds", " ".join(build_command["argv"]))
        self.assertIn(self.source_commit, build_command["argv"])
        self.assertIn("--remote-attempt-id", build_command["argv"])
        self.assertNotIn("300", build_command["argv"])
        self.assertNotIn("00_制包机_生成离线交付包.sh", build_command["argv"])
        result_command = next(
            command
            for command in remote_plan["commands"]
            if command["label"] == "retrieve remote build result"
        )
        self.assertIn("remote-build-result.json", " ".join(result_command["argv"]))
        self.assertEqual(
            result_command["argv"][-1],
            str(self.logs_dir / "remote-build-result.json"),
        )
        for command in remote_plan["commands"]:
            self.assertEqual(command["env_mode"], "replace")
            self.assertEqual(command["env_passthrough"], ["SSH_AUTH_SOCK"])
            self.assertEqual(command["env_sensitive"], ["SSH_AUTH_SOCK"])

        deb, _ = self.bind_candidate()
        unapproved = self.checkpoint("remote_build", deb=deb)
        self.assertNotEqual(unapproved.returncode, 0)
        self.assertIn("approval", unapproved.stderr.lower())

        approved = self.checkpoint("remote_build", deb=deb, approve=True)
        self.assertEqual(approved.returncode, 0, approved.stderr)

    def test_failure_stops_records_log_and_retry_cannot_skip_the_failed_stage(self):
        self.init()
        self.assertEqual(self.checkpoint("input_verify").returncode, 0)

        failed = self.checkpoint("remote_build", result="fail", approve=True)
        self.assertEqual(failed.returncode, 0, failed.stderr)
        stopped = self.plan()
        self.assertNotEqual(stopped.returncode, 0)
        payload = json.loads(stopped.stdout)
        self.assertEqual(payload["status"], "STOPPED")
        self.assertEqual(payload["stage"], "remote_build")
        self.assertTrue(payload["failure"]["log"]["path"].endswith("remote_build-fail.log"))

        cannot_skip = self.checkpoint("artifact_preflight")
        self.assertNotEqual(cannot_skip.returncode, 0)

        attempt_before_retry = json.loads(self.state.read_text(encoding="utf-8"))[
            "remote_attempt_id"
        ]
        unconfirmed = self.command(
            "retry",
            "--state",
            str(self.state),
            "--expect-source-commit",
            self.source_commit,
            "--stage",
            "remote_build",
        )
        self.assertNotEqual(unconfirmed.returncode, 0)
        self.assertIn("terminal failed", unconfirmed.stderr.lower())
        self.assertEqual(
            json.loads(self.state.read_text(encoding="utf-8"))["remote_attempt_id"],
            attempt_before_retry,
        )

        retry = self.command(
            "retry",
            "--state",
            str(self.state),
            "--expect-source-commit",
            self.source_commit,
            "--stage",
            "remote_build",
            "--confirm-remote-terminal-failed",
        )
        self.assertEqual(retry.returncode, 0, retry.stderr)
        resumed = json.loads(self.plan().stdout)
        self.assertEqual(resumed["status"], "READY")
        self.assertEqual(resumed["stage"], "remote_build")

    def test_remote_retry_uses_a_new_attempt_directory_without_deleting_the_failed_one(self):
        self.init()
        self.assertEqual(self.checkpoint("input_verify").returncode, 0)
        first = json.loads(self.plan().stdout)
        first_commands = "\n".join(
            " ".join(command.get("argv", [])) for command in first["commands"]
        )

        self.assertEqual(
            self.checkpoint("remote_build", result="fail", approve=True).returncode,
            0,
        )
        retry = self.command(
            "retry",
            "--state",
            str(self.state),
            "--expect-source-commit",
            self.source_commit,
            "--stage",
            "remote_build",
            "--confirm-remote-terminal-failed",
        )
        self.assertEqual(retry.returncode, 0, retry.stderr)
        second = json.loads(self.plan().stdout)
        second_commands = "\n".join(
            " ".join(command.get("argv", [])) for command in second["commands"]
        )

        self.assertNotEqual(first_commands, second_commands)
        self.assertNotIn("rm -rf", first_commands)
        self.assertNotIn("rm -rf", second_commands)

    def test_resume_rejects_source_commit_and_candidate_deb_identity_drift(self):
        self.init()
        wrong_source = self.command(
            "plan",
            "--state",
            str(self.state),
            "--expect-source-commit",
            "f" * 40,
        )
        self.assertNotEqual(wrong_source.returncode, 0)
        self.assertIn("source commit", wrong_source.stderr.lower())

        self.assertEqual(self.checkpoint("input_verify").returncode, 0)
        deb, digest = self.bind_candidate()
        self.assertEqual(
            self.checkpoint("remote_build", deb=deb, approve=True).returncode,
            0,
        )
        no_expected_deb = self.plan()
        self.assertNotEqual(no_expected_deb.returncode, 0)
        self.assertIn("expect-deb-sha256", no_expected_deb.stderr)

        accepted = self.plan(expected_deb=digest)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["stage"], "artifact_preflight")

        deb.write_bytes(b"candidate-was-replaced")
        drifted = self.plan(expected_deb=digest)
        self.assertNotEqual(drifted.returncode, 0)
        self.assertIn("candidate DEB", drifted.stderr)

    def test_resume_rejects_checkpoint_sequence_tampering(self):
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["current_stage"] = "remote_build"
        self.state.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.chmod(0o600)

        result = self.plan()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sequence", result.stderr.lower())

    def test_resume_requires_complete_input_identity_and_manual_approval_record(self):
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["input_identity"].pop("archive")
        self.state.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.chmod(0o600)
        missing_identity = self.plan()
        self.assertNotEqual(missing_identity.returncode, 0)
        self.assertIn("input identity", missing_identity.stderr.lower())

        self.state.unlink()
        self.init()
        self.assertEqual(self.checkpoint("input_verify").returncode, 0)
        deb, digest = self.bind_candidate()
        self.assertEqual(
            self.checkpoint("remote_build", deb=deb, approve=True).returncode,
            0,
        )
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["stages"]["remote_build"]["explicit_approval_recorded"] = False
        self.state.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.chmod(0o600)
        missing_approval = self.plan(expected_deb=digest)
        self.assertNotEqual(missing_approval.returncode, 0)
        self.assertIn("approval", missing_approval.stderr.lower())

    def test_resume_rejects_input_or_passed_checkpoint_evidence_drift(self):
        self.init()
        self.input_archive.write_bytes(b"input-was-replaced")
        input_drift = self.plan()
        self.assertNotEqual(input_drift.returncode, 0)
        self.assertIn("builder input", input_drift.stderr)

        self.state.unlink()
        self.input_archive.write_bytes(b"frozen-builder-input")
        self.init()
        self.assertEqual(self.checkpoint("input_verify").returncode, 0)
        (self.logs_dir / "input_verify-pass.log").write_text(
            "checkpoint-was-replaced\n", encoding="utf-8"
        )
        evidence_drift = self.plan()
        self.assertNotEqual(evidence_drift.returncode, 0)
        self.assertIn("evidence", evidence_drift.stderr)

    def test_certification_and_publication_envelope_nonces_must_be_independent(self):
        deb, digest = self.advance_to_publication_sign()
        self.issue_publication_envelope(deb, nonce="d" * 64)

        result = self.plan(expected_deb=digest)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("independent", result.stderr.lower())
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["current_stage"], "publication_sign")
        self.assertEqual(set(state["challenge_envelopes"]), {"certification"})

    def test_remote_host_cannot_be_parsed_as_an_ssh_option(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["remote"]["host"] = "-Fattacker-config"
        self.config.write_text(
            json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        result = self.command(
            "init",
            "--config",
            str(self.config),
            "--state",
            str(self.state),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("remote.host", result.stderr)

    def test_artifact_fingerprint_streams_without_loading_the_whole_deb(self):
        module = self.load_orchestrator()

        with mock.patch.object(
            module,
            "_read_regular",
            side_effect=AssertionError("large artifact must not use bounded control-file reader"),
        ):
            identity = module._fingerprint(self.input_archive, "candidate DEB")

        self.assertEqual(identity["sha256"], sha256(self.input_archive))
        self.assertEqual(identity["size"], self.input_archive.stat().st_size)

    def test_builder_input_archive_uses_large_artifact_limit(self):
        self.input_archive.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        self.input_checksum.write_text(
            f"{sha256(self.input_archive)}  {self.input_archive.name}\n"
            f"{sha256(self.input_manifest)}  {self.input_manifest.name}\n",
            encoding="utf-8",
        )

        result = self.command(
            "init",
            "--config",
            str(self.config),
            "--state",
            str(self.state),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state["input_identity"]["archive"]["size"], 2 * 1024 * 1024 + 1)

    def test_checkpoint_log_uses_large_evidence_limit(self):
        self.init()
        log = self.logs_dir / "input-verify-large.log"
        log.write_bytes(b"x" * (2 * 1024 * 1024 + 1))

        result = self.command(
            "checkpoint",
            "--state",
            str(self.state),
            "--expect-source-commit",
            self.source_commit,
            "--stage",
            "input_verify",
            "--result",
            "pass",
            "--log-path",
            str(log),
            "--evidence",
            str(log),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["current_stage"], "remote_build")

    def test_writable_control_file_or_candidate_is_rejected(self):
        self.config.chmod(0o666)
        config_result = self.command(
            "init",
            "--config",
            str(self.config),
            "--state",
            str(self.state),
        )
        self.assertNotEqual(config_result.returncode, 0)
        self.assertIn("writable", config_result.stderr.lower())

        self.config.chmod(0o600)
        self.init()
        self.assertEqual(self.checkpoint("input_verify").returncode, 0)
        deb, _ = self.bind_candidate()
        deb.chmod(0o666)
        candidate_result = self.checkpoint("remote_build", deb=deb, approve=True)
        self.assertNotEqual(candidate_result.returncode, 0)
        self.assertIn("writable", candidate_result.stderr.lower())

    def test_full_plan_uses_existing_trusted_entrypoints_and_never_executes_them(self):
        self.init()
        self.assertEqual(self.checkpoint("input_verify").returncode, 0)
        deb, digest = self.bind_candidate()
        self.assertEqual(
            self.checkpoint("remote_build", deb=deb, approve=True).returncode,
            0,
        )

        expected = {
            "artifact_preflight": "01_制包机_发布预检.sh",
            "challenge_preparation": "taiji-challenge-envelope.py",
            "offline_rehearsal": "produce-taiji-offline-rehearsal.py",
            "target_acceptance": "/usr/bin/taiji-agent-acceptance",
            "certification_sign": "assemble-taiji-certification-set.py",
            "ci_evidence": "produce-taiji-github-ci-evidence.py",
            "publication_sign": "sign-taiji-release-evidence.sh",
            "release_check": "taiji-release-check.sh",
            "publish": "publish-single-deb.sh",
        }
        for stage, trusted_entrypoint in expected.items():
            plan_result = self.plan(expected_deb=digest)
            self.assertEqual(plan_result.returncode, 0, plan_result.stderr)
            payload = json.loads(plan_result.stdout)
            self.assertEqual(payload["stage"], stage)
            command_text = "\n".join(
                " ".join(item.get("argv", [])) + item.get("manual_action", "")
                for item in payload["commands"]
            )
            self.assertIn(trusted_entrypoint, command_text)
            for command in payload["commands"]:
                self.assertEqual(
                    set(command),
                    {
                        "argv",
                        "boundary",
                        "cwd",
                        "env",
                        "env_mode",
                        "env_passthrough",
                        "env_sensitive",
                        "label",
                        "log_path",
                        *({"manual_action"} if "manual_action" in command else set()),
                        *({"required_inputs"} if "required_inputs" in command else set()),
                    },
                )
                self.assertEqual(command["env_sensitive"], sorted(command["env_sensitive"]))
                self.assertTrue(
                    set(command["env_sensitive"]).issubset(command["env_passthrough"])
                )
                if "manual_action" in command:
                    self.assertEqual(command["env_mode"], "human-session")
                    self.assertEqual(command["env"], {})
                    self.assertEqual(command["env_passthrough"], [])
                    continue
                self.assertEqual(command["env_mode"], "replace")
                self.assertEqual(command["env"]["PATH"], "/usr/bin:/bin")
                forbidden = {
                    "BASH_ENV",
                    "ENV",
                    "PYTHONHOME",
                    "PYTHONPATH",
                    "LD_PRELOAD",
                    "LD_LIBRARY_PATH",
                }
                self.assertTrue(forbidden.isdisjoint(command["env"]))
                self.assertTrue(forbidden.isdisjoint(command["env_passthrough"]))
                if command["boundary"].startswith("target-"):
                    self.assertNotIn("HOME", command["env"])
                    self.assertEqual(command["env_passthrough"], TARGET_SESSION_PASSTHROUGH)
                    self.assertEqual(command["env_sensitive"], [])
                else:
                    self.assertEqual(command["env"]["HOME"], str(self.execution_home))
                    self.assertEqual(command["env"]["TMPDIR"], str(self.execution_tmp))
            token_labels = {
                "collect trusted GitHub CI v2 evidence trio",
                "sign publication evidence with offline key",
                "run formal release check including live CI revalidation",
                "atomically publish exactly one customer DEB",
            }
            for command in payload["commands"]:
                if command["label"] in token_labels:
                    self.assertEqual(command["env_passthrough"], ["GITHUB_TOKEN"])
                    self.assertEqual(command["env_sensitive"], ["GITHUB_TOKEN"])
            if stage == "target_acceptance":
                self.assertIn("验收工具/certification-matrix.json", command_text)
                self.assertIn("operator-001", command_text)
                self.assertNotIn("<controlled-operator-id>", command_text)
                self.assertNotIn("bash ./04_", command_text)
            self.assertTrue(payload["checkpoint_required"])
            self.assertFalse(payload["auto_advance"])
            self.assertFalse((self.logs_dir / f"{stage}.log").exists())
            if stage == "challenge_preparation":
                self.issue_certification_envelope(deb)
            evidence = None
            if stage == "ci_evidence":
                evidence = self.create_ci_evidence_trio()
            if stage == "publication_sign":
                self.issue_publication_envelope(deb)
            result = self.checkpoint(
                stage,
                approve=stage in {
                    "offline_rehearsal",
                    "target_acceptance",
                    "certification_sign",
                    "ci_evidence",
                    "publication_sign",
                    "release_check",
                    "publish",
                },
                expected_deb=digest,
                evidence=evidence,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        complete = self.plan(expected_deb=digest)
        self.assertEqual(complete.returncode, 0, complete.stderr)
        self.assertEqual(json.loads(complete.stdout)["status"], "CHECKPOINTS_COMPLETE")

    def test_challenge_helper_and_orchestrator_are_named_python38_gate_entries(self):
        source = PYTHON38_GATE.read_text(encoding="utf-8")
        self.assertIn(
            'GOLDEN_ORCHESTRATOR = ROOT / "scripts/taiji-linux-golden-orchestrator.py"',
            source,
        )
        self.assertIn(
            'CHALLENGE_ENVELOPE_HELPER = ROOT / "scripts/taiji-challenge-envelope.py"',
            source,
        )
        self.assertIn(
            'CI_EVIDENCE_PRODUCER = ROOT / "scripts/produce-taiji-github-ci-evidence.py"',
            source,
        )
        self.assertIn("GOLDEN_ORCHESTRATOR,", source)
        self.assertIn("CHALLENGE_ENVELOPE_HELPER,", source)
        self.assertIn("CI_EVIDENCE_PRODUCER,", source)


if __name__ == "__main__":
    unittest.main()
