import io
import json
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

import pytest


class _RouteHandler:
    def __init__(self, payload: dict):
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

    def json_body(self) -> dict:
        return json.loads(bytes(self.body).decode("utf-8"))


def _post(routes, path: str, body: dict) -> _RouteHandler:
    handler = _RouteHandler(body)
    routes.handle_post(handler, urlparse(path))
    return handler


def _profile_start(**overrides):
    payload = {
        "launch_profile_id": "content-work-report",
        "session_id": "standalone-session",
        "prompt": "起草迎峰度夏保供电重点工作月度汇报",
        "idempotency_key": "start-standalone-session",
    }
    payload.update(overrides)
    return payload


def _legacy_office_timeline():
    return [
        {
            "type": "generation_completed",
            "title": "阶段成果已生成",
            "detail": "交付复核",
            "member_id": "writer",
            "at": "2026-07-23T12:00:00+08:00",
        },
        {
            "type": "office_acceptance_required",
            "title": "需完成 WPS/Word 验收",
            "detail": "最终交付包自动校验完成，请完成 Office 企业验收。",
            "member_id": "delivery",
            "at": "2026-07-23T12:01:00+08:00",
        },
        {
            "type": "stage_approved",
            "title": "阶段成果已确认",
            "detail": "交付复核",
            "member_id": "director",
            "at": "2026-07-23T12:02:00+08:00",
        },
    ]


def test_launch_profiles_are_the_only_server_owned_startable_combinations():
    from api.expert_teams.launch_profiles import list_launch_profiles

    profiles = list_launch_profiles()

    assert [profile["id"] for profile in profiles] == [
        "content-work-report",
        "research-report",
    ]
    assert [
        (
            profile["team_id"],
            profile["document_type"],
            profile["intake_example_id"],
            profile["render_template_id"],
            len(profile["stages"]),
        )
        for profile in profiles
    ] == [
        (
            "content-creator-team",
            "work_report",
            "work_report",
            "enterprise-work-report",
            5,
        ),
        (
            "deep-research-team",
            "research_report",
            "research_report",
            "enterprise-research-report",
            6,
        ),
    ]
    assert all(profile["review_policy"] == {"kind": "local_confirmation"} for profile in profiles)


def test_catalog_startable_entries_equal_profiles_without_enterprise_rollout_copy():
    from api import expert_teams
    from api.expert_teams.launch_profiles import list_launch_profiles

    catalog = expert_teams.expert_team_catalog()
    examples = [
        example
        for team in catalog["teams"]
        for example in team.get("examples") or []
    ]
    startable = [example for example in examples if example.get("available") is True]

    assert {example["launch_profile_id"] for example in startable} == {
        profile["id"] for profile in list_launch_profiles()
    }
    assert {example["id"] for example in startable} == {"work_report", "research_report"}
    assert all(example["capability"] == {"kind": "standalone", "label": "本机协作"} for example in startable)
    assert all("launch_profile_id" not in example for example in examples if not example.get("available"))
    assert all(
        example["capability"] == {"kind": "unavailable", "label": "暂未开放"}
        for example in examples
        if not example.get("available")
    )
    assert "contract_rollout" not in catalog
    assert all(
        set(example)
        == {
            "id",
            "label",
            "summary",
            "prompt",
            "available",
            "capability",
            "launch_profile_id",
        }
        for example in startable
    )
    assert all(
        set(example)
        == {
            "id",
            "label",
            "summary",
            "prompt",
            "available",
            "capability",
            "disabled_reason",
        }
        for example in examples
        if not example.get("available")
    )
    serialized = json.dumps(catalog, ensure_ascii=False)
    assert "contract_version" not in serialized
    assert "企业合同试点" not in serialized
    assert "document_brief_seed" not in serialized
    assert "document_type" not in serialized
    assert "render_template_id" not in serialized
    assert "enterprise" not in serialized.lower()
    assert all(team.get("members") and team.get("tasks") and team.get("image") for team in catalog["teams"])


