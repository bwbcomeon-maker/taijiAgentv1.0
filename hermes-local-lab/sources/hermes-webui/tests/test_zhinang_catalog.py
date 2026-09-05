"""Contracts for the immutable Taiji Zhinang source catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from api.zhinang import (
    AGENCY_AGENTS_COMMIT,
    CatalogResourceError,
    ZhinangContentCatalog,
    ZhinangSourceCatalog,
)


def _source(name: str, sentinel: str) -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Test-only role\n"
        'color: "#123456"\n'
        "---\n\n"
        f"# {name}\n\n{sentinel}\n"
    ).encode("utf-8")


def _write_catalog(root: Path) -> dict[str, bytes]:
    roles = {
        "sales/sentinel-alpha.md": _source(
            "Sentinel Alpha", "TAIJI_ZHINANG_SENTINEL_ALPHA_8F1C"
        ),
        "product/sentinel-beta.md": _source(
            "Sentinel Beta", "TAIJI_ZHINANG_SENTINEL_BETA_4D72"
        ),
    }
    source_root = root / "upstream" / "agency-agents"
    manifest_roles = []
    for source_path, payload in roles.items():
        target = source_root / source_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        manifest_roles.append(
            {
                "role_id": f"agency:{source_path.removesuffix('.md')}",
                "division": source_path.split("/", 1)[0],
                "source_path": source_path,
                "source_bytes": len(payload),
                "source_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    license_payload = b"MIT License\n"
    divisions_payload = json.dumps(
        {
            "divisions": {
                "product": {"label": "Product"},
                "sales": {"label": "Sales"},
            }
        }
    ).encode("utf-8")
    (root / "LICENSE.agency-agents").write_bytes(license_payload)
    (root / "divisions.json").write_bytes(divisions_payload)
    manifest = {
        "schema_version": 1,
        "catalog_version": "agency-agents-af128a92888f-source-v1",
        "upstream_repository": "https://github.com/msitarzewski/agency-agents",
        "upstream_commit": AGENCY_AGENTS_COMMIT,
        "source_root": "upstream/agency-agents",
        "license_path": "LICENSE.agency-agents",
        "license_bytes": len(license_payload),
        "license_sha256": hashlib.sha256(license_payload).hexdigest(),
        "divisions_path": "divisions.json",
        "divisions_bytes": len(divisions_payload),
        "divisions_sha256": hashlib.sha256(divisions_payload).hexdigest(),
        "role_count": len(manifest_roles),
        "roles": manifest_roles,
    }
    (root / "source-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return roles


def test_catalog_validates_and_reads_distinct_test_roles(tmp_path):
    _write_catalog(tmp_path)

    catalog = ZhinangSourceCatalog(tmp_path)
    snapshot = catalog.validate()
    alpha = catalog.read_role("agency:sales/sentinel-alpha")
    beta = catalog.read_role("agency:product/sentinel-beta")

    assert snapshot.catalog_version == "agency-agents-af128a92888f-source-v1"
    assert snapshot.upstream_commit == AGENCY_AGENTS_COMMIT
    assert snapshot.role_count == 2
    assert "TAIJI_ZHINANG_SENTINEL_ALPHA_8F1C" in alpha.raw_source
    assert "TAIJI_ZHINANG_SENTINEL_BETA_4D72" not in alpha.raw_source
    assert "TAIJI_ZHINANG_SENTINEL_BETA_4D72" in beta.raw_source
    assert alpha.effective_prompt_sha256 == alpha.source_sha256


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing", "source_missing"),
        ("changed", "source_digest_mismatch"),
        ("extra", "source_inventory_mismatch"),
    ],
)
def test_catalog_rejects_damaged_or_unlisted_resources(tmp_path, mutation, expected_code):
    _write_catalog(tmp_path)
    source_root = tmp_path / "upstream" / "agency-agents"
    alpha = source_root / "sales" / "sentinel-alpha.md"
    if mutation == "missing":
        alpha.unlink()
    elif mutation == "changed":
        alpha.write_text("changed", encoding="utf-8")
    else:
        (source_root / "sales" / "unlisted.md").write_text(
            "---\nname: Unlisted\ndescription: x\ncolor: blue\n---\n",
            encoding="utf-8",
        )

    with pytest.raises(CatalogResourceError) as raised:
        ZhinangSourceCatalog(tmp_path).validate()

    assert raised.value.code == expected_code
    assert str(tmp_path) not in str(raised.value)


def test_catalog_rejects_path_traversal_before_reading_outside_root(tmp_path):
    _write_catalog(tmp_path)
    manifest_path = tmp_path / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["roles"][0]["source_path"] = "../outside.md"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    outside = tmp_path.parent / "outside.md"
    outside.write_text("must not be read", encoding="utf-8")

    with pytest.raises(CatalogResourceError) as raised:
        ZhinangSourceCatalog(tmp_path).validate()

    assert raised.value.code == "manifest_invalid"
    assert "outside.md" not in str(raised.value)


def test_catalog_rejects_wrong_upstream_version(tmp_path):
    _write_catalog(tmp_path)
    manifest_path = tmp_path / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["upstream_commit"] = "0" * 40
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CatalogResourceError) as raised:
        ZhinangSourceCatalog(tmp_path).validate()

    assert raised.value.code == "catalog_version_mismatch"


@pytest.mark.parametrize("control_name", ["LICENSE.agency-agents", "divisions.json"])
def test_catalog_rejects_changed_control_resources(tmp_path, control_name):
    _write_catalog(tmp_path)
    with (tmp_path / control_name).open("ab") as handle:
        handle.write(b" ")

    with pytest.raises(CatalogResourceError) as raised:
        ZhinangSourceCatalog(tmp_path).validate()

    assert raised.value.code == "control_digest_mismatch"
    assert str(tmp_path) not in str(raised.value)


def test_catalog_returns_explicit_not_found_without_treating_id_as_path(tmp_path):
    _write_catalog(tmp_path)
    catalog = ZhinangSourceCatalog(tmp_path)
    catalog.validate()

    with pytest.raises(CatalogResourceError) as raised:
        catalog.read_role("agency:../../private")

    assert raised.value.code == "role_not_found"
    assert "private" not in str(raised.value)


def test_builtin_chinese_content_matches_every_source_and_local_role():
    source = ZhinangSourceCatalog().validate()
    content = ZhinangContentCatalog().validate(source)

    assert len(content) == source.role_count + 1 == 274
    assert set(content) - set(source.roles) == {"taiji:document-reviewer"}
    assert {item["category"] for item in content.values()} == {
        "售前与方案",
        "产品与研发",
        "设计与体验",
        "市场与增长",
        "文档与研究",
        "运营与管理",
    }
    for item in content.values():
        assert item["name"]
        assert item["original_name"]
        assert item["summary"]
        assert 3 <= len(item["capabilities"]) <= 5
        assert 2 <= len(item["deliverable_examples"]) <= 3
        assert item["starter_examples"]
