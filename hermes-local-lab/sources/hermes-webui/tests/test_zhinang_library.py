"""Focused contracts for the Taiji Zhinang catalog, favorites, and recents."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from urllib.parse import quote
import urllib.error
import urllib.request

import pytest

from api.models import Session
from api.zhinang import (
    CATALOG_CATEGORIES,
    CATALOG_PAGE_SIZE,
    ZhinangFavoritesStore,
    current_role_detail,
    load_current_catalog_rows,
    query_catalog_roles,
    removed_role_detail,
    select_recent_roles,
    snapshot_role_from_catalog,
)
CATALOG_VERSION = "agency-agents-af128a92888f-source-v1"
TEST_STATE_DIR = Path(os.environ["HERMES_WEBUI_TEST_STATE_DIR"])


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    profile: str | None = None,
) -> tuple[dict, int]:
    base = f"http://127.0.0.1:{os.environ['HERMES_WEBUI_TEST_PORT']}"
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if profile:
        headers["Cookie"] = f"hermes_profile={profile}"
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read()), response.status
    except urllib.error.HTTPError as error:
        return json.loads(error.read()), error.code


def _row(
    role_id: str,
    *,
    name: str,
    category: str = "产品与研发",
    featured_order: int | None = None,
    catalog_order: int = 0,
    raw_source: str = "",
) -> dict:
    return {
        "role_id": role_id,
        "name": name,
        "original_name": name.upper(),
        "summary": f"{name} summary",
        "category": category,
        "tags": [f"{name} tag", "shared"],
        "capabilities": [f"{name} capability"],
        "featured_order": featured_order,
        "catalog_order": catalog_order,
        "raw_source": raw_source,
        "available": True,
    }


def _favorite(row: dict, updated_at: float = 1.0) -> dict:
    return {
        "role_id": row["role_id"],
        "name": row["name"],
        "category": row["category"],
        "tags": list(row["tags"]),
        "summary": row["summary"],
        "updated_at": updated_at,
    }


def _recent_row(
    session_id: str,
    role_id: str,
    accepted_at: float,
    *,
    pre_snapshot: bool = False,
    tip_id: str | None = None,
) -> dict:
    row = {
        "session_id": session_id,
        "profile": "default",
        "pre_compression_snapshot": pre_snapshot,
        "zhinang_role": {
            "role_id": role_id,
            "name": f"Role {role_id}",
            "original_name": role_id,
            "summary": "historical summary",
            "category": "产品与研发",
            "tags": ["history"],
            "last_accepted_at": accepted_at,
        },
    }
    if tip_id:
        row["_lineage_tip_id"] = tip_id
    return row


def test_current_catalog_has_fixed_categories_featured_order_and_safe_rows():
    rows = load_current_catalog_rows()
    result = query_catalog_roles(rows=rows)

    assert len(rows) == 274
    assert result["catalog_version"] == CATALOG_VERSION
    assert result["page_size"] == CATALOG_PAGE_SIZE == 24
    assert result["filters"] == {
        "scope": "all",
        "category": "all",
        "view": "featured",
        "query": "",
    }
    assert [item["featured_order"] for item in result["items"]] == list(range(1, 7))
    assert [item["role_id"] for item in result["items"]] == [
        "agency:sales/sales-engineer",
        "agency:sales/sales-proposal-strategist",
        "agency:product/product-manager",
        "agency:engineering/engineering-software-architect",
        "agency:marketing/marketing-content-creator",
        "taiji:document-reviewer",
    ]
    assert [item["category"] for item in result["categories"]] == list(CATALOG_CATEGORIES)
    assert all("raw_source" not in item and "effective_prompt" not in item for item in result["items"])


def test_catalog_filters_are_orthogonal_search_safe_and_pagination_stable():
    alpha = _row("agency:test/alpha", name="Alpha", featured_order=2, catalog_order=1)
    beta = _row(
        "agency:test/beta",
        name="Beta",
        category="售前与方案",
        featured_order=1,
        catalog_order=0,
        raw_source="RAW_ONLY_NEVER_SEARCHED",
    )
    gamma = _row("agency:test/gamma", name="Gamma", catalog_order=2)
    favorites = {alpha["role_id"]: _favorite(alpha), beta["role_id"]: _favorite(beta)}
    recent = {
        alpha["role_id"]: {
            **_favorite(alpha),
            "last_accepted_at": 50.0,
            "continue_session_id": "alpha-tip",
        },
        gamma["role_id"]: {
            **_favorite(gamma),
            "last_accepted_at": 60.0,
            "continue_session_id": "gamma-tip",
        },
    }

    intersected = query_catalog_roles(
        rows=[beta, alpha, gamma],
        favorites=favorites,
        recent=recent,
        scope="favorites",
        category="产品与研发",
        view="recent",
        query=" alpha TAG ",
    )
    assert [item["role_id"] for item in intersected["items"]] == [alpha["role_id"]]
    assert intersected["items"][0]["continue_session_id"] == "alpha-tip"
    assert query_catalog_roles(rows=[beta], view="all", query="raw_only_never_searched")["total"] == 0

    many = [_row(f"agency:test/{index:02d}", name=f"Role {index:02d}", catalog_order=index) for index in range(49)]
    first = query_catalog_roles(rows=many, view="all", page=1)
    second = query_catalog_roles(rows=list(reversed(many)), view="all", page=2)
    third = query_catalog_roles(rows=many, view="all", page=3)
    assert (first["total"], first["pages"]) == (49, 3)
    assert [item["catalog_order"] for item in first["items"]] == list(range(24))
    assert [item["catalog_order"] for item in second["items"]] == list(range(24, 48))
    assert [item["catalog_order"] for item in third["items"]] == [48]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope": "unknown"},
        {"category": "not-a-category"},
        {"view": "unknown"},
        {"page": 0},
        {"query": "x" * 201},
    ],
)
def test_catalog_rejects_invalid_query_contract(kwargs):
    with pytest.raises(ValueError):
        query_catalog_roles(rows=[], **kwargs)


def test_removed_favorite_keeps_minimum_metadata_and_can_be_filtered():
    removed = _row(
        "agency:retired/role",
        name="Retired",
        category="文档与研究",
    )
    favorite = _favorite(removed)
    favorite_result = query_catalog_roles(
        rows=[],
        favorites={removed["role_id"]: favorite},
        scope="favorites",
        category="文档与研究",
        view="all",
        query="retired",
    )
    assert favorite_result["total"] == 1
    item = favorite_result["items"][0]
    assert item["available"] is False
    assert item["favorite"] is True
    assert item["historical"] is False
    assert item["continue_session_id"] is None
    assert "raw_source" not in item

    assert query_catalog_roles(
        rows=[],
        favorites={removed["role_id"]: favorite},
        scope="favorites",
        view="featured",
    )["total"] == 0

    recent = {
        removed["role_id"]: {
            **favorite,
            "last_accepted_at": 80.0,
            "continue_session_id": "retired-tip",
        }
    }
    recent_result = query_catalog_roles(
        rows=[],
        favorites={removed["role_id"]: favorite},
        recent=recent,
        scope="favorites",
        view="recent",
    )
    assert recent_result["items"][0]["historical"] is True
    assert recent_result["items"][0]["continue_session_id"] == "retired-tip"

    detail = removed_role_detail(
        removed["role_id"],
        favorite=favorite,
        recent=recent[removed["role_id"]],
    )
    assert detail["available"] is False
    assert detail["favorite"] is True
    assert detail["historical"] is True
    assert detail["continue_session_id"] == "retired-tip"
    assert "raw_source" not in detail
    assert "deliverable_examples" not in detail


def test_favorites_store_is_atomic_persistent_profile_scoped_and_concurrent(tmp_path, monkeypatch):
    first_store = ZhinangFavoritesStore(tmp_path)
    second_store = ZhinangFavoritesStore(tmp_path)
    alpha = _row("agency:test/alpha", name="Alpha")
    beta = _row("agency:test/beta", name="Beta")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda pair: pair[0].set_favorite(
                "default", pair[1]["role_id"], True, pair[1]
            ),
            ((first_store, alpha), (second_store, beta)),
        ))
    assert all(result["favorite"] is True for result in results)
    assert set(ZhinangFavoritesStore(tmp_path).list_favorites("default")) == {
        alpha["role_id"], beta["role_id"],
    }
    assert ZhinangFavoritesStore(tmp_path).list_favorites("research") == {}

    preferences = tmp_path / "zhinang" / "preferences.json"
    before = preferences.read_bytes()
    monkeypatch.setattr("api.zhinang.os.replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(RuntimeError, match="收藏状态保存失败"):
        first_store.set_favorite("default", alpha["role_id"], False)
    assert preferences.read_bytes() == before


def test_favorites_store_root_alias_and_removed_cancel_are_idempotent(tmp_path, monkeypatch):
    from api import profiles

    original = profiles._is_root_profile
    monkeypatch.setattr(
        profiles,
        "_is_root_profile",
        lambda value: value in {"default", "renamed-root"} or original(value),
    )
    store = ZhinangFavoritesStore(tmp_path)
    role = _row("agency:test/root", name="Root")
    store.set_favorite("renamed-root", role["role_id"], True, role)
    assert role["role_id"] in store.list_favorites("default")
    assert store.set_favorite("default", "agency:removed/no-longer-current", False) == {
        "role_id": "agency:removed/no-longer-current",
        "favorite": False,
    }
    assert store.set_favorite("default", role["role_id"], False)["favorite"] is False
    assert store.set_favorite("default", role["role_id"], False)["favorite"] is False


def test_recent_selection_separates_fuller_snapshot_from_executable_tip_and_falls_back():
    role_id = "agency:test/recent"
    snapshot = _recent_row(
        "compression-snapshot",
        role_id,
        100.0,
        pre_snapshot=True,
        tip_id="compression-tip",
    )
    tip = _recent_row("compression-tip", role_id, 110.0)
    branch = _recent_row("independent-branch", role_id, 90.0)
    duplicate = _recent_row("independent-duplicate", role_id, 80.0)
    by_id = {row["session_id"]: row for row in (tip, branch, duplicate)}

    selected = select_recent_roles(
        [snapshot, branch, duplicate],
        resolve_session=by_id.get,
    )
    assert selected[role_id]["continue_session_id"] == "compression-tip"
    assert selected[role_id]["last_accepted_at"] == 110.0

    del by_id["compression-tip"]
    fallback = select_recent_roles(
        [snapshot, branch, duplicate],
        resolve_session=by_id.get,
    )
    assert fallback[role_id]["continue_session_id"] == "independent-branch"
    del by_id["independent-branch"]
    assert select_recent_roles(
        [snapshot, duplicate],
        resolve_session=by_id.get,
    )[role_id]["continue_session_id"] == "independent-duplicate"
    del by_id["independent-duplicate"]
    assert select_recent_roles(
        [snapshot, duplicate],
        resolve_session=by_id.get,
    ) == {}

    invalid = _recent_row("invalid-tip", role_id, float("nan"))
    assert select_recent_roles(
        [invalid],
        resolve_session={"invalid-tip": invalid}.get,
    ) == {}


def test_recent_route_revalidates_lineage_tip_profile_role_snapshot_and_visibility(monkeypatch):
    from api import routes

    role_id = "agency:product/product-manager"
    display = _recent_row(
        "snapshot-display",
        role_id,
        100.0,
        pre_snapshot=True,
        tip_id="candidate-tip",
    )

    def candidate(
        *,
        profile: str = "default",
        candidate_role_id: str = role_id,
        pre_snapshot: bool = False,
    ) -> Session:
        return Session(
            session_id="candidate-tip",
            title="Candidate tip",
            profile=profile,
            pre_compression_snapshot=pre_snapshot,
            zhinang_role_snapshot=snapshot_role_from_catalog(candidate_role_id),
            zhinang_usage={
                "accepted_request_ids": ["accepted"],
                "first_accepted_at": 110.0,
                "last_accepted_at": 110.0,
            },
        )

    selected_candidate = candidate()
    visible = True
    monkeypatch.setattr(routes, "all_sessions", lambda: [display])
    monkeypatch.setattr(routes, "get_session", lambda *_args, **_kwargs: selected_candidate)
    monkeypatch.setattr(
        routes,
        "_expert_team_launch_session_is_public",
        lambda *_args, **_kwargs: visible,
    )

    selected = routes._zhinang_recent_roles_for_profile("default")
    assert selected[role_id]["continue_session_id"] == "candidate-tip"

    selected_candidate = candidate(profile="research")
    assert routes._zhinang_recent_roles_for_profile("default") == {}
    selected_candidate = candidate(candidate_role_id="agency:sales/sales-engineer")
    assert routes._zhinang_recent_roles_for_profile("default") == {}
    selected_candidate = candidate(pre_snapshot=True)
    assert routes._zhinang_recent_roles_for_profile("default") == {}
    selected_candidate = candidate()
    visible = False
    assert routes._zhinang_recent_roles_for_profile("default") == {}


def test_current_role_detail_has_complete_source_license_and_no_private_prompt():
    detail = current_role_detail("agency:sales/sales-engineer", favorite=True)

    assert detail["role_id"] == "agency:sales/sales-engineer"
    assert detail["historical"] is False
    assert detail["available"] is True
    assert detail["favorite"] is True
    assert detail["source_path"] == "sales/sales-engineer.md"
    assert detail["upstream_commit"] == "af128a92888fd7d7c389b6cb37f1820be1b3cd9d"
    assert detail["source_url"].endswith(
        "/blob/af128a92888fd7d7c389b6cb37f1820be1b3cd9d/sales/sales-engineer.md"
    )
    assert "Copyright (c) 2025 AgentLand Contributors" in detail["license"]
    assert "#" in detail["raw_source"]
    assert "effective_prompt" not in detail
    assert "private" not in detail


def test_catalog_detail_and_favorite_http_contract():
    role_id = "agency:sales/sales-engineer"
    encoded = quote(role_id, safe="")
    try:
        catalog, catalog_status = _request("GET", "/api/zhinang/catalog")
        detail, detail_status = _request("GET", f"/api/zhinang/roles/{encoded}")
        saved, saved_status = _request(
            "PUT",
            f"/api/zhinang/favorites/{encoded}",
            {"favorite": True},
        )
        favorites, favorites_status = _request(
            "GET",
            "/api/zhinang/catalog?scope=favorites&view=all",
        )
        other_profile, other_status = _request(
            "GET",
            "/api/zhinang/catalog?scope=favorites&view=all",
            profile="research",
        )

        assert (
            catalog_status
            == detail_status
            == saved_status
            == favorites_status
            == other_status
            == 200
        )
        assert catalog["total"] == 6
        assert detail["role"]["role_id"] == role_id
        assert saved == {"role_id": role_id, "favorite": True}
        assert any(item["role_id"] == role_id for item in favorites["items"])
        assert all(item["role_id"] != role_id for item in other_profile["items"])
    finally:
        _request("PUT", f"/api/zhinang/favorites/{encoded}", {"favorite": False})


def test_catalog_http_rejects_invalid_queries_paths_and_mutations_without_leaks():
    invalid_query, query_status = _request("GET", "/api/zhinang/catalog?page=0")
    traversal, traversal_status = _request(
        "GET",
        "/api/zhinang/roles/" + quote("../../api/config.py", safe=""),
    )
    missing_set, missing_set_status = _request(
        "PUT",
        "/api/zhinang/favorites/" + quote("agency:missing/role", safe=""),
        {"favorite": True},
    )
    bad_body, bad_body_status = _request(
        "PUT",
        "/api/zhinang/favorites/" + quote("agency:sales/sales-engineer", safe=""),
        {"favorite": "yes", "profile": "other"},
    )

    assert query_status == 400
    assert invalid_query["code"] == "zhinang_catalog_query_invalid"
    assert traversal_status == missing_set_status == 404
    assert bad_body_status == 400
    combined = repr((traversal, missing_set, bad_body))
    assert str(Path.cwd()) not in combined
    assert "effective_prompt" not in combined


def test_removed_favorite_http_keeps_minimum_detail_and_can_be_cancelled():
    removed = _row(
        "agency:retired/http-role",
        name="Retired HTTP",
        category="文档与研究",
    )
    encoded = quote(removed["role_id"], safe="")
    store = ZhinangFavoritesStore(TEST_STATE_DIR)
    store.set_favorite("default", removed["role_id"], True, removed)

    detail, detail_status = _request("GET", f"/api/zhinang/roles/{encoded}")
    cancelled, cancelled_status = _request(
        "PUT",
        f"/api/zhinang/favorites/{encoded}",
        {"favorite": False},
    )
    missing, missing_status = _request("GET", f"/api/zhinang/roles/{encoded}")

    assert detail_status == cancelled_status == 200
    assert detail["role"]["available"] is False
    assert detail["role"]["favorite"] is True
    assert "raw_source" not in detail["role"]
    assert cancelled == {"role_id": removed["role_id"], "favorite": False}
    assert missing_status == 404
    assert "role" not in missing


def test_recent_http_uses_only_accepted_visible_task_and_profile(cleanup_test_sessions):
    role_id = "agency:product/product-manager"
    sid = "zhinang-library-recent-http"
    session = Session(
        session_id=sid,
        title="Recent role task",
        profile="default",
        messages=[{"role": "user", "content": "accepted"}],
        zhinang_role_snapshot=snapshot_role_from_catalog(role_id),
        zhinang_usage={
            "accepted_request_ids": ["accepted-turn"],
            "first_accepted_at": 100.0,
            "last_accepted_at": 120.0,
        },
    )
    session.save()
    cleanup_test_sessions.append(sid)

    recent, recent_status = _request("GET", "/api/zhinang/catalog?view=recent")
    cross_profile, cross_status = _request(
        "GET",
        "/api/zhinang/catalog?view=recent",
        profile="research",
    )

    assert recent_status == cross_status == 200
    item = next(entry for entry in recent["items"] if entry["role_id"] == role_id)
    assert item["last_accepted_at"] == 120.0
    assert item["continue_session_id"] == sid
    assert all(entry["role_id"] != role_id for entry in cross_profile["items"])