@pytest.mark.parametrize(
    ("payload", "code", "field"),
    [
        (
            {
                "session_id": "legacy-public-start",
                "team_id": "content-creator-team",
                "prompt": "起草工作汇报",
            },
            "launch_profile_required",
            "launch_profile_id",
        ),
        (
            _profile_start(launch_profile_id="content-work-reprot"),
            "unknown_launch_profile",
            "launch_profile_id",
        ),
        (
            _profile_start(team_id="deep-research-team"),
            "server_owned_launch_field",
            "team_id",
        ),
        (
            {
                "session_id": "old-contract-public-start",
                "team_id": "content-creator-team",
                "contract_version": "expert-team-contract/v1",
                "document_type": "work_report",
                "prompt": "起草工作汇报",
            },
            "launch_profile_required",
            "launch_profile_id",
        ),
    ],
)
def test_public_start_route_rejects_missing_typo_and_legacy_selectors_without_writing(
    monkeypatch,
    tmp_path,
    payload,
    code,
    field,
):
    from api import routes

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "_append_expert_team_session_entry", lambda _run: [])
    before = sorted(tmp_path.rglob("*.json"))

    handler = _post(routes, "/api/expert-teams/start", payload)

    assert handler.status == 400
    assert handler.json_body()["code"] == code
    assert handler.json_body()["field"] == field
    assert sorted(tmp_path.rglob("*.json")) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param(field, value, id=f"{field}-{kind}")
        for field in ("launch_profile_id", "session_id", "prompt", "idempotency_key")
        for kind, value in (
            ("list", []),
            ("object", {}),
            ("bool", True),
            ("null", None),
        )
    ],
)
def test_public_start_route_rejects_non_string_fields_before_workspace_or_write(
    monkeypatch,
    tmp_path,
    field,
    value,
):
    from api import routes

    workspace_calls = []
    append_calls = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "_expert_team_workspace",
        lambda session_id=None: workspace_calls.append(session_id) or tmp_path,
    )
    monkeypatch.setattr(
        routes,
        "_append_expert_team_session_entry",
        lambda run: append_calls.append(run) or [],
    )

    handler = _post(routes, "/api/expert-teams/start", _profile_start(**{field: value}))

    assert handler.status == 400
    assert handler.json_body()["code"] == f"{field}_invalid_type"
    assert handler.json_body()["field"] == field
    assert workspace_calls == []
    assert append_calls == []
    assert sorted(tmp_path.rglob("*.json")) == []


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        pytest.param("launch_profile_id", "   ", "launch_profile_required", id="profile-empty"),
        pytest.param("session_id", "   ", "session_id_required", id="session-empty"),
        pytest.param("prompt", "   \n", "prompt_required", id="prompt-empty"),
        pytest.param("idempotency_key", "   ", "idempotency_key_required", id="idempotency-empty"),
        pytest.param("launch_profile_id", "p" * 129, "launch_profile_id_too_long", id="profile-too-long"),
        pytest.param("session_id", "s" * 241, "session_id_too_long", id="session-too-long"),
        pytest.param("prompt", "p" * 20001, "prompt_too_long", id="prompt-too-long"),
        pytest.param("idempotency_key", "i" * 241, "idempotency_key_too_long", id="idempotency-too-long"),
        pytest.param("session_id", "bad/session", "session_id_invalid_format", id="session-slash"),
        pytest.param("session_id", "bad\\session", "session_id_invalid_format", id="session-backslash"),
        pytest.param("session_id", "bad\nsession", "session_id_invalid_format", id="session-control"),
        pytest.param("idempotency_key", "short", "idempotency_key_too_short", id="idempotency-too-short"),
        pytest.param(
            "idempotency_key",
            "invalid key!",
            "idempotency_key_invalid_format",
            id="idempotency-invalid-format",
        ),
    ],
)
def test_public_start_route_rejects_empty_oversized_or_unsafe_fields_before_write(
    monkeypatch,
    tmp_path,
    field,
    value,
    code,
):
    from api import routes

    workspace_calls = []
    append_calls = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "_expert_team_workspace",
        lambda session_id=None: workspace_calls.append(session_id) or tmp_path,
    )
    monkeypatch.setattr(
        routes,
        "_append_expert_team_session_entry",
        lambda run: append_calls.append(run) or [],
    )

    handler = _post(routes, "/api/expert-teams/start", _profile_start(**{field: value}))

    assert handler.status == 400
    assert handler.json_body()["code"] == code
    assert handler.json_body()["field"] == field
    assert workspace_calls == []
    assert append_calls == []
    assert sorted(tmp_path.rglob("*.json")) == []


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("launch_profile_id", "launch_profile_required"),
        ("session_id", "session_id_required"),
        ("prompt", "prompt_required"),
        ("idempotency_key", "idempotency_key_required"),
    ],
)
def test_public_start_route_requires_every_public_field_before_write(
    monkeypatch,
    tmp_path,
    field,
    code,
):
    from api import routes

    payload = _profile_start()
    payload.pop(field)
    workspace_calls = []
    append_calls = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "_expert_team_workspace",
        lambda session_id=None: workspace_calls.append(session_id) or tmp_path,
    )
    monkeypatch.setattr(
        routes,
        "_append_expert_team_session_entry",
        lambda run: append_calls.append(run) or [],
    )

    handler = _post(routes, "/api/expert-teams/start", payload)

    assert handler.status == 400
    assert handler.json_body()["code"] == code
    assert handler.json_body()["field"] == field
    assert workspace_calls == []
    assert append_calls == []
    assert sorted(tmp_path.rglob("*.json")) == []


