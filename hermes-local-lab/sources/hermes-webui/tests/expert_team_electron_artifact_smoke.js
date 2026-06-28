#!/usr/bin/env node
/*
 * Electron smoke for expert-team artifact actions.
 *
 * This launches the desktop shell, not a standalone browser tab. Set
 * PLAYWRIGHT_NODE_PATH to a local playwright package path when the repo does
 * not install Playwright as a Node dependency.
 */
const fs = require("fs");
const path = require("path");

function loadPlaywright() {
  const candidate = process.env.PLAYWRIGHT_NODE_PATH || "playwright";
  return require(candidate);
}

const { _electron } = loadPlaywright();

const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
const appDir = path.join(repoRoot, "apps", "taiji-desktop");
const labDir = path.join(repoRoot, "hermes-local-lab");
const electronBin = path.join(
  appDir,
  "node_modules",
  "electron",
  "dist",
  "Electron.app",
  "Contents",
  "MacOS",
  "Electron"
);
const outDir = path.join(repoRoot, "output", "playwright");

function assertState(condition, message, detail) {
  if (!condition) {
    const suffix = detail ? `\n${JSON.stringify(detail, null, 2)}` : "";
    throw new Error(`${message}${suffix}`);
  }
}

async function prepareSession(page, workspace) {
  await page.evaluate(async ({ workspace }) => {
    document.documentElement.dataset.taijiDesktop = "1";
    document.documentElement.dataset.skin = "taiji-light-glass";
    window._uiVisibility = Object.assign({}, window._uiVisibility || {}, {
      composer: {
        profile: true,
        workspace_files: true,
        workspace_switcher: true,
        model: true,
        reasoning: true,
        toolsets: true,
        quota: true,
      },
    });
    if (typeof applyUiVisibility === "function") applyUiVisibility();

    const response = await fetch("/api/session/new", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace }),
    });
    if (!response.ok) {
      throw new Error(`session/new failed ${response.status}: ${await response.text()}`);
    }
    const payload = await response.json();
    if (!payload.session || !payload.session.session_id) {
      throw new Error(`session/new returned no session: ${JSON.stringify(payload)}`);
    }
    S.session = payload.session;
    S.messages = [{ role: "assistant", content: "专家团生成结果已写入当前对话。" }];
    if (typeof syncTopbar === "function") syncTopbar();
    if (typeof renderMessages === "function") renderMessages();
    if (typeof switchPanel === "function") await switchPanel("chat");
    const onboarding = document.getElementById("onboardingOverlay");
    if (onboarding) {
      onboarding.remove();
    }
    if (typeof loadDir === "function") {
      try { await loadDir("."); } catch (_) {}
    }
  }, { workspace });
}

