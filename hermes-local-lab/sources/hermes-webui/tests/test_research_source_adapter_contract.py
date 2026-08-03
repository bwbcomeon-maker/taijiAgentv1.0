import asyncio
import importlib
import importlib.util
import json
import ssl

import pytest


def _module():
    return importlib.import_module("api.expert_teams.research_sources")


def _run(awaitable):
    return asyncio.run(awaitable)


def test_research_source_contract_module_exists():
    assert importlib.util.find_spec("api.expert_teams.research_sources") is not None


class _FakeAdapter:
    def __init__(self, module, result=None, *, available=True, reason_code=""):
        self.module = module
        self.result = result
        self.available = available
        self.reason_code = reason_code
        self.calls = []

    async def probe(self):
        return self.module.ProbeResult(
            available=self.available,
            reason_code=self.reason_code,
            safe_reason="当前资料源不可用" if not self.available else "",
        )

    async def search(self, query, *, limit=5):
        self.calls.append((query, limit))
        return self.result

    async def materialize(self, hit, *, workspace, run_id):
        return {"source_id": hit.content_sha256[:12], "kind": hit.source_kind}


def _hit(module, kind, title, locator, content):
    return module.ResearchSourceHit.create(
        source_kind=kind,
        safe_title=title,
        locator=locator,
        content=content,
        retrieved_at="2026-08-03T10:00:00+00:00",
    )


def _result(module, hits=(), *, coverage="none", status="empty", reason_code="no_results"):
    return module.AdapterSearchResult(
        hits=tuple(hits),
        coverage=coverage,
        status=status,
        reason_code=reason_code,
        safe_reason="未找到可核验资料" if reason_code else "",
    )


def test_web_sufficient_skips_local_and_model():
    module = _module()
    web_hits = (
        _hit(module, "approved_public", "A", "https://a.example/x", "alpha evidence"),
        _hit(module, "approved_public", "B", "https://b.example/y", "beta evidence"),
    )
    web = _FakeAdapter(module, _result(module, web_hits, coverage="sufficient", status="success", reason_code=""))
    local = _FakeAdapter(module, _result(module))

    outcome = _run(module.orchestrate_research_sources("alpha", web_adapter=web, local_adapter=local))

    assert (outcome.public_count, outcome.local_count, outcome.model_count) == (2, 0, 0)
    assert outcome.coverage == "sufficient"
    assert outcome.status == "ready"
    assert local.calls == []


def test_partial_but_sufficient_web_retains_hits_and_skips_local():
    module = _module()
    hit = _hit(module, "approved_public", "A", "https://a.example/x", "alpha evidence")
    web = _FakeAdapter(
        module,
        _result(module, (hit,), coverage="sufficient", status="partial", reason_code="partial_success"),
    )
    local = _FakeAdapter(module, _result(module))

    outcome = _run(module.orchestrate_research_sources("alpha", web_adapter=web, local_adapter=local))

    assert outcome.public_count == 1
    assert outcome.reason_code == "partial_success"
    assert outcome.model_count == 0
    assert local.calls == []


def test_partial_insufficient_web_adds_local_sources():
    module = _module()
    public = _hit(module, "approved_public", "A", "https://a.example/x", "public evidence")
    internal = _hit(module, "approved_internal", "Memo", "local://0/memo.md", "internal evidence")
    web = _FakeAdapter(
        module,
        _result(module, (public,), coverage="insufficient", status="partial", reason_code="partial_success"),
    )
    local = _FakeAdapter(
        module,
        _result(module, (internal,), coverage="sufficient", status="success", reason_code=""),
    )

    outcome = _run(module.orchestrate_research_sources("evidence", web_adapter=web, local_adapter=local))

    assert (outcome.public_count, outcome.local_count, outcome.model_count) == (1, 1, 0)
    assert outcome.coverage == "sufficient"
    assert len(local.calls) == 1


def test_total_web_failure_can_be_recovered_by_local_sources():
    module = _module()
    internal = _hit(module, "approved_internal", "Memo", "local://0/memo.md", "internal evidence")
    web = _FakeAdapter(
        module,
        _result(module, coverage="none", status="failed", reason_code="network_unreachable"),
    )
    local = _FakeAdapter(
        module,
        _result(module, (internal,), coverage="sufficient", status="success", reason_code=""),
    )

    outcome = _run(module.orchestrate_research_sources("evidence", web_adapter=web, local_adapter=local))

    assert (outcome.public_count, outcome.local_count, outcome.model_count) == (0, 1, 0)
    assert outcome.coverage == "sufficient"


