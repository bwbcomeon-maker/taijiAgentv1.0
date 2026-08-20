from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "docs/handoffs/2026-08-20-kylin-amd64-candidate-pipeline.md"
PLAN = ROOT / "docs/superpowers/plans/2026-08-19-kylin-amd64-candidate-pipeline.md"


class KylinCandidateHandoffTests(unittest.TestCase):
    def test_handoff_binds_implementation_and_evidence_boundary(self):
        self.assertTrue(HANDOFF.is_file())
        text = HANDOFF.read_text(encoding="utf-8")
        for required in (
            "a5a36849bca009d1cfb07ac2309532a502c6bd70",
            "5364233e1297e5f2837382823d4e35a0d114aba7",
            "已实现，本地模拟通过",
            "真实麒麟连接未验证",
            "候选 DEB 未构建",
            "online doctor 未执行",
            "99/00/01 未执行",
        ):
            self.assertIn(required, text)

    def test_handoff_has_exact_resume_order_and_stop_boundary(self):
        self.assertTrue(HANDOFF.is_file())
        text = HANDOFF.read_text(encoding="utf-8")
        headings = (
            "## 目标", "## 冻结实现身份", "## 已完成", "## 未完成",
            "## 恢复前置条件", "## 精确恢复顺序", "## 跨平台接力",
            "## 证据边界", "## 状态卡",
        )
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        commands = (
            "git status --short --branch",
            "git rev-parse HEAD",
            "./taiji-package doctor",
            "./taiji-package plan",
            "./taiji-package doctor --online",
            "SSH 与传输",
            "依赖与网络",
            "候选构建",
            "./taiji-package build",
        )
        command_positions = [text.index(command) for command in commands]
        self.assertEqual(command_positions, sorted(command_positions))
        self.assertIn("主机不可达时必须在调用 99 前停止", text)

    def test_handoff_records_revised_single_repository_decision(self):
        self.assertTrue(HANDOFF.is_file())
        text = HANDOFF.read_text(encoding="utf-8")
        self.assertIn("codex/cross-platform-package-controller", text)
        self.assertIn("/Users/bwb/Documents/工作/taiji-agentv1.0", text)
        self.assertIn("旧 Windows 仓只作为有来源约束的迁移材料", text)
        self.assertNotIn("Windows 工作不得从 Linux 功能分支继续开发", text)

    def test_original_plan_no_longer_claims_it_is_unwritten(self):
        text = PLAN.read_text(encoding="utf-8")
        self.assertNotIn("当前处于 Plan Mode，尚未实际写入文件", text)
        self.assertIn("本地薄执行器已实现；真机阶段暂停", text)


if __name__ == "__main__":
    unittest.main()
