import json


def _answer_all_required(expert_teams, tmp_path, run):
    return expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "topic": "内部通知，主题是近期安全生产专项检查安排",
                "audience": "公司各部门、各基层单位",
                "boundary": "正式、简洁，包含检查范围、时间节点、责任分工和报送要求",
            },
        },
    )


def test_catalog_defaults_to_office_material_teams_only():
    from api import expert_teams

    data = expert_teams.expert_team_catalog()

    assert [team["id"] for team in data["teams"]] == ["content-creator-team", "deep-research-team"]
    payload = json.dumps(data, ensure_ascii=False)
    for text in ("公众号长文", "文章大纲", "标题党", "读者", "封面配图", "发布前检查"):
        assert text not in payload
    assert "工作汇报" in payload
    assert "会议纪要" in payload
    assert "通知通报" in payload


def test_start_run_uses_collecting_required_presentation(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-new",
            "team_id": "content-creator-team",
            "prompt": "帮我起草一份内部通知，主题是近期安全生产专项检查安排",
        },
    )

    assert run["workflow_state"] == "collecting_required"
    assert run["status"] == "awaiting_user"
    assert run["view"]["business_context"]["material_type"] == "notice"
    assert run["view"]["business_context"]["visible_title"] == "起草通知通报初稿"
    assert run["view"]["presentation"]["state"] == "collecting_required"
    assert run["view"]["presentation"]["primary_action"] == {
        "id": "answer_required",
        "label": "去确认",
        "kind": "question_popover",
    }
    assert run["view"]["timeline_events"][0]["type"] == "team_created"
    assert any(event["type"] == "member_joined" for event in run["view"]["timeline_events"])
    assert any(member.get("image") for member in run["members"])
    assert run["tasks"][0]["id"] == "plan"
    assert run["tasks"][0]["title"] == "专家团计划"


def test_required_complete_moves_to_collecting_optional_not_generating(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-optional", "team_id": "content-creator-team", "prompt": "帮我起草工作汇报"},
    )
    updated = _answer_all_required(expert_teams, tmp_path, run)

    assert updated["workflow_state"] == "collecting_optional"
    assert updated["status"] == "awaiting_user"
    assert updated["execution_status"] == "idle"
    assert updated["view"]["presentation"]["state"] == "collecting_optional"
    assert updated["view"]["presentation"]["primary_action"]["id"] == "answer_optional"


def test_optional_skip_is_the_only_empty_answer_that_starts_generation(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-skip", "team_id": "content-creator-team", "prompt": "帮我起草工作汇报"},
    )
    pending_optional = _answer_all_required(expert_teams, tmp_path, run)

    still_pending = expert_teams.answer_expert_team(
        tmp_path,
        {"run_id": pending_optional["run_id"], "answers": {"optional_context": ""}},
    )
    assert still_pending["workflow_state"] == "collecting_optional"
    assert still_pending["questions"][-1]["status"] == "pending"

    ready = expert_teams.answer_expert_team(
        tmp_path,
        {"run_id": pending_optional["run_id"], "answers": {"optional_context": ""}, "skip_optional": True},
    )
    assert ready["workflow_state"] == "ready_to_generate"
    assert ready["status"] == "awaiting_user"
    assert ready["execution_status"] == "idle"
    assert ready["questions"][-1]["status"] == "skipped"
    assert ready["view"]["presentation"]["state"] == "ready_to_generate"
    assert ready["view"]["presentation"]["primary_action"]["id"] == "start_generation"


def test_generating_presentation_has_single_running_state(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-running", "team_id": "content-creator-team", "prompt": "帮我起草工作汇报"},
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": _answer_all_required(expert_teams, tmp_path, run)["run_id"],
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )
    generating = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        ready["run_id"],
        {"stream_id": "stream-1", "pending_user_message": "专家团开始生成"},
    )

    presentation = generating["view"]["presentation"]
    assert generating["workflow_state"] == "generating"
    assert generating["execution_status"] == "running"
    assert presentation["state"] == "generating"
    assert presentation["primary_action"] == {"id": "cancel", "label": "停止生成", "kind": "danger"}
    assert "未检测到结果" not in json.dumps(presentation, ensure_ascii=False)
    assert "阶段成果待复核" not in json.dumps(presentation, ensure_ascii=False)
    assert generating["view"]["workspace"]["visible"] is True
    assert generating["view"]["workspace"]["current_worker"]["name"] == "写作总导演"
    assert generating["view"]["team"]["members"][0]["name"] == "写作总导演"
    assert generating["view"]["workflow"]["stages"][0]["id"] == "plan"
    assert generating["view"]["workflow"]["progress"]["total"] == 5


