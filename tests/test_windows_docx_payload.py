"""Windows private DOCX assembly contracts; execution is also checked on Windows."""
import json
import importlib.util
import tempfile
import zipfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WindowsDocxPayloadTests(unittest.TestCase):
    def test_render_validation_rejects_false_success(self):
        path = ROOT / 'packaging/windows/docx_payload_smoke.py'
        self.assertTrue(path.is_file(), 'payload smoke validator is missing')
        spec = importlib.util.spec_from_file_location('docx_payload_smoke', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            delivery = Path(tmp)
            document = delivery / 'document.docx'
            result = {'ok': True, 'jobId': 'smoke', 'deliveryDir': str(delivery),
                      'documentPath': str(document), 'qualityStatus': 'passed', 'replayStatus': 'passed'}
            with self.assertRaises(ValueError):
                module.validate_render(result, delivery)
            with zipfile.ZipFile(document, 'w') as archive:
                archive.writestr('[Content_Types].xml', '<Types/>')
                archive.writestr('word/document.xml', '<document/>')
            module.validate_render(result, delivery)
            for update in ({'ok': False}, {'jobId': ''}, {'qualityStatus': 'failed'},
                           {'documentPath': str(delivery.parent / 'outside.docx')}):
                with self.assertRaises(ValueError):
                    module.validate_render(dict(result, **update), delivery)

    def test_private_node_is_bound_to_cache_and_target(self):
        requirements = json.loads((ROOT / 'packaging/windows/cache-requirements.json').read_text())
        entries = {entry['id']: entry for entry in requirements['entries']}
        self.assertIn('private-node-runtime', entries)
        self.assertEqual(entries['private-node-runtime']['version'], '22.23.1')
        target = json.loads((ROOT / 'packaging/pipeline/targets/windows-x64.json').read_text())
        self.assertEqual(target['node'], r'D:\tw\cache\node-v22.23.1-win-x64\node.exe')

    def test_docx_assembled_offline_before_manifest(self):
        stage = (ROOT / 'packaging/windows/Stage-CandidatePayload.ps1').read_text()
        self.assertIn("'docx-engine-v2'", stage)
        self.assertIn('ci --offline --ignore-scripts --no-audit', stage)
        self.assertIn('Node payload identity drifted from cache observation', stage)
        self.assertLess(stage.index('ci --offline'), stage.index('$payloadManifest ='))
        self.assertIn('$docxModulesPrefix', stage)

    def test_docx_smoke_is_before_inno_and_keeps_seven_checks(self):
        build = (ROOT / 'packaging/windows/Build-CandidateReview.ps1').read_text()
        self.assertIn('Test-DocxPayload.ps1', build)
        self.assertLess(build.index('Test-DocxPayload.ps1'), build.index('Invoke-FormalCheck -Id "inno-compile"'))
        self.assertEqual(build.count('Invoke-FormalCheck -Id '), 7)


if __name__ == '__main__':
    unittest.main()
