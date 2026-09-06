"""Build-time image conversion and the fixed pilot's full decode contracts."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/prepare_zhinang_role_images.py'
ROLE_IDS = (
    'agency:sales/sales-engineer', 'agency:sales/sales-proposal-strategist',
    'agency:product/product-manager', 'agency:engineering/engineering-software-architect',
    'agency:marketing/marketing-content-creator', 'agency:marketing/marketing-aeo-foundations',
    'taiji:document-reviewer', 'agency:specialized/grant-writer',
    'agency:specialized/accounts-payable-agent', 'agency:specialized/automation-governance-architect',
    'agency:specialized/specialized-cultural-intelligence-strategist', 'agency:design/design-brand-guardian',
)

@pytest.fixture(scope='session', autouse=True)
def test_server():
    """Conversion contracts use local files and need no HTTP server."""
    yield

@pytest.fixture(autouse=True)
def _fail_fast_if_test_server_exits():
    """No server exists in this build-time test module."""
    yield

@pytest.fixture
def converter():
    assert SCRIPT.is_file(), 'PNG-to-WebP preparation script must exist'
    spec = importlib.util.spec_from_file_location('prepare_images', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png(path, size=(1024, 1024), format='PNG'):
    Image.new('RGB', size, '#92cde3').save(path, format=format)
    return path


def test_conversion_fully_decodes_and_records_source(converter, tmp_path):
    source = png(tmp_path / 'source.png')
    payload, meta = converter.convert_png(source)
    assert meta['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert meta['source_width'] == meta['source_height'] == 1024
    assert meta['quality'] == 82
    assert len(payload) <= 160 * 1024
    with Image.open(io.BytesIO(payload)) as im:
        assert im.format == 'WEBP' and im.size == (512, 512)
        im.verify()
    with Image.open(io.BytesIO(payload)) as im:
        im.load()


@pytest.mark.parametrize('kind', ['text', 'symlink', 'wrong_size', 'jpeg', 'truncated'])
def test_refuses_invalid_source(converter, tmp_path, kind):
    source = tmp_path / 'input.png'
    if kind == 'text':
        source.write_text('not an image')
    elif kind == 'symlink':
        source.symlink_to(png(tmp_path / 'actual.png'))
    elif kind == 'wrong_size':
        png(source, (512, 512))
    elif kind == 'jpeg':
        png(source, format='JPEG')
    else:
        png(source)
        source.write_bytes(source.read_bytes()[:100])
    with pytest.raises(ValueError):
        converter.convert_png(source)


def test_quality_sequence_and_hard_limit(converter, tmp_path, monkeypatch):
    source = png(tmp_path / 'source.png')
    calls = []
    original = Image.Image.save
    def save(image, target, format=None, **kwargs):
        if format == 'WEBP':
            calls.append(kwargs['quality'])
            target.write(b'x' * (160 * 1024 + 1))
        else:
            original(image, target, format=format, **kwargs)
    monkeypatch.setattr(Image.Image, 'save', save)
    with pytest.raises(ValueError, match='160 KiB'):
        converter.convert_png(source)
    assert calls == [82, 78, 74, 70, 66]

def test_normalization_preserves_raw_and_normalized_provenance(converter, tmp_path):
    raw = png(tmp_path / 'generated.png', (1254, 1254))
    target = tmp_path / 'normalized.png'
    provenance = converter.normalize_generated_png(raw, target)
    assert provenance['generated_source_dimensions'] == [1254, 1254]
    assert provenance['normalized_source_dimensions'] == [1024, 1024]
    assert provenance['generated_source_sha256'] == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert provenance['normalized_source_sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert provenance['normalization_method'] == 'Pillow LANCZOS'
    converter.convert_png(target)


def test_preparation_exact_set_canonical_audit_and_no_overwrite(converter, tmp_path):
    sources = tmp_path / 'sources'
    sources.mkdir()
    provenance = {}
    for role in ROLE_IDS:
        raw = png(tmp_path / 'generated.png', (1254, 1254))
        metadata = converter.normalize_generated_png(raw, sources / (converter.role_slug(role) + '.png'))
        provenance[role] = {**metadata, 'prompt': converter.role_prompt(role), 'visual_check': 'test fixture'}
    (sources / 'generation-inputs.json').write_bytes(converter.canonical_json(provenance))
    dest = tmp_path / 'static/assets/zhinang'
    manifest = dest / 'role-images.json'
    converter.prepare(ROOT / 'data/zhinang/chinese-content-v1.json', sources, dest / 'roles', manifest)
    raw = manifest.read_bytes()
    data = json.loads(raw)
    assert raw == converter.canonical_json(data)
    assert {r['role_id'] for r in data['images']} == set(ROLE_IDS)
    audit = json.loads((dest / 'pilot-generation-record.json').read_bytes())
    assert {r['role_id'] for r in audit['images']} == set(ROLE_IDS)
    for row in audit['images']:
        assert set(row['input_snapshot']) == {'name', 'category', 'summary', 'capabilities'}
        assert row['prompt'] and row['prompt_sha256'] == hashlib.sha256(row['prompt'].encode()).hexdigest()
        assert row['state'] == 'review' and row['reviewer'] is None and row['reviewed_at'] is None
        assert row['model'] == 'image-2.0'
        assert row['generated_source_dimensions'] == [1254, 1254]
        assert row['normalized_source_dimensions'] == [1024, 1024]
        assert row['normalized_source_sha256'] == row['source']['sha256']
        assert row['normalization_method'] == 'Pillow LANCZOS'
    target = tmp_path / data['images'][0]['path']
    target.write_bytes(b'different content')
    with pytest.raises(ValueError, match='overwrite'):
        converter.prepare(ROOT / 'data/zhinang/chinese-content-v1.json', sources, dest / 'roles', manifest)
    assert target.read_bytes() == b'different content'


def test_repository_images_fully_decode_and_include_pilot(converter):
    manifest = ROOT / 'static/assets/zhinang/role-images.json'
    if not manifest.exists():
        pytest.skip('pilot generation has not yet run')
    data = json.loads(manifest.read_bytes())
    audit = json.loads(manifest.with_name('pilot-generation-record.json').read_bytes())
    assert set(ROLE_IDS) <= {row['role_id'] for row in data['images']}
    assert {row['role_id'] for row in audit['images']} == set(ROLE_IDS)
    assert len(audit['images']) == 12
    assert len({row['role_id'] for row in data['images']}) == len(data['images'])
    assert len({row['path'] for row in data['images']}) == len(data['images'])
    for row in data['images']:
        path = ROOT / row['path']
        assert path.stat().st_size == row['bytes'] <= 160 * 1024
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        assert row['state'] in ('review', 'active')
        if row['state'] == 'active':
            assert row['reviewer'] and row['reviewed_at']
        assert row['source_sha256'] and row['conversion']['quality'] in [82, 78, 74, 70, 66]
        with Image.open(path) as image:
            assert image.format == 'WEBP' and image.size == (512, 512)
            image.verify()
        with Image.open(path) as image:
            image.load()
    for row in audit['images']:
        assert row['source']['width'] == row['source']['height'] == 1024
        assert row['source']['sha256'] and row['sha256'] and row['prompt_sha256']
        assert row['input_snapshot']['capabilities'] and row['prompt']
        assert row['generated_source_sha256'] and row['generated_source_dimensions'] == [1254, 1254]
        assert row['normalized_source_sha256'] == row['source']['sha256']
        assert row['normalized_source_dimensions'] == [1024, 1024]
        assert row['normalization_method'] == 'Pillow LANCZOS'
