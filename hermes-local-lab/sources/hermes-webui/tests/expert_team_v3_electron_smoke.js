#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

function loadPlaywright() {
  const moduleId = process.env.PLAYWRIGHT_NODE_PATH || 'playwright';
  try { return require(moduleId); }
  catch (error) { throw new Error(`Cannot resolve Playwright from ${moduleId}`, { cause: error }); }
}

function assert(condition, message, evidence) {
  if (!condition) throw new Error(`${message}\n${JSON.stringify(evidence || {}, null, 2)}`);
}

function command(cwd, executable, args) {
  return execFileSync(executable, args, { cwd, encoding: 'utf8' }).trim();
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function fixture(sessionId, publicState = 'awaiting_stage_confirmation', version = 7) {
  const awaitingStage = publicState === 'awaiting_stage_confirmation';
  const awaitingDelivery = publicState === 'awaiting_delivery_confirmation';
  const deliveryCompleted = publicState === 'completed';
  const output = {
    id: 'draft-1', kind: 'chat', title: '工作汇报阶段稿',
    content: '# 工作汇报\n\n## 一、工作开展情况\n已完成重点任务。\n\n## 二、存在问题\n部分数据待核实。\n\n## 三、下一步安排\n继续推进闭环。',
  };
  const stages = [
    { id: 'plan', task_id: 'plan', title: '任务规划', phase: '任务规划', status: 'done', worker_name: '写作总导演' },
    { id: 'materials', task_id: 'materials', title: '素材整理', phase: '素材整理', status: 'done', worker_name: '资料整理专家' },
    { id: 'draft', task_id: 'draft', title: '初稿撰写', phase: '初稿撰写', status: awaitingStage ? 'awaiting_review' : (publicState === 'executing' || publicState === 'revising' ? 'running' : 'done'), worker_name: '文案创作专家' },
    { id: 'polish', task_id: 'polish', title: '审稿打磨', phase: '审稿打磨', status: awaitingDelivery || deliveryCompleted ? 'done' : 'pending', worker_name: '审稿专家' },
    { id: 'delivery', task_id: 'delivery', title: '正式文档交付', phase: '正式文档交付', status: awaitingDelivery ? 'awaiting_review' : (deliveryCompleted ? 'done' : 'pending'), worker_name: '交付复核专家' },
  ];
  const presentation = {
    state: publicState, visible_title: '起草部门月度工作汇报',
    title: awaitingStage ? '阶段成果待确认' : (awaitingDelivery ? '最终文档待确认' : (deliveryCompleted ? '文档已交付' : '专家团正在执行')),
    detail: '请阅读阶段成果后决定是否修改。', result: output,
    primary_action: { id: 'review_stage', label: '去复核', kind: 'primary' },
    secondary_actions: [
      { id: 'approve_stage', label: '无修改，进入下一阶段', kind: 'primary' },
      { id: 'revise_stage', label: '需要修改', kind: 'secondary' },
    ],
  };
  return {
    run_id: 'et3-electron-run', session_id: sessionId, schema_version: 3,
    contract_version: 'expert-team-contract/v1', product_mode: 'standalone', version,
    workflow_state: deliveryCompleted ? 'completed' : (awaitingDelivery || awaitingStage ? 'awaiting_review' : publicState),
    phase: awaitingDelivery || deliveryCompleted ? '正式文档交付' : '初稿撰写',
    team_id: 'content-creator-team', team_title: '内容创作专家团', current_stage: awaitingDelivery || deliveryCompleted ? stages[4] : stages[2],
    document_brief: {
      status: 'confirmed', revision: 3, original_request: '起草部门月度工作汇报', exact_title: '部门月度工作汇报',
      document_type: 'work_report', purpose: '内部汇报', audience: '公司分管领导', source_refs: [],
      content_constraints: { required_sections: ['工作开展情况', '存在问题', '下一步工作安排'], must_include: [], must_avoid: [] },
    },
    questions: [], members: [], tasks: stages, artifacts: [], stage_outputs: [output],
    view: {
      product_mode: 'standalone', public_state: publicState,
      allowed_actions: awaitingStage ? ['stage_confirm', 'stage_revise'] : (awaitingDelivery
        ? ['delivery_open_document', 'delivery_open_folder', 'delivery_revise', 'delivery_confirm']
        : (deliveryCompleted ? ['delivery_open_document', 'delivery_open_folder'] : [])),
      stage_action_binding: awaitingStage ? {
        session_id: sessionId, run_id: 'et3-electron-run', expected_version: version,
        stage_id: 'draft', stage_attempt: 1, artifact_id: 'draft:1', artifact_sha256: 'a'.repeat(64),
      } : null,
      delivery_action_binding: awaitingDelivery || deliveryCompleted ? {
        session_id: sessionId, run_id: 'et3-electron-run', expected_version: version,
        stage_id: 'delivery', stage_attempt: 1, artifact_id: 'delivery:1', artifact_sha256: 'b'.repeat(64),
        delivery_attempt: 1, delivery_binding_sha256: 'c'.repeat(64), document_sha256: 'd'.repeat(64),
      } : null,
      standalone_delivery: awaitingDelivery || deliveryCompleted ? {
        document_name: '部门月度工作汇报.docx', delivery_attempt: 1, document_sha256: 'd'.repeat(64),
        quality_report_sha256: 'e'.repeat(64),
        automatic_check_summary: { status: 'passed', passed_count: 5, failed_count: 0, warning_count: 0, blocking_count: 0 },
      } : null,
      presentation,
      business_context: { visible_title: '起草部门月度工作汇报', material_type: 'work_report' },
      team: { id: 'content-creator-team', title: '内容创作专家团', members: [] },
      workflow: { stages, current_stage: awaitingDelivery || deliveryCompleted ? stages[4] : stages[2], progress: { done: deliveryCompleted ? 5 : (awaitingDelivery ? 4 : 2), total: 5, current: awaitingDelivery || deliveryCompleted ? '正式文档交付' : '初稿撰写', current_index: awaitingDelivery || deliveryCompleted ? 4 : 2 } },
      workspace: { visible: true, title: '专家团工作台', state: publicState, current_stage: awaitingDelivery || deliveryCompleted ? stages[4] : stages[2], stages },
      brief: {
        status: 'confirmed', revision: 3, original_request: '起草部门月度工作汇报', exact_title: '部门月度工作汇报',
        document_type: 'work_report', document_type_label: '工作汇报', purpose: '内部汇报', audience: '公司分管领导',
        required_sections: ['工作开展情况', '存在问题', '下一步工作安排'], editable: false, sources: [],
      },
      stage_result: { output, review_items: [{ id: 'r1', title: '补充关键指标和责任部门', phase: '待人工补充' }] },
      stage_review: { review_id: 'review-1', attempt: 1, actionable: true, output },
      review_items: [{ id: 'r1', title: '补充关键指标和责任部门', phase: '待人工补充' }],
      intake: { questions: [] },
      completion_gates: { content: { status: 'pending' }, document: { status: 'pending' }, local_confirmation: { status: 'pending' } },
      delivery_status: 'pending', timeline_events: [], actions: {},
    },
  };
}

async function main() {
  const { _electron } = loadPlaywright();
  const webuiDir = path.resolve(__dirname, '..');
  const repoRoot = path.resolve(webuiDir, '..', '..', '..');
  const electronHostRoot = process.env.TAIJI_ELECTRON_HOST_ROOT || process.env.TAIJI_MAIN_REPO_ROOT || repoRoot;
  const appDir = path.join(repoRoot, 'apps', 'taiji-desktop');
  const electronBin = path.join(electronHostRoot, 'apps', 'taiji-desktop', 'node_modules', 'electron', 'dist', 'Electron.app', 'Contents', 'MacOS', 'Electron');
  const pythonBin = process.env.HERMES_WEBUI_PYTHON || path.join(repoRoot, 'hermes-local-lab', 'sources', 'hermes-agent', 'venv', 'bin', 'python');
  const outDir = path.resolve(process.argv[process.argv.indexOf('--out-dir') + 1] || path.join(repoRoot, 'output', 'expert-team-v3'));
  assert(fs.existsSync(electronBin), 'Electron binary missing', { electronBin });
  assert(fs.existsSync(pythonBin), 'Worktree Python runtime missing', { pythonBin });
  fs.mkdirSync(outDir, { recursive: true });
  const runtime = fs.mkdtempSync(path.join(outDir, 'runtime-'));
  const workspace = path.join(runtime, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });

  const app = await _electron.launch({
    executablePath: electronBin,
    args: [appDir],
    env: {
      ...process.env,
      TAIJI_SOURCE_MODE: 'development', TAIJI_SOURCE_ROOT: repoRoot,
      TAIJI_AGENT_ROOT: path.join(repoRoot, 'hermes-local-lab'),
      HERMES_WEBUI_PYTHON: pythonBin,
      TAIJI_AGENT_PYTHON: pythonBin,
      TAIJI_WEBUI_PYTHON: pythonBin,
      TAIJI_AGENT_SYNC_PACKAGED_CONFIG: '0',
      TAIJI_AGENT_USE_USER_DIRS: '1', TAIJI_LICENSE_REQUIRED: '0', TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: '0',
      TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT: 'pilot',
      TAIJI_WORKSPACE: workspace,
      TAIJI_DESKTOP_USER_DATA_DIR: path.join(runtime, 'electron-user-data'),
      XDG_CONFIG_HOME: path.join(runtime, 'config'), XDG_DATA_HOME: path.join(runtime, 'data'), XDG_STATE_HOME: path.join(runtime, 'state'),
      AGENT_API_PORT: '21942', API_SERVER_PORT: '21942', WEBUI_PORT: '21987', TAIJI_WEBUI_PORT: '21987',
    },
    timeout: 90000,
  });

  try {
    const page = await app.firstWindow({ timeout: 90000 });
    page.on('pageerror', error => process.stderr.write(`[pageerror] ${error.message}\n`));
    page.on('console', message => { if (message.type() === 'error') process.stderr.write(`[console] ${message.text()}\n`); });
    page.on('response', async response => {
      if (!response.ok() && response.url().includes('/api/expert-teams/')) {
        process.stderr.write(`[expert-api ${response.status()}] ${response.url()} ${await response.text().catch(() => '')}\n`);
      }
    });
    await page.waitForLoadState('domcontentloaded', { timeout: 90000 });
    try {
      await page.waitForFunction(() => window.ExpertTeamV3 && typeof S !== 'undefined' && S._bootReady && typeof switchPanel === 'function', null, { timeout: 90000 });
    } catch (error) {
      const bootEvidence = await page.evaluate(() => ({
        url: location.href,
        title: document.title,
        body: String(document.body?.innerText || '').slice(0, 2000),
        hasExpertTeamV3: Boolean(window.ExpertTeamV3),
        hasState: typeof S !== 'undefined',
        bootReady: typeof S !== 'undefined' ? Boolean(S._bootReady) : false,
        hasSwitchPanel: typeof switchPanel === 'function',
      })).catch(evaluateError => ({ evaluateError: evaluateError.message }));
      throw new Error(`Electron boot did not reach the WebUI contract: ${error.message}\n${JSON.stringify(bootEvidence, null, 2)}`);
    }
    const runtimeExpertScript = await page.evaluate(async () => {
      const source = document.querySelector('script[src*="expert-team-v3.js"]')?.src;
      return source ? fetch(source).then(response => response.text()) : '';
    });
    assert(runtimeExpertScript.includes('state.keyboardBound = true'), 'Electron did not load the current Expert Team V3 source');
    await app.evaluate(({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows()[0];
      window?.show();
      window?.focus();
    });
    await page.bringToFront();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(async ({ workspace }) => {
      document.getElementById('onboardingOverlay')?.remove();
      const response = await fetch('/api/session/new', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workspace }) });
      if (!response.ok) throw new Error(`session/new failed ${response.status}: ${await response.text()}`);
      const payload = await response.json();
      if (!payload.session?.session_id) throw new Error(`session/new returned no session: ${JSON.stringify(payload)}`);
      window.__et3TestSession = payload.session;
      S.session = payload.session; S.messages = [];
      window.__et3SessionId = payload.session.session_id;
      if (typeof renderMessages === 'function') renderMessages();
      await switchPanel('writing');
      await window.ExpertTeamV3.loadCatalog(true);
    }, { workspace });

    await page.waitForSelector('#expertTeamV3PortalRoot .et3-team-card', { timeout: 20000 });
    const portal = await page.locator('#expertTeamV3PortalRoot').evaluate(root => ({ text: root.innerText, cards: root.querySelectorAll('.et3-team-card').length }));
    assert(portal.cards === 2 && portal.text.includes('专家团中心'), 'Portal did not expose exactly two pilot teams', portal);
    const firstCard = page.locator('#expertTeamV3PortalRoot .et3-team-card').first();
    await firstCard.focus();
    await page.keyboard.press('Enter');
    await page.waitForSelector('[data-et3-dialog-backdrop]:not([hidden])');
    assert(await page.locator('[data-et3-dialog]').getAttribute('role') === 'dialog', 'Team detail is not an accessible dialog');
    const dialogLayout = await page.locator('[data-et3-dialog]').evaluate(dialog => {
      const templateList = dialog.querySelector('.et3-template-list');
      const body = dialog.querySelector('.et3-dialog-body');
      const actions = dialog.querySelector('.et3-dialog-actions');
      const actionsRect = actions?.getBoundingClientRect();
      return {
        clientHeight: dialog.clientHeight,
        scrollHeight: dialog.scrollHeight,
        overflowY: getComputedStyle(dialog).overflowY,
        bodyOverflowY: body ? getComputedStyle(body).overflowY : '',
        actionsTop: actionsRect?.top || 0,
        actionsBottom: actionsRect?.bottom || 0,
        viewportHeight: innerHeight,
        templateColumns: templateList ? getComputedStyle(templateList).gridTemplateColumns.split(' ').filter(Boolean).length : 0,
        enabledTasks: Array.from(dialog.querySelectorAll('[data-et3-action="select-template"]:not([disabled])')).map(node => node.textContent.trim()),
      };
    });
    assert(dialogLayout.scrollHeight <= dialogLayout.clientHeight + 1, '1440×900 team detail requires nested scrolling', dialogLayout);
    assert(dialogLayout.bodyOverflowY === 'auto', 'Team detail content is not isolated in the scrollable body', dialogLayout);
    assert(dialogLayout.actionsTop >= 0 && dialogLayout.actionsBottom <= dialogLayout.viewportHeight, 'Summon action is outside the viewport', dialogLayout);
    assert(dialogLayout.templateColumns >= 2, 'Document tasks are not arranged compactly', dialogLayout);
    assert(dialogLayout.enabledTasks.length === 6, 'Content team does not expose six enabled tasks', dialogLayout);
    await page.screenshot({ path: path.join(outDir, '02-team-detail.png'), fullPage: false });
    for (let index = 0; index < 12; index += 1) await page.keyboard.press('Tab');
    assert(await page.locator('[data-et3-dialog]').evaluate(dialog => dialog.contains(document.activeElement)), 'Dialog focus escaped into the page background');
    await page.evaluate(() => {
      window.__et3EscapeEvents = 0;
      document.addEventListener('keydown', event => { if (event.key === 'Escape') window.__et3EscapeEvents += 1; }, { once: true, capture: true });
    });
    await page.keyboard.press('Escape');
    const returnedFocus = await firstCard.evaluate(node => ({
      matched: document.activeElement === node,
      connected: node.isConnected,
      activeTag: document.activeElement?.tagName || '',
      activeId: document.activeElement?.id || '',
      activeClass: String(document.activeElement?.className || ''),
      dialogHidden: document.querySelector('[data-et3-dialog-backdrop]')?.hidden,
      escapeEvents: window.__et3EscapeEvents,
    }));
    assert(returnedFocus.matched, 'Dialog did not return focus to trigger', returnedFocus);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: path.join(outDir, '01-portal.png'), fullPage: false });

    await page.evaluate(() => { S.session = window.__et3TestSession; });
    await firstCard.click();
    await page.evaluate(() => { S.session = window.__et3TestSession; });
    await page.getByRole('button', { name: '发起专家团任务' }).click();
    await page.waitForSelector('#expertTeamV3Workbench [data-et3-brief-form]', { timeout: 20000 });
    await page.waitForFunction(() => document.querySelector('[data-et3-action="summon"]')?.getAttribute('aria-busy') === 'false');
    const sourceUiCount = await page.locator('#expertTeamV3Workbench').locator('text=资料与依据').count()
      + await page.locator('#expertTeamV3Workbench [data-et3-source-file], #expertTeamV3Workbench [data-et3-source-text], #expertTeamV3Workbench [data-et3-action="add-text-source"], #expertTeamV3Workbench [data-et3-action="choose-source-file"], #expertTeamV3Workbench [data-et3-action="remove-source"]').count();
    assert(sourceUiCount === 0, '真实工作汇报 Brief 仍暴露资料与依据入口', { sourceUiCount });
    const workRequiredSections = await page.locator('#expertTeamV3Workbench .et3-required-sections li').allTextContents();
    assert(JSON.stringify(workRequiredSections) === JSON.stringify(['工作开展情况', '存在问题', '下一步工作安排']), '真实工作汇报 Brief 未展示服务端必备章节', { workRequiredSections });
    assert(await page.locator('#expertTeamV3Workbench .et3-required-sections input, #expertTeamV3Workbench .et3-required-sections textarea, #expertTeamV3Workbench .et3-required-sections select').count() === 0, '必备章节被错误渲染为客户端可编辑字段');
    await page.screenshot({ path: path.join(outDir, '03-real-brief-intake.png'), fullPage: false });
    await page.setViewportSize({ width: 760, height: 800 });
    const narrowIntakeLayout = await page.locator('#expertTeamV3Workbench').evaluate(root => ({
      rootWidth: Math.round(root.getBoundingClientRect().width),
      scrollWidth: root.scrollWidth,
      clientWidth: root.clientWidth,
    }));
    assert(narrowIntakeLayout.rootWidth === 760 && narrowIntakeLayout.scrollWidth <= narrowIntakeLayout.clientWidth + 1, '760px 需求确认页没有保持单一工作区或发生横向溢出', narrowIntakeLayout);
    assert(await page.locator('#expertTeamV3Workbench').locator('text=资料与依据').count() === 0, '760px 需求确认页仍显示资料与依据面板');
    await page.screenshot({ path: path.join(outDir, '03-real-brief-intake-760.png'), fullPage: false });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.locator('[data-et3-brief-form] input[name="exact_title"]').fill('部门月度工作汇报（Electron 合同验证）');
    await page.locator('[data-et3-brief-form] label.et3-form-field textarea[name="purpose"]').fill('用于内部工作会议汇报');
    await page.locator('[data-et3-brief-form] input[name="audience"]').fill('公司分管领导');
    await page.locator('[data-et3-brief-form] input[name="usage_scenario"]').fill('月度工作例会');
    await page.locator('[data-et3-brief-form] input[name="details.reporting_period"]').fill('2026年7月');
    await page.locator('[data-et3-brief-form] input[name="details.reporting_unit"]').fill('生产运营部');
    await page.getByRole('button', { name: '保存规格' }).click();
    await page.waitForFunction(() => document.body.innerText.includes('规格已保存'));
    const intakeAnswers = await page.locator('[data-et3-brief-form] textarea[name^="question__"][required]').all();
    for (let index = 0; index < intakeAnswers.length; index += 1) {
      await intakeAnswers[index].fill(`Electron 验证答案 ${index + 1}`);
    }
    assert(await page.locator('[data-et3-brief-form] textarea[name^="question__"]:not([required])').count() === 0, '真实任务仍暴露已删除的可选补充资料问题');
    await page.getByRole('button', { name: '保存回答' }).click();
    await page.getByRole('button', { name: '确认规格并继续' }).waitFor({ state: 'visible' });
    await page.getByRole('button', { name: '确认规格并继续' }).click();
    await page.getByRole('heading', { name: '生成前确认' }).waitFor({ state: 'visible' });
    const readyRequiredSections = await page.locator('#expertTeamV3Workbench .et3-required-sections li').allTextContents();
    assert(JSON.stringify(readyRequiredSections) === JSON.stringify(workRequiredSections), '生成前确认丢失必备章节', { workRequiredSections, readyRequiredSections });
    const readyLayout = await page.locator('#expertTeamV3Workbench').evaluate(root => {
      const required = root.querySelector('.et3-required-sections');
      const rootRect = root.getBoundingClientRect();
      const requiredRect = required?.getBoundingClientRect();
      return {
        scrollWidth: root.scrollWidth, clientWidth: root.clientWidth,
        requiredWithinWidth: Boolean(requiredRect && requiredRect.left >= rootRect.left && requiredRect.right <= rootRect.right + 1),
      };
    });
    assert(readyLayout.scrollWidth <= readyLayout.clientWidth + 1 && readyLayout.requiredWithinWidth, '生成前必备章节区域发生横向裁切', readyLayout);
    const realBrief = await page.evaluate(async () => {
      const root = document.getElementById('expertTeamV3Workbench');
      const payload = await window.api(`/api/expert-teams/run?session_id=${encodeURIComponent(root.dataset.expertTeamSourceSessionId)}&run_id=${encodeURIComponent(root.dataset.expertTeamRunId)}`);
      const run = payload.run || payload;
      return { runId: run.run_id, state: run.workflow_state, title: run.document_brief?.exact_title, sourceCount: (run.document_brief?.source_policy?.source_refs || []).length, requiredSections: run.document_brief?.content_constraints?.required_sections || [], profileRequiredSections: run.launch_profile_snapshot?.content_constraints?.required_sections || [] };
    });
    assert(realBrief.title.includes('Electron') && realBrief.sourceCount === 0 && realBrief.state === 'ready_to_generate', '真实 Brief HTTP 保存或确认未生效，或仍产生用户资料绑定', realBrief);
    assert(JSON.stringify(realBrief.requiredSections) === JSON.stringify(workRequiredSections) && JSON.stringify(realBrief.profileRequiredSections) === JSON.stringify(workRequiredSections), 'Launch Profile 到 Brief 的必备章节链路不一致', realBrief);
    await page.screenshot({ path: path.join(outDir, '03-real-brief.png'), fullPage: false });
    await page.screenshot({ path: path.join(outDir, '04-real-ready-required-sections.png'), fullPage: false });
    await page.setViewportSize({ width: 1024, height: 768 });
    const compactRequiredLayout = await page.locator('#expertTeamV3Workbench').evaluate(root => ({ scrollWidth: root.scrollWidth, clientWidth: root.clientWidth, rootWidth: Math.round(root.getBoundingClientRect().width), parentWidth: Math.round(root.parentElement.getBoundingClientRect().width) }));
    assert(compactRequiredLayout.scrollWidth <= compactRequiredLayout.clientWidth + 1 && Math.abs(compactRequiredLayout.rootWidth - compactRequiredLayout.parentWidth) < 2, '1024px 必备章节确认页发生横向溢出或未进入完整工作区', compactRequiredLayout);
    await page.screenshot({ path: path.join(outDir, '05-real-ready-required-sections-1024.png'), fullPage: false });
    await page.setViewportSize({ width: 1440, height: 900 });

    const researchSession = await page.evaluate(async ({ workspace }) => {
      const response = await fetch('/api/session/new', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workspace }) });
      if (!response.ok) throw new Error(`session/new failed ${response.status}: ${await response.text()}`);
      const payload = await response.json();
      if (!payload.session?.session_id) throw new Error(`session/new returned no session: ${JSON.stringify(payload)}`);
      S.session = payload.session; S.messages = [];
      if (typeof renderMessages === 'function') renderMessages();
      await switchPanel('writing');
      await window.ExpertTeamV3.loadCatalog(true);
      return payload.session;
    }, { workspace });
    await page.locator('#expertTeamV3PortalRoot .et3-team-card').nth(1).click();
    await page.getByRole('button', { name: '发起专家团任务' }).click();
    await page.waitForSelector('#expertTeamV3Workbench [data-et3-brief-form]', { timeout: 20000 });
    const researchRequiredSections = await page.locator('#expertTeamV3Workbench .et3-required-sections li').allTextContents();
    assert(JSON.stringify(researchRequiredSections) === JSON.stringify(['研究问题', '证据', '分析', '结论边界', '引用']), '真实研究报告 Brief 未展示完整必备章节', { researchRequiredSections });
    assert(await page.locator('#expertTeamV3Workbench').locator('text=资料与依据').count() === 0, '真实研究报告 Brief 仍显示资料与依据面板');
    assert(await page.locator('#expertTeamV3Workbench [data-et3-source-file], #expertTeamV3Workbench [data-et3-source-text], #expertTeamV3Workbench [data-et3-action="add-text-source"], #expertTeamV3Workbench [data-et3-action="choose-source-file"], #expertTeamV3Workbench [data-et3-action="remove-source"]').count() === 0, '真实研究报告 Brief 仍暴露资料控件');
    await page.screenshot({ path: path.join(outDir, '06-real-research-required-sections.png'), fullPage: false });

    await page.evaluate(({ source }) => {
      S.session = window.__et3TestSession;
      const makeRun = eval(`(${source})`);
      window.__et3Captured = [];
      window.__et3Forbidden = [];
      window.__et3ConflictOnce = false;
      window.__et3ConflictCaptured = [];
      window.__et3StageInputCaptured = [];
      window.__et3DeliveryCaptured = [];
      window.__et3DeliveryConflictCaptured = [];
      window.__et3DeliveryConflictOnce = false;
      window.__et3RecoveryCaptured = [];
      window.__et3RecoveryConflictCaptured = [];
      window.__et3RecoveryConflictOnce = false;
      const originalApi = window.api;
      window.__et3OriginalApi = originalApi;
      window.api = async (url, options) => {
        const target = String(url);
        const lowerTarget = target.toLowerCase();
        if (target.includes('/identity/') || target.endsWith('/stage/approve') || lowerTarget.includes('office') || lowerTarget.includes('wps')) {
          window.__et3Forbidden.push(target);
          throw new Error(`standalone forbidden request: ${target}`);
        }
        if (target === '/api/expert-teams/stage/revise' || target === '/api/expert-teams/stage/confirm') {
          const body = JSON.parse(options.body);
          if (target.endsWith('/revise') && window.__et3ConflictOnce) {
            window.__et3ConflictOnce = false;
            window.__et3ConflictCaptured.push({ url, body });
            const error = new Error('stage action binding is stale');
            error.status = 409;
            error.payload = { ok: false, code: 'stage_action_conflict', error: error.message, run: makeRun(window.__et3SessionId, 'awaiting_stage_confirmation', Number(body.expected_version || 7) + 1) };
            throw error;
          }
          window.__et3Captured.push({ url, body });
          const current = makeRun(window.__et3SessionId, target.endsWith('/revise') ? 'revising' : 'executing', Number(body.expected_version || 7) + 1);
          return { ok: true, run: current };
        }
        if (target === '/api/expert-teams/stage/input') {
          const body = JSON.parse(options.body);
          window.__et3StageInputCaptured.push({ url, body });
          return { ok: true, run: makeRun(window.__et3SessionId, 'executing', Number(body.expected_version || 10) + 1) };
        }
        if (target === '/api/expert-teams/delivery/recover') {
          const body = JSON.parse(options.body);
          if (Object.keys(body).some(key => key.includes('path')) || Object.prototype.hasOwnProperty.call(body, 'delivery_dir') || Object.prototype.hasOwnProperty.call(body, 'out_dir')) {
            throw new Error('standalone recovery request must contain only the server-bound identity');
          }
          if (window.__et3RecoveryConflictOnce) {
            window.__et3RecoveryConflictOnce = false;
            window.__et3RecoveryConflictCaptured.push({ url, body });
            const error = new Error('delivery recovery binding is stale');
            error.status = 409;
            const latest = makeRun(window.__et3SessionId, 'completed', Number(body.expected_version || 17) + 1);
            latest.view.public_state = 'awaiting_delivery_confirmation';
            latest.view.allowed_actions = ['delivery_recover'];
            latest.view.delivery_action_binding = null;
            latest.view.standalone_delivery = null;
            latest.view.delivery_recovery_binding = { ...body, expected_version: Number(body.expected_version || 17) + 1 };
            delete latest.view.delivery_recovery_binding.idempotency_key;
            latest.view.delivery_status = 'delivery_drifted';
            latest.view.presentation = { ...latest.view.presentation, state: 'completed_invalid', title: '交付文档已变化', primary_action: { id: 'delivery_recover', label: '重新生成 DOCX', kind: 'primary' } };
            error.payload = { ok: false, code: 'stale_delivery_recovery_binding', error: error.message, run: latest };
            throw error;
          }
          window.__et3RecoveryCaptured.push({ url, body });
          const regenerating = makeRun(window.__et3SessionId, 'generating_document', Number(body.expected_version || 17) + 1);
          regenerating.phase = '正式文档交付';
          regenerating.current_stage = regenerating.tasks[4];
          regenerating.view.workflow.current_stage = regenerating.view.workflow.stages[4];
          regenerating.view.workflow.progress = { done: 4, total: 5, current: '正式文档交付', current_index: 4 };
          regenerating.view.workspace.current_stage = regenerating.view.workflow.stages[4];
          regenerating.view.presentation = { ...regenerating.view.presentation, state: 'generating_document', title: '正在生成正式文档' };
          return { ok: true, run: regenerating };
        }
        if (target === '/api/expert-teams/delivery/open' || target === '/api/expert-teams/delivery/revise' || target === '/api/expert-teams/delivery/confirm') {
          const body = JSON.parse(options.body);
          if (Object.prototype.hasOwnProperty.call(body, 'path')) throw new Error('standalone delivery request must not contain path');
          if (target.endsWith('/revise') && window.__et3DeliveryConflictOnce) {
            window.__et3DeliveryConflictOnce = false;
            window.__et3DeliveryConflictCaptured.push({ url, body });
            const error = new Error('delivery action binding is stale');
            error.status = 409;
            error.payload = { ok: false, code: 'stale_delivery_binding', error: error.message, run: makeRun(window.__et3SessionId, 'awaiting_delivery_confirmation', Number(body.expected_version || 12) + 1) };
            throw error;
          }
          window.__et3DeliveryCaptured.push({ url, body });
          if (target.endsWith('/open')) return { ok: true, target: body.target };
          if (target.endsWith('/revise')) return { ok: true, run: makeRun(window.__et3SessionId, 'revising', Number(body.expected_version || 12) + 1) };
          return { ok: true, run: makeRun(window.__et3SessionId, 'completed', Number(body.expected_version || 12) + 1) };
        }
        return originalApi(url, options);
      };
      return switchPanel('chat').then(() => window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(makeRun(window.__et3SessionId))));
    }, { source: fixture.toString() });
    await page.waitForSelector('#expertTeamV3Workbench');
    const reviewDraft = page.locator('#expertTeamV3Workbench [data-et3-revision]');
    await reviewDraft.fill('这是尚未提交的复核草稿');
    await page.evaluate(({ source }) => {
      const makeRun = eval(`(${source})`);
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(makeRun(window.__et3SessionId, 'awaiting_stage_confirmation', 8)));
    }, { source: fixture.toString() });
    assert((await reviewDraft.inputValue()) === '', 'A stale binding draft entered the current revision field');
    const staleDraft = page.locator('#expertTeamV3Workbench [data-et3-stale-revision]');
    assert((await staleDraft.inputValue()).includes('尚未提交'), 'Binding change did not preserve the old draft as read-only');
    await page.getByRole('button', { name: '收起专家团工作台' }).click();
    assert(await page.locator('#expertTeamV3Workbench').evaluate(root => root.classList.contains('is-collapsed')), 'Workbench did not enter a recoverable collapsed state');
    await page.getByRole('button', { name: '展开专家团工作台' }).click();
    assert((await reviewDraft.inputValue()) === '', 'Collapsed stale draft entered the current revision field');
    assert((await staleDraft.inputValue()).includes('尚未提交'), 'Read-only stale draft was lost after collapse and restore');
    await reviewDraft.fill('');
    await page.setViewportSize({ width: 1024, height: 768 });
    assert(await page.locator('#expertTeamV3Workbench').evaluate(root => Math.abs(root.getBoundingClientRect().width - root.parentElement.getBoundingClientRect().width) < 2), '1024px workbench did not switch to full workspace mode');
    await page.screenshot({ path: path.join(outDir, '07-review-1024.png'), fullPage: false });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: path.join(outDir, '07-workbench-before-review.png'), fullPage: false });
    const stacking = await page.getByRole('button', { name: '加入修改意见' }).evaluate(node => {
      const rect = node.getBoundingClientRect();
      const root = document.getElementById('expertTeamV3Workbench');
      return { button: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }, root: root && { rect: root.getBoundingClientRect().toJSON(), position: getComputedStyle(root).position, zIndex: getComputedStyle(root).zIndex }, top: document.elementsFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2).slice(0, 5).map(item => `${item.tagName}.${item.className}`) };
    });
    fs.writeFileSync(path.join(outDir, 'stacking.json'), JSON.stringify(stacking, null, 2));
    await page.getByRole('button', { name: '加入修改意见' }).click();
    const revision = page.locator('#expertTeamV3Workbench [data-et3-revision]');
    assert((await revision.inputValue()).includes('补充关键指标'), 'Review suggestion was not added to the revision field');
    await page.evaluate(() => { window.__et3ConflictOnce = true; });
    await page.getByRole('button', { name: '提交修改意见' }).click();
    await page.waitForFunction(() => document.body.innerText.includes('状态已更新，修改意见已保留'));
    assert((await revision.inputValue()) === '', '409 stale binding draft entered the authoritative revision field');
    assert((await page.locator('[data-et3-stale-revision]').inputValue()).includes('补充关键指标'), '409 did not retain the stale draft as read-only');
    assert((await page.evaluate(() => window.__et3ConflictCaptured)).length === 1, '409 adversarial branch did not execute');
    await revision.fill('重新核对：补充关键指标和责任部门');
    await page.getByRole('button', { name: '提交修改意见' }).click();
    await page.waitForFunction(() => window.__et3Captured.length === 1);

    await page.evaluate(({ source }) => {
      const makeRun = eval(`(${source})`);
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(makeRun(window.__et3SessionId, 'awaiting_stage_confirmation', 9)));
    }, { source: fixture.toString() });
    assert(await page.locator('[data-et3-stale-revision]').count() === 0, 'Successfully submitted stage feedback was mislabeled as an unsubmitted draft');
    const approve = page.getByRole('button', { name: '无修改，进入下一阶段' });
    await approve.waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.querySelector('[data-et3-action="approve-stage"]')?.disabled);
    await approve.click();
    await page.waitForFunction(() => window.__et3Captured.length === 2);
    const captured = await page.evaluate(() => window.__et3Captured);
    assert(captured[0].url.endsWith('/revise') && captured[0].body.feedback.includes('补充关键指标'), 'Revision request contract is wrong', captured[0]);
    assert(captured[1].url.endsWith('/confirm') && captured[1].body.expected_version === 9, 'Confirm request contract is wrong', captured[1]);
    for (const request of captured) {
      for (const field of ['session_id','run_id','expected_version','stage_id','stage_attempt','artifact_id','artifact_sha256','idempotency_key']) {
        assert(Object.prototype.hasOwnProperty.call(request.body, field), `Stage request is missing ${field}`, request);
      }
    }
    assert((await page.evaluate(() => window.__et3Forbidden)).length === 0, 'Standalone review emitted an enterprise-only request', await page.evaluate(() => window.__et3Forbidden));
    await page.screenshot({ path: path.join(outDir, '08-stage-review.png'), fullPage: false });

    await page.evaluate(({ source }) => {
      const makeRun = eval(`(${source})`);
      const inputRun = makeRun(window.__et3SessionId, 'ready', 10);
      inputRun.workflow_state = 'awaiting_stage_input';
      inputRun.pending_input = {
        id: 'stage-input-1', stage_id: 'draft', question: '请选择本阶段的统计口径',
        description: '选择后，专家团将按该口径继续撰写。', options: ['按自然月', '按结算月'], required: true,
      };
      inputRun.view.public_state = 'ready';
      inputRun.view.allowed_actions = ['submit_stage_input'];
      inputRun.view.pending_input = inputRun.pending_input;
      inputRun.view.workspace.pending_input = inputRun.pending_input;
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(inputRun));
    }, { source: fixture.toString() });
    assert(await page.getByRole('button', { name: '开始生成' }).count() === 0, 'Stage input was incorrectly rendered as start-generation');
    await page.getByRole('button', { name: '按自然月' }).click();
    assert(await page.locator('[data-et3-stage-input]').inputValue() === '按自然月', 'Stage input option did not populate the answer');
    await page.screenshot({ path: path.join(outDir, '10-stage-input.png'), fullPage: false });
    await page.getByRole('button', { name: '提交并继续' }).click();
    await page.waitForFunction(() => window.__et3StageInputCaptured.length === 1);
    const stageInputCaptured = await page.evaluate(() => window.__et3StageInputCaptured);
    assert(stageInputCaptured[0].body.input_id === 'stage-input-1' && stageInputCaptured[0].body.answer === '按自然月', 'Stage input request contract is wrong', stageInputCaptured[0]);
    await page.screenshot({ path: path.join(outDir, '11-stage-input-submitted.png'), fullPage: false });

    await page.evaluate(({ source }) => {
      const makeRun = eval(`(${source})`);
      const deliveryRun = makeRun(window.__et3SessionId, 'awaiting_delivery_confirmation', 12);
      deliveryRun.view.completion_gates = { content: { status: 'passed' }, document: { status: 'passed' }, local_confirmation: { status: 'pending' } };
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(deliveryRun));
    }, { source: fixture.toString() });
    assert((await page.locator('#expertTeamV3Workbench').innerText()).includes('第 5/5 步 · 正式文档交付'), 'Delivery progress does not match the five-stage content-team contract');
    assert(await page.locator('#expertTeamV3Workbench .et3-progress > span').count() === 5, 'Delivery progress has the wrong number of content-team stages');
    const progressTops = await page.locator('#expertTeamV3Workbench .et3-progress > span').evaluateAll(nodes => nodes.map(node => Math.round(node.getBoundingClientRect().top)));
    assert(new Set(progressTops).size === 1, 'Five-stage delivery progress wrapped onto multiple rows', { progressTops });
    await page.screenshot({ path: path.join(outDir, '13-local-delivery-awaiting.png'), fullPage: false });
    await page.getByRole('button', { name: '打开最终 DOCX' }).click();
    await page.getByRole('button', { name: '打开文件夹' }).click();
    const deliveryRevision = page.locator('[data-et3-delivery-revision]');
    const reviseRequestCountBeforeEmpty = await page.evaluate(() => window.__et3DeliveryCaptured.filter(item => item.url.endsWith('/revise')).length);
    await page.getByRole('button', { name: '退回修改并重新生成' }).click();
    await page.waitForFunction(() => document.querySelector('[data-et3-delivery-revision]')?.getAttribute('aria-invalid') === 'true');
    assert(await page.locator('[data-et3-live]').innerText() === '请先填写需要修改的内容；如果文档无需修改，请确认可交付。', 'Empty delivery feedback has no associated error');
    assert(await page.evaluate(() => window.__et3DeliveryCaptured.filter(item => item.url.endsWith('/revise')).length) === reviseRequestCountBeforeEmpty, 'Empty delivery feedback emitted a request');
    await deliveryRevision.fill('请补充第三部分负责人和时间节点');
    await page.evaluate(() => { window.__et3DeliveryConflictOnce = true; });
    await page.getByRole('button', { name: '退回修改并重新生成' }).click();
    await page.waitForFunction(() => document.body.innerText.includes('交付状态已更新，修改意见已保留'));
    assert((await deliveryRevision.inputValue()) === '', '409 stale delivery draft entered the authoritative feedback field');
    assert((await page.locator('[data-et3-stale-delivery-revision]').inputValue()).includes('负责人'), '409 did not retain delivery feedback as read-only');
    await deliveryRevision.fill('重新核对：补充第三部分负责人和时间节点');
    await page.getByRole('button', { name: '退回修改并重新生成' }).click();
    await page.waitForFunction(() => window.__et3DeliveryCaptured.filter(item => item.url.endsWith('/revise')).length === 1);
    await page.evaluate(({ source }) => {
      const makeRun = eval(`(${source})`);
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(makeRun(window.__et3SessionId, 'awaiting_delivery_confirmation', 15)));
    }, { source: fixture.toString() });
    assert(await page.locator('[data-et3-stale-delivery-revision]').count() === 0, 'Successfully submitted delivery feedback was mislabeled as an unsubmitted draft');
    await page.getByRole('button', { name: '确认文档可交付' }).click();
    await page.waitForFunction(() => document.body.innerText.includes('文档已交付'));
    const deliveryCaptured = await page.evaluate(() => window.__et3DeliveryCaptured);
    assert(deliveryCaptured[0].body.target === 'document' && deliveryCaptured[1].body.target === 'folder', 'Delivery open targets are wrong', deliveryCaptured);
    assert(deliveryCaptured.every(item => !Object.prototype.hasOwnProperty.call(item.body, 'path')), 'Delivery request leaked a client path', deliveryCaptured);
    for (const request of deliveryCaptured) {
      for (const field of ['session_id','run_id','expected_version','stage_id','stage_attempt','artifact_id','artifact_sha256','delivery_attempt','delivery_binding_sha256','document_sha256','idempotency_key']) {
        assert(Object.prototype.hasOwnProperty.call(request.body, field), `Delivery request is missing ${field}`, request);
      }
    }
    assert((await page.evaluate(() => window.__et3DeliveryConflictCaptured)).length === 1, 'Delivery 409 adversarial branch did not execute');
    assert((await page.evaluate(() => window.__et3Forbidden)).length === 0, 'Delivery confirmation emitted an enterprise-only request', await page.evaluate(() => window.__et3Forbidden));
    await page.screenshot({ path: path.join(outDir, '14-local-delivery.png'), fullPage: false });

    for (const viewport of [{ width: 1280, height: 800, file: '15-local-delivery-1280.png' }, { width: 760, height: 800, file: '16-local-delivery-narrow.png' }]) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const responsiveLayout = await page.locator('#expertTeamV3Workbench').evaluate(root => {
        const parentRect = root.parentElement.getBoundingClientRect();
        const rect = root.getBoundingClientRect();
        return {
          rootWidth: Math.round(rect.width), parentWidth: Math.round(parentRect.width),
          scrollWidth: root.scrollWidth, clientWidth: root.clientWidth,
        };
      });
      const expectedWidth = viewport.width <= 920 ? viewport.width : responsiveLayout.parentWidth;
      assert(Math.abs(responsiveLayout.rootWidth - expectedWidth) < 2, `${viewport.width}px workbench is not a single full workspace`, { ...responsiveLayout, expectedWidth });
      assert(responsiveLayout.scrollWidth <= responsiveLayout.clientWidth + 1, `${viewport.width}px workbench has horizontal overflow`, responsiveLayout);
      await page.screenshot({ path: path.join(outDir, viewport.file), fullPage: false });
    }

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(({ source }) => {
      const makeRun = eval(`(${source})`);
      const drifted = makeRun(window.__et3SessionId, 'completed', 17);
      const recovery = { ...drifted.view.delivery_action_binding };
      drifted.view.public_state = 'awaiting_delivery_confirmation';
      drifted.view.allowed_actions = ['delivery_recover'];
      drifted.view.delivery_action_binding = null;
      drifted.view.delivery_recovery_binding = recovery;
      drifted.view.standalone_delivery = null;
      drifted.view.delivery_status = 'delivery_drifted';
      drifted.view.presentation = { ...drifted.view.presentation, state: 'completed_invalid', title: '交付文档已变化', primary_action: { id: 'delivery_recover', label: '重新生成 DOCX', kind: 'primary' } };
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(drifted));
    }, { source: fixture.toString() });
    const driftedText = await page.locator('#expertTeamV3Workbench').innerText();
    assert(driftedText.includes('交付文档已变化') && driftedText.includes('原本机确认已失效'), 'Drifted delivery did not expose the recovery explanation', { driftedText });
    assert(await page.getByRole('button', { name: '打开最终 DOCX' }).count() === 0, 'Drifted delivery still exposed the stale document');
    assert(await page.getByRole('button', { name: '确认文档可交付' }).count() === 0, 'Drifted delivery still exposed stale confirmation');
    await page.screenshot({ path: path.join(outDir, '17-delivery-drifted.png'), fullPage: false });
    await page.evaluate(() => { window.__et3RecoveryConflictOnce = true; });
    await page.getByRole('button', { name: '重新生成 DOCX' }).click();
    await page.waitForFunction(() => document.body.innerText.includes('交付状态已更新，请核对最新状态后重试'));
    assert((await page.evaluate(() => window.__et3RecoveryConflictCaptured)).length === 1, 'Recovery 409 adversarial branch did not execute');
    await page.getByRole('button', { name: '重新生成 DOCX' }).click();
    await page.waitForFunction(() => window.__et3RecoveryCaptured.length === 1);
    await page.waitForFunction(() => document.body.innerText.includes('正在生成正式文档'));
    assert((await page.locator('#expertTeamV3Workbench').innerText()).includes('第 5/5 步 · 正式文档交付'), 'Recovery regeneration lost the delivery-stage progress identity');
    const recoveryRequestSnapshot = await page.evaluate(() => window.__et3RecoveryCaptured);
    const recoveryKeys = ['session_id','run_id','expected_version','stage_id','stage_attempt','artifact_id','artifact_sha256','delivery_attempt','delivery_binding_sha256','document_sha256','idempotency_key'];
    assert(Object.keys(recoveryRequestSnapshot[0].body).sort().join(',') === recoveryKeys.sort().join(','), 'Recovery request did not use the exact server-bound whitelist', recoveryRequestSnapshot[0]);
    assert(Object.keys(recoveryRequestSnapshot[0].body).every(key => !key.includes('path')), 'Recovery request leaked a client path', recoveryRequestSnapshot[0]);
    assert((await page.evaluate(() => window.__et3Forbidden)).length === 0, 'Recovery emitted an enterprise-only request', await page.evaluate(() => window.__et3Forbidden));
    await page.screenshot({ path: path.join(outDir, '18-delivery-regenerating.png'), fullPage: false });

    await page.evaluate(async () => { await switchPanel("tasks"); });
    const isolated = await page.evaluate(() => ({ active: document.body.classList.contains('expert-team-v3-active'), workbench: Boolean(document.querySelector('#expertTeamV3Workbench')), tasksVisible: document.querySelector('main.main')?.classList.contains('showing-tasks') }));
    assert(!isolated.active && !isolated.workbench && isolated.tasksVisible, 'Expert Team layout leaked into non-expert page', isolated);
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.screenshot({ path: path.join(outDir, 'non-expert-tasks-1024.png'), fullPage: false });

    const sourceFiles = ['static/expert-team-v3.js', 'static/expert-team-presenter.js', 'static/expert-team-v3.css', 'api/expert_teams/contracts.py', 'api/expert_teams/document_capabilities.py', 'api/expert_teams/documents.py', 'api/expert_teams/launch_profiles.py', 'api/expert_teams/prompts.py', 'api/expert_teams/runtime.py', 'api/expert_teams/stage_artifacts.py', 'api/expert_teams/view.py', 'api/routes.py'];
    const gitStatus = command(repoRoot, 'git', ['status', '--porcelain']).split('\n').filter(Boolean);
    const ephemeralStatus = gitStatus.filter(line => line.endsWith(' hermes-local-lab/sources/hermes-agent/venv'));
    const relevantGitStatus = gitStatus.filter(line => !ephemeralStatus.includes(line));
    const evidence = {
      sourceRoot: fs.realpathSync(repoRoot),
      gitHead: command(repoRoot, 'git', ['rev-parse', 'HEAD']),
      gitDirty: relevantGitStatus.length > 0,
      gitStatus: relevantGitStatus,
      ignoredEphemeralStatus: ephemeralStatus,
      electronBin: fs.realpathSync(electronBin),
      pythonRequestedPath: path.resolve(pythonBin),
      pythonBinRealpath: fs.realpathSync(pythonBin),
      pythonBin: fs.realpathSync(pythonBin),
      runtimeRoot: runtime,
      sourceSha256: Object.fromEntries(sourceFiles.map(file => [file, sha256(path.join(webuiDir, file))])),
      realHttp: ['/api/session/new (fixture setup only)', '/api/expert-teams/catalog', '/api/expert-teams/launch', '/api/expert-teams/brief/update', '/api/expert-teams/answer', '/api/expert-teams/brief/confirm', '/api/expert-teams/run'],
      mocked: ['/api/expert-teams/stage/revise', '/api/expert-teams/stage/confirm', '/api/expert-teams/stage/input', '/api/expert-teams/delivery/open', '/api/expert-teams/delivery/revise', '/api/expert-teams/delivery/confirm', '/api/expert-teams/delivery/recover'],
    };
    const forbiddenRequests = await page.evaluate(() => window.__et3Forbidden || []);
    const recoveryCaptured = await page.evaluate(() => window.__et3RecoveryCaptured || []);
    const recoveryConflictCaptured = await page.evaluate(() => window.__et3RecoveryConflictCaptured || []);
    fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify({ evidence, portal, realBrief, workRequiredSections, narrowIntakeLayout, readyRequiredSections, readyLayout, compactRequiredLayout, researchSessionId: researchSession.session_id, researchRequiredSections, captured, stageInputCaptured, deliveryCaptured, recoveryCaptured, recoveryConflictCaptured, forbiddenRequests, isolated }, null, 2));
  } finally {
    await app.close();
  }
}

main().catch(error => { console.error(error && error.stack || error); process.exitCode = 1; });
