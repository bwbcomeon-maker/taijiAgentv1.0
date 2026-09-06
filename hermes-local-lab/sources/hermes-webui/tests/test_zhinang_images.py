"""Security and fail-soft contracts for the Zhinang role-image manifest."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlparse

import pytest

from api.zhinang_images import IMAGE_SCHEMA, ASSET_PREFIX, load_role_images


CATALOG_VERSION = "catalog-v1"
ROLE_ID = "agency:sales/sales-engineer"
SECOND_ROLE_ID = "agency:product/product-manager"
REAL_WEBP_512 = base64.b64decode(
    "UklGRi4AAABXRUJQVlA4TCIAAAAv/8F/AAdQsD4UtP8BgUCyv/cMRfQ/4z//+c9//vOf//wf"
)


def _webp(width: int = 512, height: int = 512, *, kind: str = "VP8X") -> bytes:
    if kind == "VP8X":
        payload = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
        chunk = b"VP8X" + struct.pack("<I", len(payload)) + payload
    elif kind == "VP8 ":
        payload = b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", width, height)
        chunk = b"VP8 " + struct.pack("<I", len(payload)) + payload
    elif kind == "VP8L":
        packed = (width - 1) | ((height - 1) << 14)
        payload = b"\x2f" + packed.to_bytes(4, "little")
        chunk = b"VP8L" + struct.pack("<I", len(payload)) + payload
    else:
        raise ValueError(kind)
    if len(payload) % 2:
        chunk += b"\x00"
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def _asset_path(root: Path, name: str) -> Path:
    path = root / "static" / "assets" / "zhinang" / "roles" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _item(role_id: str, path: str, payload: bytes, *, state: str = "active") -> dict:
    return {
        "role_id": role_id,
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "width": 512,
        "height": 512,
        "bytes": len(payload),
        "state": state,
    }


def _write_manifest(root: Path, items: list[dict], **overrides: object) -> Path:
    manifest = {
        "schema_version": IMAGE_SCHEMA,
        "catalog_version": CATALOG_VERSION,
        "images": items,
        **overrides,
    }
    path = root / "static" / "assets" / "zhinang" / "role-images.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _write_valid_manifest(root: Path, *, kind: str = "VP8X", state: str = "active") -> tuple[Path, dict]:
    payload = _webp(kind=kind)
    relative = ASSET_PREFIX + "sales-engineer.webp"
    _asset_path(root, "sales-engineer.webp").write_bytes(payload)
    item = _item(ROLE_ID, relative, payload, state=state)
    return _write_manifest(root, [item]), item


def _append_riff_tail(payload: bytes, tail: bytes) -> bytes:
    body = payload[8:] + tail
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.mark.parametrize("kind", ["VP8X", "VP8 ", "VP8L"])
def test_loads_valid_512_webp_from_each_supported_webp_header(tmp_path, kind):
    manifest, _ = _write_valid_manifest(tmp_path, kind=kind)

    assert load_role_images(manifest, CATALOG_VERSION) == {
        ROLE_ID: ASSET_PREFIX + "sales-engineer.webp"
    }


def test_loads_a_real_512_webp_without_runtime_pillow(tmp_path):
    image_path = _asset_path(tmp_path, "real.webp")
    image_path.write_bytes(REAL_WEBP_512)
    payload = image_path.read_bytes()
    item = _item(ROLE_ID, ASSET_PREFIX + "real.webp", payload)
    manifest = _write_manifest(tmp_path, [item])

    assert load_role_images(manifest, CATALOG_VERSION) == {ROLE_ID: item["path"]}


def test_manifest_path_must_match_the_exact_lexical_layout(tmp_path):
    manifest, _ = _write_valid_manifest(tmp_path)
    decoy = manifest.with_name("role-images-copy.json")
    decoy.write_bytes(manifest.read_bytes())

    assert load_role_images(decoy, CATALOG_VERSION) == {}


def test_symlink_above_the_lexical_root_is_rejected(tmp_path):
    root = tmp_path / "real-parent" / "webui-root"
    manifest, _ = _write_valid_manifest(root)
    alias_parent = tmp_path / "alias-parent"
    alias_parent.symlink_to(root.parent, target_is_directory=True)
    aliased_manifest = alias_parent / root.name / "static" / "assets" / "zhinang" / "role-images.json"

    assert load_role_images(aliased_manifest, CATALOG_VERSION) == {}


@pytest.mark.parametrize(
    ("mutate", "catalog_version"),
    [
        (lambda root, item: (root / "static" / "assets" / "zhinang" / "role-images.json").write_text("{", encoding="utf-8"), CATALOG_VERSION),
        (lambda root, item: _write_manifest(root, [item], schema_version="wrong"), CATALOG_VERSION),
        (lambda root, item: _write_manifest(root, [item], catalog_version="other-catalog"), CATALOG_VERSION),
        (lambda root, item: _write_manifest(root, [item, dict(item)]), CATALOG_VERSION),
        (lambda root, item: _write_manifest(root, [item, {**item, "role_id": SECOND_ROLE_ID}]), CATALOG_VERSION),
    ],
)
def test_manifest_identity_and_uniqueness_faults_fail_soft(tmp_path, mutate, catalog_version):
    manifest, item = _write_valid_manifest(tmp_path)
    mutate(tmp_path, item)

    assert load_role_images(manifest, catalog_version) == {}


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "https://example.test/image.webp",
        "/static/assets/zhinang/roles/image.webp",
        "static\\assets\\zhinang\\roles\\image.webp",
        "static/assets/zhinang/roles/../image.webp",
        "static/assets/zhinang/roles/image.webp?version=1",
        "static/assets/zhinang/roles/image.webp#fragment",
        "static/assets/zhinang/roles/%2e%2e%2fimage.webp",
        "static/assets/zhinang/roles/image%2Ewebp",
    ],
)
def test_unsafe_manifest_paths_fail_the_entire_manifest(tmp_path, unsafe_path):
    manifest, item = _write_valid_manifest(tmp_path)
    _write_manifest(tmp_path, [{**item, "path": unsafe_path}])

    assert load_role_images(manifest, CATALOG_VERSION) == {}


@pytest.mark.parametrize(
    "state",
    ["", "published", None, [], {}],
)
def test_invalid_state_fails_the_entire_manifest(tmp_path, state):
    manifest, item = _write_valid_manifest(tmp_path)
    _write_manifest(tmp_path, [{**item, "state": state}])

    assert load_role_images(manifest, CATALOG_VERSION) == {}


@pytest.mark.parametrize(
    "equivalent_path",
    [
        ASSET_PREFIX + "./sales-engineer.webp",
        ASSET_PREFIX + "/sales-engineer.webp",
    ],
)
def test_normalized_duplicate_paths_fail_the_entire_manifest(tmp_path, equivalent_path):
    manifest, first = _write_valid_manifest(tmp_path)
    duplicate = {**first, "role_id": SECOND_ROLE_ID, "path": equivalent_path}
    _write_manifest(tmp_path, [first, duplicate])

    assert load_role_images(manifest, CATALOG_VERSION) == {}


def test_duplicate_role_id_with_distinct_paths_fails_the_entire_manifest(tmp_path):
    manifest, first = _write_valid_manifest(tmp_path)
    payload = _webp()
    _asset_path(tmp_path, "other.webp").write_bytes(payload)
    duplicate = _item(ROLE_ID, ASSET_PREFIX + "other.webp", payload)
    _write_manifest(tmp_path, [first, duplicate])

    assert load_role_images(manifest, CATALOG_VERSION) == {}


def test_normal_and_review_modes_apply_the_state_gate(tmp_path):
    items = []
    for role_id, name, state in (
        (ROLE_ID, "active.webp", "active"),
        (SECOND_ROLE_ID, "review.webp", "review"),
        ("agency:design/brand-guardian", "approved.webp", "approved"),
        ("agency:marketing/content-creator", "draft.webp", "draft"),
    ):
        payload = _webp()
        relative = ASSET_PREFIX + name
        _asset_path(tmp_path, name).write_bytes(payload)
        items.append(_item(role_id, relative, payload, state=state))
    manifest = _write_manifest(tmp_path, items)

    assert load_role_images(manifest, CATALOG_VERSION) == {ROLE_ID: ASSET_PREFIX + "active.webp"}
    assert load_role_images(manifest, CATALOG_VERSION, include_review=True) == {
        ROLE_ID: ASSET_PREFIX + "active.webp",
        SECOND_ROLE_ID: ASSET_PREFIX + "review.webp",
        "agency:design/brand-guardian": ASSET_PREFIX + "approved.webp",
    }


@pytest.mark.parametrize(
    "corruption",
    ["symlink", "wrong_format", "dimensions", "bytes", "sha256"],
)
def test_bad_file_disables_only_its_role(tmp_path, corruption):
    manifest, first = _write_valid_manifest(tmp_path)
    payload = _webp()
    second_path = _asset_path(tmp_path, "second.webp")
    second_path.write_bytes(payload)
    second = _item(SECOND_ROLE_ID, ASSET_PREFIX + "second.webp", payload)
    _write_manifest(tmp_path, [first, second])

    if corruption == "symlink":
        target = _asset_path(tmp_path, "target.webp")
        target.write_bytes(payload)
        second_path.unlink()
        second_path.symlink_to(target)
    elif corruption == "wrong_format":
        second_path.write_bytes(b"not a webp")
        second = _item(SECOND_ROLE_ID, ASSET_PREFIX + "second.webp", b"not a webp")
        _write_manifest(tmp_path, [first, second])
    elif corruption == "dimensions":
        wrong_size = _webp(511, 512)
        second_path.write_bytes(wrong_size)
        second = _item(SECOND_ROLE_ID, ASSET_PREFIX + "second.webp", wrong_size)
        _write_manifest(tmp_path, [first, second])
    elif corruption == "bytes":
        second["bytes"] += 1
        _write_manifest(tmp_path, [first, second])
    elif corruption == "sha256":
        second["sha256"] = "0" * 64
        _write_manifest(tmp_path, [first, second])

    assert load_role_images(manifest, CATALOG_VERSION) == {ROLE_ID: first["path"]}


def test_cyclic_symlink_disables_only_its_role(tmp_path):
    manifest, first = _write_valid_manifest(tmp_path)
    loop_path = _asset_path(tmp_path, "loop.webp")
    loop_path.symlink_to(loop_path.name)
    loop = _item(SECOND_ROLE_ID, ASSET_PREFIX + "loop.webp", _webp())
    _write_manifest(tmp_path, [first, loop])

    assert load_role_images(manifest, CATALOG_VERSION) == {ROLE_ID: first["path"]}


@pytest.mark.parametrize("corruption", ["truncated_chunk", "missing_padding"])
def test_malformed_riff_tail_disables_only_its_role(tmp_path, corruption):
    manifest, first = _write_valid_manifest(tmp_path)
    tail = (
        b"JUNK" + struct.pack("<I", 4) + b"x"
        if corruption == "truncated_chunk"
        else b"JUNK" + struct.pack("<I", 1) + b"x"
    )
    malformed = _append_riff_tail(_webp(), tail)
    _asset_path(tmp_path, "malformed.webp").write_bytes(malformed)
    second = _item(SECOND_ROLE_ID, ASSET_PREFIX + "malformed.webp", malformed)
    _write_manifest(tmp_path, [first, second])

    assert load_role_images(manifest, CATALOG_VERSION) == {ROLE_ID: first["path"]}


def test_manifest_symlink_is_rejected(tmp_path):
    manifest, _ = _write_valid_manifest(tmp_path)
    target = tmp_path / "manifest-target.json"
    target.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(target)

    assert load_role_images(manifest, CATALOG_VERSION) == {}


def test_manifest_ancestor_symlink_is_rejected(tmp_path):
    root = tmp_path / "root"
    target = tmp_path / "target-zhinang"
    payload = _webp()
    (target / "roles").mkdir(parents=True)
    (target / "roles" / "sales-engineer.webp").write_bytes(payload)
    manifest = target / "role-images.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": IMAGE_SCHEMA,
                "catalog_version": CATALOG_VERSION,
                "images": [_item(ROLE_ID, ASSET_PREFIX + "sales-engineer.webp", payload)],
            }
        ),
        encoding="utf-8",
    )
    (root / "static" / "assets").mkdir(parents=True)
    (root / "static" / "assets" / "zhinang").symlink_to(target, target_is_directory=True)

    assert load_role_images(root / "static" / "assets" / "zhinang" / "role-images.json", CATALOG_VERSION) == {}


def test_roles_root_and_nested_ancestor_symlinks_disable_their_roles(tmp_path):
    manifest, first = _write_valid_manifest(tmp_path)
    roles_root = manifest.parent / "roles"
    target = tmp_path / "roles-target"
    target.mkdir()
    (target / "sales-engineer.webp").write_bytes(_webp())
    roles_root.rename(tmp_path / "original-roles")
    roles_root.symlink_to(target, target_is_directory=True)
    assert load_role_images(manifest, CATALOG_VERSION) == {}

    roles_root.unlink()
    (tmp_path / "original-roles").rename(roles_root)
    nested_target = tmp_path / "nested-target"
    nested_target.mkdir()
    nested_payload = _webp()
    (nested_target / "nested.webp").write_bytes(nested_payload)
    nested_link = roles_root / "nested"
    nested_link.symlink_to(nested_target, target_is_directory=True)
    nested = _item(SECOND_ROLE_ID, ASSET_PREFIX + "nested/nested.webp", nested_payload)
    _write_manifest(tmp_path, [first, nested])

    assert load_role_images(manifest, CATALOG_VERSION) == {ROLE_ID: first["path"]}


def test_image_read_uses_dir_fd_open_and_fstat_binding(monkeypatch, tmp_path):
    from api import zhinang_images

    manifest, _ = _write_valid_manifest(tmp_path)
    opened = []
    fstats = []
    actual_open = zhinang_images.os.open
    actual_fstat = zhinang_images.os.fstat

    def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
        opened.append((path, flags, dir_fd))
        return actual_open(path, flags, mode, dir_fd=dir_fd)

    def tracking_fstat(descriptor):
        fstats.append(descriptor)
        return actual_fstat(descriptor)

    monkeypatch.setattr(zhinang_images.os, "open", tracking_open)
    monkeypatch.setattr(zhinang_images.os, "fstat", tracking_fstat)

    assert load_role_images(manifest, CATALOG_VERSION) == {ROLE_ID: ASSET_PREFIX + "sales-engineer.webp"}
    image_open = next(entry for entry in opened if entry[0] == "sales-engineer.webp")
    assert image_open[2] is not None
    assert image_open[1] & zhinang_images.os.O_NOFOLLOW
    assert fstats


def test_fstat_failure_closes_the_opened_descriptor(monkeypatch, tmp_path):
    from api import zhinang_images

    manifest, _ = _write_valid_manifest(tmp_path)
    opened = []
    closed = []
    actual_open = zhinang_images.os.open
    actual_close = zhinang_images.os.close

    def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = actual_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(zhinang_images.os, "open", tracking_open)
    monkeypatch.setattr(zhinang_images.os, "close", lambda descriptor: (closed.append(descriptor), actual_close(descriptor))[1])
    monkeypatch.setattr(zhinang_images.os, "fstat", lambda _descriptor: (_ for _ in ()).throw(OSError("fstat failed")))

    assert load_role_images(manifest, CATALOG_VERSION) == {}
    assert opened == closed


def test_image_stage_dup_failure_is_fail_soft(monkeypatch, tmp_path):
    from api import zhinang_images

    manifest, _ = _write_valid_manifest(tmp_path)
    actual_dup = zhinang_images.os.dup
    calls = 0

    def fail_for_image_stage(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("dup failed")
        return actual_dup(descriptor)

    monkeypatch.setattr(zhinang_images.os, "dup", fail_for_image_stage)

    assert load_role_images(manifest, CATALOG_VERSION) == {}


def test_platform_fallback_without_dir_fd_flags_still_loads_safe_files(monkeypatch, tmp_path):
    from api import zhinang_images

    manifest, _ = _write_valid_manifest(tmp_path)
    monkeypatch.delattr(zhinang_images.os, "O_NOFOLLOW", raising=False)
    monkeypatch.delattr(zhinang_images.os, "O_DIRECTORY", raising=False)

    assert load_role_images(manifest, CATALOG_VERSION) == {
        ROLE_ID: ASSET_PREFIX + "sales-engineer.webp"
    }


@pytest.mark.parametrize("payload", [b"[" * 1200 + b"0" + b"]" * 1200, b"x" * (1024 * 1024 + 1)])
def test_deep_or_oversized_manifest_fails_soft(tmp_path, payload):
    manifest = tmp_path / "static" / "assets" / "zhinang" / "role-images.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(payload)

    assert load_role_images(manifest, CATALOG_VERSION) == {}


def test_declared_image_size_is_capped_at_160_kib(tmp_path):
    manifest, item = _write_valid_manifest(tmp_path)
    item["bytes"] = 160 * 1024 + 1
    _write_manifest(tmp_path, [item])

    assert load_role_images(manifest, CATALOG_VERSION) == {}


def _catalog_row(role_id: str) -> dict:
    return {
        "role_id": role_id,
        "name": role_id,
        "original_name": role_id,
        "summary": "safe summary",
        "category": "产品与研发",
        "tags": ["safe"],
        "capabilities": ["safe"],
        "featured_order": None,
        "catalog_order": 0,
        "available": True,
    }


def test_review_images_require_all_three_server_conditions(monkeypatch, tmp_path):
    from api.zhinang import PRODUCTION_STATE_DIR, review_images_enabled

    monkeypatch.delenv("TAIJI_ZHINANG_IMAGE_REVIEW", raising=False)
    assert review_images_enabled("127.0.0.1", tmp_path) is False
    monkeypatch.setenv("TAIJI_ZHINANG_IMAGE_REVIEW", "enabled")
    assert review_images_enabled("127.0.0.1", tmp_path) is False
    monkeypatch.setenv("TAIJI_ZHINANG_IMAGE_REVIEW", "1")
    assert review_images_enabled("10.0.0.8", tmp_path) is False
    assert review_images_enabled("invalid host", tmp_path) is False
    assert review_images_enabled("127.0.0.1", PRODUCTION_STATE_DIR) is False
    assert review_images_enabled("::1", tmp_path) is False
    monkeypatch.setenv("HERMES_WEBUI_TEST_STATE_DIR", str(PRODUCTION_STATE_DIR))
    assert review_images_enabled("127.0.0.1", PRODUCTION_STATE_DIR) is False
    monkeypatch.setenv("HERMES_WEBUI_TEST_STATE_DIR", str(tmp_path))
    assert review_images_enabled("::1", tmp_path) is True


def test_review_images_require_an_exact_isolated_state_dir_and_reject_windows_candidates(monkeypatch, tmp_path):
    from api.zhinang import review_images_enabled

    runtime_home = tmp_path / "TaijiAgent" / "runtime"
    runtime_web = runtime_home / "web"
    windows_webui_state = runtime_home.parent / "webui-state"
    isolated_state = tmp_path / "isolated-state"
    linked_runtime_web = tmp_path / "linked-runtime-web"
    linked_runtime_web.mkdir()
    runtime_home.mkdir(parents=True)
    runtime_web.symlink_to(linked_runtime_web, target_is_directory=True)

    monkeypatch.setenv("TAIJI_ZHINANG_IMAGE_REVIEW", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.delenv("HERMES_WEBUI_TEST_STATE_DIR", raising=False)
    assert review_images_enabled("127.0.0.1", isolated_state) is False
    monkeypatch.setenv("HERMES_WEBUI_TEST_STATE_DIR", str(tmp_path / "other-state"))
    assert review_images_enabled("127.0.0.1", isolated_state) is False
    monkeypatch.setenv("HERMES_WEBUI_TEST_STATE_DIR", str(isolated_state))
    assert review_images_enabled("127.0.0.1", isolated_state) is True
    monkeypatch.setenv("HERMES_WEBUI_TEST_STATE_DIR", str(runtime_web))
    assert review_images_enabled("127.0.0.1", runtime_web) is False
    monkeypatch.setenv("HERMES_WEBUI_TEST_STATE_DIR", str(windows_webui_state))
    assert review_images_enabled("127.0.0.1", windows_webui_state) is False
    monkeypatch.setenv("HERMES_WEBUI_TEST_STATE_DIR", str(linked_runtime_web))
    assert review_images_enabled("127.0.0.1", linked_runtime_web) is False


def test_catalog_route_uses_the_real_isolated_review_gate(monkeypatch, tmp_path):
    from api import routes, zhinang

    review_values = []
    monkeypatch.setenv("TAIJI_ZHINANG_IMAGE_REVIEW", "1")
    monkeypatch.setenv("HERMES_WEBUI_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "STATE_DIR", tmp_path)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kwargs: payload)
    monkeypatch.setattr(routes, "_zhinang_profile_favorites", lambda _profile: {})
    monkeypatch.setattr(routes, "_zhinang_recent_roles_for_profile", lambda _profile: {})
    monkeypatch.setattr(
        zhinang,
        "query_catalog_roles",
        lambda **kwargs: review_values.append(kwargs["include_review"]) or {"items": []},
    )

    local = SimpleNamespace(client_address=("127.0.0.1", 12345))
    remote = SimpleNamespace(client_address=("203.0.113.8", 12345))
    assert routes.handle_get(local, urlparse("/api/zhinang/catalog")) == {"items": []}
    assert routes.handle_get(remote, urlparse("/api/zhinang/catalog")) == {"items": []}
    assert review_values == [True, False]


def test_image_projection_uses_role_ids_and_keeps_review_maps_separate(monkeypatch):
    from api import zhinang, zhinang_images

    calls = []

    def fake_load_role_images(_manifest_path, catalog_version, *, include_review):
        calls.append((catalog_version, include_review))
        return {
            ROLE_ID: ASSET_PREFIX + ("review.webp" if include_review else "active.webp"),
            "agency:retired/favorite": ASSET_PREFIX + "favorite.webp",
            "agency:retired/history": ASSET_PREFIX + "history.webp",
        }

    monkeypatch.setattr(zhinang_images, "load_role_images", fake_load_role_images)
    zhinang._ROLE_IMAGE_CACHE.clear()

    normal = zhinang.query_catalog_roles(rows=[_catalog_row(ROLE_ID)], view="all")
    review = zhinang.query_catalog_roles(
        rows=[_catalog_row(ROLE_ID)], view="all", include_review=True
    )
    assert normal["items"][0]["image_path"] == ASSET_PREFIX + "active.webp"
    assert review["items"][0]["image_path"] == ASSET_PREFIX + "review.webp"
    zhinang.query_catalog_roles(rows=[_catalog_row(ROLE_ID)], view="all", include_review=True)
    assert calls == [
        (zhinang.CATALOG_VERSION, False),
        (zhinang.CATALOG_VERSION, True),
    ]

    snapshot = zhinang.snapshot_role_from_catalog(ROLE_ID)
    assert "image_path" not in snapshot["public"]
    assert zhinang.public_session_role_detail_projection(snapshot)["image_path"] == (
        ASSET_PREFIX + "active.webp"
    )
    assert zhinang.public_session_role_detail_projection(
        snapshot, include_review=True
    )["image_path"] == ASSET_PREFIX + "review.webp"

    favorite = zhinang._favorite_record(
        "agency:retired/favorite",
        _catalog_row("agency:retired/favorite"),
        1.0,
    )
    assert "image_path" not in favorite
    favorite_detail = zhinang.removed_role_detail(
        "agency:retired/favorite", favorite=favorite, include_review=True
    )
    history_detail = zhinang.removed_role_detail(
        "agency:retired/history",
        recent={
            **zhinang._favorite_record(
                "agency:retired/history",
                _catalog_row("agency:retired/history"),
                1.0,
            ),
            "last_accepted_at": 1.0,
            "continue_session_id": "historical-session",
        },
        include_review=True,
    )
    assert favorite_detail["image_path"] == ASSET_PREFIX + "favorite.webp"
    assert history_detail["image_path"] == ASSET_PREFIX + "history.webp"


def test_catalog_survives_invalid_image_manifest(monkeypatch):
    from api import zhinang, zhinang_images

    monkeypatch.setattr(zhinang_images, "load_role_images", lambda *_args, **_kwargs: {})
    zhinang._ROLE_IMAGE_CACHE.clear()
    rows = zhinang.load_current_catalog_rows()
    result = zhinang.query_catalog_roles(rows=rows, view="all", include_review=True)

    assert len(rows) == 274
    assert all("image_path" not in row for row in rows)
    assert all("image_path" not in row for row in result["items"])


def test_routes_derive_and_pass_a_request_scoped_review_flag(monkeypatch):
    from api import routes, zhinang

    review_requests = []
    calls = []
    response = {}

    monkeypatch.setattr(
        zhinang,
        "review_images_enabled",
        lambda host, state_dir: review_requests.append((host, state_dir)) or host == "127.0.0.1",
    )
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kwargs: response.update(payload=payload, status=status) or True)
    monkeypatch.setattr(routes, "_zhinang_profile_favorites", lambda _profile: {})
    monkeypatch.setattr(routes, "_zhinang_recent_roles_for_profile", lambda _profile: {})
    monkeypatch.setattr(
        zhinang,
        "query_catalog_roles",
        lambda **kwargs: calls.append(("catalog", kwargs["include_review"])) or {"items": []},
    )
    monkeypatch.setattr(
        zhinang,
        "current_role_detail",
        lambda _role_id, **kwargs: calls.append(("current", kwargs["include_review"])) or {"role_id": ROLE_ID},
    )
    monkeypatch.setattr(
        zhinang,
        "removed_role_detail",
        lambda _role_id, **kwargs: calls.append(("removed", kwargs["include_review"])) or {"role_id": _role_id},
    )
    monkeypatch.setattr(zhinang, "session_has_zhinang_binding", lambda _session: True)
    monkeypatch.setattr(
        zhinang,
        "public_session_role_detail_projection",
        lambda _snapshot, **kwargs: calls.append(("session", kwargs["include_review"])) or {"role_id": ROLE_ID},
    )

    def request(path: str, host: str):
        handler = SimpleNamespace(client_address=(host, 12345))
        assert routes.handle_get(handler, urlparse(path)) is True

    request("/api/zhinang/catalog", "127.0.0.1")
    request("/api/zhinang/roles/" + quote(ROLE_ID, safe=""), "203.0.113.8")

    def missing_current(_role_id, **_kwargs):
        raise zhinang.CatalogResourceError("role_not_found")

    monkeypatch.setattr(zhinang, "current_role_detail", missing_current)
    request("/api/zhinang/roles/agency%3Aretired%2Ffavorite", "127.0.0.1")
    monkeypatch.setattr(
        routes,
        "get_session",
        lambda _sid: SimpleNamespace(profile="default", zhinang_role_snapshot={}),
    )
    request("/api/zhinang/session-role?session_id=role-session", "203.0.113.8")

    assert calls == [
        ("catalog", True),
        ("current", False),
        ("removed", True),
        ("session", False),
    ]
    assert [host for host, _state_dir in review_requests] == [
        "127.0.0.1",
        "203.0.113.8",
        "127.0.0.1",
        "203.0.113.8",
    ]
