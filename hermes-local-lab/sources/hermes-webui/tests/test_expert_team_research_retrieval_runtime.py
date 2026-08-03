import copy
import hashlib
import json
import threading

import pytest


def _research_run(
    expert_teams,
    tmp_path,
    *,
    core_question="本地优先 AI 助理如何落地",
    subquestions=None,
    original_request=None,
):
    from api.expert_teams import runtime

    run = expert_teams.build_standalone_expert_team_run(
        {
            "launch_profile_id": "research-report",
            "session_id": "research-runtime",
            "prompt": original_request or core_question,
            "idempotency_key": "research-runtime-start",
        },
        run_id="et-research-runtime",
    )
    run = expert_teams.bind_initial_standalone_source_context(tmp_path, run)
    artifact = {
        "artifact_id": "ART-DIRECTION-1",
        "sha256": "a" * 64,
        "stage_id": "direction",
        "artifact_type": "research_charter",
        "stage_attempt": 1,
        "payload": {
            "core_question": core_question,
            "subquestions": list(subquestions or ["部署成本如何"]),
        },
        "validation_status": "valid",
        "blocking_issues": [],
    }
    run.update(
        {
            "current_stage_index": 1,
            "workflow_state": "ready_to_generate",
            "stage_artifacts": [artifact],
            "stage_outputs": [
                {"task_id": "direction", "status": "confirmed", "artifact": copy.deepcopy(artifact)}
            ],
            "approved_stage_artifact_refs": {
                "direction": {"artifact_id": artifact["artifact_id"], "sha256": artifact["sha256"]}
            },
            "local_stage_confirmations": [
                {
                    "stage_id": "direction",
                    "artifact_id": artifact["artifact_id"],
                    "artifact_sha256": artifact["sha256"],
                }
            ],
        }
    )
    return runtime._sync_derived(run)


class _Web:
    def __init__(self):
        self.search_calls = 0
        self.queries = []

    async def probe(self):
        from api.expert_teams.research_sources import ProbeResult

        return ProbeResult(True)

    async def search(self, query, *, limit=5, policy_decision=None):
        from api.expert_teams.research_sources import AdapterSearchResult, ResearchSourceHit

        self.search_calls += 1
        self.queries.append(query)
        assert query == "本地优先 AI 助理 如何落地"
        assert policy_decision.policy_id == "research-public-query/v1"
        hit = ResearchSourceHit.create(
            source_kind="approved_public",
            safe_title="权威资料",
            locator="https://example.test/report",
            content="部署成本如何：软硬件和运维需分项核算。",
            retrieved_at="2026-08-03T10:00:00+00:00",
        )
        return AdapterSearchResult(hits=(hit,), coverage="unassessed", status="success", reason_code="", safe_reason="")

    async def materialize(self, hit, *, workspace, run_id):
        from api.expert_teams.source_registry import materialize_approved_source

        return materialize_approved_source(
            workspace,
            run_id,
            kind=hit.source_kind,
            label=hit.safe_title,
            origin_locator=hit.locator,
            content=hit.content,
            retrieved_at=hit.retrieved_at,
            content_sha256=hit.content_sha256,
        )


class _LocalUnavailable:
    async def probe(self):
        from api.expert_teams.research_sources import ProbeResult

        return ProbeResult(False, "no_results", "未配置本地资料")


class _CountingLocal:
    def __init__(self):
        self.probe_calls = 0
        self.search_calls = 0

    async def probe(self):
        from api.expert_teams.research_sources import ProbeResult

        self.probe_calls += 1
        return ProbeResult(True)

    async def search(self, query, *, limit=5, policy_decision=None):
        from api.expert_teams.research_sources import AdapterSearchResult

        self.search_calls += 1
        return AdapterSearchResult(status="empty", reason_code="no_results", safe_reason="无本地结果")


class _WebUnavailable:
    def __init__(self):
        self.search_calls = 0

    async def probe(self):
        from api.expert_teams.research_sources import ProbeResult

        return ProbeResult(False, "network_unreachable", "网络不可达")

    async def search(self, *args, **kwargs):
        self.search_calls += 1
        raise AssertionError("unavailable public adapter must not search")


