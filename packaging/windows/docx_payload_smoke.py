"""Isolated Windows payload Python -> private Node -> DOCX verification."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile


def validate_render(result: dict, delivery: Path) -> None:
    if (result.get('ok') is not True or not result.get('jobId')
            or result.get('qualityStatus') not in ('passed', 'passed_with_warnings')
            or result.get('replayStatus') not in ('passed', 'passed_with_warnings')):
        raise ValueError(f'DOCX render did not complete: {result}')
    document = Path(result.get('documentPath', '')).resolve()
    if (Path(result.get('deliveryDir', '')).resolve() != delivery.resolve()
            or document.parent != delivery.resolve() or document.suffix != '.docx'
            or not document.is_file() or document.stat().st_size == 0):
        raise ValueError('DOCX output is missing or outside isolated delivery directory')
    try:
        with zipfile.ZipFile(document) as archive:
            if archive.testzip() is not None or not {'[Content_Types].xml', 'word/document.xml'} <= set(archive.namelist()):
                raise ValueError('DOCX archive is incomplete')
    except zipfile.BadZipFile as exc:
        raise ValueError('DOCX output is not a valid archive') from exc


def main(payload: Path, scratch: Path) -> None:
    payload = payload.resolve(strict=True)
    scratch = scratch.resolve(strict=True)
    if scratch == payload or scratch.is_relative_to(payload):
        raise ValueError('Smoke scratch must be outside immutable payload')
    lab = payload / 'hermes-local-lab'
    node = lab / 'runtime/node/node.exe'
    engine = lab / 'sources/docx-engine-v2'
    with tempfile.TemporaryDirectory(prefix='docx-smoke-', dir=scratch) as temporary:
        work = Path(temporary)
        for key in ('TAIJI_RUNTIME_HOME', 'HERMES_HOME', 'TAIJI_ACCOUNT_HOME', 'TMP', 'TEMP', 'TMPDIR'):
            os.environ[key] = str(work)
        os.environ.update(TAIJI_WINDOWS_CANDIDATE='1',
                          TAIJI_DOCX_ENGINE_V2_ROOT=str(engine),
                          TAIJI_DOCX_BUILTIN_ROOT=str(engine),
                          TAIJI_DOCX_RUNTIME_HOME=str(work / 'templates'),
                          PATH=str(node.parent) + os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32'))
        sys.path[:0] = [str(lab / 'sources/hermes-webui'), str(lab / 'sources/hermes-agent')]
        import taiji_runtime_profile
        from api import docx_engine_v2
        if taiji_runtime_profile.installation_profile() != 'windows-candidate':
            raise ValueError('Payload must use the build-owned Windows candidate profile')
        if Path(shutil.which('node') or '').resolve() != node.resolve(strict=True):
            raise ValueError('Python did not resolve payload private Node')
        for module, root in ((docx_engine_v2, lab / 'sources/hermes-webui'),
                             (taiji_runtime_profile, lab / 'sources/hermes-agent')):
            if not Path(module.__file__).resolve().is_relative_to(root.resolve()):
                raise ValueError('Smoke imported code outside payload')
        templates = docx_engine_v2.list_templates()
        if templates.get('ok') is not True or not any(t.get('id') == 'general-proposal' for t in templates.get('templates', [])):
            raise ValueError('Payload template enumeration failed')
        source = work / 'source.md'
        source.write_text('# Windows DOCX Smoke\n\n## Architecture\n\n'
                          '| Component | Role |\n| --- | --- |\n| Source | Input |\n\n'
                          '```mermaid\nflowchart LR\n A[Source] --> B[Delivery]\n```\n', encoding='utf-8')
        delivery = work / 'delivery'
        completed = docx_engine_v2.run_engine([
            str(engine / 'src/cli/run-job.js'), '--template-id', 'general-proposal',
            '--source', str(source), '--out-dir', str(delivery), '--json'])
        if completed.returncode != 0:
            raise ValueError(f'DOCX child failed ({completed.returncode}): {completed.stdout} {completed.stderr}')
        result = json.loads(completed.stdout)
        validate_render(result, delivery)
        print('WINDOWS_PAYLOAD_DOCX_OK ' + json.dumps({'templates': len(templates['templates']),
              'document_bytes': Path(result['documentPath']).stat().st_size, 'node': str(node)}))


if __name__ == '__main__':
    main(Path(sys.argv[1]), Path(sys.argv[2]))