async function renderExpertCard(page, artifact) {
  await page.evaluate(({ artifact }) => {
    const resultContent = String(artifact.content || "").trim();
    const resultOutput = resultContent
      ? {
          id: "stage-delivery",
          task_id: "delivery",
          phase: "交付",
          title: "专家团生成结果",
          label: "专家团生成结果",
          kind: "chat",
          status: "approved",
          content: resultContent,
          summary: "交付成果已生成，请查看完整成果。",
          preview: resultContent.replace(/\s+/g, " ").slice(0, 360),
          content_length: resultContent.length,
          has_long_content: resultContent.length > 720,
          note: "已写入当前对话",
          locator: "chat",
          artifact_id: "",
        }
      : null;
    const base = {
      type: "expert-team",
      kind: "expert_team",
      runId: `expert-team-electron-${artifact.kind || "file"}`,
      sessionId: S.session.session_id,
      sourceSessionId: S.session.session_id,
      team: { id: "content-creator-team", title: "内容创作专家团" },
      subtitle: "Electron app 端产物入口验收",
      status: "done",
      statusLabel: "已完成",
      phase: "交付",
      phases: ["需求确认", "生成初稿", "打磨发布", "交付"],
      progress: { done: 4, total: 4 },
      members: [
        { id: "flow", name: "流程编排", status: "done" },
        { id: "writer", name: "文案创作专家", status: "done" },
        { id: "image", name: "配图专家", status: "done" },
        { id: "review", name: "审稿润色", status: "done" },
      ],
      tasks: [
        { id: "direction", title: "需求确认", worker_name: "流程编排", status: "done", status_label: "完成" },
        { id: "draft", title: "起草办公材料初稿", worker_name: "文案创作专家", status: "done", status_label: "完成" },
        { id: "image", title: "生成版式和配图建议", worker_name: "配图专家", status: "done", status_label: "完成" },
        { id: "delivery", title: "交付整理", worker_name: "审稿润色", status: "done", status_label: "完成" },
      ],
      artifacts: [artifact],
      questions: [],
      stageReview: resultOutput
        ? {
            task_id: "delivery",
            phase: "交付",
            title: "最终成果待确认",
            status: "done",
            is_final_stage: true,
            output: resultOutput,
          }
        : {},
      stageOutputs: resultOutput ? [resultOutput] : [],
    };
    renderWriteflowStatusDock(base);
    if (typeof focusExpertTeamBottomDock === "function") focusExpertTeamBottomDock(null);
  }, { artifact });
}

function buildConfirmationRun(sessionId, answers) {
  const topicAnswer = answers.topic || "";
  const audienceAnswer = answers.audience || "";
  const boundaryAnswer = answers.boundary || "";
  const optionalSkipped = answers.__skip_optional === true;
  const requiredComplete = Boolean(topicAnswer && audienceAnswer);
  const complete = Boolean(requiredComplete && (boundaryAnswer || optionalSkipped));
  const questions = [
    {
      id: "topic",
      title: "这次要编制哪类办公材料，主题是什么？",
      type: "text",
      status: topicAnswer ? "answered" : "pending",
      answer: topicAnswer,
      required: true,
    },
    {
      id: "audience",
      title: "材料面向哪些对象，使用场景是什么？",
      type: "text",
      status: audienceAnswer ? "answered" : "pending",
      answer: audienceAnswer,
      required: true,
    },
    {
      id: "boundary",
      title: "有哪些已知素材、口径要求、篇幅或表述边界？",
      type: "text",
      status: boundaryAnswer ? "answered" : (optionalSkipped ? "skipped" : "pending"),
      answer: boundaryAnswer,
      required: false,
    },
  ];
  const pendingQuestions = questions.filter((question) => !["answered", "skipped"].includes(question.status));
  const primaryQuestion = pendingQuestions[0] || null;
  const primaryConfirmation = primaryQuestion
    ? {
        id: `question:${primaryQuestion.id}`,
        kind: "question",
        title: primaryQuestion.title,
        description: primaryQuestion.required === false ? "可选补充，补充后结果更准确；也可以跳过后开始生成。" : "请先补充必填需求，专家团再继续推进。",
        fields: [{ id: primaryQuestion.id, type: "text", required: primaryQuestion.required !== false, options: [] }],
        actions: primaryQuestion.required === false ? { submit: "answer", skip: "answer/skip_optional" } : { submit: "answer" },
        source_task_id: "",
        origin: "",
        status: "pending",
      }
    : {};
  return {
    run_id: "expert-team-electron-confirmation",
    session_id: sessionId,
    team_id: "content-creator-team",
    team_title: "内容创作专家团",
    title: "Electron app 端需求确认验收",
    status: complete ? "running" : "waiting_user",
    status_label: complete ? "执行中" : "待确认",
    phase: complete ? "生成初稿" : "需求确认",
    phase_progress: { current: complete ? "生成初稿" : "需求确认", done: complete ? 1 : 0, total: 4 },
    progress: { done: complete ? 1 : 0, total: 4 },
    display_progress: { done: complete ? 1 : 0, total: 4 },
    members: [
      { id: "flow", name: "流程编排", status: complete ? "完成" : "待确认" },
      { id: "writer", name: "文案创作专家", status: complete ? "执行中" : "待命" },
      { id: "image", name: "配图专家", status: "待命" },
      { id: "review", name: "审稿润色", status: "待命" },
    ],
    display_tasks: [
      { id: "direction", title: "需求确认", worker_name: "流程编排", status: complete ? "done" : "waiting", status_label: complete ? "完成" : "待确认" },
      { id: "draft", title: "起草办公材料初稿", worker_name: "文案创作专家", status: complete ? "running" : "idle", status_label: complete ? "执行中" : "待执行" },
      { id: "image", title: "生成版式和配图建议", worker_name: "配图专家", status: "idle", status_label: "待执行" },
      { id: "delivery", title: "交付整理", worker_name: "审稿润色", status: "idle", status_label: "待执行" },
    ],
    artifacts: [],
    questions,
    view: {
      status: complete ? "running" : "waiting_user",
      status_label: complete ? "执行中" : "待确认",
      execution_status: complete ? "running" : "waiting_user",
      phase_progress: { current: complete ? "生成初稿" : "需求确认", done: complete ? 1 : 0, total: 4 },
      intake: {
        required_pending: pendingQuestions.filter((question) => question.required !== false).map((question) => question.id),
        optional_pending: pendingQuestions.filter((question) => question.required === false).map((question) => question.id),
        optional_status: boundaryAnswer ? "answered" : (optionalSkipped ? "skipped" : "pending"),
      },
      pending_questions: pendingQuestions,
      pending_confirmations: primaryQuestion ? [primaryConfirmation] : [],
      primary_confirmation: primaryConfirmation,
      review_items: [],
      actions: {
        can_answer: !complete,
        can_resume: false,
        can_cancel: complete,
      },
      health: {
        needs_resume: false,
        active_stream_id: complete ? "electron-confirmation-stream" : "",
        last_error: "",
      },
    },
  };
}

