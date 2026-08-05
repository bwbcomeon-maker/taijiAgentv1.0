import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
PREFLIGHT_PATH = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SingleDebSalesContractTest(unittest.TestCase):
    def test_release_version_is_consistently_1_0_0(self):
        version = read(ROOT / "VERSION").strip()
        desktop_package = json.loads(
            read(ROOT / "apps/taiji-desktop/package.json")
        )
        desktop_lock = json.loads(
            read(ROOT / "apps/taiji-desktop/package-lock.json")
        )

        self.assertEqual(version, "1.0.0")
        self.assertEqual(desktop_package["version"], version)
        self.assertEqual(desktop_lock["version"], version)
        self.assertEqual(desktop_lock["packages"][""]["version"], version)

    def test_builder_uses_only_source_controlled_policy(self):
        builder = read(BUILDER_PATH)

        self.assertIn('POLICY_FILE="$SRC_DIR/packaging/linux/compatibility-policy.json"', builder)
        self.assertIn('POLICY_HELPER="$SRC_DIR/packaging/linux/compatibility_policy.py"', builder)
        self.assertIn('validate --policy "$POLICY_FILE" --print-id', builder)
        self.assertIn('validate --policy "$POLICY_FILE" --print-sha256', builder)
        self.assertIn('validate --policy "$POLICY_FILE" --print-maintainer', builder)
        self.assertIn("load_source_controlled_policy", builder)
        self.assertIn('source_name="$(basename "$SRC_ARCHIVE")"', builder)
        # The canonical source archive variable is SRC_ARCHIVE.  The
        # operator-facing TAIJI_SOURCE_ARCHIVE environment variable is still
        # a supported override, so only reject a stale standalone variable or
        # an undefined expansion in the report path.
        self.assertNotRegex(builder, re.compile(r"(?m)^SOURCE_ARCHIVE="))
        self.assertNotIn('"$SOURCE_ARCHIVE"', builder)

        for forbidden in (
            "TAIJI_TARGET_BASELINE_FILE",
            "TARGET_BASELINE_FILE",
            "target_baseline",
            "target-baseline",
            "runtime-depends",
            "approved-maintainer",
            "TAIJI_PACKAGE_MAINTAINER",
        ):
            self.assertNotIn(forbidden, builder)

    def test_builder_has_no_baseline_or_maintainer_input(self):
        builder = read(BUILDER_PATH)

        # Maintainer is read from the canonical policy and is not an input
        # supplied by a target machine, environment variable, or operator file.
        self.assertIn("POLICY_MAINTAINER", builder)
        self.assertNotRegex(builder, re.compile(r"TAIJI_PACKAGE_MAINTAINER|PACKAGE_MAINTAINER"))
        self.assertNotRegex(builder, re.compile(r"TARGET_BASELINE|target_baseline|target-baseline"))
        self.assertNotRegex(builder, re.compile(r"--max-age-days|profile_id"))

    def test_marker_report_and_manifest_bind_policy_and_abi_audit(self):
        builder = read(BUILDER_PATH)
        preflight = read(PREFLIGHT_PATH)
        build = read(ROOT / "packaging/linux/deb/build-deb.sh")

        marker_keys = (
            "version", "source_archive", "source_sha256", "source_commit",
            "deb", "deb_sha256", "checksum", "built_at_utc", "manifest",
            "compatibility_policy_id", "compatibility_policy_sha256",
            "elf_abi_audit_sha256", "maintainer",
        )
        for key in marker_keys:
            self.assertIn(f'printf \'{key}=', builder)
        self.assertIn("compatibility policy SHA256", builder)
        self.assertIn("ELF ABI audit SHA256", builder)
        self.assertIn('"schema": "taiji-package-manifest/v3"', build)
        self.assertIn('"compatibility_policy_id": "$POLICY_ID"', build)
        self.assertIn('"compatibility_policy_sha256": "$POLICY_SHA256"', build)
        self.assertIn('"elf_abi_audit_sha256"', build)

        self.assertIn("required = {", preflight)
        self.assertIn('"compatibility_policy_id"', preflight)
        self.assertIn('"compatibility_policy_sha256"', preflight)
        self.assertIn('"elf_abi_audit_sha256"', preflight)
        self.assertIn("verify_marker_and_manifest", preflight)
        self.assertIn("verify_deb_payload", preflight)
        self.assertIn('cmp -s "$POLICY_FILE" "$embedded_policy"', preflight)

    def test_preflight_rejects_policy_or_audit_hash_drift(self):
        preflight = read(PREFLIGHT_PATH)

        for drift_guard in (
            "marker policy binding mismatch",
            "manifest binding mismatch",
            "DEB embedded policy 与源码 policy 不一致",
            "DEB embedded ABI audit 与 marker 不一致",
            "compatibility_policy_sha256",
            "elf_abi_audit_sha256",
        ):
            self.assertIn(drift_guard, preflight)
        self.assertIn("sha256sum \"$abi\"", preflight)
        self.assertIn("cmp -s \"$POLICY_FILE\" \"$embedded_policy\"", preflight)
        self.assertIn("verify_package_output_allowlist", preflight)

    def test_output_allowlist_resolves_deb_name_after_argument_binding(self):
        preflight = read(PREFLIGHT_PATH)

        self.assertIn('local deb="$1" name', preflight)
        self.assertIn('name="$(basename -- "$deb")"', preflight)
        self.assertNotIn('local deb="$1" name="$(basename "$deb")"', preflight)

    def test_customer_contract_has_no_second_deb_or_offline_repo(self):
        builder = read(BUILDER_PATH)
        preflight = read(PREFLIGHT_PATH)

        self.assertIn("只交付一个逐字节固定的 amd64 DEB", builder)
        self.assertIn("必须且只能有一个 amd64 DEB", preflight)
        self.assertIn("verify_package_output_allowlist", preflight)
        self.assertIn("expected = {name, name + \".sha256\", \".build-success\", \"taiji-package-manifest.json\", \"构建报告.txt\"}", preflight)
        for source in (builder, preflight):
            for forbidden in (
                "dpkg-scanpackages",
                "Packages.gz",
                "apt-get download",
                "OFFLINE_REPO",
                "build_offline_dependency_repo",
                "target_baseline",
                "target-baseline",
            ):
                self.assertNotIn(forbidden, source)

    def test_builder_does_not_download_runtime_dependencies_after_candidate_is_fixed(self):
        builder = read(BUILDER_PATH)
        main = builder[builder.index("main() {"):]

        self.assertIn("CANDIDATE_DEB_FIXED=0", builder)
        self.assertIn("CANDIDATE_DEB_FIXED=1", builder)
        self.assertIn("require_candidate_deb_fixed", builder)
        self.assertLess(main.index("collect_artifacts"), main.index("write_build_report"))
        self.assertLess(main.index("collect_artifacts"), main.index("stage_target_acceptance_tools"))
        post_candidate = main[main.index("write_build_report"):]
        for network_or_runtime_download in ("curl_download", "npm ci", "apt-get -y --download-only", "apt-get download"):
            self.assertNotIn(network_or_runtime_download, post_candidate)
        self.assertIn("候选 DEB 固定后不再下载运行时依赖", builder)


if __name__ == "__main__":
    unittest.main()