def test_requirements_are_prestep_and_workflow_progress_is_catalog_driven(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-progress", "team_id": "content-creator-team", "prompt": "帮我起草一份部门月度工作汇报"},
    )

    view = run["view"]
    assert view["presentation"]["state"] == "collecting_required"
    assert view["presentation"]["progress_text"] == "0/5"
    assert view["workflow"]["progress"]["done"] == 0
    assert view["workflow"]["progress"]["total"] == 5
    assert view["workflow"]["progress"]["current_index"] == 0
    assert view["workflow"]["progress"]["is_intake"] is True
    assert view["workspace"]["current_stage"]["id"] == "plan"

    research = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-progress-research",
            "team_id": "deep-research-team",
            "prompt": "帮我研究本地优先 AI 助理在企业内部办公场景的落地趋势",
        },
    )
    assert research["view"]["team"]["id"] == "deep-research-team"
    assert len(research["view"]["team"]["members"]) == len(research["members"])
    assert len(research["view"]["workflow"]["stages"]) == len(research["tasks"])
    assert research["view"]["workflow"]["progress"]["total"] == len(research["tasks"])
    assert research["view"]["presentation"]["progress_text"] == f"0/{len(research['tasks'])}"
    assert len(run["view"]["team"]["members"]) == 5
    assert len(run["view"]["workflow"]["stages"]) == 5
    assert len(research["view"]["team"]["members"]) == 6
    assert len(research["view"]["workflow"]["stages"]) == 6


def test_invalid_generation_does_not_mix_result_running_and_missing_states(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-invalid", "team_id": "content-creator-team", "prompt": "帮我起草工作汇报"},
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": _answer_all_required(expert_teams, tmp_path, run)["run_id"],
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )
    generating = expert_teams.mark_expert_team_execution_started(tmp_path, ready["run_id"], {"stream_id": "stream-2"})
    invalid = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        generating["run_id"],
        delivery={
            "id": "delivery-invalid",
            "kind": "chat",
            "content": "标题：你有没有遇到过这些问题\n\n【开篇】这是一篇公众号长文。",
        },
    )

    presentation = invalid["view"]["presentation"]
    assert invalid["workflow_state"] == "generated_invalid"
    assert presentation["state"] == "generated_invalid"
    assert presentation["primary_action"]["id"] == "regenerate"
    joined = json.dumps(presentation, ensure_ascii=False)
    assert "正在生成" not in joined
    assert "未检测到结果" not in joined
    assert "阶段成果待复核" not in joined


def test_valid_generation_registers_structured_output_and_review_action(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-valid", "team_id": "content-creator-team", "prompt": "帮我起草一份部门月度工作汇报"},
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": _answer_all_required(expert_teams, tmp_path, run)["run_id"],
            "answers": {"optional_context": "重点包含迎峰度夏保供电工作。"},
            "skip_optional": False,
        },
    )
    generating = expert_teams.mark_expert_team_execution_started(tmp_path, ready["run_id"], {"stream_id": "stream-3"})
    completed = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        generating["run_id"],
        delivery={
            "id": "delivery-valid",
            "kind": "chat",
                "content": (
                    "阶段摘要：已形成专家团执行计划。\n"
                    "正文草稿：本阶段不直接起草完整正文，先确认材料定位、使用对象、结构边界、素材缺口和后续分工。"
                    "计划安排为先形成工作汇报初稿，再进行材料打磨，最后完成交付复核，过程中保留待人工确认的数据口径。\n"
                    "待补充事项：请补充具体数据、典型成效和下月重点工作安排。\n"
                    "建议下一步：进入生成初稿。"
                ),
        },
    )

    output = completed["view"]["stage_review"]["output"]
    assert completed["workflow_state"] == "awaiting_review"
    assert completed["view"]["presentation"]["state"] == "awaiting_review"
    assert completed["view"]["presentation"]["primary_action"]["id"] == "review_stage"
    assert output["visible_title"] == "专家团计划"
    assert completed["view"]["stage_result"]["stage_id"] == "plan"
    assert completed["view"]["stage_result"]["worker_id"] == "director"
    assert "受众" not in completed["validation"].get("violations", [])
    assert output["locator"] == "chat"
    assert output["has_long_content"] is True


