import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / "scripts" / "produce-taiji-offline-rehearsal.py"
DOCKERFILE = ROOT / "tools" / "taiji-offline-rehearsal" / "Dockerfile"
LIFECYCLE = ROOT / "tools" / "taiji-offline-rehearsal" / "run-lifecycle.sh"
CHALLENGE = "ab" * 32
SALE_READINESS = ROOT / "docs" / "taiji-sale-readiness.md"
DELIVERY_GUIDE = ROOT / "taijiagent 打包交付" / "操作说明.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o755)


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
            ["git", "-C", str(ROOT), "rev-parse", "--short=8", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        self._write_delivery_fixture()
        self._write_fake_docker()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_delivery_fixture(self) -> None:
        package_dir = self.delivery / "生成的安装包"
        offline_repo = self.delivery / "离线依赖"
        package_dir.mkdir(parents=True)
        offline_repo.mkdir()
        source_archive = self.delivery / f"taiji-agentv1.0-kylin-build-src-{self.source_commit}.tar.gz"
        source_archive.write_bytes(b"source archive fixture\n")
        deb = package_dir / "taiji-agent_0.1.0_amd64.deb"
        deb.write_bytes(b"deb fixture\n")
        packages = offline_repo / "Packages"
        packages.write_bytes(b"packages fixture\n")
        packages_gz = offline_repo / "Packages.gz"
        packages_gz.write_bytes(b"packages gzip fixture\n")
        dependency = offline_repo / "dependency-fixture_1.0_amd64.deb"
        dependency.write_bytes(b"dependency fixture\n")
        checksum = package_dir / f"{deb.name}.sha256"
        checksum.write_text(f"{sha256(deb)}  {deb.name}\n", encoding="utf-8")
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        manifest = package_dir / "taiji-package-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "package": "taiji-agent",
                    "version": "0.1.0",
                    "build_arch": "x86_64",
                    "dpkg_arch": "amd64",
                    "deb": deb.name,
                    "deb_sha256": sha256(deb),
                    "checksum": checksum.name,
                    "source_archive": source_archive.name,
                    "source_commit": self.source_commit,
                    "source_sha256": sha256(source_archive),
                    "packages_sha256": sha256(packages),
                    "packages_gz_sha256": sha256(packages_gz),
                    "electron_executable_sha256": "e" * 64,
                    "desktop_entry_sha256": "d" * 64,
                    "built_at": generated_at,
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
                    f"deb={deb.name}",
                    f"deb_sha256={sha256(deb)}",
                    f"checksum={checksum.name}",
                    "built_at=2026-07-11T08:00:00+0800",
                    f"manifest={manifest.name}",
                    f"packages_sha256={sha256(packages)}",
                    f"packages_gz_sha256={sha256(packages_gz)}",
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
        (acceptance_tools / "validate-taiji-release-evidence.py").write_text(
            "# fixture release evidence validator\n", encoding="utf-8"
        )
        (acceptance_tools / "signing-public.pem").write_text(
            "fixture release public key\n", encoding="utf-8"
        )
        (self.delivery / "SHA256SUMS.txt").write_text(
            f"{sha256(source_archive)}  {source_archive.name}\n", encoding="utf-8"
        )
        (self.delivery / "操作说明.md").write_text("instructions\n", encoding="utf-8")
        (self.delivery / "版本信息.txt").write_text("0.1.0\n", encoding="utf-8")
        (offline_repo / "SHA256SUMS.txt").write_text("fixture inventory\n", encoding="utf-8")
        (offline_repo / "runtime-dependencies.txt").write_text(
            "dependency-fixture\n", encoding="utf-8"
        )

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
                    "environment": "container",
                    "os_id": "debian",
                    "os_version": "13",
                    "network": "none",
                    "checks": checks,
                    "desktop_app_verified": False,
                    "target_verified": False,
                }
                (evidence_dir / "offline-install-rehearsal-session.json").write_text(
                    json.dumps(session, sort_keys=True) + "\n", encoding="utf-8"
                )
                if mode == "tamper_delivery":
                    delivery_mount = next(
                        item for item in state["mounts"]
                        if (item.get("dst") or item.get("destination")) == "/delivery-ro"
                    )
                    delivery_dir = Path(delivery_mount.get("src") or delivery_mount.get("source"))
                    (delivery_dir / "版本信息.txt").write_text("tampered during rehearsal\n", encoding="utf-8")
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

    def docker_calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.docker_log.read_text(encoding="utf-8").splitlines()]

    def test_dedicated_image_and_lifecycle_execute_real_three_stage_flow(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")

        self.assertIn("FROM debian:13-slim", dockerfile)
        self.assertIn('test "$TARGETARCH" = "amd64"', dockerfile)
        self.assertIn("useradd", dockerfile)
        self.assertIn("sudoers.d", dockerfile)
        self.assertIn('io.taiji.release-evidence.role="offline-rehearsal-v1"', dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/local/bin/run-lifecycle.sh"]', dockerfile)

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

    def test_success_uses_locked_down_docker_and_publishes_bound_evidence(self):
        result = self.run_producer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        session_path = self.output / "offline-install-rehearsal-session.json"
        evidence_path = self.output / "offline-install-rehearsal.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(session["challenge_nonce"], CHALLENGE)
        self.assertEqual(session["checks"], {"install": True, "uninstall": True, "reinstall": True})
        self.assertEqual(evidence["challenge_nonce"], CHALLENGE)
        self.assertEqual(evidence["log_sha256"], sha256(session_path))
        self.assertFalse(evidence["desktop_app_verified"])
        self.assertFalse(evidence["target_verified"])

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
        self.assertNotIn("/var/run/docker.sock", joined)
        for forbidden in ("API_KEY", "PRIVATE_KEY", "LICENSE", "TOKEN"):
            self.assertNotIn(forbidden, joined)
        self.assertTrue(any(call[:2] == ["rm", "--force"] for call in calls))

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
        for mode in ("lifecycle_failure", "false_check", "tamper_delivery", "cleanup_failure"):
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

        for path in (SALE_READINESS, DELIVERY_GUIDE):
            with self.subTest(path=path):
                document = path.read_text(encoding="utf-8")
                for snippet in required_snippets:
                    self.assertIn(snippet, document)

    def test_final_release_gate_reuses_original_challenges(self):
        document = SALE_READINESS.read_text(encoding="utf-8")
        final_gate = document.split("## 一键门禁", 1)[1].split("离线生命周期演练证据目录默认是", 1)[0]

        self.assertIn('<本次断网演练时保留的原 challenge>', final_gate)
        self.assertIn('<本次真实桌面 App 验收时保留的原 challenge>', final_gate)
        self.assertIn("最终门禁只复用本次验收时保留的原 challenge", final_gate)
        self.assertIn("不得在最终门禁阶段重新执行 `openssl rand`", final_gate)
        self.assertNotIn("openssl rand -hex 32", final_gate)


if __name__ == "__main__":
    unittest.main()