class _LocalHit:
    async def probe(self):
        from api.expert_teams.research_sources import ProbeResult

        return ProbeResult(True)

    async def search(self, query, *, limit=5, policy_decision=None):
        from api.expert_teams.research_sources import AdapterSearchResult, ResearchSourceHit

        hit = ResearchSourceHit.create(
            source_kind="approved_internal",
            safe_title="内部成本台账",
            locator="local://0/cost.md",
            content="部署成本如何：需统计设备、许可与运维费用。",
            retrieved_at="2026-08-03T10:00:00+00:00",
        )
        return AdapterSearchResult(hits=(hit,), coverage="unassessed", status="success", reason_code="", safe_reason="")

    async def materialize(self, hit, *, workspace, run_id):
        from api.expert_teams.source_registry import materialize_approved_source

        return materialize_approved_source(
            workspace,
            run_id,
            kind=hit.source_kind,
            label=hit.safe_title,
            origin_locator=hit.locator,
            content=hit.content,
            retrieved_at=hit.retrieved_at,
            content_sha256=hit.content_sha256,
        )


def _memory_storage(monkeypatch, runtime, initial):
    cell = {"run": copy.deepcopy(initial)}
    monkeypatch.setattr(runtime, "read_run", lambda _workspace, _run_id: copy.deepcopy(cell["run"]))

    def write_run(_workspace, value):
        cell["run"] = copy.deepcopy(value)
        return copy.deepcopy(value)

    monkeypatch.setattr(runtime, "write_run", write_run)
    return cell