def test_stage_input_pause_is_single_right_workspace_confirmation(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-stage-input", "team_id": "content-creator-team", "prompt": "帮我起草内部通知"},
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": _answer_all_required(expert_teams, tmp_path, run)["run_id"],
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )
    generating = expert_teams.mark_expert_team_execution_started(tmp_path, ready["run_id"], {"stream_id": "stream-stage-input"})
    paused = expert_teams.request_expert_team_stage_input(
        tmp_path,
        {
            "run_id": generating["run_id"],
            "question": "本次通知是否需要隐去具体部门或人员名称？",
            "description": "资料整理专家需要确认脱敏口径后继续当前阶段。",
            "options": ["不需要隐去", "需要隐去，使用代号"],
        },
    )

    view = paused["view"]
    assert paused["workflow_state"] == "awaiting_stage_input"
    assert paused["execution_status"] == "paused"
    assert view["presentation"]["state"] == "awaiting_stage_input"
    assert view["presentation"]["primary_action"] == {
        "id": "submit_stage_input",
        "label": "确认并继续生成",
        "kind": "primary",
    }
    assert view["primary_confirmation"]["type"] == "stage_input"
    assert view["pending_input"]["question"] == "本次通知是否需要隐去具体部门或人员名称？"
    assert view["workspace"]["pending_input"]["question"] == view["pending_input"]["question"]
    assert view["stage_review"]["actionable"] is False
    assert view["actions"]["can_submit_stage_input"] is True
    assert "草稿未通过校验" not in json.dumps(view, ensure_ascii=False)
    assert "阶段成果待复核" not in json.dumps(view["presentation"], ensure_ascii=False)

    resumed = expert_teams.submit_expert_team_stage_input(
        tmp_path,
        {
            "run_id": paused["run_id"],
            "answer": "需要隐去，使用代号",
            "note": "涉及客户名称全部使用 A 客户、B 客户代称。",
        },
    )
    assert resumed["workflow_state"] == "ready_to_generate"
    assert resumed["current_stage"]["index"] == paused["current_stage"]["index"]
    assert resumed.get("pending_input") in (None, {})
    assert resumed["stage_inputs"][-1]["answer"] == "需要隐去，使用代号"


def test_final_stage_review_action_completes_task_not_next_stage(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-final-review", "team_id": "content-creator-team", "prompt": "帮我起草工作汇报"},
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": _answer_all_required(expert_teams, tmp_path, run)["run_id"],
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )
    data = ready
    last_index = data["view"]["phase_progress"]["total"] - 1
    for index in range(last_index):
        generated = expert_teams.mark_expert_team_execution_complete(
            tmp_path,
            data["run_id"],
            delivery={
                "id": f"stage-{index}",
                "kind": "chat",
                "content": (
                    "阶段摘要：完成当前阶段。\n"
                    "正文草稿：标题：工作汇报\n\n"
                    "一、工作开展情况\n本阶段按专家分工完成阶段产物，未越阶段输出最终稿。\n\n"
                    "二、存在问题\n部分素材仍需人工补充确认。\n\n"
                    "三、下一步工作安排\n继续按阶段推进材料整理、初稿撰写和复核交付。\n"
                    "待补充事项：无。\n"
                    "建议下一步：进入下一阶段。"
                ),
            },
        )
        data = expert_teams.approve_expert_team_stage(tmp_path, {"run_id": generated["run_id"]})

    assert data["current_stage"]["index"] == last_index
    final_review = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        data["run_id"],
        delivery={
            "id": "final-stage",
            "kind": "chat",
            "content": (
                "阶段摘要：完成交付确认。\n"
                "正文草稿：标题：工作汇报\n\n"
                "一、工作开展情况\n重点工作已经按计划推进。\n\n"
                "二、存在问题\n个别数据仍需人工确认。\n\n"
                "三、下一步工作安排\n持续推进问题闭环和成果沉淀。\n"
                "待补充事项：无。\n"
                "建议下一步：完成任务。"
            ),
        },
    )

    actions = final_review["view"]["presentation"]["secondary_actions"]
    approve = next(action for action in actions if action["id"] == "approve_stage")
    assert final_review["workflow_state"] == "awaiting_review"
    assert approve["label"] == "无修改，完成任务"
    assert "进入下一阶段" not in json.dumps(actions, ensure_ascii=False)


