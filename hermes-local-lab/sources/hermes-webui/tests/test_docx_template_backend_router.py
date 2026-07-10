from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES_PY = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")


class DocxTemplateBackendRouterTests(unittest.TestCase):
    def test_chat_start_has_backend_template_selection_gate(self):
        self.assertIn("def _docx_template_invocation_result(", ROUTES_PY)
        self.assertIn("template_selection_required", ROUTES_PY)
        self.assertIn("将这份方案套用模板", ROUTES_PY)
        invocation = "_docx_template_invocation_result_for_session(msg, s, attachments)"
        self.assertIn(invocation, ROUTES_PY)
        self.assertLess(
            ROUTES_PY.index(invocation),
            ROUTES_PY.index("_enrich_plan_like_chat_prompt(msg)"),
        )

    def test_backend_can_map_explicit_general_proposal_template(self):
        self.assertIn("general-proposal", ROUTES_PY)
        self.assertIn("通用方案模板", ROUTES_PY)
        self.assertIn("def _normalize_docx_template_invocation_message(", ROUTES_PY)
        self.assertIn("templateId: {template_id}", ROUTES_PY)
        self.assertIn("msg = _normalize_docx_template_invocation_message(msg)", ROUTES_PY)

    def test_backend_has_figure_adjustment_workspace_gate_and_api(self):
        self.assertIn("def _docx_figure_adjustment_invocation_result(", ROUTES_PY)
        self.assertIn("docx_figure_adjustment_required", ROUTES_PY)
        self.assertIn("_docx_figure_adjustment_invocation_result(msg)", ROUTES_PY)
        self.assertIn("/api/docx-template/figure-adjust/package", ROUTES_PY)
        self.assertIn("/api/docx-template/figure-adjust/rerender", ROUTES_PY)
        self.assertIn("/api/docx-template/figure-adjust/replace", ROUTES_PY)
        self.assertLess(
            ROUTES_PY.index("_docx_figure_adjustment_invocation_result(msg)"),
            ROUTES_PY.index("_enrich_plan_like_chat_prompt(msg)"),
        )


if __name__ == "__main__":
    unittest.main()
