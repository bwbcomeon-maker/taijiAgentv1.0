import copy
import hashlib
import json
import threading

import pytest


def _research_run(expert_teams, tmp_path, *, core_question="本地优先 AI 助理如何落地", subquestions=None):
    from api.expert_teams import runtime

    run = expert_teams.build_standalone_expert_team_run(
        {
            "launch_profile_id": "research-report",
            "session_id": "research-runtime",
            "prompt": core_question,
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

    async def probe(self):
        from api.expert_teams.research_sources import ProbeResult

        return ProbeResult(True)

    async def search(self, query, *, limit=5, policy_decision=None):
        from api.expert_teams.research_sources import AdapterSearchResult, ResearchSourceHit

        self.search_calls += 1
        assert query == "本地优先 AI 助理如何落地 部署成本如何"
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