def test_public_start_route_creates_only_standalone_schema_v3(monkeypatch, tmp_path):
    from api import config, models, routes

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    sessions = OrderedDict()
    for module in (config, models, routes):
        if hasattr(module, "SESSION_DIR"):
            monkeypatch.setattr(module, "SESSION_DIR", session_dir)
        if hasattr(module, "SESSION_INDEX_FILE"):
            monkeypatch.setattr(module, "SESSION_INDEX_FILE", session_dir / "_index.json")
        if hasattr(module, "SESSIONS"):
            monkeypatch.setattr(module, "SESSIONS", sessions)
    session = models.Session(session_id="standalone-session", workspace=str(tmp_path))
    session.save(touch_updated_at=False, skip_index=True)
    sessions[session.session_id] = session
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "resolve_trusted_workspace",
        lambda value: Path(value).resolve(),
    )
    monkeypatch.setattr(routes, "_replace_state_db_truth", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args, **_kwargs: None)

    handler = _post(routes, "/api/expert-teams/start", _profile_start())

    assert handler.status == 200
    run = handler.json_body()["run"]
    assert run["schema_version"] == 3
    assert run["product_mode"] == "standalone"
    assert run["launch_profile_id"] == "content-work-report"


def test_profile_start_creates_standalone_schema_v3_with_immutable_server_snapshot(tmp_path):
    from api import expert_teams
    from api.expert_teams.launch_profiles import get_launch_profile
    from api.expert_teams.storage import read_run_raw

    # ``start_expert_team`` is the legacy/internal constructor used by runtime
    # unit tests. A standalone v3 file created here is deliberately not public:
    # production visibility requires the atomic /start receipt and bindings.
    run = expert_teams.start_expert_team(tmp_path, _profile_start())

    assert run["schema_version"] == 3
    assert run["product_mode"] == "standalone"
    assert run["launch_profile_id"] == "content-work-report"
    assert run["review_policy"] == {"kind": "local_confirmation"}
    assert run["launch_profile_snapshot"] == get_launch_profile("content-work-report")
    assert run["team_id"] == "content-creator-team"
    assert run["document_brief"]["document_type"] == "work_report"
    assert run["document_brief"]["intake_example_id"] == "work_report"
    assert run["document_brief"]["document_control"]["render_template_id"] == "enterprise-work-report"
    assert len(run["tasks"]) == 5

    run["launch_profile_snapshot"]["team_id"] = "forged-after-write"
    reopened = read_run_raw(tmp_path, run["run_id"])
    assert reopened["launch_profile_snapshot"]["team_id"] == "content-creator-team"