@pytest.mark.parametrize("local_available", [True, False])
def test_empty_or_unavailable_local_falls_back_to_unattributed_model_knowledge(local_available):
    module = _module()
    web = _FakeAdapter(module, _result(module))
    local = _FakeAdapter(
        module,
        _result(module),
        available=local_available,
        reason_code="network_unreachable" if not local_available else "",
    )

    outcome = _run(module.orchestrate_research_sources("unknown", web_adapter=web, local_adapter=local))

    assert (outcome.public_count, outcome.local_count, outcome.model_count) == (0, 0, 1)
    assert outcome.status == "model_fallback"
    assert outcome.coverage == "none"
    assert outcome.hits == ()


def test_web_adapter_deduplicates_urls_rejects_non_http_and_disables_llm_processing():
    module = _module()
    extract_calls = []

    def search(_query, limit=5):
        return json.dumps(
            {
                "success": True,
                "data": {
                    "web": [
                        {"title": " A\nTitle ", "url": "https://EXAMPLE.com/a#one"},
                        {"title": "duplicate", "url": "https://example.com/a#two"},
                        {"title": "bad", "url": "file:///etc/passwd"},
                    ]
                },
            }
        )

    async def extract(urls, **kwargs):
        extract_calls.append((urls, kwargs))
        return json.dumps({"results": [{"url": urls[0], "title": " A\nTitle ", "content": "source body"}]})

    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=search,
        extract_callable=extract,
        minimum_usable_hits=1,
        clock=lambda: "2026-08-03T10:00:00+00:00",
    )
    result = _run(adapter.search("topic", limit=5))

    assert len(result.hits) == 1
    assert result.hits[0].safe_title == "A Title"
    assert result.hits[0].locator == "https://example.com/a"
    assert extract_calls == [(["https://example.com/a"], {"format": "markdown", "use_llm_processing": False})]


def test_web_page_failures_retain_successes_and_report_partial_success():
    module = _module()

    def search(_query, limit=5):
        return {
            "success": True,
            "data": {
                "web": [
                    {"title": "A", "url": "https://a.example/"},
                    {"title": "B", "url": "https://b.example/"},
                ]
            },
        }

    async def extract(urls, **_kwargs):
        if "b.example" in urls[0]:
            return {"results": [{"url": urls[0], "error": "timed out", "content": ""}]}
        return {"results": [{"url": urls[0], "title": "A", "content": "usable body"}]}

    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=search,
        extract_callable=extract,
        minimum_usable_hits=1,
    )
    result = _run(adapter.search("topic", limit=5))

    assert len(result.hits) == 1
    assert result.status == "partial"
    assert result.reason_code == "partial_success"
    assert result.coverage == "sufficient"


def test_web_extract_timeout_is_not_flattened_into_generic_fetch_failure():
    module = _module()
    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=lambda *_a, **_k: {
            "success": True,
            "data": {"web": [{"title": "A", "url": "https://a.example/"}]},
        },
        extract_callable=lambda *_a, **_k: {"results": [{"error": "request timed out"}]},
    )

    result = _run(adapter.search("topic"))

    assert result.reason_code == "timeout"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (TimeoutError("slow"), "timeout"),
        (ssl.SSLError("certificate verify failed"), "tls_error"),
        (ConnectionError("dns unavailable"), "network_unreachable"),
        (RuntimeError("provider exploded"), "search_service_error"),
    ],
)
def test_web_search_exception_classification(failure, expected):
    module = _module()

    def search(_query, limit=5):
        raise failure

    adapter = module.HermesWebResearchSourceAdapter(search_callable=search, extract_callable=lambda *_a, **_k: None)
    result = _run(adapter.search("topic"))
    assert result.reason_code == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"success": False, "error": "Blocked by website policy"}, "policy_blocked"),
        ({"success": False, "error": "request timed out"}, "timeout"),
        ({"success": False, "error": "TLS certificate error"}, "tls_error"),
        ({"success": True, "data": {"web": []}}, "no_results"),
    ],
)
def test_web_search_response_classification(payload, expected):
    module = _module()
    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=lambda *_a, **_k: payload,
        extract_callable=lambda *_a, **_k: None,
    )
    result = _run(adapter.search("topic"))
    assert result.reason_code == expected


