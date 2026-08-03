import copy
import json


def _research_run(expert_teams, tmp_path):
    from api.expert_teams import runtime

    run = expert_teams.build_standalone_expert_team_run(
        {
            "launch_profile_id": "research-report",
            "session_id": "research-runtime",
            "prompt": "请评估本地优先 AI 助理的部署成本",
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
            "core_question": "本地优先 AI 助理如何落地",
            "subquestions": ["部署成本如何"],
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
