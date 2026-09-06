#!/usr/bin/env python3
"""Prepare the fixed Zhinang review pilot. Pillow is a build-time dependency only."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re

from PIL import Image, __version__ as PILLOW_VERSION

STYLE_SUFFIX = (
    'Premium lightweight 3D icon scene; friendly professional character or occupational subject; '
    'frosted-glass information board; soft matte polymer; Taiji blue, cyan, deep navy, white and cool gray; '
    'pale ice-blue background; centered square composition; large simple silhouette readable at 96px; '
    'no text, letters, numbers, logo, watermark or frame; avoid generic robots, childish toys, clutter and tiny details.'
)
PILOT_SUBJECTS = {
    'agency:sales/sales-engineer': 'consultant connecting customer dialogue, architecture nodes, and PoC checklist.',
    'agency:sales/sales-proposal-strategist': 'consultant arranging requirement document, evidence blocks, and evaluation target.',
    'agency:product/product-manager': 'product lead balancing user insight, prioritized roadmap cards, and acceptance check.',
    'agency:engineering/engineering-software-architect': 'architect connecting modular system blocks with dependency and resilience symbols.',
    'agency:marketing/marketing-content-creator': 'creator arranging audience, story structure, and multichannel content objects.',
    'agency:marketing/marketing-aeo-foundations': 'specialist inspecting crawler path, structured content nodes, and citation beacon.',
    'taiji:document-reviewer': 'reviewer comparing two documents with consistency markers and issue lens.',
    'agency:specialized/grant-writer': 'writer connecting project goal, budget blocks, evidence, and evaluation plan.',
    'agency:specialized/accounts-payable-agent': 'operator matching invoice, approval route, duplicate check, and audit record.',
    'agency:specialized/automation-governance-architect': 'governance lead supervising workflow nodes, human checkpoint, shield, and audit trail.',
    'agency:specialized/specialized-cultural-intelligence-strategist': 'facilitator connecting diverse user silhouettes, dialogue, and inclusive interface panel.',
    'agency:design/design-brand-guardian': 'brand guardian aligning color/material samples, message system, and consistency shield.',
}
QUALITY_SEQUENCE = (82, 78, 74, 70, 66)
MAX_BYTES = 160 * 1024
ASSET_PREFIX = 'static/assets/zhinang/roles/'


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def role_slug(role_id: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', role_id.split(':', 1)[-1].split('/')[-1]).strip('-')


def role_prompt(role_id: str) -> str:
    return f'Create one 1024x1024 PNG role illustration. Subject: {PILOT_SUBJECTS[role_id]}\n{STYLE_SUFFIX}'


def normalize_generated_png(source: Path, destination: Path) -> dict:
    """Keep evidence of the built-in service's 1254-square output before normalization."""
    if source.is_symlink() or not source.is_file() or destination.is_symlink():
        raise ValueError('Normalization requires regular non-symlink files')
    raw = source.read_bytes()
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or image.size not in ((1254, 1254), (1024, 1024)):
                raise ValueError('Generated source must be a 1254 or 1024 square PNG')
            dimensions = list(image.size)
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            image = image.resize((1024, 1024), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format='PNG')
    except (OSError, SyntaxError) as error:
        raise ValueError('Cannot decode generated PNG') from error
    payload = output.getvalue()
    if destination.exists():
        if destination.read_bytes() != payload:
            raise ValueError(f'Refusing to overwrite a different digest: {destination}')
    else:
        with destination.open('xb') as stream:
            stream.write(payload)
    return {
        'generated_source_sha256': hashlib.sha256(raw).hexdigest(),
        'generated_source_dimensions': dimensions,
        'generated_source_filename': source.name,
        'normalized_source_sha256': hashlib.sha256(payload).hexdigest(),
        'normalized_source_dimensions': [1024, 1024],
        'normalization_method': 'Pillow LANCZOS',
    }


def convert_png(source: Path) -> tuple[bytes, dict]:
    """Admit only a fully decoded regular 1024-square PNG and verified WebP bytes."""
    if source.is_symlink() or not source.is_file():
        raise ValueError(f'Expected a regular non-symlink PNG: {source}')
    raw = source.read_bytes()
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or image.size != (1024, 1024) or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Source must be a single-frame 1024x1024 PNG')
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            resized = image.convert('RGBA' if 'A' in image.getbands() else 'RGB').resize((512, 512), Image.Resampling.LANCZOS)
    except (OSError, SyntaxError) as error:
        raise ValueError(f'Cannot decode PNG: {source}') from error
    for quality in QUALITY_SEQUENCE:
        output = io.BytesIO()
        resized.save(output, format='WEBP', quality=quality, method=6, exact=True)
        payload = output.getvalue()
        if len(payload) <= MAX_BYTES:
            with Image.open(io.BytesIO(payload)) as verified:
                if verified.format != 'WEBP' or verified.size != (512, 512):
                    raise ValueError('Invalid converted WebP')
                verified.verify()
            with Image.open(io.BytesIO(payload)) as verified:
                verified.load()
            return payload, {
                'source_sha256': hashlib.sha256(raw).hexdigest(),
                'source_width': 1024, 'source_height': 1024,
                'quality': quality, 'quality_sequence': list(QUALITY_SEQUENCE), 'method': 6,
                'exact': True, 'resampling': 'LANCZOS', 'pillow_version': PILLOW_VERSION,
                'width': 512, 'height': 512, 'format': 'WEBP',
            }
    raise ValueError('Converted WebP exceeds 160 KiB at all allowed qualities')


