#!/usr/bin/env node
/*
 * Electron smoke for expert-team Plan A: right-side workbench, no bottom dock.
 */
const fs = require("fs");
const path = require("path");

function loadPlaywright() {
  return require(process.env.PLAYWRIGHT_NODE_PATH || "playwright");
}

const { _electron } = loadPlaywright();

const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
const appDir = path.join(repoRoot, "apps", "taiji-desktop");
const labDir = path.join(repoRoot, "hermes-local-lab");
const electronBin = path.join(appDir, "node_modules", "electron", "dist", "Electron.app", "Contents", "MacOS", "Electron");
const outDir = path.join(repoRoot, "output", "playwright");

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
    stage_review: { display_state: state === "generating" ? "running" : state, actionable: state === "awaiting_review", output },
    actions: { can_submit_stage_input: state === "awaiting_stage_input", can_approve_stage: state === "awaiting_review", can_request_revision: state === "awaiting_review", can_cancel: state === "generating", can_retry: state === "generated_invalid" },
    timeline_events: [],
  };
  return {
    run_id: `electron-plan-a-${state}`,
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
    const card = _expertTeamStatusCardFromRun(run, { session_id: S.session.session_id });
    renderExpertTeamStatusSurface(card);
  }, { state, overrides });
  await page.waitForSelector("#expertTeamWorkspacePanel:not([hidden])", { timeout: 10000 });
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

