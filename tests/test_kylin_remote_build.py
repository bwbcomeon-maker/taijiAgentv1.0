"""Focused contracts for the one-attempt Kylin remote build controller."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packaging/linux/kylin_remote_build.py"


def load_helper():
    if not HELPER.exists():
        raise AssertionError("kylin remote build helper is not implemented")
    spec = importlib.util.spec_from_file_location("kylin_remote_build", HELPER)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load Kylin remote build helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE_COMMIT = "a" * 40
REMOTE_ATTEMPT_ID = "b" * 16
INPUT_IDENTITY = {
    "archive": {
        "basename": "taijiagent-制包机输入-{}.tar.gz".format(SOURCE_COMMIT),
        "bytes": 1,
        "sha256": "c" * 64,
    },
    "manifest": {
        "basename": "taijiagent-制包机输入-{}.manifest.json".format(SOURCE_COMMIT),
        "bytes": 1,
        "sha256": "d" * 64,
    },
    "checksum": {
        "basename": "taijiagent-制包机输入-{}.tar.gz.sha256".format(SOURCE_COMMIT),
        "bytes": 1,
        "sha256": "e" * 64,
    },
}


def result_payload(status="RUNNING", phase="00"):
    terminal = status != "RUNNING"
    return {
        "schema": "taiji-kylin-remote-build-result/v1",
        "source_commit": SOURCE_COMMIT,
        "remote_attempt_id": REMOTE_ATTEMPT_ID,
        "input": json.loads(json.dumps(INPUT_IDENTITY, ensure_ascii=False)),
        "status": status,
        "phase": phase,
        "exit_code": (1 if status == "FAILED" else 0) if terminal else None,
        "started_at": "2026-08-24T00:00:00Z",
        "finished_at": "2026-08-24T00:01:00Z" if terminal else None,
        "remote_log": {
            "basename": "02-remote-build.log",
            "bytes": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
    }


class KylinRemoteBuildResultTests(unittest.TestCase):
    def setUp(self):
        self.module = load_helper()

    def parse(self, payload=None, **kwargs):
        value = result_payload() if payload is None else payload
        raw = value if isinstance(value, (bytes, bytearray)) else (
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        arguments = {
            "source_commit": SOURCE_COMMIT,
            "remote_attempt_id": REMOTE_ATTEMPT_ID,
            "input_identity": INPUT_IDENTITY,
        }
        arguments.update(kwargs)
        return self.module.load_remote_build_result(raw, **arguments)

    def assert_rejected(self, payload, **kwargs):
        with self.assertRaises(self.module.RemoteBuildError):
            self.parse(payload, **kwargs)

    def argv(self):
        values = [
            "--host",
            "kylin",
            "--account-home",
            "/home/kylin",
            "--remote-dir",
            "/home/kylin/taiji-builds/{}/{}".format(
                SOURCE_COMMIT, REMOTE_ATTEMPT_ID
            ),
            "--source-commit",
            SOURCE_COMMIT,
            "--remote-attempt-id",
            REMOTE_ATTEMPT_ID,
        ]
        for key in ("archive", "manifest", "checksum"):
            values.extend(
                [
                    "--{}-basename".format(key),
                    INPUT_IDENTITY[key]["basename"],
                    "--{}-bytes".format(key),
                    str(INPUT_IDENTITY[key]["bytes"]),
                    "--{}-sha256".format(key),
                    INPUT_IDENTITY[key]["sha256"],
                ]
            )
        return values

    def test_result_parser_accepts_strict_running_shape_and_decides_poll(self):
        result = self.parse()
        self.assertEqual(result["status"], "RUNNING")
        self.assertEqual(self.module.decide_remote_build_action(result), "POLL")

    def test_decision_matrix_has_only_missing_start(self):
        self.assertEqual(self.module.decide_remote_build_action(None), "START")
        self.assertEqual(
            self.module.decide_remote_build_action(self.parse(result_payload("FAILED"))),
            "FAIL",
        )
        self.assertEqual(
            self.module.decide_remote_build_action(self.parse(result_payload("SUCCEEDED"))),
            "CONTINUE",
        )

    def test_rejects_invalid_utf8_json_duplicate_keys_and_shape(self):
        self.assert_rejected(b"\xff")
        self.assert_rejected(b"{")
        duplicate = b'{"schema":"taiji-kylin-remote-build-result/v1","schema":"x"}'
        self.assert_rejected(duplicate)

        missing = result_payload()
        del missing["remote_log"]
        self.assert_rejected(missing)
        extra = result_payload()
        extra["unexpected"] = True
        self.assert_rejected(extra)

        for field, value in (
            ("source_commit", 1),
            ("remote_attempt_id", True),
            ("input", []),
            ("status", 1),
            ("phase", None),
            ("exit_code", "0"),
            ("started_at", 1),
            ("finished_at", []),
            ("remote_log", "log"),
        ):
            malformed = result_payload()
            malformed[field] = value
            self.assert_rejected(malformed)

    def test_rejects_unknown_status_phase_bad_time_digest_basename_and_size(self):
        for field, value in (("status", "UNKNOWN"), ("phase", "extract")):
            malformed = result_payload()
            malformed[field] = value
            self.assert_rejected(malformed)

        for timestamp in ("2026-08-24", "2026-13-24T00:00:00Z", "2026-08-24T00:00:00+00:00"):
            malformed = result_payload()
            malformed["started_at"] = timestamp
            self.assert_rejected(malformed)

        malformed = result_payload()
        malformed["input"]["archive"]["basename"] = "../escape"
        self.assert_rejected(malformed)
        malformed = result_payload()
        malformed["input"]["manifest"]["bytes"] = -1
        self.assert_rejected(malformed)
        malformed = result_payload()
        malformed["input"]["checksum"]["sha256"] = "z" * 64
        self.assert_rejected(malformed)
        malformed = result_payload()
        malformed["remote_log"]["bytes"] = 2**63
        self.assert_rejected(malformed)

    def test_rejects_terminal_and_running_state_invariant_violations(self):
        malformed = result_payload("FAILED")
        malformed["exit_code"] = None
        self.assert_rejected(malformed)
        malformed = result_payload("SUCCEEDED")
        malformed["finished_at"] = None
        self.assert_rejected(malformed)
        malformed = result_payload()
        malformed["exit_code"] = 0
        self.assert_rejected(malformed)
        malformed = result_payload()
        malformed["finished_at"] = "2026-08-24T00:01:00Z"
        self.assert_rejected(malformed)

    def test_rejects_source_attempt_and_exact_input_identity_mismatch(self):
        for kwargs in (
            {"source_commit": "f" * 40},
            {"remote_attempt_id": "c" * 16},
            {"input_identity": {**INPUT_IDENTITY, "archive": {**INPUT_IDENTITY["archive"], "bytes": 2}}},
        ):
            self.assert_rejected(result_payload(), **kwargs)

    def test_disconnect_reentry_uses_same_attempt_and_starts_only_once(self):
        module = self.module
        running = self.parse(result_payload("RUNNING"))
        succeeded = self.parse(result_payload("SUCCEEDED", phase="review"))
        query = mock.Mock(
            side_effect=[
                None,
                module.RemoteBuildError("simulated SSH stream loss"),
                running,
                succeeded,
            ]
        )
        launch = mock.Mock()
        with mock.patch.object(module, "_query_remote", query), mock.patch.object(
            module, "_launch_remote", launch
        ), mock.patch.object(module.time, "sleep") as sleep:
            self.assertEqual(module.main(self.argv()), 2)
            self.assertEqual(module.main(self.argv()), 0)
        launch.assert_called_once()
        sleep.assert_called_once_with(300)

    def test_main_never_launches_for_running_failed_or_succeeded(self):
        module = self.module
        for status, expected in (("FAILED", 1), ("SUCCEEDED", 0)):
            launch = mock.Mock()
            with mock.patch.object(
                module, "_query_remote", return_value=self.parse(result_payload(status))
            ), mock.patch.object(module, "_launch_remote", launch):
                self.assertEqual(module.main(self.argv()), expected)
            launch.assert_not_called()

        query = mock.Mock(
            side_effect=[
                self.parse(result_payload("RUNNING")),
                self.parse(result_payload("SUCCEEDED", phase="review")),
            ]
        )
        launch = mock.Mock()
        with mock.patch.object(module, "_query_remote", query), mock.patch.object(
            module, "_launch_remote", launch
        ), mock.patch.object(module.time, "sleep"):
            self.assertEqual(module.main(self.argv()), 0)
        launch.assert_not_called()

    def test_detached_launcher_claims_running_and_executes_worker_only_once(self):
        module = self.module
        with tempfile.TemporaryDirectory(prefix="kylin-launch-test-") as temp_dir:
            remote_dir = Path(temp_dir)
            counter = remote_dir / "fake-00-counter"
            fake_worker = "\n".join(
                [
                    "set -Eeuo pipefail",
                    "counter={}".format(module._shell_quote(str(counter))),
                    "if [ -e \"$counter\" ]; then exit 70; fi",
                    "printf '1\\n' > \"$counter\"",
                ]
            )
            with mock.patch.object(module, "_worker_script", return_value=fake_worker):
                script = module._launch_script(
                    str(remote_dir),
                    str(remote_dir),
                    SOURCE_COMMIT,
                    REMOTE_ATTEMPT_ID,
                    INPUT_IDENTITY,
                    "remote-build-result.json",
                )
            trace = remote_dir / "launcher-trace.log"
            script = script.replace(
                "</dev/null >/dev/null 2>&1 &",
                "</dev/null >{} 2>&1 &".format(module._shell_quote(str(trace))),
            )
            if not Path("/usr/bin/chmod").exists():
                script = script.replace("/usr/bin/chmod 0600 --", "/bin/chmod 0600")
            if not Path("/usr/bin/ln").exists():
                script = script.replace("/usr/bin/ln --", "/bin/ln")
            if not Path("/usr/bin/unlink").exists():
                script = script.replace("/usr/bin/unlink --", "/bin/rm -f")

            for _ in range(2):
                completed = subprocess.run(
                    ["/bin/bash", "-p", "-c", script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode, 0, completed.stderr.decode("utf-8")
                )

            deadline = time.monotonic() + 3
            result_path = remote_dir / "remote-build-result.json"
            while time.monotonic() < deadline and not (
                counter.exists() and result_path.exists()
            ):
                time.sleep(0.01)
            self.assertTrue(
                counter.exists(),
                trace.read_text(encoding="utf-8") if trace.exists() else "no trace",
            )
            self.assertEqual(counter.read_text(encoding="ascii"), "1\n")
            running = module.load_remote_build_result(
                result_path.read_bytes(),
                source_commit=SOURCE_COMMIT,
                remote_attempt_id=REMOTE_ATTEMPT_ID,
                input_identity=INPUT_IDENTITY,
            )
            self.assertEqual(running["status"], "RUNNING")

    def test_terminal_renderer_emits_json_accepted_by_the_production_parser(self):
        module = self.module
        command = module._result_printf(
            INPUT_IDENTITY,
            status="$terminal_status",
            phase="$terminal_phase",
            exit_code="$terminal_code",
            started_var="$started_at",
            finished_var="$finished_at",
            log_bytes_var="$log_bytes",
            log_sha_var="$log_sha",
        )
        script = "\n".join(
            [
                "set -Eeuo pipefail",
                "source_commit={}".format(SOURCE_COMMIT),
                "remote_attempt_id={}".format(REMOTE_ATTEMPT_ID),
                "terminal_status=SUCCEEDED",
                "terminal_phase=review",
                "terminal_code=0",
                "started_at=2026-08-24T00:00:00Z",
                "finished_at=2026-08-24T00:01:00Z",
                "log_bytes=0",
                "log_sha={}".format(hashlib.sha256(b"").hexdigest()),
                command,
            ]
        )
        completed = subprocess.run(
            ["/bin/bash", "-p", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
        parsed = module.load_remote_build_result(
            completed.stdout,
            source_commit=SOURCE_COMMIT,
            remote_attempt_id=REMOTE_ATTEMPT_ID,
            input_identity=INPUT_IDENTITY,
        )
        self.assertEqual(parsed["status"], "SUCCEEDED")
        self.assertEqual(parsed["finished_at"], "2026-08-24T00:01:00Z")

    def test_worker_preserves_builder_and_tee_failures(self):
        script = self.module._worker_script(
            "/home/kylin/taiji-builds/{}/{}".format(
                SOURCE_COMMIT, REMOTE_ATTEMPT_ID
            ),
            SOURCE_COMMIT,
            REMOTE_ATTEMPT_ID,
            INPUT_IDENTITY,
            "2026-08-24T00:00:00Z",
        )
        self.assertIn('pipeline_status=("${PIPESTATUS[@]}")', script)
        self.assertIn('build_status=${pipeline_status[0]}', script)
        self.assertIn('tee_status=${pipeline_status[1]}', script)
        self.assertIn('if [ "$tee_status" -ne 0 ]; then exit "$tee_status"; fi', script)

    def test_worker_passes_each_archive_immediately_after_tar_f_option(self):
        remote_dir = "/home/kylin/taiji-builds/{}/{}".format(
            SOURCE_COMMIT, REMOTE_ATTEMPT_ID
        )
        script = self.module._worker_script(
            remote_dir,
            SOURCE_COMMIT,
            REMOTE_ATTEMPT_ID,
            INPUT_IDENTITY,
            "2026-08-24T00:00:00Z",
        )
        archive = self.module._shell_quote(INPUT_IDENTITY["archive"]["basename"])
        source_archive = self.module._shell_quote(
            "{}/taijiagent 打包交付/taiji-agentv1.0-kylin-build-src-{}.tar.gz".format(
                remote_dir, SOURCE_COMMIT
            )
        )

        self.assertNotIn("-xzf --", script)
        self.assertIn("-xzf {}".format(archive), script)
        self.assertIn("-xzf {} -C ".format(source_archive), script)


if __name__ == "__main__":
    unittest.main()