def test_plan_stage_allows_audience_terms_and_waits_for_review(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-plan-validation", "team_id": "content-creator-team", "prompt": "帮我起草一份部门月度工作汇报"},
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": _answer_all_required(expert_teams, tmp_path, run)["run_id"],
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )

    completed = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        ready["run_id"],
        delivery={
            "id": "plan-with-audience",
            "kind": "chat",
            "content": (
                "阶段摘要：写作总导演完成流程安排。\n"
                "正文草稿：本阶段只形成执行计划。材料受众为部门领导，阅读对象关注工作进展、问题和下一步安排。"
                "后续由资料整理、初稿撰写、审稿打磨、交付确认四个阶段逐步完成。\n"
                "待补充事项：请补充具体月份、关键数据和典型问题。\n"
                "建议下一步：确认计划后进入素材整理。"
            ),
        },
    )

    assert completed["workflow_state"] == "awaiting_review"
    assert completed["validation"]["status"] == "pass"
    assert completed["view"]["workspace"]["current_stage"]["id"] == "plan"
    assert completed["view"]["workspace"]["current_worker"]["name"] == "写作总导演"


def test_deep_research_view_uses_research_specific_workspace_copy(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-research",
            "team_id": "deep-research-team",
            "prompt": "帮我研究本地优先 AI 助理在企业内部办公场景的落地趋势",
        },
    )

    payload = json.dumps(run["view"], ensure_ascii=False)
    assert run["view"]["business_context"]["material_type"] == "research_report"
    assert run["view"]["business_context"]["visible_title"] == "梳理专题研究材料"
    assert "起草办公材料初稿" not in payload
    assert run["view"]["workspace"]["current_worker"]["name"] == "研究总导演"


def test_stage_approval_advances_until_final_completed(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-approve", "team_id": "content-creator-team", "prompt": "帮我起草工作汇报"},
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": _answer_all_required(expert_teams, tmp_path, run)["run_id"],
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )
    first = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        ready["run_id"],
        delivery={"id": "d1", "kind": "chat", "content": "阶段摘要：完成。\n正文草稿：标题：工作汇报\n一、工作开展情况\n二、存在问题\n三、下一步工作安排\n待补充事项：无\n建议下一步：打磨。"},
    )

    next_stage = expert_teams.approve_expert_team_stage(tmp_path, {"run_id": first["run_id"]})
    assert next_stage["workflow_state"] == "ready_to_generate"
    assert next_stage["current_stage"]["index"] == 1

    run_id = next_stage["run_id"]
    remaining = next_stage["view"]["phase_progress"]["total"] - next_stage["current_stage"]["index"]
    for _ in range(remaining):
        done = expert_teams.mark_expert_team_execution_complete(
            tmp_path,
            run_id,
            delivery={
                "id": "d",
                "kind": "chat",
                "content": (
                    "阶段摘要：完成。\n"
                    "正文草稿：标题：工作汇报\n\n"
                    "一、工作开展情况\n已按计划推进重点任务。\n\n"
                    "二、存在问题\n部分数据仍需补充。\n\n"
                    "三、下一步工作安排\n继续完善台账并闭环推进。\n"
                    "待补充事项：无\n建议下一步：继续。"
                ),
            },
        )
        approved = expert_teams.approve_expert_team_stage(tmp_path, {"run_id": done["run_id"]})
        run_id = approved["run_id"]

    assert approved["workflow_state"] == "completed"
    assert approved["view"]["presentation"]["state"] == "completed"
    assert approved["view"]["presentation"]["primary_action"]["id"] == "view_result"