def test_research_stage_persists_frozen_sources_and_replay_skips_transport(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import build_stage_gateway_request

    stored = _research_run(expert_teams, tmp_path)

    def read_run(_workspace, _run_id):
        return copy.deepcopy(stored)

    def write_run(_workspace, value):
        nonlocal stored
        stored = copy.deepcopy(value)
        return copy.deepcopy(value)

    monkeypatch.setattr(runtime, "read_run", read_run)
    monkeypatch.setattr(runtime, "write_run", write_run)
    web = _Web()

    prepared = expert_teams.prepare_research_sources_for_gateway(
        tmp_path,
        stored,
        web_adapter=web,
        local_adapter=_LocalUnavailable(),
    )
    replayed = expert_teams.prepare_research_sources_for_gateway(
        tmp_path,
        prepared,
        web_adapter=web,
        local_adapter=_LocalUnavailable(),
    )

    assert web.search_calls == 1
    assert replayed["research_retrieval_state"]["status"] == "completed"
    assert replayed["research_retrieval_state"]["public_status"]["status"] == "success"
    assert replayed["research_retrieval_state"]["local_status"]["status"] == "skipped"
    assert replayed["source_context_snapshot_ref"]["snapshot_id"].startswith("research-evidence-")
    snapshot = expert_teams.verified_source_context_for_execution(tmp_path, replayed)
    request = build_stage_gateway_request(
        replayed,
        {"id": "research", "executor": "model", "artifact_type": "source_register", "depends_on": ["direction"]},
        source_context=snapshot,
    )
    envelope = json.loads(request["messages"][1]["content"])
    assert envelope["source_context"]["sources"][0]["origin_locator"] == "https://example.test/report"


def test_research_recovery_never_repeats_public_search(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    stored = _research_run(expert_teams, tmp_path)
    stored["research_retrieval_state"] = {
        "schema_version": "research-retrieval/v1",
        "reservation_id": "old-reservation",
        "fingerprint": runtime.research_retrieval_fingerprint(stored),
        "status": "in_progress",
        "started_at": "2026-08-03T09:00:00+00:00",
        "materialized_refs": [],
    }

    monkeypatch.setattr(runtime, "read_run", lambda _workspace, _run_id: copy.deepcopy(stored))

    def write_run(_workspace, value):
        nonlocal stored
        stored = copy.deepcopy(value)
        return copy.deepcopy(value)

    monkeypatch.setattr(runtime, "write_run", write_run)
    web = _Web()
    prepared = expert_teams.prepare_research_sources_for_gateway(
        tmp_path,
        stored,
        web_adapter=web,
        local_adapter=_LocalUnavailable(),
    )

    assert web.search_calls == 0
    assert prepared["research_retrieval_state"]["status"] == "completed"
    assert prepared["research_retrieval_state"]["public_status"]["reason"] == "interrupted_before_commit"
    assert prepared["research_retrieval_state"]["local_status"]["status"] == "unavailable"


def test_local_only_and_model_only_persist_truthful_tiers(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    local_run = _research_run(expert_teams, tmp_path / "local")
    (tmp_path / "local").mkdir(exist_ok=True)
    _memory_storage(monkeypatch, runtime, local_run)
    local_result = expert_teams.prepare_research_sources_for_gateway(
        tmp_path / "local",
        local_run,
        web_adapter=_WebUnavailable(),
        local_adapter=_LocalHit(),
    )
    state = local_result["research_retrieval_state"]
    assert state["public_status"]["status"] == "unavailable"
    assert state["local_status"]["status"] == "success"
    assert state["local_status"]["count"] == 1
    assert [item["tier"] for item in state["tier_decisions"]] == ["public", "local"]
    assert expert_teams.verified_source_context_for_execution(tmp_path / "local", local_result)["sources"][0]["kind"] == "approved_internal"

    model_workspace = tmp_path / "model"
    model_workspace.mkdir()
    model_run = _research_run(expert_teams, model_workspace)
    _memory_storage(monkeypatch, runtime, model_run)
    model_result = expert_teams.prepare_research_sources_for_gateway(
        model_workspace,
        model_run,
        web_adapter=_WebUnavailable(),
        local_adapter=_LocalUnavailable(),
    )
    state = model_result["research_retrieval_state"]
    assert state["materialized_refs"] == []
    assert state["snapshot_ref"]["snapshot_id"].startswith("research-evidence-")
    assert state["tier_decisions"][-1]["tier"] == "model"
    assert state["tier_decisions"][-1]["status"] == "used"


def test_restricted_classification_denies_public_egress_and_persists_policy_reason(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_run(expert_teams, tmp_path)
    run["document_brief"].setdefault("document_control", {})["classification"] = "restricted"
    run["document_brief"]["confirmed_sha256"] = runtime.brief_digest(run["document_brief"])
    web = _Web()
    _memory_storage(monkeypatch, runtime, run)

    result = expert_teams.prepare_research_sources_for_gateway(
        tmp_path,
        run,
        web_adapter=web,
        local_adapter=_LocalUnavailable(),
    )

    assert web.search_calls == 0
    assert result["research_retrieval_state"]["public_status"]["status"] == "denied"
    assert result["research_retrieval_state"]["public_status"]["reason"] == "policy_blocked"
    assert result["research_retrieval_state"]["tier_decisions"][-1]["tier"] == "model"


def test_materialize_failure_is_non_blocking_and_does_not_claim_frozen_evidence(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    class FailingMaterialize(_Web):
        async def materialize(self, hit, *, workspace, run_id):
            raise OSError("disk unavailable")

    run = _research_run(expert_teams, tmp_path)
    _memory_storage(monkeypatch, runtime, run)
    result = expert_teams.prepare_research_sources_for_gateway(
        tmp_path,
        run,
        web_adapter=FailingMaterialize(),
        local_adapter=_LocalUnavailable(),
    )

    state = result["research_retrieval_state"]
    assert state["status"] == "completed"
    assert state["public_status"]["status"] == "materialize_failed"
    assert state["public_status"]["reason"] == "materialize_failed"
    assert state["public_status"]["count"] == 0
    assert state["materialized_refs"] == []
    assert state["tier_decisions"][-1]["tier"] == "model"
    assert expert_teams.verified_source_context_for_execution(tmp_path, result)["sources"] == []


def test_old_stage_bypasses_retrieval_and_orphan_scan_rejects_symlink_parent(tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    direction = _research_run(expert_teams, tmp_path)
    direction["current_stage_index"] = 0
    direction = runtime._sync_derived(direction)
    assert expert_teams.prepare_research_sources_for_gateway(
        tmp_path,
        direction,
        web_adapter=object(),
        local_adapter=object(),
    ) is direction

    symlink_workspace = tmp_path / "symlink-case"
    symlink_workspace.mkdir()
    outside = tmp_path / "outside"
    (outside / "expert-teams" / "sources" / direction["run_id"]).mkdir(parents=True)
    manifest = outside / "expert-teams" / "sources" / direction["run_id"] / "PUB-forged.source.json"
    manifest.write_text('{"source_id":"PUB-forged","kind":"approved_public"}', encoding="utf-8")
    (symlink_workspace / ".taiji").symlink_to(outside, target_is_directory=True)
    assert runtime._research_materialized_refs(symlink_workspace, direction["run_id"], []) == []


@pytest.mark.parametrize(
    "query",
    [
        "我司客户报价与续约风险如何",
        "未公开的项目代号是太极-A7",
        "联系 research@example.com 调研",
        "联系手机 13800138000 调研",
        "内部合同编号 1234567890123456 的风险",
    ],
)
def test_server_owned_research_egress_policy_blocks_sensitive_queries(tmp_path, monkeypatch, query):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_run(expert_teams, tmp_path, core_question=query)
    web = _Web()
    _memory_storage(monkeypatch, runtime, run)
    result = expert_teams.prepare_research_sources_for_gateway(
        tmp_path,
        run,
        web_adapter=web,
        local_adapter=_LocalUnavailable(),
    )

    assert web.search_calls == 0
    assert result["research_retrieval_state"]["public_status"]["reason"] == "policy_blocked"


def test_research_egress_policy_is_frozen_in_profile_and_old_snapshot_fails_closed(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    safe = _research_run(expert_teams, tmp_path / "safe")
    policy = safe["launch_profile_snapshot"]["research_query_egress_policy"]
    assert policy == {
        "policy_id": "research-public-query/v1",
        "version": 1,
        "authorization_basis": "user_initiated_standalone_research",
        "trust_zone": "public_web",
        "projection_version": "research-public-topic/v1",
    }
    _memory_storage(monkeypatch, runtime, safe)
    safe_web = _Web()
    safe_result = expert_teams.prepare_research_sources_for_gateway(
        tmp_path / "safe", safe, web_adapter=safe_web, local_adapter=_LocalUnavailable()
    )
    assert safe_web.search_calls == 1
    assert safe_result["research_retrieval_state"]["public_status"]["policy_id"] == policy["policy_id"]

    old_workspace = tmp_path / "old"
    old = _research_run(expert_teams, old_workspace)
    old["launch_profile_snapshot"].pop("research_query_egress_policy", None)
    _memory_storage(monkeypatch, runtime, old)
    old_web = _Web()
    old_result = expert_teams.prepare_research_sources_for_gateway(
        old_workspace, old, web_adapter=old_web, local_adapter=_LocalUnavailable()
    )
    assert old_web.search_calls == 0
    assert old_result["research_retrieval_state"]["public_status"]["reason"] == "data_egress_not_authorized"


def test_public_query_projection_blocks_customer_contract_commercial_analysis(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    sensitive = "分析客户甲的合同报价与回款风险"
    run = _research_run(
        expert_teams,
        tmp_path,
        core_question=sensitive,
        subquestions=["客户甲是否存在续约风险"],
    )
    web = _Web()
    _memory_storage(monkeypatch, runtime, run)

    result = expert_teams.prepare_research_sources_for_gateway(
        tmp_path, run, web_adapter=web, local_adapter=_LocalUnavailable()
    )

    assert web.search_calls == 0
    assert result["research_retrieval_state"]["public_status"]["reason"] == "policy_blocked"


def test_sensitive_frozen_original_request_cannot_be_bypassed_by_safe_direction(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request="分析客户甲的合同报价与回款风险",
        core_question="本地优先 AI 助理如何落地",
        subquestions=["部署成本如何"],
    )
    web = _Web()
    _memory_storage(monkeypatch, runtime, run)

    result = expert_teams.prepare_research_sources_for_gateway(
        tmp_path, run, web_adapter=web, local_adapter=_LocalUnavailable()
    )

    assert web.search_calls == 0
    assert result["research_retrieval_state"]["public_status"]["reason"] == "policy_blocked"


def test_safe_public_topic_uses_deidentified_server_projection(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_run(expert_teams, tmp_path)
    web = _Web()
    _memory_storage(monkeypatch, runtime, run)
    expert_teams.prepare_research_sources_for_gateway(
        tmp_path, run, web_adapter=web, local_adapter=_LocalUnavailable()
    )

    assert web.queries == ["本地优先 AI 助理 如何落地"]
    assert "部署成本" not in web.queries[0]
    assert "客户" not in web.queries[0]


@pytest.mark.parametrize(
    ("original_request", "expected_query"),
    [
        ("请研究中国人口老龄化与养老服务并形成报告", "中国人口老龄化 养老服务"),
        ("帮我分析新能源汽车市场", "新能源汽车市场"),
    ],
)
def test_public_query_projection_preserves_arbitrary_safe_topics(
    tmp_path, original_request, expected_query
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
        subquestions=[original_request],
    )

    decision = authorize_research_public_query(run, original_request)

    assert decision["authorized"] is True
    assert decision["safe_query"] == expected_query


def test_public_query_projection_never_imports_direction_only_entities(tmp_path):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_request = "请研究新能源汽车市场"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question="新能源汽车市场与光明研究院的发展方向",
        subquestions=["光明研究院的发展路径"],
    )

    decision = authorize_research_public_query(
        run,
        "新能源汽车市场 光明研究院 发展路径",
    )

    assert decision["authorized"] is True
    assert decision["safe_query"] == "新能源汽车市场"
    assert "光明研究院" not in decision["safe_query"]
    assert "发展路径" not in decision["safe_query"]


def test_public_query_semantics_blocks_english_confidential_customer_contract(tmp_path):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    sensitive = "Analyze confidential customer Acme contract pricing and renewal risk"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )

    decision = authorize_research_public_query(run, sensitive)

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


def test_public_query_semantics_blocks_chinese_confidential_procurement_terms(tmp_path):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    sensitive = "研究光明研究院机密采购价格与账期"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )

    decision = authorize_research_public_query(run, sensitive)

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


def test_public_query_semantics_blocks_sensitive_english_direction(tmp_path):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_request = "Analyze global renewable energy market trends"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(
        run,
        "Analyze confidential customer Acme contract pricing",
    )

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


def test_public_query_projection_allows_safe_english_public_topic(tmp_path):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_request = "Analyze global renewable energy market trends"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, original_request)

    assert decision["authorized"] is True
    assert decision["safe_query"] == "global renewable energy market trends"


def test_public_query_semantics_allows_explicit_public_annual_report(tmp_path):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_request = "研究苹果公司公开年报营收趋势"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, original_request)

    assert decision["authorized"] is True
    assert decision["safe_query"] == "苹果公司公开年报营收趋势"


def test_public_query_instruction_cleanup_preserves_research_institute_name(tmp_path):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_request = "研究光明研究院公开成果"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, original_request)

    assert decision["authorized"] is True
    assert decision["safe_query"] == "光明研究院公开成果"


@pytest.mark.parametrize(
    "sensitive",
    [
        "研究光明研究院不公开采购价格与账期",
        "Analyze Acme not public contract pricing",
        "研究 acme 采购价格",
        "Analyze GE contract pricing",
    ],
)
def test_public_query_semantics_blocks_private_transaction_variants(tmp_path, sensitive):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )

    decision = authorize_research_public_query(run, sensitive)

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


@pytest.mark.parametrize(
    ("original_request", "expected_query"),
    [
        ("研究苹果公司营收趋势", "苹果公司营收趋势"),
        ("Analyze Apple revenue trends", "Apple revenue trends"),
    ],
)
def test_public_query_semantics_allows_public_financial_metrics(
    tmp_path, original_request, expected_query
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, original_request)

    assert decision["authorized"] is True
    assert decision["safe_query"] == expected_query


@pytest.mark.parametrize(
    "sensitive",
    [
        "acme 的采购价格与账期",
        "acme采购价格与账期",
        "采购价格 for acme",
    ],
)
def test_private_transaction_detection_is_entity_order_and_spacing_independent(
    tmp_path, sensitive
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )

    decision = authorize_research_public_query(run, sensitive)

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


@pytest.mark.parametrize(
    "sensitive_direction",
    [
        "acme 的采购价格与账期",
        "acme采购价格与账期",
        "采购价格 for acme",
    ],
)
def test_private_transaction_direction_uses_same_order_independent_gate(
    tmp_path, sensitive_direction
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_request = "Analyze global renewable energy market trends"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, sensitive_direction)

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


@pytest.mark.parametrize(
    ("original_request", "expected_query"),
    [
        ("Apple iPhone 公开市场价格趋势", "Apple iPhone 公开市场价格趋势"),
        ("Apple public retail price trends", "Apple public retail price trends"),
        ("苹果公司公开年报合同金额", "苹果公司公开年报合同金额"),
        ("Apple annual report contract values", "Apple annual report contract values"),
    ],
)
def test_explicit_public_market_retail_and_annual_report_contexts_are_allowed(
    tmp_path, original_request, expected_query
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, original_request)

    assert decision["authorized"] is True
    assert decision["safe_query"] == expected_query


@pytest.mark.parametrize(
    "sensitive",
    [
        "analyze acme contract pricing",
        "contract pricing and renewal terms for acme",
        "ge contract pricing",
    ],
)
def test_exact_private_transaction_phrases_block_lowercase_english_original(
    tmp_path, sensitive
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )

    decision = authorize_research_public_query(run, sensitive)

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


@pytest.mark.parametrize(
    "sensitive_direction",
    [
        "analyze acme contract pricing",
        "contract pricing and renewal terms for acme",
        "ge contract pricing",
    ],
)
def test_exact_private_transaction_phrases_block_lowercase_english_direction(
    tmp_path, sensitive_direction
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_request = "Analyze global renewable energy market trends"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, sensitive_direction)

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


@pytest.mark.parametrize(
    "sensitive",
    [
        "Acme not retail contract pricing",
        "Acme non-retail contract pricing",
        "苹果公司非年报合同价格",
        "Apple non-annual report contract values",
    ],
)
def test_negated_public_transaction_context_never_allows_private_transaction(
    tmp_path, sensitive
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )

    decision = authorize_research_public_query(run, sensitive)

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


@pytest.mark.parametrize(
    "sensitive_direction",
    [
        "Acme not retail contract pricing",
        "Acme non-retail contract pricing",
        "苹果公司非年报合同价格",
        "Apple non-annual report contract values",
    ],
)
def test_negated_public_transaction_context_is_blocked_in_direction(
    tmp_path, sensitive_direction
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_request = "Analyze global renewable energy market trends"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, sensitive_direction)

    assert decision["authorized"] is False
    assert decision["reason_code"] == "policy_blocked"


@pytest.mark.parametrize(
    ("original_request", "expected_query"),
    [
        ("Analyze EV price trends", "EV price trends"),
        ("研究 EV 价格趋势", "EV 价格趋势"),
    ],
)
def test_plain_public_price_trends_are_not_private_transactions(
    tmp_path, original_request, expected_query
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, original_request)

    assert decision["authorized"] is True
    assert decision["safe_query"] == expected_query


@pytest.mark.parametrize(
    "sensitive",
    [
        "acme contract-pricing",
        "acme contract  pricing",
        "acme contract and pricing",
        "acme采购的价格",
        "acme 采购 价格",
        "acme 合同的报价",
    ],
)
def test_private_transaction_tokens_ignore_formatting_and_connectors(
    tmp_path, sensitive
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )

    assert authorize_research_public_query(run, sensitive)["authorized"] is False

    safe = "Analyze global renewable energy market trends"
    (tmp_path / "direction").mkdir()
    direction_run = _research_run(
        expert_teams,
        tmp_path / "direction",
        original_request=safe,
        core_question=safe,
    )
    assert authorize_research_public_query(direction_run, sensitive)["authorized"] is False


@pytest.mark.parametrize(
    "sensitive",
    [
        "Acme not a retail contract pricing",
        "Acme non retail contract pricing",
        "Acme not from an annual report contract values",
        "苹果公司不是年报中的合同价格",
        "苹果公司非官方披露合同价格",
    ],
)
def test_negated_public_context_is_tokenized_before_authorization(tmp_path, sensitive):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )

    assert authorize_research_public_query(run, sensitive)["authorized"] is False


@pytest.mark.parametrize(
    ("original_request", "expected_query"),
    [
        ("Analyze Apple annual-report contract values", "Apple annual-report contract values"),
        ("研究苹果公司年度报告合同金额", "苹果公司年度报告合同金额"),
    ],
)
def test_normalized_public_transaction_context_remains_authorized(
    tmp_path, original_request, expected_query
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )

    decision = authorize_research_public_query(run, original_request)
    assert decision["authorized"] is True
    assert decision["safe_query"] == expected_query


@pytest.mark.parametrize(
    "sensitive",
    [
        "acme contract's pricing",
        "pricing for acme contract",
        "acme procurement unit price",
        "acme 合同相关报价",
    ],
)
def test_private_transaction_uses_concept_windows_in_original_and_direction(
    tmp_path, sensitive
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )
    assert authorize_research_public_query(original_run, sensitive)["authorized"] is False

    safe = "Analyze global renewable energy market trends"
    (tmp_path / "direction-window").mkdir()
    direction_run = _research_run(
        expert_teams,
        tmp_path / "direction-window",
        original_request=safe,
        core_question=safe,
    )
    assert authorize_research_public_query(direction_run, sensitive)["authorized"] is False


@pytest.mark.parametrize(
    "sensitive",
    [
        "Acme isn't retail contract pricing",
        "Acme without retail contract pricing",
        "Acme excluding annual report contract values",
        "苹果公司排除年报的合同价格",
    ],
)
def test_local_negation_blocks_only_its_public_transaction_marker(tmp_path, sensitive):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )
    assert authorize_research_public_query(run, sensitive)["authorized"] is False


