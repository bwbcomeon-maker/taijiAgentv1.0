import asyncio
import importlib
import importlib.util
import json
import os
import ssl
import subprocess
import sys
import time
from pathlib import Path

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

    async def search(self, query, *, limit=5, policy_decision=None):
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


def _allow_query(module):
    return lambda query: module.QueryAuthorizationDecision(True, query, "test-public-v1", "public-web")


def _coverage_when(module, predicate):
    return lambda subquestions, hits: module.CoverageEvaluation(
        "sufficient" if predicate(hits) else ("insufficient" if hits else "none"),
        subquestions if predicate(hits) else (),
    )


def test_web_sufficient_skips_local_and_model():
    module = _module()
    web_hits = (
        _hit(module, "approved_public", "A", "https://a.example/x", "alpha evidence"),
        _hit(module, "approved_public", "B", "https://b.example/y", "beta evidence"),
    )
    web = _FakeAdapter(module, _result(module, web_hits, coverage="sufficient", status="success", reason_code=""))
    local = _FakeAdapter(module, _result(module))

    outcome = _run(
        module.orchestrate_research_sources(
            "alpha",
            web_adapter=web,
            local_adapter=local,
            query_authorizer=_allow_query(module),
            research_subquestions=("test-scope",),
            coverage_evaluator=_coverage_when(module, lambda hits: len(hits) >= 2),
        )
    )

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

    outcome = _run(
        module.orchestrate_research_sources(
            "alpha",
            web_adapter=web,
            local_adapter=local,
            query_authorizer=_allow_query(module),
            research_subquestions=("test-scope",),
            coverage_evaluator=_coverage_when(module, lambda hits: len(hits) >= 1),
        )
    )

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

    outcome = _run(
        module.orchestrate_research_sources(
            "evidence",
            web_adapter=web,
            local_adapter=local,
            query_authorizer=_allow_query(module),
            research_subquestions=("test-scope",),
            coverage_evaluator=_coverage_when(module, lambda hits: len(hits) >= 2),
        )
    )

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

    outcome = _run(
        module.orchestrate_research_sources(
            "evidence",
            web_adapter=web,
            local_adapter=local,
            query_authorizer=_allow_query(module),
            research_subquestions=("test-scope",),
            coverage_evaluator=_coverage_when(
                module,
                lambda hits: any(hit.source_kind == "approved_internal" for hit in hits),
            ),
        )
    )

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

    outcome = _run(
        module.orchestrate_research_sources(
            "unknown",
            web_adapter=web,
            local_adapter=local,
            query_authorizer=_allow_query(module),
        )
    )

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
        query_authorizer=_allow_query(module),
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
        query_authorizer=_allow_query(module),
    )
    result = _run(adapter.search("topic", limit=5))

    assert len(result.hits) == 1
    assert result.status == "partial"
    assert result.reason_code == "partial_success"
    assert result.coverage == "unassessed"


def test_web_extract_timeout_is_not_flattened_into_generic_fetch_failure():
    module = _module()
    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=lambda *_a, **_k: {
            "success": True,
            "data": {"web": [{"title": "A", "url": "https://a.example/"}]},
        },
        extract_callable=lambda *_a, **_k: {"results": [{"error": "request timed out"}]},
        query_authorizer=_allow_query(module),
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

    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=search,
        extract_callable=lambda *_a, **_k: None,
        query_authorizer=_allow_query(module),
    )
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
        query_authorizer=_allow_query(module),
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


def test_same_content_different_origins_keep_distinct_materialized_source_identity(tmp_path):
    module = _module()
    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)
    first_hit = _hit(module, "approved_public", "A", "https://a.example", "same body")
    second_hit = _hit(module, "approved_public", "B", "https://b.example", "same body")

    first = _run(adapter.materialize(first_hit, workspace=tmp_path, run_id="run-1"))
    second = _run(adapter.materialize(second_hit, workspace=tmp_path, run_id="run-1"))

    assert first["source_id"] != second["source_id"]
    assert first["locator"] != second["locator"]


def test_same_origin_and_content_is_idempotent_under_concurrent_materialization(tmp_path):
    module = _module()
    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)
    first_hit = _hit(module, "approved_public", "First title", "https://EXAMPLE.com:443/a#fragment", "same body")
    second_hit = _hit(module, "approved_public", "Later title", "https://example.com/a", "same body")

    async def materialize_both():
        return await asyncio.gather(
            adapter.materialize(first_hit, workspace=tmp_path, run_id="run-1"),
            adapter.materialize(second_hit, workspace=tmp_path, run_id="run-1"),
        )

    first, second = _run(materialize_both())

    assert first == second
    assert first["source_id"].startswith("PUB-")