@pytest.mark.parametrize(
    ("patch", "field", "code"),
    [
        ({"launch_profile_id": "missing-profile"}, "launch_profile_id", "unknown_launch_profile"),
        ({"team_id": "deep-research-team"}, "team_id", "server_owned_launch_field"),
        ({"document_type": "research_report"}, "document_type", "server_owned_launch_field"),
        ({"intake_example_id": "research_report"}, "intake_example_id", "server_owned_launch_field"),
        ({"contract_version": "expert-team-contract/v1"}, "contract_version", "server_owned_launch_field"),
        (
            {"launch_profile_snapshot": {"team_id": "deep-research-team"}},
            "launch_profile_snapshot",
            "server_owned_launch_field",
        ),
        ({"review_policy": {"kind": "enterprise_approval"}}, "review_policy", "server_owned_launch_field"),
    ],
)
def test_profile_start_rejects_unknown_or_client_owned_profile_fields_without_writing(
    monkeypatch,
    tmp_path,
    patch,
    field,
    code,
):
    from api import expert_teams

    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "pilot")
    before = sorted(tmp_path.rglob("*.json"))
    with pytest.raises(expert_teams.ContractError) as error:
        expert_teams.start_expert_team(tmp_path, _profile_start(**patch))

    assert error.value.field == field
    assert error.value.code == code
    assert sorted(tmp_path.rglob("*.json")) == before


def test_standalone_view_projects_real_workflow_without_enterprise_identity_capability(tmp_path):
    from api import expert_teams
    from api.expert_teams.view import expert_team_run_view

    run = expert_teams.start_expert_team(
        tmp_path,
        _profile_start(
            launch_profile_id="research-report",
            session_id="research-session",
            idempotency_key="start-research-session",
            prompt="研究本地优先 AI 助理在企业办公场景的落地趋势",
        ),
    )
    view = run["view"]

    assert view["product_mode"] == "standalone"
    assert view["capability"] == {"kind": "standalone", "label": "本机协作"}
    assert view["workflow"]["total"] == 6
    assert view["workflow"]["current_index"] == 0
    assert len(view["workflow"]["stages"]) == 6
    assert "contract_version" not in view
    assert view["brief"]["document_type"] == "research_report"
    assert view["brief"]["document_control"]["render_template_id"] == "enterprise-research-report"

    stage_artifact = {
        "artifact_id": "artifact-direction-1",
        "sha256": "a" * 64,
        "stage_id": "direction",
        "artifact_type": "research_charter",
        "stage_attempt": 1,
        "summary": "已形成研究方向。",
        "deliverable_markdown": "# 研究方向",
        "validation_status": "valid",
        "blocking_issues": [],
    }
    run["stage_artifacts"] = [stage_artifact]
    run["current_stage_artifact_ref"] = {
        "artifact_id": stage_artifact["artifact_id"],
        "sha256": stage_artifact["sha256"],
    }
    projected = expert_team_run_view(run)
    assert projected["stage_result"]["artifact_type"] == "research_charter"
    assert projected["artifact_validation"] == {"status": "valid", "blocking_count": 0}
    serialized = json.dumps(projected, ensure_ascii=False)
    for forbidden in ("企业合同试点", "企业审批", "OIDC", "审批角色"):
        assert forbidden not in serialized


