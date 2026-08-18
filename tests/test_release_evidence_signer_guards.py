from __future__ import annotations

import ast
import json
import os
import runpy
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIGNER = ROOT / "scripts/sign-taiji-release-evidence.sh"
PYTHON38_GATE = ROOT / "tests/python38_linux_packaging_gate.py"
CHALLENGE = "ab" * 32
SOURCE_COMMIT = "a" * 40
DEB_BASENAME = "taiji-agent_1.0.2_amd64.deb"
DEB_SHA256 = "1" * 64


class ReleaseEvidenceSignerGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-signer-guard-")
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "certification-set.json"
        self.write_evidence()
        self.private_key = self.root / "release-private.pem"
        self.private_key.write_text("fixture private key\n", encoding="utf-8")
        self.private_key.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_evidence(
        self,
        *,
        schema: str = "taiji-linux-certification-set/v1",
        purpose: str = "certification",
        envelope_updates: dict | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        envelope = {
            "schema": "taiji-signing-challenge/v1",
            "purpose": purpose,
            "nonce": CHALLENGE,
            "issued_at_utc": (now - timedelta(minutes=5)).isoformat().replace(
                "+00:00", "Z"
            ),
            "expires_at_utc": (now + timedelta(minutes=55)).isoformat().replace(
                "+00:00", "Z"
            ),
            "source_commit": SOURCE_COMMIT,
            "deb_basename": DEB_BASENAME,
            "deb_sha256": DEB_SHA256,
        }
        envelope.update(envelope_updates or {})
        self.evidence.write_text(
            json.dumps(
                {
                    "schema": schema,
                    "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
                    "challenge_nonce": CHALLENGE,
                    "challenge_envelope": envelope,
                    "source_commit": SOURCE_COMMIT,
                    "deb_basename": DEB_BASENAME,
                    "deb_sha256": DEB_SHA256,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def run_signer(self, private_key: Path | None = None):
        return subprocess.run(
            ["/bin/bash", "-p", str(SIGNER), str(self.evidence), str(private_key or self.private_key)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def assert_no_signature(self) -> None:
        self.assertFalse(Path(f"{self.evidence}.sig").exists())

    def test_rejects_group_readable_private_key_before_signing(self) -> None:
        self.private_key.chmod(0o640)

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("0400/0600", result.stderr)
        self.assert_no_signature()

    def test_rejects_hardlinked_private_key_before_signing(self) -> None:
        hardlink = self.root / "release-private-hardlink.pem"
        os.link(self.private_key, hardlink)

        result = self.run_signer(private_key=hardlink)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("硬链接", result.stderr)
        self.assert_no_signature()

    def test_rejects_wrong_embedded_purpose_before_key_use(self) -> None:
        self.write_evidence(purpose="publication")

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("purpose", result.stderr)
        self.assertNotIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_invalid_private_key_reports_a_fail_closed_error(self) -> None:
        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_rejects_existing_signature_without_overwrite(self) -> None:
        signature = Path(f"{self.evidence}.sig")
        signature.write_bytes(b"existing-signature")

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("拒绝覆盖", result.stderr)
        self.assertEqual(signature.read_bytes(), b"existing-signature")

    def test_rejects_historical_v2_evidence(self) -> None:
        self.evidence.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "evidence_type": "offline-install-rehearsal",
                    "challenge_nonce": CHALLENGE,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("只接受 certification-set v1 或 release-evidence v3", result.stderr)
        self.assert_no_signature()

    def test_rejects_non_root_owned_ancestor_symlink_for_private_key(self) -> None:
        key_directory = self.root / "real-key-directory"
        key_directory.mkdir(mode=0o700)
        key = key_directory / "release-private.pem"
        key.write_text("fixture private key\n", encoding="utf-8")
        key.chmod(0o600)
        linked_directory = self.root / "linked-key-directory"
        linked_directory.symlink_to(key_directory, target_is_directory=True)

        result = self.run_signer(private_key=linked_directory / key.name)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("祖先符号链接", result.stderr)
        self.assert_no_signature()

    def test_validation_challenge_reservation_and_signature_share_one_private_snapshot(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")

        self.assertIn('SNAPSHOT_EVIDENCE="$SNAPSHOT_ROOT/evidence.json"', source)
        self.assertIn(
            'SNAPSHOT_ENVELOPE="$SNAPSHOT_ROOT/challenge-envelope.json"', source
        )
        self.assertIn('reserve --envelope "$SNAPSHOT_ENVELOPE"', source)
        self.assertIn('--evidence "$SNAPSHOT_EVIDENCE"', source)
        self.assertIn('--public-key-fingerprint "$public_fingerprint"', source)
        self.assertNotIn(".taiji-release-evidence-used-challenges", source)
        self.assertIn('-out "$tmp_signature" "$SNAPSHOT_EVIDENCE"', source)
        self.assertIn('-signature "$tmp_signature" "$SNAPSHOT_EVIDENCE"', source)
        self.assertIn('-signature "$SIGNATURE" "$EVIDENCE"', source)
        self.assertIn('rm -f -- "$SIGNATURE"', source)

    def test_rejects_incomplete_publication_before_private_key_use(self) -> None:
        self.write_evidence(
            schema="taiji-release-evidence/v3",
            purpose="publication",
        )

        result = subprocess.run(
            ["/bin/bash", "-p", str(SIGNER), str(self.evidence), str(self.private_key)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("publication physical bundle", result.stderr)
        self.assertNotIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_publication_evidence_in_external_real_delivery_root_reaches_bundle_validation(
        self,
    ) -> None:
        delivery = self.root / "review-root" / "taijiagent 打包交付"
        delivery.mkdir(parents=True, mode=0o700)
        delivery = delivery.resolve()
        self.evidence = delivery / "release-evidence.json"
        self.write_evidence(
            schema="taiji-release-evidence/v3",
            purpose="publication",
        )

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "publication physical bundle 未通过完整实物和签名前合同校验",
            result.stderr,
        )
        self.assertNotIn("canonical delivery path", result.stderr)
        self.assertNotIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_publication_evidence_requires_fixed_basename(self) -> None:
        delivery = self.root / "review-root" / "taijiagent 打包交付"
        delivery.mkdir(parents=True, mode=0o700)
        self.evidence = delivery / "renamed-release-evidence.json"
        self.write_evidence(
            schema="taiji-release-evidence/v3",
            purpose="publication",
        )

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixed basename release-evidence.json", result.stderr)
        self.assertNotIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_publication_evidence_rejects_group_or_other_writable_delivery_root(
        self,
    ) -> None:
        delivery = self.root / "review-root" / "taijiagent 打包交付"
        delivery.mkdir(parents=True, mode=0o700)
        delivery.chmod(0o777)
        delivery = delivery.resolve()
        self.evidence = delivery / "release-evidence.json"
        self.write_evidence(
            schema="taiji-release-evidence/v3",
            purpose="publication",
        )

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "publication delivery root must be current-user-owned and not group/other writable",
            result.stderr,
        )
        self.assertNotIn("publication physical bundle 未通过", result.stderr)
        self.assertNotIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_publication_evidence_rejects_group_or_other_writable_ancestor(
        self,
    ) -> None:
        unsafe_ancestor = self.root / "unsafe-ancestor"
        unsafe_ancestor.mkdir(mode=0o700)
        unsafe_ancestor.chmod(0o777)
        delivery = unsafe_ancestor / "taijiagent 打包交付"
        delivery.mkdir(mode=0o700)
        delivery = delivery.resolve()
        self.evidence = delivery / "release-evidence.json"
        self.write_evidence(
            schema="taiji-release-evidence/v3",
            purpose="publication",
        )

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "publication delivery ancestor is writable by group or other",
            result.stderr,
        )
        self.assertNotIn("publication physical bundle 未通过", result.stderr)
        self.assertNotIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_publication_evidence_allows_root_owned_sticky_1777_ancestor(
        self,
    ) -> None:
        shared_root = Path("/tmp").resolve()
        shared_stat = shared_root.lstat()
        self.assertEqual(shared_stat.st_uid, 0)
        self.assertEqual(shared_stat.st_mode & 0o7777, 0o1777)

        with tempfile.TemporaryDirectory(
            prefix="taiji-publication-sticky-",
            dir=shared_root,
        ) as controlled_root:
            delivery = Path(controlled_root) / "taijiagent 打包交付"
            delivery.mkdir(mode=0o700)
            delivery = delivery.resolve()
            self.evidence = delivery / "release-evidence.json"
            self.write_evidence(
                schema="taiji-release-evidence/v3",
                purpose="publication",
            )

            result = self.run_signer()

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "publication physical bundle 未通过完整实物和签名前合同校验",
                result.stderr,
            )
            self.assertNotIn("publication delivery ancestor", result.stderr)
            self.assertNotIn("无法读取发布私钥", result.stderr)
            self.assert_no_signature()

    def test_publication_snapshot_rechecks_trusted_delivery_root_identity(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")
        start = source.index('if [ "$MODE" = "publication" ]; then')
        end = source.index('\n  /usr/bin/python3 -I -B - "$ROOT_DIR" "$SNAPSHOT_ROOT/delivery"', start)
        snapshot = source[start:end]

        self.assertIn("def validate_publication_delivery_root(", snapshot)
        self.assertIn("leaf_stat.st_uid != os.getuid()", snapshot)
        self.assertIn("ancestor_stat.st_uid not in {0, os.getuid()}", snapshot)
        self.assertIn("ancestor_mode == 0o1777", snapshot)
        self.assertIn("publication delivery ancestor has an untrusted owner", snapshot)
        self.assertIn("delivery_root_identity = validate_publication_delivery_root(", snapshot)
        self.assertIn(
            "validate_publication_delivery_root(\n"
            "    source_root, expected_identity=delivery_root_identity\n"
            ")",
            snapshot,
        )

    def test_publication_delivery_root_identity_recheck_rejects_replacement(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")
        publication_start = source.index('if [ "$MODE" = "publication" ]; then')
        helper_start = source.index("def identity(value):", publication_start)
        helper_end = source.index("\ndef copy_file(", helper_start)
        namespace = {"os": os, "stat": __import__("stat")}
        exec(source[helper_start:helper_end], namespace)
        validate_root = namespace["validate_publication_delivery_root"]

        delivery = (self.root / "trusted-delivery").resolve()
        delivery.mkdir(mode=0o700)
        original_identity = validate_root(delivery)
        parked = delivery.with_name("parked-delivery")
        delivery.rename(parked)
        delivery.mkdir(mode=0o700)

        with self.assertRaisesRegex(SystemExit, "changed during snapshot"):
            validate_root(delivery, expected_identity=original_identity)

    def test_publication_snapshot_heredoc_uses_python38_grammar(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")
        publication_start = source.index('if [ "$MODE" = "publication" ]; then')
        heredoc_start = source.index("import os\n", publication_start)
        heredoc_end = source.index(
            '\nPY\n\n  /usr/bin/python3 -I -B - "$ROOT_DIR" "$SNAPSHOT_ROOT/delivery"',
            heredoc_start,
        )

        ast.parse(source[heredoc_start:heredoc_end], feature_version=8)

    def test_publication_trust_helper_has_stable_python38_gate_markers(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")
        begin = "# TAIJI_PYTHON38_PUBLICATION_TRUST_HELPER_BEGIN"
        end = "# TAIJI_PYTHON38_PUBLICATION_TRUST_HELPER_END"

        self.assertEqual(source.count(begin), 1)
        self.assertEqual(source.count(end), 1)
        self.assertLess(source.index(begin), source.index("def identity(value):"))
        self.assertLess(
            source.index("def validate_publication_delivery_root("),
            source.index(end),
        )

    def test_python38_gate_executes_publication_trust_helper_behavior(self) -> None:
        gate_source = PYTHON38_GATE.read_text(encoding="utf-8")
        self.assertIn(
            "exercise_publication_delivery_trust_helper(temp_root)", gate_source
        )
        namespace = runpy.run_path(str(PYTHON38_GATE))
        extract_helper = namespace["extract_publication_delivery_trust_helper"]
        exercise_helper = namespace["exercise_publication_delivery_trust_helper"]

        helper_source = extract_helper()
        compile(helper_source, str(SIGNER), "exec")
        with tempfile.TemporaryDirectory(
            prefix="taiji-python38-publication-trust-"
        ) as temp_dir:
            exercise_helper(Path(temp_dir).resolve())

    def test_python38_gate_exercises_agent_runner_with_actual_runpy_namespace(self) -> None:
        gate_namespace = runpy.run_path(str(PYTHON38_GATE))
        agent_runner = runpy.run_path(str(gate_namespace["AGENT_PARALLEL_RUNNER"]))
        gate_namespace["exercise_agent_parallel_runner"](agent_runner)

    def test_publication_evidence_rejects_relative_symlinked_or_dotdot_delivery_root(
        self,
    ) -> None:
        real_delivery = self.root / "real-delivery"
        real_delivery.mkdir(mode=0o700)
        (self.root / "review-root").mkdir(mode=0o700)
        linked_delivery = self.root / "linked-delivery"
        linked_delivery.symlink_to(real_delivery, target_is_directory=True)
        escaped_delivery = self.root / "review-root" / ".." / "real-delivery"

        for label, evidence, cwd, argument in (
            (
                "relative",
                real_delivery / "release-evidence.json",
                real_delivery,
                "release-evidence.json",
            ),
            (
                "symlinked",
                linked_delivery / "release-evidence.json",
                ROOT,
                str(linked_delivery / "release-evidence.json"),
            ),
            (
                "dotdot",
                escaped_delivery / "release-evidence.json",
                ROOT,
                str(escaped_delivery / "release-evidence.json"),
            ),
        ):
            with self.subTest(label=label):
                self.evidence = evidence
                self.write_evidence(
                    schema="taiji-release-evidence/v3",
                    purpose="publication",
                )
                result = subprocess.run(
                    ["/bin/bash", "-p", str(SIGNER), argument, str(self.private_key)],
                    cwd=cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("absolute real delivery root", result.stderr)
                self.assertNotIn("无法读取发布私钥", result.stderr)
                self.assert_no_signature()

    def test_publication_snapshot_keeps_swap_and_escape_guards(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")
        start = source.index('if [ "$MODE" = "publication" ]; then')
        end = source.index('\n  /usr/bin/python3 -I -B - "$ROOT_DIR" "$SNAPSHOT_ROOT/delivery"', start)
        snapshot = source[start:end]

        self.assertIn('getattr(os, "O_NOFOLLOW", 0)', snapshot)
        self.assertIn("before.st_nlink != 1", snapshot)
        self.assertIn("identity(opened) != identity(current)", snapshot)
        self.assertIn("selected_names(source, at_root) != names", snapshot)
        self.assertIn("source.is_symlink()", snapshot)

    def test_certification_snapshot_streams_only_the_exact_previous_deb_up_to_2gib(
        self,
    ) -> None:
        source = SIGNER.read_text(encoding="utf-8")
        start = source.index('if [ "$MODE" = "certification" ]; then')
        end = source.index('\n  /usr/bin/python3 -I -B - "$ROOT_DIR" "$SNAPSHOT_EVIDENCE"', start)
        snapshot = source[start:end]
        copy_start = snapshot.index("def copy_file(")
        copy_end = snapshot.index("\ndef copy_tree(", copy_start)
        copy_file = snapshot[copy_start:copy_end]

        self.assertIn("MAX_PREVIOUS_RELEASE_DEB_BYTES = 2 * 1024 * 1024 * 1024", snapshot)
        self.assertIn(
            "MAX_CERTIFICATION_SNAPSHOT_FILE_BYTES = 1024 * 1024 * 1024",
            snapshot,
        )
        self.assertIn("previous_deb_basename", snapshot)
        self.assertIn("source == previous_deb_path", snapshot)
        self.assertIn("while remaining:", snapshot)
        self.assertIn("identity(opened) != identity(current)", copy_file)
        self.assertNotIn("read_bytes", snapshot)
        self.assertNotIn("before.st_size > 1024 * 1024 * 1024", snapshot)

    def test_publication_signer_validates_ci_trio_from_recursive_bundle_snapshot(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")

        self.assertIn('bundle_evidence = delivery / "release-evidence.json"', source)
        self.assertIn("signing evidence and recursive bundle snapshot differ", source)
        self.assertIn(
            "validator.validate_release_evidence_v3(data, bundle_evidence, args, binding)",
            source,
        )
        self.assertIn("revalidate-taiji-github-ci-evidence.py", source)
        self.assertIn(
            '"$SNAPSHOT_ROOT/delivery/github-ci-evidence.json"', source
        )
        self.assertLess(
            source.index("github-ci-live-revalidation"),
            source.index("private_fingerprint="),
        )


if __name__ == "__main__":
    unittest.main()
