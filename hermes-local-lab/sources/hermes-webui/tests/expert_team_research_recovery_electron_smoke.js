#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

function loadPlaywright() {
  const moduleId = process.env.PLAYWRIGHT_NODE_PATH || 'playwright';
  return require(moduleId);
}

function assert(condition, message, evidence) {
  if (!condition) throw new Error(`${message}\n${JSON.stringify(evidence || {}, null, 2)}`);
}

function researchCard(overrides = {}) {
  return {
    kind: 'expert_team', researchV2: true, productMode: 'standalone', readOnly: false,
    runId: 'research-recovery-run', sourceSessionId: '', version: 7,
    publicState: 'ready', workflowState: 'ready_to_generate', currentStageId: 'research',
    pendingInputId: '', allowedActions: ['resume'],
    team: { id: 'deep-research-team', title: '深度材料研究团' },
    brief: { originalRequest: '研究本地优先 AI 助理在企业办公中的落地趋势' },
    researchProgress: { currentStep: 'model_knowledge', statusText: '正在形成研究报告' },
    evidenceSummary: {
      publicSourceCount: 0, localSourceCount: 0, unverifiedModelClaimCount: 0,
      coverageLevel: 'none', sourceBasis: { id: 'none', text: '尚无可用证据' },
    },
    presentation: {}, progress: {}, pendingInput: null,
    ...overrides,
  };
}

async function main() {
  const { _electron } = loadPlaywright();
  const webuiDir = path.resolve(__dirname, '..');
  const repoRoot = path.resolve(webuiDir, '..', '..', '..');
  const hostRoot = process.env.TAIJI_ELECTRON_HOST_ROOT || repoRoot;
  const appDir = path.join(repoRoot, 'apps', 'taiji-desktop');
  const electronBin = path.join(hostRoot, 'apps', 'taiji-desktop', 'node_modules', 'electron', 'dist', 'Electron.app', 'Contents', 'MacOS', 'Electron');
  const pythonBin = process.env.HERMES_WEBUI_PYTHON || path.join(repoRoot, 'hermes-local-lab', 'sources', 'hermes-agent', 'venv', 'bin', 'python');
  const outDir = path.resolve(process.argv[process.argv.indexOf('--out-dir') + 1] || path.join(repoRoot, 'output', 'expert-team-research-recovery'));
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
      HERMES_WEBUI_PYTHON: pythonBin, TAIJI_AGENT_PYTHON: pythonBin, TAIJI_WEBUI_PYTHON: pythonBin,
      TAIJI_AGENT_SYNC_PACKAGED_CONFIG: '0', TAIJI_AGENT_USE_USER_DIRS: '1',
      TAIJI_LICENSE_REQUIRED: '0', TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: '0',
      TAIJI_WORKSPACE: workspace, TAIJI_DESKTOP_USER_DATA_DIR: path.join(runtime, 'electron-user-data'),
      XDG_CONFIG_HOME: path.join(runtime, 'config'), XDG_DATA_HOME: path.join(runtime, 'data'), XDG_STATE_HOME: path.join(runtime, 'state'),
      AGENT_API_PORT: '22942', API_SERVER_PORT: '22942', WEBUI_PORT: '22987', TAIJI_WEBUI_PORT: '22987',
    },
    timeout: 90000,
  });

  try {
    const page = await app.firstWindow({ timeout: 90000 });
    await page.waitForLoadState('domcontentloaded', { timeout: 90000 });
    await page.waitForFunction(() => window.ExpertTeamV3 && typeof switchPanel === 'function' && typeof S !== 'undefined' && S._bootReady, null, { timeout: 90000 });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(async ({ cardSource }) => {
      document.getElementById('onboardingOverlay')?.remove();
      await switchPanel('chat');
      const cardFactory = eval(`(${cardSource})`);
      window.__researchRecoveryCard = cardFactory();
      window.__researchResumeCalls = 0;
      let rejectResume;
      window.__rejectResearchResume = () => rejectResume?.(Object.assign(new Error('provider unavailable'), { status: 503 }));
      window.api = async url => {
        if (String(url) !== '/api/expert-teams/resume') throw new Error(`unexpected API ${url}`);
        window.__researchResumeCalls += 1;
        return new Promise((_resolve, reject) => { rejectResume = reject; });
      };
      window.ExpertTeamV3.renderStatusSurface(window.__researchRecoveryCard);
    }, { cardSource: researchCard.toString() });

    await page.waitForFunction(() => window.__researchResumeCalls === 1);
    const close = page.getByRole('button', { name: '收起专家团工作台' });
    assert(await close.count() === 1, '关闭按钮缺少可访问名称');
    await close.focus();
    await page.keyboard.press('Enter');
    const collapsedWhileBusy = await page.locator('#expertTeamV3Workbench').evaluate(root => root.classList.contains('is-collapsed'));
    assert(collapsedWhileBusy, '自动续跑中无法收起工作台');

    await page.evaluate(() => window.__rejectResearchResume());
    await page.getByRole('button', { name: '展开专家团工作台' }).click();
    await page.evaluate(() => window.ExpertTeamV3.renderStatusSurface(window.__researchRecoveryCard));
    await page.waitForFunction(() => document.querySelector('[data-et3-live]')?.getAttribute('aria-busy') !== 'true');
    assert(await page.evaluate(() => window.__researchResumeCalls) === 1, '同一快照在失败后被再次自动调度');

    await page.evaluate(({ cardSource }) => {
      const cardFactory = eval(`(${cardSource})`);
      window.ExpertTeamV3.renderStatusSurface(cardFactory({
        version: 9, publicState: 'failed', workflowState: 'start_failed',
        productError: {
          schema: 'taiji.product.error.v1', code: 'backend_unavailable', title: '模型服务暂不可用',
          message: '任务进度和资料已保留，请稍后重试。', recoveryActions: [{ id: 'retry', label: '重试' }],
        },
      }));
    }, { cardSource: researchCard.toString() });
    const failureText = await page.locator('#expertTeamV3Workbench').innerText();
    assert(failureText.includes('模型服务暂不可用') && failureText.includes('重试当前阶段'), '未显示可恢复的 Provider 错误态', { failureText });
    assert(!failureText.includes('正在形成研究报告') && !failureText.includes('正在自动继续研究'), '错误态仍被呈现为进行中', { failureText });
    await page.screenshot({ path: path.join(outDir, 'research-provider-recovery.png'), fullPage: false });
    const responsiveLayouts = [];
    for (const viewport of [{ width: 1024, height: 768 }, { width: 760, height: 800 }]) {
      await page.setViewportSize(viewport);
      const layout = await page.locator('#expertTeamV3Workbench').evaluate(root => ({
        viewportWidth: innerWidth,
        rootWidth: Math.round(root.getBoundingClientRect().width),
        scrollWidth: root.scrollWidth,
        clientWidth: root.clientWidth,
      }));
      assert(layout.scrollWidth <= layout.clientWidth + 1, '错误恢复页在窄屏出现横向溢出', layout);
      responsiveLayouts.push(layout);
      await page.screenshot({ path: path.join(outDir, `research-provider-recovery-${viewport.width}.png`), fullPage: false });
    }
    fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify({ collapsedWhileBusy, resumeCalls: await page.evaluate(() => window.__researchResumeCalls), failureText, responsiveLayouts }, null, 2));
  } finally {
    await app.close();
  }
}

main().catch(error => {
  console.error(error && error.stack || error);
  process.exitCode = 1;
});
