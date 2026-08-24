"""Formal Linux build-toolchain contract tests.

These tests deliberately cover the sales-build entry points rather than the
developer setup defaults.  A formal DEB may only be produced from one pinned
uv archive and one immutable uv.lock resolution.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
SETUP = ROOT / "hermes-local-lab/scripts/setup-local.sh"
DEB_BUILDER = ROOT / "packaging/linux/deb/build-deb.sh"
PREFLIGHT = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
PREPARE = ROOT / "taijiagent 打包交付/99_本机_准备制包输入包.sh"
LOCK_HELPER = ROOT / "packaging/linux/verify-python-lock-contract.py"
VALIDATOR = ROOT / "scripts/validate-taiji-release-evidence.py"
ASSEMBLER = ROOT / "scripts/assemble-taiji-release-evidence.py"
SOURCE_INTEGRITY_HELPER = ROOT / "packaging/linux/source-archive-integrity.py"


TOOLCHAIN_FIELDS = {
    "python_dependency_lock_status",
    "python_lock_basename",
    "python_lock_sha256",
    "python_version",
    "python_archive_sha256",
    "python_executable_sha256",
    "uv_version",
    "uv_archive_sha256",
    "uv_executable_sha256",
    "node_version",
    "node_archive_sha256",
    "node_executable_sha256",
    "electron_version",
    "electron_archive_sha256",
    "electron_executable_sha256",
}
UV_EXECUTABLE_SHA256 = "72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2"
NODE_EXECUTABLE_SHA256 = "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"
PYTHON_ARCHIVE_SHA256 = "2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a"
PYTHON_EXECUTABLE_SHA256 = "5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba"
ELECTRON_EXECUTABLE_SHA256 = "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d"


class StrictBuildToolchainContractTests(unittest.TestCase):
    def test_formal_builder_pins_uv_and_has_no_online_installer_or_path_fallback(self):
        builder = BUILDER.read_text(encoding="utf-8")

        self.assertIn('UV_VERSION="0.12.2"', builder)
        self.assertIn(
            'UV_ARCHIVE_URL="https://github.com/astral-sh/uv/releases/download/0.12.2/uv-x86_64-unknown-linux-gnu.tar.gz"',
            builder,
        )
        self.assertIn(
            'UV_ARCHIVE_SHA256="d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4"',
            builder,
        )
        self.assertIn(f'UV_PINNED_EXECUTABLE_SHA256="{UV_EXECUTABLE_SHA256}"', builder)
        self.assertNotIn("https://astral.sh/uv/install.sh", builder)
        self.assertNotIn("command -v uv", builder)
        self.assertIn('UV_BIN="$UV_ROOT/current/uv"', builder)
        self.assertIn('file "$UV_BIN"', builder)
        self.assertIn('stat -c \'%h\' "$UV_BIN"', builder)
        self.assertIn('stat -c \'%u\' "$UV_BIN"', builder)
        self.assertIn(f'NODE_PINNED_EXECUTABLE_SHA256="{NODE_EXECUTABLE_SHA256}"', builder)
        self.assertIn('[ "$NODE_EXECUTABLE_SHA256" = "$NODE_PINNED_EXECUTABLE_SHA256" ]', builder)

    def test_pinned_uv_binary_uses_its_target_qualified_version_identity(self):
        builder = BUILDER.read_text(encoding="utf-8")
        deb_builder = DEB_BUILDER.read_text(encoding="utf-8")

        self.assertIn(
            '[ "$("$UV_BIN" --version)" = "uv $UV_VERSION (x86_64-unknown-linux-gnu)" ]',
            builder,
        )
        self.assertIn(
            '[ "$("$UV_EXECUTABLE" --version)" = "uv $PINNED_UV_VERSION (x86_64-unknown-linux-gnu)" ]',
            deb_builder,
        )

    def test_formal_builder_pins_the_complete_python_archive_and_binary_identity(self):
        builder = BUILDER.read_text(encoding="utf-8")
        setup = SETUP.read_text(encoding="utf-8")

        self.assertIn('PYTHON_VERSION_PINNED="3.11.15"', builder)
        self.assertIn(
            'PYTHON_ARCHIVE_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260805/cpython-3.11.15%2B20260805-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"',
            builder,
        )
        self.assertIn(PYTHON_ARCHIVE_SHA256, builder)
        self.assertIn(PYTHON_EXECUTABLE_SHA256, builder)
        self.assertIn("UV_PYTHON_DOWNLOADS=never", builder)
        self.assertIn('TAIJI_PYTHON_EXECUTABLE', setup)
        self.assertIn('--python "$PYTHON_EXECUTABLE"', setup)

    def test_formal_builder_prefers_an_exact_adjacent_uv_archive_before_downloading(self):
        builder = BUILDER.read_text(encoding="utf-8")
        ensure_uv = builder[
            builder.index("ensure_uv() {") : builder.index("\n}\n\nensure_python()", builder.index("ensure_uv() {"))
        ]

        self.assertIn('prestaged_archive="$SCRIPT_DIR/../$UV_ARCHIVE"', ensure_uv)
        self.assertIn('[ ! -L "$prestaged_archive" ]', ensure_uv)
        self.assertIn('stat -c \'%h\' "$prestaged_archive"', ensure_uv)
        self.assertIn('stat -c \'%u\' "$prestaged_archive"', ensure_uv)
        self.assertIn("$((8#$prestaged_mode & 8#022))", ensure_uv)
        self.assertIn("预置 uv 归档不允许 group/other 写入", ensure_uv)
        self.assertIn('install -m 0600 -- "$prestaged_archive" "$UV_ARCHIVE_PATH"', ensure_uv)
        self.assertLess(
            ensure_uv.index('install -m 0600 -- "$prestaged_archive" "$UV_ARCHIVE_PATH"'),
            ensure_uv.index('curl_download "$UV_ARCHIVE_URL" "$UV_ARCHIVE_PATH"'),
        )
        self.assertIn("else\n    info \"下载固定版 uv", ensure_uv)

    def test_formal_builder_prefers_an_exact_adjacent_python_archive_before_downloading(self):
        builder = BUILDER.read_text(encoding="utf-8")
        ensure_python = builder[
            builder.index("ensure_python() {") : builder.index(
                "\n}\n\nvalidate_formal_uv_contract()", builder.index("ensure_python() {")
            )
        ]

        self.assertIn('prestaged_archive="$SCRIPT_DIR/../$PYTHON_ARCHIVE"', ensure_python)
        self.assertIn('[ ! -L "$prestaged_archive" ]', ensure_python)
        self.assertIn('stat -c \'%h\' "$prestaged_archive"', ensure_python)
        self.assertIn('stat -c \'%u\' "$prestaged_archive"', ensure_python)
        self.assertIn("$((8#$prestaged_mode & 8#022))", ensure_python)
        self.assertIn("预置 Python 归档不允许 group/other 写入", ensure_python)
        self.assertIn(
            'install -m 0600 -- "$prestaged_archive" "$PYTHON_ARCHIVE_PATH"',
            ensure_python,
        )
        self.assertLess(
            ensure_python.index(
                'install -m 0600 -- "$prestaged_archive" "$PYTHON_ARCHIVE_PATH"'
            ),
            ensure_python.index(
                'curl_download "$PYTHON_ARCHIVE_URL" "$PYTHON_ARCHIVE_PATH"'
            ),
        )
        self.assertIn("else\n    info \"下载固定版 CPython", ensure_python)

    def test_formal_builder_is_strict_only_and_never_refreshes_lock(self):
        builder = BUILDER.read_text(encoding="utf-8")

        self.assertIn("validate_formal_uv_contract", builder)
        self.assertIn('uv_lock_mode="${TAIJI_UV_LOCK_MODE:-strict}"', builder)
        self.assertIn('""|strict)', builder)
        self.assertIn("auto|unlocked", builder)
        self.assertNotIn("uv lock", builder)
        self.assertNotIn("fallback-unlocked", builder)
        self.assertNotIn("explicit-unlocked", builder)
        self.assertIn("lock 在 strict sync 前后发生变化", builder)
        self.assertIn('PYTHON_DEPENDENCY_LOCK_STATUS="strict-locked"', builder)

    def test_fixed_tool_archives_are_hashed_scanned_and_extracted_from_one_open_file(self):
        builder = BUILDER.read_text(encoding="utf-8")

        self.assertIn("open_fixed_tool_archive", builder)
        self.assertIn('FIXED_TOOL_ARCHIVE_FD_PATH="/proc/self/fd/9"', builder)
        self.assertIn(
            'verify_sealed_snapshot "$FIXED_TOOL_ARCHIVE_FD_PATH"',
            builder,
        )
        self.assertIn('python3 - "$FIXED_TOOL_ARCHIVE_FD_PATH"', builder)
        self.assertIn('-xzf "$FIXED_TOOL_ARCHIVE_FD_PATH"', builder)
        self.assertIn('-xJf "$FIXED_TOOL_ARCHIVE_FD_PATH"', builder)
        self.assertNotIn('-xzf "$UV_ARCHIVE_PATH"', builder)
        self.assertNotIn('-xzf "$PYTHON_ARCHIVE_PATH"', builder)
        self.assertNotIn('-xJf "$tmp_dir/$tarball"', builder)
        self.assertIn(
            'adopt_sealed_snapshot "$SRC_ARCHIVE" "$source_archive_hash" archive',
            builder,
        )
        self.assertNotIn('tar -xzf "$SRC_ARCHIVE"', builder)
        for tool in ("uv", "python", "node"):
            self.assertIn(
                "retain_fixed_tool_archive_snapshot {}".format(tool),
                builder,
            )
        deb_builder = DEB_BUILDER.read_text(encoding="utf-8")
        self.assertIn("adopt_sealed_build_inputs", deb_builder)
        self.assertLess(
            deb_builder.rindex("\nadopt_sealed_build_inputs\n"),
            deb_builder.rindex("\nvalidate_strict_toolchain_contract\n"),
        )
        for required in (
            "fcntl.F_GET_SEALS",
            "fcntl.F_SEAL_WRITE",
            "fcntl.F_SEAL_GROW",
            "fcntl.F_SEAL_SHRINK",
            "fcntl.F_SEAL_SEAL",
        ):
            self.assertIn(required, deb_builder)
        self.assertIn("stat.S_IMODE(metadata.st_mode)", deb_builder)
        self.assertIn('expected_mode_text not in ("0400", "0500")', deb_builder)
        self.assertIn("!= expected_mode", deb_builder)
        adopt_start = deb_builder.index("adopt_sealed_build_inputs() {")
        adopt_end = deb_builder.index("\n}\n\nvalidate_source_archive_integrity", adopt_start)
        adopt_function = deb_builder[adopt_start:adopt_end]
        python_start = adopt_function.index("<<'PY'\n") + len("<<'PY'\n")
        python_end = adopt_function.index("\nPY", python_start)
        ast.parse(
            adopt_function[python_start:python_end],
            filename="build-deb-sealed-inputs.py",
            feature_version=(3, 8),
        )

    def test_fixed_tool_archives_use_one_kernel_sealed_snapshot(self):
        builder = BUILDER.read_text(encoding="utf-8")
        source_start = builder.index("sealed_snapshot_python_source() {")
        heredoc_start = builder.index("<<'PY'\n", source_start) + len("<<'PY'\n")
        heredoc_end = builder.index("\nPY\n", heredoc_start)
        snapshot_python = builder[heredoc_start:heredoc_end]
        ast.parse(
            snapshot_python,
            filename="sealed-snapshot-embedded.py",
            feature_version=(3, 8),
        )
        self.assertIn(
            'required_os = ("memfd_create", "MFD_ALLOW_SEALING", "MFD_CLOEXEC")',
            snapshot_python,
        )
        self.assertIn("os.MFD_CLOEXEC", snapshot_python)

        for required in (
            "os.memfd_create",
            "os.MFD_ALLOW_SEALING",
            "fcntl.F_ADD_SEALS",
            "fcntl.F_GET_SEALS",
            "fcntl.F_SEAL_WRITE",
            "fcntl.F_SEAL_GROW",
            "fcntl.F_SEAL_SHRINK",
            "fcntl.F_SEAL_SEAL",
        ):
            self.assertIn(required, snapshot_python)

        open_start = builder.index("open_fixed_tool_archive() {")
        open_end = builder.index("\n}\n\nrequire_open_fixed_tool_archive_unchanged", open_start)
        open_function = builder[open_start:open_end]
        self.assertIn("sealed_snapshot_python_source", open_function)
        self.assertIn("adopt_sealed_snapshot", open_function)
        self.assertIn('FIXED_TOOL_ARCHIVE_FD_PATH="$adopted_path"', builder)
        self.assertNotIn('exec 9<"$archive_path"', open_function)
        self.assertIn('"$snapshot_python" create', builder)

        with tempfile.TemporaryDirectory(prefix="taiji-unsealed-snapshot-") as temp:
            candidate = Path(temp) / "candidate.tar.gz"
            candidate.write_bytes(b"ordinary mutable file")
            expected = hashlib.sha256(candidate.read_bytes()).hexdigest()
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    snapshot_python,
                    "verify",
                    str(candidate),
                    expected,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sealed", result.stderr.lower())
        python38_gate = (ROOT / "tests/python38_linux_packaging_gate.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("extract_sealed_snapshot_python", python38_gate)
        self.assertIn("exercise_sealed_snapshot_python(temp_root)", python38_gate)

    def test_candidate_build_uses_sealed_node_and_npm_before_any_build_argv(self):
        builder = BUILDER.read_text(encoding="utf-8")
        deb_builder = DEB_BUILDER.read_text(encoding="utf-8")
        main = builder[builder.index("main() {") :]
        build_start = builder.index("build_runtime_and_deb() {")
        build_end = builder.index("\n}\n\ncollect_artifacts", build_start)
        build = builder[build_start:build_end]

        self.assertLess(
            main.index("seal_build_node_runtime"),
            main.index("build_runtime_and_deb"),
        )
        for required in (
            "BUILD_NODE_HELD_PATH",
            "BUILD_NPM_CLI_HELD_PATH",
            "run_build_node_script",
            "run_build_npm",
        ):
            self.assertIn(required, builder)
        self.assertIn("run_build_npm", build)
        self.assertIn("run_build_node", build)
        self.assertNotIn("\n  npm --version", build)
        self.assertNotIn("\n  node scripts/", build)
        self.assertIn(
            'TAIJI_PACKAGED_NODE_EXECUTABLE="$BUILD_NODE_HELD_PATH"',
            build,
        )

        self.assertIn(
            'PACKAGED_NODE_EXECUTABLE="${TAIJI_PACKAGED_NODE_EXECUTABLE:-}"',
            deb_builder,
        )
        validation_start = deb_builder.index("validate_strict_toolchain_contract() {")
        validation_end = deb_builder.index("\n}\n\nvalidate_locked_python_environment", validation_start)
        validation = deb_builder[validation_start:validation_end]
        self.assertLess(
            validation.index('sha256sum "$PACKAGED_NODE_EXECUTABLE"'),
            validation.index('"$PACKAGED_NODE_EXECUTABLE" --version'),
        )
        self.assertIn(
            '"$PACKAGED_NODE_EXECUTABLE" "$DESKTOP_JS_STAGER"',
            deb_builder,
        )

    def test_sealed_npm_uses_two_distinct_controlled_config_files(self):
        builder = BUILDER.read_text(encoding="utf-8")
        prepare_start = builder.index("prepare_build_npm_configs() {")
        prepare_end = builder.index("\n}\n", prepare_start)
        prepare = builder[prepare_start:prepare_end]
        candidate_start = builder.index("run_build_node_script() {")
        candidate_end = builder.index("\n}\n", candidate_start)
        candidate = builder[candidate_start:candidate_end]
        seal_start = builder.index("seal_build_node_runtime() {")
        seal_end = builder.index("\n}\n", seal_start)
        seal = builder[seal_start:seal_end]
        formal_start = builder.index("run_held_node_script() {")
        formal_end = builder.index("\n}\n", formal_start)
        formal = builder[formal_start:formal_end]

        self.assertIn(
            'BUILD_NPM_USERCONFIG="$BUILD_TMP_DIR/npm-userconfig"', prepare
        )
        self.assertIn(
            'BUILD_NPM_GLOBALCONFIG="$BUILD_TMP_DIR/npm-globalconfig"', prepare
        )
        self.assertIn('prepare_build_npm_configs', seal)
        for runner in (candidate, formal):
            self.assertIn(
                'NPM_CONFIG_USERCONFIG="$BUILD_NPM_USERCONFIG"', runner
            )
            self.assertIn(
                'NPM_CONFIG_GLOBALCONFIG="$BUILD_NPM_GLOBALCONFIG"', runner
            )
            self.assertNotIn(
                "NPM_CONFIG_USERCONFIG=/dev/null", runner
            )
            self.assertNotIn(
                "NPM_CONFIG_GLOBALCONFIG=/dev/null", runner
            )

    def test_success_marker_is_atomically_published_only_after_the_final_gate(self):
        builder = BUILDER.read_text(encoding="utf-8")
        collect_start = builder.index("collect_artifacts() {")
        collect_end = builder.index("\n}\n", collect_start)
        collect = builder[collect_start:collect_end]
        main = builder[builder.index("main() {") :]

        self.assertNotIn('> "$BUILD_MARKER"', collect)
        self.assertIn("write_pending_build_marker", main)
        self.assertIn("stage_pending_build_marker_for_publication", main)
        self.assertIn("publish_build_success_marker", main)
        publish_call = main.index("\n  publish_build_success_marker\n")
        self.assertLess(main.index("run_release_preflight"), publish_call)
        self.assertLess(
            main.index("cleanup_temporary_build_root"),
            publish_call,
        )
        self.assertNotIn('mv -- "$PENDING_BUILD_MARKER" "$BUILD_MARKER"', builder)
        self.assertIn('os.link(source, destination, follow_symlinks=False)', builder)
        self.assertIn('os.unlink(source)', builder)
        self.assertNotIn('rm -f -- "$PENDING_BUILD_MARKER"', builder)

    def test_candidate_deb_and_sidecar_are_no_clobber_held_fd_outputs(self):
        builder = BUILDER.read_text(encoding="utf-8")
        collect_start = builder.index("collect_artifacts() {")
        collect_end = builder.index("\n}\n\nvalidate_formal_test_python_identity", collect_start)
        collect = builder[collect_start:collect_end]
        require_start = builder.index("candidate_deb_identity_matches() {")
        require_end = builder.index("\n}\n", require_start)
        require = builder[require_start:require_end]
        main = builder[builder.index("main() {") :]

        self.assertNotIn("rm -f", collect)
        self.assertNotIn("cp -f", collect)
        self.assertNotIn('> "$OUTPUT_DIR/$deb_name.sha256"', collect)
        self.assertIn("set -o noclobber", collect)
        self.assertIn("CANDIDATE_DEB_FD", collect)
        self.assertIn("CANDIDATE_DEB_SIDECAR_FD", collect)
        self.assertIn("held_file_identity_and_sha256", require)
        self.assertIn("CANDIDATE_DEB_SIDECAR_EXPECTED_SHA256", require)
        self.assertGreaterEqual(builder.count("require_candidate_deb_fixed"), 12)

    def test_success_marker_publication_never_overwrites_an_existing_marker(self):
        builder = BUILDER.read_text(encoding="utf-8")
        start = builder.index("publish_build_success_marker() {")
        end = builder.index("\nstage_pending_build_marker_for_publication() {", start)
        publish_function = builder[start:end]
        with tempfile.TemporaryDirectory(prefix="taiji-build-marker-") as temp_dir:
            root = Path(temp_dir)
            harness = root / "publish-marker.sh"
            harness.write_text(
                "\n".join(
                    (
                        "#!/usr/bin/env bash",
                        "set -Eeuo pipefail",
                        'OUTPUT_DIR="$1"',
                        'BUILD_MARKER="$OUTPUT_DIR/.build-success"',
                        'PENDING_BUILD_MARKER="$OUTPUT_DIR/.build-success.pending.$$"',
                        'PENDING_BUILD_MARKER_SHA256=""',
                        'PUBLISHED_BUILD_MARKER_IDENTITY=""',
                        'PUBLISHED_BUILD_MARKER_SHA256=""',
                        'PUBLISHED_BUILD_MARKER_POISON="$OUTPUT_DIR/.build-success.poisoned.$$"',
                        'fail() { printf "FAIL:%s\\n" "$*" >&2; exit 23; }',
                        'require_candidate_deb_fixed() { :; }',
                        'require_pending_build_marker_identity() { :; }',
                        'close_pending_build_marker_fd() { :; }',
                        'require_published_build_marker_identity() { :; }',
                        publish_function,
                        'printf "candidate\\n" > "$PENDING_BUILD_MARKER"',
                        'PENDING_BUILD_MARKER_SHA256="$(sha256sum "$PENDING_BUILD_MARKER" | awk \'{print $1}\')"',
                        'if [ "${2:-}" = occupied ]; then printf "existing\\n" > "$BUILD_MARKER"; fi',
                        'if [ "${2:-}" = tampered ]; then printf "malicious\\n" > "$PENDING_BUILD_MARKER"; fi',
                        "publish_build_success_marker",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            occupied = root / "occupied"
            occupied.mkdir()
            blocked = subprocess.run(
                ["bash", str(harness), str(occupied), "occupied"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(blocked.returncode, 23, blocked.stdout + blocked.stderr)
            self.assertEqual((occupied / ".build-success").read_text(), "existing\n")
            self.assertEqual(len(list(occupied.glob(".build-success.pending.*"))), 1)

            free = root / "free"
            free.mkdir()
            published = subprocess.run(
                ["bash", str(harness), str(free)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(published.returncode, 0, published.stdout + published.stderr)
            self.assertEqual((free / ".build-success").read_text(), "candidate\n")
            self.assertEqual(list(free.glob(".build-success.pending.*")), [])

            tampered = root / "tampered"
            tampered.mkdir()
            blocked = subprocess.run(
                ["bash", str(harness), str(tampered), "tampered"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(blocked.returncode, 23, blocked.stdout + blocked.stderr)
            self.assertFalse((tampered / ".build-success").exists())

    def test_success_marker_publication_detects_fsync_path_replacement(self):
        builder = BUILDER.read_text(encoding="utf-8")
        function_start = builder.index("publish_build_success_marker() {")
        function_end = builder.index(
            "\nstage_pending_build_marker_for_publication() {", function_start
        )
        publish_function = builder[function_start:function_end]
        python_start = publish_function.index("import hashlib\n")
        python_end = publish_function.index("\nPY\n", python_start)
        embedded_python = publish_function[python_start:python_end]

        with tempfile.TemporaryDirectory(prefix="taiji-build-marker-swap-") as temp_dir:
            root = Path(temp_dir)
            source = root / ".build-success.pending"
            destination = root / ".build-success"
            legitimate = root / "legitimate-marker"
            poison = root / ".build-success.poisoned"
            source.write_text("candidate\n", encoding="utf-8")
            expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            embedded_uses_poison = "poison_path" in embedded_python
            wrapper = root / "replace-marker-on-fsync.py"
            wrapper.write_text(
                "\n".join(
                    (
                        "import os",
                        "import sys",
                        "source, destination, expected_sha256, poison, legitimate = sys.argv[1:]",
                        "real_fsync = os.fsync",
                        "swapped = False",
                        "def replacing_fsync(descriptor):",
                        "    global swapped",
                        "    if not swapped:",
                        "        swapped = True",
                        "        os.replace(destination, legitimate)",
                        "        with open(destination, 'w', encoding='utf-8') as stream:",
                        "            stream.write('FOREIGN_REPLACEMENT\\n')",
                        "    return real_fsync(descriptor)",
                        "os.fsync = replacing_fsync",
                        "sys.argv = ['publish-marker', source, destination, expected_sha256] + "
                        "([poison] if {!r} else [])".format(embedded_uses_poison),
                        "exec(compile({!r}, 'publish-marker-embedded.py', 'exec'))".format(
                            embedded_python
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(wrapper),
                    str(source),
                    str(destination),
                    expected_sha256,
                    str(poison),
                    str(legitimate),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(destination.read_text(encoding="utf-8"), "FOREIGN_REPLACEMENT\n")
            self.assertEqual(legitimate.read_text(encoding="utf-8"), "candidate\n")
            self.assertTrue(poison.is_file())

    def test_success_marker_staging_never_overwrites_an_existing_pending_file(self):
        builder = BUILDER.read_text(encoding="utf-8")
        start = builder.index("stage_pending_build_marker_for_publication() {")
        end = builder.index("\nrequire_candidate_deb_fixed() {", start)
        stage_function = builder[start:end]
        stage_function = stage_function.replace(
            'exec {PENDING_BUILD_MARKER_FD}< "$PENDING_BUILD_MARKER"',
            'PENDING_BUILD_MARKER_FD=9\n  exec 9< "$PENDING_BUILD_MARKER"',
        )
        self.assertNotIn('mv -- "$PENDING_BUILD_MARKER" "$staged_marker"', stage_function)
        self.assertIn("os.O_EXCL", stage_function)
        with tempfile.TemporaryDirectory(prefix="taiji-stage-marker-") as temp_dir:
            root = Path(temp_dir)
            harness = root / "stage-marker.sh"
            harness.write_text(
                "\n".join(
                    (
                        "#!/usr/bin/env bash",
                        "set -Eeuo pipefail",
                        'BUILD_ROOT="$1/build"',
                        'OUTPUT_DIR="$1/output"',
                        'mkdir -p "$BUILD_ROOT" "$OUTPUT_DIR"',
                        'PUBLISHED_BUILD_MARKER_POISON="$OUTPUT_DIR/.build-success.poisoned.$$"',
                        'PENDING_BUILD_MARKER="$BUILD_ROOT/.build-success.pending"',
                        'printf "candidate\\n" > "$PENDING_BUILD_MARKER"',
                        'PENDING_BUILD_MARKER_SHA256="$(sha256sum "$PENDING_BUILD_MARKER" | awk \'{print $1}\')"',
                        'fail() { printf "FAIL:%s\\n" "$*" >&2; exit 23; }',
                        'require_candidate_deb_fixed() { :; }',
                        'require_pending_build_marker_identity() { :; }',
                        'close_pending_build_marker_fd() { :; }',
                        'poison_pending_build_marker() { :; }',
                        'held_file_identity_and_sha256() { printf "fixture\\t%s\\n" "$(shasum -a 256 "$PENDING_BUILD_MARKER" | awk \'{print $1}\')"; }',
                        'stat() { printf "1\\n"; }',
                        stage_function,
                        'if [ "${2:-}" = occupied ]; then printf "existing\\n" > "$OUTPUT_DIR/.build-success.pending.$$"; fi',
                        "stage_pending_build_marker_for_publication",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            occupied = root / "occupied-stage"
            blocked = subprocess.run(
                ["bash", str(harness), str(occupied), "occupied"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(blocked.returncode, 23, blocked.stdout + blocked.stderr)
            self.assertEqual(
                next((occupied / "output").glob(".build-success.pending.*")).read_text(),
                "existing\n",
            )
            self.assertEqual(
                (occupied / "build/.build-success.pending").read_text(), "candidate\n"
            )

            free = root / "free-stage"
            staged = subprocess.run(
                ["bash", str(harness), str(free)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
            self.assertEqual(
                (free / "build/.build-success.pending").read_text(), "candidate\n"
            )
            self.assertEqual(
                next((free / "output").glob(".build-success.pending.*")).read_text(),
                "candidate\n",
            )

    def test_pending_marker_staging_never_deletes_a_replacement_on_error(self):
        builder = BUILDER.read_text(encoding="utf-8")
        function_start = builder.index("stage_pending_build_marker_for_publication() {")
        function_end = builder.index("\nrequire_candidate_deb_fixed() {", function_start)
        stage_function = builder[function_start:function_end]
        python_start = stage_function.index("import hashlib\n")
        python_end = stage_function.index("\nPY\n", python_start)
        embedded_python = stage_function[python_start:python_end]

        with tempfile.TemporaryDirectory(prefix="taiji-stage-marker-swap-") as temp_dir:
            root = Path(temp_dir)
            source = root / ".build-success.pending.private"
            destination = root / ".build-success.pending.public"
            legitimate = root / "legitimate-staged-marker"
            poison = root / ".build-success.poisoned"
            source.write_text("candidate\n", encoding="utf-8")
            expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            embedded_uses_poison = "poison_path" in embedded_python
            unsafe_cleanup_present = "os.unlink(destination)" in embedded_python
            wrapper = root / "replace-staged-marker-on-error.py"
            wrapper.write_text(
                "\n".join(
                    (
                        "import os",
                        "import sys",
                        "source, destination, expected_sha256, poison, legitimate = sys.argv[1:]",
                        "real_fsync = os.fsync",
                        "real_lstat = os.lstat",
                        "failed = False",
                        "swapped = False",
                        "def install_replacement():",
                        "    global swapped",
                        "    if not swapped:",
                        "        os.replace(destination, legitimate)",
                        "        with open(destination, 'w', encoding='utf-8') as stream:",
                        "            stream.write('FOREIGN_REPLACEMENT\\n')",
                        "        swapped = True",
                        "def failing_fsync(descriptor):",
                        "    global failed",
                        "    if not failed:",
                        "        failed = True",
                        "        if not {!r}:".format(unsafe_cleanup_present),
                        "            install_replacement()",
                        "        raise OSError('injected staging fsync failure')",
                        "    return real_fsync(descriptor)",
                        "def replacing_lstat(path):",
                        "    if failed and not swapped and os.fspath(path) == destination:",
                        "        opened = real_lstat(destination)",
                        "        install_replacement()",
                        "        return opened",
                        "    return real_lstat(path)",
                        "os.fsync = failing_fsync",
                        "os.lstat = replacing_lstat",
                        "sys.argv = ['stage-marker', source, destination, expected_sha256] + "
                        "([poison] if {!r} else [])".format(embedded_uses_poison),
                        "exec(compile({!r}, 'stage-marker-embedded.py', 'exec'))".format(
                            embedded_python
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(wrapper),
                    str(source),
                    str(destination),
                    expected_sha256,
                    str(poison),
                    str(legitimate),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(destination.read_text(encoding="utf-8"), "FOREIGN_REPLACEMENT\n")
            self.assertEqual(legitimate.read_text(encoding="utf-8"), "candidate\n")
            self.assertTrue(poison.is_file())

    def test_formal_builder_rejects_unlocked_mode_before_creating_build_state(self):
        with tempfile.TemporaryDirectory(prefix="taiji-formal-entry-") as tmp:
            root = Path(tmp)
            delivery = root / "delivery"
            delivery.mkdir()
            copied = delivery / BUILDER.name
            shutil.copy2(BUILDER, copied)
            state_home = root / "state"
            result = subprocess.run(
                ["bash", str(copied)],
                text=True,
                capture_output=True,
                check=False,
                env={
                    **os.environ,
                    "HOME": str(root / "home"),
                    "XDG_STATE_HOME": str(state_home),
                    "TAIJI_UV_LOCK_MODE": "unlocked",
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unset/strict", result.stderr)
            self.assertFalse(state_home.exists())
            self.assertFalse((delivery / "生成的安装包").exists())

    def test_production_setup_uses_one_locked_sync_and_validates_webui_subset(self):
        setup = SETUP.read_text(encoding="utf-8")

        self.assertIn("verify-python-lock-contract.py", setup)
        self.assertIn("--verify-installed", setup)
        self.assertNotIn("uv pip install", setup)
        production = setup[setup.index("sync_agent_dependencies() {") :]
        self.assertIn('UV_EXECUTABLE="${TAIJI_UV_EXECUTABLE:-uv}"', setup)
        self.assertIn('"$UV_EXECUTABLE" sync "${sync_args[@]}" --locked', production)

    def test_formal_builder_forces_copy_link_mode_for_the_production_uv_sync(self):
        builder = BUILDER.read_text(encoding="utf-8")
        run_setup_local = builder[
            builder.index("run_setup_local() {") : builder.index(
                "\n}\n\nbuild_runtime_and_deb()", builder.index("run_setup_local() {")
            )
        ]

        self.assertIn("UV_LINK_MODE=copy \\\n", run_setup_local)
        self.assertLess(
            run_setup_local.index("UV_LINK_MODE=copy"),
            run_setup_local.index("/bin/bash -p ./scripts/setup-local.sh"),
        )
        self.assertNotIn('UV_LINK_MODE="${', run_setup_local)

    def test_webui_requirements_are_exact_agent_direct_lock_subset(self):
        result = subprocess.run(
            [
                sys.executable,
                str(LOCK_HELPER),
                "--pyproject",
                str(ROOT / "hermes-local-lab/sources/hermes-agent/pyproject.toml"),
                "--lock",
                str(ROOT / "hermes-local-lab/sources/hermes-agent/uv.lock"),
                "--requirements",
                str(ROOT / "hermes-local-lab/sources/hermes-webui/requirements.txt"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["requirements"],
            {"cryptography": "46.0.7", "pypdf": "6.14.2", "pyyaml": "6.0.3"},
        )

    def test_lock_helper_rejects_ranges_and_non_direct_lock_entries(self):
        with tempfile.TemporaryDirectory(prefix="taiji-lock-contract-") as tmp:
            root = Path(tmp)
            pyproject = root / "pyproject.toml"
            lock = root / "uv.lock"
            requirements = root / "requirements.txt"
            pyproject.write_text(
                '[project]\nname="demo"\nversion="1.0.0"\ndependencies=["pyyaml==6.0.3"]\n',
                encoding="utf-8",
            )
            lock.write_text(
                'version=1\n[[package]]\nname="pyyaml"\nversion="6.0.3"\n'
                '[[package]]\nname="cryptography"\nversion="46.0.7"\n',
                encoding="utf-8",
            )
            requirements.write_text("pyyaml>=6.0\n", encoding="utf-8")
            ranged = subprocess.run(
                [sys.executable, str(LOCK_HELPER), "--pyproject", str(pyproject), "--lock", str(lock), "--requirements", str(requirements)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(ranged.returncode, 0)
            self.assertIn("exact", ranged.stderr.lower())

            requirements.write_text("cryptography==46.0.7\n", encoding="utf-8")
            indirect = subprocess.run(
                [sys.executable, str(LOCK_HELPER), "--pyproject", str(pyproject), "--lock", str(lock), "--requirements", str(requirements)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(indirect.returncode, 0)
            self.assertIn("direct", indirect.stderr.lower())

    def test_lock_helper_source_is_python38_compatible(self):
        source = LOCK_HELPER.read_text(encoding="utf-8")
        ast.parse(source, filename=str(LOCK_HELPER), feature_version=(3, 8))
        self.assertNotIn("import tomllib", source)

    def test_lock_helper_rejects_an_installed_version_different_from_lock(self):
        with tempfile.TemporaryDirectory(prefix="taiji-installed-lock-contract-") as tmp:
            root = Path(tmp)
            pyproject = root / "pyproject.toml"
            lock = root / "uv.lock"
            requirements = root / "requirements.txt"
            fake_python = root / "python"
            pyproject.write_text(
                '[project]\nname="demo"\nversion="1.0.0"\ndependencies=["pyyaml==6.0.3"]\n',
                encoding="utf-8",
            )
            lock.write_text(
                'version=1\n[[package]]\nname="pyyaml"\nversion="6.0.3"\n',
                encoding="utf-8",
            )
            requirements.write_text("pyyaml==6.0.3\n", encoding="utf-8")
            fake_python.write_text(
                "#!/bin/sh\nprintf '%s\\n' '{\"pyyaml\": \"6.0.2\"}'\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            result = subprocess.run(
                [
                    sys.executable,
                    str(LOCK_HELPER),
                    "--pyproject",
                    str(pyproject),
                    "--lock",
                    str(lock),
                    "--requirements",
                    str(requirements),
                    "--verify-installed",
                    "--python",
                    str(fake_python),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("differs from the lock subset", result.stderr)

    def test_lock_helper_rejects_an_installed_dependency_that_cannot_import(self):
        with tempfile.TemporaryDirectory(prefix="taiji-installed-import-contract-") as tmp:
            root = Path(tmp)
            site = root / "site"
            site.mkdir()
            (site / "yaml.py").write_text(
                'raise RuntimeError("fixture import failure")\n', encoding="utf-8"
            )
            metadata = site / "pyyaml-6.0.3.dist-info"
            metadata.mkdir()
            (metadata / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: PyYAML\nVersion: 6.0.3\n",
                encoding="utf-8",
            )
            pyproject = root / "pyproject.toml"
            lock = root / "uv.lock"
            requirements = root / "requirements.txt"
            pyproject.write_text(
                '[project]\nname="demo"\nversion="1.0.0"\ndependencies=["pyyaml==6.0.3"]\n',
                encoding="utf-8",
            )
            lock.write_text(
                'version=1\n[[package]]\nname="pyyaml"\nversion="6.0.3"\n',
                encoding="utf-8",
            )
            requirements.write_text("pyyaml==6.0.3\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(LOCK_HELPER),
                    "--pyproject",
                    str(pyproject),
                    "--lock",
                    str(lock),
                    "--requirements",
                    str(requirements),
                    "--verify-installed",
                    "--python",
                    sys.executable,
                ],
                text=True,
                capture_output=True,
                check=False,
                env={**os.environ, "PYTHONPATH": str(site)},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fixture import failure", result.stderr)

    def test_deb_builder_and_release_chain_require_full_toolchain_identity(self):
        deb_builder = DEB_BUILDER.read_text(encoding="utf-8")
        preflight = PREFLIGHT.read_text(encoding="utf-8")
        validator = VALIDATOR.read_text(encoding="utf-8")
        assembler = ASSEMBLER.read_text(encoding="utf-8")

        self.assertIn('TAIJI_PYTHON_DEPENDENCY_LOCK_STATUS', deb_builder)
        self.assertIn('"strict-locked"', deb_builder)
        self.assertIn('lock_path="$SOURCE_AGENT_DIR/$PYTHON_LOCK_BASENAME"', deb_builder)
        self.assertIn('sha256sum "$lock_path"', deb_builder)
        for source in (deb_builder, preflight, validator, assembler):
            self.assertIn(UV_EXECUTABLE_SHA256, source)
            self.assertIn(NODE_EXECUTABLE_SHA256, source)
            self.assertIn(PYTHON_ARCHIVE_SHA256, source)
            self.assertIn(PYTHON_EXECUTABLE_SHA256, source)
            self.assertIn(ELECTRON_EXECUTABLE_SHA256, source)
        self.assertIn('[ "$NODE_EXECUTABLE_SHA256" = "$PINNED_NODE_EXECUTABLE_SHA256" ]', deb_builder)
        self.assertIn('sha256sum "$UV_ARCHIVE_PATH"', deb_builder)
        self.assertIn('sha256sum "$NODE_ARCHIVE_PATH"', deb_builder)
        for field in TOOLCHAIN_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', deb_builder)
                self.assertIn(f'"{field}"', preflight)
                self.assertIn(f'"{field}"', validator)
                self.assertIn(f'"{field}"', assembler)

    def test_deb_builder_rechecks_staged_python_node_and_electron_bytes(self):
        source = DEB_BUILDER.read_text(encoding="utf-8")
        start = source.index("validate_staged_toolchain_executables() {")
        end = source.index("\n}\n", start) + len("\n}")
        function_source = source[start:end]

        with tempfile.TemporaryDirectory(prefix="taiji-staged-toolchain-") as tmp:
            root = Path(tmp)
            agent_runtime = root / "agent"
            install_root = root / "install"
            desktop_runtime = install_root / "apps/desktop"
            source_electron = root / "source-electron"
            python_bin = agent_runtime / "venv/bin/python"
            node_bin = install_root / "runtime/node/bin/node"
            electron_bin = desktop_runtime / "node_modules/electron/dist/electron"
            for path, payload in (
                (python_bin, b"python-binary"),
                (node_bin, b"node-binary"),
                (source_electron, b"electron-binary"),
                (electron_bin, b"electron-binary"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                path.chmod(0o755)

            def run() -> subprocess.CompletedProcess[str]:
                python_sha = hashlib.sha256(python_bin.read_bytes()).hexdigest()
                node_sha = hashlib.sha256(b"node-binary").hexdigest()
                script = f"""