@pytest.mark.parametrize(
    ("original_request", "expected_query"),
    [
        (
            "Analyze Apple annual report contract values, not revenue",
            "Apple annual report contract values not revenue",
        ),
        (
            "Analyze Apple annual financial report contract values",
            "Apple annual financial report contract values",
        ),
        (
            "Analyze public SaaS contract pricing benchmarks",
            "public SaaS contract pricing benchmarks",
        ),
    ],
)
def test_public_context_uses_local_concept_windows(
    tmp_path, original_request, expected_query
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )
    decision = authorize_research_public_query(run, original_request)
    assert decision["authorized"] is True
    assert decision["safe_query"] == expected_query


@pytest.mark.parametrize(
    "sensitive",
    [
        "Acme retail excluded contract pricing",
        "Acme annual report excluded contract values",
        "Acme never retail contract pricing",
        "Acme cannot be treated as retail contract pricing",
        "苹果公司年报除外的合同价格",
    ],
)
def test_public_markers_support_postfix_and_natural_negation(tmp_path, sensitive):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )
    assert authorize_research_public_query(run, sensitive)["authorized"] is False


@pytest.mark.parametrize(
    ("original_request", "expected_query"),
    [
        (
            "Analyze Apple retail contract pricing but not annual report",
            "Apple retail contract pricing but not annual report",
        ),
        (
            "Analyze Not revenue but Apple annual report contract values",
            "Not revenue but Apple annual report contract values",
        ),
        (
            "Analyze Apple annual consolidated audited financial performance report contract values",
            "Apple annual consolidated audited financial performance report contract values",
        ),
    ],
)
def test_each_public_marker_has_independent_clause_scope(
    tmp_path, original_request, expected_query
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )
    decision = authorize_research_public_query(run, original_request)
    assert decision["authorized"] is True
    assert decision["safe_query"] == expected_query