def test_local_adapter_only_reads_explicit_text_roots_and_rejects_symlink_escape(tmp_path):
    module = _module()
    workspace = tmp_path / "workspace"
    allowed = workspace / "approved"
    outside = tmp_path / "outside"
    allowed.mkdir(parents=True)
    outside.mkdir()
    (allowed / "memo.md").write_text("alpha evidence", encoding="utf-8")
    (allowed / "binary.pdf").write_bytes(b"alpha evidence")
    (outside / "secret.txt").write_text("alpha secret", encoding="utf-8")
    (allowed / "escape.txt").symlink_to(outside / "secret.txt")

    adapter = module.LocalTextResearchSourceAdapter(roots=[allowed], workspace_root=workspace)
    result = _run(adapter.search("alpha"))

    assert [hit.safe_title for hit in result.hits] == ["memo.md"]
    assert result.hits[0].source_kind == "approved_internal"


def test_local_adapter_without_roots_or_with_broad_workspace_root_is_unavailable(tmp_path):
    module = _module()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert _run(module.LocalTextResearchSourceAdapter(roots=[]).probe()).available is False
    assert _run(
        module.LocalTextResearchSourceAdapter(roots=[workspace], workspace_root=workspace).probe()
    ).available is False


def test_materialized_approved_sources_are_registry_trusted_idempotent_and_not_client_forgeable(tmp_path):
    module = _module()
    from api.expert_teams.source_registry import SourceRegistryError, resolve_source_registry

    hit = _hit(module, "approved_public", "Evidence", "https://example.com/report", "verified body")
    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)
    first = _run(adapter.materialize(hit, workspace=tmp_path, run_id="run-1"))
    second = _run(adapter.materialize(hit, workspace=tmp_path, run_id="run-1"))

    assert first == second
    refs, registry = resolve_source_registry(tmp_path, "run-1", [first])
    assert refs[0]["kind"] == "approved_public"
    assert registry[first["source_id"]]["status"] == "ready"

    with pytest.raises(SourceRegistryError):
        resolve_source_registry(
            tmp_path,
            "run-1",
            [{"source_id": "PUB-fake", "kind": "approved_public", "text": "fake", "locator": "https://evil.test"}],
        )
    with pytest.raises(SourceRegistryError):
        resolve_source_registry(
            tmp_path,
            "run-1",
            [{"source_id": "PUB-fake", "kind": "approved_public", "locator": "https://evil.test"}],
        )


def test_same_content_hash_deduplicates_materialized_sources(tmp_path):
    module = _module()
    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)
    first_hit = _hit(module, "approved_public", "A", "https://a.example", "same body")
    second_hit = _hit(module, "approved_public", "B", "https://b.example", "same body")

    first = _run(adapter.materialize(first_hit, workspace=tmp_path, run_id="run-1"))
    second = _run(adapter.materialize(second_hit, workspace=tmp_path, run_id="run-1"))

    assert first["source_id"] == second["source_id"]
    assert first["locator"] == second["locator"]


def test_source_snapshot_preserves_server_manifest_origin_and_retrieval_time(tmp_path):
    module = _module()
    from api.expert_teams.source_context import build_source_context_snapshot
    from api.expert_teams.source_registry import resolve_source_registry

    hit = _hit(
        module,
        "approved_public",
        "Evidence",
        "https://example.com/report",
        "verified body",
    )
    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)
    raw_ref = _run(adapter.materialize(hit, workspace=tmp_path, run_id="run-1"))
    raw_ref["origin_locator"] = "https://evil.test/forged"
    raw_ref["retrieved_at"] = "1900-01-01T00:00:00+00:00"

    refs, registry = resolve_source_registry(tmp_path, "run-1", [raw_ref])
    assert refs[0]["origin_locator"] == "https://example.com/report"
    assert refs[0]["retrieved_at"] == "2026-08-03T10:00:00+00:00"
    assert registry[refs[0]["source_id"]]["origin_locator"] == "https://example.com/report"

    brief = {"source_policy": {"source_refs": refs}}
    snapshot_ref = build_source_context_snapshot(
        tmp_path,
        "run-1",
        brief,
        registry,
        brief_sha256="b" * 64,
        brief_revision=1,
    )
    payload = json.loads((tmp_path / snapshot_ref["relative_path"]).read_text(encoding="utf-8"))
    source = payload["sources"][0]
    assert source["origin_locator"] == "https://example.com/report"
    assert source["retrieved_at"] == "2026-08-03T10:00:00+00:00"
    assert source["locator"].startswith(".taiji/expert-teams/sources/")
    assert source["segments"][0]["locator"].startswith("https://example.com/report#chars=")
