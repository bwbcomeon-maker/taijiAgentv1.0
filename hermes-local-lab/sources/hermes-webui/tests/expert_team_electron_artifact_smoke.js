#!/usr/bin/env node
/*
 * Electron smoke for expert-team Plan A: right-side workbench, no bottom dock.
 */
const fs = require("fs");
const path = require("path");

function parseArgs(argv) {
  const args = { outDir: "" };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--out-dir") args.outDir = argv[index + 1] || "";
  }
  if (argv.includes("--out-dir") && !args.outDir) {
    throw new Error("Electron smoke preflight failed: --out-dir requires a directory path");
  }
  return args;
}

function loadPlaywright() {
  const moduleId = process.env.PLAYWRIGHT_NODE_PATH || "playwright";
  try {
    require.resolve(moduleId);
    return require(moduleId);
  } catch (error) {
    throw new Error(
      `Electron smoke preflight failed: cannot resolve Playwright from ${moduleId}. ` +
      "Set PLAYWRIGHT_NODE_PATH to the installed Playwright module directory.",
      { cause: error }
    );
  }
}

const cli = parseArgs(process.argv.slice(2));
const { _electron } = loadPlaywright();

const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
const appDir = path.join(repoRoot, "apps", "taiji-desktop");
const labDir = path.join(repoRoot, "hermes-local-lab");
const electronBin = path.join(appDir, "node_modules", "electron", "dist", "Electron.app", "Contents", "MacOS", "Electron");
const outDir = path.resolve(cli.outDir || path.join(repoRoot, "output", "playwright"));

function assertState(condition, message, detail) {
  if (!condition) throw new Error(`${message}${detail ? `\n${JSON.stringify(detail, null, 2)}` : ""}`);
}

async function prepareDesktopSession(page, workspace) {
  await page.evaluate(async ({ workspace }) => {
    document.documentElement.dataset.taijiDesktop = "1";
    document.documentElement.dataset.skin = "taiji-light-glass";
    const response = await fetch("/api/session/new", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace }),
    });
    if (!response.ok) throw new Error(`session/new failed ${response.status}: ${await response.text()}`);
    const payload = await response.json();
    S.session = payload.session;
    S.messages = [];
    if (typeof renderMessages === "function") renderMessages();
    if (typeof switchPanel === "function") await switchPanel("chat");
    const onboarding = document.getElementById("onboardingOverlay");
    if (onboarding) onboarding.remove();
  }, { workspace });
}

function runFixture(sessionId, state, overrides = {}) {
  const reviewItemCount = Number(overrides.reviewItemCount || 1);
  const teamId = String(overrides.team_id || "content-creator-team");
  const isResearchTeam = teamId === "deep-research-team";
  const stageIndex = {
    collecting_required: 0,
    collecting_optional: 0,
    ready_to_generate: 0,
    generating: 1,
    awaiting_stage_input: 1,
    generated_invalid: 1,
    awaiting_review: 1,
    completed: 4,
  }[state] ?? 0;
  const outputContent = [
    "阶段摘要：资料整理专家已完成本阶段素材梳理。",
    "",
    "正文草稿：关于迎峰度夏保供电重点工作推进情况的部门月度工作汇报",
    "",
    "开头概述：本月围绕迎峰度夏保供电重点任务，持续推进负荷预测、隐患治理、应急保障和客户服务协同。",
    "",
    "一、工作开展情况",
    "1. 加强设备巡视与风险排查。",
    "2. 推进重点线路隐患整治。",
    "3. 完善应急值守和抢修联动。",
    "",
    "二、存在问题",
    "部分台区负荷增长较快，个别跨部门事项还需进一步压实责任。",
    "",
    "三、下一步工作安排",
    "持续跟踪高温天气变化，细化保供电措施，按周闭环重点问题。",
    "",
    "待补充事项：请补充具体月份、关键指标和责任部门。",
  ].join("\n");
  const output = {
    id: "delivery-office-material",
    kind: "chat",
    title: "起草工作汇报初稿",
    visible_title: "起草工作汇报初稿",
    summary: "当前阶段已形成工作汇报素材摘要，包含工作开展、问题和下一步安排。",
    preview: "关于迎峰度夏保供电重点工作推进情况的部门月度工作汇报。",
    content: outputContent,
    content_length: outputContent.length,
    has_long_content: true,
    locator: "chat",
    artifact_id: "",
  };
  const members = isResearchTeam ? [
    { id: "director", name: "研究总导演", role: "流程编排", status: stageIndex > 0 ? "已完成" : "等待确认", image: "static/assets/writeflow/member-workflow-producer.png" },
    { id: "research", name: "资料调研专家", role: "资料调研", status: state === "generating" ? "执行中" : stageIndex > 1 ? "已完成" : "待命", image: "static/assets/writeflow/member-research-expert.png" },
    { id: "fact", name: "事实核验专家", role: "事实核验", status: stageIndex > 2 ? "已完成" : "待命", image: "static/assets/writeflow/member-editor-review.png" },
    { id: "outline", name: "结构提纲专家", role: "结构提纲", status: stageIndex > 3 ? "已完成" : "待命", image: "static/assets/writeflow/member-outline-architect.png" },
    { id: "draft", name: "材料初稿专家", role: "材料初稿", status: "待命", image: "static/assets/writeflow/member-writing-executor.png" },
    { id: "delivery", name: "复核交付专家", role: "复核交付", status: state === "completed" ? "已完成" : "待命", image: "static/assets/writeflow/member-style-modeler.png" },
  ] : [
    { id: "director", name: "写作总导演", role: "流程编排", status: stageIndex > 0 ? "已完成" : "等待确认", image: "static/assets/writeflow/member-workflow-producer.png" },
    { id: "material", name: "资料整理专家", role: "素材整理", status: state === "generating" ? "执行中" : state === "awaiting_stage_input" ? "等待确认" : stageIndex > 1 ? "已完成" : "待命", image: "static/assets/writeflow/member-research-expert.png" },
    { id: "writer", name: "文案创作专家", role: "正文写作", status: "待命", image: "static/assets/writeflow/member-writing-executor.png" },
    { id: "reviewer", name: "审稿专家", role: "审稿打磨", status: "待命", image: "static/assets/writeflow/member-editor-review.png" },
    { id: "delivery", name: "交付复核专家", role: "交付确认", status: state === "completed" ? "已完成" : "待命", image: "static/assets/writeflow/member-outline-architect.png" },
  ];
  const tasks = isResearchTeam ? [
    { id: "direction", title: "研究方向确认", phase: "方向确认", status: stageIndex > 0 ? "done" : "pending", worker_id: "director", worker_name: "研究总导演" },
    { id: "research", title: "资料调研", phase: "资料调研", status: state === "generating" ? "running" : stageIndex > 1 ? "done" : "pending", worker_id: "research", worker_name: "资料调研专家" },
    { id: "evidence", title: "事实核验", phase: "事实核验", status: stageIndex > 2 ? "done" : "pending", worker_id: "fact", worker_name: "事实核验专家" },
    { id: "outline", title: "结构提纲", phase: "结构提纲", status: stageIndex > 3 ? "done" : "pending", worker_id: "outline", worker_name: "结构提纲专家" },
    { id: "draft", title: "材料初稿", phase: "材料初稿", status: "pending", worker_id: "draft", worker_name: "材料初稿专家" },
    { id: "delivery", title: "复核交付", phase: "复核交付", status: state === "completed" ? "done" : "pending", worker_id: "delivery", worker_name: "复核交付专家" },
  ] : [
    { id: "plan", title: "流程安排", phase: "流程安排", status: stageIndex > 0 ? "done" : "pending", worker_id: "director", worker_name: "写作总导演" },
    { id: "materials", title: "素材整理", phase: "素材整理", status: state === "generating" ? "running" : state === "awaiting_stage_input" ? "awaiting_input" : stageIndex > 1 ? "done" : "pending", worker_id: "material", worker_name: "资料整理专家" },
    { id: "draft", title: "起草工作汇报初稿", phase: "初稿撰写", status: stageIndex > 2 ? "done" : "pending", worker_id: "writer", worker_name: "文案创作专家" },
    { id: "polish", title: "审稿打磨", phase: "审稿打磨", status: stageIndex > 3 ? "done" : "pending", worker_id: "reviewer", worker_name: "审稿专家" },
    { id: "delivery", title: "交付确认", phase: "交付确认", status: state === "completed" ? "done" : "pending", worker_id: "delivery", worker_name: "交付复核专家" },
  ];
  const currentStage = tasks[stageIndex] || tasks[0];
  const pendingInput = state === "awaiting_stage_input" ? {
    id: "stage-input-hide-name",
    question: "本次汇报是否需要隐去项目或客户名称？",
    description: "资料整理专家需要你确认后继续生成；确认后仍停留在当前素材整理阶段。",
    options: ["不需要隐去", "需要隐去，使用代号"],
    required: true,
    stage_id: currentStage.id,
    worker_id: currentStage.worker_id,
  } : {};
  const reviewItems = Array.from({ length: Math.max(1, reviewItemCount) }, (_, idx) => ({
    id: `ri-${idx + 1}`,
    title: idx === 0 ? "请补充具体月份、关键指标和责任部门。" : `第 ${idx + 1} 项待人工补充事项，需要复核后再进入下一阶段。`,
    status: "pending",
    used_in_revision: false,
  }));
  const stageResult = state === "awaiting_review" || state === "completed" || state === "generated_invalid" ? {
    stage_id: currentStage.id,
    worker_id: currentStage.worker_id,
    summary: output.summary,
    deliverable: output.preview,
    review_items: reviewItems,
    next_action: "请复核当前阶段成果，确认后进入下一阶段。",
    validation: state === "generated_invalid" ? { status: "fail", message: "草稿未通过办公材料口径校验。" } : { status: "pass", message: "" },
  } : {};
  const presentationByState = {
    collecting_required: { title: "必须需求待确认", detail: "请先补充需求信息，专家团再继续推进。", primary_action: { id: "answer_required", label: "去确认", kind: "question_popover" } },
    collecting_optional: { title: "可选补充待处理", detail: "可继续补充材料，也可以跳过后开始生成。", primary_action: { id: "answer_optional", label: "补充或跳过", kind: "question_popover" } },
    ready_to_generate: { title: "准备开始生成", detail: "需求已经确认，可以启动当前阶段。", primary_action: { id: "start_generation", label: "开始生成", kind: "primary" } },
    generating: { title: "专家团正在生成", detail: "资料整理专家正在处理当前阶段。", primary_action: { id: "cancel", label: "停止生成", kind: "danger" } },
    awaiting_stage_input: { title: "需要确认后继续", detail: pendingInput.description, primary_action: { id: "submit_stage_input", label: "确认并继续生成", kind: "primary" } },
    generated_invalid: { title: "草稿未通过校验", detail: "草稿未通过办公材料口径校验，请重新生成。", primary_action: { id: "regenerate", label: "重新生成", kind: "primary" }, secondary_actions: [{ id: "view_result", label: "查看草稿", kind: "ghost" }] },
    awaiting_review: { title: "阶段成果待复核", detail: "阶段结果已生成，请查看后确认是否进入下一阶段。", primary_action: { id: "review_stage", label: "去复核", kind: "primary" }, secondary_actions: [{ id: "view_result", label: "查看成果", kind: "ghost" }, { id: "approve_stage", label: "无修改，进入下一阶段", kind: "primary" }, { id: "revise_stage", label: "需要修改", kind: "ghost" }] },
    completed: { title: "专家团任务已完成", detail: "所有阶段已完成，结果已写入当前对话。", primary_action: { id: "view_result", label: "查看成果", kind: "primary" } },
  };
  const progress = {
    done: tasks.filter((task) => task.status === "done").length,
    total: tasks.length,
    current: state.startsWith("collecting") ? "需求确认" : currentStage.phase,
    current_index: stageIndex,
    is_intake: state.startsWith("collecting"),
    text: state.startsWith("collecting") ? `0/${tasks.length}` : state === "completed" ? `${tasks.length}/${tasks.length}` : `${Math.min(tasks.length, stageIndex + 1)}/${tasks.length}`,
  };
  const questions = [
    { id: "topic", title: "这次要编制哪类办公材料，主题是什么？", required: true, status: state === "collecting_required" ? "pending" : "answered", answer: state === "collecting_required" ? "" : "部门月度工作汇报，主题是迎峰度夏保供电重点工作推进情况" },
    { id: "audience", title: "材料面向哪些对象，使用场景是什么？", required: true, status: state === "collecting_required" ? "pending" : "answered", answer: state === "collecting_required" ? "" : "公司分管领导，月度例会" },
    { id: "boundary", title: "有哪些已知素材、口径要求、篇幅或表述边界？", required: true, status: state === "collecting_required" ? "pending" : "answered", answer: state === "collecting_required" ? "" : "正式、条理清晰、包含问题和下步安排" },
    { id: "optional_context", title: "还有没有可选补充材料或特别强调的点？", required: false, status: state === "collecting_optional" ? "pending" : "skipped", answer: "" },
  ];
  const view = {
    business_context: { material_type: "work_report", visible_title: "起草工作汇报初稿", style_contract: "采用正式工作汇报口径。", forbidden_terms: [] },
    presentation: { state, visible_title: "起草工作汇报初稿", result: output, summary: output.summary, progress_text: progress.text, ...presentationByState[state] },
    team: { id: teamId, title: isResearchTeam ? "深度材料研究团" : "内容创作专家团", image: "", members },
    workflow: { stages: tasks, current_stage: currentStage, progress },
    workspace: {
      visible: true,
      title: "专家团工作台",
      state,
      current_stage: currentStage,
      current_worker: members.find((member) => member.id === currentStage.worker_id) || {},
      phases: tasks,
      members,
      timeline: [
        { type: "team_created", title: `${isResearchTeam ? "深度材料研究团" : "内容创作专家团"}已创建`, detail: "等待需求确认后开始协作。", member_id: "director", member_name: isResearchTeam ? "研究总导演" : "写作总导演" },
        { type: state === "awaiting_stage_input" ? "stage_input_requested" : "generation_started", title: state === "awaiting_stage_input" ? "资料整理专家请求确认" : "专家正在处理当前阶段", detail: currentStage.phase, member_id: currentStage.worker_id, member_name: currentStage.worker_name },
      ],
      stage_result: stageResult,
      pending_input: pendingInput,
    },
    stage_result: stageResult,
    pending_input: pendingInput,
    intake: { required_pending: state === "collecting_required" ? 3 : 0, optional_pending: state === "collecting_optional" ? 1 : 0, optional_status: state === "collecting_optional" ? "pending" : "skipped", questions },
    primary_confirmation: state === "awaiting_stage_input" ? { type: "stage_input", title: pendingInput.question } : state.startsWith("collecting") ? { type: "question", title: "需求待确认" } : state === "awaiting_review" ? { type: "stage_review", title: "阶段成果待复核" } : null,
    pending_confirmations: state === "awaiting_stage_input" || state.startsWith("collecting") || state === "awaiting_review" ? [{}] : [],
    review_items: state === "awaiting_review" ? stageResult.review_items : [],
    stage_review: {
      review_id: String(overrides.reviewId || "review-1"),
      attempt: Number(overrides.stageAttempt || 1),
      display_state: state === "generating" ? "running" : state,
      actionable: state === "awaiting_review",
      output: { ...output, attempt: Number(overrides.artifactAttempt || overrides.stageAttempt || 1) },
    },
    office_review: overrides.officeReviewUi || { review_id: String(overrides.officeReviewId || "office-review-1") },
    actions: { can_submit_stage_input: state === "awaiting_stage_input", can_approve_stage: state === "awaiting_review", can_request_revision: state === "awaiting_review", can_cancel: state === "generating", can_retry: state === "generated_invalid" },
    timeline_events: [],
  };
  return {
    run_id: `electron-plan-a-${state}`,
    schema_version: 2,
    version: Number(overrides.version || 1),
    execution_attempt: Number(overrides.executionAttempt || 1),
    document_brief: { revision: Number(overrides.briefRevision || 1) },
    current_stage_attempt_reservation: {
      stage_id: currentStage.id,
      stage_attempt: Number(overrides.stageAttempt || 1),
    },
    session_id: sessionId,
    team_id: teamId,
    team_title: isResearchTeam ? "深度材料研究团" : "内容创作专家团",
    title: "帮我起草一份部门月度工作汇报，主题是迎峰度夏保供电重点工作推进情况",
    workflow_state: state,
    phase: currentStage.phase,
    questions,
    members,
    tasks,
    artifacts: state === "awaiting_review" || state === "completed" ? [{ id: output.id, kind: "chat", label: "结果已写入对话", exists: true }] : [],
    stage_outputs: state === "awaiting_review" || state === "completed" || state === "generated_invalid" ? [output] : [],
    view,
    ...overrides,
  };
}

