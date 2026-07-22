import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest


CONTRACT_VERSION = "expert-team-contract/v1"


@pytest.fixture(autouse=True)
def _enable_contract_pilot(monkeypatch):
    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "pilot")


def _start(expert_teams, workspace: Path, *, source_refs=None):
    return expert_teams.start_expert_team(
        workspace,
        {
            "session_id": "brief-sources",
            "team_id": "content-creator-team",
            "contract_version": CONTRACT_VERSION,
            "document_type": "work_report",
            "template_id": "work_report",
            "prompt": "起草迎峰度夏月度工作汇报",
            "document_brief_seed": {
                "exact_title": "迎峰度夏月度工作汇报",
                "purpose": "向分管领导汇报进展",
                "audience": "公司分管领导",
                "usage_scenario": "月度例会",
                "source_policy": {
                    "mode": "provided_only",
                    "citation_style": "source_id",
                    "unknown_fact_action": "allow_labeled_placeholder",
                    "source_refs": list(source_refs or []),
                },
                "data_handling": {
                    "model_policy_id": "enterprise-local-default",
                    "requires_zero_retention": True,
                },
                "document_control": {
                    "classification": "internal",
                    "render_template_id": "enterprise-work-report",
                },
                "content_constraints": {
                    "required_sections": ["工作开展情况", "存在问题", "下一步工作安排"],
                    "must_include": [],
                    "must_avoid": [],
                },
                "details": {"reporting_period": "2026年7月", "reporting_unit": "综合部"},
                "approval": {"human_final_review_required": True, "approver_roles": ["部门负责人"]},
            },
        },
    )


def _source_request(run, key, **extra):
    return {
        "session_id": run["session_id"],
        "run_id": run["run_id"],
        "expected_version": run["version"],
        "expected_brief_revision": run["document_brief"]["revision"],
        "idempotency_key": key,
        **extra,
    }


