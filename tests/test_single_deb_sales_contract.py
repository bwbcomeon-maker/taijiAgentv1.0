"""Static sales contract for the unified single-DEB publisher."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "packaging/linux/deb/publish-single-deb.sh"
ORCHESTRATOR = ROOT / "scripts/taiji-linux-golden-orchestrator.py"
SALE_READINESS = ROOT / "docs/taiji-sale-readiness.md"
DELIVERY_RUNBOOK = ROOT / "docs/runbooks/taiji-kylin-uos-offline-delivery.md"
DELIVERY_GUIDE = ROOT / "taijiagent 打包交付/操作说明.md"
RECEIPT_BASENAMES = {
    "release-evidence.json",
    "release-evidence.json.sig",
    "certification-set.json",
    "certification-set.json.sig",
    "compatibility-policy.json",
    "deb.sha256",
    "github-ci-evidence.json",
    "github-ci-run-response.json",
    "github-ci-jobs-response.json",
}


def section(document: str, start: str, end: str) -> str:
    return document.split(start, 1)[1].split(end, 1)[0]


class SingleDebSalesContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.publisher = PUBLISHER.read_text(encoding="utf-8")

    def test_cli_requires_only_unified_inputs(self):
        for token in (
            "--delivery-dir",
            "--candidate-deb",
            "--policy",
            "--certification-set",
            "--certification-signature",
            "--release-evidence",
            "--release-signature",
            "--output-dir",
            "--receipt-root",
        ):
            self.assertIn(token, self.publisher)
        for forbidden in (
            "target_baseline.py",
            "runtime-depends.txt",
            "approved-maintainer.json",
            "TAIJI_PACKAGE_MAINTAINER",
        ):
            self.assertNotIn(forbidden, self.publisher)

    def test_customer_and_receipt_allowlists_are_explicit(self):
        self.assertIn("exactly-one-deb", self.publisher)
        self.assertIn("renameat2", self.publisher)
        self.assertIn("renameatx_np", self.publisher)
        self.assertIn("receipt identity is already reserved", self.publisher)
        for name in (
            "release-evidence.json",
            "release-evidence.json.sig",
            "certification-set.json",
            "certification-set.json.sig",
            "compatibility-policy.json",
            "deb.sha256",
            "github-ci-evidence.json",
            "github-ci-run-response.json",
            "github-ci-jobs-response.json",
        ):
            self.assertIn(name, self.publisher)
        self.assertIn("RECEIPT_NAMES", self.publisher)

    def test_publisher_has_input_snapshot_and_identity_bound_rollback(self):
        for token in (
            "snapshot(",
            "verify_identity(",
            "publisher input changed during formal gate",
            "rollback_output",
            "rollback_receipt",
            "output_published",
            "receipt_published",
        ):
            self.assertIn(token, self.publisher)

    def test_customer_payload_is_not_mutated_by_evidence(self):
        self.assertIn("shutil.copyfile(snapshots[\"candidate.deb\"][\"path\"], output_staging / customer_name)", self.publisher)
        self.assertNotIn("dpkg-deb -x", self.publisher)
        self.assertNotIn("install -m 0600", self.publisher)


class SingleDebDocumentationContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sale_readiness = SALE_READINESS.read_text(encoding="utf-8")
        self.runbook = DELIVERY_RUNBOOK.read_text(encoding="utf-8")
        self.guide = DELIVERY_GUIDE.read_text(encoding="utf-8")
        self.orchestrator = ORCHESTRATOR.read_text(encoding="utf-8")
        self.runbook_offline = section(
            self.runbook,
            "### 5.3 在受控发布机执行断网生命周期演练",
            "### 5.4 在真实目标机安装并验收",
        )
        self.runbook_target = section(
            self.runbook,
            "## 10. 真实 Kylin/UOS App 最终验收",
            "## 11. 一次性诊断包流程",
        )
        self.runbook_release = section(
            self.runbook,
            "### 5.5 签名与最终放行",
            "## 6. 完整离线交付契约",
        )
        self.guide_offline = section(
            self.guide,
            "### 在受控发布机生成离线演练证据",
            "## 第二步：内部验收目录拷贝到完全离线目标机",
        )
        self.guide_target = section(
            self.guide,
            "## 第三步 B：干净目标机单 DEB 双击安装",
            "## 第四步：用真实 Electron 桌面 App 验收并导出证据",
        )
        self.guide_acceptance = section(
            self.guide,
            "## 第三步 B：干净目标机单 DEB 双击安装",
            "## 最终销售发布：只生成一个客户 DEB",
        )
        self.guide_publication = section(
            self.guide,
            "## 最终销售发布：只生成一个客户 DEB",
            "## 第五步：人工双击启动复核",
        )

    def test_sale_readiness_release_chain_names_fixed_certification_set(self):
        release_chain = section(self.sale_readiness, "## 放行链", "## 销售口径")

        self.assertIn("certification-set.json", release_chain)

    def test_formal_target_docs_reuse_certification_envelope_nonce(self):
        for document in (self.runbook_target, self.guide_target):
            with self.subTest(document=document[:80]):
                self.assertNotRegex(document, r"openssl\s+rand")
                self.assertNotRegex(document, r'TAIJI_[A-Z_]*CHALLENGE="\$\(')
                self.assertNotIn(
                    "后续 certification set 和 publication 各自生成不同的 challenge",
                    document,
                )
                self.assertNotIn("由发布负责人生成当轮 challenge", document)
                self.assertNotIn("目标验收 challenge 必须由发布负责人当轮生成", document)
                self.assertIn("certification envelope", document)
                self.assertIn("同一 nonce", document)
                self.assertIn("黄金编排器", document)
                self.assertIn("publication envelope", document)
                self.assertIn("nonce 不同", document)

    def test_formal_offline_docs_reuse_certification_envelope_nonce(self):
        for document in (self.runbook_offline, self.guide_offline):
            with self.subTest(document=document[:80]):
                self.assertNotRegex(document, r"openssl\s+rand")
                self.assertNotRegex(document, r'TAIJI_[A-Z_]*CHALLENGE="\$\(')
                self.assertNotIn(
                    "目标验收、certification set 签名和 publication 签名必须分别使用各自用途的 challenge",
                    document,
                )
                self.assertIn("certification envelope", document)
                self.assertIn("同一 nonce", document)
                self.assertIn("黄金编排器", document)

    def test_formal_target_docs_use_data_only_delivery_matrix(self):
        for document in (self.runbook_target, self.guide_target):
            with self.subTest(document=document[:80]):
                self.assertIn("验收工具/certification-matrix.json", document)
                self.assertNotRegex(
                    document,
                    r"--matrix\s+['\"]?\$PWD/packaging/linux/certification-matrix\.json",
                )

    def test_formal_delivery_docs_name_fixed_nine_file_receipt(self):
        for document in (self.runbook_target, self.guide_publication):
            with self.subTest(document=document[:80]):
                self.assertIn("九文件 receipt", document)
                self.assertNotIn("六个白名单文件", document)
        for basename in RECEIPT_BASENAMES:
            with self.subTest(basename=basename):
                self.assertIn(f"`{basename}`", self.runbook_release)

    def test_formal_certification_and_publication_docs_require_approved_orchestrator_argv(self):
        for document in (self.runbook_release, self.guide_publication):
            with self.subTest(document=document[:80]):
                for stage in (
                    "challenge_preparation",
                    "certification_sign",
                    "publication_sign",
                    "release_check",
                    "publish",
                ):
                    self.assertIn(f"`{stage}`", document)
                self.assertIn("commands[].argv", document)
                self.assertIn("只用于解释参数", document)
                self.assertIn("不得作为正式流程旁路", document)

    def test_certification_envelope_precedes_offline_target_and_records_in_all_formal_docs(self):
        runbook_build = self.runbook.index(
            "### 5.2 在兼容 Linux amd64 制包机生成完整交付目录"
        )
        runbook_challenge = self.runbook.index("`challenge_preparation`")
        self.assertLess(runbook_build, runbook_challenge)
        for later in (
            "### 5.3 在受控发布机执行断网生命周期演练",
            "### 5.4 在真实目标机安装并验收",
            "### 5.5 签名与最终放行",
        ):
            self.assertLess(runbook_challenge, self.runbook.index(later))

        release_chain = section(self.sale_readiness, "## 放行链", "## 销售口径")
        sale_positions = (
            release_chain.index("最终 `01_制包机_发布预检.sh`"),
            release_chain.index("`challenge_preparation`"),
            release_chain.index("在断网的干净 Linux amd64 环境"),
            release_chain.index("在六个正向代表环境和六个负向边界"),
        )
        self.assertEqual(sale_positions, tuple(sorted(sale_positions)))

        guide_build = self.guide.index("制包成功后，你会看到")
        guide_challenge = self.guide.index("`challenge_preparation`")
        self.assertLess(guide_build, guide_challenge)
        for later in (
            "### 在受控发布机生成离线演练证据",
            "## 第三步 B：干净目标机单 DEB 双击安装",
            "## 最终销售发布：只生成一个客户 DEB",
        ):
            self.assertLess(guide_challenge, self.guide.index(later))

    def test_runbook_is_the_single_executable_golden_orchestrator_entry(self):
        heading = "### 5.2.1 黄金编排器唯一正式入口"
        self.assertIn(heading, self.runbook)
        canonical = section(
            self.runbook,
            heading,
            "### 5.3 在受控发布机执行断网生命周期演练",
        )
        self.assertIn("scripts/taiji-linux-golden-orchestrator.py", canonical)
        for command in ("init", "plan", "checkpoint", "retry"):
            self.assertRegex(canonical, rf'python3\s+"\$ORCHESTRATOR"\s+{command}\b')
        for phrase in (
            "commands[].argv",
            "保存日志和证据",
            "checkpoint pass/fail",
            "只产生命令",
            "不代替执行",
            "不代替审批",
            "不代替正式门禁",
        ):
            self.assertIn(phrase, canonical)
        self.assertIn("explicit_approval_required=true", canonical)
        self.assertIn("APPROVAL_ARGS=()", canonical)
        self.assertIn(
            '# APPROVAL_ARGS=(--approve-stage "$STAGE")',
            canonical,
        )
        self.assertNotRegex(
            canonical,
            r'(?m)^\s+--approve-stage\s+"\$STAGE"\s*$',
        )

        self.assertTrue(ORCHESTRATOR.is_file())
        self.assertIn('subparsers.add_parser("init"', self.orchestrator)
        self.assertIn('for name in ("plan", "dry-run")', self.orchestrator)
        self.assertIn('subparsers.add_parser("checkpoint"', self.orchestrator)
        self.assertIn('subparsers.add_parser("retry"', self.orchestrator)
        self.assertIn('entry["history"].append({"event": "retry"', self.orchestrator)

        canonical_link = (
            "taiji-kylin-uos-offline-delivery.md#521-"
            "黄金编排器唯一正式入口"
        )
        self.assertIn(canonical_link, self.sale_readiness)
        self.assertIn(canonical_link, self.guide)

    def test_formal_target_docs_use_only_installed_acceptance_trust_anchor(self):
        for document in (self.runbook_target, self.guide_acceptance):
            with self.subTest(document=document[:80]):
                self.assertIn("/usr/bin/taiji-agent-acceptance", document)
                self.assertNotRegex(
                    document,
                    r"(?m)^\s*(?:bash|sh)\s+(?:\./|\S+/)04_[^\n]*$",
                )


if __name__ == "__main__":
    unittest.main()