def test_standalone_completed_without_local_confirmation_projects_as_pending_not_completed():
    from api.expert_teams.contracts import confirm_document_brief
    from api.expert_teams.view import expert_team_run_view
    from api.expert_teams.launch_profiles import get_launch_profile

    brief = confirm_document_brief(
        {
            "schema_version": "document-brief/v1",
            "revision": 1,
            "status": "draft",
            "team_id": "content-creator-team",
            "task_mode": "create",
            "original_request": "起草工作汇报",
            "document_type": "work_report",
            "intake_example_id": "work_report",
            "exact_title": "部门工作汇报",
            "purpose": "本机查看与交付",
            "audience": "本机用户",
            "usage_scenario": "内部汇报",
            "source_policy": {"source_refs": []},
            "data_handling": {},
            "document_control": {"render_template_id": "enterprise-work-report"},
            "content_constraints": {},
            "details": {},
            "approval": {},
            "additional_context": "",
            "confirmed_revision": None,
            "confirmed_at": None,
            "confirmed_sha256": None,
        },
        now="2026-07-23T12:00:00+08:00",
    )
    profile = get_launch_profile("content-work-report")
    artifact_ref = {"artifact_id": "artifact-final", "sha256": "a" * 64}
    run = {
        "schema_version": 3,
        "run_id": "et-standalone-late",
        "session_id": "standalone-late",
        "contract_version": "expert-team-contract/v1",
        "product_mode": "standalone",
        "launch_profile_id": profile["id"],
        "launch_profile_snapshot": profile,
        "review_policy": {"kind": "local_confirmation"},
        "team_id": "content-creator-team",
        "workflow_state": "completed",
        "document_brief": brief,
        "canonical_document_ref": {
            **artifact_ref,
            "brief_revision": brief["confirmed_revision"],
            "brief_sha256": brief["confirmed_sha256"],
        },
        "approved_stage_artifact_refs": {"polish": artifact_ref},
        "enterprise_quality_gates": {
            "brief": "passed",
            "semantic": "passed",
            "evidence": "passed",
            "asset": "passed",
            "render": "passed",
            "office": "passed",
        },
        "current_delivery_manifest_ref": {"delivery_binding_sha256": "b" * 64, "delivery_attempt": 1},
        "completion_transaction_ref": {"transaction_id": "enterprise-transaction", "delivery_attempt": 1},
        "completion_integrity": {
            "transaction_state": "committed",
            "summary_closed": True,
            "status": "passed",
        },
        "office_review_view": {"status": "passed", "action": "open_office_review"},
        "tasks": profile["stages"],
        "current_stage_index": 4,
    }

    view = expert_team_run_view(run)

    assert view["presentation"]["state"] == "awaiting_local_confirmation"
    assert view["presentation"]["title"] == "等待本机确认"
    assert view["presentation"]["detail"] == "文档已生成，请在本机确认后完成任务。"
    assert view["presentation"]["primary_action"] is None
    assert view["presentation"]["secondary_actions"] == []
    assert view["workspace"]["state"] == "awaiting_local_confirmation"
    assert list(view["completion_gates"]) == ["content", "document", "local_confirmation"]
    assert view["completion_gates"]["content"]["status"] == "passed"
    assert view["completion_gates"]["document"]["status"] == "passed"
    assert view["completion_gates"]["local_confirmation"] == {
        "status": "pending",
        "label": "等待本机确认",
        "reason_code": "local_confirmation_required",
        "blocking_issue_count": 0,
        "next_action": {"type": "wait_local_confirmation", "label": "等待本机确认"},
    }
    assert view["delivery_status"] == "local_confirmation_required"
    assert view["next_action"] == {"type": "wait_local_confirmation", "label": "等待本机确认"}
    assert view["office_review"] is None
    serialized = json.dumps(view, ensure_ascii=False)
    assert '"office"' not in serialized
    assert "open_office_review" not in serialized
    assert "Office 验收" not in serialized


def test_standalone_awaiting_review_rewrites_stale_office_validation_as_local_confirmation(tmp_path):
    from api import expert_teams
    from api.expert_teams.view import expert_team_run_view

    run = expert_teams.start_expert_team(tmp_path, _profile_start())
    run["workflow_state"] = "awaiting_review"
    run["validation"] = {"status": "office_acceptance_required"}
    run["last_validation_error"] = "请完成 WPS/Word Office 企业验收后再确认交付。"

    view = expert_team_run_view(run)

    assert view["presentation"]["state"] == "awaiting_review"
    assert view["presentation"]["title"] == "成果待本机确认"
    assert view["presentation"]["detail"] == "阶段成果已生成，请在本机确认后继续。"
    serialized = json.dumps(view["presentation"], ensure_ascii=False)
    for forbidden in ("Office", "WPS", "Word", "企业验收"):
        assert forbidden not in serialized