async function renderRun(page, state, overrides) {
  await page.evaluate(({ state, overrides }) => {
    const run = window.__expertTeamRunFixture(S.session.session_id, state, overrides || {});
    window.__expertTeamRunFixtureCurrent = run;
    const card = _expertTeamStatusCardFromRun(run, { session_id: S.session.session_id });
    renderExpertTeamStatusSurface(card);
  }, { state, overrides });
  await page.waitForSelector("#expertTeamWorkspacePanel:not([hidden])", { timeout: 10000 });
}

async function submitOfficeAcceptanceScenario(page, { decision, issues, doubleClick = false, result = { ok: true } }) {
  const checklist = Object.fromEntries([
    "document_opened", "title_and_cover_match", "genre_and_structure_match", "content_order_correct",
    "figures_unique_and_readable", "tables_readable", "headers_footers_pagination",
    "no_placeholders_or_workflow_text", "citations_readable",
  ].map((key) => [key, "not_checked"]));
  await renderRun(page, "awaiting_review", {
    run_id: `electron-office-submit-${decision}`,
    officeReviewId: `office-submit-${decision}`,
    officeReviewUi: {
      review_id: `office-submit-${decision}`, document_revision: 4, document_sha256: "abcdef0123456789".repeat(4),
      status: "pending", decision: "pending", validity: "active", checklist, reviewer_label: "王审核", issues,
      review_session_status: "ready",
    },
  });
  await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='result']");
  await page.click("#expertTeamWorkspacePanel [data-expert-team-office-open]");
  const evidenceBefore = await page.evaluate(() => window.__officeEvidenceCalls.length);
  await page.setInputFiles("body > [data-expert-team-office-drawer] [data-office-evidence-input]", path.join(repoRoot, "hermes-local-lab", "sources", "hermes-webui", "docs", "images", "update-banner-whats-new-after.png"));
  await page.waitForFunction((count) => window.__officeEvidenceCalls.length === count + 1, evidenceBefore, { timeout: 5000 });
  await page.locator("body > [data-expert-team-office-drawer] [data-office-checklist]").evaluateAll((items) => items.forEach((item) => { item.checked = true; item.dispatchEvent(new Event("change", { bubbles: true })); }));
  await page.check(`body > [data-expert-team-office-drawer] input[name="office-decision"][value="${decision}"]`);
  await page.fill("body > [data-expert-team-office-drawer] [data-office-note]", "已用 WPS 打开正式文档并逐页检查目录、表格和整体版式。");
  const before = await page.evaluate(() => window.__officeAcceptanceCalls.length);
  await page.evaluate((next) => { window.__officeAcceptanceResults.push(next); }, result);
  if (doubleClick) {
    await page.evaluate(() => { const button = document.querySelector("body > [data-expert-team-office-drawer] [data-office-submit]"); button.click(); button.click(); });
  } else {
    await page.click("body > [data-expert-team-office-drawer] [data-office-submit]");
  }
  await page.waitForFunction((count) => window.__officeAcceptanceCalls.length === count + 1, before, { timeout: 5000 });
  await page.waitForTimeout(50);
  return page.evaluate((index) => {
    const payload = window.__officeAcceptanceCalls[index];
    const drawer = document.querySelector("body > [data-expert-team-office-drawer]");
    return {
      payload,
      callCount: window.__officeAcceptanceCalls.length - index,
      drawerVisible: Boolean(drawer && !drawer.hidden),
      live: drawer?.querySelector("[data-office-live]")?.textContent || "",
      note: drawer?.querySelector("[data-office-note]")?.value || "",
      checked: drawer?.querySelectorAll("[data-office-checklist]:checked").length || 0,
    };
  }, before);
}