@pytest.mark.parametrize(
    "sensitive",
    [
        "acme contract total cost",
        "acme contract fee",
        "acme account receivable",
    ],
)
def test_private_transaction_concepts_cover_common_financial_terms(tmp_path, sensitive):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )
    assert authorize_research_public_query(run, sensitive)["authorized"] is False


@pytest.mark.parametrize(
    "sensitive",
    [
        "Acme annual report is not applicable contract values",
        "Acme annual report has been excluded contract values",
        "Acme retail should be excluded contract pricing",
        "苹果公司年报不纳入研究范围的合同价格",
        "苹果公司不应使用年报中的合同价格",
        "Acme contract pricing. Annual operations and report preparation",
        "Acme contract pricing; public access unavailable; benchmark pending",
        "Acme annual report excluded, report contract values",
    ],
)
def test_public_transaction_context_does_not_cross_negation_or_clause_boundaries(
    tmp_path, sensitive
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=sensitive,
        core_question=sensitive,
    )
    assert authorize_research_public_query(run, sensitive)["authorized"] is False


def test_public_contract_governance_and_pricing_trends_are_not_treated_as_private(
    tmp_path,
):
    from api import expert_teams
    from api.expert_teams.data_egress import authorize_research_public_query

    original_request = "Research contract governance and pricing trends"
    run = _research_run(
        expert_teams,
        tmp_path,
        original_request=original_request,
        core_question=original_request,
    )
    decision = authorize_research_public_query(run, original_request)
    assert decision["authorized"] is True
    assert decision["safe_query"] == "contract governance and pricing trends"