def test_standalone_completion_reconciling_uses_neutral_local_delivery_recovery_copy(tmp_path):
    from api import expert_teams
    from api.expert_teams.view import expert_team_run_view

    run = expert_teams.start_expert_team(tmp_path, _profile_start(session_id="completion-recovery"))
    run["workflow_state"] = "completed"
    run["completion_transaction_ref"] = {
        "transaction_id": "stale-enterprise-transaction",
        "delivery_attempt": 1,
    }
    run["completion_integrity"] = {
        "status": "unverified",
        "transaction_state": "committed",
        "summary_closed": False,
    }
    run["office_review_view"] = {
        "status": "passed",
        "action": "open_office_review",
    }

    view = expert_team_run_view(run)

    assert view["presentation"]["state"] == "completion_reconciling"
    assert view["presentation"]["title"] == "正在恢复交付状态"
    assert view["presentation"]["detail"] == "正在恢复本机交付状态，核验完成前不会显示任务完成。"
    serialized = json.dumps(view["presentation"], ensure_ascii=False)
    for forbidden in ("Office", "WPS", "Word", "企业完成", "企业验收"):
        assert forbidden not in serialized


def test_enterprise_late_state_presentation_keeps_office_acceptance_copy():
    from api.expert_teams.view import _presentation

    awaiting_review = _presentation(
        {
            "workflow_state": "awaiting_review",
            "validation": {"status": "office_acceptance_required"},
            "last_validation_error": "请完成 WPS/Word 验收后再确认交付。",
        },
        {},
    )
    reconciling = _presentation(
        {
            "workflow_state": "completed",
            "completion_transaction_ref": {"transaction_id": "enterprise-transaction"},
            "completion_integrity": {"status": "unverified"},
        },
        {},
    )

    assert awaiting_review["title"] == "阶段成果待复核"
    assert awaiting_review["detail"] == "请完成 WPS/Word 验收后再确认交付。"
    assert reconciling["title"] == "正在恢复交付完成状态"
    assert reconciling["detail"] == "Office 验收证据正在对账恢复，摘要闭合前不会显示企业完成。"


@pytest.mark.parametrize(
    ("run_patch", "expected_state", "expected_event"),
    [
        (
            {
                "workflow_state": "awaiting_review",
                "validation": {"status": "office_acceptance_required"},
                "last_validation_error": "请完成 WPS/Word Office 企业验收后再确认交付。",
            },
            "awaiting_review",
            {
                "type": "local_confirmation_required",
                "title": "等待本机确认",
                "detail": "当前成果已生成，请在本机确认后继续。",
            },
        ),
        (
            {
                "workflow_state": "completed",
                "completion_transaction_ref": {
                    "transaction_id": "stale-enterprise-transaction",
                    "delivery_attempt": 1,
                },
                "completion_integrity": {
                    "status": "unverified",
                    "transaction_state": "committed",
                    "summary_closed": False,
                },
            },
            "completion_reconciling",
            {
                "type": "local_confirmation_required",
                "title": "等待本机确认",
                "detail": "当前成果已生成，请在本机确认后继续。",
            },
        ),
    ],
)
def test_standalone_full_view_neutralizes_legacy_office_timeline_without_losing_history(
    tmp_path,
    run_patch,
    expected_state,
    expected_event,
):
    from api import expert_teams
    from api.expert_teams.view import expert_team_run_view

    run = expert_teams.start_expert_team(tmp_path, _profile_start(session_id=f"timeline-{expected_state}"))
    run.update(run_patch)
    run["timeline_events"] = _legacy_office_timeline()

    view = expert_team_run_view(run)

    assert view["presentation"]["state"] == expected_state
    assert [event["type"] for event in view["timeline_events"]] == [
        "generation_completed",
        expected_event["type"],
        "stage_approved",
    ]
    projected = view["timeline_events"][1]
    assert {key: projected[key] for key in ("type", "title", "detail")} == expected_event
    assert projected["member_id"] == "delivery"
    assert projected["member_name"] == "交付复核专家"
    assert projected["at"] == "2026-07-23T12:01:00+08:00"
    assert [event["at"] for event in view["timeline_events"]] == [
        "2026-07-23T12:00:00+08:00",
        "2026-07-23T12:01:00+08:00",
        "2026-07-23T12:02:00+08:00",
    ]
    serialized = json.dumps(view, ensure_ascii=False)
    for forbidden in ("Office", "WPS", "Word", "企业验收", "office_acceptance_required"):
        assert forbidden not in serialized