async function verifySameStageIdentityAdvance(page, { marker, from, to, screenshot }) {
  await renderRun(page, "awaiting_review", from);
  await page.click("#expertTeamWorkspacePanel .expert-team-stage-review [data-expert-team-action='revise_stage']");
  await page.fill("#expertTeamWorkspacePanel .expert-team-stage-feedback:not([hidden]) textarea", marker);
  await page.evaluate(async ({ to }) => {
    const next = window.__expertTeamRunFixture(S.session.session_id, "awaiting_review", to);
    window.__expertTeamPollResponses = [next];
    await _hydrateExpertTeamStatusCardForSession(S.session.session_id, { silent: true });
  }, { to });
  await page.waitForSelector("#expertTeamWorkspacePanel [data-expert-team-recoverable-draft]", { timeout: 5000 });
  const state = await page.evaluate(({ marker }) => {
    const recovery = document.querySelector("[data-expert-team-recoverable-draft] [data-expert-team-draft-copy]");
    const editableValues = Array.from(document.querySelectorAll("#expertTeamWorkspacePanel textarea:not([readonly])"))
      .map((input) => input.value);
    return {
      recoveryValue: recovery?.value || "",
      editableValues,
      marker,
      stageId: document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-inner")?.dataset.expertTeamStageId || "",
    };
  }, { marker });
  assertState(
    state.recoveryValue === marker && !state.editableValues.some((value) => value.includes(marker)) && state.stageId === "materials",
    "Same-stage identity advance leaked a dirty draft into the new editable form",
    state
  );
  await page.evaluate(() => document.querySelector("[data-expert-team-recoverable-draft]")?.scrollIntoView({ block: "center" }));
  await page.screenshot({ path: screenshot, fullPage: false });
  await page.click("#expertTeamWorkspacePanel [data-expert-team-recoverable-draft] .secondary");
  return state;
}

async function snapshotState(page) {
  return page.evaluate(() => {
    const panel = document.querySelector("#expertTeamWorkspacePanel");
    const dock = document.querySelector("#writeflowStatusDock");
    const shell = document.querySelector(".taiji-home-shell");
    const panelRect = panel ? panel.getBoundingClientRect() : null;
    return {
      chatText: document.querySelector("#msgInner")?.textContent.replace(/\s+/g, " ").trim() || "",
      panelText: panel?.textContent.replace(/\s+/g, " ").trim() || "",
      dockHidden: !dock || dock.hidden || getComputedStyle(dock).display === "none",
      dockText: dock?.textContent.replace(/\s+/g, " ").trim() || "",
      panelVisible: Boolean(panel && !panel.hidden && getComputedStyle(panel).display !== "none"),
      collapsed: shell?.classList.contains("taiji-expert-team-panel-collapsed") || false,
      panelRect: panelRect ? { top: panelRect.top, left: panelRect.left, width: panelRect.width, height: panelRect.height, right: panelRect.right, bottom: panelRect.bottom } : null,
      chatConfirmButtons: Array.from(document.querySelectorAll("#msgInner button")).filter((button) => /去确认|去复核|确认并继续/.test(button.textContent || "")).length,
      memberCount: document.querySelectorAll("#expertTeamWorkspacePanel .expert-team-member-row").length,
      stageCount: document.querySelectorAll("#expertTeamWorkspacePanel .expert-team-panel-phase").length,
      primaryButtons: document.querySelectorAll("#expertTeamWorkspacePanel .expert-team-primary-task-card [data-expert-team-action], #expertTeamWorkspacePanel .expert-team-stage-input-card [data-expert-team-action], #expertTeamWorkspacePanel .expert-team-stage-review [data-expert-team-action]").length,
    };
  });
}

async function activeWorkbenchTab(page) {
  return page.evaluate(() => {
    const active = document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab].is-active");
    const visiblePanel = Array.from(document.querySelectorAll("#expertTeamWorkspacePanel [data-expert-team-tab-panel]"))
      .find((panel) => !panel.hidden && getComputedStyle(panel).display !== "none");
    return {
      tab: active?.dataset.expertTeamWorkspaceTab || "",
      label: active?.textContent.replace(/\s+/g, " ").trim() || "",
      panel: visiblePanel?.dataset.expertTeamTabPanel || "",
      text: visiblePanel?.textContent.replace(/\s+/g, " ").trim() || "",
    };
  });
}

