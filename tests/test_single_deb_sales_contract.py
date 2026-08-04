import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SingleDebSalesContractTest(unittest.TestCase):
    def test_release_version_is_consistently_1_0_0(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        desktop_package = json.loads(
            (ROOT / "apps/taiji-desktop/package.json").read_text(encoding="utf-8")
        )
        desktop_lock = json.loads(
            (ROOT / "apps/taiji-desktop/package-lock.json").read_text(encoding="utf-8")
        )

        self.assertEqual(version, "1.0.0")
        self.assertEqual(desktop_package["version"], version)
        self.assertEqual(desktop_lock["version"], version)
        self.assertEqual(desktop_lock["packages"][""]["version"], version)

    def test_deb_build_is_bound_to_fresh_target_profile_and_real_maintainer(self):
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")

        for required in (
            "TAIJI_TARGET_BASELINE_FILE",
            "target_baseline.py",
            "runtime-depends.txt",
            "render-preinst.py",
            "--max-age-days",
            "TAIJI_PACKAGE_MAINTAINER",
            "approved-maintainer.json",
            "validate-approved-maintainer.py",
            '--expect "$PACKAGE_MAINTAINER"',
            "target-baseline.json",
        ):
            self.assertIn(required, build)
        self.assertNotIn(
            "Maintainer: Taiji Agent Team <support@example.invalid>", build
        )
        self.assertRegex(
            build,
            re.compile(r'install\s+-m\s+0644[^\n]+target-baseline\.json'),
        )
        self.assertIn('"targetBaselineProfile"', build)
        self.assertIn('"targetBaselineSha256"', build)

    def test_build_uses_one_canonical_runtime_dependency_contract(self):
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        dependencies = (
            ROOT / "packaging/linux/deb/runtime-depends.txt"
        ).read_text(encoding="utf-8").splitlines()

        self.assertIn("RUNTIME_DEPENDS_FILE", build)
        self.assertNotRegex(build, re.compile(r'^DEB_DEPENDS="libc6,', re.MULTILINE))
        self.assertEqual(dependencies, sorted(set(dependencies)))
        self.assertGreaterEqual(len(dependencies), 30)

    def test_release_chain_requires_and_records_the_same_target_profile(self):
        builder = (
            ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
        ).read_text(encoding="utf-8")
        preflight = (
            ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
        ).read_text(encoding="utf-8")
        version_info = (
            ROOT / "taijiagent 打包交付/版本信息.txt"
        ).read_text(encoding="utf-8")

        for source in (builder, preflight):
            self.assertIn("target-baseline.json", source)
            self.assertIn("target_baseline.py", source)
            self.assertIn("profile_id", source)
            self.assertIn("target_baseline_sha256", source)
            self.assertIn("--max-age-days", source)
        self.assertIn(
            'TARGET_BASELINE_FILE="$SCRIPT_DIR/目标基线/target-baseline.json"',
            builder,
        )
        self.assertIn('PACKAGE_MAINTAINER="${TAIJI_PACKAGE_MAINTAINER:-}"', builder)
        self.assertIn('TAIJI_TARGET_BASELINE_FILE="$TARGET_BASELINE_SNAPSHOT"', builder)
        self.assertIn('TAIJI_PACKAGE_MAINTAINER="$PACKAGE_MAINTAINER"', builder)
        self.assertIn("target_baseline_profile_id=", builder)
        self.assertIn('"target_baseline_profile_id"', builder)
        self.assertIn(
            'TARGET_BASELINE_FILE="$SCRIPT_DIR/目标基线/target-baseline.json"',
            preflight,
        )
        self.assertIn("verify_target_baseline_binding", preflight)
        self.assertIn(
            "opt/taiji-agent/resources/target-baseline.json", preflight
        )
        self.assertIn("cmp -s", preflight)
        self.assertIn("targetBaselineProfile", preflight)
        self.assertIn("targetBaselineSha256", preflight)
        self.assertNotIn("本轮目标系统：Kylin V10 SP1", version_info)
        self.assertIn("目标基线", version_info)
        self.assertIn("不得作为当前真机证据", version_info)

    def test_customer_publisher_outputs_only_one_bit_identical_deb(self):
        publisher = (
            ROOT / "packaging/linux/deb/publish-single-deb.sh"
        ).read_text(encoding="utf-8")

        for required in (
            "target_baseline.py",
            "--max-age-days",
            "dpkg-deb",
            "target-baseline.json",
            "sha256sum",
            "find",
            "exactly one",
        ):
            self.assertIn(required, publisher)
        self.assertNotIn("apt-get download", publisher)
        self.assertNotIn("离线依赖", publisher)

    def test_runbook_keeps_single_deb_claim_at_exact_baseline_evidence_level(self):
        runbook = (
            ROOT / "docs/runbooks/taiji-kylin-uos-offline-delivery.md"
        ).read_text(encoding="utf-8")

        for required in (
            "单一 DEB",
            "target-baseline.json",
            "每个精确基线",
            "只交付一个 `.deb`",
            "不捆绑 glibc",
            "目标机已验证",
        ):
            self.assertIn(required, runbook)
        self.assertNotIn("一个 DEB 通吃所有国产 Linux", runbook)

    def test_sales_readiness_separates_internal_archive_from_customer_single_deb(self):
        readiness = (ROOT / "docs/taiji-sale-readiness.md").read_text(
            encoding="utf-8"
        )

        for required in (
            "内部完整交付目录",
            "客户安装目录",
            "必须且只能包含一个",
            "observe-single-deb-install.py",
            "人工见证不能被表述为机器自动识别",
            "Windows 安装包必须在第一阶段",
            "publish-single-deb.sh",
        ):
            self.assertIn(required, readiness)
        for stale_claim in (
            "交付完整离线 DEB 目录，而不是只拷贝单个 .deb",
            "0.1.0-preview",
            "TAIJI_TARGET_INSTALL_METHOD",
            "TAIJI_TARGET_DPKG_STATUS_BEFORE",
            "TAIJI_TARGET_FIRST_LAUNCH",
        ):
            self.assertNotIn(stale_claim, readiness)

    def test_release_metadata_distinguishes_internal_rehearsal_from_customer_install(self):
        builder = (
            ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
        ).read_text(encoding="utf-8")

        for required in (
            "Internal rehearsal archive",
            "Customer installation: exactly one bit-identical DEB",
            "内部演练边界",
            "不作为客户安装目录",
            "客户销售边界",
            "只交付一个与目标基线绑定、逐字节一致的 DEB",
        ):
            self.assertIn(required, builder)
        for contradictory_claim in (
            "Complete delivery directory with generated DEB and local offline apt repository",
            "Offline installations missing 离线依赖/Packages or Packages.gz",
            "必须同时包含离线依赖/Packages 与 Packages.gz",
            "目标机离线仓库：离线依赖/Packages 与 Packages.gz",
        ):
            self.assertNotIn(contradictory_claim, builder)


if __name__ == "__main__":
    unittest.main()