async function renderConfirmationCard(page, run) {
  await page.evaluate(({ run }) => {
    const card = _expertTeamStatusCardFromRun(run, { session_id: run.session_id, team_id: run.team_id });
    renderWriteflowStatusDock(card);
    if (typeof syncExpertTeamChatConfirmationCard === "function") syncExpertTeamChatConfirmationCard(card);
    if (typeof focusExpertTeamBottomDock === "function") focusExpertTeamBottomDock(null);
  }, { run });
}

async function main() {
  assertState(fs.existsSync(electronBin), `Electron binary not found: ${electronBin}`);
  fs.mkdirSync(outDir, { recursive: true });
  const tmpRoot = fs.mkdtempSync(path.join(outDir, "electron-artifact-runtime-"));
  const workspace = path.join(tmpRoot, "workspace");
  fs.mkdirSync(path.join(workspace, "articles"), { recursive: true });
  fs.writeFileSync(
    path.join(workspace, "articles", "expert-team-electron.md"),
    "# 专家团生成结果\n\n这是 Electron app 端验收文件。\n",
    "utf-8"
  );

  const env = {
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
  };

  const app = await _electron.launch({
    executablePath: electronBin,
    args: [appDir],
    env,
    timeout: 90000,
  });
  let page;
  try {
    page = await app.firstWindow({ timeout: 90000 });
    await page.waitForLoadState("domcontentloaded", { timeout: 90000 });
    await page.waitForFunction(
      () => (
        location.href.includes("taiji_desktop=1") &&
        typeof renderWriteflowStatusDock === "function" &&
        typeof openExpertTeamChatDelivery === "function"
      ),
      { timeout: 90000 }
    );

    const shellState = await page.evaluate(() => ({
      href: location.href,
      desktop: document.documentElement.dataset.taijiDesktop,
      uiScript: Array.from(document.scripts).map((script) => script.src).find((src) => src.includes("/static/ui.js")) || "",
      hasChatDeliveryHandler: typeof openExpertTeamChatDelivery === "function",
    }));
    assertState(shellState.desktop === "1", "Electron shell did not load desktop mode", shellState);
    assertState(shellState.uiScript.includes("/static/ui.js?v="), "Electron shell did not load cache-busted ui.js", shellState);
    assertState(shellState.hasChatDeliveryHandler, "Electron shell did not load chat delivery handler", shellState);

    await prepareSession(page, workspace);
    const sessionId = await page.evaluate(() => S.session.session_id);
    const confirmationAnswers = {};
    await page.route("**/api/expert-teams/answer", async (route) => {
      const body = JSON.parse(route.request().postData() || "{}");
      Object.assign(confirmationAnswers, body.answers || {});
      if (body.skip_optional === true) confirmationAnswers.__skip_optional = true;
      const run = buildConfirmationRun(sessionId, confirmationAnswers);
      const responseBody = { ok: true, session_id: sessionId, run };
      if (confirmationAnswers.__skip_optional === true) {
        responseBody.stream_id = "electron-optional-stream";
        responseBody.pending_started_at = Date.now() / 1000;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(responseBody),
      });
    });
    await renderConfirmationCard(page, buildConfirmationRun(sessionId, confirmationAnswers));
    await page.waitForSelector("#writeflowStatusDock .expert-team-question-summary button", { timeout: 10000 });
    const uniqueConfirmationState = await page.evaluate(() => {
      const messageText = document.querySelector("#msgInner")?.textContent.replace(/\s+/g, " ").trim() || "";
      return {
        lifecycle: Boolean(document.querySelector("#msgInner .expert-team-lifecycle-card")),
        chatActionCards: document.querySelectorAll("#msgInner .expert-team-chat-confirmation-card").length,
        chatConfirmButtons: Array.from(document.querySelectorAll("#msgInner button")).filter((button) => button.textContent.includes("去确认")).length,
        dockConfirmButtons: Array.from(document.querySelectorAll("#writeflowStatusDock button")).filter((button) => button.textContent.includes("打开需求确认") || button.textContent.includes("去确认")).length,
        messageText,
      };
    });
    assertState(uniqueConfirmationState.lifecycle, "Expert-team creation did not render passive lifecycle state", uniqueConfirmationState);
    assertState(uniqueConfirmationState.chatActionCards === 0, "Chat area still renders actionable confirmation cards", uniqueConfirmationState);
    assertState(uniqueConfirmationState.chatConfirmButtons === 0, "Chat area still has a 去确认 action", uniqueConfirmationState);
    assertState(uniqueConfirmationState.dockConfirmButtons >= 1, "Bottom dock does not expose the unique confirmation action", uniqueConfirmationState);
    await page.click("#writeflowStatusDock .expert-team-question-summary button");
    await page.waitForSelector("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current textarea", { timeout: 10000 });
    let confirmationState = await page.evaluate(() => {
      const workspace = document.querySelector("#writeflowStatusDock .expert-team-confirmation-workspace");
      const popover = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden])");
      const current = document.querySelector("#writeflowStatusDock .status-card-expert-question.pending.is-current");
      const input = current && current.querySelector("[data-expert-team-answer-input]");
      const button = current && current.querySelector(".status-card-expert-question-submit");
      const phases = document.querySelector("#writeflowStatusDock .expert-team-panel-phases");
      const workspaceRect = workspace && workspace.getBoundingClientRect();
      const phasesRect = phases && phases.getBoundingClientRect();
      return {
        workspaceText: workspace ? workspace.textContent.replace(/\s+/g, " ").trim() : "",
        popoverText: popover ? popover.textContent.replace(/\s+/g, " ").trim() : "",
        inputAria: input ? input.getAttribute("aria-label") || "" : "",
        inputUsable: Boolean(input && !input.disabled && input.offsetParent !== null),
        buttonText: button ? button.textContent.replace(/\s+/g, " ").trim() : "",
        buttonDisabled: Boolean(button && button.disabled),
        beforePhases: Boolean(workspaceRect && phasesRect && workspaceRect.top <= phasesRect.top),
      };
    });
    assertState(confirmationState.workspaceText.includes("必填需求待确认"), "Confirmation workspace is not the first task", confirmationState);
    assertState(confirmationState.popoverText.includes("需求确认 1/3"), "Question popover is not on the first item", confirmationState);
    assertState(confirmationState.popoverText.includes("请先填写"), "Question popover does not show the empty required state", confirmationState);
    assertState(confirmationState.inputAria.includes("这次要编制哪类办公材料"), "Confirmation textarea lacks a precise accessible name", confirmationState);
    assertState(confirmationState.inputUsable, "Confirmation textarea is not usable after opening popover", confirmationState);
    assertState(confirmationState.buttonText === "请先填写" && confirmationState.buttonDisabled, "Empty required confirmation is not disabled", confirmationState);
    assertState(confirmationState.beforePhases, "Confirmation workspace is not above progress content", confirmationState);
    await page.fill("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current textarea", "本地优先 AI 助理");
    await page.waitForFunction(() => {
      const button = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current .status-card-expert-question-submit");
      return button && !button.disabled && button.textContent.includes("确认并下一题");
    }, { timeout: 5000 });
    await page.click("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current .status-card-expert-question-submit");
    await page.waitForFunction(() => {
      const workspace = document.querySelector("#writeflowStatusDock .expert-team-confirmation-workspace");
      const popover = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden])");
      const input = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current textarea");
      return workspace && popover && popover.textContent.includes("需求确认 2/3") &&
        input && !input.disabled && input.offsetParent !== null &&
        (input.getAttribute("aria-label") || "").includes("材料面向哪些对象");
    }, { timeout: 10000 });
    await page.fill("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current textarea", "企业管理者");
    await page.waitForFunction(() => {
      const button = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current .status-card-expert-question-submit");
      return button && !button.disabled;
    }, { timeout: 5000 });
    await page.click("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current .status-card-expert-question-submit");
	    await page.waitForFunction(() => {
	      const card = document.querySelector("#writeflowStatusDock .status-card-writeflow");
	      const text = document.querySelector("#writeflowStatusDock")?.textContent || "";
	      const input = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current textarea");
	      const skip = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .expert-team-question-skip");
      return card && card.classList.contains("is-expanded") &&
        text.includes("生成尚未开始") &&
        input && !input.disabled && input.offsetParent !== null &&
        (input.getAttribute("aria-label") || "").includes("已知素材") &&
	        skip && skip.textContent.includes("跳过并开始生成");
	    }, { timeout: 10000 });
	    const optionalSelector = "#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current textarea";
	    await page.fill(optionalSelector, "这是一段刷新保护草稿");
	    await page.focus(optionalSelector);
	    await page.waitForFunction((selector) => document.activeElement === document.querySelector(selector), optionalSelector, { timeout: 3000 });
	    await page.waitForTimeout(6200);
	    const notAutoStarted = await page.evaluate(() => {
	      const text = document.querySelector("#writeflowStatusDock")?.textContent || "";
	      const input = document.querySelector("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .status-card-expert-question.pending.is-current textarea");
	      const active = document.activeElement;
	      return {
	        text: text.replace(/\s+/g, " ").trim(),
	        activeStream: Boolean(S && S.activeStreamId),
	        draft: input ? input.value : "",
	        activeIsInput: Boolean(input && document.activeElement === input),
	        activeTag: active ? active.tagName : "",
	        activeClass: active ? active.className || "" : "",
	        activeText: active ? active.textContent.replace(/\s+/g, " ").trim().slice(0, 80) : "",
	      };
	    });
	    assertState(!notAutoStarted.activeStream, "Optional pending state started stream before explicit skip", notAutoStarted);
	    assertState(notAutoStarted.text.includes("生成尚未开始"), "Optional pending state did not keep the waiting copy", notAutoStarted);
	    assertState(notAutoStarted.draft === "这是一段刷新保护草稿", "Silent refresh dropped the optional answer draft", notAutoStarted);
	    assertState(notAutoStarted.activeIsInput, "Silent refresh did not preserve focus in the question popover", notAutoStarted);
	    await page.fill(optionalSelector, "");
	    await page.click("#writeflowStatusDock .expert-team-question-popover:not([hidden]) .expert-team-question-skip");
    await page.waitForFunction(() => {
      const card = document.querySelector("#writeflowStatusDock .status-card-writeflow");
      const toast = document.getElementById("toast");
      const panelText = document.querySelector("#writeflowStatusDock")?.textContent || "";
      return card && card.classList.contains("is-expanded") &&
        toast && (toast.dataset.toastMessage || "").includes("需求已确认，正在进入生成") &&
        panelText.includes("生成初稿") &&
        panelText.includes("已跳过");
    }, { timeout: 10000 });
    for (const viewport of [
      { width: 1024, height: 720 },
      { width: 1280, height: 720 },
      { width: 1440, height: 900 },
    ]) {
      await page.setViewportSize(viewport);
      await page.waitForTimeout(250);
      const viewportState = await page.evaluate(() => {
        const dock = document.querySelector("#writeflowStatusDock .status-card-writeflow");
        const primary = document.querySelector("#writeflowStatusDock .expert-team-panel-priority-card button:not(:disabled), #writeflowStatusDock .expert-team-panel-retry, #writeflowStatusDock .expert-team-panel-resume");
        const rect = primary && primary.getBoundingClientRect();
        return {
          expanded: Boolean(dock && dock.classList.contains("is-expanded")),
          hasPrimary: Boolean(primary),
          buttonText: primary ? primary.textContent.replace(/\s+/g, " ").trim() : "",
          inViewport: Boolean(rect && rect.left >= 0 && rect.top >= 0 && rect.right <= window.innerWidth && rect.bottom <= window.innerHeight),
        };
      });
      assertState(viewportState.expanded, `Expert dock is not expanded at ${viewport.width}px`, viewportState);
      assertState(viewportState.hasPrimary && viewportState.inViewport, `Primary expert-team control is not visible at ${viewport.width}px`, viewportState);
      await page.screenshot({ path: path.join(outDir, `expert-team-electron-${viewport.width}.png`), fullPage: true });
    }
    await page.screenshot({ path: path.join(outDir, "expert-team-electron-confirmation-workspace.png"), fullPage: true });

    await renderExpertCard(page, {
      id: "draft",
      label: "专家团生成结果",
      path: "articles/expert-team-electron.md",
      kind: "md",
      exists: true,
      openable: true,
      download_name: "专家团生成结果.md",
    });
    await page.click("#writeflowStatusDock .expert-team-panel-artifact-open");
    await page.waitForFunction(
      () => {
        const preview = document.getElementById("previewArea");
        const pathText = document.getElementById("previewPathText");
        return preview && preview.classList.contains("visible") &&
          pathText && pathText.textContent.includes("expert-team-electron.md");
      },
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(outDir, "expert-team-electron-file-artifact.png"), fullPage: true });

	    const longResultContent = [
	      "# 迎峰度夏保供电 6 月重点工作推进情况汇报",
	      "",
	      "## 阶段目标",
	      "面向公司分管领导汇报本月重点工作进展、存在问题和下一步安排。",
	      "",
	      "## 阶段产物",
	      "已形成办公材料初稿，包含完成工作、风险隐患、协同事项和后续安排。",
	      "",
	      "## 待人工补充事项",
	      "1. 请补充本月负荷峰值和重点线路名称。",
	      "2. 请核对跨部门协同事项责任人。",
	      "",
	      "## 交付后核对事项",
	      "核对数据、名称、时间、责任部门后即可进入内部流转。",
	      "",
	      "这一段是只应出现在完整预览中的长正文标识。".repeat(24),
	    ].join("\\n");
	    await page.evaluate(({ content }) => {
	      S.messages = [
	        { role: "user", content: "召唤内容创作专家团：起草迎峰度夏保供电工作汇报", timestamp: Date.now() / 1000 - 1 },
	        { role: "assistant", content, timestamp: Date.now() / 1000 },
	      ];
	      if (typeof renderMessages === "function") renderMessages();
	    }, { content: longResultContent });
	    await renderExpertCard(page, {
	      id: "expert-team-chat-delivery",
	      label: "专家团生成结果",
	      path: "",
	      kind: "chat",
	      exists: true,
	      openable: false,
	      note: "已写入当前对话",
	      content: longResultContent,
	    });
	    await page.waitForSelector("#msgInner .expert-team-result-card", { timeout: 10000 });
	    const compactMessageState = await page.evaluate(() => {
	      const resultCard = document.querySelector("#msgInner .expert-team-result-card");
	      const body = resultCard && resultCard.closest(".msg-body");
	      return {
	        hasResultCard: Boolean(resultCard),
	        text: resultCard ? resultCard.textContent.replace(/\s+/g, " ").trim() : "",
	        bodyText: body ? body.textContent.replace(/\s+/g, " ").trim() : "",
	      };
	    });
	    assertState(compactMessageState.hasResultCard, "Long expert-team delivery did not render as a compact result card", compactMessageState);
	    assertState(compactMessageState.text.includes("查看完整成果"), "Compact result card does not expose full-result action", compactMessageState);
	    assertState(compactMessageState.text.includes("定位原文"), "Compact result card does not expose source location action", compactMessageState);
	    assertState(!compactMessageState.bodyText.includes("这一段是只应出现在完整预览中的长正文标识这一段是只应出现在完整预览中的长正文标识"), "Long markdown was expanded directly in the chat body", compactMessageState);
	    const openedFromResultCard = await page.evaluate(() => {
	      if (typeof hideExpertTeamWorkspacePanel === "function") hideExpertTeamWorkspacePanel(null);
	      const button = document.querySelector("#msgInner .expert-team-result-card [onclick*='openExpertTeamResultViewer']");
	      if (!button) return false;
	      button.click();
	      return true;
	    });
	    assertState(openedFromResultCard, "Compact result card disappeared before it could be opened");
	    await page.waitForFunction(
	      () => {
	        const viewer = document.querySelector("#expertTeamResultViewer:not([hidden])");
	        return viewer && viewer.textContent.includes("完整预览中的长正文标识") &&
	          viewer.textContent.includes("定位原文");
	      },
	      undefined,
	      { timeout: 5000 }
	    );
	    await page.click("#expertTeamResultViewer [data-expert-team-result-raw-idx]");
	    await page.waitForFunction(
	      () => {
	        const viewer = document.querySelector("#expertTeamResultViewer");
	        return viewer && viewer.hidden && document.querySelector("#msgInner .expert-team-result-source-focus");
	      },
	      undefined,
	      { timeout: 5000 }
	    );
	    await page.evaluate(() => {
	      if (typeof showExpertTeamWorkspacePanel === "function") showExpertTeamWorkspacePanel(null);
	    });
	    await page.waitForTimeout(200);
	    const chatState = await page.evaluate(() => {
	      const card = document.querySelector("#writeflowStatusDock .status-card-writeflow");
	      const priority = document.querySelector("#writeflowStatusDock .expert-team-panel-priority-card.artifact");
      const button = priority && priority.querySelector(".expert-team-panel-artifact-open");
      return {
        expanded: Boolean(card && card.classList.contains("is-expanded")),
        text: priority ? priority.textContent.replace(/\s+/g, " ").trim() : "",
        buttonText: button ? button.textContent.replace(/\s+/g, " ").trim() : "",
        buttonDisabled: Boolean(button && button.disabled),
        buttonChatDelivery: Boolean(button && button.dataset.expertTeamChatDelivery === "1"),
      };
    });
    assertState(chatState.expanded, "Chat delivery card is not expanded", chatState);
	    assertState(chatState.text.includes("已写入当前对话"), "Chat delivery is not labeled as conversation result", chatState);
	    assertState(!chatState.text.includes("1 个可打开"), "Chat delivery is still presented as openable", chatState);
	    assertState(chatState.buttonText.includes("查看对话结果"), "Chat delivery button has the wrong label", chatState);
	    assertState(!chatState.buttonDisabled && chatState.buttonChatDelivery, "Chat delivery button is not actionable", chatState);
	    await page.click("#writeflowStatusDock .expert-team-panel-artifact-open");
	    await page.waitForTimeout(600);
	    const viewerState = await page.evaluate(() => {
	      const viewer = document.querySelector("#expertTeamResultViewer");
	      const latestInfo = typeof _expertTeamLatestDeliveryMessageInfo === "function" ? _expertTeamLatestDeliveryMessageInfo() : null;
	      const cardInfo = typeof _expertTeamResultFromCard === "function" ? _expertTeamResultFromCard() : null;
	      return {
	        hasViewer: Boolean(viewer),
	        hidden: viewer ? viewer.hidden : null,
	        viewerText: viewer ? viewer.textContent.replace(/\s+/g, " ").trim().slice(0, 240) : "",
	        viewerHasMarker: Boolean(viewer && viewer.textContent.includes("完整预览中的长正文标识")),
	        viewerHasLocate: Boolean(viewer && viewer.textContent.includes("定位原文")),
	        latestInfo: latestInfo ? { title: latestInfo.title, len: latestInfo.contentLength, summary: latestInfo.summary } : null,
	        cardInfo: cardInfo ? { title: cardInfo.title, len: cardInfo.contentLength, summary: cardInfo.summary } : null,
	        dockText: document.querySelector("#writeflowStatusDock")?.textContent.replace(/\s+/g, " ").trim().slice(0, 240) || "",
	      };
	    });
	    assertState(
	      viewerState.hasViewer && viewerState.hidden === false &&
	        viewerState.viewerHasMarker,
	      "Chat delivery button did not open the result viewer",
	      viewerState
	    );
	    await page.screenshot({ path: path.join(outDir, "expert-team-electron-result-viewer.png"), fullPage: true });
	    await page.click("#expertTeamResultViewer .expert-team-result-viewer-head button");
	    const compactChatState = await page.evaluate(() => {
	      const summary = document.querySelector("#writeflowStatusDock .status-card-expert-dock-summary");
	      const approve = document.querySelector("#writeflowStatusDock .expert-team-stage-approve");
	      return {
	        text: summary ? summary.textContent.replace(/\s+/g, " ").trim() : "",
	        hasApprove: Boolean(approve),
	        dockText: document.querySelector("#writeflowStatusDock")?.textContent.replace(/\s+/g, " ").trim() || "",
	      };
	    });
	    assertState(compactChatState.text.includes("查看结果"), "Compact chat delivery summary should say 查看结果", compactChatState);
	    assertState(!compactChatState.text.includes("查看产物"), "Compact chat delivery summary still says 查看产物", compactChatState);
	    assertState(!compactChatState.hasApprove, "Completed expert team still exposes a stage approval action", compactChatState);
	    assertState(!compactChatState.dockText.includes("下一阶段建议"), "Completed expert team still hints at a next stage", compactChatState);
	    await page.screenshot({ path: path.join(outDir, "expert-team-electron-chat-delivery.png"), fullPage: true });
  } finally {
    await app.close().catch(() => {});
  }
  console.log("EXPERT TEAM ELECTRON ARTIFACT SMOKE PASSED");
  console.log(`workspace=${workspace}`);
}

main().catch((error) => {
  console.error("EXPERT TEAM ELECTRON ARTIFACT SMOKE FAILED");
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
