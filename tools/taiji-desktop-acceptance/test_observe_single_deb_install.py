#!/usr/bin/env python3
"""Dynamic contract tests for the single-DEB install observation workflow."""

import hashlib
import importlib.util
import json
import struct
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path


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

    def test_canonical_mode_emits_category_bound_environment_record_without_baseline(self):
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
            os_id="kylin",
            os_version="V10",
            desktop_environment="UKUI",
        )
        self.assertEqual(observation["schema"], "taiji.single-deb-install-observation/v2")
        self.assertEqual(record["schema"], "taiji-linux-environment-evidence/v1")
        self.assertEqual(record["compatibility"], "COMPATIBLE")
        self.assertEqual(record["category_id"], "kylin-current-standard")
        self.assertNotIn("target_baseline_profile_id", record)
        self.assertNotIn("CERTIFIED", json.dumps(record))

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
