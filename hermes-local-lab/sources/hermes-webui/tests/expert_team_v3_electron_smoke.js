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

function fixture(sessionId, workflowState = 'awaiting_review', version = 7) {
  const output = {
    id: 'draft-1', kind: 'chat', title: '工作汇报阶段稿',
    content: '# 工作汇报\n\n## 一、工作开展情况\n已完成重点任务。\n\n## 二、存在问题\n部分数据待核实。\n\n## 三、下一步安排\n继续推进闭环。',
  };
  const stages = [
    { id: 'plan', task_id: 'plan', title: '任务规划', phase: '任务规划', status: 'done', worker_name: '写作总导演' },
    { id: 'draft', task_id: 'draft', title: '初稿撰写', phase: '初稿撰写', status: workflowState === 'generating' ? 'running' : 'awaiting_review', worker_name: '文案创作专家' },
    { id: 'delivery', task_id: 'delivery', title: '交付确认', phase: '交付确认', status: 'pending', worker_name: '交付复核专家' },
  ];
  const presentation = {
    state: workflowState, visible_title: '起草部门月度工作汇报',
    title: workflowState === 'awaiting_review' ? '阶段成果待复核' : '专家团正在生成',
    detail: '请阅读阶段成果后决定是否修改。', result: output,
    primary_action: { id: 'review_stage', label: '去复核', kind: 'primary' },
    secondary_actions: [
      { id: 'approve_stage', label: '无修改，进入下一阶段', kind: 'primary' },
      { id: 'revise_stage', label: '需要修改', kind: 'secondary' },
    ],
  };
  return {
    run_id: `et3-electron-${workflowState}`, session_id: sessionId, schema_version: 3,
    contract_version: 'expert-team-contract/v1', version, workflow_state: workflowState,
    team_id: 'content-creator-team', team_title: '内容创作专家团', current_stage: stages[1],
    document_brief: {
      status: 'confirmed', revision: 3, original_request: '起草部门月度工作汇报', exact_title: '部门月度工作汇报',
      document_type: 'work_report', purpose: '内部汇报', audience: '公司分管领导', source_refs: [],
    },
    questions: [], members: [], tasks: stages, artifacts: [], stage_outputs: [output],
    view: {
      presentation,
      business_context: { visible_title: '起草部门月度工作汇报', material_type: 'work_report' },
      team: { id: 'content-creator-team', title: '内容创作专家团', members: [] },
      workflow: { stages, current_stage: stages[1], progress: { done: 1, total: 3, current: '初稿撰写' } },
      workspace: { visible: true, title: '专家团工作台', state: workflowState, current_stage: stages[1], stages },
      brief: {
        status: 'confirmed', revision: 3, original_request: '起草部门月度工作汇报', exact_title: '部门月度工作汇报',
        document_type: 'work_report', document_type_label: '工作汇报', purpose: '内部汇报', audience: '公司分管领导',
        editable: false, sources: [],
      },
      stage_result: { output, review_items: [{ id: 'r1', title: '补充关键指标和责任部门', phase: '待人工补充' }] },
      stage_review: { review_id: 'review-1', attempt: 1, actionable: true, output },
      review_items: [{ id: 'r1', title: '补充关键指标和责任部门', phase: '待人工补充' }],
      completion_gates: { content: { status: 'pending' }, document: { status: 'pending' }, office: { status: 'pending' } },
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
    await page.waitForFunction(() => window.ExpertTeamV3 && typeof S !== 'undefined' && typeof switchPanel === 'function', null, { timeout: 90000 });
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
    await page.keyboard.press('Escape');
    assert(await firstCard.evaluate(node => document.activeElement === node), 'Dialog did not return focus to trigger');
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: path.join(outDir, '01-portal.png'), fullPage: false });

    await page.evaluate(() => { S.session = window.__et3TestSession; });
    await firstCard.click();
    await page.evaluate(() => { S.session = window.__et3TestSession; });
    await page.getByRole('button', { name: '发起专家团任务' }).click();
    await page.waitForSelector('#expertTeamV3Workbench [data-et3-brief-form]', { timeout: 20000 });
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
      const originalApi = window.api;
      window.__et3OriginalApi = originalApi;
      window.api = async (url, options) => {
        if (String(url).startsWith('/api/expert-teams/identity/status')) {
          return { enabled: true, authenticated: true, principal: { display_name: '测试审批人', roles: ['document-approver', 'document-reviewer'] } };
        }
        if (String(url).startsWith('/api/expert-teams/stage/')) {
          window.__et3Captured.push({ url, body: JSON.parse(options.body) });
          const current = makeRun(window.__et3SessionId, url.endsWith('/revise') ? 'revising' : 'generating', Number(JSON.parse(options.body).expected_version || 7) + 1);
          return { ok: true, run: current };
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
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(makeRun(window.__et3SessionId, 'awaiting_review', 8)));
    }, { source: fixture.toString() });
    assert((await reviewDraft.inputValue()).includes('尚未提交'), 'Review draft was lost during authoritative re-render');
    await page.getByRole('button', { name: '收起专家团工作台' }).click();
    assert(await page.locator('#expertTeamV3Workbench').evaluate(root => root.classList.contains('is-collapsed')), 'Workbench did not enter a recoverable collapsed state');
    await page.getByRole('button', { name: '展开专家团工作台' }).click();
    assert((await reviewDraft.inputValue()).includes('尚未提交'), 'Review draft was lost after collapse and restore');
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
    await page.getByRole('button', { name: '提交修改意见' }).click();
    await page.waitForFunction(() => window.__et3Captured.length === 1);

    await page.evaluate(({ source }) => {
      const makeRun = eval(`(${source})`);
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(makeRun(window.__et3SessionId, 'awaiting_review', 9)));
    }, { source: fixture.toString() });
    const approve = page.getByRole('button', { name: '无修改，进入下一阶段' });
    await approve.waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.querySelector('[data-et3-action="approve-stage"]')?.disabled);
    await approve.click();
    await page.waitForFunction(() => window.__et3Captured.length === 2);
    const captured = await page.evaluate(() => window.__et3Captured);
    assert(captured[0].url.endsWith('/revise') && captured[0].body.feedback.includes('补充关键指标'), 'Revision request contract is wrong', captured[0]);
    assert(captured[1].url.endsWith('/approve') && captured[1].body.expected_version === 9, 'Approve request contract is wrong', captured[1]);
    await page.screenshot({ path: path.join(outDir, '08-stage-review.png'), fullPage: false });

    const evidencePath = path.join(outDir, 'office-evidence.png');
    fs.writeFileSync(evidencePath, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64'));
    await page.evaluate(({ source }) => {
      const makeRun = eval(`(${source})`);
      const officeRun = makeRun(window.__et3SessionId, 'awaiting_review', 12);
      officeRun.artifacts = [{ kind: 'docx', title: '最终 DOCX', path: '.taiji/expert-teams/run/delivery/1/document.docx', exists: true }];
      officeRun.view.completion_gates = { content: { status: 'passed' }, document: { status: 'passed' }, office: { status: 'pending' } };
      officeRun.view.office_review = {
        review_id: 'office-review-1', status: 'pending', decision: 'pending', validity: 'active', review_session_status: 'begin_required', issues: [],
        checklist: Object.fromEntries(['document_opened','title_and_cover_match','genre_and_structure_match','content_order_correct','figures_unique_and_readable','tables_readable','headers_footers_pagination','no_placeholders_or_workflow_text','citations_readable'].map(key => [key, 'not_checked'])),
      };
      window.__et3OfficeRun = officeRun;
      window.__et3OfficeCaptured = [];
      window.openExpertTeamFileArtifact = async () => true;
      window.api = async (url, options = {}) => {
        const target = String(url);
        if (target.startsWith('/api/expert-teams/identity/status')) return { enabled: true, authenticated: true, principal: { display_name: '测试验收人', roles: ['document-reviewer'] } };
        if (target === '/api/docx-engine-v2/quality/wps-visual/begin') { window.__et3OfficeCaptured.push({ url: target, kind: 'begin' }); return { ok: true, review_session_status: 'ready' }; }
        if (target === '/api/docx-engine-v2/quality/wps-visual/evidence') { window.__et3OfficeCaptured.push({ url: target, kind: 'evidence' }); return { ok: true, count: 1, uploaded_count: 1 }; }
        if (target === '/api/docx-engine-v2/quality/wps-visual') { window.__et3OfficeCaptured.push({ url: target, kind: 'acceptance', body: JSON.parse(options.body) }); return { ok: true }; }
        if (target.startsWith('/api/expert-teams/run?')) {
          const completed = structuredClone(window.__et3OfficeRun);
          completed.workflow_state = 'completed'; completed.view.presentation.state = 'completed';
          completed.view.completion_gates.office = { status: 'passed' };
          return { ok: true, run: completed };
        }
        return window.__et3OriginalApi(url, options);
      };
      window.ExpertTeamV3.renderStatusSurface(buildExpertTeamCardFromRun(officeRun));
    }, { source: fixture.toString() });
    await page.waitForFunction(() => document.querySelectorAll('[data-et3-office-check]').length === 9 && !document.querySelector('[data-et3-action="office-begin"]')?.disabled);
    await page.getByRole('button', { name: '打开 DOCX 并开始复核' }).click();
    await page.setInputFiles('[data-et3-office-evidence]', evidencePath);
    await page.waitForFunction(() => document.querySelector('[data-et3-office-evidence]') && document.querySelector('[data-et3-office-evidence]').disabled === false && document.body.innerText.includes('已上传 1 份'));
    assert(await page.locator('[data-et3-office-issue]').evaluate(node => getComputedStyle(node).display === 'none'), '通过验收时仍展示不通过问题表单');
    await page.locator('input[name="et3-office-decision"][value="failed"]').check();
    assert(await page.locator('[data-et3-office-issue]').evaluate(node => getComputedStyle(node).display !== 'none'), '不通过验收时未展示结构化问题表单');
    await page.locator('input[name="et3-office-decision"][value="passed"]').check();
    await page.locator('[data-et3-office-check]').evaluateAll(items => items.forEach(item => { item.checked = true; item.dispatchEvent(new Event('change', { bubbles: true })); }));
    await page.locator('[data-et3-office-note]').fill('已使用 WPS 打开并逐页检查目录、版式、表格和分页，未发现异常。');
    await page.screenshot({ path: path.join(outDir, '12-office-form.png'), fullPage: false });
    await page.getByRole('button', { name: '提交验收结论' }).click();
    await page.waitForFunction(() => window.__et3OfficeCaptured.some(item => item.kind === 'acceptance'));
    const officeCaptured = await page.evaluate(() => window.__et3OfficeCaptured);
    const acceptance = officeCaptured.find(item => item.kind === 'acceptance');
    assert(officeCaptured.some(item => item.kind === 'begin') && officeCaptured.some(item => item.kind === 'evidence'), 'Office review did not begin and bind evidence', officeCaptured);
    assert(acceptance.body.status === 'passed' && Object.keys(acceptance.body.checklist).length === 9 && acceptance.body.note.includes('WPS'), 'Office acceptance payload is incomplete', acceptance);
    await page.screenshot({ path: path.join(outDir, '14-final-delivery.png'), fullPage: false });

    await page.evaluate(async () => { await switchPanel("tasks"); });
    const isolated = await page.evaluate(() => ({ active: document.body.classList.contains('expert-team-v3-active'), workbench: Boolean(document.querySelector('#expertTeamV3Workbench')), tasksVisible: document.querySelector('main.main')?.classList.contains('showing-tasks') }));
    assert(!isolated.active && !isolated.workbench && isolated.tasksVisible, 'Expert Team layout leaked into non-expert page', isolated);
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.screenshot({ path: path.join(outDir, 'non-expert-tasks-1024.png'), fullPage: false });

    const sourceFiles = ['static/expert-team-v3.js', 'static/expert-team-v3.css', 'api/routes.py'];
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
      realHttp: ['/api/session/new', '/api/expert-teams/catalog', '/api/expert-teams/start', '/api/expert-teams/brief/sources/add', '/api/expert-teams/brief/update', '/api/expert-teams/run'],
      mocked: ['/api/expert-teams/identity/status', '/api/expert-teams/stage/revise', '/api/expert-teams/stage/approve', '/api/docx-engine-v2/quality/wps-visual/*'],
    };
    fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify({ evidence, portal, realBrief, captured, officeCaptured, isolated }, null, 2));
  } finally {
    await app.close();
  }
}

main().catch(error => { console.error(error && error.stack || error); process.exitCode = 1; });