def test_standalone_legacy_office_timeline_projection_is_stable_across_run_state(tmp_path):
    from api import expert_teams
    from api.expert_teams.view import expert_team_run_view

    run = expert_teams.start_expert_team(tmp_path, _profile_start(session_id="timeline-stability"))
    run["timeline_events"] = _legacy_office_timeline()
    run["workflow_state"] = "awaiting_review"
    awaiting = expert_team_run_view(run)

    run["workflow_state"] = "completed"
    run["completion_transaction_ref"] = {
        "transaction_id": "stale-enterprise-transaction",
        "delivery_attempt": 1,
    }
    run["completion_integrity"] = {
        "status": "unverified",
        "transaction_state": "committed",
        "summary_closed": False,
    }
    reconciling = expert_team_run_view(run)

    assert awaiting["presentation"]["state"] == "awaiting_review"
    assert reconciling["presentation"]["state"] == "completion_reconciling"
    assert awaiting["timeline_events"] == reconciling["timeline_events"]
    assert [event["at"] for event in awaiting["timeline_events"]] == [
        "2026-07-23T12:00:00+08:00",
        "2026-07-23T12:01:00+08:00",
        "2026-07-23T12:02:00+08:00",
    ]


def test_catalog_import_strategy_is_deterministic_and_direct_load_uses_current_worktree():
    import ast
    import importlib.util
    from pathlib import Path

    from api.expert_teams import launch_profiles

    repo_root = Path(__file__).resolve().parents[1]
    catalog_path = repo_root / "api" / "expert_teams" / "catalog.py"
    source = catalog_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert not any(
        isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "ImportError"
        for node in ast.walk(tree)
    )
    assert "if __package__:" in source

    spec = importlib.util.spec_from_file_location("_expert_team_catalog_direct_load", catalog_path)
    assert spec and spec.loader
    catalog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog)

    assert Path(catalog.__file__).resolve() == catalog_path
    assert Path(launch_profiles.__file__).resolve() == catalog_path.with_name("launch_profiles.py")
    assert catalog.CONTENT_PHASES is launch_profiles.CONTENT_PHASES


@pytest.mark.parametrize(
    "run_patch",
    [
        {
            "workflow_state": "awaiting_review",
            "validation": {"status": "office_acceptance_required"},
        },
        {
            "workflow_state": "completed",
            "completion_transaction_ref": {"transaction_id": "enterprise-transaction"},
            "completion_integrity": {"status": "unverified"},
        },
    ],
)
def test_enterprise_full_view_keeps_legacy_office_timeline(run_patch, tmp_path):
    from api import expert_teams
    from api.expert_teams.view import expert_team_run_view

    run = expert_teams.start_expert_team(tmp_path, _profile_start(session_id="enterprise-timeline-control"))
    run.pop("product_mode")
    run["schema_version"] = 2
    run.update(run_patch)
    run["timeline_events"] = _legacy_office_timeline()

    view = expert_team_run_view(run)

    projected = view["timeline_events"][1]
    assert projected["type"] == "office_acceptance_required"
    assert projected["title"] == "需完成 WPS/Word 验收"
    assert projected["detail"] == "最终交付包自动校验完成，请完成 Office 企业验收。"
    assert projected["member_id"] == "delivery"
    assert projected["at"] == "2026-07-23T12:01:00+08:00"


def test_legacy_run_remains_readable_after_standalone_contract_is_added(tmp_path):
    from api import expert_teams

    created = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "legacy-session",
            "team_id": "content-creator-team",
            "prompt": "起草旧版工作汇报",
        },
    )
    reopened = expert_teams.read_expert_team_run(tmp_path, created["run_id"])

    assert reopened["schema_version"] == 2
    assert reopened["run_id"] == created["run_id"]
    assert reopened["view"]["capability"]["kind"] == "legacy"
