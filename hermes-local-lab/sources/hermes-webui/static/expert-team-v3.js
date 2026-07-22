(function () {
  'use strict';

  const state = {
    catalog: [],
    selectedTeam: null,
    selectedExample: null,
    card: null,
    portalController: null,
    workbenchController: null,
    identityController: null,
    identityStatus: null,
    identityRole: '',
    officeEvidenceCount: 0,
    officeEvidenceKey: '',
    dialogReturnFocus: null,
    draft: null,
    collapsed: false,
    busy: false,
  };

  const officeChecks = [
    ['document_opened', '文档可在 WPS/Word 正常打开', true],
    ['title_and_cover_match', '标题与封面信息一致', true],
    ['genre_and_structure_match', '文种与章节结构正确', true],
    ['content_order_correct', '正文顺序与最终确认内容一致', true],
    ['figures_unique_and_readable', '图片清晰且无重复', false],
    ['tables_readable', '表格完整且可阅读', false],
    ['headers_footers_pagination', '页眉、页脚与分页正常', true],
    ['no_placeholders_or_workflow_text', '无占位符或流程话术残留', true],
    ['citations_readable', '引用与来源标注可阅读', false],
  ];

  const fallbackTeams = [
    {
      id: 'content-creator-team',
      title: '内容创作专家团',
      category: '办公材料',
      description: '把零散诉求和资料整理为可复核、可交付的工作汇报。',
      image: 'static/assets/writeflow/team-content-creator.png',
      tags: ['工作汇报', '规格确认', 'DOCX 交付'],
      members: [],
      examples: [{
        id: 'monthly-work-report',
        label: '工作汇报',
        document_type: 'work_report',
        prompt: '帮我起草一份部门月度工作汇报，主题是迎峰度夏保供电重点工作推进情况。',
      }],
    },
    {
      id: 'deep-research-team',
      title: '深度材料研究团',
      category: '材料研究',
      description: '围绕指定资料建立研究边界、证据链和结构化研究报告。',
      image: 'static/assets/writeflow/team-research.png',
      tags: ['研究报告', '证据梳理', '引用核验'],
      members: [],
      examples: [{
        id: 'research-report',
        label: '研究报告',
        document_type: 'research_report',
        prompt: '请根据我提供的资料形成一份专题研究报告，明确证据来源、判断边界和待核实事项。',
      }],
    },
  ];

  const stateCopy = {
    collecting_required: ['确认任务规格', '先把主题、对象、用途和边界确认清楚。'],
    collecting_optional: ['补充任务规格', '可补充资料，也可以在信息足够时确认规格。'],
    ready_to_generate: ['规格已确认', '开始后专家团将按阶段生成，每一阶段都可复核。'],
    starting: ['正在启动专家团', '正在建立本次任务的执行上下文。'],
    generating: ['专家协作中', '当前阶段正在生成，完成后会进入人工复核。'],
    awaiting_stage_input: ['需要你的补充', '专家团在继续前需要确认一项信息。'],
    awaiting_review: ['阶段成果待复核', '阅读成果后，可以直接进入下一阶段或提交修改意见。'],
    revising: ['正在按意见修改', '修改完成后会回到当前阶段复核。'],
    delivery_validation_required: ['正在生成正式文档', '内容已确认，正在完成 DOCX 自动检查。'],
    office_acceptance_required: ['等待 Office 验收', '请在 WPS/Word 中检查正式文档后提交验收。'],
    completed: ['文档已交付', '正式 DOCX 已生成，可打开或下载。'],
    failed: ['任务未完成', '查看原因后返回专家团门户重新发起。'],
    cancelled: ['任务已取消', '当前任务已停止，不会继续生成。'],
    legacy_read_only: ['历史任务（只读）', '该任务使用旧版数据结构，仅保留查看能力。'],
  };

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  function list(value) { return Array.isArray(value) ? value : []; }
  function uid(kind) {
    const id = globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function'
      ? globalThis.crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `expert-team-v3:${kind}:${id}`;
  }

  function normalizeTeam(team) {
    const fallback = fallbackTeams.find(item => item.id === team.id) || {};
    return {
      ...fallback,
      ...team,
      title: team.title || fallback.title || '专家团',
      description: team.description || fallback.description || '',
      image: team.image || fallback.image || '',
      tags: list(team.tags).length ? team.tags : list(fallback.tags),
      members: list(team.members),
      examples: list(team.examples).length ? team.examples : list(fallback.examples),
    };
  }

  function portalRoot() { return document.getElementById('expertTeamV3PortalRoot'); }
  function workbenchRoot() { return document.getElementById('expertTeamV3Workbench'); }

  function renderPortal(message) {
    const root = portalRoot();
    if (!root) return false;
    const teams = state.catalog.length ? state.catalog : fallbackTeams;
    root.innerHTML = `
      <main class="et3-portal" aria-labelledby="expertTeamV3PortalTitle">
        <div class="et3-portal-head">
          <div>
            <p class="et3-eyebrow">专家协作工作台</p>
            <h1 id="expertTeamV3PortalTitle">专家团中心</h1>
            <p class="et3-subtitle">选择团队，确认任务规格，分阶段复核，最后交付正式文档。</p>
          </div>
          <div class="et3-search">
            <label for="expertTeamV3Search">查找专家团</label>
            <input id="expertTeamV3Search" type="search" autocomplete="off" placeholder="搜索团队、能力或文档类型">
          </div>
        </div>
        <p class="et3-status" data-et3-portal-status aria-live="polite">${esc(message || '')}</p>
        <div class="et3-team-grid" data-et3-team-grid>
          ${teams.map(team => teamCard(team)).join('')}
        </div>
      </main>
      <div class="et3-dialog-backdrop" data-et3-dialog-backdrop hidden>
        <section class="et3-dialog" role="dialog" aria-modal="true" aria-labelledby="expertTeamV3DialogTitle" data-et3-dialog></section>
      </div>`;
    bindPortalEvents(root);
    return true;
  }

  function teamCard(team) {
    return `<button type="button" class="et3-team-card" data-et3-action="open-team" data-team-id="${esc(team.id)}" aria-label="查看并发起${esc(team.title)}">
      <img src="${esc(team.image)}" alt="" loading="lazy">
      <span>
        <small>${esc(team.category || '专业协作')}</small>
        <h2>${esc(team.title)}</h2>
        <p>${esc(team.description)}</p>
        <span class="et3-tags">${list(team.tags).slice(0, 4).map(tag => `<span class="et3-tag">${esc(tag)}</span>`).join('')}</span>
        <span class="et3-card-cta">查看并发起 <span aria-hidden="true">→</span></span>
      </span>
    </button>`;
  }

  function bindPortalEvents(root) {
    if (state.portalController) state.portalController.abort();
    state.portalController = new AbortController();
    const signal = state.portalController.signal;
    root.addEventListener('click', event => handlePortalClick(event), { signal });
    root.addEventListener('input', event => handlePortalInput(event), { signal });
    root.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeDialog();
      if (event.key === 'Tab') trapDialogFocus(event);
    }, { signal });
  }

  function handlePortalInput(event) {
    if (event.target.id !== 'expertTeamV3Search') return;
    const query = event.target.value.trim().toLowerCase();
    const teams = (state.catalog.length ? state.catalog : fallbackTeams).filter(team =>
      [team.title, team.description, team.category, ...list(team.tags)].join(' ').toLowerCase().includes(query));
    const grid = portalRoot().querySelector('[data-et3-team-grid]');
    const live = portalRoot().querySelector('[data-et3-portal-status]');
    if (grid) grid.innerHTML = teams.length ? teams.map(teamCard).join('') : '<p>没有匹配的专家团。</p>';
    if (live) live.textContent = query ? `找到 ${teams.length} 个专家团` : '';
  }

  function handlePortalClick(event) {
    const action = event.target.closest('[data-et3-action]');
    if (!action) return;
    const kind = action.dataset.et3Action;
    if (kind === 'open-team') openTeam(action.dataset.teamId, action);
    if (kind === 'close-dialog') closeDialog();
    if (kind === 'select-template') selectTemplate(action.dataset.exampleId);
    if (kind === 'summon') summon(action);
  }

  function openTeam(teamId, trigger) {
    const team = (state.catalog.length ? state.catalog : fallbackTeams).find(item => item.id === teamId);
    if (!team) return;
    state.selectedTeam = team;
    state.selectedExample = list(team.examples)[0] || null;
    state.dialogReturnFocus = trigger || null;
    renderTeamDialog();
  }

  function renderTeamDialog() {
    const root = portalRoot();
    const team = state.selectedTeam;
    const dialog = root && root.querySelector('[data-et3-dialog]');
    const backdrop = root && root.querySelector('[data-et3-dialog-backdrop]');
    if (!team || !dialog || !backdrop) return;
    const examples = list(team.examples);
    const prompt = (state.selectedExample && state.selectedExample.prompt) || '';
    dialog.innerHTML = `
      <header class="et3-dialog-head">
        <div><p class="et3-eyebrow">选择专家团</p><h2 id="expertTeamV3DialogTitle" tabindex="-1">${esc(team.title)}</h2><p class="et3-subtitle">${esc(team.category || '')}</p></div>
        <button type="button" class="et3-icon-button" data-et3-action="close-dialog" aria-label="关闭专家团详情">×</button>
      </header>
      <div class="et3-dialog-body">
        <div>
          <section class="et3-section"><h3>团队能力</h3><p>${esc(team.description)}</p></section>
          <section class="et3-section"><h3>团队成员</h3><div class="et3-member-list">${list(team.members).map(member => `<div class="et3-member"><strong>${esc(member.name || member.id)}</strong><span>${esc(member.role || '')}</span></div>`).join('') || '<p>专家角色会在任务启动后按阶段加入。</p>'}</div></section>
        </div>
        <div>
          <section class="et3-section">
            <h3>选择文档任务</h3>
            <div class="et3-template-list">${examples.map((example, index) => `<button type="button" class="et3-template" data-et3-action="select-template" data-example-id="${esc(example.id)}" aria-pressed="${state.selectedExample ? state.selectedExample.id === example.id : index === 0}"><strong>${esc(example.label || example.id)}</strong><span>${esc(example.document_type || '')}</span></button>`).join('')}</div>
            <label class="et3-form-field" for="expertTeamV3Prompt"><span>原始诉求</span><textarea id="expertTeamV3Prompt" rows="6" aria-describedby="expertTeamV3PromptHelp">${esc(prompt)}</textarea></label>
            <p id="expertTeamV3PromptHelp" class="et3-help">发起后先确认完整任务规格，不会直接生成文档。</p>
            <p class="et3-live" data-et3-dialog-live aria-live="polite"></p>
          </section>
        </div>
      </div>
      <footer class="et3-dialog-actions"><button type="button" class="et3-button" data-et3-action="close-dialog">取消</button><button type="button" class="et3-button et3-button--primary" data-et3-action="summon">发起专家团任务</button></footer>`;
    backdrop.hidden = false;
    const portal = root.querySelector('.et3-portal');
    if (portal) portal.inert = true;
    document.getElementById('expertTeamV3DialogTitle').focus();
  }

  function trapDialogFocus(event) {
    const backdrop = portalRoot() && portalRoot().querySelector('[data-et3-dialog-backdrop]');
    const dialog = backdrop && backdrop.querySelector('[data-et3-dialog]');
    if (!dialog || backdrop.hidden) return;
    const focusable = Array.from(dialog.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function selectTemplate(exampleId) {
    const example = list(state.selectedTeam && state.selectedTeam.examples).find(item => item.id === exampleId);
    if (!example) return;
    state.selectedExample = example;
    const prompt = document.getElementById('expertTeamV3Prompt')?.value;
    renderTeamDialog();
    const field = document.getElementById('expertTeamV3Prompt');
    if (field && typeof prompt === 'string') field.value = state.selectedExample?.prompt || prompt;
    portalRoot()?.querySelector(`[data-example-id="${CSS.escape(exampleId)}"]`)?.focus();
  }

  function closeDialog() {
    const backdrop = portalRoot() && portalRoot().querySelector('[data-et3-dialog-backdrop]');
    if (!backdrop || backdrop.hidden) return false;
    backdrop.hidden = true;
    const portal = portalRoot()?.querySelector('.et3-portal');
    if (portal) portal.inert = false;
    if (state.dialogReturnFocus && state.dialogReturnFocus.isConnected) state.dialogReturnFocus.focus();
    return true;
  }

  async function summon(button) {
    const prompt = String(document.getElementById('expertTeamV3Prompt')?.value || '').trim();
    const live = portalRoot().querySelector('[data-et3-dialog-live]');
    if (!prompt) { live.textContent = '请先填写本次任务诉求。'; return; }
    if (typeof window.sendExpertTeamAction !== 'function') { live.textContent = '专家团启动服务尚未就绪，请刷新后重试。'; return; }
    setBusy(button, true, '正在发起…');
    const example = state.selectedExample || {};
    const team = state.selectedTeam;
    const payload = {
      action: 'start', new_session: true, summon_only: false,
      team_id: team.id, prompt, project: prompt.slice(0, 28),
      contract_version: 'expert-team-contract/v1',
      intake_example_id: String(example.intake_example_id || example.id || ''),
      document_type: String(example.document_type || (team.id === 'deep-research-team' ? 'research_report' : 'work_report')),
      document_brief_seed: { ...(example.document_brief_seed || {}), task_mode: String(example.task_mode || 'create') },
    };
    try {
      const started = await window.sendExpertTeamAction(payload);
      if (started) closeDialog();
      else live.textContent = '未能发起任务，请检查页面提示后重试。';
    } catch (error) {
      live.textContent = error && error.message ? error.message : '发起失败，请重试。';
    } finally { setBusy(button, false); }
  }

  async function loadCatalog(force) {
    if (state.catalog.length && !force) return renderPortal();
    renderPortal('正在加载专家团…');
    try {
      const payload = await window.api('/api/expert-teams/catalog');
      const allowed = new Set(['content-creator-team', 'deep-research-team']);
      state.catalog = list(payload && payload.teams).filter(team => allowed.has(team.id)).map(normalizeTeam);
      renderPortal();
    } catch (error) {
      state.catalog = fallbackTeams.map(normalizeTeam);
      renderPortal(`暂时无法刷新目录，已显示本地团队：${error.message || error}`);
    }
  }

  function progressHtml(card) {
    const total = Math.max(4, Number(card.progress && card.progress.total || 0));
    const done = Number(card.progress && card.progress.done || 0);
    const visibleTotal = Math.min(total, 6);
    return `<div class="et3-progress" role="progressbar" aria-label="阶段进度：已完成 ${Math.min(done, visibleTotal)} / ${visibleTotal}" aria-valuemin="0" aria-valuemax="${visibleTotal}" aria-valuenow="${Math.min(done, visibleTotal)}">${Array.from({ length: visibleTotal }, (_, index) => `<span class="${index < done ? 'is-done' : index === done ? 'is-current' : ''}"${index === done ? ' aria-current="step"' : ''}><span class="et3-visually-hidden">第 ${index + 1} 阶段</span></span>`).join('')}</div>`;
  }

  function effectiveState(card) {
    if (card.readOnly) return 'legacy_read_only';
    const status = String(card.status || 'collecting_required');
    const gates = card.completionGates || {};
    if ((gates.document || {}).status === 'passed' && (gates.office || {}).status !== 'passed') return 'office_acceptance_required';
    return status;
  }

  function draftControlKey(control, index) {
    const dataKey = Object.entries(control.dataset || {}).find(([key]) => key.startsWith('et3'));
    const base = control.id || (dataKey ? `${dataKey[0]}:${dataKey[1]}` : control.name || `${control.tagName}:${index}`);
    return control.type === 'radio' ? `${base}:${control.value}` : base;
  }

  function draftFingerprint(card) {
    const surface = effectiveState(card);
    if (surface === 'awaiting_review') return [card.runId, surface, card.stageReviewId, card.draftIdentity?.stageAttempt, card.draftIdentity?.artifactAttempt].join(':');
    if (surface === 'office_acceptance_required') return [card.runId, surface, card.officeReview?.reviewId, card.officeReview?.documentRevision, card.officeReview?.documentSha256].join(':');
    if (surface === 'awaiting_stage_input') return [card.runId, surface, card.pendingInputId].join(':');
    if (surface === 'collecting_required' || surface === 'collecting_optional') return [card.runId, surface, list(card.questions).map(item => item.id).join(',')].join(':');
    return [card.runId, surface].join(':');
  }

  function captureWorkbenchDraft(root, card) {
    if (!root || !card) return null;
    const controls = Array.from(root.querySelectorAll('input:not([type="file"]), textarea, select'));
    const active = document.activeElement;
    return {
      fingerprint: draftFingerprint(card),
      values: controls.map((control, index) => ({
        key: draftControlKey(control, index),
        value: control.value,
        checked: Boolean(control.checked),
        kind: control.type || control.tagName,
      })),
      focusKey: controls.includes(active) ? draftControlKey(active, controls.indexOf(active)) : '',
      selectionStart: controls.includes(active) && typeof active.selectionStart === 'number' ? active.selectionStart : null,
      selectionEnd: controls.includes(active) && typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
      scrollTop: root.querySelector('.et3-workbench-scroll')?.scrollTop || 0,
    };
  }

  function restoreWorkbenchDraft(root, draft, card) {
    if (!root || !draft || draft.fingerprint !== draftFingerprint(card)) return;
    const controls = Array.from(root.querySelectorAll('input:not([type="file"]), textarea, select'));
    const saved = new Map(draft.values.map(item => [item.key, item]));
    controls.forEach((control, index) => {
      const item = saved.get(draftControlKey(control, index));
      if (!item) return;
      if (control.type === 'checkbox' || control.type === 'radio') control.checked = item.checked;
      else control.value = item.value;
    });
    const focusControl = controls.find((control, index) => draftControlKey(control, index) === draft.focusKey);
    if (focusControl) {
      focusControl.focus({ preventScroll: true });
      if (draft.selectionStart != null && typeof focusControl.setSelectionRange === 'function') focusControl.setSelectionRange(draft.selectionStart, draft.selectionEnd);
    }
    const scroll = root.querySelector('.et3-workbench-scroll');
    if (scroll) scroll.scrollTop = draft.scrollTop;
  }

  function renderStatusSurface(card) {
    if (!card || card.kind !== 'expert_team') return clearStatusSurface();
    const activeSession = window.S && window.S.session && window.S.session.session_id;
    if (card.sourceSessionId && activeSession && card.sourceSessionId !== activeSession) return clearStatusSurface();
    const previousCard = state.card;
    const main = document.getElementById('mainChat');
    if (!main) return false;
    const host = main.parentElement;
    if (!host) return false;
    document.getElementById('expertTeamWorkspacePanel')?.remove();
    let root = workbenchRoot();
    if (!root) {
      root = document.createElement('aside');
      root.id = 'expertTeamV3Workbench';
      root.className = 'expert-team-v3-workbench';
      root.dataset.expertTeamV3 = '';
      root.dataset.expertTeamV3Surface = 'workbench';
      host.appendChild(root);
    } else if (root.parentElement !== host) {
      host.appendChild(root);
    }
    const draft = captureWorkbenchDraft(root, previousCard);
    state.card = card;
    root.dataset.expertTeamRunId = card.runId || '';
    root.dataset.expertTeamSourceSessionId = card.sourceSessionId || '';
    root.dataset.expertTeamVersion = String(card.version || 0);
    root.dataset.expertTeamStageId = card.currentStageId || '';
    root.dataset.expertTeamStreamId = card.executionStreamId || '';
    root.dataset.expertTeamInputId = card.pendingInputId || '';
    root.dataset.expertTeamReviewId = card.stageReviewId || '';
    root.dataset.expertTeamReadOnly = String(card.readOnly === true);
    root.innerHTML = workbenchHtml(card);
    root.classList.toggle('is-collapsed', state.collapsed);
    document.body.classList.add('expert-team-v3-active');
    document.body.classList.toggle('expert-team-v3-collapsed', state.collapsed);
    document.querySelector('.taiji-home-shell')?.classList.remove(
      'taiji-expert-team-active', 'taiji-expert-team-panel-visible',
      'taiji-expert-team-panel-hidden', 'taiji-expert-team-panel-collapsed');
    bindWorkbenchEvents(root);
    restoreWorkbenchDraft(root, draft || state.draft, card);
    state.draft = null;
    const current = effectiveState(card);
    if (current === 'awaiting_review') ensureIdentity('document-approver');
    if (current === 'office_acceptance_required') ensureIdentity('document-reviewer');
    return true;
  }

  function clearStatusSurface() {
    if (state.workbenchController) state.workbenchController.abort();
    workbenchRoot()?.remove();
    document.body.classList.remove('expert-team-v3-active', 'expert-team-v3-collapsed');
    state.card = null;
    state.identityRole = '';
    state.identityStatus = null;
    state.officeEvidenceCount = 0;
    state.officeEvidenceKey = '';
    state.draft = null;
    state.collapsed = false;
    if (state.identityController) state.identityController.abort();
    return true;
  }

  function workbenchHtml(card) {
    const current = effectiveState(card);
    const copy = stateCopy[current] || [card.presentation?.statusLabel || '专家团任务', card.presentation?.detail || ''];
    return `<div class="et3-workbench-shell">
      <header class="et3-workbench-head"><div class="et3-workbench-head-row"><div><p class="et3-eyebrow">专家团工作台</p><h2>${esc(card.presentation?.visibleTitle || card.subtitle || '专家团任务')}</h2><p>${esc(card.team?.title || '专家团')} · ${esc(card.phase || '需求确认')}</p></div><button type="button" class="et3-icon-button" data-et3-action="close-workbench" aria-label="收起专家团工作台">×</button></div></header>
      ${progressHtml(card)}
      <div class="et3-workbench-scroll">
        <section class="et3-state-banner"><div><strong>${esc(copy[0])}</strong><p>${esc(copy[1])}</p></div><span class="et3-state-pill">${esc(card.presentation?.statusLabel || copy[0])}</span></section>
        ${statePanel(card, current)}
        <p class="et3-live" data-et3-live aria-live="polite"></p>
      </div>
    </div><button type="button" class="et3-workbench-restore" data-et3-action="restore-workbench" aria-label="展开专家团工作台">专家团</button>`;
  }

  function statePanel(card, current) {
    if (current === 'legacy_read_only') return legacyPanel(card);
    if (current === 'collecting_required' || current === 'collecting_optional') return briefPanel(card, current);
    if (current === 'ready_to_generate') return readyPanel(card);
    if (current === 'generating' || current === 'starting' || current === 'revising') return generatingPanel(card, current);
    if (current === 'awaiting_stage_input') return stageInputPanel(card);
    if (current === 'awaiting_review') return reviewPanel(card);
    if (current === 'delivery_validation_required' || current === 'completion_reconciling') return documentValidationPanel(card);
    if (current === 'office_acceptance_required') return officePanel(card);
    if (current === 'completed') return completedPanel(card);
    return failurePanel(card, current);
  }

  function briefPanel(card, current) {
    const brief = card.brief || {};
    const sources = list(brief.sources);
    const questions = list(card.questions).filter(question => !['answered', 'skipped'].includes(question.status));
    return `<section class="et3-panel"><h3>任务规格</h3>
      <dl class="et3-kv"><dt>原始诉求</dt><dd>${esc(brief.originalRequest || brief.originalRequestSummary || '')}</dd><dt>文档类型</dt><dd>${esc(brief.documentTypeLabel || brief.documentType || '')}</dd></dl>
      <form data-et3-brief-form>
        ${questions.map(question => `<div class="et3-question"><label for="et3-question-${esc(question.id)}">${esc(question.title)}</label><textarea id="et3-question-${esc(question.id)}" name="question__${esc(question.id)}" ${question.required ? 'required' : ''} placeholder="${esc(question.placeholder || '')}">${esc(question.answer || '')}</textarea></div>`).join('')}
        <label class="et3-form-field"><span>文档标题</span><input name="exact_title" value="${esc(brief.exactTitle || '')}"></label>
        <label class="et3-form-field"><span>用途</span><textarea name="purpose">${esc(brief.purpose || '')}</textarea></label>
        <label class="et3-form-field"><span>阅读对象</span><input name="audience" value="${esc(brief.audience || '')}"></label>
      </form>
    </section>
    <section class="et3-panel"><h3>资料与依据</h3><p>支持 UTF-8 纯文本、TXT、Markdown、CSV、JSON，单份不超过 10MB。</p>
      <ul class="et3-source-list">${sources.map(source => `<li class="et3-source"><span><strong>${esc(source.label || '资料')}</strong><small>${esc(source.kind || '')} · ${esc(source.status || '已绑定')}</small></span><button type="button" class="et3-button" data-et3-action="remove-source" data-source-id="${esc(source.source_id || source.sourceId)}" aria-label="移除资料：${esc(source.label || '未命名资料')}">移除</button></li>`).join('') || '<li class="et3-help">尚未添加资料。没有资料也可以继续，但缺失数据会在文档中标注待补充。</li>'}</ul>
      <label class="et3-form-field"><span>添加文字资料</span><textarea data-et3-source-text placeholder="粘贴需要引用的事实、数据或背景"></textarea></label>
      <label class="et3-form-field"><span>资料名称</span><input data-et3-source-label placeholder="例如：6月工作台账"></label>
      <div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="add-text-source">添加文字资料</button><button type="button" class="et3-button" data-et3-action="choose-source-file" aria-describedby="expertTeamV3SourceHelp">添加本地文件</button><input id="expertTeamV3SourceFile" class="et3-visually-hidden" type="file" data-et3-source-file accept=".txt,.md,.markdown,.csv,.json,text/plain,text/markdown,text/csv,application/json"><span id="expertTeamV3SourceHelp" class="et3-visually-hidden">支持 UTF-8 文本，单份不超过 10MB</span></div>
    </section>
    <div class="et3-primary-actions"><button type="button" class="et3-button" data-et3-action="save-brief">保存规格</button><button type="button" class="et3-button et3-button--primary" data-et3-action="${current === 'collecting_required' && questions.length ? 'submit-answers' : 'confirm-brief'}">${current === 'collecting_required' && questions.length ? '保存并继续' : '确认规格'}</button></div>`;
  }

  function readyPanel(card) {
    const brief = card.brief || {};
    return `<section class="et3-panel"><h3>生成前确认</h3><dl class="et3-kv"><dt>标题</dt><dd>${esc(brief.exactTitle || card.subtitle)}</dd><dt>对象</dt><dd>${esc(brief.audience || '以已确认规格为准')}</dd><dt>资料</dt><dd>${list(brief.sources).length} 份已绑定</dd></dl><p>开始后规格将冻结。每个阶段完成后都需要人工确认，不会自动越过复核。</p></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="start-generation">开始生成</button></div>`;
  }

  function generatingPanel(card, current) {
    const stage = card.workflow?.currentStage || {};
    return `<section class="et3-panel"><h3>${current === 'revising' ? '正在修改' : '当前阶段'}</h3><dl class="et3-kv"><dt>阶段</dt><dd>${esc(stage.title || card.phase || '')}</dd><dt>负责专家</dt><dd>${esc(stage.worker_name || stage.workerName || '正在分配')}</dd></dl><div class="et3-skeleton"></div><div class="et3-skeleton" style="width:82%"></div><div class="et3-skeleton" style="width:64%"></div><p>你可以继续查看对话；阶段完成后，复核入口会出现在这里。</p></section><div class="et3-inline-actions"><button type="button" class="et3-button et3-button--danger" data-et3-action="cancel-run">停止生成</button></div>`;
  }

  function stageInputPanel(card) {
    const input = card.pendingInput || {};
    const options = list(input.options || input.choices);
    return `<section class="et3-panel"><h3>${esc(input.title || '补充当前阶段信息')}</h3><p>${esc(input.prompt || input.question || input.detail || '')}</p><label class="et3-form-field"><span>补充内容</span><textarea data-et3-stage-input placeholder="填写后提交给专家团"></textarea></label>${options.length ? `<div class="et3-tags">${options.map(option => `<button type="button" class="et3-tag" data-et3-action="choose-stage-input" data-value="${esc(typeof option === 'string' ? option : option.value)}">${esc(typeof option === 'string' ? option : option.label)}</button>`).join('')}</div>` : ''}</section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="submit-stage-input">提交并继续</button></div>`;
  }

  function reviewPanel(card) {
    const result = card.stageReview?.output || card.stageResult?.output || card.stageResult || {};
    const content = result.content || card.presentation?.result?.content || '';
    const items = list(card.reviewItems);
    return `<section class="et3-panel"><h3>阶段成果</h3><div class="et3-document" tabindex="-1" data-et3-result-document>${esc(content || '阶段成果已生成，请稍后刷新状态。')}</div><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="view-result">定位到完整成果</button></div></section>
      <section class="et3-panel"><h3>复核建议</h3><ul class="et3-review-list">${items.map(item => `<li class="et3-review-item"><span><strong>${esc(item.title || '待确认事项')}</strong><small>${esc(item.phase || '待人工确认')}</small></span><button type="button" class="et3-button" data-et3-action="append-revision" data-revision-text="${esc(item.title || '')}">加入修改意见</button></li>`).join('') || '<li class="et3-help">未发现阻断问题。仍建议阅读完整成果后确认。</li>'}</ul><label class="et3-form-field"><span>修改意见</span><textarea data-et3-revision placeholder="逐条写清需要修改的位置和目标；无修改可直接进入下一阶段"></textarea></label></section>
      ${identityPanel('document-approver')}
      <div class="et3-primary-actions"><button type="button" class="et3-button" data-et3-action="submit-revision">提交修改意见</button><button type="button" class="et3-button et3-button--primary" data-et3-action="approve-stage" ${identityAllowed('document-approver') ? '' : 'disabled aria-disabled="true"'}>无修改，进入下一阶段</button></div>`;
  }

  function documentValidationPanel() {
    return `<section class="et3-panel"><h3>DOCX 自动检查</h3><p>正在核对文档结构、文件完整性和交付绑定。自动检查通过后仍需在 WPS/Word 中完成视觉验收。</p><div class="et3-skeleton"></div><div class="et3-skeleton" style="width:72%"></div></section>`;
  }

  function officePanel(card) {
    const office = card.officeReview || {};
    const evidenceKey = [card.runId || '', office.reviewId || '', office.documentSha256 || ''].join(':');
    const serverEvidenceCount = Number(office.evidenceCount || office.uploadedCount || list(office.evidence).length || list(office.visualEvidence).length || 0);
    if (state.officeEvidenceKey !== evidenceKey) {
      state.officeEvidenceKey = evidenceKey;
      state.officeEvidenceCount = serverEvidenceCount;
    } else state.officeEvidenceCount = Math.max(state.officeEvidenceCount, serverEvidenceCount);
    const failedIssues = list(office.issues);
    if (office.status === 'failed' && failedIssues.length) {
      return `<section class="et3-panel"><h3>Office 验收未通过</h3><p>请选择需要专家团修复的问题，系统会重新生成正式文档。</p><div class="et3-review-list">${failedIssues.map(issue => `<label class="et3-review-item"><input type="checkbox" data-et3-office-revision-issue="${esc(issue.issueId)}"><span><strong>${esc(issue.description)}</strong><small>${esc(issue.expectedFix)}</small></span></label>`).join('')}</div></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="office-create-revision">退回专家团修改</button></div>`;
    }
    const ready = office.reviewSessionStatus === 'ready';
    return `<section class="et3-panel"><h3>Office 验收</h3><p>自动检查已经完成。请使用可信企业验收身份，在 WPS/Word 中逐页检查正式 DOCX。</p>
      ${identityPanel('document-reviewer')}
      <div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="office-begin" ${identityAllowed('document-reviewer') ? '' : 'disabled aria-disabled="true"'}>${ready ? '重新开始可信复核' : '打开 DOCX 并开始复核'}</button><button type="button" class="et3-button" data-et3-action="choose-office-evidence" aria-describedby="expertTeamV3OfficeEvidenceHelp" ${ready ? '' : 'disabled aria-disabled="true"'}>上传复核证据</button><input id="expertTeamV3OfficeEvidence" class="et3-visually-hidden" type="file" multiple accept=".png,.jpg,.jpeg,.pdf,image/png,image/jpeg,application/pdf" data-et3-office-evidence ${ready ? '' : 'disabled'}><span id="expertTeamV3OfficeEvidenceHelp" class="et3-visually-hidden">支持 PNG、JPG 或 PDF，需先开始可信复核</span></div>
      <p class="et3-help">复核会话：${ready ? '已开始' : '待开始'} · 本次已上传 ${state.officeEvidenceCount} 份证据</p>
      <fieldset class="et3-office-grid"><legend>逐项检查</legend>${officeChecks.map(([key, label, required]) => `<label><input type="checkbox" data-et3-office-check="${key}"><span>${esc(label)}${required ? '（必检）' : '（适用时）'}</span></label>`).join('')}</fieldset>
      <fieldset class="et3-office-decision"><legend>验收结论</legend><label><input type="radio" name="et3-office-decision" value="passed" checked>通过</label><label><input type="radio" name="et3-office-decision" value="failed">不通过并记录问题</label></fieldset>
      <div class="et3-office-issue" data-et3-office-issue><h4>不通过时填写结构化问题</h4><label class="et3-form-field"><span>问题类型</span><select data-et3-office-issue-category><option value="required_check_failed">必检项未通过</option><option value="title_or_genre_mismatch">标题或文种不符</option><option value="placeholder_content">存在占位符</option><option value="duplicate_figure">图片重复</option><option value="visual_alignment">版式对齐</option><option value="minor_typography">轻微排版</option><option value="pagination_preference">分页调整</option></select></label><label class="et3-form-field"><span>问题描述</span><textarea data-et3-office-issue-description></textarea></label><label class="et3-form-field"><span>期望修复</span><textarea data-et3-office-issue-fix></textarea></label><label class="et3-form-field"><span>页码（可选）</span><input type="number" min="1" data-et3-office-issue-page></label></div>
      <label class="et3-form-field"><span>验收备注</span><textarea data-et3-office-note placeholder="例如：已在 WPS 打开并逐页检查目录、版式、表格和分页，未发现异常。"></textarea></label>
      <p class="et3-help">当前验收状态：${esc(office.status || '待开始')}</p></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="office-submit" ${ready && identityAllowed('document-reviewer') ? '' : 'disabled aria-disabled="true"'}>提交验收结论</button></div>`;
  }

  function completedPanel(card) {
    const artifacts = list(card.artifacts).filter(item => item && (item.exists !== false));
    return `<section class="et3-panel"><h3>最终交付</h3><p>文档内容、DOCX 自动检查和 Office 验收已经形成完整交付链。</p><dl class="et3-kv"><dt>交付状态</dt><dd>已完成</dd><dt>验收链</dt><dd>内容确认 · DOCX 自检 · Office 验收</dd></dl><ul class="et3-artifact-list">${artifacts.map(item => `<li class="et3-artifact"><span><strong>${esc(item.title || item.label || (item.kind === 'docx' ? '最终交付文档.docx' : item.kind) || '交付文件')}</strong><small>${esc(item.kind === 'docx' ? 'DOCX · 已验收' : (item.kind || '交付文件'))}</small></span><button type="button" class="et3-button" data-et3-action="open-artifact" data-path="${esc(item.path || '')}" data-kind="${esc(item.kind || '')}" aria-label="打开${esc(item.title || item.label || item.kind || '交付文件')}">打开</button></li>`).join('') || '<li class="et3-help">交付文件入口正在同步，请刷新任务状态。</li>'}</ul></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="open-docx">打开最终 DOCX</button></div>`;
  }

  function legacyPanel(card) {
    return `<section class="et3-panel"><h3>历史任务只读</h3><p>该任务没有新版文档规格、证据绑定和交付验收记录。为避免误写历史数据，当前只提供查看。</p><div class="et3-document" tabindex="-1" data-et3-result-document>${esc(card.presentation?.result?.content || card.presentation?.summary || '暂无可展示的历史成果。')}</div><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="view-result">定位到历史成果</button></div></section>`;
  }

  function failurePanel(card, current) {
    const canRetry = Boolean(card.actions?.can_retry) || ['start_failed', 'generation_failed', 'generated_invalid', 'result_unverified', 'legacy_result_unverified'].includes(current);
    const canCancel = Boolean(card.actions?.can_cancel) || current === 'cancelling';
    return `<section class="et3-panel"><h3>${esc(stateCopy[current]?.[0] || '任务需要处理')}</h3><p class="et3-error">${esc(card.presentation?.detail || card.presentation?.summary || '当前任务需要恢复或重新发起。')}</p><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="refresh-run">刷新状态</button>${canRetry ? '<button type="button" class="et3-button et3-button--primary" data-et3-action="retry-run">重新尝试</button>' : ''}${canCancel ? '<button type="button" class="et3-button et3-button--danger" data-et3-action="cancel-run">重试停止</button>' : ''}</div></section>`;
  }

  function bindWorkbenchEvents(root) {
    if (state.workbenchController) state.workbenchController.abort();
    state.workbenchController = new AbortController();
    const signal = state.workbenchController.signal;
    root.addEventListener('click', event => handleWorkbenchClick(event), { signal });
    root.addEventListener('change', event => handleWorkbenchChange(event), { signal });
  }

  async function handleWorkbenchClick(event) {
    const button = event.target.closest('[data-et3-action]');
    if (!button || state.busy) return;
    const action = button.dataset.et3Action;
    if (action === 'close-workbench') {
      state.draft = captureWorkbenchDraft(workbenchRoot(), state.card);
      state.collapsed = true;
      workbenchRoot()?.classList.add('is-collapsed');
      document.body.classList.add('expert-team-v3-collapsed');
      workbenchRoot()?.querySelector('[data-et3-action="restore-workbench"]')?.focus();
      return true;
    }
    if (action === 'restore-workbench') {
      state.collapsed = false;
      workbenchRoot()?.classList.remove('is-collapsed');
      document.body.classList.remove('expert-team-v3-collapsed');
      restoreWorkbenchDraft(workbenchRoot(), state.draft, state.card);
      workbenchRoot()?.querySelector('.et3-workbench-head h2')?.setAttribute('tabindex', '-1');
      workbenchRoot()?.querySelector('.et3-workbench-head h2')?.focus();
      return true;
    }
    if (action === 'append-revision') return appendRevision(button.dataset.revisionText);
    if (action === 'choose-stage-input') { const field = workbenchRoot().querySelector('[data-et3-stage-input]'); if (field) field.value = button.dataset.value || ''; return; }
    if (action === 'view-result') { const result = workbenchRoot().querySelector('[data-et3-result-document]'); if (result) { result.focus(); result.scrollIntoView({ block: 'start' }); return true; } return setLive('完整成果尚未同步，请刷新状态。', true); }
    if (action === 'open-artifact') return openArtifact(button.dataset.path, button.dataset.kind, button);
    if (action === 'open-docx') return openFinalDocx(button);
    if (action === 'choose-source-file') { workbenchRoot().querySelector('[data-et3-source-file]')?.click(); return true; }
    if (action === 'choose-office-evidence') { workbenchRoot().querySelector('[data-et3-office-evidence]')?.click(); return true; }
    if (action === 'add-text-source') return addTextSource(button);
    if (action === 'remove-source') {
      if (!window.confirm('移除后该资料不再用于本任务，确定继续吗？')) return false;
      return mutate('/api/expert-teams/brief/sources/remove', { expected_brief_revision: Number(state.card.brief?.revision || 0), source_id: button.dataset.sourceId }, button);
    }
    if (action === 'save-brief') return saveBrief(button, false);
    if (action === 'confirm-brief') return saveBrief(button, true);
    if (action === 'submit-answers') return submitAnswers(button);
    if (action === 'start-generation') return mutate('/api/expert-teams/resume', {}, button);
    if (action === 'retry-run') return mutate('/api/expert-teams/resume', {}, button, 'retry');
    if (action === 'cancel-run') return mutate('/api/expert-teams/cancel', {}, button, 'cancel');
    if (action === 'refresh-run') return refreshRun(button);
    if (action === 'submit-stage-input') return submitStageInput(button);
    if (action === 'submit-revision') return submitRevision(button);
    if (action === 'approve-stage') return approveStage(button);
    if (action === 'identity-login') return startIdentityLogin(button.dataset.identityRole, button);
    if (action === 'identity-refresh') return refreshIdentity(button.dataset.identityRole);
    if (action === 'office-begin') return beginOfficeReview(button);
    if (action === 'office-submit') return submitOffice(button);
    if (action === 'office-create-revision') return createOfficeRevision(button);
  }

  function handleWorkbenchChange(event) {
    if (event.target.matches('[data-et3-source-file]')) addLocalFile(event.target);
    if (event.target.matches('[data-et3-office-evidence]')) uploadOfficeEvidence(event.target);
  }

  function mutationControl(kind) {
    const card = state.card || {};
    return {
      session_id: card.sourceSessionId || '', run_id: card.runId || '',
      expected_version: Number(card.version || 0), stage_id: card.currentStageId || '',
      idempotency_key: uid(kind),
    };
  }

  async function mutate(endpoint, extra, button, kind) {
    setBusy(button, true, '处理中…');
    try {
      const payload = await window.api(endpoint, { method: 'POST', body: JSON.stringify({ ...mutationControl(kind || endpoint.split('/').pop()), ...(extra || {}) }) });
      applyResponse(payload);
      setLive('操作已保存。');
      return true;
    } catch (error) {
      if (error && error.payload && error.payload.run) applyResponse(error.payload);
      setLive(error.message || '操作失败，请刷新状态后重试。', true);
      return false;
    } finally { setBusy(button, false); }
  }

  function applyResponse(payload) {
    const run = payload && payload.run ? payload.run : payload;
    if (!run || !run.run_id || typeof window.buildExpertTeamCardFromRun !== 'function') return false;
    return renderStatusSurface(window.buildExpertTeamCardFromRun(run, payload));
  }

  function formValues(form) {
    return Object.fromEntries(Array.from(new FormData(form).entries()).map(([key, value]) => [key, String(value).trim()]));
  }

  async function submitAnswers(button) {
    const form = workbenchRoot().querySelector('[data-et3-brief-form]');
    const values = formValues(form);
    const answers = Object.fromEntries(Object.entries(values).filter(([key]) => key.startsWith('question__')).map(([key, value]) => [key.slice('question__'.length), value]));
    if (!await saveBriefFields(button, values)) return false;
    return mutate('/api/expert-teams/answer', { answers, skip_optional: false }, button, 'answer');
  }

  function saveBriefFields(button, values) {
    const patch = Object.fromEntries(Object.entries(values).filter(([key]) => !key.startsWith('question__')));
    return mutate('/api/expert-teams/brief/update', { expected_brief_revision: Number(state.card.brief?.revision || 0), patch }, button, 'brief-update');
  }

  async function saveBrief(button, confirmAfter) {
    const form = workbenchRoot().querySelector('[data-et3-brief-form]');
    const values = formValues(form);
    const saved = await saveBriefFields(button, values);
    if (!saved || !confirmAfter) return saved;
    return mutate('/api/expert-teams/brief/confirm', { expected_brief_revision: Number(state.card.brief?.revision || 0) }, button, 'brief-confirm');
  }

  async function addTextSource(button) {
    const text = String(workbenchRoot().querySelector('[data-et3-source-text]')?.value || '').trim();
    const label = String(workbenchRoot().querySelector('[data-et3-source-label]')?.value || '').trim() || '粘贴资料';
    if (!text) return setLive('请先填写需要添加的文字资料。', true);
    return mutate('/api/expert-teams/brief/sources/add', { expected_brief_revision: Number(state.card.brief?.revision || 0), source: { kind: 'provided_text', label, text } }, button, 'source-add');
  }

  async function addLocalFile(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { setLive('文件超过 10MB，未添加。', true); input.value = ''; return; }
    const extension = (file.name.split('.').pop() || '').toLowerCase();
    if (!['txt', 'md', 'markdown', 'csv', 'json'].includes(extension)) { setLive('仅支持 TXT、Markdown、CSV、JSON。', true); input.value = ''; return; }
    try {
      const bytes = await file.arrayBuffer();
      const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
      if (text.includes('\u0000')) throw new Error('文件不是有效的 UTF-8 文本');
      await mutate('/api/expert-teams/brief/sources/add', { expected_brief_revision: Number(state.card.brief?.revision || 0), source: { kind: 'provided_text', label: file.name, text } }, input, 'source-file-add');
    } catch (error) { setLive(error.message || '读取文件失败。', true); }
    input.value = '';
  }

  function appendRevision(text) {
    const field = workbenchRoot().querySelector('[data-et3-revision]');
    if (!field) return;
    const line = String(text || '').trim();
    if (line && !field.value.includes(line)) field.value = `${field.value.trim()}${field.value.trim() ? '\n' : ''}- ${line}`;
    field.focus();
  }

  function submitRevision(button) {
    const feedback = String(workbenchRoot().querySelector('[data-et3-revision]')?.value || '').trim();
    if (!feedback) return setLive('请填写修改意见；若无修改，请使用“无修改，进入下一阶段”。', true);
    return mutate('/api/expert-teams/stage/revise', { feedback, review_id: state.card.stageReviewId || '' }, button, 'stage-revise');
  }

  function approveStage(button) {
    if (!identityAllowed('document-approver')) return setLive('需先使用具有文档审批权限的企业身份登录。', true);
    return mutate('/api/expert-teams/stage/approve', { review_id: state.card.stageReviewId || '' }, button, 'stage-approve');
  }

  function submitStageInput(button) {
    const answer = String(workbenchRoot().querySelector('[data-et3-stage-input]')?.value || '').trim();
    if (!answer) return setLive('请先填写补充内容。', true);
    return mutate('/api/expert-teams/stage/input', { input_id: state.card.pendingInputId || '', answer }, button, 'stage-input');
  }

  async function openArtifact(path, kind, button) {
    if (!path) return setLive('文件入口尚未同步，请刷新任务状态。', true);
    if (typeof window.openExpertTeamFileArtifact === 'function') {
      button.dataset.expertTeamArtifactPath = path;
      button.dataset.expertTeamArtifactKind = kind || 'file';
      button.dataset.expertTeamArtifactExists = 'true';
      return window.openExpertTeamFileArtifact(button);
    }
    return setLive('当前桌面端不支持打开文件。', true);
  }

  function finalDocument() {
    return list(state.card && state.card.artifacts).find(item => item && (item.kind === 'docx' || /document\.docx$/i.test(item.path || '')));
  }

  function openFinalDocx(button) {
    const artifact = finalDocument();
    return openArtifact(artifact && artifact.path, 'docx', button);
  }

  function identityAllowed(role) {
    const status = state.identityStatus || {};
    const roles = list(status.principal && status.principal.roles);
    return status.enabled !== false && status.authenticated === true && roles.includes(role);
  }

  function identityPanel(role) {
    const allowed = identityAllowed(role);
    const status = state.identityStatus || {};
    const name = status.principal && (status.principal.display_name || status.principal.displayName);
    const loginLabel = role === 'document-reviewer' ? '使用企业验收身份登录' : '使用企业审批身份登录';
    const message = allowed ? `${esc(name || '企业身份')} · 权限已验证` : loginLabel;
    return `<section class="et3-identity" aria-label="企业身份"><span>${message}</span><div class="et3-inline-actions">${allowed ? '<button type="button" class="et3-button" data-et3-action="identity-refresh" data-et3-identity-action="refresh" data-identity-role="' + role + '">刷新身份</button>' : '<button type="button" class="et3-button" data-et3-action="identity-login" data-et3-identity-action="login" data-identity-role="' + role + '">' + loginLabel + '</button>'}</div></section>`;
  }

  async function ensureIdentity(role) {
    if (state.identityRole === role && state.identityStatus) return state.identityStatus;
    return refreshIdentity(role);
  }

  async function refreshIdentity(role) {
    state.identityRole = role;
    try {
      const status = await window.api('/api/expert-teams/identity/status');
      if (state.identityRole !== role) return status;
      state.identityStatus = status || {};
      if (state.card) renderStatusSurface(state.card);
      return status;
    } catch (error) {
      state.identityStatus = { enabled: true, authenticated: false };
      setLive(`企业身份状态检查失败：${error.message || error}`, true);
      return state.identityStatus;
    }
  }

  function identityDelay(ms, signal) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, ms);
      signal.addEventListener('abort', () => { clearTimeout(timer); const error = new Error('identity login aborted'); error.name = 'AbortError'; reject(error); }, { once: true });
    });
  }

  async function startIdentityLogin(role, button) {
    if (state.identityController) state.identityController.abort();
    const controller = new AbortController();
    state.identityController = controller;
    setBusy(button, true, '正在打开登录…');
    const popup = window.open('', '_blank');
    if (!popup) { setBusy(button, false); return setLive('登录窗口被浏览器拦截，请允许弹出窗口后重试。', true); }
    try {
      const redirectUri = `${window.location.origin}/api/expert-teams/identity/callback`;
      const flow = await window.api('/api/expert-teams/identity/start', { method: 'POST', signal: controller.signal, body: JSON.stringify({ redirect_uri: redirectUri, purpose: 'login' }) });
      popup.opener = null;
      popup.location.replace(String(flow.authorization_url || ''));
      setLive('已打开安全登录窗口，完成后此处会自动更新。');
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await identityDelay(1000, controller.signal);
        const status = await window.api(`/api/expert-teams/identity/status?flow_id=${encodeURIComponent(String(flow.flow_id || ''))}`, { signal: controller.signal });
        const flowStatus = String(status.identity_flow_status || status.login_state || '');
        if (['cancelled', 'failed', 'expired', 'session_mismatch'].includes(flowStatus)) throw new Error('企业身份登录未完成');
        if (flowStatus === 'completed' && status.authenticated) {
          state.identityStatus = status;
          state.identityRole = role;
          renderStatusSurface(state.card);
          return identityAllowed(role);
        }
      }
      throw new Error('企业身份登录已过期，请重试');
    } catch (error) {
      try { popup.close(); } catch (_error) { /* ignored */ }
      if (error.name !== 'AbortError') setLive(error.message || '企业身份登录失败。', true);
      return false;
    } finally {
      if (state.identityController === controller) state.identityController = null;
      setBusy(button, false);
    }
  }

  async function refreshRun(button) {
    const card = state.card || {};
    setBusy(button, true, '正在刷新…');
    try {
      const payload = await window.api(`/api/expert-teams/run?session_id=${encodeURIComponent(card.sourceSessionId || '')}&run_id=${encodeURIComponent(card.runId || '')}`);
      applyResponse(payload);
      setLive('状态已刷新。');
      return payload;
    } catch (error) {
      setLive(`状态刷新失败：${error.message || error}`, true);
      return null;
    } finally { setBusy(button, false); }
  }

  async function beginOfficeReview(button) {
    if (!identityAllowed('document-reviewer')) return setLive('需先使用具有文档验收权限的企业身份登录。', true);
    setBusy(button, true, '正在打开…');
    try {
      const opened = await openFinalDocx(button);
      if (!opened) throw new Error('未能打开最终 DOCX，本次复核未开始');
      await window.api('/api/docx-engine-v2/quality/wps-visual/begin', { method: 'POST', body: JSON.stringify({ session_id: state.card.sourceSessionId, run_id: state.card.runId, expected_version: Number(state.card.version || 0) }) });
      state.card.officeReview = { ...(state.card.officeReview || {}), reviewSessionStatus: 'ready' };
      state.officeEvidenceKey = [state.card.runId || '', state.card.officeReview?.reviewId || '', state.card.officeReview?.documentSha256 || ''].join(':');
      state.officeEvidenceCount = 0;
      renderStatusSurface(state.card);
      setLive('可信复核已开始，请上传本次 WPS/Word 检查证据。');
      return true;
    } catch (error) {
      setLive(`可信复核启动失败：${error.message || error}`, true);
      return false;
    } finally { setBusy(button, false); }
  }

  async function uploadOfficeEvidence(input) {
    const files = Array.from(input.files || []);
    if (!files.length) return false;
    if (state.card?.officeReview?.reviewSessionStatus !== 'ready') return setLive('请先打开 DOCX 并开始可信复核。', true);
    const form = new FormData();
    form.append('session_id', String(state.card.sourceSessionId || ''));
    form.append('run_id', String(state.card.runId || ''));
    form.append('expected_version', String(state.card.version || 0));
    files.forEach((file, index) => form.append(`file_${index}`, file, file.name));
    try {
      const result = await window.api('/api/docx-engine-v2/quality/wps-visual/evidence', { method: 'POST', body: form });
      state.officeEvidenceCount = Number(result.count || result.uploaded_count || files.length);
      renderStatusSurface(state.card);
      setLive(`已上传 ${state.officeEvidenceCount} 份本次复核证据。`);
      return true;
    } catch (error) {
      setLive(`证据上传失败：${error.message || error}`, true);
      return false;
    } finally { input.value = ''; }
  }

  function officeIssue(root, decision) {
    if (decision !== 'failed') return [];
    const category = String(root.querySelector('[data-et3-office-issue-category]')?.value || '');
    const description = String(root.querySelector('[data-et3-office-issue-description]')?.value || '').trim();
    const expectedFix = String(root.querySelector('[data-et3-office-issue-fix]')?.value || '').trim();
    const page = Number(root.querySelector('[data-et3-office-issue-page]')?.value || 0);
    if (!description || !expectedFix) throw new Error('不通过时必须填写问题描述和期望修复。');
    const conditions = new Set(['visual_alignment', 'minor_typography', 'pagination_preference']);
    return [{ issue_id: `ui-${state.card.runId}-${Date.now()}`, severity: conditions.has(category) ? 'condition' : 'blocking', category, description, expected_fix: expectedFix, ...(page > 0 ? { page } : {}) }];
  }

  async function submitOffice(button) {
    const root = workbenchRoot();
    if (!identityAllowed('document-reviewer')) return setLive('需先使用具有文档验收权限的企业身份登录。', true);
    if (state.officeEvidenceCount < 1) return setLive('请先上传至少 1 份本次 WPS/Word 复核证据。', true);
    const decision = String(root.querySelector('input[name="et3-office-decision"]:checked')?.value || '');
    const note = String(root.querySelector('[data-et3-office-note]')?.value || '').trim();
    if (note.length < 10 || !/(wps|word)/i.test(note) || !/(打开|页面|逐页|分页)/.test(note) || !/(目录|版式|布局|图表|图片|表格|分页|页眉|页脚|字体)/.test(note)) return setLive('验收备注需说明 WPS/Word、打开或逐页检查，以及已核对的版式区域。', true);
    const selected = new Map(Array.from(root.querySelectorAll('[data-et3-office-check]')).map(item => [item.dataset.et3OfficeCheck, item.checked]));
    const requiredMissing = officeChecks.some(([key, _label, required]) => required && !selected.get(key));
    if (decision === 'passed' && requiredMissing) return setLive('全部必检项通过后才能提交“通过”。', true);
    let issues;
    try { issues = officeIssue(root, decision); } catch (error) { return setLive(error.message, true); }
    const checklist = Object.fromEntries(officeChecks.map(([key, _label, required]) => [key, selected.get(key) ? 'passed' : (required ? 'not_checked' : 'not_applicable')]));
    setBusy(button, true, '正在提交…');
    try {
      await window.api('/api/docx-engine-v2/quality/wps-visual', { method: 'POST', body: JSON.stringify({ session_id: state.card.sourceSessionId, run_id: state.card.runId, expected_version: Number(state.card.version || 0), status: decision, checklist, issues, note, idempotency_key: uid('office-acceptance') }) });
      const refreshed = await refreshRun();
      if (!refreshed) throw new Error('验收已提交，但最新交付状态尚未同步，请手动刷新');
      setLive(decision === 'passed' ? 'Office 验收已通过，正在闭合最终交付。' : 'Office 问题已记录，请选择问题并退回修改。');
      return true;
    } catch (error) {
      setLive(`Office 验收提交失败：${error.message || error}；当前输入仍保留。`, true);
      return false;
    } finally { setBusy(button, false); }
  }

  async function createOfficeRevision(button) {
    const issueIds = Array.from(workbenchRoot().querySelectorAll('[data-et3-office-revision-issue]:checked')).map(item => item.dataset.et3OfficeRevisionIssue).filter(Boolean);
    if (!issueIds.length) return setLive('请至少选择一个需要修复的问题。', true);
    setBusy(button, true, '正在退回…');
    try {
      const payload = await window.api('/api/expert-teams/office-revisions/create', { method: 'POST', body: JSON.stringify({ session_id: state.card.sourceSessionId, run_id: state.card.runId, expected_version: Number(state.card.version || 0), office_review_id: state.card.officeReview?.reviewId || '', issue_ids: issueIds, idempotency_key: uid('office-revision') }) });
      applyResponse(payload);
      return true;
    } catch (error) {
      setLive(`退回修改失败：${error.message || error}`, true);
      return false;
    } finally { setBusy(button, false); }
  }

  function setLive(message, error) {
    const live = workbenchRoot() && workbenchRoot().querySelector('[data-et3-live]');
    if (live) { live.textContent = message || ''; live.classList.toggle('et3-error', Boolean(error)); live.setAttribute('role', error ? 'alert' : 'status'); live.setAttribute('aria-live', error ? 'assertive' : 'polite'); }
    return false;
  }

  function setBusy(button, busy, label) {
    state.busy = busy;
    if (!button) return;
    if (busy) { button.dataset.et3OriginalLabel = button.textContent; button.textContent = label || '处理中…'; }
    else if (button.dataset.et3OriginalLabel) { button.textContent = button.dataset.et3OriginalLabel; delete button.dataset.et3OriginalLabel; }
    button.disabled = busy;
    button.setAttribute('aria-busy', String(Boolean(busy)));
  }

  function init() {
    renderPortal();
    window.loadWriteflow = loadCatalog;
    window.renderWriteflowTeams = renderPortal;
    window.openWriteflowTeamModal = openTeam;
    window.closeWriteflowTeamModal = closeDialog;
    window.renderExpertTeamStatusSurface = renderStatusSurface;
  }

  window.ExpertTeamV3 = Object.freeze({
    init, loadCatalog, renderPortal, renderStatusSurface, clearStatusSurface,
    applyResponse, effectiveState,
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
}());
