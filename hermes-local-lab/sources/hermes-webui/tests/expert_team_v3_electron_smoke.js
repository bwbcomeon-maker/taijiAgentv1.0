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
  const output = {
    id: 'draft-1', kind: 'chat', title: '工作汇报阶段稿',
    content: '# 工作汇报\n\n## 一、工作开展情况\n已完成重点任务。\n\n## 二、存在问题\n部分数据待核实。\n\n## 三、下一步安排\n继续推进闭环。',
  };
  const stages = [
    { id: 'plan', task_id: 'plan', title: '任务规划', phase: '任务规划', status: 'done', worker_name: '写作总导演' },
    { id: 'draft', task_id: 'draft', title: '初稿撰写', phase: '初稿撰写', status: publicState === 'executing' ? 'running' : 'awaiting_review', worker_name: '文案创作专家' },
    { id: 'delivery', task_id: 'delivery', title: '交付确认', phase: '交付确认', status: 'pending', worker_name: '交付复核专家' },
  ];
  const presentation = {
    state: publicState, visible_title: '起草部门月度工作汇报',
    title: publicState === 'awaiting_stage_confirmation' ? '阶段成果待确认' : '专家团正在执行',
    detail: '请阅读阶段成果后决定是否修改。', result: output,
    primary_action: { id: 'review_stage', label: '去复核', kind: 'primary' },
    secondary_actions: [
      { id: 'approve_stage', label: '无修改，进入下一阶段', kind: 'primary' },
      { id: 'revise_stage', label: '需要修改', kind: 'secondary' },
    ],
  };
  return {
    run_id: 'et3-electron-run', session_id: sessionId, schema_version: 3,
    contract_version: 'expert-team-contract/v1', product_mode: 'standalone', version, workflow_state: 'awaiting_review',
    team_id: 'content-creator-team', team_title: '内容创作专家团', current_stage: stages[1],
    document_brief: {
      status: 'confirmed', revision: 3, original_request: '起草部门月度工作汇报', exact_title: '部门月度工作汇报',
      document_type: 'work_report', purpose: '内部汇报', audience: '公司分管领导', source_refs: [],
    },
    questions: [], members: [], tasks: stages, artifacts: [], stage_outputs: [output],
    view: {
      product_mode: 'standalone', public_state: publicState,
      allowed_actions: publicState === 'awaiting_stage_confirmation' ? ['stage_confirm', 'stage_revise'] : [],
      stage_action_binding: publicState === 'awaiting_stage_confirmation' ? {
        session_id: sessionId, run_id: 'et3-electron-run', expected_version: version,
        stage_id: 'draft', stage_attempt: 1, artifact_id: 'draft:1', artifact_sha256: 'a'.repeat(64),
      } : null,
      presentation,
      business_context: { visible_title: '起草部门月度工作汇报', material_type: 'work_report' },
      team: { id: 'content-creator-team', title: '内容创作专家团', members: [] },
      workflow: { stages, current_stage: stages[1], progress: { done: 1, total: 3, current: '初稿撰写' } },
      workspace: { visible: true, title: '专家团工作台', state: publicState, current_stage: stages[1], stages },
      brief: {
        status: 'confirmed', revision: 3, original_request: '起草部门月度工作汇报', exact_title: '部门月度工作汇报',
        document_type: 'work_report', document_type_label: '工作汇报', purpose: '内部汇报', audience: '公司分管领导',
        editable: false, sources: [],
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
  const formalRoot = process.env.TAIJI_MAIN_REPO_ROOT || '/Users/bwb/Documents/工作/taiji-agentv1.0';
  const appDir = path.join(repoRoot, 'apps', 'taiji-desktop');
  const electronBin = path.join(formalRoot, 'apps', 'taiji-desktop', 'node_modules', 'electron', 'dist', 'Electron.app', 'Contents', 'MacOS', 'Electron');
  const outDir = path.resolve(process.argv[process.argv.indexOf('--out-dir') + 1] || path.join(repoRoot, 'output', 'expert-team-v3'));
  assert(fs.existsSync(electronBin), 'Electron binary missing', { electronBin });
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
      HERMES_WEBUI_PYTHON: path.join(formalRoot, 'hermes-local-lab', 'sources', 'hermes-agent', '.venv', 'bin', 'python'),
      TAIJI_AGENT_PYTHON: path.join(formalRoot, 'hermes-local-lab', 'sources', 'hermes-agent', '.venv', 'bin', 'python'),
      TAIJI_WEBUI_PYTHON: path.join(formalRoot, 'hermes-local-lab', 'sources', 'hermes-agent', '.venv', 'bin', 'python'),
      TAIJI_AGENT_USE_USER_DIRS: '1', TAIJI_LICENSE_REQUIRED: '0', TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: '0',
      TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT: 'pilot',
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
    await page.waitForFunction(() => window.ExpertTeamV3 && typeof S !== 'undefined' && S._bootReady && typeof switchPanel === 'function', null, { timeout: 90000 });
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
    await page.evaluate(async ({ workspace }) => {
      document.getElementById('onboardingOverlay')?.remove();
      const response = await fetch('/api/session/new', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workspace }) });
      const payload = await response.json();
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
    await page.locator('[data-et3-brief-form] input[name="exact_title"]').fill('部门月度工作汇报（Electron 合同验证）');
    await page.locator('[data-et3-brief-form] label.et3-form-field textarea[name="purpose"]').fill('用于内部工作会议汇报');
    await page.locator('[data-et3-brief-form] input[name="audience"]').fill('公司分管领导');
    await page.locator('[data-et3-brief-form] textarea').evaluateAll(items => items.forEach((item, index) => { if (!item.value.trim()) item.value = `Electron 验证答案 ${index + 1}`; }));
    await page.locator('[data-et3-source-label]').fill('Electron 验证资料');
    await page.locator('[data-et3-source-text]').fill('六月重点工作按计划推进，本行仅用于隔离测试。');
    await page.getByRole('button', { name: '添加文字资料' }).click();
    await page.waitForFunction(() => document.body.innerText.includes('Electron 验证资料'));
    await page.getByRole('button', { name: '保存规格' }).click();
    await page.waitForFunction(() => document.body.innerText.includes('操作已保存'));
    const realBrief = await page.evaluate(async () => {
      const root = document.getElementById('expertTeamV3Workbench');
      const payload = await window.api(`/api/expert-teams/run?session_id=${encodeURIComponent(root.dataset.expertTeamSourceSessionId)}&run_id=${encodeURIComponent(root.dataset.expertTeamRunId)}`);
      const run = payload.run || payload;
      return { runId: run.run_id, state: run.workflow_state, title: run.document_brief?.exact_title, sourceCount: (run.document_brief?.source_policy?.source_refs || []).length };
    });
    assert(realBrief.title.includes('Electron') && realBrief.sourceCount === 1, '真实 Brief HTTP 保存或资料绑定未生效', realBrief);
    await page.screenshot({ path: path.join(outDir, '03-real-brief.png'), fullPage: false });

    await page.evaluate(({ source }) => {
      const makeRun = eval(`(${source})`);
      window.__et3Captured = [];
      window.__et3Forbidden = [];
      window.__et3ConflictOnce = false;
      window.__et3ConflictCaptured = [];
      window.__et3StageInputCaptured = [];
      const originalApi = window.api;
      window.__et3OriginalApi = originalApi;
      window.api = async (url, options) => {
        const target = String(url);
        if (target.includes('/identity/') || target.endsWith('/stage/approve') || target.includes('/quality/wps-visual') || target.includes('/office-revisions/')) {
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
      deliveryRun.artifacts = [{ kind: 'docx', title: '最终 DOCX', path: '.taiji/expert-teams/run/delivery/1/document.docx', exists: true }];
      deliveryRun.view.completion_gates = { content: { status: 'passed' }, document: { status: 'passed' }, local_confirmation: { status: 'pending' } };
      window.__et3Opened = [];
      window.openExpertTeamFileArtifact = async button => { window.__et3Opened.push(button.dataset.expertTeamArtifactPath); return true; };
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(deliveryRun));
    }, { source: fixture.toString() });
    await page.getByRole('button', { name: '打开最终 DOCX' }).click();
    assert((await page.evaluate(() => window.__et3Opened)).length === 1, 'Local delivery document was not opened through the desktop bridge');
    assert((await page.evaluate(() => window.__et3Forbidden)).length === 0, 'Delivery confirmation emitted an enterprise-only request', await page.evaluate(() => window.__et3Forbidden));
    await page.screenshot({ path: path.join(outDir, '14-local-delivery.png'), fullPage: false });

    await page.evaluate(async () => { await switchPanel("tasks"); });
    const isolated = await page.evaluate(() => ({ active: document.body.classList.contains('expert-team-v3-active'), workbench: Boolean(document.querySelector('#expertTeamV3Workbench')), tasksVisible: document.querySelector('main.main')?.classList.contains('showing-tasks') }));
    assert(!isolated.active && !isolated.workbench && isolated.tasksVisible, 'Expert Team layout leaked into non-expert page', isolated);
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.screenshot({ path: path.join(outDir, 'non-expert-tasks-1024.png'), fullPage: false });

    const sourceFiles = ['static/expert-team-v3.js', 'static/expert-team-presenter.js', 'static/expert-team-v3.css', 'api/routes.py'];
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
      pythonBin: fs.realpathSync(path.join(formalRoot, 'hermes-local-lab', 'sources', 'hermes-agent', '.venv', 'bin', 'python')),
      runtimeRoot: runtime,
      sourceSha256: Object.fromEntries(sourceFiles.map(file => [file, sha256(path.join(webuiDir, file))])),
      realHttp: ['/api/session/new (fixture setup only)', '/api/expert-teams/catalog', '/api/expert-teams/launch', '/api/expert-teams/brief/sources/add', '/api/expert-teams/brief/update', '/api/expert-teams/run'],
      mocked: ['/api/expert-teams/stage/revise', '/api/expert-teams/stage/confirm', '/api/expert-teams/stage/input'],
    };
    const forbiddenRequests = await page.evaluate(() => window.__et3Forbidden || []);
    fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify({ evidence, portal, realBrief, captured, stageInputCaptured, forbiddenRequests, isolated }, null, 2));
  } finally {
    await app.close();
  }
}

main().catch(error => { console.error(error && error.stack || error); process.exitCode = 1; });