set -euo pipefail
fail() {{ printf '%s\\n' "$*" >&2; exit 1; }}
AGENT_RUNTIME={str(agent_runtime)!r}
INSTALL_ROOT={str(install_root)!r}
DESKTOP_RUNTIME={str(desktop_runtime)!r}
ELECTRON_BIN={str(source_electron)!r}
PYTHON_EXECUTABLE_SHA256={python_sha!r}
PINNED_PYTHON_EXECUTABLE_SHA256={python_sha!r}
NODE_EXECUTABLE_SHA256={node_sha!r}
PINNED_NODE_EXECUTABLE_SHA256={node_sha!r}
PINNED_ELECTRON_EXECUTABLE_SHA256={hashlib.sha256(b"electron-binary").hexdigest()!r}
{function_source}
validate_staged_toolchain_executables
"""
                return subprocess.run(
                    ["bash", "-c", script],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            accepted = run()
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            node_bin.write_bytes(b"tampered-node")
            rejected = run()
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("Node", rejected.stderr)

    def test_deb_builder_rechecks_the_complete_locked_python_environment(self):
        source = DEB_BUILDER.read_text(encoding="utf-8")
        start = source.index("validate_locked_python_environment() {")
        end = source.index("\n}\n", start) + len("\n}")
        function_source = source[start:end]

        with tempfile.TemporaryDirectory(prefix="taiji-deb-python-contract-") as tmp:
            root = Path(tmp)
            agent = root / "agent"
            web = root / "web"
            agent.mkdir()
            web.mkdir()
            lock = agent / "uv.lock"
            lock.write_text('version=1\n', encoding="utf-8")
            (agent / "pyproject.toml").write_text(
                '[project]\nname="demo"\nversion="1.0.0"\ndependencies=[]\n',
                encoding="utf-8",
            )
            (web / "requirements.txt").write_text("pyyaml==6.0.3\n", encoding="utf-8")
            python_bin = agent / "venv/bin/python"
            python_bin.parent.mkdir(parents=True)
            python_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python_bin.chmod(0o755)
            helper = root / "lock-helper"
            helper.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
            helper.chmod(0o755)
            uv = root / "uv"
            uv_log = root / "uv.log"
            uv.write_text(
                f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {str(uv_log)!r}\n",
                encoding="utf-8",
            )
            uv.chmod(0o755)
            def run() -> subprocess.CompletedProcess[str]:
                script = f"""