def test_materialized_identity_is_independent_of_insertion_order(tmp_path):
    module = _module()
    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)
    hits = (
        _hit(module, "approved_public", "A", "https://a.example/report", "same body"),
        _hit(module, "approved_public", "B", "https://b.example/report", "same body"),
    )
    (tmp_path / "forward").mkdir()
    (tmp_path / "reverse").mkdir()

    forward = {
        hit.locator: _run(adapter.materialize(hit, workspace=tmp_path / "forward", run_id="run-1"))["source_id"]
        for hit in hits
    }
    reverse = {
        hit.locator: _run(adapter.materialize(hit, workspace=tmp_path / "reverse", run_id="run-1"))["source_id"]
        for hit in reversed(hits)
    }

    assert forward == reverse
    assert len(set(forward.values())) == 2


def test_materialization_rejects_symlinked_taiji_storage_before_writing(tmp_path):
    module = _module()
    from api.expert_teams.source_registry import SourceRegistryError

    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / ".taiji").symlink_to(outside, target_is_directory=True)
    hit = _hit(module, "approved_public", "A", "https://a.example/report", "body")
    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)

    with pytest.raises(SourceRegistryError):
        _run(adapter.materialize(hit, workspace=workspace, run_id="run-1"))

    assert list(outside.rglob("*")) == []


