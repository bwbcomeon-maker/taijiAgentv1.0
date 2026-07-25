(function () {
  'use strict';

  const state = {
    catalog: [],
    selectedTeam: null,
    selectedExample: null,
    card: null,
    portalController: null,
    workbenchController: null,
    keyboardBound: false,
    dialogReturnFocus: null,
    draft: null,
    conflictRevisionDraft: null,
    collapsed: false,
    busy: false,
    catalogStatus: 'idle',
    catalogError: '',
  };

  const teamPresentationDefaults = [
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
    intake: ['确认任务规格', '先把主题、对象、用途和边界确认清楚。'],
    ready: ['规格已确认', '开始后专家团将按阶段生成，每一阶段都可复核。'],
    executing: ['专家协作中', '当前阶段正在生成，完成后会进入人工复核。'],
    awaiting_stage_confirmation: ['阶段成果待确认', '阅读成果后，可以直接进入下一阶段或提交修改意见。'],
    revising: ['正在按意见修改', '修改完成后会回到当前阶段复核。'],
    generating_document: ['正在生成正式文档', '内容已确认，正在完成 DOCX 自动检查。'],
    awaiting_delivery_confirmation: ['最终文档待确认', '请在本机打开文档检查，确认后再完成交付。'],
    completed: ['文档已交付', '正式 DOCX 已生成，可打开或下载。'],
    contract_error: ['状态暂不可用', '服务端没有返回完整的单机任务状态，请刷新后重试。'],
    failed: ['任务未完成', '查看原因后返回专家团门户重新发起。'],
    cancelled: ['任务已取消', '当前任务已停止，不会继续生成。'],
    cancelling: ['正在停止专家团', '停止请求正在确认，可刷新查看最新状态。'],
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
    const fallback = teamPresentationDefaults.find(item => item.id === team.id) || {};
    const examples = list(team.examples).map(example => ({
      ...example,
      available: example.available === true && Boolean(String(example.launch_profile_id || '').trim()),
      disabled_reason: String(example.disabled_reason || '该任务尚未完成交付验证'),
    }));
    return {
      ...fallback,
      ...team,
      title: team.title || fallback.title || '专家团',
      description: team.description || fallback.description || '',
      image: team.image || fallback.image || '',
      tags: list(team.tags).length ? team.tags : list(fallback.tags),
      members: list(team.members),
      examples,
      available: examples.some(example => example.available === true),
    };
  }

  function portalRoot() { return document.getElementById('expertTeamV3PortalRoot'); }
  function workbenchRoot() { return document.getElementById('expertTeamV3Workbench'); }

  function renderPortal(message) {
    const root = portalRoot();
    if (!root) return false;
    const teams = state.catalog;
    const statusMessage = message || (state.catalogStatus === 'loading'
      ? '正在加载专家团…'
      : (state.catalogStatus === 'error' ? state.catalogError : ''));
    const catalogSurface = state.catalogStatus === 'error'
      ? `<section class="et3-catalog-error" role="alert"><strong>专家团目录暂时不可用</strong><p>${esc(state.catalogError || '无法确认当前可用的专家团。')}</p><button type="button" class="et3-button" data-et3-action="retry-catalog">重新加载</button></section>`
      : (teams.length
        ? teams.map(team => teamCard(team)).join('')
        : `<p class="et3-catalog-empty" role="status">${state.catalogStatus === 'loading' ? '正在读取可用任务…' : '请加载专家团目录。'}</p>`);
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
        <p class="et3-status" data-et3-portal-status aria-live="polite">${esc(statusMessage)}</p>
        <div class="et3-team-grid" data-et3-team-grid>
          ${catalogSurface}
        </div>
      </main>
      <div class="et3-dialog-backdrop" data-et3-dialog-backdrop hidden>
        <section class="et3-dialog" role="dialog" aria-modal="true" aria-labelledby="expertTeamV3DialogTitle" data-et3-dialog></section>
      </div>`;
    bindPortalEvents(root);
    return true;
  }

  function teamCard(team) {
    const unavailable = team.available !== true;
    const reason = list(team.examples).map(example => example.disabled_reason).find(Boolean) || '暂无通过验证的文档任务';
    return `<button type="button" class="et3-team-card${unavailable ? ' is-disabled' : ''}" data-et3-action="open-team" data-team-id="${esc(team.id)}" aria-label="${unavailable ? esc(`${team.title}暂不可用：${reason}`) : esc(`查看并发起${team.title}`)}" aria-disabled="${String(unavailable)}" ${unavailable ? 'disabled' : ''}>
      <img src="${esc(team.image)}" alt="" loading="lazy">
      <span>
        <small>${esc(team.category || '专业协作')}</small>
        <h2>${esc(team.title)}</h2>
        <p>${esc(team.description)}</p>
        <span class="et3-tags">${list(team.tags).slice(0, 4).map(tag => `<span class="et3-tag">${esc(tag)}</span>`).join('')}</span>
        <span class="et3-card-cta">${unavailable ? esc(reason) : '查看并发起 <span aria-hidden="true">→</span>'}</span>
      </span>
    </button>`;
  }

  function bindPortalEvents(root) {
    if (state.portalController) state.portalController.abort();
    state.portalController = new AbortController();
    const signal = state.portalController.signal;
    root.addEventListener('click', event => handlePortalClick(event), { signal });
    root.addEventListener('input', event => handlePortalInput(event), { signal });
  }

  function handlePortalInput(event) {
    if (event.target.id !== 'expertTeamV3Search') return;
    const query = event.target.value.trim().toLowerCase();
    const teams = state.catalog.filter(team =>
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
    if (kind === 'retry-catalog') loadCatalog(true);
  }

  async function openTeam(teamId, trigger) {
    if (!trigger && typeof switchPanel === 'function') await switchPanel('writing');
    if (state.catalogStatus !== 'ready') await loadCatalog(true);
    const team = state.catalog.find(item => item.id === teamId);
    if (!team) return;
    state.selectedTeam = team;
    state.selectedExample = list(team.examples).find(example => example.available === true) || null;
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
    const hasAvailableTask = examples.some(example => example.available === true);
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
            <div class="et3-template-list">${examples.map(example => {
              const unavailable = example.available !== true;
              const detail = unavailable
                ? (example.disabled_reason || '暂未开放')
                : (example.summary || (example.capability && example.capability.label) || '本机协作');
              return `<button type="button" class="et3-template${unavailable ? ' is-disabled' : ''}" data-et3-action="select-template" data-example-id="${esc(example.id)}" aria-pressed="${state.selectedExample ? state.selectedExample.id === example.id : false}" aria-disabled="${String(unavailable)}" ${unavailable ? 'disabled' : ''}><strong>${esc(example.label || '文档任务')}</strong><span>${esc(detail)}</span></button>`;
            }).join('')}</div>
            <label class="et3-form-field" for="expertTeamV3Prompt"><span>原始诉求</span><textarea id="expertTeamV3Prompt" rows="6" aria-describedby="expertTeamV3PromptHelp">${esc(prompt)}</textarea></label>
            <p id="expertTeamV3PromptHelp" class="et3-help">发起后先确认完整任务规格，不会直接生成文档。</p>
            <p class="et3-live" data-et3-dialog-live aria-live="polite"></p>
          </section>
        </div>
      </div>
      <footer class="et3-dialog-actions"><button type="button" class="et3-button" data-et3-action="close-dialog">取消</button><button type="button" class="et3-button et3-button--primary" data-et3-action="summon" ${hasAvailableTask ? '' : 'disabled aria-disabled="true" title="当前没有已通过交付验证的文档任务"'}>发起专家团任务</button></footer>`;
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
    if (!example || example.available !== true) return;
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
    const fallbackSelector = state.selectedTeam?.id
      ? `[data-et3-action="open-team"][data-team-id="${CSS.escape(state.selectedTeam.id)}"]`
      : '';
    const returnFocus = state.dialogReturnFocus?.isConnected
      ? state.dialogReturnFocus
      : (fallbackSelector ? portalRoot()?.querySelector(fallbackSelector) : null);
    returnFocus?.focus();
    return true;
  }

  async function summon(button) {
    const prompt = String(document.getElementById('expertTeamV3Prompt')?.value || '').trim();
    const live = portalRoot().querySelector('[data-et3-dialog-live]');
    if (!prompt) { live.textContent = '请先填写本次任务诉求。'; return; }
    if (typeof window.sendExpertTeamAction !== 'function') { live.textContent = '专家团启动服务尚未就绪，请刷新后重试。'; return; }
    const example = state.selectedExample || {};
    if (example.available !== true || !String(example.launch_profile_id || '').trim()) {
      live.textContent = example.disabled_reason || '当前文档任务尚未开放，请选择可用任务。';
      return;
    }
    setBusy(button, true, '正在发起…');
    const payload = {
      launch_profile_id: String(example.launch_profile_id),
      prompt,
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
    if (state.catalogStatus === 'ready' && !force) return renderPortal();
    state.catalogStatus = 'loading';
    state.catalogError = '';
    renderPortal();
    try {
      const payload = await window.api('/api/expert-teams/catalog');
      if (!payload || payload.product_mode !== 'standalone' || !Array.isArray(payload.teams)) {
        throw new Error('服务端未返回可用的单机专家团目录');
      }
      const allowed = new Set(['content-creator-team', 'deep-research-team']);
      state.catalog = list(payload && payload.teams).filter(team => allowed.has(team.id)).map(normalizeTeam);
      state.catalogStatus = 'ready';
      renderPortal();
    } catch (error) {
      state.catalog = [];
      state.catalogStatus = 'error';
      state.catalogError = `无法加载已验证的专家团：${error.message || error}`;
      renderPortal();
    }
  }

  function progressHtml(card) {
    const total = Math.max(4, Number(card.progress && card.progress.total || 0));
    const done = Number(card.progress && card.progress.done || 0);
    const visibleTotal = Math.min(total, 6);
    return `<div class="et3-progress" role="progressbar" aria-label="阶段进度：已完成 ${Math.min(done, visibleTotal)} / ${visibleTotal}" aria-valuemin="0" aria-valuemax="${visibleTotal}" aria-valuenow="${Math.min(done, visibleTotal)}">${Array.from({ length: visibleTotal }, (_, index) => `<span class="${index < done ? 'is-done' : index === done ? 'is-current' : ''}"${index === done ? ' aria-current="step"' : ''}><span class="et3-visually-hidden">第 ${index + 1} 阶段</span></span>`).join('')}</div>`;
  }

  function effectiveState(card) {
    if (card.readOnly || card.productMode !== 'standalone') return 'legacy_read_only';
    return String(card.publicState || 'contract_error');
  }

  function actionAllowed(card, action) {
    return card?.productMode === 'standalone' && list(card.allowedActions).includes(action);
  }

  function stateCopyFor(card, current) {
    if (current === 'ready' && actionAllowed(card, 'submit_stage_input')) {
      return ['需要你的补充', '专家团在继续当前阶段前，需要你确认一项信息。'];
    }
    if (current === 'ready' && actionAllowed(card, 'resume')) {
      return ['任务等待恢复', '上一次执行未完整结束，可以从已保存状态继续。'];
    }
    return stateCopy[current] || [card.presentation?.statusLabel || '专家团任务', card.presentation?.detail || ''];
  }

  function draftControlKey(control, index) {
    const dataKey = Object.entries(control.dataset || {}).find(([key]) => key.startsWith('et3'));
    const base = control.id || (dataKey ? `${dataKey[0]}:${dataKey[1]}` : control.name || `${control.tagName}:${index}`);
    return control.type === 'radio' ? `${base}:${control.value}` : base;
  }

  function draftFingerprint(card) {
    const surface = effectiveState(card);
    if (surface === 'awaiting_stage_confirmation') {
      const bindingFingerprint = stageBindingFingerprint(card);
      return bindingFingerprint ? JSON.stringify([card.runId, surface, bindingFingerprint]) : '';
    }
    if (surface === 'intake') return [card.runId, surface, list(card.questions).map(item => item.id).join(',')].join(':');
    if (surface === 'ready' && actionAllowed(card, 'submit_stage_input')) return [card.runId, surface, 'submit_stage_input', card.pendingInputId].join(':');
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
    if (!root || !draft || !draft.fingerprint || draft.fingerprint !== draftFingerprint(card)) return;
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

  function captureConflictRevisionDraft(card) {
    const value = String(workbenchRoot()?.querySelector('[data-et3-revision]')?.value || '');
    const stageFingerprint = stageBindingFingerprint(card);
    if (!value.trim() || !card?.runId || !stageFingerprint) return null;
    return { runId: card.runId, stageFingerprint, value };
  }

  function stageBindingFingerprint(card) {
    const binding = card?.stageActionBinding || {};
    const fields = ['session_id', 'run_id', 'expected_version', 'stage_id', 'stage_attempt', 'artifact_id', 'artifact_sha256'];
    const values = fields.map(field => String(binding[field] ?? ''));
    if (values.some(value => !value)) return '';
    return JSON.stringify(values);
  }

  function conflictDraftMatches(card, draft) {
    return Boolean(
      draft
      && draft.runId === card?.runId
      && draft.stageFingerprint
      && draft.stageFingerprint === stageBindingFingerprint(card)
    );
  }

  function restoreConflictRevisionDraft(root, card) {
    const draft = state.conflictRevisionDraft;
    const field = root?.querySelector('[data-et3-revision]');
    if (!conflictDraftMatches(card, draft) || !field) return false;
    if (!String(field.value || '').trim()) field.value = draft.value;
    return true;
  }

  function staleConflictRevisionHtml(card) {
    const draft = state.conflictRevisionDraft;
    if (!draft || draft.runId !== card?.runId || conflictDraftMatches(card, draft)) return '';
    return `<section class="et3-panel et3-stale-draft" role="status"><h3>上一阶段有未提交的修改意见</h3><p>阶段或产物已变更，为避免误提交，以下内容未自动带入当前阶段。如仍适用，请手动复制并重新核对。</p><textarea readonly data-et3-stale-revision aria-label="上一阶段未提交的修改意见">${esc(draft.value)}</textarea></section>`;
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
    const staleRevisionDraft = captureConflictRevisionDraft(previousCard);
    if (staleRevisionDraft && !conflictDraftMatches(card, staleRevisionDraft)) {
      state.conflictRevisionDraft = staleRevisionDraft;
    }
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
    restoreConflictRevisionDraft(root, card);
    state.draft = null;
    return true;
  }

  function clearStatusSurface() {
    if (state.workbenchController) state.workbenchController.abort();
    workbenchRoot()?.remove();
    document.body.classList.remove('expert-team-v3-active', 'expert-team-v3-collapsed');
    state.card = null;
    state.draft = null;
    state.collapsed = false;
    return true;
  }

  function workbenchHtml(card) {
    const current = effectiveState(card);
    const copy = stateCopyFor(card, current);
    const statusLabel = copy[0];
    return `<div class="et3-workbench-shell">
      <header class="et3-workbench-head"><div class="et3-workbench-head-row"><div><p class="et3-eyebrow">专家团工作台</p><h2>${esc(card.presentation?.visibleTitle || card.subtitle || '专家团任务')}</h2><p>${esc(card.team?.title || '专家团')} · ${esc(card.phase || '需求确认')}</p></div><button type="button" class="et3-icon-button" data-et3-action="close-workbench" aria-label="收起专家团工作台">×</button></div></header>
      ${progressHtml(card)}
      <div class="et3-workbench-scroll">
        <section class="et3-state-banner"><div><strong>${esc(copy[0])}</strong><p>${esc(copy[1])}</p></div><span class="et3-state-pill">${esc(statusLabel)}</span></section>
        ${staleConflictRevisionHtml(card)}
        ${statePanel(card, current)}
        <p class="et3-live" data-et3-live aria-live="polite"></p>
      </div>
    </div><button type="button" class="et3-workbench-restore" data-et3-action="restore-workbench" aria-label="展开专家团工作台">专家团</button>`;
  }

  function statePanel(card, current) {
    if (current === 'legacy_read_only') return legacyPanel(card);
    if (current === 'intake') return briefPanel(card);
    if (current === 'ready' && actionAllowed(card, 'submit_stage_input')) return stageInputPanel(card);
    if (current === 'ready' && actionAllowed(card, 'resume')) return resumePanel(card);
    if (current === 'ready') return readyPanel(card);
    if (current === 'executing' || current === 'revising') return generatingPanel(card, current);
    if (current === 'cancelling') return cancellationPanel(card);
    if (current === 'awaiting_stage_confirmation') return reviewPanel(card);
    if (current === 'generating_document') return documentValidationPanel(card);
    if (current === 'awaiting_delivery_confirmation') return deliveryConfirmationPanel(card);
    if (current === 'completed') return completedPanel(card);
    return failurePanel(card, current);
  }

  function briefPanel(card) {
    const brief = card.brief || {};
    const sources = list(brief.sources);
    const questions = list(card.questions).filter(question => !['answered', 'skipped'].includes(question.status));
    const canAnswer = actionAllowed(card, 'answer');
    const disabled = canAnswer ? '' : 'disabled aria-disabled="true" aria-describedby="expertTeamV3IntakeActionHelp"';
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
      <ul class="et3-source-list">${sources.map(source => `<li class="et3-source"><span><strong>${esc(source.label || '资料')}</strong><small>${esc(source.kind || '')} · ${esc(source.status || '已绑定')}</small></span><button type="button" class="et3-button" data-et3-action="remove-source" data-source-id="${esc(source.source_id || source.sourceId)}" aria-label="移除资料：${esc(source.label || '未命名资料')}" ${disabled}>移除</button></li>`).join('') || '<li class="et3-help">尚未添加资料。没有资料也可以继续，但缺失数据会在文档中标注待补充。</li>'}</ul>
      <label class="et3-form-field"><span>添加文字资料</span><textarea data-et3-source-text placeholder="粘贴需要引用的事实、数据或背景"></textarea></label>
      <label class="et3-form-field"><span>资料名称</span><input data-et3-source-label placeholder="例如：6月工作台账"></label>
      <div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="add-text-source" ${disabled}>添加文字资料</button><button type="button" class="et3-button" data-et3-action="choose-source-file" aria-describedby="expertTeamV3SourceHelp${canAnswer ? '' : ' expertTeamV3IntakeActionHelp'}" ${canAnswer ? '' : 'disabled aria-disabled="true"'}>添加本地文件</button><input id="expertTeamV3SourceFile" class="et3-visually-hidden" type="file" data-et3-source-file accept=".txt,.md,.markdown,.csv,.json,text/plain,text/markdown,text/csv,application/json" ${canAnswer ? '' : 'disabled'}><span id="expertTeamV3SourceHelp" class="et3-visually-hidden">支持 UTF-8 文本，单份不超过 10MB</span></div>
    </section>
    ${canAnswer ? '' : '<p id="expertTeamV3IntakeActionHelp" class="et3-help">任务规格已被其他操作更新，请刷新状态后继续。</p>'}
    <div class="et3-primary-actions"><button type="button" class="et3-button" data-et3-action="save-brief" ${disabled}>保存规格</button><button type="button" class="et3-button et3-button--primary" data-et3-action="${questions.length ? 'submit-answers' : 'confirm-brief'}" ${disabled}>${questions.length ? '保存并继续' : '确认规格'}</button></div>`;
  }

  function readyPanel(card) {
    const brief = card.brief || {};
    const canStart = actionAllowed(card, 'start_generation');
    return `<section class="et3-panel"><h3>生成前确认</h3><dl class="et3-kv"><dt>标题</dt><dd>${esc(brief.exactTitle || card.subtitle)}</dd><dt>对象</dt><dd>${esc(brief.audience || '以已确认规格为准')}</dd><dt>资料</dt><dd>${list(brief.sources).length} 份已绑定</dd></dl><p>开始后规格将冻结。每个阶段完成后都需要人工确认，不会自动越过复核。</p></section>${canStart ? '<div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="start-generation">开始生成</button></div>' : '<p class="et3-help">当前状态尚不允许开始生成，请刷新后重试。</p>'}`;
  }

  function generatingPanel(card, current) {
    const stage = card.workflow?.currentStage || {};
    const canCancel = actionAllowed(card, 'cancel');
    return `<section class="et3-panel"><h3>${current === 'revising' ? '正在修改' : '当前阶段'}</h3><dl class="et3-kv"><dt>阶段</dt><dd>${esc(stage.title || card.phase || '')}</dd><dt>负责专家</dt><dd>${esc(stage.worker_name || stage.workerName || '正在分配')}</dd></dl><div class="et3-skeleton"></div><div class="et3-skeleton" style="width:82%"></div><div class="et3-skeleton" style="width:64%"></div><p>你可以继续查看对话；阶段完成后，复核入口会出现在这里。</p></section>${canCancel ? '<div class="et3-inline-actions"><button type="button" class="et3-button et3-button--danger" data-et3-action="cancel-run">停止生成</button></div>' : ''}`;
  }

  function cancellationPanel(card) {
    const canRetry = actionAllowed(card, 'retry_cancel') && cancelActionControl(card);
    return `<section class="et3-panel"><h3>正在停止专家团</h3><p>停止请求已保存，正在等待运行时确认。刷新不会重复发起任务。</p></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="refresh-run">刷新停止状态</button>${canRetry ? '<button type="button" class="et3-button et3-button--danger" data-et3-action="retry-cancel">重试停止</button>' : ''}</div>`;
  }

  function stageInputPanel(card) {
    const input = card.pendingInput || {};
    const options = list(input.options || input.choices);
    const title = input.title || input.question || '补充阶段信息';
    const detail = input.description || input.detail || '当前阶段需要你补充信息后才能继续。';
    return `<section class="et3-panel"><h3>${esc(title)}</h3><p>${esc(detail)}</p>
      ${options.length ? `<div class="et3-inline-actions">${options.map(option => {
        const value = typeof option === 'object' ? (option.value || option.id || option.label || option.title || '') : option;
        const label = typeof option === 'object' ? (option.label || option.title || option.value || option.id || '') : option;
        return `<button type="button" class="et3-button" data-et3-action="choose-stage-input" data-stage-input-value="${esc(value)}">${esc(label)}</button>`;
      }).join('')}</div>` : ''}
      <label class="et3-form-field"><span>你的补充</span><textarea data-et3-stage-input placeholder="请填写当前阶段需要的信息">${esc(input.answer || '')}</textarea></label>
    </section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="submit-stage-input">提交并继续</button></div>`;
  }

  function resumePanel(card) {
    return `<section class="et3-panel"><h3>任务等待恢复</h3><p>${esc(card.presentation?.detail || card.presentation?.summary || '上一次执行未完整结束，可以从已保存状态继续。')}</p><p class="et3-help">恢复只会继续当前任务，不会新建会话或重复生成已确认成果。</p></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="retry-run">恢复任务</button></div>`;
  }

  function reviewPanel(card) {
    const result = card.stageReview?.output || card.stageResult?.output || card.stageResult || {};
    const content = result.content || card.presentation?.result?.content || '';
    const items = list(card.reviewItems);
    const canRevise = actionAllowed(card, 'stage_revise');
    const canConfirm = actionAllowed(card, 'stage_confirm');
    const blocked = !canRevise || !canConfirm;
    return `<section class="et3-panel"><h3>阶段成果</h3><div class="et3-document" tabindex="-1" data-et3-result-document>${esc(content || '阶段成果已生成，请稍后刷新状态。')}</div><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="view-result">定位到完整成果</button></div></section>
      <section class="et3-panel"><h3>复核建议</h3><ul class="et3-review-list">${items.map(item => `<li class="et3-review-item"><span><strong>${esc(item.title || '待确认事项')}</strong><small>${esc(item.phase || '待人工确认')}</small></span><button type="button" class="et3-button" data-et3-action="append-revision" data-revision-text="${esc(item.title || '')}">加入修改意见</button></li>`).join('') || '<li class="et3-help">未发现阻断问题。仍建议阅读完整成果后确认。</li>'}</ul><label class="et3-form-field"><span>修改意见</span><textarea data-et3-revision placeholder="逐条写清需要修改的位置和目标；无修改可直接进入下一阶段"></textarea></label></section>
      ${blocked ? '<p id="expertTeamV3StageActionHelp" class="et3-help">服务端尚未允许当前操作，请刷新任务状态后重试。</p>' : ''}
      <div class="et3-primary-actions"><button type="button" class="et3-button" data-et3-action="submit-revision" ${canRevise ? '' : 'disabled aria-disabled="true" aria-describedby="expertTeamV3StageActionHelp"'}>提交修改意见</button><button type="button" class="et3-button et3-button--primary" data-et3-action="confirm-stage" ${canConfirm ? '' : 'disabled aria-disabled="true" aria-describedby="expertTeamV3StageActionHelp"'}>无修改，进入下一阶段</button></div>`;
  }

  function documentValidationPanel() {
    return `<section class="et3-panel"><h3>DOCX 自动检查</h3><p>正在核对文档结构、文件完整性和交付绑定。完成后可在本机打开正式文档检查。</p><div class="et3-skeleton"></div><div class="et3-skeleton" style="width:72%"></div></section>`;
  }

  function deliveryConfirmationPanel(card) {
    const artifact = finalDocument(card);
    return `<section class="et3-panel"><h3>最终文档</h3><p>正式 DOCX 已生成。请先在本机打开检查，再确认是否交付。</p><dl class="et3-kv"><dt>文件</dt><dd>${esc(artifact?.title || artifact?.label || '最终交付文档.docx')}</dd><dt>状态</dt><dd>等待本机确认</dd></dl></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="open-docx">打开最终 DOCX</button><button type="button" class="et3-button" data-et3-action="refresh-run">刷新状态</button></div>`;
  }

  function completedPanel(card) {
    const artifacts = list(card.artifacts).filter(item => item && (item.exists !== false));
    return `<section class="et3-panel"><h3>最终交付</h3><p>文档内容、DOCX 自动检查和本机确认已经形成完整交付链。</p><dl class="et3-kv"><dt>交付状态</dt><dd>已完成</dd><dt>确认链</dt><dd>内容确认 · DOCX 自检 · 本机确认</dd></dl><ul class="et3-artifact-list">${artifacts.map(item => `<li class="et3-artifact"><span><strong>${esc(item.title || item.label || (item.kind === 'docx' ? '最终交付文档.docx' : item.kind) || '交付文件')}</strong><small>${esc(item.kind === 'docx' ? 'DOCX · 已确认' : (item.kind || '交付文件'))}</small></span><button type="button" class="et3-button" data-et3-action="open-artifact" data-path="${esc(item.path || '')}" data-kind="${esc(item.kind || '')}" aria-label="打开${esc(item.title || item.label || item.kind || '交付文件')}">打开</button></li>`).join('') || '<li class="et3-help">交付文件入口正在同步，请刷新任务状态。</li>'}</ul></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="open-docx">打开最终 DOCX</button></div>`;
  }

  function legacyPanel(card) {
    return `<section class="et3-panel"><h3>历史任务只读</h3><p>该任务没有新版文档规格、证据绑定和交付验收记录。为避免误写历史数据，当前只提供查看。</p><div class="et3-document" tabindex="-1" data-et3-result-document>${esc(card.presentation?.result?.content || card.presentation?.summary || '暂无可展示的历史成果。')}</div><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="view-result">定位到历史成果</button></div></section>`;
  }

  function failurePanel(card, current) {
    const canRetry = actionAllowed(card, 'resume');
    const canCancel = actionAllowed(card, 'cancel');
    return `<section class="et3-panel"><h3>${esc(stateCopy[current]?.[0] || '任务需要处理')}</h3><p class="et3-error">${esc(card.presentation?.detail || card.presentation?.summary || '当前任务需要恢复或重新发起。')}</p><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="refresh-run">刷新状态</button>${canRetry ? '<button type="button" class="et3-button et3-button--primary" data-et3-action="retry-run">恢复任务</button>' : ''}${canCancel ? '<button type="button" class="et3-button et3-button--danger" data-et3-action="cancel-run">重试停止</button>' : ''}</div></section>`;
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
    const requiredAction = {
      'add-text-source': 'answer', 'choose-source-file': 'answer', 'remove-source': 'answer',
      'save-brief': 'answer', 'confirm-brief': 'answer', 'submit-answers': 'answer',
      'start-generation': 'start_generation', 'submit-stage-input': 'submit_stage_input',
      'retry-run': 'resume', 'cancel-run': 'cancel', 'retry-cancel': 'retry_cancel',
    }[action];
    if (requiredAction && !actionAllowed(state.card, requiredAction)) {
      return setLive('该操作已不适用于服务端最新状态，请刷新后重试。', true);
    }
    if (action === 'append-revision') return appendRevision(button.dataset.revisionText);
    if (action === 'choose-stage-input') {
      const field = workbenchRoot().querySelector('[data-et3-stage-input]');
      if (!field) return false;
      field.value = button.dataset.stageInputValue || '';
      field.focus();
      return true;
    }
    if (action === 'view-result') { const result = workbenchRoot().querySelector('[data-et3-result-document]'); if (result) { result.focus(); result.scrollIntoView({ block: 'start' }); return true; } return setLive('完整成果尚未同步，请刷新状态。', true); }
    if (action === 'open-artifact') return openArtifact(button.dataset.path, button.dataset.kind, button);
    if (action === 'open-docx') return openFinalDocx(button);
    if (action === 'choose-source-file') { workbenchRoot().querySelector('[data-et3-source-file]')?.click(); return true; }
    if (action === 'add-text-source') return addTextSource(button);
    if (action === 'remove-source') {
      if (!window.confirm('移除后该资料不再用于本任务，确定继续吗？')) return false;
      return mutate('/api/expert-teams/brief/sources/remove', { expected_brief_revision: Number(state.card.brief?.revision || 0), source_id: button.dataset.sourceId }, button);
    }
    if (action === 'save-brief') return saveBrief(button, false);
    if (action === 'confirm-brief') return saveBrief(button, true);
    if (action === 'submit-answers') return submitAnswers(button);
    if (action === 'submit-stage-input') return submitStageInput(button);
    if (action === 'start-generation') return mutate('/api/expert-teams/resume', {}, button);
    if (action === 'retry-run') return mutate('/api/expert-teams/resume', {}, button, 'retry');
    if (action === 'cancel-run') return mutate('/api/expert-teams/cancel', {}, button, 'cancel');
    if (action === 'retry-cancel') return retryCancel(button);
    if (action === 'refresh-run') return refreshRun(button);
    if (action === 'submit-revision') return submitRevision(button);
    if (action === 'confirm-stage') return confirmStage(button);
  }

  function handleWorkbenchChange(event) {
    if (event.target.matches('[data-et3-source-file]')) addLocalFile(event.target);
  }

  function mutationControl(kind) {
    const card = state.card || {};
    return {
      session_id: card.sourceSessionId || '', run_id: card.runId || '',
      expected_version: Number(card.version || 0), stage_id: card.currentStageId || '',
      idempotency_key: uid(kind),
    };
  }

  function cancelActionControl(card) {
    const binding = card && card.cancelActionBinding;
    if (!binding || typeof binding !== 'object') return null;
    const control = {
      session_id: String(binding.session_id || '').trim(),
      run_id: String(binding.run_id || '').trim(),
      expected_version: Number(binding.expected_version),
      stage_id: String(binding.stage_id || '').trim(),
      idempotency_key: String(binding.idempotency_key || '').trim(),
    };
    if (
      !control.session_id || !control.run_id || !Number.isInteger(control.expected_version) || control.expected_version < 0 ||
      !control.stage_id || !control.idempotency_key || control.session_id !== String(card.sourceSessionId || '') ||
      control.run_id !== String(card.runId || '') || control.expected_version !== Number(card.version) ||
      control.stage_id !== String(card.currentStageId || '')
    ) return null;
    return control;
  }

  async function retryCancel(button) {
    const control = actionAllowed(state.card, 'retry_cancel') ? cancelActionControl(state.card) : null;
    if (!control) return setLive('停止重试信息不完整，请刷新任务状态后重试。', true);
    setBusy(button, true, '正在重试…');
    try {
      const payload = await window.api('/api/expert-teams/cancel', {
        method: 'POST',
        body: JSON.stringify(control),
      });
      applyResponse(payload);
      setLive('停止请求已重新提交。');
      return true;
    } catch (error) {
      if (error && error.payload && error.payload.run) applyResponse(error.payload);
      setLive(error.message || '停止重试失败，请刷新状态后重试。', true);
      return false;
    } finally { setBusy(button, false); }
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

  function isConflictError(error) {
    return Number(error && error.status || 0) === 409;
  }

  function stageActionControl(kind, action) {
    if (!actionAllowed(state.card, action)) return null;
    if (typeof window.buildExpertTeamStageActionPayload !== 'function') return null;
    const control = window.buildExpertTeamStageActionPayload(state.card, uid(kind));
    const required = ['session_id', 'run_id', 'expected_version', 'stage_id', 'stage_attempt', 'artifact_id', 'artifact_sha256', 'idempotency_key'];
    return control && required.every(key => Object.prototype.hasOwnProperty.call(control, key)) ? control : null;
  }

  async function mutateStage(endpoint, action, extra, button, kind) {
    const control = stageActionControl(kind, action);
    if (!control) return setLive('当前阶段操作信息不完整，请刷新任务状态后重试。', true);
    const conflictDraft = captureConflictRevisionDraft(state.card);
    setBusy(button, true, '处理中…');
    try {
      const payload = await window.api(endpoint, {
        method: 'POST',
        body: JSON.stringify({ ...control, ...(extra || {}) }),
      });
      state.conflictRevisionDraft = null;
      applyResponse(payload);
      setLive('操作已保存。');
      return true;
    } catch (error) {
      if (isConflictError(error) && conflictDraft) state.conflictRevisionDraft = conflictDraft;
      if (error && error.payload && error.payload.run) applyResponse(error.payload);
      if (isConflictError(error)) {
        setLive(conflictDraft ? '状态已更新，修改意见已保留，请核对后重试。' : '状态已更新，请核对最新阶段后重试。', true);
      } else {
        setLive(error.message || '操作失败，请刷新状态后重试。', true);
      }
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
    return mutateStage('/api/expert-teams/stage/revise', 'stage_revise', { feedback }, button, 'stage-revise');
  }

  function confirmStage(button) {
    return mutateStage('/api/expert-teams/stage/confirm', 'stage_confirm', {}, button, 'stage-confirm');
  }

  function submitStageInput(button) {
    const answer = String(workbenchRoot().querySelector('[data-et3-stage-input]')?.value || '').trim();
    if (!answer) return setLive('请先填写当前阶段需要的信息。', true);
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
    if (!state.keyboardBound) {
      document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeDialog();
        if (event.key === 'Tab') trapDialogFocus(event);
      }, { capture: true });
      state.keyboardBound = true;
    }
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