set -euo pipefail
fail() {{ printf '%s\\n' "$*" >&2; exit 1; }}
SOURCE_AGENT_DIR={str(agent)!r}
SOURCE_WEB_DIR={str(web)!r}
LOCK_CONTRACT_HELPER={str(helper)!r}
PINNED_LOCK_CONTRACT_HELPER_SHA256={hashlib.sha256(helper.read_bytes()).hexdigest()!r}
UV_EXECUTABLE={str(uv)!r}
PYTHON_LOCK_SHA256={hashlib.sha256(lock.read_bytes()).hexdigest()!r}
{function_source}
validate_locked_python_environment
"""
                return subprocess.run(
                    ["bash", "-c", script],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            rejected = run()
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("lock contract", rejected.stderr.lower())
            self.assertFalse(uv_log.exists(), "uv check must not run after helper rejection")

            helper.write_text("raise SystemExit(0)\n", encoding="utf-8")
            accepted = run()
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(
                uv_log.read_text(encoding="utf-8").strip(),
                "sync --extra all --locked --check",
            )

    def test_deb_builder_rejects_a_same_version_node_with_a_forged_archive_marker(self):
        source = DEB_BUILDER.read_text(encoding="utf-8")
        start = source.index("validate_strict_toolchain_contract() {")
        end = source.index("\n}\n", start) + len("\n}")
        function_source = source[start:end]

        with tempfile.TemporaryDirectory(prefix="taiji-node-identity-") as tmp:
            root = Path(tmp)
            agent = root / "agent"
            python_bin = agent / "venv/bin/python"
            python_bin.parent.mkdir(parents=True)
            python_bin.write_text(
                "#!/bin/sh\nprintf '%s\\n' 3.11.15\n", encoding="utf-8"
            )
            python_bin.chmod(0o755)
            python_archive = root / "python.tar.gz"
            python_archive.write_bytes(b"verified-python-archive")
            pinned_python_sha = hashlib.sha256(python_bin.read_bytes()).hexdigest()
            lock = agent / "uv.lock"
            lock.write_text("version = 1\n", encoding="utf-8")
            uv = root / "uv"
            uv.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'uv 0.12.2 (x86_64-unknown-linux-gnu)'\n",
                encoding="utf-8",
            )
            uv.chmod(0o755)
            uv_archive = root / "uv.tar.gz"
            uv_archive.write_bytes(b"verified-uv-archive")
            node_root = root / "node"
            node_bin = node_root / "bin/node"
            node_bin.parent.mkdir(parents=True)
            node_bin.write_text(
                "#!/bin/sh\nprintf '%s\\n' v22.23.1\n", encoding="utf-8"
            )
            node_bin.chmod(0o755)
            node_archive = root / "node.tar.xz"
            node_archive.write_bytes(b"verified-node-archive")
            electron_archive = root / "electron.zip"
            electron_archive.write_bytes(b"verified-electron-archive")

            uv_archive_sha = hashlib.sha256(uv_archive.read_bytes()).hexdigest()
            uv_sha = hashlib.sha256(uv.read_bytes()).hexdigest()
            node_archive_sha = hashlib.sha256(node_archive.read_bytes()).hexdigest()
            node_sha = hashlib.sha256(node_bin.read_bytes()).hexdigest()
            electron_sha = hashlib.sha256(electron_archive.read_bytes()).hexdigest()
            (node_root / ".taiji-node-archive-sha256").write_text(
                node_archive_sha + "\n", encoding="ascii"
            )

            def run() -> subprocess.CompletedProcess[str]:
                script = f"""
