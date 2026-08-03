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
    conflictDeliveryDraft: null,
    collapsed: false,
    busy: false,
    compositionActive: false,
    deferredCard: null,
    catalogStatus: 'idle',
    catalogError: '',
    suggestionMode: false,
    suggestedPrompt: '',
    suggestedSourceSessionId: '',
  };

  const teamPresentationDefaults = [
    {
      id: 'content-creator-team',
      title: '内容创作专家团',
      category: '办公材料',
      description: '把零散诉求和资料整理为可复核、可交付的工作汇报。',
      image: 'static/assets/taiji/expert-teams/team-content-cover.png',
      image_alt: '内容创作专家团五位专家协作插画',
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
      image: 'static/assets/taiji/expert-teams/team-research-cover.png',
      image_alt: '深度材料研究团六位专家协作插画',
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
    completed: ['文档已交付', '正式 DOCX 已生成，可在本机打开。'],
    contract_error: ['状态暂不可用', '服务端没有返回完整的单机任务状态，请刷新后重试。'],
    failed: ['任务未完成', '查看原因后返回专家团门户重新发起。'],
    cancelled: ['任务已取消', '当前任务已停止，不会继续生成。'],
    cancelling: ['正在停止专家团', '停止请求正在确认，可刷新查看最新状态。'],
    legacy_read_only: ['历史任务（只读）', '该任务使用旧版数据结构，仅保留查看能力。'],
  };

  const EXPERT_TEAM_ASSET_PATTERN = /^static\/assets\/taiji\/expert-teams\/[a-z0-9][a-z0-9._-]*\.png$/i;

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  function reviewInlineHtml(value) {
    return esc(value)
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`\n]+)`/g, '<code>$1</code>');
  }

  function reviewDocumentHtml(value) {
    const lines = String(value == null ? '' : value).replace(/\r\n?/g, '\n').split('\n');
    const html = [];
    let listKind = '';
    const closeList = () => {
      if (!listKind) return;
      html.push(`</${listKind}>`);
      listKind = '';
    };
    const openList = kind => {
      if (listKind === kind) return;
      closeList();
      listKind = kind;
      html.push(`<${kind}>`);
    };
    lines.forEach(rawLine => {
      const line = rawLine.trim();
      if (!line) {
        closeList();
        return;
      }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        closeList();
        const level = Math.min(4, heading[1].length + 1);
        html.push(`<h${level}>${reviewInlineHtml(heading[2])}</h${level}>`);
        return;
      }
      const bullet = line.match(/^[-*+]\s+(.+)$/);
      if (bullet) {
        openList('ul');
        html.push(`<li>${reviewInlineHtml(bullet[1])}</li>`);
        return;
      }
      const ordered = line.match(/^\d+[.)]\s+(.+)$/);
      if (ordered) {
        openList('ol');
        html.push(`<li>${reviewInlineHtml(ordered[1])}</li>`);
        return;
      }
      closeList();
      html.push(`<p>${reviewInlineHtml(line.replace(/^>\s?/, ''))}</p>`);
    });
    closeList();
    return html.join('');
  }

  function list(value) { return Array.isArray(value) ? value : []; }
  function localExpertTeamImage(value, fallback) {
    return [value, fallback]
      .map(candidate => String(candidate || '').trim())
      .find(candidate => EXPERT_TEAM_ASSET_PATTERN.test(candidate)) || '';
  }
  function imageFallbackText(value) {
    return Array.from(String(value || '').trim())[0] || '专';
  }
  function uid(kind) {
    const id = globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function'
      ? globalThis.crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `expert-team-v3:${kind}:${id}`;
  }

  async function requestV3Confirmation(options = {}) {
    const dialog = typeof window.showConfirmDialog === 'function'
      ? window.showConfirmDialog
      : null;
    if (!dialog) {
      setLive('确认弹窗尚未就绪，未执行此操作。', true);
      return false;
    }
    try {
      return Boolean(await dialog({ ...options, focusCancel: true }));
    } catch (_error) {
      setLive('无法打开确认弹窗，未执行此操作。', true);
      return false;
    }
  }

  function classifyDocumentTaskPrompt(value) {
    const prompt = String(value || '').replace(/\s+/g, ' ').trim();
    if (prompt.length < 4 || /^(?:请问[，,\s]*)?(?:怎么|如何|为什么|是否|能否|可否)/.test(prompt)) return null;
    const rules = [
      {
        launchProfileId: 'content-polish',
        label: '材料润色',
        pattern: /(?:润色|改写|修订).{0,24}(?:材料|文档|稿件|文字|报告|方案|正文)/,
      },
      {
        launchProfileId: 'research-report',
        label: '研究报告',
        pattern: /(?:起草|撰写|编写|编制|写(?:一份|一篇|一个)?|生成|形成|制作).{0,24}(?:研究报告|调研报告|专题报告)/,
      },
      {
        launchProfileId: 'content-meeting-minutes',
        label: '会议纪要',
        pattern: /(?:起草|撰写|编写|编制|写(?:一份|一篇)?|生成|形成|整理).{0,24}(?:会议纪要|会议记录)/,
      },
      {
        launchProfileId: 'content-notice',
        label: '通知通报',
        pattern: /(?:起草|撰写|编写|编制|写(?:一份|一篇)?|生成|形成|制作).{0,24}(?:通知|通报)/,
      },
      {
        launchProfileId: 'content-summary-plan',
        label: '总结计划',
        pattern: /(?:起草|撰写|编写|编制|写(?:一份|一篇)?|生成|形成|制作).{0,24}(?:总结计划|工作总结|阶段性总结|总结和下一步计划|总结与下一步计划)/,
      },
      {
        launchProfileId: 'content-work-report',
        label: '工作汇报',
        pattern: /(?:起草|撰写|编写|编制|写(?:一份|一篇)?|生成|形成|制作).{0,24}(?:工作汇报|月度汇报|季度汇报|年度汇报|述职报告)/,
      },
      {
        launchProfileId: 'content-plan',
        label: '方案说明',
        pattern: /(?:起草|撰写|编写|编制|写(?:一份|一篇|一个)?|生成|形成|制作).{0,24}(?:方案说明|实施方案|工作方案|专项方案|方案)/,
      },
    ];
    return rules.find(rule => rule.pattern.test(prompt)) || null;
  }

  function normalizeTeam(team) {
    const fallback = teamPresentationDefaults.find(item => item.id === team.id) || {};
    const examples = list(team.examples).map(example => ({
      ...example,
      available: example.available === true && Boolean(String(example.launch_profile_id || '').trim()),
      disabled_reason: String(example.disabled_reason || '当前任务配置异常，请刷新后重试；若仍存在，请联系管理员。'),
    }));
    const title = team.title || fallback.title || '专家团';
    return {
      ...fallback,
      ...team,
      title,
      description: team.description || fallback.description || '',
      image: localExpertTeamImage(team.image, fallback.image),
      image_alt: String(team.image_alt || fallback.image_alt || `${title}协作插画`).trim(),
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
    const examples = list(team.examples);
    const availableCount = examples.filter(example => example.available === true).length;
    const readiness = `已开放 ${availableCount}/${examples.length}`;
    const reason = examples.map(example => example.disabled_reason).find(Boolean) || '当前没有可发起的任务，请刷新后重试。';
    const cover = localExpertTeamImage(team.image);
    const coverAlt = String(team.image_alt || `${team.title || '专家团'}协作插画`).trim();
    const image = cover
      ? `<img src="${esc(cover)}" alt="${esc(coverAlt)}" loading="lazy" data-et3-image><span class="et3-image-fallback et3-team-image-fallback" data-et3-image-fallback hidden aria-hidden="true">${esc(imageFallbackText(team.title))}</span>`
      : `<span class="et3-image-fallback et3-team-image-fallback" data-et3-image-fallback>${esc(imageFallbackText(team.title))}</span>`;
    return `<button type="button" class="et3-team-card${unavailable ? ' is-disabled' : ''}" data-et3-action="open-team" data-team-id="${esc(team.id)}" aria-label="${unavailable ? esc(`${team.title}暂不可用：${reason}`) : esc(`查看并发起${team.title}`)}" aria-disabled="${String(unavailable)}" ${unavailable ? 'disabled' : ''}>
      ${image}
      <span>
        <small>${esc(team.category || '专业协作')}</small>
        <h2>${esc(team.title)}</h2>
        <p>${esc(team.description)}</p>
        <span class="et3-tags">${list(team.tags).slice(0, 4).map(tag => `<span class="et3-tag">${esc(tag)}</span>`).join('')}</span>
        <span class="et3-card-cta">${unavailable ? esc(reason) : `${esc(readiness)} · 查看并发起 <span aria-hidden="true">→</span>`}</span>
      </span>
    </button>`;
  }

  function bindPortalEvents(root) {
    if (state.portalController) state.portalController.abort();
    state.portalController = new AbortController();
    const signal = state.portalController.signal;
    root.addEventListener('click', event => handlePortalClick(event), { signal });
    root.addEventListener('input', event => handlePortalInput(event), { signal });
    root.addEventListener('error', event => handlePortalImageError(event), { signal, capture: true });
  }

  function handlePortalImageError(event) {
    const image = event && event.target;
    if (!image || typeof image.matches !== 'function' || !image.matches('[data-et3-image]')) return false;
    const fallback = image.nextElementSibling;
    if (!fallback || typeof fallback.matches !== 'function' || !fallback.matches('[data-et3-image-fallback]')) return false;
    image.hidden = true;
    image.setAttribute('aria-hidden', 'true');
    image.removeAttribute('src');
    image.removeAttribute('srcset');
    fallback.hidden = false;
    fallback.removeAttribute('aria-hidden');
    return true;
  }

  function memberRowsHtml(members) {
    return list(members).map(member => {
      const name = String(member.name || member.id || '专家').trim();
      const role = String(member.role || '').trim();
      const source = localExpertTeamImage(member.image);
      const imageAlt = String(member.image_alt || `${name}头像`).trim();
      const avatar = source
        ? `<img class="et3-member-avatar" src="${esc(source)}" alt="${esc(imageAlt)}" loading="lazy" data-et3-image><span class="et3-member-avatar et3-image-fallback" data-et3-image-fallback hidden aria-hidden="true">${esc(imageFallbackText(name))}</span>`
        : `<span class="et3-member-avatar et3-image-fallback" data-et3-image-fallback>${esc(imageFallbackText(name))}</span>`;
      return `<div class="et3-member">${avatar}<span class="et3-member-copy"><strong>${esc(name)}</strong><span>${esc(role)}</span></span></div>`;
    }).join('');
  }

  function exampleTaskRowsHtml(examples, selectedExample) {
    return list(examples).map(example => {
      const unavailable = example.available !== true;
      const summary = example.summary || (example.capability && example.capability.label) || '本机协作';
      const unavailableReason = String(example.disabled_reason || '当前任务配置异常，请刷新后重试；若仍存在，请联系管理员。').trim();
      const selected = Boolean(selectedExample && selectedExample.id === example.id);
      const label = example.label || '文档任务';
      const accessibleLabel = unavailable ? `${label}。${summary} 当前任务配置异常：${unavailableReason}` : `${label}。${summary}`;
      return `<button type="button" class="et3-template${unavailable ? ' is-disabled' : ''}" data-et3-action="select-template" data-example-id="${esc(example.id)}" aria-label="${esc(accessibleLabel)}" aria-pressed="${selected}" aria-disabled="${String(unavailable)}" ${unavailable ? `disabled title="${esc(unavailableReason)}"` : ''}><strong>${esc(label)}</strong>${unavailable ? '<small class="et3-template-status">配置异常</small>' : ''}<span>${esc(summary)}</span>${unavailable ? `<small class="et3-template-unavailable-reason">${esc(unavailableReason)}</small>` : ''}</button>`;
    }).join('');
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
    if (kind === 'close-dialog') {
      if (state.suggestionMode) returnSuggestionToComposer();
      else closeDialog();
    }
    if (kind === 'select-template') selectTemplate(action.dataset.exampleId);
    if (kind === 'summon') summon(action);
    if (kind === 'continue-regular-chat') continueRegularChat();
    if (kind === 'retry-catalog') loadCatalog(true);
  }

  function replacementExample(team, documentType, prompt) {
    const available = list(team?.examples).filter(example => example.available === true);
    const exact = available.find(example => String(example.document_type || '') === String(documentType || ''));
    if (exact) return exact;
    const suggestion = classifyDocumentTaskPrompt(prompt);
    return available.find(example => example.launch_profile_id === suggestion?.launchProfileId)
      || available[0]
      || null;
  }

  async function openTeam(teamId, trigger, options = {}) {
    if (!trigger && typeof switchPanel === 'function') await switchPanel('writing');
    if (state.catalogStatus !== 'ready') await loadCatalog(true);
    const team = state.catalog.find(item => item.id === teamId);
    if (!team) return;
    state.selectedTeam = team;
    state.selectedExample = options.correction
      ? replacementExample(team, options.documentType, options.prompt)
      : list(team.examples).find(example => example.available === true) || null;
    state.suggestionMode = options.correction === true;
    state.suggestedPrompt = state.suggestionMode ? String(options.prompt || '').trim() : '';
    state.suggestedSourceSessionId = state.suggestionMode && window.S?.session
      ? String(window.S.session.session_id || '')
      : '';
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
    const prompt = state.suggestionMode
      ? state.suggestedPrompt
      : ((state.selectedExample && state.selectedExample.prompt) || '');
    const hasAvailableTask = examples.some(example => example.available === true);
    const suggestion = state.suggestionMode
      ? `<aside class="et3-suggestion" role="status"><strong>已识别为“${esc(state.selectedExample?.label || '文档任务')}”</strong><p>请确认任务类型；如识别不准确，可以在下方更换文档任务。系统不会未经确认自动发起。</p></aside>`
      : '';
    const footerActions = state.suggestionMode
      ? `<button type="button" class="et3-button" data-et3-action="close-dialog">返回修改</button><button type="button" class="et3-button" data-et3-action="continue-regular-chat">继续普通对话</button><button type="button" class="et3-button et3-button--primary" data-et3-action="summon" ${hasAvailableTask ? '' : 'disabled aria-disabled="true" title="当前任务配置异常，请刷新后重试"'}>使用专家团</button>`
      : `<button type="button" class="et3-button" data-et3-action="close-dialog">取消</button><button type="button" class="et3-button et3-button--primary" data-et3-action="summon" ${hasAvailableTask ? '' : 'disabled aria-disabled="true" title="当前任务配置异常，请刷新后重试"'}>发起专家团任务</button>`;
    dialog.innerHTML = `
      <header class="et3-dialog-head">
        <div><p class="et3-eyebrow">选择专家团</p><h2 id="expertTeamV3DialogTitle" tabindex="-1">${esc(team.title)}</h2><p class="et3-subtitle">${esc(team.category || '')}</p></div>
        <button type="button" class="et3-icon-button" data-et3-action="close-dialog" aria-label="${state.suggestionMode ? '返回聊天并继续编辑' : '关闭专家团详情'}">×</button>
      </header>
      <div class="et3-dialog-body">
        <div>
          <section class="et3-section"><h3>团队能力</h3><p>${esc(team.description)}</p></section>
          <section class="et3-section"><h3>团队成员</h3><div class="et3-member-list">${memberRowsHtml(team.members) || '<p>专家角色会在任务启动后按阶段加入。</p>'}</div></section>
        </div>
        <div>
          <section class="et3-section">
            ${suggestion}
            <h3>选择文档任务</h3>
            <div class="et3-template-list">${exampleTaskRowsHtml(examples, state.selectedExample)}</div>
            <label class="et3-form-field" for="expertTeamV3Prompt"><span>原始诉求</span><textarea id="expertTeamV3Prompt" rows="6" aria-describedby="expertTeamV3PromptHelp">${esc(prompt)}</textarea></label>
            <p id="expertTeamV3PromptHelp" class="et3-help">发起后先确认完整任务规格，不会直接生成文档。</p>
            <p class="et3-live" data-et3-dialog-live aria-live="polite"></p>
          </section>
        </div>
      </div>
      <footer class="et3-dialog-actions">${footerActions}</footer>`;
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
    if (state.suggestionMode && typeof prompt === 'string') state.suggestedPrompt = prompt;
    renderTeamDialog();
    const field = document.getElementById('expertTeamV3Prompt');
    if (field && typeof prompt === 'string') field.value = state.suggestionMode ? prompt : (state.selectedExample?.prompt || prompt);
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
    state.suggestionMode = false;
    state.suggestedPrompt = '';
    state.suggestedSourceSessionId = '';
    returnFocus?.focus();
    return true;
  }

  async function returnSuggestionToComposer() {
    const prompt = String(document.getElementById('expertTeamV3Prompt')?.value || state.suggestedPrompt || '').trim();
    const composer = document.getElementById('msg');
    if (composer && prompt) composer.value = prompt;
    closeDialog();
    if (typeof switchPanel === 'function') await switchPanel('chat');
    if (typeof autoResize === 'function') autoResize();
    composer?.focus();
    return prompt;
  }

  async function continueRegularChat() {
    const prompt = await returnSuggestionToComposer();
    if (!prompt || typeof window.send !== 'function') return false;
    return window.send({ skipExpertTeamSuggestion: true });
  }

  function clearSuggestionComposerAfterLaunch() {
    const composer = document.getElementById('msg');
    if (composer) composer.value = '';
    if (state.suggestedSourceSessionId && typeof _clearComposerDraft === 'function') {
      _clearComposerDraft(state.suggestedSourceSessionId);
    }
    if (typeof autoResize === 'function') autoResize();
    if (typeof updateSendBtn === 'function') updateSendBtn();
    return true;
  }

  async function suggestFromPrompt(prompt) {
    const suggestion = classifyDocumentTaskPrompt(prompt);
    if (!suggestion) return false;
    const sourceSessionId = typeof S !== 'undefined' && S.session
      ? String(S.session.session_id || '')
      : '';
    if (typeof switchPanel === 'function') await switchPanel('writing');
    if (state.catalogStatus !== 'ready') await loadCatalog(true);
    if (state.catalogStatus !== 'ready' || !portalRoot()) return false;
    const team = state.catalog.find(item => list(item.examples).some(
      example => example.available === true && example.launch_profile_id === suggestion.launchProfileId
    ));
    const example = team && list(team.examples).find(
      item => item.available === true && item.launch_profile_id === suggestion.launchProfileId
    );
    if (!team || !example) return false;
    state.selectedTeam = team;
    state.selectedExample = example;
    state.suggestionMode = true;
    state.suggestedPrompt = String(prompt || '').trim();
    state.suggestedSourceSessionId = sourceSessionId;
    state.dialogReturnFocus = document.getElementById('msg');
    renderTeamDialog();
    return true;
  }

  async function summon(button) {
    const prompt = String(document.getElementById('expertTeamV3Prompt')?.value || '').trim();
    const launchedFromSuggestion = state.suggestionMode;
    const live = portalRoot().querySelector('[data-et3-dialog-live]');
    if (!prompt) { live.textContent = '请先填写本次任务诉求。'; return; }
    if (typeof window.sendExpertTeamAction !== 'function') { live.textContent = '专家团启动服务尚未就绪，请刷新后重试。'; return; }
    const example = state.selectedExample || {};
    if (example.available !== true || !String(example.launch_profile_id || '').trim()) {
      live.textContent = example.disabled_reason || '当前任务配置异常，请刷新后重试。';
      return;
    }
    setBusy(button, true, '正在发起…');
    const payload = {
      launch_profile_id: String(example.launch_profile_id),
      prompt,
    };
    try {
      const started = await window.sendExpertTeamAction(payload);
      if (started) {
        if (launchedFromSuggestion) clearSuggestionComposerAfterLaunch();
        closeDialog();
      }
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
    const progress = card.progress || {};
    const total = Math.max(0, Number(progress.total || 0));
    const done = Math.max(0, Math.min(total, Number(progress.done || 0)));
    const current = effectiveState(card);
    const stageName = String(
      progress.current || card.workflow?.progress?.current || card.workflow?.currentStage?.title || card.phase || '阶段信息同步中'
    ).trim();
    if (!total) return `<p class="et3-progress-label et3-help" role="status">阶段进度待同步 · ${esc(stageName)}</p>`;
    const intake = progress.isIntake === true || current === 'intake' || (current === 'ready' && done === 0);
    const currentIndex = Number(progress.currentIndex);
    const terminal = ['generating_document', 'awaiting_delivery_confirmation', 'completed'].includes(current);
    const step = intake ? 0 : (terminal
      ? total
      : Math.min(total, Math.max(1, Number.isInteger(currentIndex) ? currentIndex + 1 : done + 1)));
    const text = intake ? `准备阶段 · ${stageName}` : `第 ${step}/${total} 步 · ${stageName}`;
    return `<div class="et3-progress-group"><p class="et3-progress-label et3-help">${esc(text)}</p><div class="et3-progress" role="progressbar" aria-label="${esc(text)}" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${step}">${Array.from({ length: total }, (_, index) => `<span class="${index < step - 1 || terminal ? 'is-done' : index === step - 1 ? 'is-current' : ''}"${index === step - 1 ? ' aria-current="step"' : ''}><span class="et3-visually-hidden">第 ${index + 1} 步</span></span>`).join('')}</div></div>`;
  }

  function effectiveState(card) {
    if (card.readOnly || card.productMode !== 'standalone') return 'legacy_read_only';
    return String(card.publicState || 'contract_error');
  }

  function actionAllowed(card, action) {
    return card?.productMode === 'standalone' && list(card.allowedActions).includes(action);
  }

  function stateCopyFor(card, current) {
    if (actionAllowed(card, 'delivery_recover')) {
      return ['交付文档已变化', '已交付的 DOCX 与确认时不一致，原本机确认已失效。'];
    }
    if (current === 'ready' && actionAllowed(card, 'submit_stage_input')) {
      return ['需要你的补充', '专家团在继续当前阶段前，需要你确认一项信息。'];
    }
    if (current === 'ready' && card.workflowState === 'generated_invalid' && actionAllowed(card, 'resume')) {
      return [
        card.presentation?.title || '生成格式需要重新处理',
        card.presentation?.detail || '本次生成结果格式不完整，系统没有采用这份内容。请重新生成当前阶段。',
      ];
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
    if (surface === 'awaiting_delivery_confirmation') {
      const bindingFingerprint = deliveryBindingFingerprint(card);
      return bindingFingerprint ? JSON.stringify([card.runId, surface, bindingFingerprint]) : '';
    }
    if (surface === 'intake') return [card.runId, surface, list(card.questions).map(item => item.id).join(',')].join(':');
    if (surface === 'ready' && actionAllowed(card, 'submit_stage_input')) return [card.runId, surface, 'submit_stage_input', card.pendingInputId].join(':');
    return [card.runId, surface].join(':');
  }

  function captureWorkbenchDraft(root, card) {
    if (!root || !card) return null;
    const controls = Array.from(root.querySelectorAll('input:not([type="file"]), textarea, select'));
    const openDisclosures = Array.from(root.querySelectorAll('details[data-et3-disclosure]'))
      .filter(item => item.open && item.dataset?.et3Disclosure)
      .map(item => item.dataset.et3Disclosure);
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
      openDisclosures,
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
    const openDisclosures = new Set(Array.isArray(draft.openDisclosures) ? draft.openDisclosures : []);
    Array.from(root.querySelectorAll('details[data-et3-disclosure]')).forEach(item => {
      const disclosure = item.dataset?.et3Disclosure;
      if (disclosure) item.open = openDisclosures.has(disclosure);
    });
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

  function deliveryBindingFingerprint(card) {
    const binding = card?.deliveryActionBinding || {};
    const fields = [
      'session_id', 'run_id', 'expected_version', 'stage_id', 'stage_attempt',
      'artifact_id', 'artifact_sha256', 'delivery_attempt', 'delivery_binding_sha256', 'document_sha256',
    ];
    const values = fields.map(field => String(binding[field] ?? ''));
    if (values.some(value => !value)) return '';
    return JSON.stringify(values);
  }

  function deliveryRecoveryBindingFingerprint(card) {
    const binding = card?.deliveryRecoveryBinding || {};
    const fields = [
      'session_id', 'run_id', 'expected_version', 'stage_id', 'stage_attempt',
      'artifact_id', 'artifact_sha256', 'delivery_attempt', 'delivery_binding_sha256', 'document_sha256',
    ];
    const values = fields.map(field => String(binding[field] ?? ''));
    if (values.some(value => !value)) return '';
    return JSON.stringify(values);
  }

  function captureConflictDeliveryDraft(card) {
    const value = String(workbenchRoot()?.querySelector('[data-et3-delivery-revision]')?.value || '');
    const deliveryFingerprint = deliveryBindingFingerprint(card);
    if (!value.trim() || !card?.runId || !deliveryFingerprint) return null;
    return { runId: card.runId, deliveryFingerprint, value };
  }

  function conflictDeliveryDraftMatches(card, draft) {
    return Boolean(
      draft
      && draft.runId === card?.runId
      && draft.deliveryFingerprint
      && draft.deliveryFingerprint === deliveryBindingFingerprint(card)
    );
  }

  function restoreConflictDeliveryDraft(root, card) {
    const draft = state.conflictDeliveryDraft;
    const field = root?.querySelector('[data-et3-delivery-revision]');
    if (!conflictDeliveryDraftMatches(card, draft) || !field) return false;
    if (!String(field.value || '').trim()) field.value = draft.value;
    return true;
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

  function staleConflictDeliveryHtml(card) {
    const draft = state.conflictDeliveryDraft;
    if (!draft || draft.runId !== card?.runId || conflictDeliveryDraftMatches(card, draft)) return '';
    return `<section class="et3-panel et3-stale-draft" role="status"><h3>上一份交付修改意见已保留</h3><p>交付文档或校验摘要已变更，为避免误提交，以下内容未自动带入新文档。如仍适用，请手动复制并重新核对。</p><textarea readonly data-et3-stale-delivery-revision aria-label="上一份交付修改意见">${esc(draft.value)}</textarea></section>`;
  }

  function deferStatusRenderDuringComposition(card) {
    if (!state.compositionActive || !state.card) return false;
    if (
      String(card?.runId || '') !== String(state.card.runId || '')
      || String(card?.sourceSessionId || '') !== String(state.card.sourceSessionId || '')
    ) return false;
    const deferredVersion = Number(state.deferredCard?.version || 0);
    if (!state.deferredCard || Number(card?.version || 0) >= deferredVersion) {
      state.deferredCard = card;
    }
    return true;
  }

  function releaseDeferredStatusCard() {
    const deferred = state.deferredCard;
    state.deferredCard = null;
    return deferred;
  }

  function renderStatusSurface(card) {
    if (!card || card.kind !== 'expert_team') return clearStatusSurface();
    const activeSession = window.S && window.S.session && window.S.session.session_id;
    if (card.sourceSessionId && activeSession && card.sourceSessionId !== activeSession) return clearStatusSurface();
    if (deferStatusRenderDuringComposition(card)) return true;
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
    const staleDeliveryDraft = captureConflictDeliveryDraft(previousCard);
    if (staleRevisionDraft && !conflictDraftMatches(card, staleRevisionDraft)) {
      state.conflictRevisionDraft = staleRevisionDraft;
    }
    if (staleDeliveryDraft && !conflictDeliveryDraftMatches(card, staleDeliveryDraft)) {
      state.conflictDeliveryDraft = staleDeliveryDraft;
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
    restoreConflictDeliveryDraft(root, card);
    state.draft = null;
    return true;
  }

  function clearStatusSurface() {
    if (state.workbenchController) state.workbenchController.abort();
    workbenchRoot()?.remove();
    document.body.classList.remove('expert-team-v3-active', 'expert-team-v3-collapsed');
    state.card = null;
    state.draft = null;
    state.conflictRevisionDraft = null;
    state.conflictDeliveryDraft = null;
    state.collapsed = false;
    state.compositionActive = false;
    state.deferredCard = null;
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
        ${staleConflictDeliveryHtml(card)}
        ${statePanel(card, current)}
        <p id="expertTeamV3Live" class="et3-live" data-et3-live aria-live="polite"></p>
      </div>
    </div><button type="button" class="et3-workbench-restore" data-et3-action="restore-workbench" aria-label="展开专家团工作台">专家团</button>`;
  }

  function statePanel(card, current) {
    if (current === 'legacy_read_only') return legacyPanel(card);
    if (current === 'intake') return briefPanel(card);
    if (card.productError?.schema === 'taiji.product.error.v1') return failurePanel(card, current);
    if (current === 'ready' && actionAllowed(card, 'submit_stage_input')) return stageInputPanel(card);
    if (current === 'ready' && actionAllowed(card, 'resume')) return resumePanel(card);
    if (current === 'ready') return readyPanel(card);
    if (current === 'executing' || current === 'revising') return generatingPanel(card, current);
    if (current === 'cancelling') return cancellationPanel(card);
    if (current === 'awaiting_stage_confirmation') return reviewPanel(card);
    if (current === 'generating_document') return documentValidationPanel(card);
    if (actionAllowed(card, 'delivery_recover')) return deliveryRecoveryPanel(card);
    if (current === 'awaiting_delivery_confirmation') return deliveryConfirmationPanel(card);
    if (current === 'completed') return completedPanel(card);
    return failurePanel(card, current);
  }

  function briefFieldSchema(brief) {
    const configured = list(brief?.fieldSchema).filter(field => field && field.path);
    if (configured.length) return configured;
    return [
      { path: 'exact_title', label: '文档标题', control: 'text', required: true, placeholder: '', help: '', value: brief?.exactTitle || '' },
      { path: 'purpose', label: '文档用途', control: 'textarea', required: true, placeholder: '', help: '', value: brief?.purpose || '' },
      { path: 'audience', label: '阅读对象', control: 'text', required: true, placeholder: '', help: '', value: brief?.audience || '' },
      { path: 'usage_scenario', label: '使用场景', control: 'text', required: true, placeholder: '', help: '', value: brief?.usageScenario || '' },
    ];
  }

  function briefFieldDomId(path) {
    return `et3-brief-${String(path || '').replace(/[^a-z0-9_-]+/gi, '-')}`;
  }

  function fieldErrorsFor(brief, path) {
    return list(brief?.fieldErrors).filter(error => String(error?.field || '') === path);
  }

  function briefFieldHtml(field, brief) {
    const path = String(field.path || '');
    const id = briefFieldDomId(path);
    const helpId = `${id}-help`;
    const errorId = `${id}-error`;
    const errors = fieldErrorsFor(brief, path);
    const errorMessage = errors.map(error => error.message).filter(Boolean).join('；');
    const describedBy = [field.help ? helpId : '', errorId].filter(Boolean).join(' ');
    const attributes = `id="${esc(id)}" name="${esc(path)}" data-et3-brief-path="${esc(path)}" value="${esc(field.value || '')}" placeholder="${esc(field.placeholder || '')}" ${field.required ? 'required aria-required="true"' : ''} aria-describedby="${esc(describedBy)}" ${errorMessage ? 'aria-invalid="true"' : ''}`;
    const control = field.control === 'textarea'
      ? `<textarea ${attributes.replace(` value="${esc(field.value || '')}"`, '')}>${esc(field.value || '')}</textarea>`
      : `<input type="${field.control === 'date' ? 'date' : 'text'}" ${attributes}>`;
    return `<label class="et3-form-field et3-brief-field" for="${esc(id)}"><span>${esc(field.label || path)}${field.required ? '<b aria-hidden="true"> *</b>' : ''}</span>${control}${field.help ? `<small id="${esc(helpId)}" class="et3-help">${esc(field.help)}</small>` : ''}<small id="${esc(errorId)}" class="et3-field-error" data-et3-field-error-for="${esc(path)}"${errorMessage ? '' : ' hidden'}>${esc(errorMessage)}</small></label>`;
  }

  function requiredSectionsHtml(brief) {
    const sections = list(brief?.requiredSections).map(section => String(section || '').trim()).filter(Boolean);
    if (!sections.length) return '';
    return `<section class="et3-required-sections" aria-labelledby="expertTeamV3RequiredSectionsTitle"><h4 id="expertTeamV3RequiredSectionsTitle">必备章节</h4><p>以下结构由任务类型确定；阶段成果和 DOCX 自动检查都必须完整保留。</p><ol>${sections.map(section => `<li>${esc(section)}</li>`).join('')}</ol></section>`;
  }

  function briefPanel(card) {
    const brief = card.brief || {};
    const fields = briefFieldSchema(brief);
    const questions = list(card.questions).filter(question => !['answered', 'skipped'].includes(question.status));
    const canAnswer = actionAllowed(card, 'answer');
    const disabled = canAnswer ? '' : 'disabled aria-disabled="true" aria-describedby="expertTeamV3IntakeActionHelp"';
    return `<section class="et3-panel"><h3>任务规格</h3>
      <dl class="et3-kv"><dt>原始诉求</dt><dd>${esc(brief.originalRequest || brief.originalRequestSummary || '')}</dd><dt>文档类型</dt><dd>${esc(brief.documentTypeLabel || brief.documentType || '')}</dd></dl>
      ${requiredSectionsHtml(brief)}
      <form data-et3-brief-form>
        ${questions.map(question => `<div class="et3-question"><label for="et3-question-${esc(question.id)}">${esc(question.title)}</label><textarea id="et3-question-${esc(question.id)}" name="question__${esc(question.id)}" ${question.required ? 'required' : ''} placeholder="${esc(question.placeholder || '')}">${esc(question.answer || '')}</textarea></div>`).join('')}
        ${fields.map(field => briefFieldHtml(field, brief)).join('')}
      </form>
    </section>
    ${canAnswer ? '' : '<p id="expertTeamV3IntakeActionHelp" class="et3-help">任务规格已被其他操作更新，请刷新状态后继续。</p>'}
    <div class="et3-primary-actions"><button type="button" class="et3-button" data-et3-action="save-brief" ${disabled}>保存规格</button><button type="button" class="et3-button et3-button--primary" data-et3-action="${questions.length ? 'submit-answers' : 'confirm-brief'}" ${disabled}>${questions.length ? '保存回答' : '确认规格并继续'}</button></div>`;
  }

  function readyPanel(card) {
    const brief = card.brief || {};
    const canStart = actionAllowed(card, 'start_generation');
    return `<section class="et3-panel"><h3>生成前确认</h3><dl class="et3-kv"><dt>标题</dt><dd>${esc(brief.exactTitle || card.subtitle)}</dd><dt>对象</dt><dd>${esc(brief.audience || '以已确认规格为准')}</dd></dl>${requiredSectionsHtml(brief)}<p>开始后规格将冻结。每个阶段完成后都需要人工确认，不会自动越过复核。</p></section>${canStart ? '<div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="start-generation">开始生成</button></div>' : '<p class="et3-help">当前状态尚不允许开始生成，请刷新后重试。</p>'}`;
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
    if (card.workflowState === 'generated_invalid') {
      if (card.currentStageId === 'delivery') {
        return `<section class="et3-panel"><h3>重新生成最终 DOCX</h3><p>${esc(card.presentation?.detail || '最终文档生成未完成，已确认的正文仍然保留。')}</p><p class="et3-help">本次只重新生成并检查 DOCX，不会重新调用模型，也不会重做已确认的内容阶段。</p></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="retry-run">重新生成最终 DOCX</button></div>`;
      }
      return `<section class="et3-panel"><h3>重新生成当前阶段</h3><p>${esc(card.presentation?.detail || '本次生成结果格式不完整，系统没有采用这份内容。')}</p><p class="et3-help">重新生成只会重试当前阶段，不会新建会话，也不会采用上一次的无效内容。</p></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="retry-run">重新生成当前阶段</button></div>`;
    }
    return `<section class="et3-panel"><h3>任务等待恢复</h3><p>${esc(card.presentation?.detail || card.presentation?.summary || '上一次执行未完整结束，可以从已保存状态继续。')}</p><p class="et3-help">恢复只会继续当前任务，不会新建会话或重复生成已确认成果。</p></section><div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="retry-run">恢复任务</button></div>`;
  }

  function stageResultPresentation(card) {
    const result = card.stageReview?.output || card.stageResult?.output || card.stageResult || {};
    const content = result.content || result.deliverable || result.summary
      || card.presentation?.result?.content || card.presentation?.result?.deliverable
      || card.presentation?.summary || '';
    const stageQuality = card.stageResult?.stage_quality || card.stageResult?.stageQuality
      || result.stage_quality || result.stageQuality || {};
    const qualityIssues = list(stageQuality.issues).map(issue => ({
      title: issue.message || '存在待确认事项',
      phase: issue.suggested_action || issue.suggestedAction || '请人工核对',
    }));
    return { result, content, stageQuality, qualityIssues };
  }

  function reviewPanel(card) {
    const { content, stageQuality, qualityIssues } = stageResultPresentation(card);
    const items = [...qualityIssues, ...list(card.reviewItems)];
    const blockedQuality = stageQuality.state === 'blocked'
      || Number(stageQuality.blocking_count || stageQuality.blockingCount || 0) > 0;
    const attention = stageQuality.state === 'attention' && Number(stageQuality.warning_count || stageQuality.warningCount || 0) > 0;
    const qualityPanel = blockedQuality
      ? '<section class="et3-panel et3-panel--blocked" role="alert"><h3>当前成果存在阻断问题</h3><p>系统已保留当前成果，但在问题处理完成前不能进入下一阶段。请查看复核建议，提交修改意见后重新生成当前阶段。</p></section>'
      : attention
        ? '<section class="et3-panel et3-panel--warning" role="status"><h3>可继续，但有待确认事项</h3><p>当前结果已通过结构校验，可以继续复核；请在确认进入下一阶段前核对以下事项。</p></section>'
        : '';
    const bindingReady = Boolean(stageBindingFingerprint(card));
    const canRevise = bindingReady && actionAllowed(card, 'stage_revise');
    const canConfirm = bindingReady && actionAllowed(card, 'stage_confirm');
    const canRecheck = bindingReady && actionAllowed(card, 'stage_recheck');
    const actionsUnavailable = !canRevise || (!canConfirm && !canRecheck);
    const actionHelp = canRecheck
      ? '<p id="expertTeamV3StageActionHelp" class="et3-help">已生成的正文保持不变；重新检查只会按最新规则复核当前结果，不会重复调用模型。</p>'
      : !actionsUnavailable
      ? ''
      : blockedQuality && bindingReady && canRevise && !canConfirm
        ? '<p id="expertTeamV3StageActionHelp" class="et3-help">当前成果未通过业务校验，“进入下一阶段”已停用。请先提交修改意见并重新生成当前阶段。</p>'
        : '<p id="expertTeamV3StageActionHelp" class="et3-help">服务端尚未允许当前操作，请刷新任务状态后重试。</p>';
    return `${qualityPanel}<section class="et3-panel"><h3>阶段成果</h3><div class="et3-document et3-document--rendered" tabindex="-1" data-et3-result-document>${reviewDocumentHtml(content || '阶段成果已生成，请稍后刷新状态。')}</div><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="view-result">定位到完整成果</button></div></section>
      <section class="et3-panel"><h3>复核建议</h3><ul class="et3-review-list">${items.map(item => `<li class="et3-review-item"><span><strong>${esc(item.title || '待确认事项')}</strong><small>${esc(item.phase || '待人工确认')}</small></span><button type="button" class="et3-button" data-et3-action="append-revision" data-revision-text="${esc(item.title || '')}">加入修改意见</button></li>`).join('') || '<li class="et3-help">未发现阻断问题。仍建议阅读完整成果后确认。</li>'}</ul><label class="et3-form-field"><span>修改意见</span><textarea data-et3-revision aria-describedby="expertTeamV3Live" placeholder="逐条写清需要修改的位置和目标；无修改可直接进入下一阶段"></textarea></label></section>
      ${actionHelp}
      <div class="et3-primary-actions"><button type="button" class="et3-button" data-et3-action="submit-revision" ${canRevise ? '' : 'disabled aria-disabled="true" aria-describedby="expertTeamV3StageActionHelp"'}>提交修改意见</button>${canRecheck ? '<button type="button" class="et3-button et3-button--primary" data-et3-action="recheck-stage" aria-describedby="expertTeamV3StageActionHelp">重新检查当前结果</button>' : `<button type="button" class="et3-button et3-button--primary" data-et3-action="confirm-stage" ${canConfirm ? '' : 'disabled aria-disabled="true" aria-describedby="expertTeamV3StageActionHelp"'}>无修改，进入下一阶段</button>`}</div>`;
  }

  function documentValidationPanel() {
    return `<section class="et3-panel"><h3>DOCX 自动检查</h3><p>正在核对文档结构、文件完整性和交付绑定。完成后可在本机打开正式文档检查。</p><div class="et3-skeleton"></div><div class="et3-skeleton" style="width:72%"></div></section>`;
  }

  function deliveryRecoveryPanel(card) {
    const bindingReady = Boolean(deliveryRecoveryBindingFingerprint(card));
    const canRecover = bindingReady && actionAllowed(card, 'delivery_recover');
    const documentName = `${String(card.brief?.exactTitle || card.subtitle || '最终交付文档').trim()}.docx`;
    return `<section class="et3-panel et3-panel--warning" role="alert"><h3>交付文档已变化</h3><p>已交付的 DOCX 可能被修改、替换或删除，已不再符合确认时的文件摘要。</p><dl class="et3-kv"><dt>原文件</dt><dd>${esc(documentName)}</dd><dt>确认状态</dt><dd>原本机确认已失效</dd><dt>处理方式</dt><dd>保留已确认内容，重新生成并检查 DOCX</dd></dl><p class="et3-help">不会重做已经确认的内容阶段，也不会读取客户端传入的任意路径。</p></section>
      ${canRecover ? '<div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="delivery-recover">重新生成 DOCX</button></div>' : '<p class="et3-help">恢复操作信息不完整，请刷新任务状态后重试。</p>'}`;
  }

  function qualityReportDetails(card) {
    const delivery = card.standaloneDelivery || {};
    const checks = delivery.automaticCheckSummary || {};
    const passed = Number(checks.passedCount || 0);
    const failed = Number(checks.failedCount || 0);
    const warnings = Number(checks.warningCount || 0);
    const blocking = Number(checks.blockingCount || 0);
    const overall = failed > 0 || blocking > 0
      ? '未通过'
      : warnings > 0
        ? '通过，存在待确认提示'
        : checks.status === 'passed'
          ? '通过'
          : '等待检查结果';
    const localConfirmation = effectiveState(card) === 'completed'
      ? '已完成本机确认'
      : '等待本机确认';
    return `<details class="et3-quality-report" data-et3-disclosure="quality-report"><summary class="et3-button">查看质量报告</summary><section class="et3-quality-report-body" aria-labelledby="expertTeamV3QualityReportTitle"><h4 id="expertTeamV3QualityReportTitle">文档质量报告</h4><dl class="et3-kv"><dt>总体结果</dt><dd>${esc(overall)}</dd><dt>自动检查</dt><dd>${passed} 项通过 · ${failed} 项失败 · ${warnings} 项提示 · ${blocking} 项阻断</dd><dt>交付绑定</dt><dd>已校验当前文档与任务成果</dd><dt>本机检查</dt><dd>${esc(localConfirmation)}</dd></dl><p class="et3-help">底层校验明细已随交付证据保留，普通使用无需打开内部数据文件。</p></section></details>`;
  }

  function canOpenDeliveryNatively() {
    return Boolean(
      document.documentElement?.dataset?.taijiDesktop === '1'
      || (window.taijiDesktop && typeof window.taijiDesktop.pickDirectory === 'function')
    );
  }

  function deliveryDocumentActionLabel() {
    return canOpenDeliveryNatively() ? '打开最终 DOCX' : '下载最终 DOCX';
  }

  function deliverySaveCopyActionLabel() {
    return canOpenDeliveryNatively() ? '保存副本' : '下载副本';
  }

  function deliveryConfirmationPanel(card) {
    const delivery = card.standaloneDelivery || {};
    const checks = delivery.automaticCheckSummary || {};
    const bindingReady = Boolean(deliveryBindingFingerprint(card));
    const canOpenDocument = bindingReady && actionAllowed(card, 'delivery_open_document');
    const canSaveCopy = bindingReady && actionAllowed(card, 'delivery_save_copy');
    const canOpenFolder = canOpenDeliveryNatively()
      && bindingReady
      && actionAllowed(card, 'delivery_open_folder');
    const canOpenQualityReport = bindingReady && actionAllowed(card, 'delivery_open_quality_report');
    const canRerender = bindingReady && actionAllowed(card, 'delivery_rerender');
    const canRevise = bindingReady && actionAllowed(card, 'delivery_revise');
    const canConfirm = bindingReady && actionAllowed(card, 'delivery_confirm');
    const openActions = [
      canOpenDocument ? `<button type="button" class="et3-button et3-button--primary" data-et3-action="delivery-open-document">${deliveryDocumentActionLabel()}</button>` : '',
      canSaveCopy ? `<button type="button" class="et3-button" data-et3-action="delivery-save-copy">${deliverySaveCopyActionLabel()}</button>` : '',
      canOpenFolder ? '<button type="button" class="et3-button" data-et3-action="delivery-open-folder">打开文件夹</button>' : '',
    ].filter(Boolean).join('');
    return `<section class="et3-panel"><h3>最终文档</h3><p>正式 DOCX 已生成。请先在本机打开检查，再确认是否可交付。</p><dl class="et3-kv"><dt>文件</dt><dd>${esc(delivery.documentName || '最终交付文档.docx')}</dd><dt>自动检查</dt><dd>${checks.status === 'passed' ? `自动检查通过 ${Number(checks.passedCount || 0)} 项` : '自动检查状态待同步'}</dd><dt>状态</dt><dd>等待本机确认</dd></dl>${openActions ? `<div class="et3-inline-actions">${openActions}</div>` : ''}${canOpenQualityReport ? qualityReportDetails(card) : ''}</section>
      ${canRerender ? '<section class="et3-panel"><h3>仅文件排版或兼容性有问题？</h3><p class="et3-help">保留已确认正文，只重新生成并检查 DOCX；不会重新调用模型。</p><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="delivery-rerender">仅重新生成 DOCX</button></div></section>' : ''}
      ${canRevise ? `<section class="et3-panel"><h3>发现问题？</h3><label class="et3-form-field" for="expertTeamV3DeliveryRevision"><span>修改意见</span><textarea id="expertTeamV3DeliveryRevision" data-et3-delivery-revision aria-describedby="expertTeamV3DeliveryRevisionHelp expertTeamV3Live" placeholder="说明需要修改的位置、内容和目标"></textarea></label><p id="expertTeamV3DeliveryRevisionHelp" class="et3-help">退回后当前交付文档将失效，专家团会按意见重新生成。</p><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="submit-delivery-revision">退回修改并重新生成</button></div></section>` : ''}
      ${bindingReady ? '' : '<p id="expertTeamV3DeliveryActionHelp" class="et3-help">交付操作信息不完整，为避免打开或确认错误文档，当前操作已停用。请重新进入任务或刷新会话状态。</p>'}
      ${canConfirm ? '<div class="et3-primary-actions"><button type="button" class="et3-button et3-button--primary" data-et3-action="delivery-confirm">确认文档可交付</button></div>' : ''}`;
  }

  function completedPanel(card) {
    const delivery = card.standaloneDelivery || {};
    const bindingReady = Boolean(deliveryBindingFingerprint(card));
    const canOpenDocument = bindingReady && actionAllowed(card, 'delivery_open_document');
    const canSaveCopy = bindingReady && actionAllowed(card, 'delivery_save_copy');
    const canOpenFolder = canOpenDeliveryNatively()
      && bindingReady
      && actionAllowed(card, 'delivery_open_folder');
    const canOpenQualityReport = bindingReady && actionAllowed(card, 'delivery_open_quality_report');
    return `<section class="et3-panel"><h3>最终交付</h3><p>文档内容、DOCX 自动检查和本机确认已经形成完整交付链。</p><dl class="et3-kv"><dt>文件</dt><dd>${esc(delivery.documentName || '最终交付文档.docx')}</dd><dt>交付状态</dt><dd>已完成</dd><dt>确认链</dt><dd>内容确认 · DOCX 自检 · 本机确认</dd></dl><div class="et3-inline-actions">${canOpenDocument ? `<button type="button" class="et3-button et3-button--primary" data-et3-action="delivery-open-document">${deliveryDocumentActionLabel()}</button>` : ''}${canSaveCopy ? `<button type="button" class="et3-button" data-et3-action="delivery-save-copy">${deliverySaveCopyActionLabel()}</button>` : ''}${canOpenFolder ? '<button type="button" class="et3-button" data-et3-action="delivery-open-folder">打开文件夹</button>' : ''}</div>${canOpenQualityReport ? qualityReportDetails(card) : ''}</section>${bindingReady ? '' : '<p class="et3-help">交付文件入口已失效，请刷新会话状态后再试。</p>'}`;
  }

  function legacyPanel(card) {
    return `<section class="et3-panel"><h3>历史任务只读</h3><p>该任务没有新版文档规格、证据绑定和交付验收记录。为避免误写历史数据，当前只提供查看。</p><div class="et3-document" tabindex="-1" data-et3-result-document>${esc(card.presentation?.result?.content || card.presentation?.summary || '暂无可展示的历史成果。')}</div><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="view-result">定位到历史成果</button></div></section>`;
  }

  function expertTeamDiagnosticText(card) {
    const diagnostics = card?.diagnostics || {};
    const productError = card?.productError || {};
    const rows = [
      ['commit', diagnostics.commit || 'unknown'],
      ['source_mode', diagnostics.sourceMode || 'unknown'],
      ['run_id', diagnostics.runId || card?.runId || ''],
      ['stage_id', diagnostics.stageId || card?.currentStageId || ''],
      ['stage_attempt', Number(diagnostics.stageAttempt || 0)],
      ['error_code', diagnostics.errorCode || productError.code || ''],
      ['incident_id', diagnostics.incidentId || productError.incidentId || ''],
      ['blocking_count', Number(diagnostics.blockingCount || 0)],
      ['warning_count', Number(diagnostics.warningCount || 0)],
      ['provider_error_category', diagnostics.providerErrorCategory || ''],
      ['delivery_state', diagnostics.deliveryState || card?.deliveryStatus || ''],
    ];
    return rows.map(([key, value]) => `${key}: ${String(value)}`).join('\n');
  }

  function failurePanel(card, current) {
    const canRetry = actionAllowed(card, 'resume');
    const canCancel = actionAllowed(card, 'cancel');
    const productError = card.productError;
    if (productError?.schema === 'taiji.product.error.v1') {
      const actionIds = new Set(list(productError.recoveryActions).map(action => String(action?.id || '')));
      const retryLabel = card.currentStageId === 'delivery' && card.workflowState === 'generated_invalid'
        ? '重新生成最终 DOCX'
        : actionIds.has('regenerate') ? '重新生成当前阶段' : actionIds.has('open_model_settings') ? '配置完成后恢复任务' : '重试当前阶段';
      const startNewLabel = productError.code === 'expert_team_evidence_required'
        ? '重新发起并补充资料'
        : '重新发起任务';
      const incident = String(productError.incidentId || card?.diagnostics?.incidentId || '');
      const actionButtons = [
        actionIds.has('open_model_settings') ? '<button type="button" class="et3-button et3-button--primary" data-et3-action="open-model-settings">打开模型配置</button>' : '',
        actionIds.has('start_new') ? `<button type="button" class="et3-button et3-button--primary" data-et3-action="start-new-task">${esc(startNewLabel)}</button>` : '',
        actionIds.has('open_result') ? '<button type="button" class="et3-button" data-et3-action="view-result">查看已保留结果</button>' : '',
        actionIds.has('refresh') ? '<button type="button" class="et3-button" data-et3-action="refresh-run">刷新任务状态</button>' : '',
        canRetry && (actionIds.has('retry') || actionIds.has('regenerate') || actionIds.has('open_model_settings')) ? `<button type="button" class="et3-button" data-et3-action="retry-run">${esc(retryLabel)}</button>` : '',
        '<button type="button" class="et3-button" data-et3-action="copy-diagnostics">复制诊断信息</button>',
        actionIds.has('export_diagnostics') ? '<button type="button" class="et3-button" data-et3-action="export-diagnostics">导出完整诊断</button>' : '',
      ].filter(Boolean).join('');
      const preserved = preservedStageResultPanel(card);
      return `<section class="et3-panel" role="alert"><h3>${esc(productError.title || '操作未能完成')}</h3><p class="et3-error">${esc(productError.message || '请按提示处理后重试。')}</p>${incident ? `<p class="et3-help">诊断编号：<code>${esc(incident)}</code></p>` : ''}<div class="et3-inline-actions">${actionButtons}</div></section>${preserved}`;
    }
    return `<section class="et3-panel"><h3>${esc(stateCopy[current]?.[0] || '任务需要处理')}</h3><p class="et3-error">${esc(card.presentation?.detail || card.presentation?.summary || '当前任务需要恢复或重新发起。')}</p><div class="et3-inline-actions"><button type="button" class="et3-button" data-et3-action="refresh-run">刷新状态</button>${canRetry ? '<button type="button" class="et3-button et3-button--primary" data-et3-action="retry-run">恢复任务</button>' : ''}${canCancel ? '<button type="button" class="et3-button et3-button--danger" data-et3-action="cancel-run">重试停止</button>' : ''}</div></section>`;
  }

  function preservedStageResultPanel(card) {
    const { content, qualityIssues } = stageResultPresentation(card);
    if (!String(content || '').trim() && !qualityIssues.length) return '';
    const document = String(content || '').trim()
      ? `<div class="et3-document et3-document--rendered" tabindex="-1" data-et3-result-document>${reviewDocumentHtml(content)}</div>`
      : '<p class="et3-help">阶段正文尚未形成，但校验问题已保留。</p>';
    const issues = qualityIssues.length
      ? `<section class="et3-panel et3-panel--blocked"><h3>需要处理的问题</h3><ul class="et3-review-list">${qualityIssues.map(item => `<li class="et3-review-item"><span><strong>${esc(item.title)}</strong><small>${esc(item.phase)}</small></span></li>`).join('')}</ul></section>`
      : '';
    return `<section class="et3-panel"><h3>已保留的阶段结果</h3><p class="et3-help">以下结果未被采用为当前阶段的权威成果，但已安全保留供你核对。请先按建议补充或调整，再重新生成当前阶段。</p>${document}</section>${issues}`;
  }

  function bindWorkbenchEvents(root) {
    if (state.workbenchController) state.workbenchController.abort();
    state.workbenchController = new AbortController();
    const signal = state.workbenchController.signal;
    root.addEventListener('click', event => handleWorkbenchClick(event), { signal });
    root.addEventListener('compositionstart', event => {
      if (!event.target?.closest?.('input, textarea, [contenteditable="true"]')) return;
      state.compositionActive = true;
    }, { signal });
    root.addEventListener('compositionend', event => {
      if (!event.target?.closest?.('input, textarea, [contenteditable="true"]')) return;
      state.compositionActive = false;
      const deferred = releaseDeferredStatusCard();
      if (!deferred) return;
      setTimeout(() => {
        if (state.compositionActive) {
          deferStatusRenderDuringComposition(deferred);
          return;
        }
        const activeSession = window.S && window.S.session && window.S.session.session_id;
        if (deferred.sourceSessionId && activeSession && deferred.sourceSessionId !== activeSession) return;
        if (state.card && String(deferred.runId || '') !== String(state.card.runId || '')) return;
        renderStatusSurface(deferred);
      }, 0);
    }, { signal });
  }

  async function handleWorkbenchClick(event) {
    const button = event.target.closest('[data-et3-action]');
    if (!button || state.busy) return;
    const action = button.dataset.et3Action;
    if (action === 'open-model-settings') {
      if (typeof window.switchSettingsSection === 'function') window.switchSettingsSection('models');
      else setLive('模型配置入口暂不可用，请从设置中打开模型配置。', true);
      return true;
    }
    if (action === 'export-diagnostics') {
      if (typeof window.exportProductDiagnostics === 'function') await window.exportProductDiagnostics();
      else setLive('诊断导出入口暂不可用，请从设置中打开安全诊断。', true);
      return true;
    }
    if (action === 'copy-diagnostics') return copyExpertTeamDiagnostics();
    if (action === 'start-new-task') {
      const card = state.card || {};
      const teamId = String(card.team?.id || '');
      const prompt = String(card.brief?.originalRequest || card.brief?.originalRequestSummary || '').trim();
      const documentType = String(card.brief?.documentType || '');
      clearStatusSurface();
      if (teamId) await openTeam(teamId, null, {
        correction: true,
        prompt,
        documentType,
      });
      else setLive('请从专家团中心选择任务后重新发起。', true);
      return true;
    }
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
      'save-brief': 'answer', 'confirm-brief': 'answer', 'submit-answers': 'answer',
      'start-generation': 'start_generation', 'submit-stage-input': 'submit_stage_input',
      'retry-run': 'resume', 'cancel-run': 'cancel', 'retry-cancel': 'retry_cancel',
      'recheck-stage': 'stage_recheck',
      'delivery-open-document': 'delivery_open_document', 'delivery-save-copy': 'delivery_save_copy',
      'delivery-open-folder': 'delivery_open_folder',
      'delivery-rerender': 'delivery_rerender', 'submit-delivery-revision': 'delivery_revise',
      'delivery-confirm': 'delivery_confirm',
      'delivery-recover': 'delivery_recover',
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
    if (action === 'delivery-open-document') {
      if (canOpenDeliveryNatively()) return openDelivery('document', button);
      const control = deliveryActionControl('delivery-open-document', 'delivery_open_document');
      if (!control) return setLive('当前交付操作信息不完整，请重新进入任务或刷新会话状态。', true);
      return downloadDeliveryCopy(button, control, { finalDocument: true });
    }
    if (action === 'delivery-save-copy') return saveDeliveryCopy(button);
    if (action === 'delivery-open-folder') return openDelivery('folder', button);
    if (action === 'delivery-rerender') return rerenderDelivery(button);
    if (action === 'submit-delivery-revision') return submitDeliveryRevision(button);
    if (action === 'delivery-confirm') return confirmDelivery(button);
    if (action === 'delivery-recover') return recoverDelivery(button);
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
    if (action === 'recheck-stage') return recheckStage(button);
    if (action === 'confirm-stage') return confirmStage(button);
  }

  async function copyExpertTeamDiagnostics() {
    const value = expertTeamDiagnosticText(state.card);
    try {
      if (typeof window._copyText === 'function') await window._copyText(value);
      else if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') await navigator.clipboard.writeText(value);
      else throw new Error('clipboard unavailable');
      setLive('诊断信息已复制。');
      return true;
    } catch (_error) {
      setLive('未能复制诊断信息，请使用“导出完整诊断”。', true);
      return false;
    }
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

  async function mutate(endpoint, extra, button, kind, options = {}) {
    setBusy(button, true, options.busyLabel || '处理中…');
    try {
      const payload = await window.api(endpoint, { method: 'POST', body: JSON.stringify({ ...mutationControl(kind || endpoint.split('/').pop()), ...(extra || {}) }) });
      if (options.renderResponse === false) updateCardFromResponse(payload);
      else applyResponse(payload);
      setLive(options.successMessage || '操作已保存。');
      return true;
    } catch (error) {
      if (error && error.payload && error.payload.run) applyResponse(error.payload);
      if (String(endpoint || '').includes('/brief/') && error?.payload?.field) {
        showBriefFieldErrors([{
          field: error.payload.field,
          code: error.payload.code || 'invalid',
          message: error.payload.error || error.message || '请检查此项',
        }]);
      }
      setLive(mutationErrorMessage(error, '操作失败，请刷新状态后重试。'), true);
      return false;
    } finally { setBusy(button, false); }
  }

  function isConflictError(error) {
    return Number(error && error.status || 0) === 409;
  }

  function mutationErrorMessage(error, fallback) {
    if (error && error.payload && error.payload.product_error) {
      return String(error.payload.product_error.message || fallback || '操作未能完成。');
    }
    return String(error && error.message || fallback || '操作未能完成。');
  }

  function stageActionControl(kind, action) {
    if (!actionAllowed(state.card, action)) return null;
    if (typeof window.buildExpertTeamStageActionPayload !== 'function') return null;
    const control = window.buildExpertTeamStageActionPayload(state.card, uid(kind));
    const required = ['session_id', 'run_id', 'expected_version', 'stage_id', 'stage_attempt', 'artifact_id', 'artifact_sha256', 'idempotency_key'];
    return control && required.every(key => Object.prototype.hasOwnProperty.call(control, key)) ? control : null;
  }

  function deliveryActionControl(kind, action) {
    if (!actionAllowed(state.card, action)) return null;
    if (typeof window.buildExpertTeamDeliveryActionPayload !== 'function') return null;
    const control = window.buildExpertTeamDeliveryActionPayload(state.card, uid(kind));
    const required = [
      'session_id', 'run_id', 'expected_version', 'stage_id', 'stage_attempt',
      'artifact_id', 'artifact_sha256', 'delivery_attempt', 'delivery_binding_sha256',
      'document_sha256', 'idempotency_key',
    ];
    return control && required.every(key => Object.prototype.hasOwnProperty.call(control, key)) ? control : null;
  }

  function deliveryRecoveryControl(kind) {
    if (!actionAllowed(state.card, 'delivery_recover')) return null;
    if (typeof window.buildExpertTeamDeliveryRecoveryPayload !== 'function') return null;
    const control = window.buildExpertTeamDeliveryRecoveryPayload(state.card, uid(kind));
    const required = [
      'session_id', 'run_id', 'expected_version', 'stage_id', 'stage_attempt',
      'artifact_id', 'artifact_sha256', 'delivery_attempt', 'delivery_binding_sha256',
      'document_sha256', 'idempotency_key',
    ];
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
      if (action === 'stage_revise') {
        const field = workbenchRoot()?.querySelector('[data-et3-revision]');
        if (field) field.value = '';
      }
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

  async function mutateDelivery(endpoint, action, extra, button, kind, successMessage) {
    const control = deliveryActionControl(kind, action);
    if (!control) return setLive('当前交付操作信息不完整，请重新进入任务或刷新会话状态。', true);
    const conflictDraft = action === 'delivery_revise' ? captureConflictDeliveryDraft(state.card) : null;
    setBusy(button, true, action === 'delivery_confirm' ? '正在确认…' : '处理中…');
    try {
      const payload = await window.api(endpoint, {
        method: 'POST',
        body: JSON.stringify({ ...control, ...(extra || {}) }),
      });
      if (!['delivery_open_document', 'delivery_open_folder'].includes(action)) {
        if (action === 'delivery_revise') {
          const field = workbenchRoot()?.querySelector('[data-et3-delivery-revision]');
          if (field) field.value = '';
        }
        state.conflictDeliveryDraft = null;
        applyResponse(payload);
      }
      setLive(successMessage || '操作已保存。');
      return true;
    } catch (error) {
      if (isConflictError(error) && conflictDraft) state.conflictDeliveryDraft = conflictDraft;
      if (error && error.payload && error.payload.run) applyResponse(error.payload);
      if (isConflictError(error)) {
        setLive(conflictDraft ? '交付状态已更新，修改意见已保留，请核对新文档后重试。' : '交付状态已更新，请核对最新文档后重试。', true);
      } else {
        setLive(error.message || '交付操作失败，请刷新状态后重试。', true);
      }
      return false;
    } finally { setBusy(button, false); }
  }

  function cardFromResponse(payload) {
    const run = payload && payload.run ? payload.run : payload;
    if (!run || !run.run_id || typeof window.buildExpertTeamCardFromRun !== 'function') return null;
    return window.buildExpertTeamCardFromRun(run, payload);
  }

  function updateCardFromResponse(payload) {
    const card = cardFromResponse(payload);
    if (!card) return false;
    state.card = card;
    return true;
  }

  function applyResponse(payload) {
    if (typeof window._applyExpertTeamStreamResponse === 'function') {
      window._applyExpertTeamStreamResponse(payload);
    }
    const card = cardFromResponse(payload);
    return card ? renderStatusSurface(card) : false;
  }

  function formValues(form) {
    return Object.fromEntries(Array.from(new FormData(form).entries()).map(([key, value]) => [key, String(value).trim()]));
  }

  function setNestedBriefValue(target, path, value) {
    const segments = String(path || '').split('.');
    if (!segments.length || segments.some(segment => !/^[a-z][a-z0-9_]*$/i.test(segment) || ['__proto__', 'prototype', 'constructor'].includes(segment))) return;
    let current = target;
    segments.forEach((segment, index) => {
      if (index === segments.length - 1) current[segment] = value;
      else {
        if (!current[segment] || typeof current[segment] !== 'object' || Array.isArray(current[segment])) current[segment] = {};
        current = current[segment];
      }
    });
  }

  function buildBriefPatch(values, schema) {
    const patch = {};
    list(schema).forEach(field => {
      const path = String(field?.path || '');
      if (!path || !Object.prototype.hasOwnProperty.call(values || {}, path)) return;
      setNestedBriefValue(patch, path, String(values[path] ?? '').trim());
    });
    return patch;
  }

  function clientBriefFieldErrors(values, schema) {
    return list(schema).filter(field => field?.required === true && !String(values?.[field.path] || '').trim()).map(field => ({
      field: String(field.path || ''), code: 'required', message: `请填写${String(field.label || field.path || '必填项')}`,
    }));
  }

  function showBriefFieldErrors(errors) {
    const form = workbenchRoot()?.querySelector('[data-et3-brief-form]');
    if (!form) return false;
    const normalized = list(errors).filter(error => error && error.field && error.message);
    const byField = new Map();
    normalized.forEach(error => {
      const field = String(error.field);
      if (!byField.has(field)) byField.set(field, []);
      byField.get(field).push(String(error.message));
    });
    Array.from(form.querySelectorAll('[data-et3-field-error-for]')).forEach(slot => {
      const messages = byField.get(String(slot.dataset.et3FieldErrorFor || '')) || [];
      slot.textContent = messages.join('；');
      slot.hidden = messages.length === 0;
    });
    Array.from(form.querySelectorAll('[data-et3-brief-path]')).forEach(control => {
      const invalid = byField.has(String(control.dataset.et3BriefPath || ''));
      if (invalid) control.setAttribute('aria-invalid', 'true');
      else control.removeAttribute('aria-invalid');
    });
    const first = Array.from(form.querySelectorAll('[data-et3-brief-path]')).find(control => byField.has(String(control.dataset.et3BriefPath || '')));
    if (first) {
      first.focus();
      first.scrollIntoView({ block: 'center' });
    }
    return normalized.length === 0;
  }

  async function submitAnswers(button) {
    const form = workbenchRoot().querySelector('[data-et3-brief-form]');
    const nativeValid = form.reportValidity();
    const values = formValues(form);
    const errors = clientBriefFieldErrors(values, briefFieldSchema(state.card.brief));
    if (!nativeValid || errors.length) { showBriefFieldErrors(errors); return setLive('请先补全页面标出的必填项。', true); }
    const answers = Object.fromEntries(Object.entries(values).filter(([key]) => key.startsWith('question__')).map(([key, value]) => [key.slice('question__'.length), value]));
    if (!await saveBriefFields(button, values, false)) return false;
    const answered = await mutate('/api/expert-teams/answer', { answers, skip_optional: true }, button, 'answer', {
      busyLabel: '正在保存回答…', successMessage: '回答已保存，请确认规格。',
    });
    if (!answered) return false;
    setLive('回答已保存，请确认规格。');
    const heading = workbenchRoot()?.querySelector('[data-et3-brief-form]')?.closest('.et3-panel')?.querySelector('h3')
      || workbenchRoot()?.querySelector('.et3-workbench-scroll h3');
    if (heading) {
      heading.setAttribute('tabindex', '-1');
      heading.focus();
      heading.scrollIntoView({ block: 'start' });
    }
    return true;
  }

  function saveBriefFields(button, values, renderResponse = true) {
    const patch = buildBriefPatch(values, briefFieldSchema(state.card.brief));
    const requestOptions = renderResponse === false
      ? { renderResponse: false, busyLabel: '保存中…', successMessage: '规格已保存。' }
      : { busyLabel: '保存中…', successMessage: '规格已保存。' };
    return mutate('/api/expert-teams/brief/update', { expected_brief_revision: Number(state.card.brief?.revision || 0), patch }, button, 'brief-update', requestOptions);
  }

  async function saveBrief(button, confirmAfter) {
    const form = workbenchRoot().querySelector('[data-et3-brief-form]');
    const nativeValid = !confirmAfter || form.reportValidity();
    const values = formValues(form);
    const errors = confirmAfter ? clientBriefFieldErrors(values, briefFieldSchema(state.card.brief)) : [];
    if (!nativeValid || errors.length) { showBriefFieldErrors(errors); return setLive('请先补全页面标出的必填项。', true); }
    const saved = await saveBriefFields(button, values, !confirmAfter);
    if (!saved || !confirmAfter) return saved;
    return mutate('/api/expert-teams/brief/confirm', { expected_brief_revision: Number(state.card.brief?.revision || 0) }, button, 'brief-confirm', {
      busyLabel: '正在确认规格…', successMessage: '规格已确认。',
    });
  }

  function appendRevision(text) {
    const field = workbenchRoot().querySelector('[data-et3-revision]');
    if (!field) return;
    const line = String(text || '').trim();
    if (line && !field.value.includes(line)) field.value = `${field.value.trim()}${field.value.trim() ? '\n' : ''}- ${line}`;
    field.focus();
  }

  function submitRevision(button) {
    const field = workbenchRoot().querySelector('[data-et3-revision]');
    const feedback = String(field?.value || '').trim();
    if (!feedback) {
      field?.setAttribute('aria-invalid', 'true');
      field?.setAttribute('aria-errormessage', 'expertTeamV3Live');
      return setLive('请填写修改意见；若无修改，请使用“无修改，进入下一阶段”。', true);
    }
    field?.removeAttribute('aria-invalid');
    field?.removeAttribute('aria-errormessage');
    return mutateStage('/api/expert-teams/stage/revise', 'stage_revise', { feedback }, button, 'stage-revise');
  }

  function confirmStage(button) {
    return mutateStage('/api/expert-teams/stage/confirm', 'stage_confirm', {}, button, 'stage-confirm');
  }

  function recheckStage(button) {
    return mutateStage('/api/expert-teams/stage/confirm', 'stage_recheck', {}, button, 'stage-recheck');
  }

  function submitStageInput(button) {
    const answer = String(workbenchRoot().querySelector('[data-et3-stage-input]')?.value || '').trim();
    if (!answer) return setLive('请先填写当前阶段需要的信息。', true);
    return mutate('/api/expert-teams/stage/input', { input_id: state.card.pendingInputId || '', answer }, button, 'stage-input');
  }

  function openDelivery(target, button) {
    const action = {
      folder: 'delivery_open_folder',
      document: 'delivery_open_document',
    }[target] || 'delivery_open_document';
    return mutateDelivery(
      '/api/expert-teams/delivery/open',
      action,
      { target },
      button,
      `delivery-open-${target}`,
      target === 'folder' ? '已打开文件夹。' : '已打开最终 DOCX。',
    );
  }

  async function saveDeliveryCopy(button) {
    const control = deliveryActionControl('delivery-save-copy', 'delivery_save_copy');
    if (!control) return setLive('当前交付操作信息不完整，请重新进入任务或刷新会话状态。', true);
    if (!window.taijiDesktop || typeof window.taijiDesktop.pickDirectory !== 'function') {
      return downloadDeliveryCopy(button, control);
    }
    setBusy(button, true, '选择保存位置…');
    try {
      const selected = await window.taijiDesktop.pickDirectory();
      if (!selected?.ok) {
        if (selected?.canceled) setLive('已取消保存副本。');
        else setLive('未能打开保存位置选择器。', true);
        return false;
      }
      setBusy(button, true, '保存中…');
      const payload = await window.api('/api/expert-teams/delivery/save-copy', {
        method: 'POST',
        body: JSON.stringify({ ...control, destination_dir: selected.path }),
      });
      setLive(`已保存副本：${String(payload?.saved_name || state.card?.standaloneDelivery?.documentName || '最终文档.docx')}`);
      return true;
    } catch (error) {
      if (error?.payload?.run) applyResponse(error.payload);
      setLive(error.message || '保存副本失败，原交付文档仍已保留。', true);
      return false;
    } finally {
      setBusy(button, false);
    }
  }

  async function downloadDeliveryCopy(button, control, options = {}) {
    const finalDocument = options.finalDocument === true;
    if (typeof window.fetch !== 'function' || !window.URL || typeof window.URL.createObjectURL !== 'function') {
      return setLive(
        finalDocument
          ? '当前环境无法下载最终 DOCX，请刷新后重试。'
          : '当前环境无法保存副本，请在太极智能体桌面端中重试。',
        true,
      );
    }
    setBusy(button, true, '准备下载…');
    let objectUrl = '';
    try {
      const response = await window.fetch('/api/expert-teams/delivery/download', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(control),
      });
      if (!response.ok) {
        const detail = await response.text();
        let message = detail || '下载副本失败';
        try {
          const payload = JSON.parse(detail);
          message = payload.error || payload.message || message;
        } catch (_error) {}
        throw new Error(message);
      }
      const blob = await response.blob();
      objectUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      const documentName = String(state.card?.standaloneDelivery?.documentName || '最终交付文档.docx');
      link.href = objectUrl;
      link.download = documentName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setLive(`${finalDocument ? '已开始下载最终 DOCX' : '已开始下载副本'}：${documentName}`);
      return true;
    } catch (error) {
      setLive(
        error.message || (finalDocument
          ? '下载最终 DOCX 失败，原交付文档仍已保留。'
          : '下载副本失败，原交付文档仍已保留。'),
        true,
      );
      return false;
    } finally {
      if (objectUrl) window.URL.revokeObjectURL(objectUrl);
      setBusy(button, false);
    }
  }

  function submitDeliveryRevision(button) {
    const field = workbenchRoot().querySelector('[data-et3-delivery-revision]');
    const feedback = String(field?.value || '').trim();
    if (!feedback) {
      field?.setAttribute('aria-invalid', 'true');
      field?.setAttribute('aria-errormessage', 'expertTeamV3Live');
      field?.focus();
      return setLive('请先填写需要修改的内容；如果文档无需修改，请确认可交付。', true);
    }
    field?.removeAttribute('aria-invalid');
    field?.removeAttribute('aria-errormessage');
    return mutateDelivery(
      '/api/expert-teams/delivery/revise',
      'delivery_revise',
      { feedback },
      button,
      'delivery-revise',
      '修改意见已提交，专家团正在重新生成文档。',
    );
  }

  function rerenderDelivery(button) {
    return mutateDelivery(
      '/api/expert-teams/delivery/rerender',
      'delivery_rerender',
      {},
      button,
      'delivery-rerender',
      '已保留确认正文，正在重新生成并检查 DOCX。',
    );
  }

  function confirmDelivery(button) {
    return mutateDelivery(
      '/api/expert-teams/delivery/confirm',
      'delivery_confirm',
      {},
      button,
      'delivery-confirm',
      '本机确认已提交，交付状态以服务端最新结果为准。',
    );
  }

  async function recoverDelivery(button) {
    const control = deliveryRecoveryControl('delivery-recover');
    if (!control) return setLive('当前恢复操作信息不完整，请刷新任务状态后重试。', true);
    setBusy(button, true, '正在重新生成…');
    try {
      const payload = await window.api('/api/expert-teams/delivery/recover', {
        method: 'POST',
        body: JSON.stringify(control),
      });
      applyResponse(payload);
      setLive('恢复请求已提交，正在重新生成并检查 DOCX。');
      return true;
    } catch (error) {
      if (error && error.payload && error.payload.run) applyResponse(error.payload);
      if (isConflictError(error)) setLive('交付状态已更新，请核对最新状态后重试。', true);
      else setLive(error.message || '重新生成 DOCX 失败，请重试。', true);
      return false;
    } finally { setBusy(button, false); }
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
        if (event.key === 'Escape') {
          if (state.suggestionMode) returnSuggestionToComposer();
          else closeDialog();
        }
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
    applyResponse, effectiveState, suggestFromPrompt,
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
}());