def test_provided_text_survives_normalization_and_confirms(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    _, _, policies = _registries()
    monkeypatch.setattr(runtime, "load_model_policy_registry", lambda: policies)
    run = _start(
        expert_teams,
        tmp_path,
        source_refs=[
            {
                "source_id": "SRC-TEXT",
                "kind": "provided_text",
                "label": "人工补充说明",
                "text": "已完成三项重点任务，剩余两项按计划推进。",
            }
        ],
    )

    assert run["document_brief"]["source_policy"]["source_refs"][0]["text"].startswith("已完成")
    confirmed = expert_teams.confirm_expert_team_document_brief(
        tmp_path,
        _source_request(run, "confirm-provided-text"),
    )

    ref = confirmed["document_brief"]["source_policy"]["source_refs"][0]
    assert ref["kind"] == "provided_text"
    assert "text" not in ref
    assert ref["locator"].startswith(".taiji/expert-teams/sources/")
    assert confirmed["view"]["brief"]["sources"] == [
        {
            "source_id": "SRC-TEXT",
            "kind": "provided_text",
            "label": "人工补充说明",
            "status": "ready",
            "size_bytes": len("已完成三项重点任务，剩余两项按计划推进。".encode("utf-8")),
            "sha256": ref["sha256"],
        }
    ]


def test_source_add_and_remove_are_cas_guarded_idempotent_and_safe(tmp_path):
    from api import expert_teams

    run = _start(expert_teams, tmp_path)
    request = _source_request(
        run,
        "source-add-1",
        source={
            "source_id": "SRC-ADD",
            "kind": "provided_text",
            "label": "7月台账",
            "text": "完成率 96%，其余事项均已明确责任人。",
        },
    )
    added = expert_teams.add_expert_team_brief_source(tmp_path, request)
    replay = expert_teams.add_expert_team_brief_source(tmp_path, request)

    assert added == replay
    assert added["version"] == run["version"] + 1
    assert added["document_brief"]["revision"] == run["document_brief"]["revision"] + 1
    assert len(added["view"]["brief"]["sources"]) == 1
    safe = json.dumps(added["view"]["brief"]["sources"], ensure_ascii=False)
    assert "完成率 96%" not in safe
    assert ".taiji/" not in safe

    conflicting_replay = {
        **request,
        "source": {"source_id": "SRC-OTHER", "kind": "provided_text", "label": "其他", "text": "不同内容"},
    }
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as reused:
        expert_teams.add_expert_team_brief_source(tmp_path, conflicting_replay)
    assert reused.value.code == "idempotency_key_reused"

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as stale:
        expert_teams.remove_expert_team_brief_source(
            tmp_path,
            _source_request(run, "source-remove-stale", source_id="SRC-ADD"),
        )
    assert stale.value.code == "version_conflict"

    remove = _source_request(added, "source-remove-1", source_id="SRC-ADD")
    removed = expert_teams.remove_expert_team_brief_source(tmp_path, remove)
    assert expert_teams.remove_expert_team_brief_source(tmp_path, remove) == removed
    assert removed["view"]["brief"]["sources"] == []


def test_source_added_as_materialized_text_can_be_confirmed(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    _, _, policies = _registries()
    monkeypatch.setattr(runtime, "load_model_policy_registry", lambda: policies)
    run = _start(expert_teams, tmp_path)
    added = expert_teams.add_expert_team_brief_source(
        tmp_path,
        _source_request(
            run,
            "source-add-confirm",
            source={"source_id": "SRC-READY", "kind": "provided_text", "label": "月度材料", "text": "本月完成三项重点任务。"},
        ),
    )
    confirmed = expert_teams.confirm_expert_team_document_brief(
        tmp_path,
        _source_request(added, "source-confirm-after-add"),
    )

    assert confirmed["workflow_state"] == "ready_to_generate"
    assert confirmed["view"]["brief"]["sources"][0]["status"] == "ready"


def test_source_add_assigns_a_server_source_id_when_browser_omits_it(tmp_path):
    from api import expert_teams

    run = _start(expert_teams, tmp_path)
    added = expert_teams.add_expert_team_brief_source(
        tmp_path,
        _source_request(
            run,
            "source-add-server-id",
            source={"kind": "provided_text", "label": "浏览器资料", "text": "由浏览器 File.text 读取。"},
        ),
    )

    source = added["view"]["brief"]["sources"][0]
    assert source["source_id"].startswith("SRC-")
    assert source["label"] == "浏览器资料"


def test_source_mutation_preserves_the_current_intake_state(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import write_run

    run = _start(expert_teams, tmp_path)
    optional = {**run, "workflow_state": "collecting_optional"}
    write_run(tmp_path, optional)

    added = expert_teams.add_expert_team_brief_source(
        tmp_path,
        _source_request(
            optional,
            "source-add-preserve-state",
            source={"kind": "provided_text", "label": "补充资料", "text": "本月完成三项重点任务。"},
        ),
    )

    assert added["workflow_state"] == "collecting_optional"
    removed = expert_teams.remove_expert_team_brief_source(
        tmp_path,
        _source_request(added, "source-remove-preserve-state", source_id=added["view"]["brief"]["sources"][0]["source_id"]),
    )
    assert removed["workflow_state"] == "collecting_optional"


@pytest.mark.parametrize(
    ("filename", "content", "expected_code"),
    [
        ("payload.exe", b"hello", "source_type_not_allowed"),
        ("binary.txt", b"hello\x00world", "source_binary_not_allowed"),
        ("invalid.md", b"\xff\xfe", "source_invalid_utf8"),
    ],
)
def test_source_add_rejects_unsupported_binary_and_invalid_utf8(tmp_path, filename, content, expected_code):
    from api import expert_teams
    from api.expert_teams.contracts import ContractError

    run = _start(expert_teams, tmp_path)
    (tmp_path / filename).write_bytes(content)
    with pytest.raises(ContractError) as error:
        expert_teams.add_expert_team_brief_source(
            tmp_path,
            _source_request(
                run,
                f"reject-{filename}",
                source={"source_id": "SRC-FILE", "kind": "local_file", "label": filename, "locator": filename},
            ),
        )
    assert error.value.code == expected_code


def test_source_add_rejects_absolute_escape_symlink_and_oversize(tmp_path):
    from api import expert_teams
    from api.expert_teams.contracts import ContractError

    run = _start(expert_teams, tmp_path)
    outside = tmp_path.parent / "outside-source.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    (tmp_path / "large.txt").write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    locators = [str(outside), "../outside-source.txt", "link.txt", "large.txt"]

    for index, locator in enumerate(locators):
        with pytest.raises(ContractError) as error:
            expert_teams.add_expert_team_brief_source(
                tmp_path,
                _source_request(
                    run,
                    f"unsafe-{index}",
                    source={"source_id": f"SRC-{index}", "kind": "local_file", "label": "unsafe", "locator": locator},
                ),
            )
        assert error.value.code in {"source_unresolved", "source_too_large"}


def test_provided_text_source_rejects_more_than_ten_megabytes(tmp_path):
    from api import expert_teams
    from api.expert_teams.contracts import ContractError

    run = _start(expert_teams, tmp_path)
    with pytest.raises(ContractError) as error:
        expert_teams.add_expert_team_brief_source(
            tmp_path,
            _source_request(
                run,
                "provided-text-too-large",
                source={"source_id": "SRC-LARGE", "kind": "provided_text", "label": "超限资料", "text": "x" * (10 * 1024 * 1024 + 1)},
            ),
        )
    assert error.value.code == "source_too_large"


def test_source_mutations_keep_legacy_runs_read_only(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import write_run

    legacy = {
        "run_id": "et-legacy-source",
        "session_id": "legacy-session",
        "schema_version": 1,
        "version": 1,
        "workflow_state": "collecting_required",
    }
    write_run(tmp_path, legacy)
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as error:
        expert_teams.add_expert_team_brief_source(
            tmp_path,
            {
                "session_id": "legacy-session",
                "run_id": legacy["run_id"],
                "expected_version": 1,
                "expected_brief_revision": 1,
                "idempotency_key": "legacy-source",
                "source": {"source_id": "SRC", "kind": "provided_text", "text": "x"},
            },
        )
    assert error.value.code == "legacy_read_only"


class _Handler:
    def __init__(self, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.status = None
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = self
        self.body = bytearray()

    def send_response(self, status):
        self.status = status

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)

    def json_body(self):
        return json.loads(bytes(self.body).decode("utf-8"))


def test_source_add_and_remove_routes_call_authoritative_mutations(monkeypatch, tmp_path):
    from api import expert_teams, routes

    run = _start(expert_teams, tmp_path)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    add_body = _source_request(
        run,
        "route-add",
        source={"source_id": "SRC-ROUTE", "kind": "provided_text", "label": "补充", "text": "补充材料"},
    )
    add = _Handler(add_body)
    routes.handle_post(add, urlparse("/api/expert-teams/brief/sources/add"))
    assert add.status == 200
    added = add.json_body()["run"]
    assert added["view"]["brief"]["sources"][0]["source_id"] == "SRC-ROUTE"

    remove = _Handler(
        {
            "session_id": added["session_id"],
            "run_id": added["run_id"],
            "expected_version": added["version"],
            "expected_brief_revision": added["document_brief"]["revision"],
            "idempotency_key": "route-remove",
            "source_id": "SRC-ROUTE",
        }
    )
    routes.handle_post(remove, urlparse("/api/expert-teams/brief/sources/remove"))
    assert remove.status == 200
    assert remove.json_body()["run"]["view"]["brief"]["sources"] == []


def _registries():
    return (
        {"approved_public_search": False},
        {},
        {
            "enterprise-local-default": {
                "label": "企业本地模型",
                "allowed_classifications": ["public", "internal", "restricted"],
                "provider_ids": ["local-enterprise-model"],
                "deployment_ids": ["taiji-onprem-01"],
                "trust_zones": ["local"],
                "retention_modes": ["zero_retention"],
                "training_opt_out_required": True,
                "allowed_source_kinds": ["attachment", "local_file", "provided_text"],
                "expires_at": "2027-07-15T00:00:00+08:00",
                "approval_ref": "security-policy-2026-01",
            }
        },
    )