set -euo pipefail
fail() {{ printf '%s\\n' "$*" >&2; exit 1; }}
stat() {{ printf '%s\\n' 1; }}
file() {{ printf '%s\\n' 'ELF 64-bit LSB executable, x86-64'; }}
readlink() {{ [ "$1" = -f ] && printf '%s\\n' "$2"; }}
SOURCE_AGENT_DIR={str(agent)!r}
PYTHON_DEPENDENCY_LOCK_STATUS=strict-locked
PYTHON_LOCK_BASENAME=uv.lock
PYTHON_LOCK_SHA256={hashlib.sha256(lock.read_bytes()).hexdigest()!r}
UV_EXECUTABLE={str(uv)!r}
UV_ARCHIVE_PATH={str(uv_archive)!r}
UV_VERSION=0.12.2
UV_ARCHIVE_SHA256={uv_archive_sha!r}
UV_EXECUTABLE_SHA256={uv_sha!r}
PINNED_UV_VERSION=0.12.2
PINNED_UV_ARCHIVE_SHA256={uv_archive_sha!r}
PINNED_UV_EXECUTABLE_SHA256={uv_sha!r}
PACKAGED_NODE_ROOT={str(node_root)!r}
PACKAGED_NODE_EXECUTABLE={str(node_bin)!r}
PACKAGED_NODE_VERSION=22.23.1
PACKAGED_NODE_ARCHIVE_SHA256={node_archive_sha!r}
PINNED_NODE_EXECUTABLE_SHA256={node_sha!r}
NODE_ARCHIVE_PATH={str(node_archive)!r}
ELECTRON_ARCHIVE={str(electron_archive)!r}
ELECTRON_ARCHIVE_SHA256=''
TAIJI_ELECTRON_VERSION=39.8.10
TAIJI_ELECTRON_ARCHIVE_SHA256={electron_sha!r}
PYTHON_ARCHIVE_PATH={str(python_archive)!r}
PYTHON_ARCHIVE_SHA256={hashlib.sha256(python_archive.read_bytes()).hexdigest()!r}
PINNED_PYTHON_ARCHIVE_SHA256={hashlib.sha256(python_archive.read_bytes()).hexdigest()!r}
PINNED_PYTHON_VERSION=3.11.15
PINNED_PYTHON_EXECUTABLE_SHA256={pinned_python_sha!r}
EXPECTED_PYTHON_VERSION=3.11.15
EXPECTED_PYTHON_EXECUTABLE={str(python_bin)!r}
EXPECTED_PYTHON_EXECUTABLE_SHA256={pinned_python_sha!r}
{function_source}
validate_strict_toolchain_contract
"""
                return subprocess.run(
                    ["bash", "-c", script],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            accepted = run()
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            uv.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'uv 0.12.2 (x86_64-unknown-linux-gnu)'\n# forged replacement\n",
                encoding="utf-8",
            )
            uv.chmod(0o755)
            forged_uv = run()
            self.assertNotEqual(forged_uv.returncode, 0)
            self.assertIn("uv executable SHA256", forged_uv.stderr)
            uv.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'uv 0.12.2 (x86_64-unknown-linux-gnu)'\n",
                encoding="utf-8",
            )
            uv.chmod(0o755)
            node_bin.write_text(
                "#!/bin/sh\nprintf '%s\\n' v22.23.1\n# forged replacement\n",
                encoding="utf-8",
            )
            node_bin.chmod(0o755)
            rejected = run()
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("official archive identity", rejected.stderr)

            node_bin.write_text(
                "#!/bin/sh\nprintf '%s\\n' v22.23.1\n", encoding="utf-8"
            )
            node_bin.chmod(0o755)
            python_bin.write_text(
                "#!/bin/sh\nprintf '%s\\n' 3.11.15\n# forged replacement\n",
                encoding="utf-8",
            )
            python_bin.chmod(0o755)
            forged_python = run()
            self.assertNotEqual(forged_python.returncode, 0)
            self.assertIn("Python executable SHA256", forged_python.stderr)

    def test_local_input_preparation_runs_source_contract_before_archiving(self):
        prepare = PREPARE.read_text(encoding="utf-8")
        main = prepare[prepare.index("main() {") :]
        frozen_preflight = prepare.split("run_frozen_release_preflight() {", 1)[1].split(
            "withdraw_published_triplet() {", 1
        )[0]
        self.assertIn("TAIJI_RELEASE_REQUIRE_ARTIFACTS=0", frozen_preflight)
        self.assertIn("01_制包机_发布预检.sh", frozen_preflight)
        self.assertLess(
            main.index("run_frozen_release_preflight"),
            main.index("write_builder_input_package"),
        )

    def test_repo_source_gates_capture_and_recheck_f_with_fixed_system_git(self):
        builder = BUILDER.read_text(encoding="utf-8")
        preflight = PREFLIGHT.read_text(encoding="utf-8")
        builder_fallback = builder[
            builder.index("create_source_archive_from_git() {") :
            builder.index("resolve_source_archive() {")
        ]
        preflight_gate = preflight[
            preflight.index("check_git_clean_and_commit_match() {") :
            preflight.index("check_source_archive_matches_git_head() {")
        ]

        for source in (builder, preflight):
            self.assertIn("raw_system_git()", source)
            self.assertIn(
                "env -i PATH=/usr/bin:/bin LC_ALL=C LANG=C GIT_NO_REPLACE_OBJECTS=1",
                source,
            )
        self.assertIn(
            'FROZEN_SOURCE_COMMIT="$(raw_system_git -C "$repo_root" rev-parse --verify \'HEAD^{commit}\')"',
            builder_fallback,
        )
        self.assertNotIn(
            'FROZEN_SOURCE_COMMIT="$("$trusted_git"',
            builder_fallback,
        )
        self.assertGreaterEqual(
            builder_fallback.count(
                'raw_system_git -C "$repo_root" symbolic-ref --quiet --short HEAD'
            ),
            2,
        )
        self.assertGreaterEqual(
            builder_fallback.count(
                'raw_system_git -C "$repo_root" rev-parse --verify refs/heads/main'
            ),
            2,
        )
        self.assertIn(
            'observed="$(raw_system_git -C "$REPO_ROOT" rev-parse --verify \'HEAD^{commit}\')"',
            preflight_gate,
        )
        self.assertNotIn('observed="$("$TRUSTED_GIT"', preflight_gate)
        self.assertIn(
            'branch="$(raw_system_git -C "$REPO_ROOT" symbolic-ref --quiet --short HEAD)"',
            preflight_gate,
        )
        self.assertIn(
            'main_commit="$(raw_system_git -C "$REPO_ROOT" rev-parse --verify refs/heads/main)"',
            preflight_gate,
        )

    def test_source_only_preflight_rejects_downgraded_formal_builder(self):
        source_members = (
            "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
            "hermes-local-lab/scripts/setup-local.sh",
            "hermes-local-lab/sources/hermes-agent/pyproject.toml",
            "hermes-local-lab/sources/hermes-webui/requirements.txt",
            "hermes-local-lab/sources/hermes-agent/uv.lock",
            "packaging/linux/verify-python-lock-contract.py",
            "packaging/linux/source-archive-integrity.py",
            "packaging/linux/deb/build-deb.sh",
        )

        with tempfile.TemporaryDirectory(prefix="taiji-source-only-preflight-") as tmp:
            root = Path(tmp)
            delivery = root / "taijiagent 打包交付"
            delivery.mkdir()
            shutil.copy2(PREFLIGHT, delivery / PREFLIGHT.name)
            shutil.copy2(SOURCE_INTEGRITY_HELPER, delivery / SOURCE_INTEGRITY_HELPER.name)
            commit = "a" * 40
            archive = delivery / f"taiji-agentv1.0-kylin-build-src-{commit}.tar.gz"

            def write_archive(*, downgrade: str = "") -> None:
                staging = root / "source"
                if staging.exists():
                    shutil.rmtree(staging)
                for relative in source_members:
                    target = staging / "taiji-agentv1.0" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    payload = (ROOT / relative).read_bytes()
                    if downgrade == "default-auto" and relative.endswith("00_制包机_生成离线交付包.sh"):
                        payload = payload.replace(
                            b'uv_lock_mode="${TAIJI_UV_LOCK_MODE:-strict}"',
                            b'uv_lock_mode="${TAIJI_UV_LOCK_MODE:-auto}"',
                        )
                    if downgrade == "dead-call" and relative.endswith("00_制包机_生成离线交付包.sh"):
                        payload = payload.replace(
                            b"  validate_formal_uv_contract\n  initialize_build_logging",
                            b"  # validate_formal_uv_contract retained as dead text\n  initialize_build_logging",
                        )
                    if downgrade == "python-pin" and relative.endswith("00_制包机_生成离线交付包.sh"):
                        payload = payload.replace(
                            PYTHON_EXECUTABLE_SHA256.encode("ascii"),
                            ("f" * 64).encode("ascii"),
                        )
                    if downgrade == "electron-pin" and relative.endswith("packaging/linux/deb/build-deb.sh"):
                        payload = payload.replace(
                            ELECTRON_EXECUTABLE_SHA256.encode("ascii"),
                            ("e" * 64).encode("ascii"),
                        )
                    if downgrade in ("pyproject-drift", "malicious-helper") and relative.endswith("pyproject.toml"):
                        payload = payload.replace(b'"pyyaml==6.0.3"', b'"pyyaml==6.0.2"')
                    if downgrade == "malicious-helper" and relative.endswith("verify-python-lock-contract.py"):
                        payload = (
                            b"#!/usr/bin/env python3\n"
                            b"# verify_installed\n"
                            b"def validate_subset(*args):\n"
                            b"    return {}\n"
                        )
                    target.write_bytes(payload)
                with tarfile.open(archive, "w:gz") as handle:
                    handle.add(staging / "taiji-agentv1.0", arcname="taiji-agentv1.0")
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                inventory = delivery / f"{archive.name[:-len('.tar.gz')]}.inventory.json"
                inventory.unlink(missing_ok=True)
                created = subprocess.run(
                    [
                        sys.executable,
                        str(SOURCE_INTEGRITY_HELPER),
                        "create",
                        "--archive",
                        str(archive),
                        "--inventory",
                        str(inventory),
                        "--source-commit",
                        commit,
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(created.returncode, 0, created.stderr)
                inventory_digest = hashlib.sha256(inventory.read_bytes()).hexdigest()
                (delivery / "SHA256SUMS.txt").write_text(
                    f"{digest}  {archive.name}\n"
                    f"{inventory_digest}  {inventory.name}\n",
                    encoding="ascii",
                )

            env = {
                **os.environ,
                "TAIJI_RELEASE_SKIP_GIT_CHECK": "1",
                "TAIJI_RELEASE_REQUIRE_ARTIFACTS": "0",
                "TMPDIR": str(root),
            }
            write_archive()
            accepted = subprocess.run(
                ["bash", str(delivery / PREFLIGHT.name)],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

            for downgrade in (
                "default-auto",
                "dead-call",
                "python-pin",
                "electron-pin",
                "pyproject-drift",
                "malicious-helper",
            ):
                with self.subTest(downgrade=downgrade):
                    write_archive(downgrade=downgrade)
                    rejected = subprocess.run(
                        ["bash", str(delivery / PREFLIGHT.name)],
                        text=True,
                        capture_output=True,
                        check=False,
                        env=env,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(
                        "formal source toolchain",
                        (rejected.stdout + rejected.stderr).lower(),
                    )


if __name__ == "__main__":
    unittest.main()
