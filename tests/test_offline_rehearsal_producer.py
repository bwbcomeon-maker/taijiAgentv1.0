import hashlib
import importlib.util
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / "scripts" / "produce-taiji-offline-rehearsal.py"
VALIDATOR = ROOT / "scripts" / "validate-taiji-release-evidence.py"
CERTIFICATION_ASSEMBLER = ROOT / "scripts" / "assemble-taiji-certification-set.py"
POLICY = ROOT / "packaging" / "linux" / "compatibility-policy.json"
POLICY_HELPER = ROOT / "packaging" / "linux" / "compatibility_policy.py"
SOURCE_INTEGRITY_HELPER = ROOT / "packaging" / "linux" / "source-archive-integrity.py"
DOCKERFILE = ROOT / "tools" / "taiji-offline-rehearsal" / "Dockerfile"
LIFECYCLE = ROOT / "tools" / "taiji-offline-rehearsal" / "run-lifecycle.sh"
REQUIRED_SYSTEM_PACKAGES = (
    ROOT / "tools" / "taiji-offline-rehearsal" / "ubuntu20-required-system-packages.tsv"
)
CHALLENGE = "ab" * 32
SALE_READINESS = ROOT / "docs" / "taiji-sale-readiness.md"
DELIVERY_GUIDE = ROOT / "taijiagent 打包交付" / "操作说明.md"
TOOLCHAIN = {
    "python_dependency_lock_status": "strict-locked",
    "python_lock_basename": "uv.lock",
    "python_lock_sha256": "dbab12665d98aef021ba64953c61b0ed8a908cfb56a1c01e2fcb4b052b71a2a1",
    "python_version": "3.11.15",
    "python_archive_sha256": "2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a",
    "python_executable_sha256": "5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba",
    "uv_version": "0.12.2",
    "uv_archive_sha256": "d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4",
    "uv_executable_sha256": "72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2",
    "node_version": "22.23.1",
    "node_archive_sha256": "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578",
    "node_executable_sha256": "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068",
    "electron_version": "39.8.10",
    "electron_archive_sha256": "92e8b031fa5327c78a972279fd75fc8503fcd1773401809f4557e4de583eabd1",
    "electron_executable_sha256": "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OfflineRehearsalProducerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.delivery = self.temp_path / "taijiagent 打包交付"
        self.output = self.delivery / "offline-install-rehearsal"
        self.fake_bin = self.temp_path / "bin"
        self.fake_bin.mkdir()
        self.docker_log = self.temp_path / "docker.log"
        self.docker_state = self.temp_path / "docker-state.json"
        self.source_commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        self._write_delivery_fixture()
        self._write_fake_docker()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_formal_input_reads_use_one_stable_file_descriptor(self) -> None:
        source = PRODUCER.read_text(encoding="utf-8")

        self.assertIn("def read_regular_bytes", source)
        self.assertNotIn("path.read_text(encoding=\"utf-8\")", source)
        self.assertNotIn("sidecar.read_text(encoding=\"ascii\")", source)
        self.assertNotIn("manifest.read_bytes()", source)

    def test_stable_reader_rejects_path_replacement_between_check_and_open(self) -> None:
        producer = load_module(PRODUCER, "taiji_offline_stable_reader_test")
        target = self.temp_path / "stable-input.json"
        replacement = self.temp_path / "replacement.json"
        original = self.temp_path / "original.json"
        target.write_text('{"trusted":true}\n', encoding="utf-8")
        replacement.write_text('{"trusted":false}\n', encoding="utf-8")
        real_open = os.open

        def replace_then_open(path, flags, *args, **kwargs):
            if Path(path) == target:
                os.replace(target, original)
                os.replace(replacement, target)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(producer.os, "open", side_effect=replace_then_open):
            with self.assertRaisesRegex(producer.ProducerError, "打开时发生替换"):
                producer.read_regular_bytes(target, "stable input")

    def test_previous_deb_archive_copy_streams_large_file_without_read_bytes(self) -> None:
        producer = load_module(PRODUCER, "taiji_offline_large_previous_archive_copy")
        source = self.temp_path / "taiji-agent_0.0.9_amd64.deb"
        with source.open("wb") as handle:
            handle.seek(40 * 1024 * 1024 - 1)
            handle.write(b"\0")
        destination = self.temp_path / "archived-previous.deb"
        expected = producer.sha256_file(source)
        resolved_source = source.resolve()
        original_read_bytes = Path.read_bytes

        def reject_whole_previous_read(path: Path) -> bytes:
            if path == resolved_source:
                raise AssertionError("previous DEB must not be read into one bytes object")
            return original_read_bytes(path)

        with mock.patch.object(Path, "read_bytes", reject_whole_previous_read):
            observed = producer.copy_new_evidence_file(source, destination)

        self.assertEqual(observed, expected)
        self.assertEqual(destination.stat().st_size, 40 * 1024 * 1024)
        self.assertEqual(producer.sha256_file(destination), expected)

    def _write_delivery_fixture(self) -> None:
        package_dir = self.delivery / "生成的安装包"
        package_dir.mkdir(parents=True)
        source_archive = self.delivery / f"taiji-agentv1.0-kylin-build-src-{self.source_commit}.tar.gz"
        lock_payload = b"version = 1\n"
        lock_member = tarfile.TarInfo(
            "taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/uv.lock"
        )
        lock_member.size = len(lock_payload)
        lock_member.mode = 0o644
        with tarfile.open(source_archive, "w:gz") as archive:
            archive.addfile(lock_member, io.BytesIO(lock_payload))
        source_inventory = self.delivery / (
            f"taiji-agentv1.0-kylin-build-src-{self.source_commit}.inventory.json"
        )
        inventory_result = subprocess.run(
            [
                sys.executable,
                str(SOURCE_INTEGRITY_HELPER),
                "create",
                "--archive",
                str(source_archive),
                "--inventory",
                str(source_inventory),
                "--source-commit",
                self.source_commit,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(inventory_result.returncode, 0, inventory_result.stderr)
        shutil.copy2(SOURCE_INTEGRITY_HELPER, self.delivery / SOURCE_INTEGRITY_HELPER.name)
        deb = package_dir / "taiji-agent_0.1.0_amd64.deb"
        deb.write_bytes(b"deb fixture\n")
        checksum = package_dir / f"{deb.name}.sha256"
        checksum.write_text(f"{sha256(deb)}  {deb.name}\n", encoding="utf-8")
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        policy_helper = load_module(POLICY_HELPER, "taiji_offline_rehearsal_policy_test")
        policy = policy_helper.load_and_validate(POLICY)
        self.policy_id = policy["policy_id"]
        self.policy_sha256 = policy_helper.canonical_sha256(policy)
        manifest = package_dir / "taiji-package-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "taiji-package-manifest/v3",
                    "package": "taiji-agent",
                    "version": "0.1.0",
                    "architecture": "amd64",
                    "source_commit": self.source_commit,
                    "source_archive_basename": source_archive.name,
                    "source_archive_sha256": sha256(source_archive),
                    "source_inventory_basename": source_inventory.name,
                    "source_inventory_sha256": sha256(source_inventory),
                    "deb_basename": deb.name,
                    "deb_sha256": sha256(deb),
                    "compatibility_policy_id": self.policy_id,
                    "compatibility_policy_sha256": self.policy_sha256,
                    "elf_abi_audit_basename": "elf-abi-audit.json",
                    "elf_abi_audit_sha256": "a" * 64,
                    "icon_set_sha256": "1" * 64,
                    "desktop_entry_sha256": "d" * 64,
                    "acceptance_binding_sha256": "2" * 64,
                    "acceptance_tools_manifest_sha256": "3" * 64,
                    "acceptance_entrypoint_sha256": "4" * 64,
                    "installed_release_manifest_sha256": "5" * 64,
                    "maintainer": "Taiji Agent Product Team <noreply@localhost>",
                    "built_at_utc": generated_at,
                    **TOOLCHAIN,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (package_dir / ".build-success").write_text(
            "\n".join(
                (
                    "version=0.1.0",
                    f"source_archive={source_archive.name}",
                    f"source_sha256={sha256(source_archive)}",
                    f"source_commit={self.source_commit}",
                    f"source_inventory={source_inventory.name}",
                    f"source_inventory_sha256={sha256(source_inventory)}",
                    f"deb={deb.name}",
                    f"deb_sha256={sha256(deb)}",
                    f"checksum={checksum.name}",
                    f"built_at_utc={generated_at}",
                    f"manifest={manifest.name}",
                    f"compatibility_policy_id={self.policy_id}",
                    f"compatibility_policy_sha256={self.policy_sha256}",
                    f"elf_abi_audit_sha256={'a' * 64}",
                    f"icon_set_sha256={'1' * 64}",
                    f"acceptance_binding_sha256={'2' * 64}",
                    f"acceptance_tools_manifest_sha256={'3' * 64}",
                    f"acceptance_entrypoint_sha256={'4' * 64}",
                    f"installed_release_manifest_sha256={'5' * 64}",
                    "maintainer=Taiji Agent Product Team <noreply@localhost>",
                    *(f"{key}={value}" for key, value in TOOLCHAIN.items()),
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (package_dir / "构建报告.txt").write_text("build report\n", encoding="utf-8")
        for filename in (
            "00_制包机_生成离线交付包.sh",
            "01_制包机_发布预检.sh",
            "02_目标终端_安装并验证.sh",
            "03_目标终端_导出诊断报告.sh",
            "04_目标终端_桌面App验收并导出证据.sh",
            "99_本机_准备制包输入包.sh",
        ):
            write_executable(self.delivery / filename, "#!/usr/bin/env bash\nexit 0\n")
        acceptance_tools = self.delivery / "验收工具"
        acceptance_tools.mkdir()
        (acceptance_tools / "run-installed-electron-acceptance.js").write_text(
            "// fixture desktop acceptance driver\n", encoding="utf-8"
        )
        (acceptance_tools / "assemble-target-evidence.py").write_text(
            "# fixture target evidence assembler\n", encoding="utf-8"
        )
        (acceptance_tools / "observe-single-deb-install.py").write_text(
            "# fixture pre-install observer\n", encoding="utf-8"
        )
        (acceptance_tools / "certification-matrix.json").write_text(
            "{\"schema\":\"taiji-linux-certification-matrix/v2\"}\n", encoding="utf-8"
        )
        (acceptance_tools / "assemble-taiji-certification-set.py").write_text(
            "# fixture certification set assembler\n", encoding="utf-8"
        )
        (acceptance_tools / "validate-taiji-release-evidence.py").write_text(
            "# fixture release evidence validator\n", encoding="utf-8"
        )
        (acceptance_tools / "taiji-challenge-envelope.py").write_text(
            "# fixture challenge envelope helper\n", encoding="utf-8"
        )
        (acceptance_tools / "signing-public.pem").write_text(
            "fixture release public key\n", encoding="utf-8"
        )
        (self.delivery / "SHA256SUMS.txt").write_text(
            f"{sha256(source_archive)}  {source_archive.name}\n"
            f"{sha256(source_inventory)}  {source_inventory.name}\n",
            encoding="utf-8",
        )
        (self.delivery / "操作说明.md").write_text("instructions\n", encoding="utf-8")
        (self.delivery / "版本信息.txt").write_text("0.1.0\n", encoding="utf-8")

    def _write_fake_docker(self) -> None:
        write_executable(
            self.fake_bin / "docker",
            r'''
            #!/usr/bin/env python3
            import json
            import os
            import sys
            from datetime import datetime, timezone
            from pathlib import Path

            args = sys.argv[1:]
            log = Path(os.environ["FAKE_DOCKER_LOG"])
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(args, ensure_ascii=False) + "\n")
            state_path = Path(os.environ["FAKE_DOCKER_STATE"])
            mode = os.environ.get("FAKE_DOCKER_MODE", "success")

            if args[:2] == ["image", "inspect"]:
                architecture = "arm64" if mode == "wrong_arch" else "amd64"
                print(json.dumps([{
                    "Id": "sha256:expected-image",
                    "Architecture": architecture,
                    "Os": "linux",
                    "Config": {
                        "Entrypoint": ["/usr/local/bin/run-lifecycle.sh"],
                        "Labels": {
                            "io.taiji.release-evidence.role": (
                                "wrong-role" if mode == "wrong_profile" else "offline-rehearsal-v1"
                            ),
                            "io.taiji.release-evidence.baseline": (
                                "debian-13" if mode == "wrong_baseline" else "ubuntu-20.04"
                            ),
                            "io.taiji.release-evidence.fixture": (
                                "wrong-fixture"
                                if mode == "wrong_fixture"
                                else "kylin-os-release-v1"
                            ),
                        },
                    },
                }]))
                raise SystemExit(0)

            if args and args[0] == "create":
                mounts = []
                env = {}
                image = args[-1]
                index = 1
                while index < len(args) - 1:
                    value = args[index]
                    if value == "--mount":
                        spec = args[index + 1]
                        fields = {}
                        for item in spec.split(","):
                            if "=" in item:
                                key, field_value = item.split("=", 1)
                                fields[key] = field_value
                            else:
                                fields[item] = True
                        mounts.append(fields)
                        index += 2
                        continue
                    if value == "--env":
                        key, field_value = args[index + 1].split("=", 1)
                        env[key] = field_value
                        index += 2
                        continue
                    index += 1
                state_path.write_text(json.dumps({
                    "mounts": mounts,
                    "env": env,
                    "image": image,
                    "exit_code": 0,
                }), encoding="utf-8")
                print("fake-container-id")
                raise SystemExit(0)

            if args and args[0] == "inspect":
                state = json.loads(state_path.read_text(encoding="utf-8"))
                inspect_mounts = []
                for mount in state["mounts"]:
                    destination = mount.get("dst") or mount.get("destination")
                    read_only = bool(mount.get("readonly"))
                    if mode == "writable_delivery" and destination == "/delivery-ro":
                        read_only = False
                    inspect_mounts.append({
                        "Type": "bind",
                        "Source": mount.get("src") or mount.get("source"),
                        "Destination": destination,
                        "RW": not read_only,
                    })
                if mode == "socket_mount":
                    inspect_mounts.append({
                        "Type": "bind",
                        "Source": "/var/run/docker.sock",
                        "Destination": "/var/run/docker.sock",
                        "RW": True,
                    })
                image_id = "sha256:other-image" if mode == "wrong_image" else "sha256:expected-image"
                network = "bridge" if mode == "network_bridge" else "none"
                print(json.dumps([{
                    "Id": "fake-container-id",
                    "Image": image_id,
                    "HostConfig": {"NetworkMode": network},
                    "Mounts": inspect_mounts,
                    "State": {"ExitCode": state.get("exit_code", 0)},
                }]))
                raise SystemExit(0)

            if args[:2] == ["start", "--attach"]:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if mode == "lifecycle_failure":
                    state["exit_code"] = 23
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    print("fake lifecycle failed", file=sys.stderr)
                    raise SystemExit(23)
                evidence_mount = next(
                    item for item in state["mounts"]
                    if (item.get("dst") or item.get("destination")) == "/evidence"
                )
                evidence_dir = Path(evidence_mount.get("src") or evidence_mount.get("source"))
                checks = {"install": True, "uninstall": True, "reinstall": True}
                if mode == "false_check":
                    checks["uninstall"] = False
                session = {
                    "schema": "taiji.offline-install-rehearsal.v1",
                    "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "rehearsal_session_id": "1" * 32,
                    "challenge_nonce": state["env"]["TAIJI_OFFLINE_REHEARSAL_CHALLENGE"],
                    "source_commit": state["env"]["TAIJI_EXPECTED_SOURCE_COMMIT"],
                    "deb_basename": state["env"]["TAIJI_EXPECTED_DEB_BASENAME"],
                    "deb_sha256": state["env"]["TAIJI_EXPECTED_DEB_SHA256"],
                    "platform": "linux/amd64",
                    "environment": "container-kylin-policy-fixture-v1",
                    "os_id": "debian" if mode == "wrong_runtime_os" else "ubuntu",
                    "os_version": "13" if mode == "wrong_runtime_os" else "20.04",
                    "network": "none",
                    "checks": checks,
                    "desktop_app_verified": False,
                    "target_verified": False,
                }
                (evidence_dir / "offline-install-rehearsal-session.json").write_text(
                    json.dumps(session, sort_keys=True) + "\n", encoding="utf-8"
                )
                if mode == "expanded_success":
                    lifecycle = dict(session)
                    lifecycle.update({
                        "previous_deb_basename": state["env"]["TAIJI_EXPECTED_PREVIOUS_DEB_BASENAME"],
                        "previous_deb_sha256": state["env"]["TAIJI_EXPECTED_PREVIOUS_DEB_SHA256"],
                        "previous_version": state["env"]["TAIJI_EXPECTED_PREVIOUS_VERSION"],
                        "previous_signature_basename": state["env"]["TAIJI_EXPECTED_PREVIOUS_SIGNATURE_BASENAME"],
                        "previous_signature_sha256": state["env"]["TAIJI_EXPECTED_PREVIOUS_SIGNATURE_SHA256"],
                        "previous_signature_verification": "PASS",
                        "steps": [
                            "fresh_install_n",
                            "same_version_reinstall_n",
                            "seed_n_minus_one",
                            "upgrade_n_minus_one_to_n",
                            "data_manifest_after_upgrade",
                            "inject_postinst_failure_same_candidate",
                            "automatic_rollback_to_n_minus_one",
                            "upgrade_n_again",
                            "remove_preserves_user_data",
                            "purge_clears_root_state_only",
                        ],
                        "receipts": [
                            {
                                "operation": operation,
                                "result": result,
                                "state": "committed",
                                "transaction_id": transaction_id,
                                "deb_sha256": state["env"]["TAIJI_EXPECTED_DEB_SHA256"],
                                "compatibility_policy_id": state["env"]["TAIJI_COMPATIBILITY_POLICY_ID"],
                                "compatibility_policy_sha256": state["env"]["TAIJI_COMPATIBILITY_POLICY_SHA256"],
                                "network": "none",
                            }
                            for operation, result, transaction_id in (
                                ("fresh_install", "installed", "fresh-n"),
                                ("reinstall", "reinstalled", "reinstall-n"),
                                ("upgrade", "upgraded", "upgrade-n"),
                                ("rollback", "rolled_back", "rollback-n"),
                                ("upgrade_again", "upgraded", "upgrade-again-n"),
                            )
                        ],
                        "data_manifests": {
                            "before_upgrade": "d" * 64,
                            "after_upgrade": "d" * 64,
                            "after_rollback": "d" * 64,
                            "after_remove": "d" * 64,
                            "after_purge": "d" * 64,
                        },
                        "compatibility_policy_id": state["env"]["TAIJI_COMPATIBILITY_POLICY_ID"],
                        "compatibility_policy_sha256": state["env"]["TAIJI_COMPATIBILITY_POLICY_SHA256"],
                        "journal": {
                            "upgrade_transaction_id": "upgrade-n",
                            "rollback_transaction_id": "rollback-n",
                            "second_upgrade_transaction_id": "upgrade-again-n",
                            "resume": "partial journal is never committed; manual_recovery_required is explicit",
                            "power_loss_resume_checked": True,
                            "partial_journal_treated_as_committed": False,
                            "partial_journal_result": "manual_recovery_required",
                            "manual_recovery_required": False,
                        },
                        "package_actions": [
                            {
                                "command": "dpkg --install",
                                "package": state["env"]["TAIJI_EXPECTED_DEB_BASENAME"],
                                "network": "none",
                                "download": False,
                            },
                            {
                                "command": "dpkg --remove",
                                "package": "taiji-agent",
                                "network": "none",
                                "download": False,
                            },
                            {
                                "command": "dpkg --purge",
                                "package": "taiji-agent",
                                "network": "none",
                                "download": False,
                            },
                        ],
                    })
                    (evidence_dir / "offline-install-rehearsal-lifecycle.json").write_text(
                        json.dumps(lifecycle, sort_keys=True) + "\n", encoding="utf-8"
                    )
                if mode == "tamper_delivery":
                    delivery_mount = next(
                        item for item in state["mounts"]
                        if (item.get("dst") or item.get("destination")) == "/delivery-ro"
                    )
                    delivery_dir = Path(delivery_mount.get("src") or delivery_mount.get("source"))
                    (delivery_dir / "版本信息.txt").write_text(
                        "tampered during rehearsal\n", encoding="utf-8"
                    )
                print("fake lifecycle ok")
                raise SystemExit(0)

            if args[:2] == ["rm", "--force"]:
                if mode == "cleanup_failure":
                    print("fake cleanup failed", file=sys.stderr)
                    raise SystemExit(44)
                raise SystemExit(0)

            print(f"unsupported fake docker args: {args}", file=sys.stderr)
            raise SystemExit(97)
            ''',
        )

    def run_producer(self, mode: str = "success") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "FAKE_DOCKER_LOG": str(self.docker_log),
                "FAKE_DOCKER_STATE": str(self.docker_state),
                "FAKE_DOCKER_MODE": mode,
            }
        )
        return subprocess.run(
            [
                "python3",
                str(PRODUCER),
                "--delivery-dir",
                str(self.delivery),
                "--output-dir",
                str(self.output),
                "--image",
                "taiji-offline-rehearsal:test",
                "--challenge",
                CHALLENGE,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_producer_explicit(
        self,
        mode: str = "expanded_success",
        *,
        previous: Path | None = None,
        previous_signature: Path | None = None,
        previous_manifest: Path | None = None,
        expected_public_key_fingerprint: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        previous = previous or self._write_previous_release()
        if previous_signature is None:
            if previous.is_file():
                previous_signature, public_key = self._write_previous_signature_fixture(previous)
            else:
                public_key = self._ensure_test_release_key()
                previous_signature = Path(f"{previous}.sig")
        else:
            public_key = self._ensure_test_release_key()
        previous_manifest = previous_manifest or previous.parent / "taiji-package-manifest.json"
        validator_runner = self.temp_path / "validate-offline-with-test-trust-root.py"
        validator_runner.write_text(
            textwrap.dedent(
                f'''\
                import importlib.util
                import os
                import pathlib
                import sys

                validator_path = pathlib.Path({str(VALIDATOR)!r})
                spec = importlib.util.spec_from_file_location("taiji_test_release_validator", validator_path)
                if spec is None or spec.loader is None:
                    raise SystemExit(97)
                _validator = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = _validator
                spec.loader.exec_module(_validator)
                _validator.PINNED_RELEASE_PUBLIC_KEY_PATH = pathlib.Path(os.environ["TEST_RELEASE_PUBLIC_KEY"])
                _validator.PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT = os.environ["TEST_RELEASE_PUBLIC_KEY_FINGERPRINT"]
                for _name in dir(_validator):
                    if not _name.startswith("__"):
                        globals()[_name] = getattr(_validator, _name)
                if __name__ == "__main__":
                    raise SystemExit(_validator.main())
                '''
            ),
            encoding="utf-8",
        )
        runner = self.temp_path / "run-offline-producer-with-test-trust-root.py"
        runner.write_text(
            textwrap.dedent(
                f'''\
                import importlib.util
                import os
                import pathlib
                import sys

                producer_path = pathlib.Path({str(PRODUCER)!r})
                spec = importlib.util.spec_from_file_location("taiji_test_offline_producer", producer_path)
                if spec is None or spec.loader is None:
                    raise SystemExit(97)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                module.RELEASE_PUBLIC_KEY = pathlib.Path(os.environ["TEST_RELEASE_PUBLIC_KEY"])
                module.PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT = os.environ["TEST_RELEASE_PUBLIC_KEY_FINGERPRINT"]
                module.VALIDATOR = pathlib.Path(os.environ["TEST_RELEASE_VALIDATOR"])
                sys.argv = [str(producer_path), *sys.argv[1:]]
                raise SystemExit(module.main())
                '''
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "FAKE_DOCKER_LOG": str(self.docker_log),
                "FAKE_DOCKER_STATE": str(self.docker_state),
                "FAKE_DOCKER_MODE": mode,
                "TEST_RELEASE_PUBLIC_KEY": str(public_key),
                "TEST_RELEASE_PUBLIC_KEY_FINGERPRINT": (
                    expected_public_key_fingerprint
                    or self._test_release_public_fingerprint()
                ),
                "TEST_RELEASE_VALIDATOR": str(validator_runner),
            }
        )
        package_dir = self.delivery / "生成的安装包"
        candidate = package_dir / "taiji-agent_0.1.0_amd64.deb"
        return subprocess.run(
            [
                "python3",
                str(runner),
                "--deb",
                str(candidate),
                "--previous-deb",
                str(previous),
                "--previous-signature",
                str(previous_signature),
                "--previous-manifest",
                str(previous_manifest),
                "--build-manifest",
                str(package_dir / "taiji-package-manifest.json"),
                "--policy",
                str(ROOT / "packaging/linux/compatibility-policy.json"),
                "--output-dir",
                str(self.output),
                "--image",
                "taiji-offline-rehearsal:test",
                "--challenge",
                CHALLENGE,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_current_offline_validator(
        self,
        *,
        evidence: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        package_dir = self.delivery / "生成的安装包"
        deb = package_dir / "taiji-agent_0.1.0_amd64.deb"
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "offline",
                "--evidence",
                str(evidence or (self.output / "offline-install-rehearsal.json")),
                "--source-commit",
                self.source_commit,
                "--deb",
                str(deb),
                "--checksum",
                str(package_dir / f"{deb.name}.sha256"),
                "--manifest",
                str(package_dir / "taiji-package-manifest.json"),
                "--build-marker",
                str(package_dir / ".build-success"),
                "--source-archive",
                str(
                    self.delivery
                    / f"taiji-agentv1.0-kylin-build-src-{self.source_commit}.tar.gz"
                ),
                "--delivery-dir",
                str(self.delivery),
                "--challenge",
                CHALLENGE,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def _write_previous_release(self, version: str = "0.0.9") -> Path:
        previous = self.temp_path / "previous" / f"taiji-agent_{version}_amd64.deb"
        previous.parent.mkdir(parents=True, exist_ok=True)
        previous.write_bytes(b"previous deb fixture\n")
        (previous.parent / f"{previous.name}.sha256").write_text(
            f"{sha256(previous)}  {previous.name}\n", encoding="utf-8"
        )
        policy_helper = load_module(POLICY_HELPER, "taiji_policy_previous_fixture")
        policy = policy_helper.load_and_validate(ROOT / "packaging/linux/compatibility-policy.json")
        (previous.parent / "taiji-package-manifest.json").write_text(
            json.dumps(
                {
                    "schema": "taiji-package-manifest/v3",
                    "package": "taiji-agent",
                    "version": version,
                    "architecture": "amd64",
                    "source_commit": "8" * 40,
                    "deb_basename": previous.name,
                    "deb_sha256": sha256(previous),
                    "compatibility_policy_id": policy["policy_id"],
                    "compatibility_policy_sha256": policy_helper.canonical_sha256(policy),
                    "upgrade_data_contract_id": "taiji-linux-upgrade-data-v1",
                    "upgrade_data_contract_sha256": sha256(
                        ROOT / "packaging/linux/upgrade-data-contract.json"
                    ),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return previous

    def _ensure_test_release_key(self) -> Path:
        private_key = self.temp_path / "test-release-private.pem"
        public_key = self.temp_path / "test-release-public.pem"
        if not private_key.exists():
            generated = subprocess.run(
                [
                    "openssl",
                    "genpkey",
                    "-algorithm",
                    "RSA",
                    "-pkeyopt",
                    "rsa_keygen_bits:2048",
                    "-out",
                    str(private_key),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            exported = subprocess.run(
                [
                    "openssl",
                    "pkey",
                    "-in",
                    str(private_key),
                    "-pubout",
                    "-out",
                    str(public_key),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
        return public_key

    def _write_previous_signature_fixture(self, previous: Path) -> tuple[Path, Path]:
        public_key = self._ensure_test_release_key()
        private_key = self.temp_path / "test-release-private.pem"
        signature = Path(f"{previous}.sig")
        signed = subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-sign",
                str(private_key),
                "-out",
                str(signature),
                str(previous),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(signed.returncode, 0, signed.stderr)
        return signature, public_key

    def _test_release_public_fingerprint(self) -> str:
        public_key = self._ensure_test_release_key()
        derived = subprocess.run(
            [
                "openssl",
                "pkey",
                "-pubin",
                "-in",
                str(public_key),
                "-outform",
                "DER",
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(derived.returncode, 0, derived.stderr.decode("utf-8", errors="replace"))
        return hashlib.sha256(derived.stdout).hexdigest()

    def docker_calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.docker_log.read_text(encoding="utf-8").splitlines()]

    def test_dedicated_image_and_lifecycle_execute_real_three_stage_flow(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")

        self.assertIn("FROM ubuntu:20.04", dockerfile)
        self.assertIn('test "$TARGETARCH" = "amd64"', dockerfile)
        self.assertIn("useradd", dockerfile)
        self.assertIn("sudoers.d", dockerfile)
        self.assertIn('io.taiji.release-evidence.role="offline-rehearsal-v1"', dockerfile)
        self.assertIn('io.taiji.release-evidence.baseline="ubuntu-20.04"', dockerfile)
        self.assertIn('io.taiji.release-evidence.fixture="kylin-os-release-v1"', dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/local/bin/run-lifecycle.sh"]', dockerfile)
        self.assertIn("verify_runtime_baseline", lifecycle)
        self.assertIn('[ "$runtime_id" = "ubuntu" ]', lifecycle)
        self.assertIn('[ "$runtime_version" = "20.04" ]', lifecycle)

        installer = 'TAIJI_ALLOW_HEADLESS_REHEARSAL=1'
        self.assertEqual(lifecycle.count(installer), 2)
        first_install = lifecycle.index(installer)
        purge = lifecycle.index("apt-get purge -y taiji-agent")
        second_install = lifecycle.index(installer, first_install + 1)
        self.assertLess(first_install, purge)
        self.assertLess(purge, second_install)
        self.assertIn("dpkg-query", lifecycle)
        self.assertIn('! -e /opt/taiji-agent', lifecycle)
        self.assertIn('"schema": "taiji.offline-install-rehearsal.v1"', lifecycle)
        self.assertNotIn("ONLINE_OK=1", lifecycle)

    def test_rehearsal_image_installs_only_the_policy_required_system_boundary(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        expected = {
            "libdbus-1.so.3": "libdbus-1-3",
            "libdrm.so.2": "libdrm2",
            "libgbm.so.1": "libgbm1",
            "libGL.so.1": "libgl1",
            "libEGL.so.1": "libegl1",
            "libGLX.so.0": "libglx0",
            "libgcrypt.so.20": "libgcrypt20",
            "libgnutls.so.30": "libgnutls30",
            "libgssapi_krb5.so.2": "libgssapi-krb5-2",
            "libmount.so.1": "libmount1",
            "libselinux.so.1": "libselinux1",
            "libudev.so.1": "libudev1",
            "libgcc_s.so.1": "libgcc-s1",
            "libstdc++.so.6": "libstdc++6",
            "libz.so.1": "zlib1g",
            "libcrypt.so.1": "libcrypt1",
        }
        mapping = {
            line.split("\t", 1)[0]: line.split("\t", 1)[1]
            for line in REQUIRED_SYSTEM_PACKAGES.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        policy = json.loads(POLICY.read_text(encoding="utf-8"))

        self.assertEqual(mapping, expected)
        self.assertEqual(set(mapping), set(policy["elf"]["required_system_sonames"]))
        self.assertIn("COPY ubuntu20-required-system-packages.tsv", dockerfile)
        self.assertIn("xargs -r apt-get install -y --no-install-recommends", dockerfile)
        self.assertIn("ctypes.CDLL", dockerfile)

    def test_lifecycle_activates_policy_fixture_only_after_real_baseline_and_network_checks(self):
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        main = lifecycle.index('[ "$EUID" -eq 0 ]')
        baseline = lifecycle.index("\nverify_runtime_baseline\n", main)
        network_none = lifecycle.index("\nverify_runtime_network_none\n", main)
        self.assertIn("\nactivate_kylin_policy_fixture\n", lifecycle[main:])
        fixture = lifecycle.index("\nactivate_kylin_policy_fixture\n", main)
        first_install = lifecycle.index('sudo -H -u "$REHEARSAL_USER"', main)

        self.assertLess(baseline, network_none)
        self.assertLess(network_none, fixture)
        self.assertLess(fixture, first_install)
        self.assertIn('EXPECTED_REHEARSAL_FIXTURE_ID="kylin-os-release-v1"', lifecycle)
        self.assertIn("ID=kylin", lifecycle)
        self.assertIn("/usr/share/xsessions", lifecycle)
        self.assertIn("0:644:1", lifecycle)

    def test_lifecycle_accepts_down_kernel_tunnels_but_rejects_usable_network(self):
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")

        self.assertIn("verify_runtime_network_none", lifecycle)
        self.assertIn("ip -o link show up", lifecycle)
        self.assertIn("ip -o addr show scope global", lifecycle)
        self.assertIn("ip -o route show table all", lifecycle)
        self.assertNotIn("find /sys/class/net", lifecycle)
        self.assertNotIn('[ "$network_nodes" = "lo " ]', lifecycle)

        definitions = lifecycle.split('[ "$EUID" -eq 0 ]', 1)[0]
        definitions_path = self.temp_path / "lifecycle-definitions.sh"
        definitions_path.write_text(definitions, encoding="utf-8")
        network_bin = self.temp_path / "network-bin"
        network_bin.mkdir()
        write_executable(
            network_bin / "ip",
            r'''
            #!/usr/bin/env bash
            set -eu
            mode="${FAKE_IP_MODE:?}"
            case "$*" in
              "-o link show up")
                printf '%s\n' '1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 state UNKNOWN'
                if [ "$mode" = "active-link" ]; then
                  printf '%s\n' '2: eth0: <BROADCAST,UP,LOWER_UP> mtu 1500 state UP'
                fi
                ;;
              "-o addr show scope global")
                if [ "$mode" = "global-address" ]; then
                  printf '%s\n' '2: eth0 inet 192.0.2.10/24 scope global eth0'
                fi
                ;;
              "-o route show table all")
                printf '%s\n' 'local 127.0.0.0/8 dev lo table local scope host'
                if [ "$mode" = "external-route" ]; then
                  printf '%s\n' 'default via 192.0.2.1 dev eth0'
                fi
                ;;
              *) exit 64 ;;
            esac
            ''',
        )
        command = f'source "{definitions_path}"; verify_runtime_network_none'
        base_env = {**os.environ, "PATH": f"{network_bin}:{os.environ['PATH']}"}

        down_tunnels = subprocess.run(
            ["bash", "-c", command],
            env={**base_env, "FAKE_IP_MODE": "down-tunnels"},
            text=True,
            capture_output=True,
        )
        self.assertEqual(down_tunnels.returncode, 0, down_tunnels.stderr)

        for mode, expected in (
            ("active-link", "启用的非 loopback 链路"),
            ("global-address", "全局 IP 地址"),
            ("external-route", "非 loopback route"),
        ):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    ["bash", "-c", command],
                    env={**base_env, "FAKE_IP_MODE": mode},
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_lifecycle_resolves_the_container_hostname_before_using_sudo(self):
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")

        self.assertIn("ensure_local_hostname_resolution", lifecycle)
        self.assertIn("getent hosts", lifecycle)
        self.assertIn("127.0.1.1", lifecycle)
        self.assertIn("{0,252}", lifecycle)
        main = lifecycle.index('[ "$EUID" -eq 0 ]')
        self.assertLess(
            lifecycle.index("\nensure_local_hostname_resolution\n", main),
            lifecycle.index('sudo -H -u "$REHEARSAL_USER"', main),
        )

    def test_success_uses_locked_down_docker_and_publishes_bound_evidence(self):
        self.assertFalse((self.delivery / "离线依赖").exists())
        result = self.run_producer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        session_path = self.output / "offline-install-rehearsal-session.json"
        evidence_path = self.output / "offline-install-rehearsal.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(session["challenge_nonce"], CHALLENGE)
        self.assertEqual(session["checks"], {"install": True, "uninstall": True, "reinstall": True})
        self.assertEqual(evidence["challenge_nonce"], CHALLENGE)
        self.assertEqual(evidence["schema"], "taiji.offline-install-rehearsal.v1")
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["version"], "0.1.0")
        self.assertEqual(evidence["architecture"], "amd64")
        self.assertEqual(evidence["compatibility_policy_id"], self.policy_id)
        self.assertEqual(evidence["compatibility_policy_sha256"], self.policy_sha256)
        self.assertEqual(evidence["environment"], "container-kylin-policy-fixture-v1")
        self.assertEqual(session["environment"], "container-kylin-policy-fixture-v1")
        validator = load_module(VALIDATOR, "taiji_offline_inventory_test")
        self.assertEqual(
            evidence["delivery_inventory_sha256"],
            validator.delivery_inventory_sha256(self.delivery),
        )
        self.assertEqual(
            evidence["checks"],
            {"install": "PASS", "uninstall": "PASS", "reinstall": "PASS"},
        )
        self.assertNotIn("schema_version", evidence)
        self.assertNotIn("release_artifacts_sha256", evidence)
        self.assertNotIn("target_baseline_profile_id", evidence)
        self.assertNotIn("target_baseline_sha256", evidence)
        self.assertEqual(evidence["log_sha256"], sha256(session_path))
        self.assertFalse(evidence["desktop_app_verified"])
        self.assertFalse(evidence["target_verified"])

        validation = self.run_current_offline_validator()
        self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
        self.assertIn("offline-rehearsal-valid", validation.stdout)

        assembler = load_module(CERTIFICATION_ASSEMBLER, "taiji_offline_rehearsal_assembler_test")
        with self.assertRaisesRegex(assembler.CertificationSetError, "完整 N-1"):
            assembler._validate_offline_evidence(
                evidence_path.parent,
                source_commit=self.source_commit,
                version="0.1.0",
                deb_basename=evidence["deb_basename"],
                deb_sha256=evidence["deb_sha256"],
                policy_id=self.policy_id,
                policy_sha256=self.policy_sha256,
            )

        calls = self.docker_calls()
        create = next(call for call in calls if call and call[0] == "create")
        self.assertEqual(create.count("create"), 1)
        self.assertEqual(create[1], "--platform")
        self.assertIn("--platform", create)
        self.assertIn("linux/amd64", create)
        self.assertIn("--pull=never", create)
        self.assertIn("--network", create)
        self.assertIn("none", create)
        joined = " ".join(create)
        self.assertIn("dst=/delivery-ro,readonly", joined)
        self.assertIn("dst=/evidence", joined)
        self.assertIn("TAIJI_REHEARSAL_FIXTURE_ID=kylin-os-release-v1", joined)
        self.assertNotIn("/var/run/docker.sock", joined)
        for forbidden in ("API_KEY", "PRIVATE_KEY", "LICENSE", "TOKEN"):
            self.assertNotIn(forbidden, joined)
        self.assertTrue(any(call[:2] == ["rm", "--force"] for call in calls))

    def test_current_offline_validator_rejects_target_baseline_fields(self):
        result = self.run_producer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence_path = self.output / "offline-install-rehearsal.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["target_baseline_sha256"] = "b" * 64
        evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")

        validation = self.run_current_offline_validator()

        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("target baseline", validation.stderr)

    def test_current_offline_validator_retains_generic_v1_read_compatibility(self):
        result = self.run_producer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        session_path = self.output / "offline-install-rehearsal-session.json"
        evidence_path = self.output / "offline-install-rehearsal.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        session["environment"] = "container"
        session["os_id"] = "debian"
        session["os_version"] = "12"
        session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["environment"] = "container"
        evidence["os_id"] = "debian"
        evidence["os_version"] = "12"
        evidence["log_sha256"] = sha256(session_path)
        evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")

        validation = self.run_current_offline_validator()

        self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)

    def test_current_offline_validator_rejects_tampered_bound_session_log(self):
        result = self.run_producer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        session_path = self.output / "offline-install-rehearsal-session.json"
        session_path.write_text(session_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

        validation = self.run_current_offline_validator()

        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("log_sha256", validation.stderr)

    def test_current_offline_validator_rejects_policy_binding_mismatch(self):
        result = self.run_producer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence_path = self.output / "offline-install-rehearsal.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["compatibility_policy_sha256"] = "0" * 64
        evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")

        validation = self.run_current_offline_validator()

        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("compatibility_policy_sha256", validation.stderr)

    def test_current_offline_validator_rejects_delivery_inventory_drift_after_rehearsal(self):
        result = self.run_producer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.delivery / "操作说明.md").write_text(
            "replaced after rehearsal\n",
            encoding="utf-8",
        )

        validation = self.run_current_offline_validator()

        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("delivery_inventory_sha256", validation.stderr)

    def test_v3_manifest_target_baseline_is_rejected_before_docker(self):
        manifest_path = self.delivery / "生成的安装包" / "taiji-package-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["target_baseline_profile_id"] = "legacy-profile"
        manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

        result = self.run_producer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("target baseline", result.stdout + result.stderr)
        self.assertFalse(self.docker_log.exists())

    def test_network_mode_mismatch_fails_before_start_and_publishes_nothing(self):
        result = self.run_producer("network_bridge")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NetworkMode", result.stdout + result.stderr)
        self.assertFalse(self.output.exists())
        calls = self.docker_calls()
        self.assertFalse(any(call and call[0] == "start" for call in calls))
        self.assertTrue(any(call[:2] == ["rm", "--force"] for call in calls))

    def test_writable_delivery_mount_or_wrong_image_fails_closed(self):
        for mode, expected in (
            ("writable_delivery", "只读"),
            ("wrong_image", "镜像"),
            ("wrong_profile", "专用离线演练镜像"),
            ("wrong_baseline", "兼容基线"),
            ("wrong_fixture", "policy fixture"),
            ("socket_mount", "未授权挂载"),
        ):
            with self.subTest(mode=mode):
                if self.docker_log.exists():
                    self.docker_log.unlink()
                if self.docker_state.exists():
                    self.docker_state.unlink()
                result = self.run_producer(mode)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout + result.stderr)
                self.assertFalse(self.output.exists())

    def test_lifecycle_failure_or_false_session_check_never_publishes(self):
        for mode in (
            "lifecycle_failure",
            "false_check",
            "wrong_runtime_os",
            "tamper_delivery",
            "cleanup_failure",
        ):
            with self.subTest(mode=mode):
                if self.docker_log.exists():
                    self.docker_log.unlink()
                if self.docker_state.exists():
                    self.docker_state.unlink()
                result = self.run_producer(mode)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.output.exists())
                self.assertTrue(any(call[:2] == ["rm", "--force"] for call in self.docker_calls()))

    def test_existing_evidence_directory_is_not_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("existing evidence\n", encoding="utf-8")

        result = self.run_producer()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "existing evidence\n")
        self.assertFalse(self.docker_log.exists())

    def test_lifecycle_runs_fresh_reinstall_upgrade_failed_rollback_and_second_upgrade(self):
        result = self.run_producer_explicit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads((self.output / "offline-install-rehearsal.json").read_text(encoding="utf-8"))
        self.assertEqual(
            evidence["steps"],
            [
                "fresh_install_n",
                "same_version_reinstall_n",
                "seed_n_minus_one",
                "upgrade_n_minus_one_to_n",
                "data_manifest_after_upgrade",
                "inject_postinst_failure_same_candidate",
                "automatic_rollback_to_n_minus_one",
                "upgrade_n_again",
                "remove_preserves_user_data",
                "purge_clears_root_state_only",
            ],
        )
        previous = self._write_previous_release()
        self.assertEqual(evidence["previous_deb_basename"], previous.name)
        self.assertEqual(evidence["previous_version"], "0.0.9")
        self.assertEqual(evidence["previous_deb_sha256"], sha256(previous))
        signature = Path(f"{previous}.sig")
        self.assertEqual(evidence["previous_signature_basename"], signature.name)
        self.assertEqual(evidence["previous_signature_sha256"], sha256(signature))
        self.assertEqual(evidence["previous_signature_verification"], "PASS")
        lifecycle_path = self.output / evidence["lifecycle_log_basename"]
        self.assertEqual(evidence["lifecycle_log_sha256"], sha256(lifecycle_path))
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        for key in (
            "previous_deb_basename",
            "previous_version",
            "previous_deb_sha256",
            "previous_signature_basename",
            "previous_signature_sha256",
            "previous_signature_verification",
        ):
            self.assertEqual(lifecycle[key], evidence[key])

    def test_expanded_evidence_archives_and_binds_previous_release_identity(self):
        previous = self._write_previous_release()
        previous_manifest = previous.parent / "taiji-package-manifest.json"
        result = self.run_producer_explicit(
            previous=previous,
            previous_manifest=previous_manifest,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads((self.output / "offline-install-rehearsal.json").read_text(encoding="utf-8"))
        identity = evidence["previous_release"]
        self.assertEqual(identity["source_commit"], "8" * 40)
        self.assertEqual(identity["version"], "0.0.9")
        self.assertEqual(identity["deb_sha256"], sha256(previous))
        self.assertEqual(identity["manifest_sha256"], sha256(previous_manifest))
        signature = Path(f"{previous}.sig")
        self.assertEqual(identity["signature_basename"], signature.name)
        self.assertEqual(identity["signature_sha256"], sha256(signature))
        self.assertEqual(identity["signature_verification"], "PASS")
        for key in (
            "deb_basename",
            "checksum_basename",
            "signature_basename",
            "manifest_basename",
        ):
            archived = self.output / identity[key]
            self.assertTrue(archived.is_file(), key)
            self.assertFalse(archived.is_symlink(), key)
        self.assertEqual((self.output / identity["signature_basename"]).read_bytes(), signature.read_bytes())

    def test_current_validator_rejects_tampered_archived_previous_release(self):
        result = self.run_producer_explicit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads(
            (self.output / "offline-install-rehearsal.json").read_text(encoding="utf-8")
        )
        archived_previous = self.output / evidence["previous_release"]["deb_basename"]
        archived_previous.write_bytes(archived_previous.read_bytes() + b"tampered\n")

        validation = self.run_current_offline_validator()

        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("deb_sha256", validation.stderr)

    def test_validator_rejects_self_consistent_but_forged_previous_signature(self):
        result = self.run_producer_explicit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence_path = self.output / "offline-install-rehearsal.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        identity = evidence["previous_release"]
        archived_signature = self.output / identity["signature_basename"]
        archived_signature.write_bytes(b"forged-but-rehashed-previous-signature\n")
        forged_sha = sha256(archived_signature)
        lifecycle_path = self.output / evidence["lifecycle_log_basename"]
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        lifecycle["previous_signature_sha256"] = forged_sha
        lifecycle_path.write_text(
            json.dumps(lifecycle, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        evidence["previous_signature_sha256"] = forged_sha
        identity["signature_sha256"] = forged_sha
        evidence["lifecycle_log_sha256"] = sha256(lifecycle_path)
        evidence_path.write_text(
            json.dumps(evidence, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        validator = load_module(VALIDATOR, "taiji_offline_forged_previous_signature")
        binding = validator.BuildBinding(
            source_commit=self.source_commit,
            version="0.1.0",
            architecture="amd64",
            deb_basename="taiji-agent_0.1.0_amd64.deb",
            deb_sha256=evidence["deb_sha256"],
            compatibility_policy_id=self.policy_id,
            compatibility_policy_sha256=self.policy_sha256,
            electron_executable_sha256="e" * 64,
            desktop_entry_sha256="d" * 64,
            delivery_inventory_sha256=evidence["delivery_inventory_sha256"],
        )
        public_key = self._ensure_test_release_key()
        with (
            mock.patch.object(validator, "PINNED_RELEASE_PUBLIC_KEY_PATH", public_key),
            mock.patch.object(
                validator,
                "PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT",
                self._test_release_public_fingerprint(),
            ),
            self.assertRaisesRegex(validator.EvidenceError, "detached signature|\u7b7e\u540d"),
        ):
            validator.validate_offline_evidence_v1(
                evidence,
                evidence_path,
                SimpleNamespace(
                    challenge=CHALLENGE,
                    source_commit=self.source_commit,
                    deb=Path(binding.deb_basename),
                ),
                binding,
            )

    def test_missing_previous_manifest_blocks_expanded_rehearsal(self):
        previous = self._write_previous_release()
        missing_manifest = previous.parent / "missing-manifest.json"
        result = self.run_producer_explicit(
            previous=previous,
            previous_manifest=missing_manifest,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("previous", (result.stdout + result.stderr).lower())
        self.assertFalse(self.output.exists())

    def test_postinst_failure_injection_uses_same_candidate_deb_bytes(self):
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        self.assertIn("dpkg-divert", lifecycle)
        self.assertIn("postinst", lifecycle)
        self.assertIn("TAIJI_EXPECTED_DEB_SHA256", lifecycle)
        self.assertIn("sha256sum", lifecycle)
        self.assertIn("candidate DEB 字节", lifecycle)

    def test_all_receipts_bind_same_candidate_sha_and_policy(self):
        result = self.run_producer_explicit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads((self.output / "offline-install-rehearsal.json").read_text(encoding="utf-8"))
        receipts = evidence["receipts"]
        self.assertGreaterEqual(len(receipts), 4)
        for receipt in receipts:
            self.assertEqual(receipt["deb_sha256"], evidence["deb_sha256"])
            self.assertEqual(receipt["compatibility_policy_id"], evidence["compatibility_policy_id"])
            self.assertEqual(receipt["compatibility_policy_sha256"], evidence["compatibility_policy_sha256"])

    def test_data_manifest_matches_before_upgrade_after_upgrade_and_after_rollback(self):
        result = self.run_producer_explicit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads((self.output / "offline-install-rehearsal.json").read_text(encoding="utf-8"))
        manifests = evidence["data_manifests"]
        self.assertEqual(manifests["before_upgrade"], manifests["after_upgrade"])
        self.assertEqual(manifests["before_upgrade"], manifests["after_rollback"])

    def test_network_none_and_no_download_are_enforced_for_every_package_action(self):
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        producer = PRODUCER.read_text(encoding="utf-8")
        self.assertIn('"--network",\n            "none"', producer)
        self.assertNotRegex(lifecycle, r"apt-get\s+(?:update|install|download|get)")
        self.assertNotIn("apt-get install -f", lifecycle)
        self.assertIn("dpkg --install", lifecycle)
        self.assertIn("dpkg --purge", lifecycle)

    def test_missing_previous_release_blocks_upgrade_rehearsal(self):
        missing = self.temp_path / "missing-previous.deb"
        result = self.run_producer_explicit(previous=missing)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("previous", (result.stdout + result.stderr).lower())
        self.assertFalse(self.output.exists())

    def test_explicit_lifecycle_contract_requires_external_previous_signature(self):
        producer = load_module(PRODUCER, "taiji_offline_signature_contract")

        parameters = inspect.signature(producer.prepare_explicit_inputs).parameters

        self.assertIn("previous_signature_arg", parameters)
        source = PRODUCER.read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--previous-signature"', source)
        self.assertIn("previous_signature_arg=args.previous_signature", source)

    def test_previous_detached_signature_is_verified_with_the_pinned_public_key(self):
        producer = load_module(PRODUCER, "taiji_offline_previous_signature_verifier")
        previous = self._write_previous_release()
        signature, public_key = self._write_previous_signature_fixture(previous)

        self.assertTrue(
            hasattr(producer, "verify_previous_detached_signature"),
            "producer must expose the fixed-public-key previous signature admission",
        )
        with (
            mock.patch.object(producer, "RELEASE_PUBLIC_KEY", public_key),
            mock.patch.object(
                producer,
                "PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT",
                self._test_release_public_fingerprint(),
                create=True,
            ),
        ):
            producer.verify_previous_detached_signature(previous, signature)
            signature.write_bytes(b"forged previous signature\n")
            with self.assertRaisesRegex(producer.ProducerError, "signature|\u7b7e\u540d"):
                producer.verify_previous_detached_signature(previous, signature)

    def test_previous_signature_verification_ignores_path_injected_fake_openssl(self):
        producer = load_module(PRODUCER, "taiji_offline_fake_openssl_admission")
        previous = self._write_previous_release()
        signature, public_key = self._write_previous_signature_fixture(previous)
        signature.write_bytes(b"forged previous signature\n")
        write_executable(
            self.fake_bin / "openssl",
            """
            #!/bin/sh
            exit 0
            """,
        )

        with (
            mock.patch.object(producer, "RELEASE_PUBLIC_KEY", public_key),
            mock.patch.object(
                producer,
                "PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT",
                self._test_release_public_fingerprint(),
                create=True,
            ),
            mock.patch.dict(
                os.environ,
                {"PATH": f"{self.fake_bin}:{os.environ['PATH']}"},
                clear=False,
            ),
            self.assertRaisesRegex(producer.ProducerError, "signature|\u7b7e\u540d"),
        ):
            producer.verify_previous_detached_signature(previous, signature)

    def test_openssl_admission_uses_fixed_root_managed_single_link_executable(self):
        producer = load_module(PRODUCER, "taiji_offline_fixed_openssl_contract")

        resolved = Path(producer.resolve_trusted_openssl())

        self.assertEqual(resolved, Path("/usr/bin/openssl").resolve(strict=True))
        for directory in (Path("/usr"), Path("/usr/bin")):
            metadata = directory.lstat()
            self.assertFalse(directory.is_symlink())
            self.assertEqual(metadata.st_uid, 0)
            self.assertEqual(metadata.st_mode & 0o022, 0)
        metadata = resolved.lstat()
        self.assertTrue(metadata.st_mode & 0o111)
        self.assertEqual(metadata.st_uid, 0)
        self.assertEqual(metadata.st_nlink, 1)
        self.assertEqual(metadata.st_mode & 0o022, 0)
        source = PRODUCER.read_text(encoding="utf-8")
        self.assertIn('TRUSTED_OPENSSL = Path("/usr/bin/openssl")', source)
        self.assertNotIn('shutil.which("openssl")', source)

    def test_tracked_release_public_key_matches_the_product_fingerprint(self):
        producer = load_module(PRODUCER, "taiji_offline_release_key_fingerprint_contract")

        derived = subprocess.run(
            [
                producer.resolve_trusted_openssl(),
                "pkey",
                "-pubin",
                "-in",
                str(producer.RELEASE_PUBLIC_KEY),
                "-outform",
                "DER",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(
            hashlib.sha256(derived.stdout).hexdigest(),
            "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da",
        )
        self.assertEqual(
            producer.PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT,
            "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da",
        )

    def test_replaced_release_key_and_matching_signature_stop_before_docker(self):
        previous = self._write_previous_release()
        attacker_signature, _attacker_public_key = self._write_previous_signature_fixture(previous)

        result = self.run_producer_explicit(
            previous=previous,
            previous_signature=attacker_signature,
            expected_public_key_fingerprint=(
                "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
            ),
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fingerprint", (result.stdout + result.stderr).lower())
        self.assertFalse(self.docker_log.exists())
        self.assertFalse(self.output.exists())

    def test_missing_or_forged_previous_signature_stops_before_docker(self):
        previous = self._write_previous_release()
        valid_signature, _public_key = self._write_previous_signature_fixture(previous)
        missing_signature = previous.parent / "missing-previous.deb.sig"

        for label, signature in (
            ("missing", missing_signature),
            ("forged", valid_signature),
        ):
            with self.subTest(case=label):
                if signature == valid_signature:
                    signature.write_bytes(b"forged previous detached signature\n")
                result = self.run_producer_explicit(
                    previous=previous,
                    previous_signature=signature,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("signature", (result.stdout + result.stderr).lower())
                self.assertFalse(self.docker_log.exists())
                self.assertFalse(self.output.exists())

    def test_expanded_container_binds_external_signature_and_tracked_admission_inputs(self):
        producer = load_module(PRODUCER, "taiji_offline_signature_container_binding")
        previous_deb = self._write_previous_release()
        signature, public_key = self._write_previous_signature_fixture(previous_deb)
        previous = {
            "deb": previous_deb,
            "sha256": sha256(previous_deb),
            "checksum": Path(f"{previous_deb}.sha256"),
            "signature": signature,
            "signature_sha256": sha256(signature),
            "manifest": previous_deb.parent / "taiji-package-manifest.json",
            "version": "0.0.9",
        }
        package_dir = self.delivery / "生成的安装包"
        candidate = package_dir / "taiji-agent_0.1.0_amd64.deb"
        release = {
            "source_commit": self.source_commit,
            "deb": candidate,
            "deb_sha256": sha256(candidate),
            "version": "0.1.0",
        }
        policy = {"id": self.policy_id, "sha256": self.policy_sha256}
        evidence_dir = self.temp_path / "direct-container-evidence"
        evidence_dir.mkdir()
        fake_env = {
            "PATH": f"{self.fake_bin}:{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(self.docker_log),
            "FAKE_DOCKER_STATE": str(self.docker_state),
            "FAKE_DOCKER_MODE": "expanded_success",
        }

        with (
            mock.patch.object(producer, "RELEASE_PUBLIC_KEY", public_key),
            mock.patch.dict(os.environ, fake_env, clear=False),
            producer.staged_expanded_delivery(self.delivery, previous, POLICY) as staged,
        ):
            staged_inputs = staged / ".rehearsal-inputs"
            staged_signature = staged_inputs / "previous" / signature.name
            self.assertEqual(staged_signature.read_bytes(), signature.read_bytes())
            self.assertEqual(
                (staged_inputs / "signing-public.pem").read_bytes(),
                public_key.read_bytes(),
            )
            self.assertTrue((staged_inputs / "taiji-silent-deploy.sh").is_file())
            producer.run_lifecycle_container(
                docker=str(self.fake_bin / "docker"),
                image="taiji-offline-rehearsal:test",
                delivery=staged.resolve(),
                evidence_dir=evidence_dir.resolve(),
                challenge=CHALLENGE,
                release=release,
                previous=previous,
                policy=policy,
                expanded=True,
            )

        state = json.loads(self.docker_state.read_text(encoding="utf-8"))
        container_env = state["env"]
        self.assertEqual(
            container_env["TAIJI_EXPECTED_PREVIOUS_SIGNATURE_BASENAME"],
            signature.name,
        )
        self.assertEqual(
            container_env["TAIJI_EXPECTED_PREVIOUS_SIGNATURE_SHA256"],
            sha256(signature),
        )
        self.assertEqual(
            container_env["TAIJI_PREVIOUS_SIGNATURE_RELATIVE"],
            f".rehearsal-inputs/previous/{signature.name}",
        )
        self.assertEqual(
            container_env["TAIJI_EXPECTED_RELEASE_PUBLIC_KEY_SHA256"],
            sha256(public_key),
        )
        self.assertEqual(
            container_env["TAIJI_SILENT_DEPLOY_RELATIVE"],
            ".rehearsal-inputs/taiji-silent-deploy.sh",
        )

    def test_expanded_lifecycle_uses_real_silent_deploy_admission_and_never_forges_signature(self):
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        producer = PRODUCER.read_text(encoding="utf-8")

        self.assertNotIn("offline rehearsal previous signature", lifecycle)
        self.assertNotIn("transaction.run_upgrade(", lifecycle)
        self.assertIn("taiji-silent-deploy.sh", lifecycle)
        self.assertIn("--previous-signature", lifecycle)
        self.assertIn("TAIJI_RELEASE_PUBLIC_KEY=", lifecycle)
        self.assertIn(
            'RELEASE_PUBLIC_KEY = ROOT / "tools" / "taiji-release-evidence" / "signing-public.pem"',
            producer,
        )

    def test_previous_release_must_be_strictly_older_under_debian_version_rules(self):
        producer = load_module(PRODUCER, "taiji_offline_previous_version_contract")
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        producer_source = PRODUCER.read_text(encoding="utf-8")
        self.assertIn('dpkg-deb -f "$candidate" Version', lifecycle)
        self.assertIn('dpkg-deb -f "$previous" Version', lifecycle)
        self.assertIn("TAIJI_EXPECTED_CANDIDATE_VERSION", producer_source)
        self.assertIn("TAIJI_EXPECTED_PREVIOUS_VERSION", lifecycle)
        comparisons = (
            ("0.0.9", "0.1.0", -1),
            ("0.1.0", "0.1.0", 0),
            ("9.9.9", "0.1.0", 1),
            ("9.9.9", "1:0.1.0", -1),
            ("1:0.1.0", "0.1.0", 1),
            ("0.1.0-1", "0.1.0-2", -1),
            ("0.1.0-2", "0.1.0-1", 1),
            ("0.1.0~rc1", "0.1.0", -1),
        )
        for previous, candidate, expected in comparisons:
            with self.subTest(previous=previous, candidate=candidate):
                actual = producer.compare_debian_versions(previous, candidate)
                self.assertEqual((actual > 0) - (actual < 0), expected)

        newer = self._write_previous_release("9.9.9")
        result = self.run_producer_explicit(previous=newer)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("strictly older", (result.stdout + result.stderr).lower())
        self.assertFalse(self.output.exists())

    def test_power_loss_resume_never_treats_partial_journal_as_committed(self):
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        transaction = (ROOT / "packaging/linux/upgrade_transaction.py").read_text(encoding="utf-8")
        self.assertIn("journal", lifecycle)
        self.assertIn("manual_recovery_required", lifecycle)
        self.assertIn("resume", transaction)
        self.assertIn("committed", transaction)


class OfflineRehearsalDocumentationTest(unittest.TestCase):
    def test_docs_show_executable_offline_evidence_producer_flow(self):
        required_snippets = (
            "docker build --platform linux/amd64",
            "-t taiji-offline-rehearsal:local",
            "tools/taiji-offline-rehearsal",
            "python3 scripts/produce-taiji-offline-rehearsal.py",
            '--delivery-dir "taijiagent 打包交付"',
            '--output-dir "taijiagent 打包交付/offline-install-rehearsal"',
            "--image taiji-offline-rehearsal:local",
            '--challenge "$TAIJI_OFFLINE_REHEARSAL_CHALLENGE"',
            "输出目录必须不存在",
            "容器运行时强制使用 `--network none`",
            "仅证明离线安装生命周期",
            "不能替代真实 Electron 桌面 App 验收",
        )

        readiness = SALE_READINESS.read_text(encoding="utf-8")
        self.assertIn("完整操作步骤以", readiness)
        self.assertIn("runbooks/taiji-kylin-uos-offline-delivery.md", readiness)
        document = DELIVERY_GUIDE.read_text(encoding="utf-8")
        for snippet in required_snippets:
            self.assertIn(snippet, document)

    def test_final_release_gate_reuses_original_challenges(self):
        document = DELIVERY_GUIDE.read_text(encoding="utf-8")
        final_gate = document.split("## 最终销售发布", 1)[1].split("## 第五步", 1)[0]

        self.assertIn('<当轮认证集原值>', final_gate)
        self.assertIn('<当轮发布回执原值>', final_gate)
        self.assertIn("TAIJI_CERTIFICATION_CHALLENGE", final_gate)
        self.assertIn("TAIJI_PUBLICATION_CHALLENGE", final_gate)
        self.assertNotIn("openssl rand -hex 32", final_gate)


if __name__ == "__main__":
    unittest.main()