async function main() {
  assertState(fs.existsSync(electronBin), `Electron binary not found: ${electronBin}`);
  fs.mkdirSync(outDir, { recursive: true });
  const tmpRoot = fs.mkdtempSync(path.join(outDir, "electron-expert-team-runtime-"));
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

    await renderRun(page, "collecting_required");
    const collecting = await snapshotState(page);
    assertState(collecting.panelText.includes("必须需求待确认") && collecting.panelText.includes("去确认"), "Collecting state has no right-side confirmation action", collecting);
    assertState(collecting.panelText.includes("0/5") && collecting.memberCount === 5, "Content team progress/members are not dynamically rendered from fixture", collecting);
    assertState(collecting.dockHidden && collecting.chatConfirmButtons === 0, "Collecting state still duplicates actions outside the workbench", collecting);
    const oldTabs = await page.evaluate(() => ({
      flow: Boolean(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='flow']")),
      members: Boolean(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='members']")),
      collaboration: Boolean(document.querySelector("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='collaboration']")),
    }));
    assertState(!oldTabs.flow && !oldTabs.members && oldTabs.collaboration, "Workbench still exposes separate Flow/Members tabs", oldTabs);
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='collaboration']");
    const collaborationBeforeRefresh = await activeWorkbenchTab(page);
    assertState(collaborationBeforeRefresh.tab === "collaboration" && collaborationBeforeRefresh.panel === "collaboration", "Collaboration tab did not become active before refresh", collaborationBeforeRefresh);
    await renderRun(page, "collecting_required");
    const collaborationAfterRefresh = await activeWorkbenchTab(page);
    assertState(collaborationAfterRefresh.tab === "collaboration" && collaborationAfterRefresh.panel === "collaboration", "Collaboration tab did not survive workbench refresh", collaborationAfterRefresh);
    const contentTeamLayout = await page.evaluate(() => {
      const body = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-expanded-body");
      const panel = document.querySelector("#expertTeamWorkspacePanel [data-expert-team-tab-panel='collaboration']");
      const list = panel?.querySelector(".expert-team-member-list");
      return {
        hasVerticalList: Boolean(list),
        rowCount: panel?.querySelectorAll(".expert-team-member-row").length || 0,
        avatarCount: panel?.querySelectorAll(".expert-team-member-avatar").length || 0,
        currentCount: panel?.querySelectorAll(".expert-team-member-row.running .expert-team-member-state").length || 0,
        noBodyOverflow: body ? body.scrollHeight <= body.clientHeight + 4 : false,
        hasHorizontalStrip: Boolean(panel?.querySelector(".expert-team-member-strip")),
        scrollWidth: list ? list.scrollWidth : 0,
        clientWidth: list ? list.clientWidth : 0,
      };
    });
    assertState(
      contentTeamLayout.hasVerticalList && contentTeamLayout.rowCount === 5 && contentTeamLayout.avatarCount >= 5 && contentTeamLayout.currentCount === 1 && contentTeamLayout.noBodyOverflow && !contentTeamLayout.hasHorizontalStrip && contentTeamLayout.scrollWidth <= contentTeamLayout.clientWidth + 1,
      "Collaboration tab does not show the 5-person content team in one screen",
      contentTeamLayout
    );
    await page.screenshot({ path: path.join(outDir, "expert-team-plan-a-collaboration-tab-content-team.png"), fullPage: false });
    await renderRun(page, "collecting_required", { run_id: "electron-plan-a-research-run", team_id: "deep-research-team" });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='collaboration']");
    const researchTeamLayout = await page.evaluate(() => {
      const body = document.querySelector("#expertTeamWorkspacePanel .expert-team-panel-expanded-body");
      const panel = document.querySelector("#expertTeamWorkspacePanel [data-expert-team-tab-panel='collaboration']");
      const list = panel?.querySelector(".expert-team-member-list");
      return {
        text: panel?.textContent.replace(/\s+/g, " ").trim() || "",
        rowCount: panel?.querySelectorAll(".expert-team-member-row").length || 0,
        avatarCount: panel?.querySelectorAll(".expert-team-member-avatar").length || 0,
        noBodyOverflow: body ? body.scrollHeight <= body.clientHeight + 4 : false,
        scrollWidth: list ? list.scrollWidth : 0,
        clientWidth: list ? list.clientWidth : 0,
      };
    });
    assertState(
      researchTeamLayout.text.includes("深度材料研究团") && researchTeamLayout.rowCount === 6 && researchTeamLayout.avatarCount >= 6 && researchTeamLayout.noBodyOverflow && researchTeamLayout.scrollWidth <= researchTeamLayout.clientWidth + 1,
      "Collaboration tab does not dynamically render the 6-person research team in one screen",
      researchTeamLayout
    );
    await page.screenshot({ path: path.join(outDir, "expert-team-plan-a-collaboration-tab-research-team.png"), fullPage: false });
    await renderRun(page, "collecting_required", { run_id: "electron-plan-a-stable-run" });
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='collaboration']");
    await renderRun(page, "generating", { run_id: "electron-plan-a-stable-run" });
    const collaborationAfterStateRefresh = await activeWorkbenchTab(page);
    assertState(collaborationAfterStateRefresh.tab === "collaboration" && collaborationAfterStateRefresh.panel === "collaboration", "Collaboration tab did not survive same-run state refresh", collaborationAfterStateRefresh);
    await renderRun(page, "collecting_required", { run_id: "electron-plan-a-new-run" });
    const newRunTab = await activeWorkbenchTab(page);
    assertState(newRunTab.tab === "todo" && newRunTab.panel === "todo", "A different expert-team run inherited the previous run tab", newRunTab);
    await page.click("#expertTeamWorkspacePanel [data-expert-team-workspace-tab='todo']");

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
    assertState(!invalid.panelText.includes("专家团正在生成") && !invalid.panelText.includes("阶段成果待复核"), "Generated-invalid state is mixed with running or review state", invalid);
    assertState(invalid.dockHidden && invalid.chatConfirmButtons === 0, "Generated-invalid state leaks duplicate actions", invalid);

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
    assertState(!expanded.collapsed && expanded.panelText.includes("专家团协作状态"), "Capsule action did not expand the workbench", expanded);

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
        path.join(outDir, "expert-team-plan-a-review-scroll-preserved.png"),
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
