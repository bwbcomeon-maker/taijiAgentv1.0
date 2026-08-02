# 专家团分支范围审计

> 审计日期：2026-08-02。比较范围：本地 `main...codex/expert-team-standalone-core`，已复验的实现候选 HEAD `8d49fae72a8f8ae9d35b4de8faca778ffbdd3a86`；本文更新前 Draft PR #5 的审查候选 HEAD 为 `6401d49b685ea2a1903b240a9c71e3e18f99961b`。当前比较范围仍为 182 个唯一路径、51 个提交；实现候选之后只增加验收文档与 CHANGELOG，本次审计更新将另形成不新增路径的文档 checkpoint。路径分类用于审查导航，不替代逐行代码审查。

## 结论

- 专家团直接实现：38 个路径。
- 专家团必要基础设施：37 个路径。
- 测试、模板与文档：107 个路径。
- 无关变更：0 个路径。
- 当前未发现应从专家团成果排除的无关路径；若 PR 审查发现类别归属不成立，应在原分支移除后重新跑门禁。
- 本轮重新计算 `git diff --name-only main...HEAD` 仍为 182 个唯一路径，与下方三类合计一致；`git diff --check`、98 个改动 Python 文件语法和 27 个改动 JavaScript 文件语法均通过。
- 提交内容敏感模式扫描只命中安全脱敏回归使用的虚构令牌，以及“不得出现本机路径”的负向断言；未发现真实 Provider 凭据、用户生成 DOCX、QA 截图或运行日志进入分支。
- Draft PR #5 已创建并添加 `full-ci`；本文更新前 HEAD `6401d49b685ea2a1903b240a9c71e3e18f99961b` 的权威运行 `30742844842` 中 `CI Gate` 及全部分项均为绿色。较早运行 `30742844806` 是同一 PR 重复触发后被并发策略取消，不作为代码失败。

## 必要基础设施不可分离原因

- Agent transport、system prompt 与 gateway：承载专家团阶段协议、Provider 观察上限和结构化结果返回；缺失会使 WebUI 合同无法落到真实模型执行。
- Session、truth rewrite、streaming 与 turn envelope：提供原子启动、跨进程 CAS、幂等恢复和协议流隔离；缺失会重新引入重复权威 attempt 或原始协议泄漏。
- 共享产品错误、隐私投影、routes 与 updates：把内部错误映射为可执行中文动作，并保证诊断不泄露内部路径或 Provider 原文。
- DOCX Engine 公共层：执行 required sections、standalone 模板选择、Markdown 纯净化、交付哈希和质量报告校验；缺失不能形成独立可核验 DOCX。
- 桌面启动链与共享前端壳：保证 linked worktree 来源绑定、固定工作台入口、会话恢复和浏览器/桌面交付动作分流。

## 专家团直接实现（38）

- `hermes-local-lab/sources/hermes-webui/api/expert_teams/__init__.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/catalog.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/contracts.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/delivery_integrity.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/document_capabilities.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/documents.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/error_projection.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/issue_policy.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/launch.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/launch_profiles.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/launch_storage.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/materials.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/prompts.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/source_context.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/stage_artifacts.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/standalone_delivery.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/storage.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/system_stages.py`
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/view.py`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/content-delivery-reviewer.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/content-director.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/content-material-organizer.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/content-reviewer.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/content-writer.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/research-director.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/research-evidence-verifier.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/research-final-reviewer.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/research-planner.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/research-structure-analyst.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/research-writer.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/team-content-cover.png`
- `hermes-local-lab/sources/hermes-webui/static/assets/taiji/expert-teams/team-research-cover.png`
- `hermes-local-lab/sources/hermes-webui/static/expert-team-actions.js`
- `hermes-local-lab/sources/hermes-webui/static/expert-team-presenter.js`
- `hermes-local-lab/sources/hermes-webui/static/expert-team-v3.css`
- `hermes-local-lab/sources/hermes-webui/static/expert-team-v3.js`
- `hermes-local-lab/sources/hermes-webui/static/taiji-home.js`