def prepare(catalog: Path, source_dir: Path, output_dir: Path, manifest: Path, *, state='review', model='image-2.0') -> dict:
    if state != 'review' or model != 'image-2.0':
        raise ValueError('Pilot preparation requires state=review and model=image-2.0')
    if output_dir != manifest.parent / 'roles' or manifest.name != 'role-images.json':
        raise ValueError('Output must be the manifest sibling roles directory')
    for path in (output_dir, manifest, manifest.with_name('pilot-generation-record.json')):
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if str(p) not in ('/tmp', '/var')):
            raise ValueError(f'Symlink output is forbidden: {path}')
    rows = json.loads(catalog.read_bytes())
    selected = {row['role_id']: row for row in rows if row['role_id'] in PILOT_SUBJECTS}
    if set(selected) != set(PILOT_SUBJECTS) or sum(row['role_id'] in PILOT_SUBJECTS for row in rows) != 12:
        raise ValueError('Catalog must contain exactly one entry for each fixed pilot role')
    version = json.loads(catalog.with_name('source-manifest.json').read_bytes())['catalog_version']
    provenance = json.loads((source_dir / 'generation-inputs.json').read_bytes())
    if set(provenance) != set(PILOT_SUBJECTS):
        raise ValueError('Generation provenance must bind all twelve pilot roles')
    entries, audit, writes = [], [], []
    for role_id in PILOT_SUBJECTS:
        row = selected[role_id]
        slug = role_slug(role_id)
        payload, conversion = convert_png(source_dir / f'{slug}.png')
        source_sha = conversion.pop('source_sha256')
        conversion.pop('source_width')
        conversion.pop('source_height')
        digest = hashlib.sha256(payload).hexdigest()
        filename = f'{slug}-{hashlib.sha256(role_id.encode()).hexdigest()[:12]}.webp'
        path = output_dir / filename
        if path.exists() and (not path.is_file() or path.read_bytes() != payload):
            raise ValueError(f'Refusing to overwrite a different digest: {path}')
        entry = {
            'role_id': role_id, 'path': ASSET_PREFIX + filename, 'sha256': digest,
            'width': 512, 'height': 512, 'bytes': len(payload), 'model': model,
            'source_sha256': source_sha, 'conversion': conversion,
            'state': state, 'reviewer': None, 'reviewed_at': None,
        }
        evidence = provenance[role_id]
        prompt = evidence['prompt']
        if (
            evidence.get('normalized_source_sha256') != source_sha
            or evidence.get('normalized_source_dimensions') != [1024, 1024]
            or evidence.get('generated_source_dimensions') not in ([1254, 1254], [1024, 1024])
            or not re.fullmatch(r'[0-9a-f]{64}', evidence.get('generated_source_sha256', ''))
            or evidence.get('normalization_method') != 'Pillow LANCZOS'
            or not isinstance(prompt, str)
            or PILOT_SUBJECTS[role_id] not in prompt
            or not prompt.endswith(STYLE_SUFFIX)
        ):
            raise ValueError(f'Generation provenance mismatch: {role_id}')
        audit.append({
            **evidence,
            **entry,
            'input_snapshot': {key: row[key] for key in ('name', 'category', 'summary', 'capabilities')},
            'prompt': prompt, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'source': {'filename': f'{slug}.png', 'sha256': source_sha, 'format': 'PNG', 'width': 1024, 'height': 1024},
        })
        entries.append(entry)
        writes.append((path, payload))
    batch = hashlib.sha256(canonical_json([(r['role_id'], r['source_sha256']) for r in entries])).hexdigest()
    common = {'catalog_version': version, 'generation_batch': batch}
    manifest_data = {**common, 'schema_version': 'taiji-zhinang-role-images/v1', 'images': entries}
    audit_data = {**common, 'schema_version': 'taiji-zhinang-pilot-generation/v1', 'images': audit}
    writes.extend([(manifest, canonical_json(manifest_data)), (manifest.with_name('pilot-generation-record.json'), canonical_json(audit_data))])
    # All twelve conversions and collision checks finish before any output is written.
    for path, payload in writes:
        if path.exists() and (not path.is_file() or path.read_bytes() != payload):
            raise ValueError(f'Refusing to overwrite a different digest: {path}')
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in writes:
        if path.exists():
            if path.read_bytes() != payload:
                raise ValueError(f'Refusing to overwrite a different digest: {path}')
        else:
            with path.open('xb') as stream:
                stream.write(payload)
    return manifest_data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--state', choices=['review'], default='review')
    parser.add_argument('--model', choices=['image-2.0'], default='image-2.0')
    args = parser.parse_args()
    result = prepare(args.catalog, args.source_dir, args.output_dir, args.manifest, state=args.state, model=args.model)
    print(json.dumps({'status': 'PASS', 'images': len(result['images']), 'bytes': sum(row['bytes'] for row in result['images'])}))


if __name__ == '__main__':
    main()
