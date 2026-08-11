#!/usr/bin/env python3
"""Dynamic contract tests for the single-DEB install observation workflow."""

import hashlib
import importlib.util
import json
import os
import struct
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


TOOLS_DIR = Path(__file__).resolve().parent
OBSERVER = TOOLS_DIR / "observe-single-deb-install.py"
MATRIX = TOOLS_DIR.parents[1] / "packaging/linux/certification-matrix.json"


def png_fixture(width=800, height=600):
    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + (b"\x20\x80\xe0" * width)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def load_observer():
    spec = importlib.util.spec_from_file_location("taiji_single_deb_observer_test", OBSERVER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load single-DEB observer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completed(stdout="", returncode=0, stderr=""):
    return type(
        "Completed",
        (),
        {"stdout": stdout, "stderr": stderr, "returncode": returncode},
    )()


class FakeRuntime:
    def __init__(self, statuses, network_samples=None, machine_id="a" * 32, boot_id="b" * 32):
        self.statuses = list(statuses)
        self.network_samples = list(network_samples or [True] * max(1, len(statuses)))
        self.machine_id = machine_id
        self.boot_id = boot_id
        self.status_index = 0
        self.network_index = 0
        self.after_sleep = None
        self.elapsed = 0.0

    def package_status(self):
        index = min(self.status_index, len(self.statuses) - 1)
        self.status_index += 1
        return self.statuses[index]

    def network_is_offline(self):
        index = min(self.network_index, len(self.network_samples) - 1)
        self.network_index += 1
        return self.network_samples[index]

    def identity(self):
        return self.machine_id, self.boot_id

    def monotonic(self):
        return self.elapsed

    def utc_now(self):
        return datetime.now(timezone.utc)

    def sleep(self, seconds):
        self.elapsed += seconds
        if self.after_sleep is not None:
            callback, self.after_sleep = self.after_sleep, None
            callback()


class SingleDebInstallObserverTests(unittest.TestCase):
    def setUp(self):
        self.observer = load_observer()
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-single-deb-observer-")
        self.root = Path(self.temporary.name)
        self.customer = self.root / "candidate"
        self.customer.mkdir(mode=0o700)
        self.deb = self.customer / "taiji-agent_1.0.0_amd64.deb"
        self.deb.write_bytes(b"single-deb-candidate")
        self.challenge = "c" * 64
        self.manifest = self.root / "taiji-package-manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "source_commit": "d" * 40,
                    "version": "1.0.0",
                    "deb": "taiji-agent_1.0.0_amd64.deb",
                    "deb_sha256": hashlib.sha256(self.deb.read_bytes()).hexdigest(),
                    "target_baseline_profile_id": "kylin-v10-amd64-123456789abc",
                    "target_baseline_sha256": "e" * 64,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.user_paths = [self.root / "xdg-config", self.root / "xdg-data", self.root / "xdg-state"]

    def tearDown(self):
        self.temporary.cleanup()

    def observe(self, runtime):
        return self.observer.observe_install(
            customer_dir=self.customer,
            manifest_path=self.manifest,
            challenge=self.challenge,
            user_state_paths=self.user_paths,
            runtime=runtime,
            timeout_seconds=10,
            poll_interval_seconds=0.1,
        )

    def observe_canonical(self, challenge=None, runtime=None):
        canonical_manifest = self.root / "taiji-package-manifest-v3.json"
        canonical_manifest.write_text(
            json.dumps(
                {
                    "schema": "taiji-package-manifest/v3",
                    "package": "taiji-agent",
                    "version": "1.0.0",
                    "architecture": "amd64",
                    "source_commit": "d" * 40,
                    "deb_basename": self.deb.name,
                    "deb_sha256": hashlib.sha256(self.deb.read_bytes()).hexdigest(),
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": "e" * 64,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        platform_identity = {
            "os_id": "kylin",
            "os_version": "v10/2503",
            "desktop_environment": "UKUI",
            "security_facts": {
                "administrator_available": True,
                "business_data_mutation": False,
                "graphical_desktop": True,
                "network_observation": "continuous-process-sampling-no-non-loopback-up",
                "package_manager": "dpkg",
                "security_profile": "supported-default",
                "kysec_detected": False,
                "kysec_enabled": False,
                "kysec_exec_control": "not-present",
                "os_release_sha256": "a" * 64,
                "os_version_sha256": "not-present",
            },
        }
        return self.observer.observe_environment_install(
            customer_dir=self.customer,
            manifest_path=canonical_manifest,
            matrix_path=MATRIX,
            category_id="kylin-current-standard",
            challenge=challenge or self.challenge,
            user_state_paths=self.user_paths,
            runtime=runtime or FakeRuntime([None, "install ok installed"]),
            timeout_seconds=10,
            poll_interval_seconds=0.1,
            platform_identity=platform_identity,
        )

    def make_trusted_desktop_fixture(
        self,
        executable_name="ukui-session",
        scope="session-4.scope",
        fixture_root=None,
    ):
        root = self.root if fixture_root is None else Path(fixture_root)
        loginctl = root / "trusted-bin/loginctl"
        loginctl.parent.mkdir(mode=0o755)
        loginctl.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        loginctl.chmod(0o755)
        executable_root = root / "trusted-desktop"
        executable_root.mkdir(mode=0o755)
        executable = executable_root / executable_name
        executable.write_bytes(b"trusted desktop session executable")
        executable.chmod(0o755)
        proc_root = root / "proc"
        process = proc_root / "2295"
        process.mkdir(parents=True)
        (process / "cgroup").write_text(
            "0::/user.slice/user-1000.slice/%s\n" % scope,
            encoding="ascii",
        )
        stat_fields = ["S"] + (["0"] * 18) + ["123456"]
        (process / "stat").write_text(
            "2295 (%s) %s\n" % (executable_name, " ".join(stat_fields)),
            encoding="ascii",
        )
        (process / "status").write_text(
            "Name:\t%s\nUid:\t1000\t1000\t1000\t1000\n" % executable_name,
            encoding="ascii",
        )
        (process / "exe").symlink_to(executable)
        current_process = proc_root / "self"
        current_process.mkdir()
        (current_process / "cgroup").write_text(
            "0::/user.slice/user-1000.slice/%s\n" % scope,
            encoding="ascii",
        )
        return loginctl, proc_root, executable_root, executable

    @staticmethod
    def loginctl_runner(
        session_overrides=None,
        user_output="Display=4\nSessions=4\n",
        calls=None,
    ):
        session = {
            "User": "1000",
            "Seat": "seat0",
            "Display": ":0",
            "Remote": "no",
            "Desktop": "ukui",
            "Leader": "2295",
            "Type": "x11",
            "Class": "user",
            "Active": "yes",
            "State": "active",
            "Scope": "session-4.scope",
        }
        session.update(session_overrides or {})

        def run(argv, **kwargs):
            if calls is not None:
                calls.append((list(argv), dict(kwargs)))
            if argv[1:3] == ["show-user", "1000"]:
                return completed(user_output)
            if argv[1:3] == ["show-session", "4"]:
                return completed(
                    "".join("%s=%s\n" % (key, value) for key, value in session.items())
                )
            if len(argv) > 2 and argv[1] == "show-session":
                alternate = dict(session)
                alternate["Scope"] = "session-%s.scope" % argv[2]
                alternate["Leader"] = str(3000 + int(argv[2]))
                return completed(
                    "".join("%s=%s\n" % (key, value) for key, value in alternate.items())
                )
            return completed(returncode=1, stderr="unexpected invocation")

        return run

    def test_observes_absent_to_installed_transition_while_offline(self):
        payload = self.observe(FakeRuntime([None, "install ok unpacked", "install ok installed"]))

        self.assertEqual(payload["package_status_before"], "not-installed")
        self.assertEqual(payload["package_status_after"], "install ok installed")
        self.assertEqual(payload["network_observation"], "continuous-process-sampling-no-non-loopback-up")
        self.assertGreaterEqual(payload["network_sample_count"], 3)
        self.assertEqual(payload["candidate_file_count"], 1)
        self.assertFalse(payload["additional_install_files_observed"])
        self.assertTrue(payload["first_launch_eligible"])
        self.assertFalse(payload["installation_method_machine_observed"])

    def test_canonical_mode_emits_category_bound_environment_observation_without_baseline(self):
        canonical_manifest = self.root / "taiji-package-manifest-v3.json"
        canonical_manifest.write_text(
            json.dumps(
                {
                    "schema": "taiji-package-manifest/v3",
                    "package": "taiji-agent",
                    "version": "1.0.0",
                    "architecture": "amd64",
                    "source_commit": "d" * 40,
                    "deb_basename": self.deb.name,
                    "deb_sha256": hashlib.sha256(self.deb.read_bytes()).hexdigest(),
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": "e" * 64,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        platform_identity = {
            "os_id": "kylin",
            "os_version": "v10/2503",
            "desktop_environment": "UKUI",
            "security_facts": {
                "administrator_available": True,
                "business_data_mutation": False,
                "graphical_desktop": True,
                "network_observation": "continuous-process-sampling-no-non-loopback-up",
                "package_manager": "dpkg",
                "security_profile": "supported-default",
                "kysec_detected": False,
                "kysec_enabled": False,
                "kysec_exec_control": "not-present",
                "os_release_sha256": "a" * 64,
                "os_version_sha256": "not-present",
            },
        }
        observation, record = self.observer.observe_environment_install(
            customer_dir=self.customer,
            manifest_path=canonical_manifest,
            matrix_path=MATRIX,
            category_id="kylin-current-standard",
            challenge=self.challenge,
            user_state_paths=self.user_paths,
            runtime=FakeRuntime([None, "install ok installed"]),
            timeout_seconds=10,
            poll_interval_seconds=0.1,
            platform_identity=platform_identity,
        )
        self.assertEqual(observation["schema"], "taiji.single-deb-install-observation/v2")
        self.assertEqual(record["schema"], "taiji-linux-environment-observation/v1")
        self.assertEqual(record["compatibility"], "COMPATIBLE")
        self.assertEqual(record["category_id"], "kylin-current-standard")
        self.assertNotIn("target_baseline_profile_id", record)
        self.assertNotIn("CERTIFIED", json.dumps(record))
        expected_commitment = hashlib.sha256(
            ("taiji-machine-identity-v1\0" + "a" * 32).encode("utf-8")
        ).hexdigest()
        expected_fingerprint = hashlib.sha256(
            (self.challenge + "\0" + expected_commitment).encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            observation["machine_identity_commitment_sha256"],
            expected_commitment,
        )
        self.assertEqual(observation["machine_fingerprint_sha256"], expected_fingerprint)
        self.assertEqual(
            record["machine_identity_commitment_sha256"],
            expected_commitment,
        )

    def test_canonical_machine_commitment_is_stable_and_forgery_is_rejected(self):
        runtime = FakeRuntime([None, "install ok installed"])
        first, first_record = self.observe_canonical(runtime=runtime)
        second_challenge = "d" * 64
        second, second_record = self.observe_canonical(
            challenge=second_challenge,
            runtime=FakeRuntime([None, "install ok installed"]),
        )

        commitment = first["machine_identity_commitment_sha256"]
        self.assertEqual(commitment, second["machine_identity_commitment_sha256"])
        self.assertEqual(commitment, first_record["machine_identity_commitment_sha256"])
        self.assertEqual(commitment, second_record["machine_identity_commitment_sha256"])
        self.assertNotEqual(
            first["machine_fingerprint_sha256"],
            second["machine_fingerprint_sha256"],
        )
        self.assertEqual(
            second["machine_fingerprint_sha256"],
            hashlib.sha256(
                (second_challenge + "\0" + commitment).encode("utf-8")
            ).hexdigest(),
        )

        forged = dict(first)
        forged["machine_identity_commitment_sha256"] = "1" * 64
        forged["machine_fingerprint_sha256"] = hashlib.sha256(
            (self.challenge + "\0" + forged["machine_identity_commitment_sha256"]).encode(
                "utf-8"
            )
        ).hexdigest()
        old_positive_v2 = dict(first)
        old_positive_v2.pop("machine_identity_commitment_sha256")
        with self.assertRaisesRegex(self.observer.ObservationError, "field|commitment"):
            self.observer._validate_observation_identity(
                old_positive_v2,
                self.challenge,
                runtime,
                user_state_paths=self.user_paths,
                canonical=True,
            )
        with self.assertRaisesRegex(self.observer.ObservationError, "machine|commitment"):
            self.observer._validate_observation_identity(
                forged,
                self.challenge,
                runtime,
                user_state_paths=self.user_paths,
                canonical=True,
            )

    def test_canonical_records_never_serialize_raw_machine_id(self):
        machine_id = "0123456789abcdef0123456789abcdef"
        observation, record = self.observe_canonical(
            runtime=FakeRuntime(
                [None, "install ok installed"],
                machine_id=machine_id,
            )
        )

        serialized = json.dumps(
            {"observation": observation, "environment": record},
            ensure_ascii=False,
            sort_keys=True,
        )
        self.assertNotIn(machine_id, serialized)

    def test_trusted_desktop_probe_uses_fixed_loginctl_and_scope_process_not_forged_path(self):
        loginctl, proc_root, executable_root, _ = self.make_trusted_desktop_fixture()
        calls = []

        desktop = self.observer._probe_trusted_desktop_session(
            environment={
                "PATH": str(self.root / "attacker-bin"),
                "XDG_CURRENT_DESKTOP": "UKUI",
                "XDG_SESSION_TYPE": "x11",
            },
            loginctl_path=loginctl,
            proc_root=proc_root,
            current_process_cgroup_path=proc_root / "self/cgroup",
            trusted_executable_roots=(executable_root,),
            expected_owner_uid=os.getuid(),
            uid=1000,
            command_runner=self.loginctl_runner(calls=calls),
        )

        self.assertEqual(desktop, "UKUI")
        self.assertTrue(calls)
        self.assertTrue(all(call[0][0] == str(loginctl.resolve()) for call in calls))
        self.assertTrue(
            all(
                call[1]["env"]
                == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}
                for call in calls
            )
        )

    def test_trusted_desktop_probe_recognizes_each_certified_process_family(self):
        cases = (
            ("ukui-session", "ukui", "UKUI"),
            ("startdde", "dde", "DDE"),
            ("dde-session", "deepin", "DDE"),
            ("gnome-session-binary", "gnome", "GNOME"),
        )
        for executable_name, logind_desktop, expected in cases:
            with self.subTest(executable=executable_name):
                case_root = self.root / executable_name
                case_root.mkdir()
                loginctl, proc_root, executable_root, _ = self.make_trusted_desktop_fixture(
                    executable_name=executable_name,
                    fixture_root=case_root,
                )
                actual = self.observer._probe_trusted_desktop_session(
                    environment={"XDG_CURRENT_DESKTOP": logind_desktop, "XDG_SESSION_TYPE": "x11"},
                    loginctl_path=loginctl,
                    proc_root=proc_root,
                    current_process_cgroup_path=proc_root / "self/cgroup",
                    trusted_executable_roots=(executable_root,),
                    expected_owner_uid=os.getuid(),
                    uid=1000,
                    command_runner=self.loginctl_runner({"Desktop": logind_desktop}),
                )
                self.assertEqual(actual, expected)

    def test_trusted_desktop_probe_accepts_trusted_leader_and_separate_session_process(self):
        loginctl, proc_root, executable_root, _ = self.make_trusted_desktop_fixture()
        desktop_process = proc_root / "2295"
        desktop_process.rename(proc_root / "2390")
        (proc_root / "2390/stat").write_text(
            "2390 (ukui-session) %s\n"
            % " ".join(["S"] + (["0"] * 18) + ["223344"]),
            encoding="ascii",
        )
        leader_executable = executable_root / "systemd"
        leader_executable.write_bytes(b"trusted session leader")
        leader_executable.chmod(0o755)
        leader = proc_root / "2295"
        leader.mkdir()
        (leader / "cgroup").write_text(
            "2:cpu,cpuacct:/user.slice/user-1000.slice/session-4.scope\n"
            "0::/user.slice/user-1000.slice/session-4.scope\n",
            encoding="ascii",
        )
        (leader / "stat").write_text(
            "2295 (systemd) %s\n"
            % " ".join(["S"] + (["0"] * 18) + ["112233"]),
            encoding="ascii",
        )
        (leader / "status").write_text(
            "Name:\tsystemd\nUid:\t1000\t1000\t1000\t1000\n",
            encoding="ascii",
        )
        (leader / "exe").symlink_to(leader_executable)

        desktop = self.observer._probe_trusted_desktop_session(
            environment={"XDG_CURRENT_DESKTOP": "UKUI", "XDG_SESSION_TYPE": "x11"},
            loginctl_path=loginctl,
            proc_root=proc_root,
            current_process_cgroup_path=proc_root / "self/cgroup",
            trusted_executable_roots=(executable_root,),
            expected_owner_uid=os.getuid(),
            uid=1000,
            command_runner=self.loginctl_runner(),
        )

        self.assertEqual(desktop, "UKUI")

    def test_trusted_desktop_probe_rejects_executor_outside_selected_display_scope(self):
        loginctl, proc_root, executable_root, _ = self.make_trusted_desktop_fixture()
        foreign = self.root / "foreign-self-cgroup"
        foreign.write_text(
            "0::/user.slice/user-1000.slice/session-99.scope\n",
            encoding="ascii",
        )

        with self.assertRaisesRegex(self.observer.ObservationError, "current|executor|scope|session"):
            self.observer._probe_trusted_desktop_session(
                environment={"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "UKUI"},
                loginctl_path=loginctl,
                proc_root=proc_root,
                current_process_cgroup_path=foreign,
                trusted_executable_roots=(executable_root,),
                expected_owner_uid=os.getuid(),
                uid=1000,
                command_runner=self.loginctl_runner(),
            )

    def test_trusted_desktop_probe_rejects_execve_switch_and_manager_uid_mismatch(self):
        loginctl, proc_root, executable_root, executable = self.make_trusted_desktop_fixture()
        replacement = executable_root / "startdde"
        replacement.write_bytes(b"different trusted executable")
        replacement.chmod(0o755)
        original_snapshot = self.observer._read_proc_executable_snapshot
        snapshots = 0

        def switching_snapshot(*args, **kwargs):
            nonlocal snapshots
            snapshots += 1
            if snapshots == 2:
                (proc_root / "2295/exe").unlink()
                (proc_root / "2295/exe").symlink_to(replacement)
            return original_snapshot(*args, **kwargs)

        with mock.patch.object(
            self.observer,
            "_read_proc_executable_snapshot",
            side_effect=switching_snapshot,
        ):
            with self.assertRaisesRegex(self.observer.ObservationError, "executable|changed|process"):
                self.observer._probe_trusted_desktop_session(
                    environment={"XDG_CURRENT_DESKTOP": "UKUI"},
                    loginctl_path=loginctl,
                    proc_root=proc_root,
                    current_process_cgroup_path=proc_root / "self/cgroup",
                    trusted_executable_roots=(executable_root,),
                    expected_owner_uid=os.getuid(),
                    uid=1000,
                    command_runner=self.loginctl_runner(),
                )

        (proc_root / "2295/exe").unlink()
        (proc_root / "2295/exe").symlink_to(executable)
        (proc_root / "2295/status").write_text(
            "Name:\tukui-session\nUid:\t2000\t2000\t2000\t2000\n",
            encoding="ascii",
        )
        with self.assertRaisesRegex(self.observer.ObservationError, "UID|uid|user|manager"):
            self.observer._probe_trusted_desktop_session(
                environment={"XDG_CURRENT_DESKTOP": "UKUI"},
                loginctl_path=loginctl,
                proc_root=proc_root,
                current_process_cgroup_path=proc_root / "self/cgroup",
                trusted_executable_roots=(executable_root,),
                expected_owner_uid=os.getuid(),
                uid=1000,
                command_runner=self.loginctl_runner(),
            )

    def test_trusted_desktop_probe_rejects_prefix_lookalike_manager_names(self):
        cases = (("ukui-session-helper", "ukui"), ("dde-session-arbitrary", "dde"))
        for executable_name, desktop_name in cases:
            with self.subTest(executable=executable_name):
                case_root = self.root / ("lookalike-" + executable_name)
                case_root.mkdir()
                loginctl, proc_root, executable_root, _ = self.make_trusted_desktop_fixture(
                    executable_name=executable_name,
                    fixture_root=case_root,
                )
                with self.assertRaisesRegex(self.observer.ObservationError, "manager|family|desktop|process"):
                    self.observer._probe_trusted_desktop_session(
                        environment={"XDG_CURRENT_DESKTOP": desktop_name},
                        loginctl_path=loginctl,
                        proc_root=proc_root,
                        current_process_cgroup_path=proc_root / "self/cgroup",
                        trusted_executable_roots=(executable_root,),
                        expected_owner_uid=os.getuid(),
                        uid=1000,
                        command_runner=self.loginctl_runner({"Desktop": desktop_name}),
                    )

    def test_trusted_desktop_probe_allows_empty_logind_desktop_when_manager_is_unique(self):
        loginctl, proc_root, executable_root, _ = self.make_trusted_desktop_fixture()

        desktop = self.observer._probe_trusted_desktop_session(
            environment={},
            loginctl_path=loginctl,
            proc_root=proc_root,
            current_process_cgroup_path=proc_root / "self/cgroup",
            trusted_executable_roots=(executable_root,),
            expected_owner_uid=os.getuid(),
            uid=1000,
            command_runner=self.loginctl_runner({"Desktop": ""}),
        )

        self.assertEqual(desktop, "UKUI")

    def test_trusted_desktop_probe_rejects_logind_session_change_after_process_scan(self):
        loginctl, proc_root, executable_root, _ = self.make_trusted_desktop_fixture()
        base_runner = self.loginctl_runner()
        selected_reads = 0

        def changing_runner(argv, **kwargs):
            nonlocal selected_reads
            result = base_runner(argv, **kwargs)
            if argv[1:3] == ["show-session", "4"]:
                selected_reads += 1
                if selected_reads == 2:
                    result.stdout = result.stdout.replace("Active=yes", "Active=no")
            return result

        with self.assertRaisesRegex(self.observer.ObservationError, "changed|session|logind"):
            self.observer._probe_trusted_desktop_session(
                environment={"XDG_CURRENT_DESKTOP": "UKUI"},
                loginctl_path=loginctl,
                proc_root=proc_root,
                current_process_cgroup_path=proc_root / "self/cgroup",
                trusted_executable_roots=(executable_root,),
                expected_owner_uid=os.getuid(),
                uid=1000,
                command_runner=changing_runner,
            )

    def test_trusted_desktop_probe_rejects_xdg_forgery_and_logind_process_disagreement(self):
        loginctl, proc_root, executable_root, _ = self.make_trusted_desktop_fixture()
        with self.assertRaisesRegex(self.observer.ObservationError, "desktop|XDG|session"):
            self.observer._probe_trusted_desktop_session(
                environment={"XDG_CURRENT_DESKTOP": "GNOME", "XDG_SESSION_TYPE": "x11"},
                loginctl_path=loginctl,
                proc_root=proc_root,
                current_process_cgroup_path=proc_root / "self/cgroup",
                trusted_executable_roots=(executable_root,),
                expected_owner_uid=os.getuid(),
                uid=1000,
                command_runner=self.loginctl_runner(),
            )
        with self.assertRaisesRegex(self.observer.ObservationError, "desktop|process|session"):
            self.observer._probe_trusted_desktop_session(
                environment={"XDG_CURRENT_DESKTOP": "GNOME", "XDG_SESSION_TYPE": "x11"},
                loginctl_path=loginctl,
                proc_root=proc_root,
                current_process_cgroup_path=proc_root / "self/cgroup",
                trusted_executable_roots=(executable_root,),
                expected_owner_uid=os.getuid(),
                uid=1000,
                command_runner=self.loginctl_runner({"Desktop": "gnome"}),
            )

    def test_trusted_desktop_probe_fails_closed_for_remote_tty_ambiguous_or_timeout(self):
        loginctl, proc_root, executable_root, _ = self.make_trusted_desktop_fixture()
        cases = (
            ("remote", self.loginctl_runner({"Remote": "yes"})),
            ("tty", self.loginctl_runner({"Type": "tty", "Display": ""})),
            ("ambiguous", self.loginctl_runner(user_output="Display=4\nSessions=4 5\n")),
        )
        for label, runner in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(self.observer.ObservationError, "desktop|session|local|graphical|ambiguous"):
                    self.observer._probe_trusted_desktop_session(
                        environment={"XDG_CURRENT_DESKTOP": "UKUI"},
                        loginctl_path=loginctl,
                        proc_root=proc_root,
                        current_process_cgroup_path=proc_root / "self/cgroup",
                        trusted_executable_roots=(executable_root,),
                        expected_owner_uid=os.getuid(),
                        uid=1000,
                        command_runner=runner,
                    )

        def timeout_runner(argv, **kwargs):
            raise TimeoutError("timeout")

        with self.assertRaisesRegex(self.observer.ObservationError, "loginctl|desktop|session"):
            self.observer._probe_trusted_desktop_session(
                environment={},
                loginctl_path=loginctl,
                proc_root=proc_root,
                current_process_cgroup_path=proc_root / "self/cgroup",
                trusted_executable_roots=(executable_root,),
                expected_owner_uid=os.getuid(),
                uid=1000,
                command_runner=timeout_runner,
            )

        noisy_runner = self.loginctl_runner()

        def stderr_runner(argv, **kwargs):
            result = noisy_runner(argv, **kwargs)
            result.stderr = "untrusted partial loginctl warning"
            return result

        with self.assertRaisesRegex(self.observer.ObservationError, "loginctl|desktop|session"):
            self.observer._probe_trusted_desktop_session(
                environment={},
                loginctl_path=loginctl,
                proc_root=proc_root,
                current_process_cgroup_path=proc_root / "self/cgroup",
                trusted_executable_roots=(executable_root,),
                expected_owner_uid=os.getuid(),
                uid=1000,
                command_runner=stderr_runner,
            )

    def test_trusted_desktop_probe_rejects_user_writable_executable_and_pid_race(self):
        loginctl, proc_root, executable_root, executable = self.make_trusted_desktop_fixture()
        executable.chmod(0o775)
        with self.assertRaisesRegex(self.observer.ObservationError, "desktop|executable|trusted"):
            self.observer._probe_trusted_desktop_session(
                environment={"XDG_CURRENT_DESKTOP": "UKUI"},
                loginctl_path=loginctl,
                proc_root=proc_root,
                current_process_cgroup_path=proc_root / "self/cgroup",
                trusted_executable_roots=(executable_root,),
                expected_owner_uid=os.getuid(),
                uid=1000,
                command_runner=self.loginctl_runner(),
            )

        executable.chmod(0o755)
        with mock.patch.object(
            self.observer,
            "_read_proc_start_time",
            side_effect=["123456", "654321"],
        ):
            with self.assertRaisesRegex(self.observer.ObservationError, "changed|race|process"):
                self.observer._probe_trusted_desktop_session(
                    environment={"XDG_CURRENT_DESKTOP": "UKUI"},
                    loginctl_path=loginctl,
                    proc_root=proc_root,
                    current_process_cgroup_path=proc_root / "self/cgroup",
                    trusted_executable_roots=(executable_root,),
                    expected_owner_uid=os.getuid(),
                    uid=1000,
                    command_runner=self.loginctl_runner(),
                )

    def test_trusted_machine_id_accepts_canonical_symlink_and_rejects_mutable_or_racing_file(self):
        trusted_root = self.root / "machine-id-root"
        trusted_root.mkdir(mode=0o755)
        target = trusted_root / "machine-id.real"
        target.write_text("a" * 32 + "\n", encoding="ascii")
        target.chmod(0o644)
        link = trusted_root / "machine-id"
        link.symlink_to(target.name)

        observed = self.observer._read_trusted_machine_id(
            paths=(link,),
            trusted_roots=(trusted_root,),
            expected_owner_uid=os.getuid(),
        )
        self.assertEqual(observed, "a" * 32)

        target.chmod(0o666)
        with self.assertRaisesRegex(self.observer.ObservationError, "machine|trusted|root-owned"):
            self.observer._read_trusted_machine_id(
                paths=(link,),
                trusted_roots=(trusted_root,),
                expected_owner_uid=os.getuid(),
            )
        target.chmod(0o644)
        hardlink = trusted_root / "machine-id-hardlink"
        os.link(target, hardlink)
        with self.assertRaisesRegex(self.observer.ObservationError, "machine|trusted|root-owned"):
            self.observer._read_trusted_machine_id(
                paths=(link,),
                trusted_roots=(trusted_root,),
                expected_owner_uid=os.getuid(),
            )
        hardlink.unlink()

        real_fstat = os.fstat
        calls = 0

        def racing_fstat(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                target.write_text("b" * 32 + "\n", encoding="ascii")
            return real_fstat(descriptor)

        with mock.patch.object(self.observer.os, "fstat", side_effect=racing_fstat):
            with self.assertRaisesRegex(self.observer.ObservationError, "changed|machine"):
                self.observer._read_trusted_machine_id(
                    paths=(link,),
                    trusted_roots=(trusted_root,),
                    expected_owner_uid=os.getuid(),
                )

    def test_trusted_machine_id_rejects_writable_directory_in_parent_chain(self):
        trusted_root = self.root / "trusted-machine-root"
        trusted_root.mkdir(mode=0o755)
        writable_parent = trusted_root / "replaceable"
        writable_parent.mkdir(mode=0o777)
        writable_parent.chmod(0o777)
        machine_id = writable_parent / "machine-id"
        machine_id.write_text("a" * 32 + "\n", encoding="ascii")
        machine_id.chmod(0o644)

        with self.assertRaisesRegex(self.observer.ObservationError, "directory|parent|trusted|machine"):
            self.observer._read_trusted_machine_id(
                paths=(machine_id,),
                trusted_roots=(trusted_root,),
                expected_owner_uid=os.getuid(),
            )

    def test_loginctl_symlink_rejects_writable_original_parent_chain(self):
        trusted_root = self.root / "trusted-loginctl-root"
        trusted_root.mkdir(mode=0o755)
        real_dir = trusted_root / "libexec"
        real_dir.mkdir(mode=0o755)
        real_loginctl = real_dir / "loginctl"
        real_loginctl.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        real_loginctl.chmod(0o755)
        writable_bin = trusted_root / "bin"
        writable_bin.mkdir(mode=0o777)
        writable_bin.chmod(0o777)
        linked_loginctl = writable_bin / "loginctl"
        linked_loginctl.symlink_to(real_loginctl)

        with self.assertRaisesRegex(self.observer.ObservationError, "loginctl|directory|trusted"):
            self.observer._probe_trusted_desktop_session(
                environment={},
                loginctl_path=linked_loginctl,
                trusted_loginctl_roots=(trusted_root,),
                proc_root=self.root / "unused-proc",
                current_process_cgroup_path=self.root / "unused-cgroup",
                trusted_executable_roots=(trusted_root,),
                expected_owner_uid=os.getuid(),
                uid=1000,
                command_runner=self.loginctl_runner(),
            )

    def test_platform_identity_is_derived_from_release_files_desktop_session_and_kysec_probe(self):
        matrix = self.observer._read_certification_matrix(MATRIX)
        files = {
            "/etc/os-release": (
                b'ID=kylin\nVERSION_ID="v10"\nKYLIN_RELEASE_ID="2503"\n'
            ),
        }

        identity = self.observer.collect_platform_identity(
            matrix,
            "kylin-hardened",
            read_system_file=lambda path, required=True: files.get(str(path)),
            environment={
                "XDG_CURRENT_DESKTOP": "UKUI",
                "XDG_SESSION_TYPE": "x11",
            },
            desktop_probe=lambda environment: "UKUI",
            kysec_probe=lambda: {
                "detected": True,
                "enabled": True,
                "exec_control": "off",
            },
        )

        self.assertEqual(identity["os_id"], "kylin")
        self.assertEqual(identity["os_version"], "v10/2503")
        self.assertEqual(identity["desktop_environment"], "UKUI")
        self.assertEqual(
            identity["security_facts"]["security_profile"],
            "kysec-enabled-exec-control-off",
        )
        self.assertEqual(
            identity["security_facts"]["os_release_sha256"],
            hashlib.sha256(files["/etc/os-release"]).hexdigest(),
        )

    def test_platform_identity_rejects_claimed_category_version_desktop_or_security_mismatch(self):
        matrix = self.observer._read_certification_matrix(MATRIX)
        cases = (
            (
                "release",
                b'ID=kylin\nVERSION_ID="v10"\nKYLIN_RELEASE_ID="2403"\n',
                {"XDG_CURRENT_DESKTOP": "UKUI"},
                {"detected": False, "enabled": False, "exec_control": "not-present"},
            ),
            (
                "desktop",
                b'ID=kylin\nVERSION_ID="v10"\nKYLIN_RELEASE_ID="2503"\n',
                {"XDG_CURRENT_DESKTOP": "DDE"},
                {"detected": False, "enabled": False, "exec_control": "not-present"},
            ),
            (
                "security",
                b'ID=kylin\nVERSION_ID="v10"\nKYLIN_RELEASE_ID="2503"\n',
                {"XDG_CURRENT_DESKTOP": "UKUI"},
                {"detected": False, "enabled": False, "exec_control": "not-present"},
            ),
        )
        for label, os_release, environment, kysec in cases:
            with self.subTest(case=label):
                with self.assertRaisesRegex(self.observer.ObservationError, "platform|release|desktop|security|Kysec"):
                    self.observer.collect_platform_identity(
                        matrix,
                        "kylin-hardened",
                        read_system_file=lambda path, required=True, payload=os_release: (
                            payload if str(path) == "/etc/os-release" else None
                        ),
                        environment=environment,
                        desktop_probe=lambda environment, observer=self.observer: observer._desktop_family_from_label(
                            environment["XDG_CURRENT_DESKTOP"], "test desktop"
                        ),
                        kysec_probe=lambda value=kysec: value,
                    )

    def test_uos_and_openkylin_release_normalization_is_deterministic(self):
        matrix = self.observer._read_certification_matrix(MATRIX)
        uos_files = {
            "/etc/os-release": b'ID=uos\nVERSION_ID="20"\n',
            "/etc/os-version": b'[Version]\nMajorVersion=20\nMinorVersion=1070u2\n',
        }
        uos = self.observer.collect_platform_identity(
            matrix,
            "uos-min-dde",
            read_system_file=lambda path, required=True: uos_files.get(str(path)),
            environment={"XDG_CURRENT_DESKTOP": "Deepin"},
            desktop_probe=lambda environment: "DDE",
            kysec_probe=lambda: {"detected": False, "enabled": False, "exec_control": "not-present"},
        )
        self.assertEqual(uos["os_version"], "20/1070u2")
        self.assertEqual(uos["desktop_environment"], "DDE")

        openkylin_files = {
            "/etc/os-release": (
                b'ID=openkylin\nVERSION_ID="2.0"\nPRETTY_NAME="openKylin 2.0 SP2"\n'
            ),
        }
        openkylin = self.observer.collect_platform_identity(
            matrix,
            "openkylin-current",
            read_system_file=lambda path, required=True: openkylin_files.get(str(path)),
            environment={"XDG_CURRENT_DESKTOP": "GNOME"},
            desktop_probe=lambda environment: "GNOME",
            kysec_probe=lambda: {"detected": False, "enabled": False, "exec_control": "not-present"},
        )
        self.assertEqual(openkylin["os_version"], "2.0/2.0-SP2")

    def test_uos_realistic_os_version_with_localized_and_unquoted_values_is_accepted(self):
        matrix = self.observer._read_certification_matrix(MATRIX)
        files = {
            "/etc/os-release": b'ID=uos\nVERSION_ID="25"\n',
            "/etc/os-version": (
                "[Version]\n"
                "SystemName=UOS Desktop\n"
                "SystemName[zh_CN]=统信桌面操作系统\n"
                "ProductType=Desktop\n"
                "ProductType[zh_CN]=桌面\n"
                "EditionName=Professional\n"
                "MajorVersion=25\n"
                "MinorVersion=2500\n"
            ).encode("utf-8"),
        }

        identity = self.observer.collect_platform_identity(
            matrix,
            "uos-current-or-hardened",
            read_system_file=lambda path, required=True: files.get(str(path)),
            environment={"XDG_CURRENT_DESKTOP": "DDE"},
            desktop_probe=lambda environment: "DDE",
            kysec_probe=lambda: {
                "detected": False,
                "enabled": False,
                "exec_control": "not-present",
            },
        )

        self.assertEqual(identity["os_version"], "25/2500")

    def test_canonical_root_owned_0777_os_release_symlink_is_accepted(self):
        trusted_root = self.root / "trusted-system"
        trusted_root.mkdir(mode=0o755)
        target = trusted_root / "os-release.real"
        payload = b'ID=kylin\nVERSION_ID="v10"\nKYLIN_RELEASE_ID="2503"\n'
        target.write_bytes(payload)
        target.chmod(0o644)
        link = trusted_root / "os-release"
        link.symlink_to(target.name)

        observed = self.observer._read_trusted_system_file(
            link,
            trusted_roots=(trusted_root,),
            expected_owner_uid=os.getuid(),
        )

        self.assertEqual(observed, payload)

    def test_any_kysec_exec_control_on_is_rejected_even_if_status_is_disabled(self):
        matrix = self.observer._read_certification_matrix(MATRIX)
        with self.assertRaisesRegex(self.observer.ObservationError, "Kysec|security"):
            self.observer.collect_platform_identity(
                matrix,
                "kylin-current-standard",
                read_system_file=lambda path, required=True: (
                    b'ID=kylin\nVERSION_ID="v10"\nKYLIN_RELEASE_ID="2503"\n'
                    if str(path) == "/etc/os-release"
                    else None
                ),
                environment={"XDG_CURRENT_DESKTOP": "UKUI"},
                desktop_probe=lambda environment: "UKUI",
                kysec_probe=lambda: {
                    "detected": True,
                    "enabled": False,
                    "exec_control": "on",
                },
            )

    def test_kysec_probe_fails_closed_when_marker_exists_but_trusted_tool_is_missing(self):
        marker = self.root / "etc/kysec"
        marker.mkdir(parents=True)
        missing_tool = self.root / "usr/sbin/getstatus"
        with self.assertRaisesRegex(self.observer.ObservationError, "Kysec"):
            self.observer._probe_kysec_status(
                tool=missing_tool,
                markers=(marker, missing_tool),
                expected_owner_uid=os.getuid(),
            )

    def test_kysec_probe_accepts_only_trusted_unambiguous_exec_control_off(self):
        tool = self.root / "usr/sbin/getstatus"
        tool.parent.mkdir(parents=True, mode=0o755)
        tool.write_text(
            "#!/bin/sh\nprintf 'Kysec status : enabled\\nexec control : off\\n'\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)

        status = self.observer._probe_kysec_status(
            tool=tool,
            markers=(tool,),
            expected_owner_uid=os.getuid(),
        )

        self.assertEqual(
            status,
            {"detected": True, "enabled": True, "exec_control": "off"},
        )

    def test_rejects_observer_started_after_package_is_already_installed(self):
        with self.assertRaisesRegex(self.observer.ObservationError, "before the package is installed"):
            self.observe(FakeRuntime(["install ok installed"]))

    def test_rejects_network_becoming_available_mid_install(self):
        runtime = FakeRuntime(
            [None, "install ok unpacked", "install ok installed"],
            network_samples=[True, False, True],
        )
        with self.assertRaisesRegex(self.observer.ObservationError, "non-loopback network"):
            self.observe(runtime)

    def test_rejects_network_becoming_available_at_final_installed_sample(self):
        runtime = FakeRuntime(
            [None, "install ok unpacked", "install ok installed"],
            network_samples=[True, True, True, False],
        )
        with self.assertRaisesRegex(self.observer.ObservationError, "non-loopback network"):
            self.observe(runtime)

    def test_rejects_preexisting_taiji_xdg_state(self):
        self.user_paths[1].mkdir()
        with self.assertRaisesRegex(self.observer.ObservationError, "user state already exists"):
            self.observe(FakeRuntime([None, "install ok installed"]))

    def test_rejects_candidate_deb_replaced_during_observation(self):
        runtime = FakeRuntime([None, "install ok unpacked", "install ok installed"])
        runtime.after_sleep = lambda: self.deb.write_bytes(b"replaced-after-observer-start")
        with self.assertRaisesRegex(self.observer.ObservationError, "candidate DEB changed"):
            self.observe(runtime)

    def test_rejects_hash_matching_candidate_with_wrong_manifest_basename(self):
        wrong_name = self.customer / "renamed-but-byte-identical_amd64.deb"
        self.deb.rename(wrong_name)
        with self.assertRaisesRegex(self.observer.ObservationError, "manifest DEB basename"):
            self.observe(FakeRuntime([None, "install ok installed"]))

    def test_verify_rejects_deb_renamed_after_observation(self):
        runtime = FakeRuntime([None, "install ok installed"])
        observation = self.observe(runtime)
        observation_path = self.root / "single-deb-install-observation.json"
        observation_path.write_text(json.dumps(observation), encoding="utf-8")
        renamed = self.root / "different-name_amd64.deb"
        self.deb.rename(renamed)
        with self.assertRaisesRegex(self.observer.ObservationError, "manifest DEB basename"):
            self.observer.verify_observation(
                observation_path,
                manifest_path=self.manifest,
                deb_path=renamed,
                challenge=self.challenge,
                runtime=runtime,
                user_state_paths=self.user_paths,
            )

    def test_verify_rejects_changed_uid_home_or_xdg_context(self):
        runtime = FakeRuntime([None, "install ok installed"])
        observation = self.observe(runtime)
        observation_path = self.root / "single-deb-install-observation.json"
        observation_path.write_text(json.dumps(observation), encoding="utf-8")
        changed_paths = [self.root / "other-xdg-config", *self.user_paths[1:]]
        with self.assertRaisesRegex(self.observer.ObservationError, "user state paths"):
            self.observer.verify_observation(
                observation_path,
                manifest_path=self.manifest,
                deb_path=self.deb,
                challenge=self.challenge,
                runtime=runtime,
                user_state_paths=changed_paths,
            )

    def test_rejects_candidate_directory_replaced_during_observation(self):
        runtime = FakeRuntime([None, "install ok unpacked", "install ok installed"])

        def replace_directory_preserving_file_inode():
            original = self.root / "original-candidate-directory"
            self.customer.rename(original)
            self.customer.mkdir(mode=0o700)
            (original / self.deb.name).rename(self.customer / self.deb.name)

        runtime.after_sleep = replace_directory_preserving_file_inode
        with self.assertRaisesRegex(self.observer.ObservationError, "candidate directory changed"):
            self.observe(runtime)

    def test_rejects_symlink_candidate_even_when_target_hash_matches(self):
        target = self.root / "real-payload_amd64.deb"
        self.deb.rename(target)
        self.deb.symlink_to(target)
        with self.assertRaisesRegex(self.observer.ObservationError, "regular single-link"):
            self.observe(FakeRuntime([None, "install ok installed"]))

    def test_atomic_json_rejects_symlink_parent(self):
        real_parent = self.root / "real-output"
        real_parent.mkdir()
        linked_parent = self.root / "linked-output"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaisesRegex(self.observer.ObservationError, "parent"):
            self.observer._atomic_json(linked_parent / "evidence.json", {"safe": True})
        self.assertFalse((real_parent / "evidence.json").exists())

    def test_atomic_json_publishes_once_in_real_parent(self):
        output = self.root.resolve() / "atomic-observation.json"
        self.observer._atomic_json(output, {"safe": True})
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"safe": True})
        with self.assertRaisesRegex(self.observer.ObservationError, "overwrite"):
            self.observer._atomic_json(output, {"safe": False})

    def test_network_parser_allows_only_loopback_links_and_routes(self):
        self.assertTrue(
            self.observer.network_outputs_are_offline(
                link_lines=["1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 state UNKNOWN"],
                global_address_lines=[],
                ipv4_route_lines=["local 127.0.0.1 dev lo scope host"],
                ipv6_route_lines=["local ::1 dev lo proto kernel metric 0"],
            )
        )
        cases = (
            {"link_lines": ["2: eth0: <BROADCAST,UP> mtu 1500 state UP"]},
            {"global_address_lines": ["2: eth0 inet 10.0.0.5/24 scope global eth0"]},
            {"ipv4_route_lines": ["default via 10.0.0.1 dev eth0"]},
            {"ipv6_route_lines": ["default via fe80::1 dev eth0 metric 100"]},
            {"ipv6_route_lines": ["default dev lo"]},
        )
        defaults = {
            "link_lines": ["1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 state UNKNOWN"],
            "global_address_lines": [],
            "ipv4_route_lines": [],
            "ipv6_route_lines": [],
        }
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(self.observer.network_outputs_are_offline(**{**defaults, **case}))

    def test_rejects_machine_or_boot_identity_change(self):
        runtime = FakeRuntime([None, "install ok unpacked", "install ok installed"])
        runtime.after_sleep = lambda: setattr(runtime, "boot_id", "f" * 32)
        with self.assertRaisesRegex(self.observer.ObservationError, "machine or boot identity changed"):
            self.observe(runtime)

    def test_verify_bundle_rejects_different_challenge_and_machine(self):
        runtime = FakeRuntime([None, "install ok installed"])
        observation = self.observe(runtime)
        observation_path = self.root / "single-deb-install-observation.json"
        observation_path.write_text(json.dumps(observation), encoding="utf-8")

        with self.assertRaisesRegex(self.observer.ObservationError, "challenge"):
            self.observer.verify_observation(
                observation_path,
                manifest_path=self.manifest,
                deb_path=self.deb,
                challenge="0" * 64,
                runtime=runtime,
                user_state_paths=self.user_paths,
            )
        other_machine = FakeRuntime(["install ok installed"], machine_id="1" * 32)
        with self.assertRaisesRegex(self.observer.ObservationError, "machine"):
            self.observer.verify_observation(
                observation_path,
                manifest_path=self.manifest,
                deb_path=self.deb,
                challenge=self.challenge,
                runtime=other_machine,
                user_state_paths=self.user_paths,
            )

    def test_method_attestation_is_explicitly_human_and_binds_png_and_observation(self):
        runtime = FakeRuntime([None, "install ok installed"])
        observation = self.observe(runtime)
        observation_path = self.root / "single-deb-install-observation.json"
        observation_path.write_text(json.dumps(observation, sort_keys=True), encoding="utf-8")
        screenshot = self.root / "graphical-installer.png"
        screenshot.write_bytes(png_fixture())

        attestation = self.observer.create_method_attestation(
            observation_path=observation_path,
            graphical_evidence_path=screenshot,
            challenge=self.challenge,
            operator_id="target-operator-01",
            runtime=runtime,
            user_state_paths=self.user_paths,
        )
        self.assertEqual(attestation["installation_method_attested"], "desktop-double-click")
        self.assertFalse(attestation["installation_method_machine_observed"])
        self.assertEqual(attestation["attestation_scope"], "human-observed-system-graphical-installer")
        attestation_path = self.root / "single-deb-install-method-attestation.json"
        attestation_path.write_text(json.dumps(attestation, sort_keys=True), encoding="utf-8")
        self.observer.verify_method_attestation(
            attestation_path=attestation_path,
            observation_path=observation_path,
            graphical_evidence_path=screenshot,
            challenge=self.challenge,
            runtime=runtime,
            user_state_paths=self.user_paths,
        )

        screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"tampered")
        with self.assertRaisesRegex(self.observer.ObservationError, "graphical installer evidence"):
            self.observer.verify_method_attestation(
                attestation_path=attestation_path,
                observation_path=observation_path,
                graphical_evidence_path=screenshot,
                challenge=self.challenge,
                runtime=runtime,
                user_state_paths=self.user_paths,
            )

    def test_canonical_cli_attest_accepts_v2_and_binds_environment_matrix_category(self):
        runtime = FakeRuntime([None, "install ok installed"])
        observation, environment = self.observe_canonical(runtime=runtime)
        output_dir = self.root.resolve() / "canonical-attestation"
        output_dir.mkdir(mode=0o700)
        observation_path = output_dir / self.observer.OBSERVATION_BASENAME
        environment_path = output_dir / self.observer.ENVIRONMENT_RECORD_BASENAME
        observation_path.write_text(
            json.dumps(observation, sort_keys=True), encoding="utf-8"
        )
        environment_path.write_text(
            json.dumps(environment, sort_keys=True), encoding="utf-8"
        )
        screenshot = self.root / "raw-installer-success.png"
        screenshot.write_bytes(png_fixture())
        platform_identity = {
            "os_id": environment["os_id"],
            "os_version": environment["os_version"],
            "desktop_environment": environment["desktop_environment"],
            "security_facts": environment["security_facts"],
        }

        with mock.patch.object(
            self.observer, "SystemRuntime", return_value=runtime
        ), mock.patch.object(
            self.observer,
            "collect_platform_identity",
            return_value=platform_identity,
        ), mock.patch.object(
            self.observer,
            "default_user_state_paths",
            return_value=self.user_paths,
        ):
            result = self.observer.main(
                [
                    "attest",
                    "--observation",
                    str(observation_path),
                    "--graphical-evidence",
                    str(screenshot),
                    "--challenge",
                    self.challenge,
                    "--operator-id",
                    "target-operator-01",
                    "--confirmation",
                    "I-observed-desktop-double-click-and-system-installer",
                    "--output-dir",
                    str(output_dir),
                    "--matrix",
                    str(MATRIX),
                    "--category-id",
                    environment["category_id"],
                    "--environment-observation",
                    str(environment_path),
                ]
            )

        self.assertEqual(result, 0)
        attestation_path = output_dir / self.observer.ATTESTATION_BASENAME
        self.assertTrue(attestation_path.is_file())
        self.observer.verify_method_attestation(
            attestation_path=attestation_path,
            observation_path=observation_path,
            graphical_evidence_path=output_dir / self.observer.GRAPHICAL_EVIDENCE_BASENAME,
            challenge=self.challenge,
            runtime=runtime,
            user_state_paths=self.user_paths,
            canonical=True,
        )

    def test_canonical_attest_fails_closed_on_schema_challenge_or_environment_binding(self):
        runtime = FakeRuntime([None, "install ok installed"])
        observation, environment = self.observe_canonical(runtime=runtime)
        observation_path = self.root / self.observer.OBSERVATION_BASENAME
        environment_path = self.root / self.observer.ENVIRONMENT_RECORD_BASENAME
        screenshot = self.root / "graphical-installer.png"
        screenshot.write_bytes(png_fixture())
        platform_identity = {
            "os_id": environment["os_id"],
            "os_version": environment["os_version"],
            "desktop_environment": environment["desktop_environment"],
            "security_facts": environment["security_facts"],
        }
        cases = {
            "schema": ({**observation, "schema": "taiji.single-deb-install-observation/v9"}, environment),
            "challenge": ({**observation, "challenge_nonce": "0" * 64}, environment),
            "environment": (observation, {**environment, "category_id": "uos-min-dde"}),
            "environment-deb-name": (
                observation,
                {
                    **environment,
                    "version": "1.0.0+renamed",
                    "deb_basename": "taiji-agent_1.0.0+renamed_amd64.deb",
                },
            ),
        }
        for label, (candidate_observation, candidate_environment) in cases.items():
            with self.subTest(label=label):
                observation_path.write_text(
                    json.dumps(candidate_observation, sort_keys=True), encoding="utf-8"
                )
                environment_path.write_text(
                    json.dumps(candidate_environment, sort_keys=True), encoding="utf-8"
                )
                with mock.patch.object(
                    self.observer,
                    "collect_platform_identity",
                    return_value=platform_identity,
                ), self.assertRaises(self.observer.ObservationError):
                    self.observer.create_method_attestation(
                        observation_path=observation_path,
                        graphical_evidence_path=screenshot,
                        challenge=self.challenge,
                        operator_id="target-operator-01",
                        runtime=runtime,
                        user_state_paths=self.user_paths,
                        matrix_path=MATRIX,
                        category_id=environment["category_id"],
                        environment_observation_path=environment_path,
                    )

    def test_canonical_attest_fails_closed_on_operator_png_and_legacy_input(self):
        runtime = FakeRuntime([None, "install ok installed"])
        observation, environment = self.observe_canonical(runtime=runtime)
        observation_path = self.root / self.observer.OBSERVATION_BASENAME
        environment_path = self.root / self.observer.ENVIRONMENT_RECORD_BASENAME
        observation_path.write_text(
            json.dumps(observation, sort_keys=True), encoding="utf-8"
        )
        environment_path.write_text(
            json.dumps(environment, sort_keys=True), encoding="utf-8"
        )
        valid_png = self.root / "valid-installer.png"
        invalid_png = self.root / "invalid-installer.png"
        valid_png.write_bytes(png_fixture())
        invalid_png.write_bytes(b"not-a-png")
        canonical_arguments = {
            "observation_path": observation_path,
            "challenge": self.challenge,
            "runtime": runtime,
            "user_state_paths": self.user_paths,
            "matrix_path": MATRIX,
            "category_id": environment["category_id"],
            "environment_observation_path": environment_path,
        }
        with self.assertRaisesRegex(self.observer.ObservationError, "operator_id"):
            self.observer.create_method_attestation(
                graphical_evidence_path=valid_png,
                operator_id="!",
                **canonical_arguments,
            )

        platform_identity = {
            "os_id": environment["os_id"],
            "os_version": environment["os_version"],
            "desktop_environment": environment["desktop_environment"],
            "security_facts": environment["security_facts"],
        }
        with mock.patch.object(
            self.observer,
            "collect_platform_identity",
            return_value=platform_identity,
        ), self.assertRaisesRegex(self.observer.ObservationError, "PNG|png"):
            self.observer.create_method_attestation(
                graphical_evidence_path=invalid_png,
                operator_id="target-operator-01",
                **canonical_arguments,
            )

        legacy_runtime = FakeRuntime([None, "install ok installed"])
        legacy_observation = self.observe(legacy_runtime)
        observation_path.write_text(
            json.dumps(legacy_observation, sort_keys=True), encoding="utf-8"
        )
        with self.assertRaisesRegex(
            self.observer.ObservationError,
            "legacy.*canonical",
        ):
            self.observer.create_method_attestation(
                graphical_evidence_path=valid_png,
                operator_id="target-operator-01",
                **{**canonical_arguments, "runtime": legacy_runtime},
            )

    def test_canonical_attest_rejects_strict_json_duplicates_and_identity_swaps(self):
        runtime = FakeRuntime([None, "install ok installed"])
        observation, environment = self.observe_canonical(runtime=runtime)
        platform_identity = {
            "os_id": environment["os_id"],
            "os_version": environment["os_version"],
            "desktop_environment": environment["desktop_environment"],
            "security_facts": environment["security_facts"],
        }

        duplicate_dir = self.root / "duplicate-json"
        duplicate_dir.mkdir(mode=0o700)
        duplicate_observation = duplicate_dir / self.observer.OBSERVATION_BASENAME
        canonical_text = json.dumps(observation, sort_keys=True)
        duplicate_observation.write_text(
            canonical_text[:-1] + ',"schema":"%s"}' % observation["schema"],
            encoding="utf-8",
        )
        duplicate_environment = duplicate_dir / self.observer.ENVIRONMENT_RECORD_BASENAME
        duplicate_environment.write_text(json.dumps(environment, sort_keys=True), encoding="utf-8")
        duplicate_matrix = duplicate_dir / "certification-matrix.json"
        duplicate_matrix.write_bytes(MATRIX.read_bytes())
        duplicate_png = duplicate_dir / "installer.png"
        duplicate_png.write_bytes(png_fixture())
        with mock.patch.object(
            self.observer, "collect_platform_identity", return_value=platform_identity
        ), self.assertRaisesRegex(self.observer.ObservationError, "duplicate|JSON"):
            self.observer.create_method_attestation(
                observation_path=duplicate_observation,
                graphical_evidence_path=duplicate_png,
                challenge=self.challenge,
                operator_id="target-operator-01",
                runtime=runtime,
                user_state_paths=self.user_paths,
                matrix_path=duplicate_matrix,
                category_id=environment["category_id"],
                environment_observation_path=duplicate_environment,
            )

        for label in ("observation", "environment", "matrix"):
            with self.subTest(label=label):
                case_dir = self.root / ("swap-" + label)
                case_dir.mkdir(mode=0o700)
                observation_path = case_dir / self.observer.OBSERVATION_BASENAME
                environment_path = case_dir / self.observer.ENVIRONMENT_RECORD_BASENAME
                matrix_path = case_dir / "certification-matrix.json"
                observation_path.write_text(json.dumps(observation, sort_keys=True), encoding="utf-8")
                environment_path.write_text(json.dumps(environment, sort_keys=True), encoding="utf-8")
                matrix_path.write_bytes(MATRIX.read_bytes())
                screenshot = case_dir / "installer.png"
                screenshot.write_bytes(png_fixture())
                target = {
                    "observation": observation_path,
                    "environment": environment_path,
                    "matrix": matrix_path,
                }[label]
                replacement = case_dir / (label + "-replacement")
                replacement.write_bytes(target.read_bytes())
                parked = case_dir / (label + "-parked")
                source_inode = target.stat().st_ino
                swapped = False
                real_read = self.observer.os.read

                def swap_after_first_read(descriptor, size):
                    nonlocal swapped
                    chunk = real_read(descriptor, size)
                    if not swapped and self.observer.os.fstat(descriptor).st_ino == source_inode:
                        target.rename(parked)
                        replacement.rename(target)
                        swapped = True
                    return chunk

                with mock.patch.object(
                    self.observer.os, "read", side_effect=swap_after_first_read
                ), mock.patch.object(
                    self.observer,
                    "collect_platform_identity",
                    return_value=platform_identity,
                ), self.assertRaisesRegex(self.observer.ObservationError, "changed|identity"):
                    self.observer.create_method_attestation(
                        observation_path=observation_path,
                        graphical_evidence_path=screenshot,
                        challenge=self.challenge,
                        operator_id="target-operator-01",
                        runtime=runtime,
                        user_state_paths=self.user_paths,
                        matrix_path=matrix_path,
                        category_id=environment["category_id"],
                        environment_observation_path=environment_path,
                    )

    def test_method_attestation_rejects_fake_truncated_or_small_png(self):
        runtime = FakeRuntime([None, "install ok installed"])
        observation = self.observe(runtime)
        observation_path = self.root / "single-deb-install-observation.json"
        observation_path.write_text(json.dumps(observation, sort_keys=True), encoding="utf-8")
        screenshot = self.root / "graphical-installer.png"
        invalid_payloads = {
            "signature only": b"\x89PNG\r\n\x1a\n" + b"not-png-data",
            "truncated": png_fixture()[:-10],
            "too small": png_fixture(320, 240),
        }
        for label, payload in invalid_payloads.items():
            with self.subTest(label=label):
                screenshot.write_bytes(payload)
                with self.assertRaisesRegex(self.observer.ObservationError, "PNG"):
                    self.observer.create_method_attestation(
                        observation_path=observation_path,
                        graphical_evidence_path=screenshot,
                        challenge=self.challenge,
                        operator_id="target-operator-01",
                        runtime=runtime,
                        user_state_paths=self.user_paths,
                    )


if __name__ == "__main__":
    unittest.main()