## 专家团必要基础设施（37）

- `hermes-local-lab/启动太极Agent桌面端.app/Contents/MacOS/taiji-agent-desktop-launcher`
- `hermes-local-lab/启动太极Agent桌面端.command`
- `hermes-local-lab/sources/docx-engine-v2/src/domain/schemas.js`
- `hermes-local-lab/sources/docx-engine-v2/src/domain/section-anchors.js`
- `hermes-local-lab/sources/docx-engine-v2/src/rendering/postprocess-docx.js`
- `hermes-local-lab/sources/docx-engine-v2/src/source/normalize-markdown.js`
- `hermes-local-lab/sources/docx-engine-v2/src/validation/validate-delivery-package.js`
- `hermes-local-lab/sources/docx-engine-v2/src/workflow/run-document-job.js`
- `hermes-local-lab/sources/docx-engine-v2/tools/build-standalone-templates.py`
- `hermes-local-lab/sources/hermes-agent/agent/agent_init.py`
- `hermes-local-lab/sources/hermes-agent/agent/anthropic_adapter.py`
- `hermes-local-lab/sources/hermes-agent/agent/chat_completion_helpers.py`
- `hermes-local-lab/sources/hermes-agent/agent/conversation_loop.py`
- `hermes-local-lab/sources/hermes-agent/agent/system_prompt.py`
- `hermes-local-lab/sources/hermes-agent/agent/transports/anthropic.py`
- `hermes-local-lab/sources/hermes-agent/agent/transports/chat_completions.py`
- `hermes-local-lab/sources/hermes-agent/agent/transports/codex.py`
- `hermes-local-lab/sources/hermes-agent/gateway/platforms/api_server.py`
- `hermes-local-lab/sources/hermes-agent/run_agent.py`
- `hermes-local-lab/sources/hermes-webui/api/artifacts.py`
- `hermes-local-lab/sources/hermes-webui/api/brand_privacy.py`
- `hermes-local-lab/sources/hermes-webui/api/gateway_chat.py`
- `hermes-local-lab/sources/hermes-webui/api/helpers.py`
- `hermes-local-lab/sources/hermes-webui/api/models.py`
- `hermes-local-lab/sources/hermes-webui/api/product_contract.py`
- `hermes-local-lab/sources/hermes-webui/api/routes.py`
- `hermes-local-lab/sources/hermes-webui/api/runtime_adapter.py`
- `hermes-local-lab/sources/hermes-webui/api/streaming.py`
- `hermes-local-lab/sources/hermes-webui/api/truth_rewrite.py`
- `hermes-local-lab/sources/hermes-webui/api/turn_envelope.py`
- `hermes-local-lab/sources/hermes-webui/api/updates.py`
- `hermes-local-lab/sources/hermes-webui/static/commands.js`
- `hermes-local-lab/sources/hermes-webui/static/messages.js`
- `hermes-local-lab/sources/hermes-webui/static/panels.js`
- `hermes-local-lab/sources/hermes-webui/static/sessions.js`
- `hermes-local-lab/sources/hermes-webui/static/style.css`
- `hermes-local-lab/sources/hermes-webui/static/ui.js`

## 测试、模板与文档（107）

