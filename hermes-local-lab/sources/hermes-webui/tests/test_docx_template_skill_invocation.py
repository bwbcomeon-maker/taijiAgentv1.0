from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
MESSAGES_JS = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")


class DocxTemplateSkillInvocationTests(unittest.TestCase):
    def test_plain_docx_template_request_is_routed_to_skill_before_chat_start(self):
        self.assertIn("function normalizeDocxTemplateInvocationText(", MESSAGES_JS)
        self.assertIn("let msgText=normalizeDocxTemplateInvocationText(text);", MESSAGES_JS)

        helper_start = MESSAGES_JS.index("function normalizeDocxTemplateInvocationText(")
        send_start = MESSAGES_JS.index("async function send(")
        api_start = MESSAGES_JS.index("api('/api/chat/start'", send_start)

        self.assertLess(helper_start, send_start)
        self.assertLess(
            MESSAGES_JS.index("let msgText=normalizeDocxTemplateInvocationText(text);", send_start),
            api_start,
        )

    def test_docx_template_skill_routing_keeps_template_selection_mandatory(self):
        self.assertIn("/docx-template-skill", MESSAGES_JS)
        self.assertIn("先列出可用模板", MESSAGES_JS)
        self.assertIn("不要默认选择模板", MESSAGES_JS)

    def test_natural_language_template_request_is_detected_before_model_stream(self):
        self.assertIn("function isDocxTemplateInvocationText(", MESSAGES_JS)
        self.assertIn("将这份方案套用模板", MESSAGES_JS)
        self.assertIn("帮我把当前方案套用模板", MESSAGES_JS)
        self.assertIn("template_selection_required", MESSAGES_JS)
        self.assertIn("renderDocxTemplateSelectionMessage", MESSAGES_JS)

    def test_template_selection_has_visible_confirm_and_cancel_controls(self):
        ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
        self.assertIn("docx-template-selection-card", ui_js)
        self.assertIn("chooseDocxTemplate", ui_js)
        self.assertIn("dismissDocxTemplateSelection", ui_js)
        self.assertIn("aria-label", ui_js)

    def test_figure_adjustment_workspace_has_visible_actions(self):
        ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
        style_css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn("docx-figure-adjustment-card", ui_js)
        self.assertIn("_docxFigureAdjustmentHtml", ui_js)
        self.assertIn("runDocxDraftPackage", ui_js)
        self.assertIn("runDocxFigureRerender", ui_js)
        self.assertIn("runDocxFigureReplace", ui_js)
        self.assertIn("docx_figure_adjustment", ui_js)
        self.assertIn("图片调整工作台", ui_js)
        self.assertIn("旧 DOCX 需要先重新套模板", ui_js)
        self.assertIn(".docx-figure-adjustment-card", style_css)


if __name__ == "__main__":
    unittest.main()