def test_fifo_manifest_is_rejected_with_bounded_time_without_hanging_pytest(tmp_path):
    module = _module()
    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)
    hit = _hit(module, "approved_public", "A", "https://a.example/report", "body")
    ref = _run(adapter.materialize(hit, workspace=tmp_path, run_id="run-1"))
    manifest = (tmp_path / ref["locator"]).with_suffix(".source.json")
    manifest.chmod(0o600)
    manifest.unlink()
    os.mkfifo(manifest)

    webui_root = Path(__file__).resolve().parents[1]
    agent_root = webui_root.parent / "hermes-agent"
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(filter(None, (str(webui_root), str(agent_root), existing_pythonpath)))
    script = """
import sys
from pathlib import Path
from api.expert_teams.source_registry import materialize_approved_source

try:
    materialize_approved_source(
        Path(sys.argv[1]),
        "run-1",
        kind="approved_public",
        label="A",
        origin_locator="https://a.example/report",
        content="body",
        retrieved_at="2026-08-03T10:00:00+00:00",
        content_sha256="230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5",
    )
except Exception as exc:
    print(type(exc).__name__)
else:
    print("unexpected-success")
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            cwd=webui_root,
            env={
                **os.environ,
                "HERMES_WEBUI_AGENT_DIR": str(agent_root),
                "PYTHONPATH": pythonpath,
            },
            capture_output=True,
            text=True,
            timeout=0.75,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("FIFO 清单读取超时，回归路径仍会阻塞")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "SourceRegistryError"


def test_manifest_publish_failure_rolls_back_only_newly_created_body(tmp_path):
    module = _module()
    from api.expert_teams.source_registry import SourceRegistryError

    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)
    hit = _hit(module, "approved_public", "A", "https://a.example/report", "body")
    ref = _run(adapter.materialize(hit, workspace=tmp_path, run_id="run-1"))
    body = tmp_path / ref["locator"]
    manifest = body.with_suffix(".source.json")
    body.chmod(0o600)
    body.unlink()
    manifest.chmod(0o600)
    manifest.unlink()
    manifest.mkdir()

    with pytest.raises(SourceRegistryError):
        _run(adapter.materialize(hit, workspace=tmp_path, run_id="run-1"))

    assert body.exists() is False
    assert manifest.is_dir()


def test_manifest_publish_failure_never_deletes_preexisting_body(tmp_path):
    module = _module()
    from api.expert_teams.source_registry import SourceRegistryError

    adapter = module.HermesWebResearchSourceAdapter(search_callable=lambda *_a, **_k: {}, extract_callable=None)
    hit = _hit(module, "approved_public", "A", "https://a.example/report", "body")
    ref = _run(adapter.materialize(hit, workspace=tmp_path, run_id="run-1"))
    body = tmp_path / ref["locator"]
    manifest = body.with_suffix(".source.json")
    manifest.chmod(0o600)
    manifest.unlink()
    manifest.mkdir()

    with pytest.raises(SourceRegistryError):
        _run(adapter.materialize(hit, workspace=tmp_path, run_id="run-1"))

    assert body.read_bytes() == b"body"


def test_sync_web_search_runs_off_event_loop_thread():
    module = _module()
    events = []

    def slow_search(_query, **_kwargs):
        time.sleep(0.05)
        events.append("search_done")
        return {"success": True, "data": {"web": []}}

    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=slow_search,
        extract_callable=lambda *_a, **_k: None,
        query_authorizer=_allow_query(module),
    )

    async def scenario():
        task = asyncio.create_task(adapter.search("topic"))
        await asyncio.sleep(0.005)
        events.append("tick")
        await task

    _run(scenario())
    assert events[0] == "tick"


def test_local_scan_and_read_run_off_event_loop_thread(tmp_path, monkeypatch):
    module = _module()
    allowed = tmp_path / "approved"
    allowed.mkdir()
    (allowed / "memo.md").write_text("alpha evidence", encoding="utf-8")
    original_read_bytes = module.Path.read_bytes
    events = []

    def slow_read(path):
        time.sleep(0.05)
        events.append("read_done")
        return original_read_bytes(path)

    monkeypatch.setattr(module.Path, "read_bytes", slow_read)
    adapter = module.LocalTextResearchSourceAdapter(roots=[allowed])

    async def scenario():
        task = asyncio.create_task(adapter.search("alpha"))
        await asyncio.sleep(0.005)
        events.append("tick")
        await task

    _run(scenario())
    assert events[0] == "tick"


def test_local_scan_honors_entry_limit_without_pre_enumerating_tree(tmp_path, monkeypatch):
    module = _module()
    allowed = tmp_path / "approved"
    allowed.mkdir()
    for index in range(20):
        (allowed / f"{index:02d}.md").write_text("alpha evidence", encoding="utf-8")

    def forbidden_rglob(*_args, **_kwargs):
        raise AssertionError("local scan must not pre-enumerate the entire tree")

    monkeypatch.setattr(module.Path, "rglob", forbidden_rglob)
    adapter = module.LocalTextResearchSourceAdapter(roots=[allowed], max_files_scanned=2, max_results=20)
    result = _run(adapter.search("alpha", limit=20))

    assert len(result.hits) <= 2


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


def test_web_egress_is_fail_closed_without_authorizer_and_never_calls_search():
    module = _module()
    observed = []
    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=lambda query, **_kwargs: observed.append(query) or {"success": True, "data": {"web": []}},
        extract_callable=lambda *_a, **_k: None,
    )

    result = _run(adapter.search("secret customer acquisition plan"))

    assert observed == []
    assert result.reason_code == "data_egress_not_authorized"


def test_orchestrator_only_sends_authorized_safe_query_and_records_safe_policy_metadata():
    module = _module()
    searched = []
    raw_query = "研究客户 secret-token-123 的市场"

    def authorize(value):
        assert value == raw_query
        return module.QueryAuthorizationDecision(
            authorized=True,
            safe_query="研究目标客户市场",
            policy_id="research-public-v1",
            trust_zone="public-web",
        )

    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=lambda query, **_kwargs: searched.append(query) or {"success": True, "data": {"web": []}},
        extract_callable=lambda *_a, **_k: None,
    )
    local = _FakeAdapter(module, _result(module))
    outcome = _run(
        module.orchestrate_research_sources(
            raw_query,
            web_adapter=adapter,
            local_adapter=local,
            query_authorizer=authorize,
        )
    )

    assert searched == ["研究目标客户市场"]
    assert outcome.public_status["policy_id"] == "research-public-v1"
    assert outcome.public_status["trust_zone"] == "public-web"
    assert "secret-token-123" not in json.dumps(outcome.tier_decisions, ensure_ascii=False)


def test_two_unrelated_web_pages_are_not_sufficient_without_positive_coverage_evaluation():
    module = _module()
    hits = (
        _hit(module, "approved_public", "Weather", "https://a.example", "today is sunny"),
        _hit(module, "approved_public", "Sports", "https://b.example", "team won match"),
    )
    web = _FakeAdapter(module, _result(module, hits, coverage="sufficient", status="success", reason_code=""))
    local = _FakeAdapter(module, _result(module), available=False, reason_code="no_results")
    authorize = lambda query: module.QueryAuthorizationDecision(True, query, "public-v1", "public-web")

    outcome = _run(
        module.orchestrate_research_sources(
            "AI 办公落地与风险",
            web_adapter=web,
            local_adapter=local,
            query_authorizer=authorize,
        )
    )

    assert outcome.coverage == "insufficient"
    assert outcome.model_count == 1


def test_explicit_subquestion_coverage_evaluator_can_mark_relevant_sources_sufficient():
    module = _module()
    hits = (
        _hit(module, "approved_public", "Adoption", "https://a.example", "AI office adoption cases"),
        _hit(module, "approved_public", "Risks", "https://b.example", "AI office security risks"),
    )
    web = _FakeAdapter(module, _result(module, hits, coverage="insufficient", status="success", reason_code=""))
    local = _FakeAdapter(module, _result(module))
    authorize = lambda query: module.QueryAuthorizationDecision(True, query, "public-v1", "public-web")

    def evaluate(subquestions, candidate_hits):
        contents = " ".join(hit.content for hit in candidate_hits)
        covered = tuple(item for item in subquestions if item in contents)
        return module.CoverageEvaluation("sufficient", covered)

    outcome = _run(
        module.orchestrate_research_sources(
            "AI office adoption and risks",
            web_adapter=web,
            local_adapter=local,
            query_authorizer=authorize,
            research_subquestions=("adoption", "risks"),
            coverage_evaluator=evaluate,
        )
    )

    assert outcome.coverage == "sufficient"
    assert outcome.model_count == 0
    assert local.calls == []


def test_evaluator_self_report_cannot_bypass_missing_subquestion_coverage():
    module = _module()
    hit = _hit(module, "approved_public", "Adoption", "https://a.example", "AI office adoption cases")
    web = _FakeAdapter(module, _result(module, (hit,), status="success", reason_code=""))
    local = _FakeAdapter(module, _result(module), available=False, reason_code="no_results")

    outcome = _run(
        module.orchestrate_research_sources(
            "AI office adoption and risks",
            web_adapter=web,
            local_adapter=local,
            query_authorizer=_allow_query(module),
            research_subquestions=("adoption", "risks"),
            coverage_evaluator=lambda _required, _hits: module.CoverageEvaluation(
                "sufficient",
                ("adoption",),
            ),
        )
    )

    assert outcome.coverage == "insufficient"
    assert outcome.model_count == 1


def test_layered_status_preserves_public_and_local_failures_for_recovery():
    module = _module()
    web = _FakeAdapter(
        module,
        _result(module, coverage="none", status="failed", reason_code="network_unreachable"),
    )
    local = _FakeAdapter(module, available=False, reason_code="no_results")
    authorize = lambda query: module.QueryAuthorizationDecision(True, query, "public-v1", "public-web")

    outcome = _run(
        module.orchestrate_research_sources(
            "topic",
            web_adapter=web,
            local_adapter=local,
            query_authorizer=authorize,
        )
    )

    assert outcome.public_status == {
        "status": "failed",
        "reason": "network_unreachable",
        "safe_reason": "未找到可核验资料",
        "count": 0,
        "coverage": "none",
        "policy_id": "public-v1",
        "trust_zone": "public-web",
    }
    assert outcome.local_status["status"] == "unavailable"
    assert outcome.local_status["reason"] == "no_results"
    assert [decision["tier"] for decision in outcome.tier_decisions] == ["public", "local", "model"]
    assert outcome.status == "model_fallback"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("Blocked by website policy", "policy_blocked"),
        ("request timed out", "timeout"),
        ("TLS certificate failed", "tls_error"),
        ("extract provider failed", "search_service_error"),
    ],
)
def test_web_extract_top_level_error_is_classified_from_actual_response(error, expected):
    module = _module()
    authorize = lambda query: module.QueryAuthorizationDecision(True, query, "public-v1", "public-web")
    adapter = module.HermesWebResearchSourceAdapter(
        search_callable=lambda *_a, **_k: {
            "success": True,
            "data": {"web": [{"title": "A", "url": "https://a.example/"}]},
        },
        extract_callable=lambda *_a, **_k: {"success": False, "error": error},
        query_authorizer=authorize,
    )

    result = _run(adapter.search("topic"))

    assert result.reason_code == expected