def test_concurrent_same_process_retrieval_has_one_owner_and_one_web_call(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    entered = threading.Event()
    release = threading.Event()

    class BlockingWeb(_Web):
        async def search(self, query, *, limit=5, policy_decision=None):
            entered.set()
            assert release.wait(timeout=5)
            return await super().search(query, limit=limit, policy_decision=policy_decision)

    run = _research_run(expert_teams, tmp_path)
    cell = _memory_storage(monkeypatch, runtime, run)
    web = BlockingWeb()
    first = []

    def invoke_first():
        try:
            first.append(
                expert_teams.prepare_research_sources_for_gateway(
                    tmp_path, run, web_adapter=web, local_adapter=_LocalUnavailable()
                )
            )
        except Exception as exc:  # pragma: no cover - asserted below
            first.append(exc)

    worker = threading.Thread(target=invoke_first)
    worker.start()
    assert entered.wait(timeout=5)
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as conflict:
        expert_teams.prepare_research_sources_for_gateway(
            tmp_path,
            copy.deepcopy(cell["run"]),
            web_adapter=web,
            local_adapter=_LocalUnavailable(),
        )
    assert conflict.value.code == "retrieval_in_progress"
    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert web.search_calls == 1
    assert len(first) == 1 and isinstance(first[0], dict)
    assert cell["run"]["research_retrieval_state"]["status"] == "completed"
    assert cell["run"]["research_retrieval_state"]["public_status"]["status"] == "success"


def test_new_fingerprint_reservation_drops_old_receipts_and_snapshot(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_run(expert_teams, tmp_path)
    run["research_retrieval_state"] = {
        "schema_version": "research-retrieval/v1",
        "reservation_id": "old-reservation",
        "fingerprint": "f" * 64,
        "status": "completed",
        "started_at": "2026-08-03T09:00:00+00:00",
        "completed_at": "2026-08-03T09:01:00+00:00",
        "public_status": {"status": "success", "count": 1},
        "local_status": {},
        "tier_decisions": [{"tier": "public", "status": "success"}],
        "materialized_refs": [{"source_id": "PUB-OLD", "kind": "approved_public"}],
        "snapshot_ref": {"snapshot_id": "research-evidence-old"},
        "safe_reason": "old",
    }
    _memory_storage(monkeypatch, runtime, run)
    fingerprint = runtime.research_retrieval_fingerprint(run)
    reserved, mode = runtime._reserve_research_retrieval(
        tmp_path, run["run_id"], run["version"], fingerprint
    )

    assert mode == "reserved"
    state = reserved["research_retrieval_state"]
    assert state["fingerprint"] == fingerprint
    assert state["materialized_refs"] == []
    assert state["public_status"] == {}
    assert state["local_status"] == {}
    assert state["tier_decisions"] == []
    assert state["snapshot_ref"] == {}


def test_recovery_uses_only_receipted_refs_for_coverage_and_skips_model(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_run(expert_teams, tmp_path)
    cell = _memory_storage(monkeypatch, runtime, run)
    web = _Web()
    original_complete = runtime._complete_research_retrieval

    def crash_before_complete(*args, **kwargs):
        raise RuntimeError("crash after receipt")

    monkeypatch.setattr(runtime, "_complete_research_retrieval", crash_before_complete)
    with pytest.raises(RuntimeError, match="crash after receipt"):
        expert_teams.prepare_research_sources_for_gateway(
            tmp_path, run, web_adapter=web, local_adapter=_LocalUnavailable()
        )
    state = cell["run"]["research_retrieval_state"]
    assert state["status"] == "in_progress"
    assert len(state["materialized_refs"]) == 1

    monkeypatch.setattr(runtime, "_complete_research_retrieval", original_complete)
    recovered = expert_teams.prepare_research_sources_for_gateway(
        tmp_path,
        copy.deepcopy(cell["run"]),
        web_adapter=web,
        local_adapter=_LocalUnavailable(),
    )

    assert web.search_calls == 1
    tiers = recovered["research_retrieval_state"]["tier_decisions"]
    assert not any(item["tier"] == "model" for item in tiers)
    assert recovered["research_retrieval_state"]["public_status"]["count"] == 1
    assert recovered["research_retrieval_state"]["public_status"]["coverage"] == "sufficient"


def test_recovered_sufficient_public_evidence_skips_healthy_local_probe_and_search(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_run(expert_teams, tmp_path)
    cell = _memory_storage(monkeypatch, runtime, run)
    web = _Web()
    original_complete = runtime._complete_research_retrieval
    monkeypatch.setattr(
        runtime,
        "_complete_research_retrieval",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("crash after receipt")),
    )
    with pytest.raises(RuntimeError, match="crash after receipt"):
        expert_teams.prepare_research_sources_for_gateway(
            tmp_path, run, web_adapter=web, local_adapter=_LocalUnavailable()
        )
    monkeypatch.setattr(runtime, "_complete_research_retrieval", original_complete)
    local = _CountingLocal()

    recovered = expert_teams.prepare_research_sources_for_gateway(
        tmp_path,
        copy.deepcopy(cell["run"]),
        web_adapter=web,
        local_adapter=local,
    )

    assert web.search_calls == 1
    assert local.probe_calls == 0
    assert local.search_calls == 0
    assert not any(
        item["tier"] == "model"
        for item in recovered["research_retrieval_state"]["tier_decisions"]
    )


def test_unreceipted_orphan_manifest_is_never_recovered(tmp_path):
    from api.expert_teams import runtime
    from api.expert_teams.source_registry import materialize_approved_source

    content = "部署成本如何：孤儿资料"
    ref = materialize_approved_source(
        tmp_path,
        "et-orphan",
        kind="approved_public",
        label="orphan",
        origin_locator="https://example.test/orphan",
        content=content,
        retrieved_at="2026-08-03T10:00:00+00:00",
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    assert ref["source_id"]
    assert runtime._research_materialized_refs(tmp_path, "et-orphan", []) == []