async function verifyRolloutGate(page) {
  const requested = process.env.TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT;
  const expectedMode = requested === "pilot" ? "pilot" : "off";
  const expectedSource = Object.prototype.hasOwnProperty.call(process.env, "TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT")
    ? "environment"
    : "default";
  const status = await page.evaluate(async () => {
    const response = await fetch("/api/expert-teams/rollout/status", { credentials: "include" });
    if (!response.ok) throw new Error(`rollout status failed ${response.status}: ${await response.text()}`);
    return response.json();
  });
  const rollout = status.contract_rollout || {};
  assertState(
    rollout.effective_mode === expectedMode && rollout.effective_source === expectedSource,
    "Rollout status does not match the isolated process configuration",
    { expectedMode, expectedSource, rollout }
  );

  const inspectEntry = async (teamId, exampleId) => {
    await page.evaluate(async (id) => {
      if (typeof switchPanel === "function") await switchPanel("writeflow");
      await loadWriteflow(true);
      openWriteflowTeamModal(id);
    }, teamId);
    const selector = `#writeflowTeamModal [data-template-id="${exampleId}"]`;
    await page.waitForSelector(selector, { state: "visible", timeout: 10000 });
    await page.focus(selector);
    await page.keyboard.press("Enter");
    return page.evaluate(({ teamId, exampleId }) => {
      const team = _writeflowTeamById(teamId);
      const example = (team.examples || []).find((item) => item.id === exampleId);
      const selected = document.querySelector(`#writeflowTeamModal [data-template-id="${exampleId}"]`);
      return {
        text: selected?.textContent.replace(/\s+/g, " ").trim() || "",
        selected: Boolean(selected?.classList.contains("selected")),
        focused: document.activeElement === selected,
        capability: example?.capability || {},
        payload: _writeflowExpertTeamStartPayload(team, example, { prompt: example.prompt || "测试诉求" }),
      };
    }, { teamId, exampleId });
  };
  const startFromEntry = (teamId, exampleId) => page.evaluate(async ({ teamId, exampleId }) => {
    const team = _writeflowTeamById(teamId);
    const example = (team.examples || []).find((item) => item.id === exampleId);
    const payload = _writeflowExpertTeamStartPayload(team, example, {
      session_id: S.session.session_id,
      prompt: example.prompt || "测试诉求",
    });
    const response = await fetch("/api/expert-teams/start", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    return { ok: response.ok, status: response.status, payload, result };
  }, { teamId, exampleId });

  const workEntry = await inspectEntry("content-creator-team", "work_report");
  assertState(workEntry.selected && workEntry.focused, "Work-report rollout entry is not keyboard discoverable", workEntry);
  await page.screenshot({ path: path.join(outDir, "expert-team-rollout-gate.png"), fullPage: false });
  const starts = [];
  starts.push(await startFromEntry("content-creator-team", "work_report"));
  if (expectedMode === "off") {
    assertState(
      workEntry.text.includes("AI 草稿能力") && !workEntry.text.includes("企业合同试点") &&
        !Object.prototype.hasOwnProperty.call(workEntry.payload, "contract_version") && starts[0].ok &&
        !Object.prototype.hasOwnProperty.call(starts[0].result.run || {}, "contract_version") &&
        !Object.prototype.hasOwnProperty.call(starts[0].result.run || {}, "document_brief"),
      "Off mode exposed or created an enterprise contract run",
      { workEntry, start: starts[0] }
    );
  } else {
    const researchEntry = await inspectEntry("deep-research-team", "research_report");
    assertState(researchEntry.selected && researchEntry.focused, "Research-report rollout entry is not keyboard discoverable", researchEntry);
    starts.push(await startFromEntry("deep-research-team", "research_report"));
    for (const [entry, start] of [[workEntry, starts[0]], [researchEntry, starts[1]]]) {
      const run = start.result.run || {};
      const progressText = String(run.view?.presentation?.progress_text || run.view?.phase_progress?.text || "");
      assertState(
        entry.text.includes("企业合同试点") && entry.capability.kind === "enterprise_contract_pilot" &&
          start.ok && start.payload.contract_version === "expert-team-contract/v1" &&
          run.contract_version === "expert-team-contract/v1" && run.document_brief?.status === "draft" &&
          (progressText.startsWith("0/") || Number(run.view?.phase_progress?.done || -1) === 0),
        "Pilot entry did not create a contract-v1 Brief at 0/N",
        { entry, start, progress_text: progressText }
      );
    }
  }
  fs.writeFileSync(
    path.join(outDir, "expert-team-rollout-gate.json"),
    JSON.stringify({ expected_mode: expectedMode, effective_mode: rollout.effective_mode, effective_source: rollout.effective_source, starts }, null, 2)
  );
  await page.evaluate(async () => {
    closeWriteflowTeamModal();
    if (typeof switchPanel === "function") await switchPanel("chat");
  });
  return { rollout, starts };
}

async function main() {
  assertState(fs.existsSync(electronBin), `Electron binary not found: ${electronBin}`);
  fs.mkdirSync(outDir, { recursive: true });
  const runtimeBase = path.join(repoRoot, "output", "playwright");
  fs.mkdirSync(runtimeBase, { recursive: true });
  const tmpRoot = fs.mkdtempSync(path.join(runtimeBase, "electron-expert-team-runtime-"));
  const workspace = path.join(tmpRoot, "workspace");
  fs.mkdirSync(workspace, { recursive: true });

  const app = await _electron.launch({
    executablePath: electronBin,
    args: [appDir],
    env: {
      ...process.env,
      TAIJI_AGENT_ROOT: labDir,
      TAIJI_AGENT_USE_USER_DIRS: "1",
      TAIJI_DESKTOP_USER_DATA_DIR: path.join(tmpRoot, "electron-user-data"),
      XDG_CONFIG_HOME: path.join(tmpRoot, "config"),
      XDG_DATA_HOME: path.join(tmpRoot, "data"),
      XDG_STATE_HOME: path.join(tmpRoot, "state"),
      AGENT_API_PORT: "19942",
      API_SERVER_PORT: "19942",
      WEBUI_PORT: "19987",
      TAIJI_WEBUI_PORT: "19987",
      TAIJI_LICENSE_REQUIRED: "0",
      TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: "0",
    },
    timeout: 90000,
  });
  try {
    const page = await app.firstWindow({ timeout: 90000 });
    await page.waitForLoadState("domcontentloaded", { timeout: 90000 });
    await page.waitForFunction(
      () => location.href.includes("taiji_desktop=1") &&
        typeof buildExpertTeamCardFromRun === "function" &&
        typeof renderExpertTeamStatusSurface === "function" &&
        typeof handleExpertTeamPresentationAction === "function",
      { timeout: 90000 }
    );
    await prepareDesktopSession(page, workspace);
    await page.evaluate(({ fixtureSource }) => {
      window.__expertTeamRunFixture = eval(`(${fixtureSource})`);
    }, { fixtureSource: runFixture.toString() });

    await verifyRolloutGate(page);

    await page.evaluate(async () => {
      const ok = await sendExpertTeamAction({
        team_id: "content-creator-team",
        prompt: "帮我起草一份部门月度工作汇报，主题是迎峰度夏保供电重点工作推进情况",
        new_session: false,
      });
      if (!ok) throw new Error("sendExpertTeamAction returned false");
    });
    await page.waitForSelector("#expertTeamWorkspacePanel:not([hidden])", { timeout: 10000 });
    const realStart = await snapshotState(page);
    assertState(realStart.panelVisible && realStart.panelText.includes("专家团工作台"), "Real start did not show the right-side workbench", realStart);
    assertState(realStart.dockHidden && !realStart.dockText, "Real start still exposes the legacy bottom dock", realStart);
    assertState(realStart.chatText.includes("专家团已创建") && realStart.chatText.includes("右侧专家团工作台"), "Real start lifecycle message is missing or still points to bottom dock", realStart);
    assertState(realStart.panelRect && realStart.panelRect.left > 600 && realStart.panelRect.width >= 240 && realStart.panelRect.bottom <= 900, "Workbench is not positioned as a right-side surface", realStart);
    await page.evaluate(() => {
      const originalApi = api;
      window.__expertTeamApiOriginal = originalApi;
      window.__expertTeamPollRequestCount = 0;
      window.__expertTeamPollResponses = [];
      window.__expertTeamRejectRevision409 = false;
      window.__expertTeamRevision409Count = 0;
      window.__expertTeamRevision409Run = null;
      window.__officeIdentityStatusQueue = [];
      window.__officeHandoffCalls = [];
      window.__officeWaiverCalls = [];
      window.__officeRevisionCalls = [];
      window.__officeAcceptanceCalls = [];
      window.__officeAcceptanceResults = [];
      window.__officeBeginCalls = [];
      window.__officeEvidenceCalls = [];
      window.__officeWindowOpenOriginal = window.open;
      window.open = () => null;
      api = async (url, options) => {
        if (String(url).startsWith("/api/expert-teams/run?") && window.__expertTeamRunFixtureCurrent) {
          window.__expertTeamPollRequestCount += 1;
          if (window.__expertTeamPollResponses.length) {
            window.__expertTeamRunFixtureCurrent = window.__expertTeamPollResponses.shift();
          }
          return { run: window.__expertTeamRunFixtureCurrent };
        }
        if (String(url) === "/api/expert-teams/stage/revise" && window.__expertTeamRejectRevision409) {
          window.__expertTeamRevision409Count += 1;
          const error = new Error("状态已更新，请核对后重试。");
          error.status = 409;
          error.payload = { run: window.__expertTeamRevision409Run };
          throw error;
        }
        if (String(url) === "/api/expert-teams/identity/start" && JSON.parse(options?.body || "{}").purpose === "authorizer_handoff") {
          window.__officeHandoffCalls.push(JSON.parse(options.body));
          return { authorization_url: "https://identity.invalid/select-account" };
        }
        if (String(url) === "/api/expert-teams/identity/status" && window.__officeIdentityStatusQueue.length) {
          return window.__officeIdentityStatusQueue.shift();
        }
        if (String(url) === "/api/expert-teams/waivers/create") {
          window.__officeWaiverCalls.push(JSON.parse(options?.body || "{}"));
          return { ok: true };
        }
        if (String(url) === "/api/expert-teams/office-revisions/create") {
          window.__officeRevisionCalls.push(JSON.parse(options?.body || "{}"));
          return { ok: true };
        }
        if (String(url) === "/api/docx-engine-v2/quality/wps-visual") {
          window.__officeAcceptanceCalls.push(JSON.parse(options?.body || "{}"));
          const result = window.__officeAcceptanceResults.length ? window.__officeAcceptanceResults.shift() : { ok: true };
          if (result && result.error) {
            const error = new Error(result.message || "Office review failed");
            error.payload = { code: result.code || "office_review_failed" };
            throw error;
          }
          return result;
        }
        if (String(url) === "/api/docx-engine-v2/quality/wps-visual/evidence") {
          const form = options?.body;
          window.__officeEvidenceCalls.push({
            session_id: String(form?.get("session_id") || ""), run_id: String(form?.get("run_id") || ""),
            expected_version: String(form?.get("expected_version") || ""), file_name: String(form?.get("file_0")?.name || ""),
            has_token: Boolean(form?.get("review_token")), has_path: Boolean(form?.get("delivery_dir") || form?.get("document_path")),
          });
          return { ok: true, count: 1, uploaded_count: 1, files: [{ name: "office-safe.png", sha256_short: "123456789abc", size_bytes: 1234 }] };
        }
        if (String(url) === "/api/docx-engine-v2/quality/wps-visual/begin") {
          window.__officeBeginCalls.push(JSON.parse(options?.body || "{}"));
          return { ok: true, review_session_status: "ready", reviewer: "王审核", opened_at: "now", expires_at_ns: Date.now() * 1e6 + 60000000000, document_sha256: "f".repeat(64) };
        }
        return originalApi(url, options);
      };
    });

    await renderRun(page, "collecting_required");
    const collecting = await snapshotState(page);
    assertState(collecting.panelText.includes("必须需求待确认") && collecting.panelText.includes("去确认"), "Collecting state has no right-side confirmation action", collecting);
    assertState(collecting.panelText.includes("0/5") && collecting.memberCount === 5, "Content team progress/members are not dynamically rendered from fixture", collecting);
    assertState(collecting.dockHidden && collecting.chatConfirmButtons === 0, "Collecting state still duplicates actions outside the workbench", collecting);
    const oldTabs = await page.evaluate(() => ({
      flow: Boolean(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='flow']")),
      members: Boolean(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='members']")),
      process: Boolean(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='process']")),
    }));
    assertState(!oldTabs.flow && !oldTabs.members && oldTabs.process, "Workbench does not expose the consolidated Process tab", oldTabs);
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='process']");
    const collaborationBeforeRefresh = await activeWorkbenchTab(page);
    assertState(collaborationBeforeRefresh.tab === "process" && collaborationBeforeRefresh.panel === "process", "Process tab did not become active before refresh", collaborationBeforeRefresh);
    await renderRun(page, "collecting_required");
    const collaborationAfterRefresh = await activeWorkbenchTab(page);
    assertState(collaborationAfterRefresh.tab === "process" && collaborationAfterRefresh.panel === "process", "Process tab did not survive workbench refresh", collaborationAfterRefresh);
    const contentTeamLayout = await page.evaluate(() => {
      const body = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-expanded-body");
      const tabs = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-tabs");
      const panel = document.querySelector("#expertTeamWorkspacePanel [data-expert-team-tab-panel='process']");
      const list = panel?.querySelector(".expert-team-member-list");
      const card = panel?.querySelector(".expert-team-collaboration-card");
      const current = panel?.querySelector(".expert-team-collaboration-current");
      const bodyRect = body?.getBoundingClientRect();
      const tabsRect = tabs?.getBoundingClientRect();
      const cardRect = card?.getBoundingClientRect();
      const workbench = document.querySelector("#expertTeamWorkspacePanel");
      const workbenchRect = workbench?.getBoundingClientRect();
      const tabRects = Array.from(tabs?.querySelectorAll("[role='tab']") || []).map((tab) => tab.getBoundingClientRect());
      const tabsReachable = Boolean(workbenchRect && tabRects.length === 3 && tabRects.every((rect) => rect.left >= workbenchRect.left && rect.right <= workbenchRect.right && rect.top >= workbenchRect.top && rect.bottom <= workbenchRect.bottom));
      switchExpertTeamWorkspaceTab(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='task']"));
      const primaryAction = document.querySelector("#expertTeamWorkspacePanel [data-expert-team-tab-panel='task'] [data-expert-team-action]");
      primaryAction?.scrollIntoView({ block: "nearest" });
      const primaryRect = primaryAction?.getBoundingClientRect();
      const primaryActionReachable = Boolean(primaryRect && workbenchRect && primaryRect.left >= workbenchRect.left && primaryRect.right <= workbenchRect.right && primaryRect.top >= workbenchRect.top && primaryRect.bottom <= workbenchRect.bottom);
      switchExpertTeamWorkspaceTab(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='process']"));
      return {
        text: panel?.textContent.replace(/\s+/g, " ").trim() || "",
        hasVerticalList: Boolean(list),
        rowCount: panel?.querySelectorAll(".expert-team-member-row").length || 0,
        avatarCount: panel?.querySelectorAll(".expert-team-member-avatar").length || 0,
        currentCount: panel?.querySelectorAll(".expert-team-member-row.running .expert-team-member-state").length || 0,
        currentText: current?.textContent.replace(/\s+/g, " ").trim() || "",
        noBodyOverflow: body ? body.scrollHeight <= body.clientHeight + 4 : false,
        cardFillsAvailableSpace: bodyRect && tabsRect && cardRect ? cardRect.height >= bodyRect.height - tabsRect.height - 18 : false,
        hasHorizontalStrip: Boolean(panel?.querySelector(".expert-team-member-strip")),
        scrollWidth: list ? list.scrollWidth : 0,
        clientWidth: list ? list.clientWidth : 0,
        bodyNoHorizontalOverflow: body ? body.scrollWidth <= body.clientWidth + 1 : false,
        tabsReachable,
        primaryActionReachable,
      };
    });
    assertState(
      contentTeamLayout.hasVerticalList && contentTeamLayout.rowCount === 5 && contentTeamLayout.avatarCount >= 5 && contentTeamLayout.currentCount === 1 && contentTeamLayout.currentText.includes("当前处理") && contentTeamLayout.currentText.includes("正在处理：") && !contentTeamLayout.hasHorizontalStrip && contentTeamLayout.scrollWidth <= contentTeamLayout.clientWidth + 1 && contentTeamLayout.bodyNoHorizontalOverflow && contentTeamLayout.tabsReachable && contentTeamLayout.primaryActionReachable && !contentTeamLayout.text.includes("generated_invalid"),
      "Process tab does not show the 5-person content team with Chinese states",
      contentTeamLayout
    );
    await page.screenshot({ path: path.join(outDir, "expert-team-plan-a-collaboration-tab-content-team.png"), fullPage: false });
    await renderRun(page, "collecting_required", { run_id: "electron-plan-a-research-run", team_id: "deep-research-team" });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='process']");
    const researchTeamLayout = await page.evaluate(() => {
      const body = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-expanded-body");
      const panel = document.querySelector("#expertTeamWorkspacePanel [data-expert-team-tab-panel='process']");
      const list = panel?.querySelector(".expert-team-member-list");
      const tabs = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-tabs");
      const workbench = document.querySelector("#expertTeamWorkspacePanel");
      const workbenchRect = workbench?.getBoundingClientRect();
      const tabRects = Array.from(tabs?.querySelectorAll("[role='tab']") || []).map((tab) => tab.getBoundingClientRect());
      const tabsReachable = Boolean(workbenchRect && tabRects.length === 3 && tabRects.every((rect) => rect.left >= workbenchRect.left && rect.right <= workbenchRect.right && rect.top >= workbenchRect.top && rect.bottom <= workbenchRect.bottom));
      switchExpertTeamWorkspaceTab(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='task']"));
      const primaryAction = document.querySelector("#expertTeamWorkspacePanel [data-expert-team-tab-panel='task'] [data-expert-team-action]");
      primaryAction?.scrollIntoView({ block: "nearest" });
      const primaryRect = primaryAction?.getBoundingClientRect();
      const primaryActionReachable = Boolean(primaryRect && workbenchRect && primaryRect.left >= workbenchRect.left && primaryRect.right <= workbenchRect.right && primaryRect.top >= workbenchRect.top && primaryRect.bottom <= workbenchRect.bottom);
      switchExpertTeamWorkspaceTab(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='process']"));
      return {
        text: panel?.textContent.replace(/\s+/g, " ").trim() || "",
        rowCount: panel?.querySelectorAll(".expert-team-member-row").length || 0,
        avatarCount: panel?.querySelectorAll(".expert-team-member-avatar").length || 0,
        noBodyOverflow: body ? body.scrollHeight <= body.clientHeight + 4 : false,
        scrollWidth: list ? list.scrollWidth : 0,
        clientWidth: list ? list.clientWidth : 0,
        bodyNoHorizontalOverflow: body ? body.scrollWidth <= body.clientWidth + 1 : false,
        tabsReachable,
        primaryActionReachable,
      };
    });
    assertState(
      researchTeamLayout.text.includes("深度材料研究团") && researchTeamLayout.rowCount === 6 && researchTeamLayout.avatarCount >= 6 && researchTeamLayout.scrollWidth <= researchTeamLayout.clientWidth + 1 && researchTeamLayout.bodyNoHorizontalOverflow && researchTeamLayout.tabsReachable && researchTeamLayout.primaryActionReachable,
      "Process tab does not dynamically render the 6-person research team",
      researchTeamLayout
    );
    await page.screenshot({ path: path.join(outDir, "expert-team-plan-a-collaboration-tab-research-team.png"), fullPage: false });
    await renderRun(page, "collecting_required", { run_id: "electron-plan-a-stable-run" });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='process']");
    await renderRun(page, "generating", { run_id: "electron-plan-a-stable-run" });
    const collaborationAfterStateRefresh = await activeWorkbenchTab(page);
    assertState(collaborationAfterStateRefresh.tab === "process" && collaborationAfterStateRefresh.panel === "process", "Process tab did not survive same-run state refresh", collaborationAfterStateRefresh);
    await renderRun(page, "collecting_required", { run_id: "electron-plan-a-new-run" });
    const newRunTab = await activeWorkbenchTab(page);
    assertState(newRunTab.tab === "task" && newRunTab.panel === "task", "A different expert-team run inherited the previous run tab", newRunTab);
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='task']");

    await page.click("#expertTeamWorkspacePanel [data-expert-team-action='answer_required']");
    await page.waitForSelector("#expertTeamWorkspacePanel .expert-team-question-popover:not([hidden]) textarea", { timeout: 10000 });
    const confirmOpen = await page.evaluate(() => {
      const panel = document.querySelector("#expertTeamWorkspacePanel");
      const popover = panel?.querySelector(".expert-team-question-popover:not([hidden])");
      const tabVisible = Array.from(panel?.querySelectorAll(".expert-team-panel-tabs,[data-expert-team-tab-panel]") || [])
        .some((node) => !node.hidden && getComputedStyle(node).display !== "none");
      const panelRect = panel ? panel.getBoundingClientRect() : null;
      const popoverRect = popover ? popover.getBoundingClientRect() : null;
      return {
        mode: panel?.dataset.expertTeamWorkspaceMode || "",
        popoverVisible: Boolean(popover && getComputedStyle(popover).display !== "none"),
        tabVisible,
        focusedTag: document.activeElement ? document.activeElement.tagName : "",
        panelRect: panelRect ? { top: panelRect.top, left: panelRect.left, width: panelRect.width, height: panelRect.height, right: panelRect.right, bottom: panelRect.bottom } : null,
        popoverRect: popoverRect ? { top: popoverRect.top, left: popoverRect.left, width: popoverRect.width, height: popoverRect.height, right: popoverRect.right, bottom: popoverRect.bottom } : null,
      };
    });
    assertState(
      confirmOpen.mode === "confirm" && confirmOpen.popoverVisible && !confirmOpen.tabVisible && confirmOpen.focusedTag === "TEXTAREA",
      "Question confirmation did not switch the right workbench into a focused wizard",
      confirmOpen
    );
    await page.screenshot({ path: path.join(outDir, "expert-team-plan-a-confirmation-open.png"), fullPage: false });
    await page.fill("#expertTeamWorkspacePanel .expert-team-question-popover:not([hidden]) textarea", "部门月度工作汇报，主题是迎峰度夏保供电重点工作推进情况");
    await page.focus("#expertTeamWorkspacePanel .expert-team-question-popover:not([hidden]) textarea");
    await page.waitForTimeout(6500);
    const draftProtected = await page.evaluate(() => {
      const input = document.querySelector("#expertTeamWorkspacePanel .expert-team-question-popover:not([hidden]) textarea");
      return { value: input ? input.value : "", popoverOpen: Boolean(input), activeInside: Boolean(document.activeElement && document.activeElement.closest("#expertTeamWorkspacePanel")) };
    });
    assertState(draftProtected.value.includes("迎峰度夏") && draftProtected.popoverOpen && draftProtected.activeInside, "Question popover draft was not preserved during refresh window", draftProtected);
    await page.keyboard.press("Escape").catch(() => {});

    await renderRun(page, "generating");
    const generating = await snapshotState(page);
    assertState(generating.panelText.includes("专家团正在生成") && generating.panelText.includes("停止生成"), "Generating state is not represented in the right workbench", generating);
    assertState(!generating.panelText.includes("阶段成果待复核") && !generating.panelText.includes("未检测到结果"), "Generating state is mixed with review or missing-result state", generating);
    assertState(generating.dockHidden && generating.chatConfirmButtons === 0, "Generating state leaks duplicate actions", generating);

    await renderRun(page, "generated_invalid");
    const invalid = await snapshotState(page);
    assertState(invalid.panelText.includes("草稿未通过校验") && invalid.panelText.includes("重新生成") && invalid.panelText.includes("查看草稿"), "Generated-invalid state does not expose recovery actions", invalid);
    assertState(!invalid.panelText.includes("generated_invalid"), "Generated-invalid state leaked the raw backend state into the UI", invalid);
    assertState(!invalid.panelText.includes("专家团正在生成") && !invalid.panelText.includes("阶段成果待复核"), "Generated-invalid state is mixed with running or review state", invalid);
    assertState(invalid.dockHidden && invalid.chatConfirmButtons === 0, "Generated-invalid state leaks duplicate actions", invalid);
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='process']");
    const invalidCollaboration = await activeWorkbenchTab(page);
    assertState(
      invalidCollaboration.tab === "process" && invalidCollaboration.text.includes("当前处理") && invalidCollaboration.text.includes("正在处理：") && !invalidCollaboration.text.includes("generated_invalid"),
      "Generated-invalid collaboration tab leaked raw status or lost the current collaboration summary",
      invalidCollaboration
    );
    await page.screenshot({ path: path.join(outDir, "expert-team-plan-a-collaboration-tab-generated-invalid.png"), fullPage: false });

    await renderRun(page, "awaiting_stage_input");
    const stageInput = await snapshotState(page);
    assertState(stageInput.panelText.includes("需要确认后继续") && stageInput.panelText.includes("本次汇报是否需要隐去项目或客户名称？"), "Stage input is not shown in the right workbench", stageInput);
    assertState(stageInput.primaryButtons === 1 && stageInput.chatConfirmButtons === 0, "Stage input has duplicate or missing primary actions", stageInput);
    await page.click("#expertTeamWorkspacePanel [data-expert-team-stage-input-choice='不需要隐去']");
    const selectedInput = await page.evaluate(() => Boolean(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-stage-input-choice].is-selected")));
    assertState(selectedInput, "Stage input quick choice cannot be selected");

    await page.click("#expertTeamWorkspacePanel .expert-team-panel-collapse-toggle");
    const collapsed = await snapshotState(page);
    assertState(collapsed.collapsed && collapsed.panelText.includes("处理") && !collapsed.panelText.includes("工作流程"), "Workbench did not collapse to the right capsule", collapsed);
    await page.click("#expertTeamWorkspacePanel .expert-team-capsule-action");
    const expanded = await snapshotState(page);
    assertState(!expanded.collapsed && expanded.panelText.includes("专家团工作台"), "Capsule action did not expand the workbench", expanded);

    await renderRun(page, "awaiting_review");
    await page.waitForSelector("#expertTeamWorkspacePanel .expert-team-result-card", { timeout: 10000 });
    await page.waitForSelector("#expertTeamWorkspacePanel .expert-team-stage-review", { timeout: 10000 });
    const review = await snapshotState(page);
    assertState(review.panelText.includes("阶段成果待复核") && review.panelText.includes("查看成果") && review.panelText.includes("需要修改"), "Review state is not actionable inside the workbench", review);
    assertState(!review.panelText.includes("公众号"), "Office-material review still contains public-account wording", review);
    await renderRun(page, "awaiting_review", { run_id: "electron-plan-a-scroll-run", reviewItemCount: 12 });
    const scrollBeforeRefresh = await page.evaluate(() => {
      const scroller = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-expanded-body");
      if (!scroller) return { found: false };
      const max = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      scroller.scrollTop = max;
      return { found: true, top: scroller.scrollTop, max, height: scroller.clientHeight, scrollHeight: scroller.scrollHeight };
    });
    assertState(scrollBeforeRefresh.found && scrollBeforeRefresh.max > 80, "Review workbench did not create a meaningful scroll range", scrollBeforeRefresh);
    await renderRun(page, "awaiting_review", { run_id: "electron-plan-a-scroll-run", reviewItemCount: 12 });
    const scrollAfterRefresh = await page.evaluate(() => {
      const scroller = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-expanded-body");
      if (!scroller) return { found: false };
      const max = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      return { found: true, top: scroller.scrollTop, max, height: scroller.clientHeight, scrollHeight: scroller.scrollHeight };
    });
    assertState(scrollAfterRefresh.found && scrollAfterRefresh.top >= scrollBeforeRefresh.top - 8, "Workbench refresh reset the user's scroll position", { before: scrollBeforeRefresh, after: scrollAfterRefresh });
    await page.screenshot({ path: path.join(outDir, "expert-team-plan-a-review-scroll-preserved.png"), fullPage: false });
    await page.click("#expertTeamWorkspacePanel .expert-team-stage-review [data-expert-team-action='revise_stage']");
    await page.waitForSelector("#expertTeamWorkspacePanel .expert-team-stage-feedback:not([hidden]) textarea", { timeout: 10000 });
    const revision = await page.evaluate(() => ({
      textareaVisible: Boolean(document.querySelector("#expertTeamWorkspacePanel .expert-team-stage-feedback:not([hidden]) textarea")),
      focusedTag: document.activeElement ? document.activeElement.tagName : "",
    }));
    assertState(revision.textareaVisible && revision.focusedTag === "TEXTAREA", "Review revision action did not reveal and focus the textarea", revision);

    await renderRun(page, "awaiting_review", { run_id: "electron-poll-draft-run", version: 7, reviewItemCount: 12 });
    await page.click("#expertTeamWorkspacePanel .expert-team-stage-review [data-expert-team-action='revise_stage']");
    const pollDraft = "POLL-DRAFT-7F3A\n" + Array.from({ length: 18 }, (_, index) => `第 ${index + 1} 行修改意见，用于验证输入区滚动保留。`).join("\n");
    await page.fill("#expertTeamWorkspacePanel .expert-team-stage-feedback:not([hidden]) textarea", pollDraft);
    const pollBefore = await page.evaluate(({ pollDraft }) => {
      const input = document.querySelector("#expertTeamWorkspacePanel .expert-team-stage-feedback:not([hidden]) textarea");
      const scroller = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-expanded-body");
      input.focus({ preventScroll: true });
      input.setSelectionRange(5, 15);
      input.scrollTop = input.scrollHeight;
      scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      const makeRun = (version, label) => {
        const run = window.__expertTeamRunFixture(S.session.session_id, "awaiting_review", {
          run_id: "electron-poll-draft-run", version, reviewItemCount: 12,
        });
        run.view.stage_result.summary = label;
        run.view.workspace.stage_result.summary = label;
        return run;
      };
      window.__expertTeamPollRequestCount = 0;
      window.__expertTeamPollResponses = [makeRun(8, "轮询状态版本 8"), makeRun(9, "轮询状态版本 9")];
      return {
        value: input.value,
        selectionStart: input.selectionStart,
        selectionEnd: input.selectionEnd,
        inputScrollTop: input.scrollTop,
        panelScrollTop: scroller.scrollTop,
        panelBottomGap: Math.max(0, scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop),
        tab: document.querySelector("[data-expert-team-workspace-tab].is-active")?.dataset.expertTeamWorkspaceTab || "",
        expected: pollDraft,
      };
    }, { pollDraft });
    await page.waitForFunction(() => window.__expertTeamPollRequestCount >= 2, { timeout: 13000 });
    const pollAfter = await page.evaluate(() => {
      const input = document.querySelector("#expertTeamWorkspacePanel .expert-team-stage-feedback:not([hidden]) textarea");
      const scroller = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-expanded-body");
      return {
        requests: window.__expertTeamPollRequestCount,
        panelText: document.querySelector("#expertTeamWorkspacePanel")?.textContent.replace(/\s+/g, " ").trim() || "",
        value: input?.value || "",
        expanded: Boolean(input),
        focused: document.activeElement === input,
        selectionStart: input?.selectionStart,
        selectionEnd: input?.selectionEnd,
        inputScrollTop: input?.scrollTop || 0,
        panelScrollTop: scroller?.scrollTop || 0,
        panelBottomGap: scroller ? Math.max(0, scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop) : 0,
        tab: document.querySelector("[data-expert-team-workspace-tab].is-active")?.dataset.expertTeamWorkspaceTab || "",
      };
    });
    assertState(
      pollAfter.requests >= 2 && pollAfter.panelText.includes("轮询状态版本 9") && pollAfter.value === pollBefore.expected && pollAfter.expanded && pollAfter.focused && pollAfter.selectionStart === 5 && pollAfter.selectionEnd === 15 && pollAfter.panelBottomGap <= pollBefore.panelBottomGap + 8 && pollAfter.tab === pollBefore.tab,
      "Dirty review draft did not survive two authoritative polling cycles",
      { before: pollBefore, after: pollAfter }
    );
    await page.screenshot({ path: path.join(outDir, "expert-team-polling-draft-preserved.png"), fullPage: false });

    await page.evaluate(() => {
      const advanced = window.__expertTeamRunFixture(S.session.session_id, "awaiting_review", {
        run_id: "electron-poll-draft-run", version: 10, stageAttempt: 2,
      });
      window.__expertTeamPollResponses = [advanced];
    });
    await page.waitForFunction(() => window.__expertTeamPollRequestCount >= 3, { timeout: 7000 });
    const advancedState = await page.evaluate(() => {
      const recovery = document.querySelector("[data-expert-team-recoverable-draft] [data-expert-team-draft-copy]");
      const editable = Array.from(document.querySelectorAll("#expertTeamWorkspacePanel textarea:not([readonly])"));
      return {
        recoveryValue: recovery?.value || "",
        editableValues: editable.map((input) => input.value),
        panelText: document.querySelector("#expertTeamWorkspacePanel")?.textContent.replace(/\s+/g, " ").trim() || "",
      };
    });
    assertState(
      advancedState.recoveryValue.includes("POLL-DRAFT-7F3A") && !advancedState.editableValues.some((value) => value.includes("POLL-DRAFT-7F3A")) && advancedState.panelText.includes("上一项未提交内容已保留"),
      "Stage advance did not isolate the old draft in the recovery area",
      advancedState
    );
    await page.evaluate(() => {
      document.querySelector("[data-expert-team-recoverable-draft]")?.scrollIntoView({ block: "center" });
    });
    await page.screenshot({ path: path.join(outDir, "expert-team-polling-draft-recovery.png"), fullPage: false });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-recoverable-draft] .secondary");

    await verifySameStageIdentityAdvance(page, {
      marker: "POLL-BRIEF-REVISION-DRAFT-88A1",
      from: { run_id: "electron-poll-draft-run", version: 10, stageAttempt: 2, briefRevision: 1 },
      to: { run_id: "electron-poll-draft-run", version: 11, stageAttempt: 2, briefRevision: 2 },
      screenshot: path.join(outDir, "expert-team-polling-brief-revision-recovery.png"),
    });
    await verifySameStageIdentityAdvance(page, {
      marker: "POLL-REVIEW-ID-DRAFT-17B2",
      from: { run_id: "electron-poll-draft-run", version: 11, stageAttempt: 2, briefRevision: 2, reviewId: "review-1" },
      to: { run_id: "electron-poll-draft-run", version: 12, stageAttempt: 2, briefRevision: 2, reviewId: "review-2" },
      screenshot: path.join(outDir, "expert-team-polling-review-id-recovery.png"),
    });
    await verifySameStageIdentityAdvance(page, {
      marker: "POLL-OFFICE-REVIEW-ID-DRAFT-63C4",
      from: { run_id: "electron-poll-draft-run", version: 12, stageAttempt: 2, briefRevision: 2, reviewId: "review-2", officeReviewId: "office-review-1" },
      to: { run_id: "electron-poll-draft-run", version: 13, stageAttempt: 2, briefRevision: 2, reviewId: "review-2", officeReviewId: "office-review-2" },
      screenshot: path.join(outDir, "expert-team-polling-office-review-id-recovery.png"),
    });

    await renderRun(page, "awaiting_review", { run_id: "electron-poll-409-run", version: 20 });
    await page.click("#expertTeamWorkspacePanel .expert-team-stage-review [data-expert-team-action='revise_stage']");
    await page.fill("#expertTeamWorkspacePanel .expert-team-stage-feedback:not([hidden]) textarea", "POLL-409-DRAFT-29C1");
    await page.evaluate(() => {
      const input = document.querySelector("#expertTeamWorkspacePanel .expert-team-stage-feedback:not([hidden]) textarea");
      input.focus({ preventScroll: true });
      input.setSelectionRange(5, 12);
      window.__expertTeamRejectRevision409 = true;
      window.__expertTeamRevision409Count = 0;
      window.__expertTeamRevision409Run = window.__expertTeamRunFixture(S.session.session_id, "awaiting_review", {
        run_id: "electron-poll-409-run",
        version: 21,
        stageAttempt: 2,
        artifactAttempt: 2,
        executionAttempt: 2,
        briefRevision: 2,
        reviewId: "review-409-new",
        officeReviewId: "office-review-409-new",
      });
    });
    await page.click("#expertTeamWorkspacePanel .expert-team-stage-feedback:not([hidden]) button");
    await page.waitForFunction(() => window.__expertTeamRevision409Count >= 1, { timeout: 5000 });
    const conflictState = await page.evaluate(() => {
      const recovery = document.querySelector("#expertTeamWorkspacePanel [data-expert-team-recoverable-draft] [data-expert-team-draft-copy]");
      const editableValues = Array.from(document.querySelectorAll("#expertTeamWorkspacePanel textarea:not([readonly])")).map((input) => input.value);
      window.__expertTeamRejectRevision409 = false;
      return {
        requests: window.__expertTeamRevision409Count,
        recoveryValue: recovery?.value || "",
        editableValues,
      };
    });
    assertState(
      conflictState.requests === 1 && conflictState.recoveryValue === "POLL-409-DRAFT-29C1" && !conflictState.editableValues.some((value) => value.includes("POLL-409-DRAFT-29C1")),
      "409 authoritative identity advance restored an old draft into the new editable form",
      conflictState
    );
    await page.evaluate(() => document.querySelector("[data-expert-team-recoverable-draft]")?.scrollIntoView({ block: "center" }));
    await page.screenshot({ path: path.join(outDir, "expert-team-polling-409-preserved.png"), fullPage: false });

    const officeChecklist = Object.fromEntries([
      "document_opened", "title_and_cover_match", "genre_and_structure_match", "content_order_correct",
      "figures_unique_and_readable", "tables_readable", "headers_footers_pagination",
      "no_placeholders_or_workflow_text", "citations_readable",
    ].map((key) => [key, "not_checked"]));
    await page.evaluate(() => {
      window._expertTeamIdentityStatus = { enabled: true, authenticated: true, authorizerHandoffReady: true, principal: { display_name: "王审核", roles: ["document-reviewer"] } };
    });
    await renderRun(page, "awaiting_review", {
      run_id: "electron-office-first-pending", officeReviewId: "office-first-pending",
      officeReviewUi: { review_id: "office-first-pending", document_revision: 1, document_sha256: "f".repeat(64), status: "pending", decision: "pending", validity: "active", review_session_status: "begin_required", checklist: officeChecklist, reviewer_label: "", issues: [] },
    });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='result']");
    await page.click("#expertTeamWorkspacePanel [data-expert-team-office-open]");
    assertState(await page.isVisible("body > [data-expert-team-office-drawer] [data-office-begin]"), "First pending Office view has no discoverable begin action");
    assertState(await page.isDisabled("body > [data-expert-team-office-drawer] [data-office-submit]"), "First pending Office view enabled submit before begin");
    await page.click("body > [data-expert-team-office-drawer] [data-office-begin]");
    await page.waitForFunction(() => window.__officeBeginCalls.length === 1, { timeout: 5000 });
    const firstBegin = await page.evaluate(() => ({ payload: window.__officeBeginCalls[0], submitDisabled: document.querySelector("body > [data-expert-team-office-drawer] [data-office-submit]")?.disabled, uploadDisabled: document.querySelector("body > [data-expert-team-office-drawer] [data-office-evidence-input]")?.disabled }));
    assertState(firstBegin.payload.run_id === "electron-office-first-pending" && firstBegin.payload.expected_version > 0 && !firstBegin.payload.delivery_dir && !firstBegin.payload.review_token && firstBegin.submitDisabled === true && firstBegin.uploadDisabled === false, "First pending Office begin leaked paths/token or enabled submit before evidence", firstBegin);
    await page.evaluate(() => { const drawer = document.querySelector("body > [data-expert-team-office-drawer]"); if (drawer) closeExpertTeamOfficeDrawer(drawer.querySelector("[data-office-close]"), true); });
    const passedSubmission = await submitOfficeAcceptanceScenario(page, { decision: "passed", issues: [], doubleClick: true });
    const conditionIssue = { issue_id: "condition-1", severity: "condition", target_domain: "office_issue", category: "visual_alignment", description: "第三页表格对齐略有差异", expected_fix: "授权保留或返修" };
    const conditionedSubmission = await submitOfficeAcceptanceScenario(page, { decision: "passed_with_conditions", issues: [conditionIssue] });
    const blockingIssue = { issue_id: "blocking-1", severity: "blocking", target_domain: "office_issue", category: "placeholder_content", description: "正文存在占位语", expected_fix: "删除后重新验收" };
    const failedSubmission = await submitOfficeAcceptanceScenario(page, { decision: "failed", issues: [blockingIssue] });
    for (const [decision, submission] of [["passed", passedSubmission], ["passed_with_conditions", conditionedSubmission], ["failed", failedSubmission]]) {
      const payload = submission.payload || {};
      const forbidden = ["reviewer", "principal", "role", "review_token", "delivery_dir", "document_path", "evidence_files"].filter((key) => Object.prototype.hasOwnProperty.call(payload, key));
      assertState(submission.callCount === 1 && payload.status === decision && payload.expected_version > 0 && payload.idempotency_key && forbidden.length === 0, `Office ${decision} did not submit exactly once with a safe payload`, submission);
    }
    const expiredSubmission = await submitOfficeAcceptanceScenario(page, {
      decision: "passed_with_conditions", issues: [conditionIssue],
      result: { error: true, code: "office_review_token_expired", message: "review token expired" },
    });
    assertState(expiredSubmission.callCount === 1 && expiredSubmission.drawerVisible && expiredSubmission.live.includes("已过期") && expiredSubmission.note.includes("逐页检查") && expiredSubmission.checked === 9, "Expired Office review token did not preserve typed checklist/note state", expiredSubmission);
    await page.evaluate(() => { const drawer = document.querySelector("body > [data-expert-team-office-drawer]"); if (drawer) closeExpertTeamOfficeDrawer(drawer.querySelector("[data-office-close]"), true); });
    await renderRun(page, "awaiting_review", {
      run_id: "electron-office-review-run",
      officeReviewId: "office-review-ui-1",
      officeReviewUi: {
        review_id: "office-review-ui-1", document_revision: 4, document_sha256: "abcdef0123456789".repeat(4),
        status: "pending", decision: "pending", validity: "active", checklist: officeChecklist, reviewer_label: "王审核",
        review_session_status: "ready",
        issues: [
          conditionIssue,
          blockingIssue,
        ],
      },
    });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='result']");
    const officeSummary = await page.evaluate(() => {
      const summary = document.querySelector("#expertTeamWorkspacePanel .expert-team-office-summary");
      return { text: summary?.textContent.replace(/\s+/g, " ").trim() || "", leaksFullHash: summary?.textContent.includes("abcdef0123456789".repeat(4)) || false };
    });
    assertState(officeSummary.text.includes("正式版本 4") && officeSummary.text.includes("abcdef012345") && !officeSummary.leaksFullHash, "Office summary did not progressively disclose technical identity", officeSummary);
    await page.click("#expertTeamWorkspacePanel [data-expert-team-office-open]");
    const driftEvidenceBefore = await page.evaluate(() => window.__officeEvidenceCalls.length);
    await page.setInputFiles("body > [data-expert-team-office-drawer] [data-office-evidence-input]", path.join(repoRoot, "hermes-local-lab", "sources", "hermes-webui", "docs", "images", "update-banner-whats-new-after.png"));
    await page.waitForFunction((count) => window.__officeEvidenceCalls.length === count + 1, driftEvidenceBefore, { timeout: 5000 });
    const officeDrawer = await page.evaluate(() => {
      const drawer = document.querySelector("body > [data-expert-team-office-drawer]");
      return {
        visible: Boolean(drawer && !drawer.hidden), focusedInside: Boolean(document.activeElement?.closest("[data-expert-team-office-drawer]")),
        checklist: drawer?.querySelectorAll("[data-office-checklist]").length || 0,
        waiverButtons: drawer?.querySelectorAll("[data-office-waiver-issue]").length || 0,
        blockingWaiver: Boolean(drawer?.querySelector('[data-office-waiver-issue="blocking-1"]')),
        oneScroll: drawer?.querySelectorAll(".expert-team-office-scroll").length || 0,
      };
    });
    assertState(officeDrawer.visible && officeDrawer.focusedInside && officeDrawer.checklist === 9 && officeDrawer.waiverButtons === 1 && !officeDrawer.blockingWaiver && officeDrawer.oneScroll === 1, "Structured Office drawer failed policy or accessibility gate", officeDrawer);
    await page.fill('body > [data-expert-team-office-drawer] [data-office-waiver-reason="condition-1"]', "经业务负责人确认，该对齐差异不影响使用。");
    await page.evaluate(() => { window.__officeReasonConfirmOriginal = window.confirm; window.confirm = () => false; });
    await page.keyboard.press("Escape");
    assertState(await page.isVisible("body > [data-expert-team-office-drawer]"), "Waiver-reason-only dirty draft closed without confirmation");
    await page.evaluate(() => { window.confirm = window.__officeReasonConfirmOriginal; delete window.__officeReasonConfirmOriginal; });
    await page.evaluate(() => { window.__officeIdentityStatusQueue = [{ enabled: true, authenticated: false, identity_flow_status: "authorizer_same_as_reviewer" }]; });
    await page.click('body > [data-expert-team-office-drawer] [data-office-waiver-issue="condition-1"]');
    await page.waitForFunction(() => document.querySelector("[data-office-live]")?.textContent.includes("仍是原验收人"), { timeout: 5000 });
    const sameReviewer = await page.evaluate(() => ({
      handoffs: window.__officeHandoffCalls.length, waivers: window.__officeWaiverCalls.length,
      reason: document.querySelector('[data-office-waiver-reason="condition-1"]')?.value || "",
      focused: document.activeElement?.matches('[data-office-waiver-reason="condition-1"]') || false,
    }));
    assertState(sameReviewer.handoffs === 1 && sameReviewer.waivers === 0 && sameReviewer.reason.includes("不影响使用") && sameReviewer.focused, "Same-reviewer SSO did not fail closed while preserving draft focus", sameReviewer);
    await page.evaluate(() => { window.__officeIdentityStatusQueue = [{ enabled: true, authenticated: true, principal: { display_name: "李授权", roles: ["waiver-authorizer"] } }]; });
    await page.click('body > [data-expert-team-office-drawer] [data-office-waiver-issue="condition-1"]');
    await page.waitForFunction(() => window.__officeWaiverCalls.length === 1, { timeout: 5000 });
    const authorized = await page.evaluate(() => ({ handoffs: window.__officeHandoffCalls.length, waiver: window.__officeWaiverCalls[0] }));
    assertState(authorized.handoffs === 2 && authorized.waiver.target_id === "condition-1" && authorized.waiver.reason.includes("不影响使用") && authorized.waiver.expected_version > 0 && authorized.waiver.idempotency_key && !authorized.waiver.principal && !authorized.waiver.role && !authorized.waiver.reviewer, "Authorizer handoff emitted an unsafe waiver payload", authorized);
    await page.evaluate(() => { window.__officeIdentityStatusQueue = [{ enabled: true, authenticated: false, identity_flow_status: "expired" }]; });
    await page.click('body > [data-expert-team-office-drawer] [data-office-waiver-issue="condition-1"]');
    await page.waitForFunction(() => document.querySelector("[data-office-live]")?.textContent.includes("已过期"), { timeout: 5000 });
    const expiredHandoff = await page.evaluate(() => ({ waivers: window.__officeWaiverCalls.length, reason: document.querySelector('[data-office-waiver-reason="condition-1"]')?.value || "", live: document.querySelector("[data-office-live]")?.textContent || "" }));
    assertState(expiredHandoff.waivers === 1 && expiredHandoff.reason.includes("不影响使用") && expiredHandoff.live.includes("重试"), "Expired handoff did not preserve the recoverable draft", expiredHandoff);
    await page.check('body > [data-expert-team-office-drawer] [data-office-revision-issue="blocking-1"]');
    await page.evaluate(() => { window.__officeConfirmOriginal = window.confirm; window.confirm = () => true; });
    await page.click('body > [data-expert-team-office-drawer] .expert-team-office-drawer-actions .expert-team-secondary-action');
    await page.waitForFunction(() => window.__officeRevisionCalls.length === 1, { timeout: 5000 });
    const revisionMutation = await page.evaluate(() => window.__officeRevisionCalls[0]);
    assertState(Array.isArray(revisionMutation.issue_ids) && revisionMutation.issue_ids.length === 1 && revisionMutation.issue_ids[0] === "blocking-1" && revisionMutation.expected_version > 0 && revisionMutation.idempotency_key && !revisionMutation.feedback && !revisionMutation.message && !revisionMutation.expected_fix && !revisionMutation.target_stage_id, "Office revision emitted free text or a client-derived target", revisionMutation);
    await page.check("body > [data-expert-team-office-drawer] [data-office-checklist='document_opened']");
    await page.evaluate(() => { window.confirm = () => false; });
    await page.keyboard.press("Escape");
    assertState(await page.isVisible("body > [data-expert-team-office-drawer]"), "Dirty Office drawer closed without confirmation");
    await page.evaluate(() => { window.confirm = () => true; });
    await page.keyboard.press("Escape");
    const officeClosed = await page.evaluate(() => ({ hidden: document.querySelector("#expertTeamWorkspacePanel [data-expert-team-office-drawer]")?.hidden, focusReturned: document.activeElement?.hasAttribute("data-expert-team-office-open") || false, inert: document.getElementById("mainChat")?.inert || false }));
    assertState(officeClosed.hidden && officeClosed.focusReturned && !officeClosed.inert, "Office drawer did not close and restore focus safely", officeClosed);
    await page.evaluate(() => { window.confirm = window.__officeConfirmOriginal; delete window.__officeConfirmOriginal; });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-office-open]");
    await page.screenshot({ path: path.join(outDir, "expert-team-office-review-drawer.png"), fullPage: false });
    await page.evaluate(() => { const drawer = document.querySelector("body > [data-expert-team-office-drawer]"); if (drawer) closeExpertTeamOfficeDrawer(drawer.querySelector("[data-office-close]"), true); });

    await renderRun(page, "awaiting_review", {
      run_id: "electron-office-abort-drift", officeReviewId: "office-abort-drift-1",
      officeReviewUi: {
        review_id: "office-abort-drift-1", document_revision: 5, document_sha256: "e".repeat(64),
        status: "pending", decision: "pending", validity: "active", checklist: officeChecklist,
        reviewer_label: "王审核", review_session_status: "ready", issues: [conditionIssue],
      },
    });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='result']");
    await page.click("#expertTeamWorkspacePanel [data-expert-team-office-open]");
    await page.fill('body > [data-expert-team-office-drawer] [data-office-waiver-reason="condition-1"]', "授权人确认该细微对齐差异不影响正式使用。");
    const waiverBeforeClose = await page.evaluate(() => {
      window.__officeIdentityStatusQueue = [{ enabled: true, authenticated: true, principal: { display_name: "迟到授权人", roles: ["waiver-authorizer"] } }];
      return window.__officeWaiverCalls.length;
    });
    await page.click('body > [data-expert-team-office-drawer] [data-office-waiver-issue="condition-1"]');
    await page.evaluate(() => { const drawer = document.querySelector("body > [data-expert-team-office-drawer]"); if (drawer) closeExpertTeamOfficeDrawer(drawer.querySelector("[data-office-close]"), true); });
    await page.waitForTimeout(1200);
    const closeAbort = await page.evaluate((before) => ({ before, after: window.__officeWaiverCalls.length }), waiverBeforeClose);
    assertState(closeAbort.after === closeAbort.before, "Closing the Office drawer allowed a stale authorizer handoff to create a waiver", closeAbort);

    await page.click("#expertTeamWorkspacePanel [data-expert-team-office-open]");
    const staleSubmitEvidenceBefore = await page.evaluate(() => window.__officeEvidenceCalls.length);
    await page.setInputFiles("body > [data-expert-team-office-drawer] [data-office-evidence-input]", path.join(repoRoot, "hermes-local-lab", "sources", "hermes-webui", "docs", "images", "update-banner-whats-new-after.png"));
    await page.waitForFunction((count) => window.__officeEvidenceCalls.length === count + 1, staleSubmitEvidenceBefore, { timeout: 5000 });
    await page.locator("body > [data-expert-team-office-drawer] [data-office-checklist]").evaluateAll((items) => items.forEach((item) => { item.checked = true; item.dispatchEvent(new Event("change", { bubbles: true })); }));
    await page.check('body > [data-expert-team-office-drawer] input[name="office-decision"][value="passed_with_conditions"]');
    await page.fill("body > [data-expert-team-office-drawer] [data-office-note]", "已用 WPS 打开正式文档并逐页检查目录、表格和整体版式，草稿需保留。");
    const driftBefore = await page.evaluate(() => {
      window._activeExpertTeamStatusCard = { ...window._activeExpertTeamStatusCard, version: Number(window._activeExpertTeamStatusCard.version || 0) + 1 };
      return window.__officeAcceptanceCalls.length;
    });
    await page.click("body > [data-expert-team-office-drawer] [data-office-submit]");
    const driftBlocked = await page.evaluate((before) => {
      const drawer = document.querySelector("body > [data-expert-team-office-drawer]");
      return {
        before, after: window.__officeAcceptanceCalls.length,
        live: drawer?.querySelector("[data-office-live]")?.textContent || "",
        note: drawer?.querySelector("[data-office-note]")?.value || "",
        checked: drawer?.querySelectorAll("[data-office-checklist]:checked").length || 0,
      };
    }, driftBefore);
    assertState(driftBlocked.after === driftBlocked.before && driftBlocked.live.includes("数据已更新") && driftBlocked.note.includes("草稿需保留") && driftBlocked.checked === 9, "Polling identity drift submitted stale Office acceptance or discarded the draft", driftBlocked);
    await page.evaluate(() => { const drawer = document.querySelector("body > [data-expert-team-office-drawer]"); if (drawer) closeExpertTeamOfficeDrawer(drawer.querySelector("[data-office-close]"), true); });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='task']");

    await page.click("#expertTeamWorkspacePanel .expert-team-result-card [data-expert-team-action='view_result']");
    await page.waitForSelector("#expertTeamResultViewer:not([hidden])", { timeout: 10000 });
    const viewer = await page.evaluate(() => ({
      text: document.querySelector("#expertTeamResultViewer")?.textContent.replace(/\s+/g, " ").trim() || "",
      height: document.querySelector("#expertTeamResultViewer .expert-team-result-viewer-panel")?.getBoundingClientRect().height || 0,
      viewport: window.innerHeight,
    }));
    assertState(viewer.text.includes("关于迎峰度夏保供电重点工作推进情况") && viewer.height < viewer.viewport, "Result viewer did not open bounded full content", viewer);

    await page.evaluate(() => { const viewer = document.getElementById("expertTeamResultViewer"); if (viewer) viewer.hidden = true; });
    await renderRun(page, "completed");
    const completed = await snapshotState(page);
    assertState(completed.panelText.includes("专家团任务已完成") && completed.panelText.includes("查看成果"), "Completed state is not closed cleanly", completed);
    assertState(!completed.panelText.includes("下一阶段建议"), "Completed state still exposes next-stage wording", completed);

    for (const width of [1024, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await renderRun(page, "awaiting_stage_input");
      await page.evaluate(() => {
        if (typeof showExpertTeamWorkspacePanel === "function") showExpertTeamWorkspacePanel(document.querySelector("#expertTeamWorkspacePanel"));
      });
      await page.screenshot({ path: path.join(outDir, `expert-team-plan-a-stage-input-${width}.png`), fullPage: false });
      await page.click("#expertTeamWorkspacePanel .expert-team-panel-collapse-toggle");
      await page.screenshot({ path: path.join(outDir, `expert-team-plan-a-capsule-${width}.png`), fullPage: false });
    }

    console.log("EXPERT TEAM ELECTRON SMOKE OK", JSON.stringify({
      screenshots: [
        path.join(outDir, "expert-team-plan-a-confirmation-open.png"),
        path.join(outDir, "expert-team-plan-a-collaboration-tab-content-team.png"),
        path.join(outDir, "expert-team-plan-a-collaboration-tab-research-team.png"),
        path.join(outDir, "expert-team-plan-a-collaboration-tab-generated-invalid.png"),
        path.join(outDir, "expert-team-plan-a-review-scroll-preserved.png"),
        path.join(outDir, "expert-team-polling-draft-preserved.png"),
        path.join(outDir, "expert-team-polling-draft-recovery.png"),
        path.join(outDir, "expert-team-polling-brief-revision-recovery.png"),
        path.join(outDir, "expert-team-polling-review-id-recovery.png"),
        path.join(outDir, "expert-team-polling-office-review-id-recovery.png"),
        path.join(outDir, "expert-team-polling-409-preserved.png"),
        path.join(outDir, "expert-team-office-review-drawer.png"),
        path.join(outDir, "expert-team-rollout-gate.png"),
        ...[1024, 1280, 1440].flatMap((width) => [
        path.join(outDir, `expert-team-plan-a-stage-input-${width}.png`),
        path.join(outDir, `expert-team-plan-a-capsule-${width}.png`),
      ]),
      ],
    }, null, 2));
  } finally {
    if (app) await app.close().catch(() => {});
  }
}

main().catch((error) => {
  console.error("EXPERT TEAM ELECTRON SMOKE FAILED");
  console.error(error && error.stack || error);
  process.exit(1);
});