def test_plan_like_draft_prompt_names_rich_draft_acceptance_gate(tmp_path):
    from api import expert_teams, routes

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-rich-draft-prompt",
            "team_id": "content-creator-team",
            "prompt": "帮我起草一份方案说明，主题是提升营业厅服务质效专项行动。",
        },
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "topic": "方案说明，主题是提升营业厅服务质效专项行动",
                "audience": "公司分管领导和营销服务部门，用于专项行动部署",
                "boundary": "正式方案口径，包含目标、任务、进度、责任分工和保障机制",
            },
        },
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": ready["run_id"],
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )
    planned = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        ready["run_id"],
        delivery={
            "id": "plan-stage",
            "kind": "chat",
            "content": (
                "阶段摘要：已确认方案说明的编制路径和阶段分工。\n"
                "正文草稿：本阶段只形成执行计划，不直接起草完整正文。\n"
                "待补充事项：请确认营业厅清单、办理量和投诉问题台账。\n"
                "建议下一步：进入素材整理。"
            ),
        },
    )
    materials = expert_teams.approve_expert_team_stage(tmp_path, {"run_id": planned["run_id"]})
    material_output = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        materials["run_id"],
        delivery={
            "id": "materials-stage",
            "kind": "chat",
            "content": "阶段摘要：已整理行动目标、问题台账、责任单位和进度节点。\n待补充事项：数据口径待人工确认。",
        },
    )
    draft_ready = expert_teams.approve_expert_team_stage(tmp_path, {"run_id": material_output["run_id"]})

    assert draft_ready["current_stage"]["task_id"] == "draft"
    prompt = routes._expert_team_execution_prompt(draft_ready)

    assert "富内容初稿" in prompt
    assert "至少 2 个 Markdown 表格" in prompt
    assert "至少 1 个架构图、流程图、用例图或图示引用" in prompt
    assert "不得只输出普通段落或下一阶段建议" in prompt


def test_deep_research_draft_prompt_names_rich_draft_acceptance_gate(tmp_path):
    from api import expert_teams, routes

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-research-rich-draft-prompt",
            "team_id": "deep-research-team",
            "prompt": "帮我研究本地优先 AI 助理在企业内部办公场景的落地趋势",
        },
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "research_topic": "本地优先 AI 助理在企业内部办公场景的落地趋势",
                "audience_goal": "面向企业中高层，用于判断内部办公智能体落地路径",
                "source_boundary": "优先公开研究、企业案例和可验证数据，避免消费级 AI 科普",
            },
        },
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {"run_id": ready["run_id"], "answers": {"optional_context": ""}, "skip_optional": True},
    )
    data = ready
    for stage in ("direction", "research", "evidence", "outline"):
        generated = expert_teams.mark_expert_team_execution_complete(
            tmp_path,
            data["run_id"],
            delivery={
                "id": f"{stage}-stage",
                "kind": "chat",
                "content": (
                    "阶段摘要：当前研究阶段已经完成。\n"
                    "阶段产物：已整理核心问题、案例素材、事实核验项和结构安排。\n"
                    "待人工补充事项：请补充内部部署样本和可披露案例。\n"
                    "下一阶段建议：继续推进。"
                ),
            },
        )
        data = expert_teams.approve_expert_team_stage(tmp_path, {"run_id": generated["run_id"]})

    assert data["current_stage"]["task_id"] == "draft"
    prompt = routes._expert_team_execution_prompt(data)

    assert "富内容初稿" in prompt
    assert "至少 2 个 Markdown 表格" in prompt
    assert "至少 1 个架构图、流程图、用例图或图示引用" in prompt
    assert "不得只输出普通段落或下一阶段建议" in prompt


def test_new_runtime_does_not_emit_legacy_confirmation_or_writeflow_fields(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-clean", "team_id": "content-creator-team", "prompt": "帮我起草工作汇报"},
    )

    payload = json.dumps(run, ensure_ascii=False)
    assert "stage_confirmation_points" not in payload
    assert "expert_team_from_writeflow_run" not in dir(expert_teams)
    assert run.get("source") != "writeflow"
