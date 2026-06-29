#!/usr/bin/env node
/*
 * Electron smoke for the rebuilt expert-team presenter.
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
  if (!condition) {
    throw new Error(`${message}${detail ? `\n${JSON.stringify(detail, null, 2)}` : ""}`);
  }
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
  const outputContent = [
    "阶段摘要：已完成办公材料初稿。",
    "正文草稿：标题：关于开展近期安全生产专项检查的通知",
    "",
    "为进一步压实安全生产责任，现就近期安全生产专项检查有关事项通知如下。",
    "",
    "一、检查范围",
    "覆盖各部门、各基层单位重点场所、重点设备和重点作业环节。",
    "",
    "二、时间安排",
    "自本周起至月底前完成自查、抽查和问题整改闭环。",
    "",
    "三、责任分工",
    "各责任部门按职责落实检查、整改、报送和复核工作。",
    "",
    "待补充事项：请补充检查联系人和报送邮箱。",
    "建议下一步：进入材料打磨。",
  ].join("\n");
  const presentation = {
    collecting_required: {
      title: "必须需求待确认",
      detail: "请先补充需求信息，专家团再继续推进。",
      primary_action: { id: "answer_required", label: "去确认", kind: "question_popover" },
    },
    collecting_optional: {
      title: "可选补充待处理",
      detail: "可继续补充材料，也可以跳过后开始生成。",
      primary_action: { id: "answer_optional", label: "补充或跳过", kind: "question_popover" },
    },
    generating: {
      title: "专家团正在生成",
      detail: "后台正在按当前阶段生成内容。",
      primary_action: { id: "cancel", label: "停止生成", kind: "danger" },
    },
    generated_invalid: {
      title: "草稿未通过校验",
      detail: "草稿未通过办公材料口径校验，请重新生成。",
      primary_action: { id: "regenerate", label: "重新生成", kind: "primary" },
      secondary_actions: [{ id: "view_result", label: "查看草稿", kind: "ghost" }],
    },
    awaiting_review: {
      title: "阶段成果待复核",
      detail: "阶段结果已生成，请查看后确认是否进入下一阶段。",
      primary_action: { id: "review_stage", label: "去复核", kind: "primary" },
      secondary_actions: [
        { id: "view_result", label: "查看成果", kind: "ghost" },
        { id: "approve_stage", label: "无修改，进入下一阶段", kind: "primary" },
        { id: "revise_stage", label: "需要修改", kind: "ghost" },
      ],
    },
    completed: {
      title: "专家团任务已完成",
      detail: "所有阶段已完成，结果已写入当前对话。",
      primary_action: { id: "view_result", label: "查看成果", kind: "primary" },
    },
  }[state];
  const requiredPending = state === "collecting_required";
  const optionalPending = state === "collecting_optional";
  const output = {
    id: "delivery-office-material",
    kind: "chat",
    title: "起草通知通报初稿",
    visible_title: "起草通知通报初稿",
    summary: "当前阶段已形成内部通知初稿，包含检查范围、时间安排、责任分工和报送要求。",
    preview: "关于开展近期安全生产专项检查的通知。",
    content: outputContent,
    content_length: outputContent.length,
    has_long_content: true,
    locator: "chat",
    artifact_id: "",
  };
  const currentStage = {
    id: state === "collecting_required" || state === "collecting_optional" ? "plan" : "draft",
    task_id: state === "collecting_required" || state === "collecting_optional" ? "plan" : "draft",
    index: state === "collecting_required" || state === "collecting_optional" ? 0 : 2,
    title: state === "collecting_required" || state === "collecting_optional" ? "流程安排" : "起草办公材料初稿",
    phase: state === "collecting_required" || state === "collecting_optional" ? "需求确认" : "生成初稿",
    worker_id: state === "collecting_required" || state === "collecting_optional" ? "director" : "writer",
    worker_name: state === "collecting_required" || state === "collecting_optional" ? "写作总导演" : "文案创作专家",
    status: state === "generating" ? "running" : state === "awaiting_review" ? "awaiting_review" : "pending",
  };
  const members = [
    { id: "director", name: "写作总导演", role: "流程编排", status: state === "collecting_required" || state === "collecting_optional" ? "等待确认" : "已完成", image: "static/assets/writeflow/member-workflow-producer.png" },
    { id: "material", name: "资料整理专家", role: "素材整理", status: "待命", image: "static/assets/writeflow/member-research-expert.png" },
    { id: "writer", name: "文案创作专家", role: "正文写作", status: state === "generating" ? "执行中" : state === "awaiting_review" || state === "completed" ? "待复核" : "待命", image: "static/assets/writeflow/member-writing-executor.png" },
    { id: "reviewer", name: "审稿专家", role: "审稿打磨", status: "待命", image: "static/assets/writeflow/member-editor.png" },
    { id: "delivery", name: "交付复核专家", role: "交付确认", status: state === "completed" ? "已完成" : "待命", image: "static/assets/writeflow/member-proofreader.png" },
  ];
  const tasks = [
    { id: "plan", title: "流程安排", phase: "流程安排", status: currentStage.id === "plan" ? (state === "collecting_required" || state === "collecting_optional" ? "pending" : "running") : "done", worker_id: "director", worker_name: "写作总导演" },
    { id: "materials", title: "素材整理", phase: "素材整理", status: state === "completed" ? "done" : currentStage.id === "draft" ? "done" : "pending", worker_id: "material", worker_name: "资料整理专家" },
    { id: "draft", title: "起草办公材料初稿", phase: "生成初稿", status: state === "generating" ? "running" : state === "awaiting_review" ? "awaiting_review" : state === "completed" ? "done" : "pending", worker_id: "writer", worker_name: "文案创作专家" },
    { id: "polish", title: "审稿打磨", phase: "审稿打磨", status: state === "completed" ? "done" : "pending", worker_id: "reviewer", worker_name: "审稿专家" },
    { id: "delivery", title: "交付确认", phase: "交付确认", status: state === "completed" ? "done" : "pending", worker_id: "delivery", worker_name: "交付复核专家" },
  ];
  const stageResult = state === "awaiting_review" || state === "completed" || state === "generated_invalid" ? {
    stage_id: currentStage.id,
    worker_id: currentStage.worker_id,
    summary: output.summary,
    deliverable: output.preview,
    review_items: [{ id: "ri-1", title: "请补充检查联系人和报送邮箱。", status: "pending", used_in_revision: false }],
    next_action: "请复核当前阶段成果，确认后进入下一阶段。",
    validation: state === "generated_invalid" ? { status: "fail", message: "草稿未通过办公材料口径校验。" } : { status: "pass", message: "" },
  } : {};
  const timelineEvents = [
    { type: "team_created", title: "专家团已创建", detail: "等待需求确认", member_id: "director", member_name: "写作总导演", member_image: "static/assets/writeflow/member-workflow-producer.png" },
    { type: "generation_started", title: "专家开始执行当前阶段", detail: currentStage.phase, member_id: currentStage.worker_id, member_name: currentStage.worker_name },
    { type: "generation_completed", title: "阶段成果已生成", detail: state === "awaiting_review" ? "等待复核" : "", member_id: currentStage.worker_id, member_name: currentStage.worker_name },
  ];
  const workspaceView = {
    visible: true,
    title: "专家团工作台",
    state,
    current_stage: currentStage,
    current_worker: members.find((member) => member.id === currentStage.worker_id) || {},
    phases: tasks,
    members,
    timeline: timelineEvents,
    stage_result: stageResult,
  };
  return {
    run_id: `electron-presenter-${state}`,
    session_id: sessionId,
    team_id: "content-creator-team",
    team_title: "内容创作专家团",
    title: "帮我起草一份内部通知，主题是近期安全生产专项检查安排",
    workflow_state: state,
    phase: state === "collecting_required" || state === "collecting_optional" ? "需求确认" : "生成初稿",
    questions: [
      { id: "topic", title: "这次要编制哪类办公材料，主题是什么？", placeholder: "例如：内部通知，主题是近期安全生产专项检查安排", required: true, status: requiredPending ? "pending" : "answered", answer: requiredPending ? "" : "内部通知，主题是近期安全生产专项检查安排" },
      { id: "audience", title: "材料面向哪些对象，使用场景是什么？", placeholder: "例如：公司各部门、各基层单位", required: true, status: requiredPending ? "pending" : "answered", answer: requiredPending ? "" : "公司各部门、各基层单位" },
      { id: "boundary", title: "有哪些已知素材、口径要求、篇幅或表述边界？", placeholder: "正式、简洁，包含检查范围、时间节点、责任分工和报送要求", required: true, status: requiredPending ? "pending" : "answered", answer: requiredPending ? "" : "正式、简洁" },
      { id: "optional_context", title: "还有没有可选补充材料或特别强调的点？", placeholder: "没有可直接跳过", required: false, status: optionalPending ? "pending" : "skipped", answer: "" },
    ],
    members,
    tasks,
    artifacts: state === "awaiting_review" || state === "completed" ? [{ id: output.id, kind: "chat", label: "结果已写入对话", exists: true }] : [],
    stage_outputs: state === "awaiting_review" || state === "completed" || state === "generated_invalid" ? [output] : [],
    view: {
      business_context: {
        material_type: "notice",
        visible_title: "起草通知通报初稿",
        style_contract: "采用内部通知通报口径。",
        forbidden_terms: [],
      },
      presentation: { state, visible_title: "起草通知通报初稿", result: output, summary: output.summary, ...presentation },
      workspace: workspaceView,
      dock: {
        state,
        title: presentation.title,
        detail: presentation.detail,
        primary_action: presentation.primary_action,
        secondary_actions: presentation.secondary_actions || [],
      },
      stage_result: stageResult,
      intake: {
        required_pending: requiredPending ? 3 : 0,
        optional_pending: optionalPending ? 1 : 0,
        optional_status: optionalPending ? "pending" : "skipped",
        questions: [],
      },
      primary_confirmation: requiredPending || optionalPending ? { type: "question", question_id: requiredPending ? "topic" : "optional_context", title: requiredPending ? "这次要编制哪类办公材料，主题是什么？" : "还有没有可选补充材料或特别强调的点？" } : state === "awaiting_review" ? { type: "stage_review", title: "阶段成果待复核" } : null,
      pending_confirmations: requiredPending || optionalPending || state === "awaiting_review" ? [{}] : [],
      review_items: [],
      stage_review: { display_state: state === "generating" ? "running" : state, actionable: state === "awaiting_review", output },
      phase_progress: { done: state === "completed" ? 3 : state === "awaiting_review" ? 1 : 0, total: 3, current: state === "completed" ? "交付" : state === "collecting_required" || state === "collecting_optional" ? "需求确认" : "生成初稿" },
      actions: {},
      timeline_events: timelineEvents,
    },
    ...overrides,
  };
}

async function renderRun(page, state, overrides) {
  await page.evaluate(({ state, overrides }) => {
    const run = window.__expertTeamRunFixture(S.session.session_id, state, overrides || {});
    const card = _expertTeamStatusCardFromRun(run, { session_id: S.session.session_id });
    renderWriteflowStatusDock(card);
    if (typeof focusExpertTeamBottomDock === "function") focusExpertTeamBottomDock(null);
  }, { state, overrides });
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
  let page;
  try {
    page = await app.firstWindow({ timeout: 90000 });
    await page.waitForLoadState("domcontentloaded", { timeout: 90000 });
    await page.waitForFunction(
      () => location.href.includes("taiji_desktop=1") &&
        typeof buildExpertTeamCardFromRun === "function" &&
        typeof renderExpertTeamDockFromPresentation === "function" &&
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
        prompt: "帮我起草一份内部通知，主题是近期安全生产专项检查安排",
        new_session: false,
      });
      if (!ok) throw new Error("sendExpertTeamAction returned false");
    });
    await page.waitForSelector("#writeflowStatusDock .status-card-expert-dock-button", { timeout: 10000 });
    await page.waitForSelector("#expertTeamWorkspacePanel:not([hidden])", { timeout: 10000 });
    const realStart = await page.evaluate(() => ({
      msgCount: Array.isArray(S.messages) ? S.messages.length : -1,
      chatText: document.querySelector("#msgInner")?.textContent.replace(/\s+/g, " ").trim() || "",
      dockText: document.querySelector("#writeflowStatusDock")?.textContent.replace(/\s+/g, " ").trim() || "",
      workspaceText: document.querySelector("#expertTeamWorkspacePanel")?.textContent.replace(/\s+/g, " ").trim() || "",
      workspaceVisible: Boolean(document.querySelector("#expertTeamWorkspacePanel:not([hidden])")),
      runIds: (Array.isArray(S.messages) ? S.messages : []).map((msg) => msg && msg.expert_team_run_id).filter(Boolean),
      memberAvatars: document.querySelectorAll("#expertTeamWorkspacePanel .expert-team-member-avatar img").length,
      timelineRows: document.querySelectorAll("#expertTeamWorkspacePanel .expert-team-timeline-item").length,
    }));
    assertState(realStart.msgCount >= 2, "Real expert-team start did not sync session messages immediately", realStart);
    assertState(realStart.chatText.includes("召唤内容创作专家团") && realStart.chatText.includes("专家团已创建"), "Real expert-team start did not render lifecycle messages in chat", realStart);
    assertState(realStart.dockText.includes("必须需求待确认") && realStart.dockText.includes("去确认"), "Real expert-team start did not render the dock action", realStart);
    assertState(realStart.runIds.length >= 2, "Session messages are missing expert-team run ids", realStart);
    assertState(realStart.workspaceVisible && realStart.workspaceText.includes("专家团工作台"), "Expert team workspace panel is not visible after real start", realStart);
    assertState(realStart.memberAvatars >= 2 && realStart.timelineRows >= 1, "Expert team members or timeline are not visible in the workspace after real start", realStart);

    await renderRun(page, "collecting_required");
    await page.waitForSelector("#writeflowStatusDock .status-card-expert-dock-button", { timeout: 10000 });
    const initial = await page.evaluate(() => ({
      dockText: document.querySelector("#writeflowStatusDock")?.textContent.replace(/\s+/g, " ").trim() || "",
      workspaceText: document.querySelector("#expertTeamWorkspacePanel")?.textContent.replace(/\s+/g, " ").trim() || "",
      chatConfirmCards: document.querySelectorAll("#msgInner .expert-team-chat-confirmation-card").length,
      directChatButtons: Array.from(document.querySelectorAll("#msgInner button")).filter((button) => button.textContent.includes("去确认")).length,
    }));
    assertState(initial.dockText.includes("必须需求待确认") && initial.dockText.includes("去确认"), "Collecting-required state is not driven by dock presentation", initial);
    assertState(initial.workspaceText.includes("专家团工作台") && initial.workspaceText.includes("流程安排"), "Collecting-required state did not keep the workspace visible", initial);
    assertState(initial.chatConfirmCards === 0 && initial.directChatButtons === 0, "Chat area still exposes duplicate expert-team confirmation", initial);

    await page.click("#writeflowStatusDock .status-card-expert-dock-button");
    await page.waitForSelector("#writeflowStatusDock .expert-team-question-popover:not([hidden]) textarea", { timeout: 10000 });
    await page.fill("#writeflowStatusDock .expert-team-question-popover:not([hidden]) textarea", "安全生产专项检查通知");
    await page.focus("#writeflowStatusDock .expert-team-question-popover:not([hidden]) textarea");
    await page.waitForTimeout(6500);
    const draftProtected = await page.evaluate(() => {
      const input = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden]) textarea");
      const popover = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden])");
      return { value: input ? input.value : "", popoverOpen: Boolean(popover) };
    });
    assertState(draftProtected.value === "安全生产专项检查通知" && draftProtected.popoverOpen, "Question draft or popover state was not preserved", draftProtected);

    await renderRun(page, "generating");
    const generating = await page.evaluate(() => ({
      text: document.querySelector("#writeflowStatusDock")?.textContent.replace(/\s+/g, " ").trim() || "",
      workspaceText: document.querySelector("#expertTeamWorkspacePanel")?.textContent.replace(/\s+/g, " ").trim() || "",
      cards: document.querySelectorAll("#writeflowStatusDock .status-card-expert-dock-summary").length,
    }));
    assertState(generating.cards === 1, "Generating state rendered more than one dock state", generating);
    assertState(generating.text.includes("专家团正在生成") && generating.text.includes("停止生成"), "Generating state lacks the single running action", generating);
    assertState(generating.workspaceText.includes("专家团工作台") && generating.workspaceText.includes("文案创作专家"), "Generating state did not show current expert in workspace", generating);
    assertState(!generating.text.includes("未检测到结果") && !generating.text.includes("阶段成果待复核"), "Generating state is mixed with result or missing states", generating);

    await renderRun(page, "awaiting_review");
    await page.waitForSelector("#expertTeamWorkspacePanel .expert-team-result-card", { timeout: 10000 });
    const review = await page.evaluate(() => ({
      text: document.querySelector("#writeflowStatusDock")?.textContent.replace(/\s+/g, " ").trim() || "",
      workspaceText: document.querySelector("#expertTeamWorkspacePanel")?.textContent.replace(/\s+/g, " ").trim() || "",
      resultCards: document.querySelectorAll("#expertTeamWorkspacePanel .expert-team-result-card").length,
    }));
    assertState(review.text.includes("阶段成果待复核") && review.resultCards === 1, "Awaiting review does not show one workspace result card", review);
    assertState(review.workspaceText.includes("查看完整成果") && !review.workspaceText.includes("公众号"), "Office-material result workspace is missing the result entry or still contains public-account wording", review);
    await page.click("#expertTeamWorkspacePanel .expert-team-result-card [data-expert-team-action='view_result']");
    await page.waitForSelector("#expertTeamResultViewer:not([hidden])", { timeout: 10000 });
    const viewer = await page.evaluate(() => ({
      text: document.querySelector("#expertTeamResultViewer")?.textContent.replace(/\s+/g, " ").trim() || "",
      height: document.querySelector("#expertTeamResultViewer .expert-team-result-viewer-panel")?.getBoundingClientRect().height || 0,
      viewport: window.innerHeight,
    }));
    assertState(viewer.text.includes("关于开展近期安全生产专项检查的通知"), "Result viewer did not open the full office-material draft", viewer);
    assertState(viewer.height < viewer.viewport, "Result viewer exceeds viewport height", viewer);

    await renderRun(page, "completed");
    const completed = await page.evaluate(() => ({
      text: document.querySelector("#writeflowStatusDock")?.textContent.replace(/\s+/g, " ").trim() || "",
    }));
    assertState(completed.text.includes("专家团任务已完成") && completed.text.includes("查看成果"), "Completed state does not close the workflow cleanly", completed);
    assertState(!completed.text.includes("下一阶段建议"), "Completed state still shows next-stage workflow copy", completed);

    await page.evaluate(() => {
      const viewer = document.getElementById("expertTeamResultViewer");
      if (viewer) viewer.hidden = true;
    });
    for (const width of [1024, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await renderRun(page, "awaiting_review");
      await page.screenshot({ path: path.join(outDir, `expert-team-refactor-${width}.png`), fullPage: false });
    }

    console.log("EXPERT TEAM ELECTRON SMOKE OK", JSON.stringify({ screenshots: [1024, 1280, 1440].map((width) => path.join(outDir, `expert-team-refactor-${width}.png`)) }, null, 2));
  } finally {
    if (app) await app.close().catch(() => {});
  }
}

main().catch((error) => {
  console.error("EXPERT TEAM ELECTRON SMOKE FAILED");
  console.error(error && error.stack || error);
  process.exit(1);
});