- `apps/taiji-desktop/tests/source-provenance-launcher.test.js`
- `docs/reviews/expert-team-branch-scope-audit-2026-08-01.md`
- `docs/reviews/expert-team-required-sections-ux-qa-2026-07-26.md`
- `docs/reviews/expert-team-sale-ready-ux-qa-2026-07-30.md`
- `docs/superpowers/plans/2026-07-23-expert-team-standalone-redesign.md`
- `docs/superpowers/plans/2026-07-27-worktree-finder-launcher.md`
- `docs/superpowers/plans/2026-07-28-expert-team-all-content-tasks.md`
- `docs/superpowers/plans/2026-07-30-expert-team-sale-ready-delivery.md`
- `docs/superpowers/specs/2026-07-27-worktree-finder-launcher-design.md`
- `docs/superpowers/specs/2026-07-28-expert-team-all-content-tasks-design.md`
- `hermes-local-lab/sources/docx-engine-v2/template-registry.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/adapter-sample.render-plan.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/data-adapter.js`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/manifest.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/prompt.md`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/sample.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/schema.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/template-package.binding.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/template.docx`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/adapter-sample.render-plan.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/data-adapter.js`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/manifest.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/prompt.md`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/sample.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/schema.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/template-package.binding.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/template.docx`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-research-report/adapter-sample.render-plan.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-research-report/data-adapter.js`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-research-report/manifest.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-research-report/prompt.md`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-research-report/sample.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-research-report/schema.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-research-report/template-package.binding.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-research-report/template.docx`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-work-report/adapter-sample.render-plan.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-work-report/data-adapter.js`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-work-report/manifest.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-work-report/prompt.md`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-work-report/sample.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-work-report/schema.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-work-report/template-package.binding.json`
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-work-report/template.docx`
- `hermes-local-lab/sources/docx-engine-v2/tests/delivery-validation.test.js`
- `hermes-local-lab/sources/docx-engine-v2/tests/run-job-contract.test.js`
- `hermes-local-lab/sources/docx-engine-v2/tests/source-normalization.test.js`
- `hermes-local-lab/sources/docx-engine-v2/tests/standalone-template-contract.test.js`
- `hermes-local-lab/sources/docx-engine-v2/tests/template-data-adapter.test.js`
- `hermes-local-lab/sources/docx-engine-v2/tests/template-package.test.js`
- `hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server.py`
- `hermes-local-lab/sources/hermes-agent/tests/run_agent/test_run_agent.py`
- `hermes-local-lab/sources/hermes-agent/tests/test_exact_system_prompt_contract.py`
- `hermes-local-lab/sources/hermes-webui/CHANGELOG.md`
- `hermes-local-lab/sources/hermes-webui/tests/expert_team_v3_electron_smoke.js`
- `hermes-local-lab/sources/hermes-webui/tests/fixtures/expert_team_start/early_v1_prepared.json`
- `hermes-local-lab/sources/hermes-webui/tests/test_auth_session_persistence.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_auth_sessions.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_background_title_write_conflict.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_brief_capabilities.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_capability_registry.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_cross_process_cas.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_frontend.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_integrity_hardening.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_enterprise_prompt_contract.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_error_projection.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v2.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_issue_policy.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_launch_atomicity.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_launch_frontend.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_protocol_authority.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_required_sections_contract.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_rollout_gate.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_semantic_delivery_contract.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_source_context_contract.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_stage_artifact_contract.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_contract.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_delivery_actions.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_delivery_contract.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_provider_execution.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_state_machine.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_template_selection.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_templates.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_start_atomicity.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_v2_runtime.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_visual_assets.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_zero_source_contract.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_issue765_streaming_persistence.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_issue2223_compression_no_rename.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_issue3256_context_length_default_only_guard.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_memory_session_lifecycle_generation.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_metadata_save_wipe_1558.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_notify_on_complete_webui.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_phase2_gateway_context_persistence.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_phase2_session_lifecycle.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_product_error_mapping.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_regressions.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_session_index.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_taiji_license_routes.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_taiji_recent_controls.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_updates.py`
- `hermes-local-lab/sources/hermes-webui/tests/test_writeflow_frontend.py`
- `tests/test_canonical_account_home.py`
- `tests/test_canonical_main_source_gate.py`

## 无关变更（0）

- 无。

## 审查使用方式

1. 先审专家团直接实现，确认七类合同、状态机、恢复动作和用户投影。
2. 再按上面的不可分离理由审必要基础设施，重点检查共享能力是否保持非专家团兼容。
3. 最后核对测试、模板与文档是否与实现一一对应，以及是否存在生成物、用户资料、密钥或本机路径。
4. PR 合并前重新计算路径集合；任何新增路径必须先加入本表分类。
