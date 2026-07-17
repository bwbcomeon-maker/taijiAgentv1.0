/* global S, api, switchPanel, renderSessionList, loadSession, newSession, send, autoResize, updateSendBtn, showConfirmDialog, showPromptDialog, showToast, deleteSession */
(function(){
  'use strict';

  const PANEL_LABELS={
    chat:'聊天',
    tasks:'任务',
    kanban:'看板',
    writing:'专家团',
    skills:'技能',
    memory:'记忆',
    workspaces:'工作区',
    profiles:'配置',
    todos:'待办',
    insights:'统计',
    logs:'日志',
    settings:'设置'
  };
  const SECONDARY_PANEL_CONFIG={
    chat:{title:'最近会话'},
    tasks:{title:'计划任务',panelId:'panelTasks'},
    kanban:{title:'看板',panelId:'panelKanban'},
    writing:{title:'专家团',panelId:'panelWriting'},
    skills:{title:'技能',panelId:'panelSkills'},
    memory:{title:'记忆',panelId:'panelMemory'},
    workspaces:{title:'工作区',panelId:'panelWorkspaces'},
    profiles:{title:'配置',panelId:'panelProfiles'},
    todos:{title:'待办',panelId:'panelTodos'},
    insights:{title:'统计',panelId:'panelInsights'},
    logs:{title:'日志',panelId:'panelLogs'},
    settings:{title:'设置',panelId:'panelSettings'}
  };
  const PANEL_BY_LABEL=Object.fromEntries(Object.entries(PANEL_LABELS).map(([k,v])=>[v,k]));
  const SESSION_FILTERS={all:'all',ungrouped:'ungrouped'};
  const RECENT_SESSION_PREVIEW_LIMIT=18;
  const SECONDARY_COLLAPSED_KEY='hermes-webui-taiji-secondary-collapsed';
  const state={
    mounted:false,
    sessions:[],
    projects:[],
    sessionFilter:'all',
    projectPanelOpen:false,
    projectSearch:'',
    showAllSessions:false,
    search:'',
    secondaryCollapsed:false,
    refreshInFlight:null,
    refreshTimer:0,
    syncTimer:0,
    wrapped:false,
    panelPlaceholders:new Map(),
    mainPlaceholder:null,
    rightpanelPlaceholder:null
  };
  let projectMenuClickClose=null;
  let projectMenuKeyClose=null;
  let projectPanelClickClose=null;
  let projectPanelKeyClose=null;
  let sessionActionMenuAnchor=null;
  let sessionActionMenuClickClose=null;
  let sessionActionMenuKeyClose=null;

  const $=id=>document.getElementById(id);
  const shell=()=>document.querySelector('.taiji-home-shell');
  const workspace=()=>document.querySelector('.taiji-main-workspace');
  const secondary=()=>document.querySelector('.taiji-secondary-panel');
  const secondaryHost=()=>document.getElementById('taijiPanelSecondaryHost');
  const homeShellActive=()=>window.matchMedia&&window.matchMedia('(min-width:901px)').matches;
  const desktop=()=>window.matchMedia&&window.matchMedia('(min-width:1024px)').matches;
  const appState=()=>{
    if(typeof S!=='undefined'&&S) return S;
    return window.S||null;
  };
  const escapeHtml=value=>String(value==null?'':value).replace(/[&<>"']/g,ch=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));

  function hydrateTaijiIcons(root=document){
    if(typeof li!=='function') return;
    root.querySelectorAll('.taiji-icon[data-icon]').forEach(node=>{
      if(node.dataset.iconHydrated==='1') return;
      const name=node.dataset.icon;
      const size=Number(node.dataset.iconSize||24);
      node.innerHTML=li(name,size);
      node.dataset.iconHydrated='1';
    });
  }

  function readSecondaryCollapsed(){
    try{
      return localStorage.getItem(SECONDARY_COLLAPSED_KEY)==='1';
    }catch(_){
      return false;
    }
  }

  function writeSecondaryCollapsed(value){
    try{
      localStorage.setItem(SECONDARY_COLLAPSED_KEY,value?'1':'0');
    }catch(_){}
  }

  function secondaryToggleLabel(panel){
    if(panel==='chat') return '会话';
    const config=SECONDARY_PANEL_CONFIG[panel]||{};
    return config.title||PANEL_LABELS[panel]||'二栏';
  }

  function activePanel(){
    const main=document.querySelector('main.main');
    if(!main) return 'chat';
    for(const key of Object.keys(PANEL_LABELS)){
      if(key!=='chat'&&main.classList.contains('showing-'+key)) return key;
    }
    return 'chat';
  }

  function panelVisible(panel){
    return panel==='chat'||typeof isUiFeatureVisible!=='function'||isUiFeatureVisible('nav',panel);
  }

  function visiblePanel(panel){
    const key=SECONDARY_PANEL_CONFIG[panel]?panel:'chat';
    return panelVisible(key)?key:'chat';
  }

  function hasVisibleMessages(){
    const s=appState();
    return !!(s&&Array.isArray(s.messages)&&s.messages.some(m=>m&&m.role&&m.role!=='tool'));
  }

  function scheduleSync(){
    if(state.syncTimer) return;
    state.syncTimer=setTimeout(()=>{
      state.syncTimer=0;
      syncShellState();
    },0);
  }

  function syncShellState(){
    const root=shell();
    if(!root) return;
    if(!homeShellActive()){
      unmountRealWorkspace();
      returnHostedPanels();
      return;
    }
    if(!state.mounted) mountRealWorkspace();
    const rawPanel=activePanel();
    const panel=visiblePanel(rawPanel);
    if(rawPanel!==panel&&typeof switchPanel==='function'){
      setTimeout(()=>switchPanel('chat',{bypassSettingsGuard:true}),0);
    }
    root.dataset.activePanel=panel;
    root.dataset.secondaryPanel=panel;
    root.dataset.secondaryCollapsed=desktop()&&state.secondaryCollapsed?'1':'0';
    root.classList.toggle('taiji-chat-active',panel==='chat');
    root.classList.toggle('taiji-welcome',panel==='chat'&&!hasVisibleMessages());
    root.classList.toggle('taiji-chat-has-messages',panel==='chat'&&hasVisibleMessages());
    const toggle=$('taijiSecondaryToggle');
    if(toggle){
      const collapsed=root.dataset.secondaryCollapsed==='1';
      const label=secondaryToggleLabel(panel);
      const action=collapsed?'展开':'收起';
      toggle.setAttribute('aria-expanded',collapsed?'false':'true');
      toggle.setAttribute('aria-label',`${action}${label}栏`);
      toggle.title=`${action}${label}栏`;
      const icon=toggle.querySelector('.taiji-secondary-toggle-icon');
      if(icon) icon.textContent=collapsed?'›':'‹';
      const text=toggle.querySelector('.taiji-secondary-toggle-label');
      if(text) text.textContent=label;
    }
    document.querySelectorAll('.taiji-nav-item').forEach(btn=>{
      btn.classList.toggle('is-active',btn.dataset.taijiPanel===panel);
    });
    if(typeof applyUiVisibility==='function') applyUiVisibility();
    const s=appState();
    const activeSid=s&&s.session&&s.session.session_id;
    document.querySelectorAll('.taiji-session-row').forEach(row=>{
      row.classList.toggle('is-active',!!activeSid&&row.dataset.sessionId===activeSid);
    });
    renderSecondaryPanel(panel);
  }

  function ensurePanelPlaceholder(panelEl){
    if(!panelEl||state.panelPlaceholders.has(panelEl.id)) return;
    const marker=document.createComment('taiji-secondary-origin:'+panelEl.id);
    if(panelEl.parentNode) panelEl.parentNode.insertBefore(marker,panelEl);
    state.panelPlaceholders.set(panelEl.id,marker);
  }

  function returnHostedPanel(panelEl){
    if(!panelEl||!panelEl.id) return;
    const marker=state.panelPlaceholders.get(panelEl.id);
    if(marker&&marker.parentNode){
      marker.parentNode.insertBefore(panelEl,marker.nextSibling);
    }
    panelEl.classList.remove('taiji-secondary-active-view');
  }

  function returnHostedPanels(exceptId){
    const host=secondaryHost();
    if(!host) return;
    Array.from(host.children).forEach(child=>{
      if(child.classList&&child.classList.contains('panel-view')&&child.id!==exceptId){
        returnHostedPanel(child);
      }
    });
  }

  function titleSecondaryPanel(panelEl,title){
    if(!panelEl||!title) return;
    const titleEl=panelEl.querySelector('.panel-head > span');
    if(titleEl) titleEl.textContent=title;
  }

  function renderSecondaryPanel(panel){
    const aside=secondary();
    const host=secondaryHost();
    if(!aside||!host) return;
    const key=visiblePanel(panel);
    const config=SECONDARY_PANEL_CONFIG[key];
    aside.dataset.secondaryPanel=key;
    aside.setAttribute('aria-label',key==='chat'?'最近会话':config.title+'子功能');
    if(key==='chat'){
      returnHostedPanels();
      host.replaceChildren();
      return;
    }
    const panelEl=$(config.panelId);
    if(!panelEl){
      returnHostedPanels();
      host.innerHTML=`<div class="taiji-secondary-empty"><h2>${escapeHtml(config.title)}</h2><p>该功能暂无可用子功能。</p></div>`;
      return;
    }
    ensurePanelPlaceholder(panelEl);
    returnHostedPanels(panelEl.id);
    if(panelEl.parentNode!==host) host.appendChild(panelEl);
    panelEl.classList.add('active','taiji-secondary-active-view');
    titleSecondaryPanel(panelEl,config.title);
  }

  function mountRealWorkspace(){
    const root=shell();
    const target=workspace();
    const main=document.querySelector('main.main');
    if(!root||!target||!main||state.mounted||!homeShellActive()) return;
    const rightpanel=document.querySelector('.rightpanel');
    if(!state.mainPlaceholder&&main.parentNode){
      state.mainPlaceholder=document.createComment('taiji-main-origin');
      main.parentNode.insertBefore(state.mainPlaceholder,main);
    }
    main.classList.add('taiji-real-main');
    target.appendChild(main);
    if(rightpanel){
      if(!state.rightpanelPlaceholder&&rightpanel.parentNode){
        state.rightpanelPlaceholder=document.createComment('taiji-rightpanel-origin');
        rightpanel.parentNode.insertBefore(state.rightpanelPlaceholder,rightpanel);
      }
      rightpanel.classList.add('taiji-workspace-drawer');
      target.appendChild(rightpanel);
    }
    state.mounted=true;
  }

  function restoreAfterMarker(el,marker){
    if(!el||!marker||!marker.parentNode) return;
    marker.parentNode.insertBefore(el,marker.nextSibling);
  }

  function unmountRealWorkspace(){
    if(!state.mounted) return;
    const main=document.querySelector('main.main.taiji-real-main');
    const rightpanel=document.querySelector('.rightpanel.taiji-workspace-drawer');
    if(main){
      main.classList.remove('taiji-real-main');
      restoreAfterMarker(main,state.mainPlaceholder);
    }
    if(rightpanel){
      rightpanel.classList.remove('taiji-workspace-drawer');
      restoreAfterMarker(rightpanel,state.rightpanelPlaceholder);
    }
    state.mounted=false;
  }

  function wrapFunction(name){
    const original=window[name];
    if(typeof original!=='function'||original.__taijiWrapped) return;
    const wrapped=function(){
      const result=original.apply(this,arguments);
      if(result&&typeof result.then==='function'){
        return result.finally(()=>{
          if(name==='renderSessionList'||name==='renderSessionListFromCache'||name==='newSession'||name==='loadSession'){
            scheduleSessionRefresh();
          }
          scheduleSync();
        });
      }
      if(name==='renderSessionList'||name==='renderSessionListFromCache'||name==='newSession'||name==='loadSession'){
        scheduleSessionRefresh();
      }
      scheduleSync();
      return result;
    };
    wrapped.__taijiWrapped=true;
    wrapped.__taijiOriginal=original;
    window[name]=wrapped;
  }

  function wrapLegacyHooks(){
    if(state.wrapped) return;
    ['switchPanel','renderSessionList','renderSessionListFromCache','loadSession','newSession','renderMessages','setBusy','syncTopbar','updateSendBtn','syncModelChip'].forEach(wrapFunction);
    state.wrapped=true;
  }

  function globalFn(name){
    if(name==='api'&&typeof api==='function') return api;
    if(name==='switchPanel'&&typeof switchPanel==='function') return switchPanel;
    if(name==='renderSessionList'&&typeof renderSessionList==='function') return renderSessionList;
    if(name==='loadSession'&&typeof loadSession==='function') return loadSession;
    if(name==='newSession'&&typeof newSession==='function') return newSession;
    if(name==='send'&&typeof send==='function') return send;
    if(name==='autoResize'&&typeof autoResize==='function') return autoResize;
    if(name==='updateSendBtn'&&typeof updateSendBtn==='function') return updateSendBtn;
    if(name==='showConfirmDialog'&&typeof showConfirmDialog==='function') return showConfirmDialog;
    if(name==='showPromptDialog'&&typeof showPromptDialog==='function') return showPromptDialog;
    if(name==='showToast'&&typeof showToast==='function') return showToast;
    return typeof window[name]==='function'?window[name]:null;
  }

  function sessionTimestamp(session){
    const raw=session.last_message_at||session.updated_at||session.created_at||0;
    const n=Number(raw)||0;
    if(!n) return 0;
    return n>1000000000000?n:n*1000;
  }

  function dayStart(date){
    const d=new Date(date);
    d.setHours(0,0,0,0);
    return d.getTime();
  }

  function groupNameForSession(session){
    const ts=sessionTimestamp(session);
    if(!ts) return '更早';
    const now=Date.now();
    const today=dayStart(now);
    const yesterday=today-86400000;
    const week=today-6*86400000;
    if(ts>=today) return '今天';
    if(ts>=yesterday) return '昨天';
    if(ts>=week) return '本周';
    return '更早';
  }

  function sessionTimeLabel(session){
    const ts=sessionTimestamp(session);
    if(!ts) return '';
    const d=new Date(ts);
    const group=groupNameForSession(session);
    if(group==='今天'||group==='昨天'){
      return d.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});
    }
    const days=['周日','周一','周二','周三','周四','周五','周六'];
    if(group==='本周') return days[d.getDay()];
    return d.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'});
  }

  function normalizeTaijiSessionTitle(value){
    return String(value==null?'':value)
      .replace(/\n\n\[Attached files: [^\]]+\]$/,'')
      .replace(/\s+/g,' ')
      .trim()
      .replace(/^[：:，,\s]+|[。.!！?？、，,：:；;\s]+$/g,'');
  }

  function taijiClampSessionTitle(value, max=32){
    const text=normalizeTaijiSessionTitle(value);
    if(text.length<=max) return text;
    return text.slice(0,Math.max(1,max-1)).trimEnd()+'…';
  }

  function taijiSessionTitleIsGeneric(value){
    const text=normalizeTaijiSessionTitle(value);
    if(!text) return true;
    if(['Untitled','New Chat','New chat','未命名会话','太极 Agent','Hermes WebUI','taiji Agent WebUI'].includes(text)) return true;
    if(/^太极 Agent #\d+$/.test(text)||/^Hermes WebUI #\d+$/.test(text)||/^taiji Agent WebUI #\d+$/.test(text)) return true;
    return false;
  }

  function taijiWriteflowTeamLabel(session, rawTitle=''){
    const teamId=String((session&&session.writeflow_team_id)||'');
    const teamLabels={
      'content-creator-team':'内容创作',
      'deep-research-team':'深度研究'
    };
    if(teamLabels[teamId]) return teamLabels[teamId];
    const match=normalizeTaijiSessionTitle(rawTitle).match(/请【([^】]+)】/);
    if(match&&match[1]){
      return match[1]
        .replace(/内容创作专家团/,'内容创作')
        .replace(/深度文章研究团/,'深度研究')
        .replace(/专家团$/,'')
        .replace(/研究团$/,'研究')
        .trim()||'专家团';
    }
    return '专家团';
  }

  function taijiCompactTopic(value){
    let text=normalizeTaijiSessionTitle(value);
    const expertStartMatch=text.match(/^召唤[^：:\n]{0,64}专家团[：:]\s*(.+)$/);
    if(expertStartMatch&&expertStartMatch[1]){
      return taijiClampSessionTitle(expertStartMatch[1].trim(),32);
    }
    text=text
      .replace(/^召唤[^：:\n]{0,64}专家团[：:]\s*/,'')
      .replace(/^请【[^】]+】接手这个写作任务[。.\s]*/,'')
      .replace(/^请把这个任务交给[^，,。.!！?？]*[，,。.!！?？\s]*/,'')
      .replace(/^请?帮我(整理|写|生成|规划|输出|起草|做)?一篇(关于|有关)?/i,'')
      .replace(/^请?帮我(整理|写|生成|规划|输出|起草|做)?/i,'')
      .replace(/^请?(围绕|关于|有关|主题是)[「“"]?/i,'')
      .replace(/[」”"]$/,'')
      .trim();
    if(!text) return '';
    return taijiClampSessionTitle(text,32);
  }

  function taijiSessionDisplayTitle(session){
    if(!session) return '未命名会话';
    const rawTitle=normalizeTaijiSessionTitle(session.title||session.name||'');
    const displayTitle=normalizeTaijiSessionTitle(session.display_title||'');
    const writeflowTitle=normalizeTaijiSessionTitle(session.writeflow_title||'');
    const rawLooksWriteflow=rawTitle.startsWith('请【')&&rawTitle.includes('接手这个写作任务');
    if(displayTitle){
      const parts=displayTitle.split(/[｜|]/);
      if(parts.length>=2){
        const label=normalizeTaijiSessionTitle(parts.shift());
        const topic=taijiCompactTopic(parts.join('｜'));
        return topic||taijiCompactTopic(label)||'写作项目';
      }
      return taijiCompactTopic(displayTitle)||taijiClampSessionTitle(displayTitle,32);
    }
    if(writeflowTitle||rawLooksWriteflow){
      const topic=taijiCompactTopic(writeflowTitle||rawTitle);
      return topic||'写作项目';
    }
    if(taijiSessionTitleIsGeneric(rawTitle)) return '普通会话';
    if(/^(你|您好?|hi|hello|hey|晚|早|上午好|下午好|晚上好)$/i.test(rawTitle)) return '日常问候';
    return taijiCompactTopic(rawTitle)||taijiClampSessionTitle(rawTitle,32)||'未命名会话';
  }

  function taijiSessionKind(session){
    if(!session) return '问答';
    const rawTitle=normalizeTaijiSessionTitle(session.title||session.name||'');
    const displayTitle=normalizeTaijiSessionTitle(session.display_title||'');
    const writeflowTitle=normalizeTaijiSessionTitle(session.writeflow_title||'');
    const displayPrefix=normalizeTaijiSessionTitle((displayTitle.split(/[｜|]/)[0]||''));
    const rawLooksWriteflow=rawTitle.startsWith('请【')&&rawTitle.includes('接手这个写作任务');
    const rawLooksExpertTeam=/^召唤[^：:\n]{0,64}专家团[：:]/.test(rawTitle);
    const displayLooksWriteflow=['内容创作','深度研究','写作团队','专家团'].includes(displayPrefix);
    const text=[displayTitle,writeflowTitle,rawTitle].filter(Boolean).join(' ');
    if(session.writeflow_team_id||writeflowTitle||rawLooksWriteflow||rawLooksExpertTeam||displayLooksWriteflow||/接手这个写作任务|workflow-producer/.test(text)){
      return '专家团';
    }
    return '问答';
  }

  function taijiSessionWorktreeLabel(session){
    return String(session&&(session.worktree_label||session.worktree_branch)||'Worktree');
  }

  function taijiSessionFullTitle(session){
    if(!session) return '未命名会话';
    const displayTitle=normalizeTaijiSessionTitle(session.display_title||'');
    if(displayTitle) return displayTitle;
    const writeflowTitle=normalizeTaijiSessionTitle(session.writeflow_title||'');
    const rawTitle=normalizeTaijiSessionTitle(session.title||session.name||'');
    if(writeflowTitle) return `${taijiWriteflowTeamLabel(session,rawTitle)}｜${taijiCompactTopic(writeflowTitle)||writeflowTitle}`;
    if(rawTitle) return rawTitle;
    return '未命名会话';
  }

  function taijiSessionSearchText(session){
    return [
      taijiSessionDisplayTitle(session),
      session&&session.display_title,
      session&&session.writeflow_title,
      session&&session.title,
      session&&session.session_id,
      session&&session.workspace,
      session&&session.project_name,
      session&&session.source_label,
      session&&session.profile
    ].filter(Boolean).join(' ').toLowerCase();
  }

  function taijiSafeProjectColor(project){
    const color=String(project&&project.color||'').trim();
    return /^#[0-9a-fA-F]{3,8}$/.test(color)?color:'';
  }

  function activeProjectId(){
    if(!state.sessionFilter||!state.sessionFilter.startsWith('project:')) return null;
    return state.sessionFilter.slice('project:'.length)||null;
  }

  function projectById(projectId){
    if(!projectId) return null;
    return state.projects.find(project=>project&&project.project_id===projectId)||null;
  }

  function projectNameById(projectId){
    const project=projectById(projectId);
    return project&&(project.name||'未命名分组');
  }

  function sessionById(sid){
    return state.sessions.find(session=>session&&session.session_id===sid)||null;
  }

  function closeProjectMenu(){
    document.querySelectorAll('.taiji-project-menu').forEach(menu=>menu.remove());
    if(projectMenuClickClose){
      document.removeEventListener('click',projectMenuClickClose);
      projectMenuClickClose=null;
    }
    if(projectMenuKeyClose){
      document.removeEventListener('keydown',projectMenuKeyClose);
      projectMenuKeyClose=null;
    }
  }

  function unbindProjectPanelClose(){
    if(projectPanelClickClose){
      document.removeEventListener('click',projectPanelClickClose);
      projectPanelClickClose=null;
    }
    if(projectPanelKeyClose){
      document.removeEventListener('keydown',projectPanelKeyClose);
      projectPanelKeyClose=null;
    }
  }

  function bindProjectPanelClose(){
    unbindProjectPanelClose();
    projectPanelClickClose=event=>{
      const host=$('taijiProjectFilters');
      if(host&&host.contains(event.target)) return;
      closeProjectPanel(true);
    };
    projectPanelKeyClose=event=>{
      if(event.key==='Escape') closeProjectPanel(true);
    };
    setTimeout(()=>{
      document.addEventListener('click',projectPanelClickClose);
      document.addEventListener('keydown',projectPanelKeyClose);
    },0);
  }

  function closeProjectPanel(render=false){
    state.projectPanelOpen=false;
    unbindProjectPanelClose();
    if(render) renderProjectFilters();
  }

  function openProjectPanel(){
    state.projectPanelOpen=true;
    closeSessionActionMenu();
    closeProjectMenu();
    renderProjectFilters();
    bindProjectPanelClose();
    setTimeout(()=>{
      const input=$('taijiProjectSearch');
      if(input) input.focus();
    },0);
  }

  function toggleProjectPanel(event){
    if(event){
      event.preventDefault();
      event.stopPropagation();
    }
    if(state.projectPanelOpen){
      closeProjectPanel(true);
    }else{
      openProjectPanel();
    }
  }

  function closeSessionActionMenu(){
    document.querySelectorAll('.taiji-session-action-menu').forEach(menu=>menu.remove());
    if(sessionActionMenuAnchor){
      sessionActionMenuAnchor.classList.remove('is-active');
      sessionActionMenuAnchor.setAttribute('aria-expanded','false');
      sessionActionMenuAnchor=null;
    }
    if(sessionActionMenuClickClose){
      document.removeEventListener('click',sessionActionMenuClickClose);
      sessionActionMenuClickClose=null;
    }
    if(sessionActionMenuKeyClose){
      document.removeEventListener('keydown',sessionActionMenuKeyClose);
      sessionActionMenuKeyClose=null;
    }
  }

  function projectSessionCount(projectId){
    return state.sessions.filter(session=>session&&session.project_id===projectId).length;
  }

  function filterProjectsForPanel(){
    const q=state.projectSearch.trim().toLowerCase();
    if(!q) return state.projects.slice();
    return state.projects.filter(project=>[
      project&&project.name,
      project&&project.project_id
    ].filter(Boolean).join(' ').toLowerCase().includes(q));
  }

  function activeProjectName(){
    const projectId=activeProjectId();
    return projectId?projectNameById(projectId):'';
  }

  function renderProjectPanel(){
    const panel=$('taijiProjectPanel');
    if(!panel) return;
    const list=panel.querySelector('[data-taiji-project-list]');
    const count=panel.querySelector('[data-taiji-project-count]');
    if(count) count.textContent=`${state.projects.length} 个分组`;
    if(!list) return;
    const activeId=activeProjectId();
    const projects=filterProjectsForPanel();
    if(!projects.length){
      list.innerHTML=`<div class="taiji-project-panel-empty">${state.projectSearch?'没有匹配分组':'暂无分组'}</div>`;
      return;
    }
    list.innerHTML=projects.map(project=>{
      if(!project||!project.project_id) return '';
      const projectId=escapeHtml(project.project_id);
      const name=escapeHtml(project.name||'未命名分组');
      const color=taijiSafeProjectColor(project);
      const countLabel=projectSessionCount(project.project_id);
      const active=project.project_id===activeId?' is-active':'';
      const selected=project.project_id===activeId?'true':'false';
      const dot=color?`<span class="taiji-project-dot" style="background:${color}"></span>`:'<span class="taiji-project-dot"></span>';
      const editIcon=typeof li==='function'?li('pencil',14):'';
      const trashIcon=typeof li==='function'?li('trash-2',14):'';
      return `<div class="taiji-project-panel-row${active}" role="option" tabindex="0" aria-selected="${selected}" data-taiji-project-action="select" data-project-id="${projectId}" title="${name}">
        <span class="taiji-project-panel-main">${dot}<span class="taiji-project-panel-name">${name}</span><span class="taiji-project-panel-count">${countLabel}</span></span>
        <span class="taiji-project-panel-actions" aria-label="分组操作">
          <button class="taiji-project-panel-action" type="button" data-taiji-project-action="rename" data-project-id="${projectId}" aria-label="重命名分组：${name}" title="重命名分组">${editIcon}<span>重命名分组</span></button>
          <button class="taiji-project-panel-action is-danger" type="button" data-taiji-project-action="delete" data-project-id="${projectId}" aria-label="删除分组：${name}" title="删除分组">${trashIcon}<span>删除分组</span></button>
        </span>
      </div>`;
    }).join('');
  }

  function buildProjectMenuButton(label,{active=false,color='',create=false}={},onClick){
    const btn=document.createElement('button');
    btn.type='button';
    btn.className='taiji-project-menu-item'+(active?' is-active':'')+(create?' is-create':'');
    if(color){
      const dot=document.createElement('span');
      dot.className='taiji-project-dot';
      dot.style.background=color;
      btn.appendChild(dot);
    }
    const text=document.createElement('span');
    text.textContent=label;
    btn.appendChild(text);
    btn.addEventListener('click',onClick);
    return btn;
  }

  function updateSessionProject(sid,projectId){
    state.sessions=state.sessions.map(session=>{
      if(!session||session.session_id!==sid) return session;
      return {...session,project_id:projectId||null};
    });
    const s=appState();
    if(s&&s.session&&s.session.session_id===sid) s.session.project_id=projectId||null;
  }

  async function moveSessionToProject(session,projectId,projectName){
    const apiFn=globalFn('api');
    const toastFn=globalFn('showToast');
    const renderListFn=globalFn('renderSessionList');
    if(!apiFn||!session||!session.session_id) return false;
    try{
      const res=await apiFn('/api/session/move',{method:'POST',body:JSON.stringify({session_id:session.session_id,project_id:projectId||null})});
      const nextProjectId=res&&res.session&&Object.prototype.hasOwnProperty.call(res.session,'project_id')
        ? res.session.project_id
        : (projectId||null);
      updateSessionProject(session.session_id,nextProjectId);
      if(renderListFn) await renderListFn();
      await refreshSessions();
      if(toastFn) toastFn(nextProjectId?`已加入分组：${projectName||projectNameById(nextProjectId)||'未命名分组'}`:'已移出分组');
      return true;
    }catch(error){
      if(toastFn) toastFn('分组调整失败：'+(error&&error.message||error),3000,'error');
      return false;
    }
  }

  async function createProjectAndMoveSession(session){
    const promptFn=globalFn('showPromptDialog');
    const apiFn=globalFn('api');
    const toastFn=globalFn('showToast');
    if(!promptFn||!apiFn){
      if(toastFn) toastFn('分组创建功能暂不可用',2500,'error');
      return false;
    }
    const name=await promptFn({
      message:'请输入新分组名称',
      confirmLabel:'创建',
      placeholder:'分组名称'
    });
    if(!name||!String(name).trim()) return false;
    const colors=['#13b6c8','#2f80ed','#38bdf8','#22c55e','#f59e0b','#ef4444'];
    const color=colors[state.projects.length%colors.length];
    try{
      const body={name:String(name).trim(),color};
      if(session&&session.profile) body.profile=session.profile;
      const res=await apiFn('/api/projects/create',{method:'POST',body:JSON.stringify(body)});
      if(res&&res.project&&res.project.project_id){
        state.projects=state.projects.filter(project=>project&&project.project_id!==res.project.project_id).concat(res.project);
        return moveSessionToProject(session,res.project.project_id,res.project.name);
      }
      return false;
    }catch(error){
      if(toastFn) toastFn('分组创建失败：'+(error&&error.message||error),3000,'error');
      return false;
    }
  }

  async function renameProjectFromHome(projectId){
    const project=projectById(projectId);
    const promptFn=globalFn('showPromptDialog');
    const apiFn=globalFn('api');
    const toastFn=globalFn('showToast');
    if(!project||!project.project_id) return false;
    if(!promptFn||!apiFn){
      if(toastFn) toastFn('重命名分组功能暂不可用',2500,'error');
      return false;
    }
    const next=await promptFn({
      title:'重命名分组',
      message:'输入新的分组名称',
      value:project.name||'未命名分组',
      placeholder:'分组名称',
      confirmLabel:'保存',
      selectAll:true
    });
    const name=String(next||'').trim();
    if(!name) return false;
    try{
      const res=await apiFn('/api/projects/rename',{method:'POST',body:JSON.stringify({project_id:project.project_id,name})});
      const nextProject=res&&res.project?res.project:{...project,name};
      state.projects=state.projects.map(item=>item&&item.project_id===project.project_id?{...item,...nextProject}:item);
      renderProjectFilters();
      renderRecentSessions();
      if(toastFn) toastFn('分组已重命名');
      return true;
    }catch(error){
      if(toastFn) toastFn('重命名分组失败：'+(error&&error.message||error),3000,'error');
      return false;
    }
  }

  async function deleteProjectFromHome(projectId){
    const project=projectById(projectId);
    const confirmFn=globalFn('showConfirmDialog');
    const apiFn=globalFn('api');
    const toastFn=globalFn('showToast');
    const renderListFn=globalFn('renderSessionList');
    if(!project||!project.project_id) return false;
    if(!confirmFn||!apiFn){
      if(toastFn) toastFn('删除分组功能暂不可用',2500,'error');
      return false;
    }
    const ok=await confirmFn({
      title:'删除分组',
      message:`确定删除“${project.name||'未命名分组'}”吗？分组内会话会回到未分组。`,
      confirmLabel:'删除',
      cancelLabel:'取消',
      danger:true,
      focusCancel:true
    });
    if(!ok) return false;
    try{
      await apiFn('/api/projects/delete',{method:'POST',body:JSON.stringify({project_id:project.project_id})});
      state.projects=state.projects.filter(item=>item&&item.project_id!==project.project_id);
      state.sessions=state.sessions.map(session=>session&&session.project_id===project.project_id?{...session,project_id:null}:session);
      if(state.sessionFilter===`project:${project.project_id}`) state.sessionFilter=SESSION_FILTERS.all;
      if(renderListFn) await renderListFn();
      await refreshSessions();
      if(toastFn) toastFn('分组已删除');
      return true;
    }catch(error){
      if(toastFn) toastFn('删除分组失败：'+(error&&error.message||error),3000,'error');
      return false;
    }
  }

  function selectProjectFromPanel(projectId){
    if(projectId&&projectById(projectId)){
      state.sessionFilter=`project:${projectId}`;
    }else{
      state.sessionFilter=SESSION_FILTERS.all;
    }
    closeProjectPanel(false);
    renderRecentSessions();
  }

  function showProjectMenuForSession(sid,anchorEl,event){
    if(event){
      event.preventDefault();
      event.stopPropagation();
    }
    const session=sessionById(sid);
    if(!session||!anchorEl) return;
    closeProjectMenu();
    const menu=document.createElement('div');
    menu.className='taiji-project-menu';
    menu.setAttribute('role','menu');
    menu.appendChild(buildProjectMenuButton('未分组',{active:!session.project_id},async ()=>{
      closeProjectMenu();
      await moveSessionToProject(session,null,'');
    }));
    for(const project of state.projects){
      if(!project||!project.project_id) continue;
      const color=taijiSafeProjectColor(project);
      menu.appendChild(buildProjectMenuButton(project.name||'未命名分组',{
        active:session.project_id===project.project_id,
        color
      },async ()=>{
        closeProjectMenu();
        await moveSessionToProject(session,project.project_id,project.name||'未命名分组');
      }));
    }
    menu.appendChild(buildProjectMenuButton('新建分组并加入',{create:true},async ()=>{
      closeProjectMenu();
      await createProjectAndMoveSession(session);
    }));
    document.body.appendChild(menu);
    const rect=anchorEl.getBoundingClientRect();
    const width=Math.min(240,Math.max(172,menu.offsetWidth||172));
    const left=Math.max(8,Math.min(window.innerWidth-width-8,rect.right-width));
    const spaceBelow=window.innerHeight-rect.bottom;
    menu.style.left=left+'px';
    menu.style.position='fixed';
    menu.style.zIndex='9999';
    if(spaceBelow<180&&rect.top>180){
      menu.style.bottom=(window.innerHeight-rect.top+6)+'px';
      menu.style.top='auto';
    }else{
      menu.style.top=(rect.bottom+6)+'px';
      menu.style.bottom='auto';
    }
    projectMenuClickClose=e=>{
      if(!menu.contains(e.target)&&e.target!==anchorEl) closeProjectMenu();
    };
    projectMenuKeyClose=e=>{
      if(e.key==='Escape') closeProjectMenu();
    };
    setTimeout(()=>{
      document.addEventListener('click',projectMenuClickClose);
      document.addEventListener('keydown',projectMenuKeyClose);
    },0);
  }

  function buildSessionActionMenuItem(label,{icon='',danger=false,attr='',sid=''}={},onClick){
    const btn=document.createElement('button');
    btn.type='button';
    btn.className='taiji-session-action-menu-item'+(danger?' is-danger':'');
    btn.setAttribute('role','menuitem');
    if(attr) btn.setAttribute(attr,'');
    if(sid) btn.dataset.sessionId=sid;
    btn.innerHTML=`<span class="taiji-session-action-menu-icon" aria-hidden="true">${icon}</span><span>${escapeHtml(label)}</span>`;
    btn.addEventListener('click',async event=>{
      event.preventDefault();
      event.stopPropagation();
      await onClick(event);
    });
    return btn;
  }

  async function renameSessionFromRecent(session){
    if(!session||!session.session_id) return false;
    const promptFn=globalFn('showPromptDialog');
    const apiFn=globalFn('api');
    const toastFn=globalFn('showToast');
    if(!promptFn||!apiFn){
      if(toastFn) toastFn('重命名功能暂不可用',2500,'error');
      return false;
    }
    const currentTitle=taijiSessionDisplayTitle(session);
    const next=await promptFn({
      title:'重命名会话',
      message:'输入新的会话标题',
      value:currentTitle,
      placeholder:'会话标题',
      confirmLabel:'保存',
      selectAll:true
    });
    const nextTitle=normalizeTaijiSessionTitle(next||'');
    if(!nextTitle) return false;
    try{
      await apiFn('/api/session/rename',{method:'POST',body:JSON.stringify({session_id:session.session_id,title:nextTitle})});
      session.title=nextTitle;
      session.display_title=nextTitle;
      const s=appState();
      if(s&&s.session&&s.session.session_id===session.session_id){
        s.session.title=nextTitle;
        s.session.display_title=nextTitle;
      }
      const renderListFn=globalFn('renderSessionList');
      if(renderListFn) await renderListFn();
      await refreshSessions();
      scheduleSync();
      if(toastFn) toastFn('已重命名会话');
      return true;
    }catch(error){
      if(toastFn) toastFn('重命名失败：'+(error&&error.message||error),3000,'error');
      return false;
    }
  }

  function showSessionActionMenu(sid,anchorEl,event){
    if(event){
      event.preventDefault();
      event.stopPropagation();
    }
    const session=sessionById(sid);
    if(!session||!anchorEl) return;
    closeSessionActionMenu();
    closeProjectMenu();
    closeProjectPanel(false);
    const sessionId=String(sid);
    const editIcon=typeof li==='function'?li('pencil',15):'';
    const folderIcon=typeof li==='function'?li('folder',15):'';
    const trashIcon=typeof li==='function'?li('trash-2',15):'';
    const menu=document.createElement('div');
    menu.className='taiji-session-action-menu';
    menu.setAttribute('role','menu');
    menu.setAttribute('aria-label','会话操作');
    menu.appendChild(buildSessionActionMenuItem('重命名',{icon:editIcon,attr:'data-taiji-session-rename',sid:sessionId},async ()=>{
      closeSessionActionMenu();
      await renameSessionFromRecent(session);
    }));
    menu.appendChild(buildSessionActionMenuItem('分组',{icon:folderIcon,attr:'data-taiji-session-move',sid:sessionId},async event=>{
      closeSessionActionMenu();
      showProjectMenuForSession(sid,anchorEl,event);
    }));
    if(session.is_worktree){
      const worktreeLabel=taijiSessionWorktreeLabel(session);
      menu.appendChild(buildSessionActionMenuItem(`移除 Worktree（${worktreeLabel}）`,{
        icon:trashIcon,
        danger:true,
        attr:'data-taiji-session-worktree-remove',
        sid:sessionId
      },async ()=>{
        closeSessionActionMenu();
        const removeWorktreeFn=globalFn('removeWorktree');
        const toastFn=globalFn('showToast');
        if(!removeWorktreeFn){
          if(toastFn) toastFn('Worktree 移除功能暂不可用',2500,'error');
          return;
        }
        await removeWorktreeFn(session);
        await refreshSessions();
        scheduleSync();
      }));
    }
    menu.appendChild(buildSessionActionMenuItem('删除',{icon:trashIcon,danger:true,attr:'data-taiji-session-delete',sid:sessionId},async event=>{
      closeSessionActionMenu();
      await window.taijiHomeDeleteSession(sid,event);
    }));
    document.body.appendChild(menu);
    sessionActionMenuAnchor=anchorEl;
    anchorEl.classList.add('is-active');
    anchorEl.setAttribute('aria-expanded','true');
    const rect=anchorEl.getBoundingClientRect();
    const width=Math.min(220,Math.max(168,menu.offsetWidth||168));
    const left=Math.max(8,Math.min(window.innerWidth-width-8,rect.right-width));
    const spaceBelow=window.innerHeight-rect.bottom;
    menu.style.left=left+'px';
    menu.style.position='fixed';
    menu.style.zIndex='9999';
    if(spaceBelow<132&&rect.top>132){
      menu.style.bottom=(window.innerHeight-rect.top+6)+'px';
      menu.style.top='auto';
    }else{
      menu.style.top=(rect.bottom+6)+'px';
      menu.style.bottom='auto';
    }
    sessionActionMenuClickClose=e=>{
      if(!menu.contains(e.target)&&e.target!==anchorEl) closeSessionActionMenu();
    };
    sessionActionMenuKeyClose=e=>{
      if(e.key==='Escape') closeSessionActionMenu();
    };
    setTimeout(()=>{
      document.addEventListener('click',sessionActionMenuClickClose);
      document.addEventListener('keydown',sessionActionMenuKeyClose);
    },0);
  }

  function renderProjectFilters(){
    const container=$('taijiProjectFilters');
    if(!container) return;
    const projectCount=state.projects.length;
    const activeName=activeProjectName();
    const active=activeName?' is-active':'';
    const label=escapeHtml(`分组 ${projectCount}`);
    const title=escapeHtml(activeName?`当前分组：${activeName}`:'会话分组');
    const expanded=state.projectPanelOpen?'true':'false';
    const hidden=state.projectPanelOpen?'':' hidden';
    const searchValue=escapeHtml(state.projectSearch);
    const chevron=typeof li==='function'?li(state.projectPanelOpen?'chevron-up':'chevron-down',14):'';
    const searchIcon=typeof li==='function'?li('search',14):'';
    const plusIcon=typeof li==='function'?li('plus',14):'';
    container.innerHTML=`<button class="taiji-project-filter-trigger${active}" id="taijiProjectFilterTrigger" type="button" aria-haspopup="dialog" aria-expanded="${expanded}" aria-controls="taijiProjectPanel" data-taiji-project-action="toggle" title="${title}">
      <span id="taijiProjectFilterLabel">${label}</span><span class="taiji-project-filter-trigger-icon" aria-hidden="true">${chevron}</span>
    </button>
    <div class="taiji-project-panel" id="taijiProjectPanel" role="dialog" aria-label="会话分组"${hidden}>
      <div class="taiji-project-panel-head"><strong>会话分组</strong><span data-taiji-project-count>${projectCount} 个分组</span></div>
      <label class="taiji-project-panel-search" aria-label="搜索分组"><span aria-hidden="true">${searchIcon}</span><input id="taijiProjectSearch" type="search" placeholder="搜索分组" value="${searchValue}" autocomplete="off"></label>
      <div class="taiji-project-panel-list" role="listbox" aria-label="分组列表" data-taiji-project-list></div>
      <button class="taiji-project-panel-create" type="button" data-taiji-project-action="create">${plusIcon}<span>新建分组</span></button>
    </div>`;
    renderProjectPanel();
  }

  function syncSessionFilterButtons(){
    document.querySelectorAll('[data-taiji-session-filter]').forEach(btn=>{
      btn.classList.toggle('is-active',(btn.dataset.taijiSessionFilter||'all')===state.sessionFilter);
    });
  }

  function taijiViewAllLabel(totalCount=0,recentCount=0){
    return state.showAllSessions?`查看最近 ${recentCount} 个`:`查看全部 ${totalCount} 个会话`;
  }

  function syncViewAllButton(totalCount=0,recentCount=0){
    const btn=document.querySelector('.taiji-view-all');
    if(!btn) return;
    const hasToggle=totalCount>recentCount;
    const label=taijiViewAllLabel(totalCount,recentCount);
    btn.hidden=!hasToggle;
    btn.disabled=!hasToggle;
    btn.innerHTML=`${label} <span aria-hidden="true">›</span>`;
    btn.setAttribute('aria-label',label);
    btn.setAttribute('aria-pressed',state.showAllSessions?'true':'false');
    btn.setAttribute('aria-controls','taijiSessionGroups');
    btn.setAttribute('aria-disabled',hasToggle?'false':'true');
    btn.title=label;
  }

  function activeFilterLabel(){
    if(state.sessionFilter===SESSION_FILTERS.ungrouped) return '未分组';
    const projectName=activeProjectName();
    return projectName||'';
  }

  function renderFilterStatus(visibleCount=0){
    const status=$('taijiFilterStatus');
    if(!status) return;
    const label=activeFilterLabel();
    if(!label){
      status.hidden=true;
      status.innerHTML='';
      return;
    }
    const count=Math.max(0,Number(visibleCount)||0);
    const safeLabel=escapeHtml(label);
    status.hidden=false;
    status.innerHTML=`<span class="taiji-filter-status-dot" aria-hidden="true"></span><span class="taiji-filter-status-label">当前分组：<strong>${safeLabel}</strong></span><span class="taiji-filter-status-count">${count} 个会话</span><button class="taiji-filter-status-clear" type="button" data-taiji-clear-session-filter aria-label="清除当前分组筛选">清除</button>`;
  }

  function normalizeProjectFilter(){
    if(!state.sessionFilter||!state.sessionFilter.startsWith('project:')) return;
    const projectId=state.sessionFilter.slice('project:'.length);
    if(!state.projects.some(project=>project&&project.project_id===projectId)){
      state.sessionFilter=SESSION_FILTERS.all;
    }
  }

  function filteredSessions(){
    const q=state.search.trim().toLowerCase();
    return state.sessions.filter(session=>{
      if(!session||!session.session_id) return false;
      if(state.sessionFilter&&state.sessionFilter.startsWith('project:')){
        const projectId=state.sessionFilter.slice('project:'.length);
        if(session.project_id!==projectId) return false;
      }else if(state.sessionFilter===SESSION_FILTERS.ungrouped&&session.project_id) return false;
      if(q){
        if(!taijiSessionSearchText(session).includes(q)) return false;
      }
      return true;
    });
  }

  function sessionAppearsInRecentPreview(session){
    if(!session) return false;
    const s=appState();
    return (session.message_count||0)>0||session.active_stream_id||session.pending_user_message||(s&&s.session&&s.session.session_id===session.session_id);
  }

  function recentPreviewSessions(sessions){
    return sessions.filter(sessionAppearsInRecentPreview).slice(0,RECENT_SESSION_PREVIEW_LIMIT);
  }

  function renderRecentSessions(){
    const container=$('taijiSessionGroups');
    if(!container) return;
    renderProjectFilters();
    syncSessionFilterButtons();
    const allSessions=filteredSessions();
    const recentSessions=recentPreviewSessions(allSessions);
    const visibleSessions=state.showAllSessions?allSessions:recentSessions;
    syncViewAllButton(allSessions.length,recentSessions.length);
    renderFilterStatus(allSessions.length);
    const groups=['今天','昨天','本周','更早'];
    const s=appState();
    const activeSid=s&&s.session&&s.session.session_id;
    if(!visibleSessions.length){
      container.innerHTML=allSessions.length&&!state.showAllSessions?'<div class="taiji-session-empty">暂无最近会话</div>':'<div class="taiji-session-empty">暂无匹配会话</div>';
      syncShellState();
      return;
    }
    const html=groups.map(group=>{
      const items=visibleSessions.filter(session=>groupNameForSession(session)===group);
      if(!items.length) return '';
      const rows=items.map(session=>{
        const title=escapeHtml(taijiSessionDisplayTitle(session));
        const fullTitle=escapeHtml(taijiSessionFullTitle(session));
        const sid=escapeHtml(session.session_id);
        const time=escapeHtml(sessionTimeLabel(session));
        const kind=taijiSessionKind(session);
        const kindCode=kind==='专家团'?'expert':'qa';
        const kindLabel=escapeHtml(kind);
        const badge=session.is_streaming||session.active_stream_id?'<span class="taiji-session-live">运行</span>':'';
        const worktreeLabel=escapeHtml(taijiSessionWorktreeLabel(session));
        const worktreeBadge=session.is_worktree?`<span class="taiji-session-worktree" aria-label="Worktree：${worktreeLabel}" title="Worktree：${worktreeLabel}">WT</span>`:'';
        const moreLabel=escapeHtml(`更多操作：${taijiSessionFullTitle(session)}`);
        const moreIcon='<span class="taiji-session-more-dots" aria-hidden="true">...</span>';
        return `<div class="taiji-session-row${activeSid===session.session_id?' is-active':''}" data-session-id="${sid}" title="${fullTitle}"><button class="taiji-session-open" type="button" data-taiji-session-open data-session-id="${sid}" title="${fullTitle}" aria-label="${fullTitle}"><span class="taiji-session-kind" data-kind="${kindCode}">${kindLabel}</span>${worktreeBadge}${badge}<span class="taiji-session-title">${title}</span><span class="taiji-session-meta"><time class="taiji-session-time">${time}</time></span></button><button class="taiji-session-more" type="button" data-taiji-session-more data-session-id="${sid}" title="${moreLabel}" aria-label="${moreLabel}" aria-haspopup="menu" aria-expanded="false">${moreIcon}</button></div>`;
      }).join('');
      return `<section class="taiji-session-group" aria-label="${group}"><header><span>${group}</span><span aria-hidden="true">⌃</span></header><div class="taiji-session-card">${rows}</div></section>`;
    }).join('');
    container.innerHTML=html||'<div class="taiji-session-empty">暂无匹配会话</div>';
    syncShellState();
  }

  function scheduleSessionRefresh(delay=120){
    if(state.refreshTimer) clearTimeout(state.refreshTimer);
    state.refreshTimer=setTimeout(()=>{
      state.refreshTimer=0;
      refreshSessions();
    },delay);
  }

  async function refreshSessions(){
    const apiFn=globalFn('api');
    if(!apiFn) return;
    if(state.refreshInFlight) return state.refreshInFlight;
    state.refreshInFlight=(async()=>{
      try{
        const [sessionsData,projectsData]=await Promise.all([
          apiFn('/api/sessions',{timeoutToast:false}),
          apiFn('/api/projects',{timeoutToast:false}).catch(()=>({projects:[]}))
        ]);
        state.sessions=Array.isArray(sessionsData&&sessionsData.sessions)?sessionsData.sessions:[];
        state.projects=Array.isArray(projectsData&&projectsData.projects)?projectsData.projects:[];
        state.sessions.sort((a,b)=>sessionTimestamp(b)-sessionTimestamp(a));
        normalizeProjectFilter();
        renderRecentSessions();
      }catch(error){
        const container=$('taijiSessionGroups');
        if(container) container.innerHTML='<div class="taiji-session-empty">会话加载失败</div>';
        console.warn('[taiji-home] refreshSessions failed',error);
      }finally{
        state.refreshInFlight=null;
      }
    })();
    return state.refreshInFlight;
  }

  function bindRecentControls(){
    const input=$('taijiSessionSearch');
    if(input&&!input.__taijiBound){
      input.__taijiBound=true;
      input.addEventListener('input',()=>{
        state.search=input.value||'';
        renderRecentSessions();
      });
    }
    const groups=$('taijiSessionGroups');
    if(groups&&!groups.__taijiBound){
      groups.__taijiBound=true;
      groups.addEventListener('click',event=>{
        const moreBtn=event.target&&event.target.closest?event.target.closest('[data-taiji-session-more]'):null;
        if(moreBtn&&groups.contains(moreBtn)){
          showSessionActionMenu(moreBtn.dataset.sessionId,moreBtn,event);
          return;
        }
        const openBtn=event.target&&event.target.closest?event.target.closest('[data-taiji-session-open]'):null;
        if(openBtn&&groups.contains(openBtn)){
          window.taijiHomeLoadSession(openBtn.dataset.sessionId);
        }
      });
    }
    const filterStatus=$('taijiFilterStatus');
    if(filterStatus&&!filterStatus.__taijiBound){
      filterStatus.__taijiBound=true;
      filterStatus.addEventListener('click',event=>{
        const btn=event.target&&event.target.closest?event.target.closest('[data-taiji-clear-session-filter]'):null;
        if(!btn||!filterStatus.contains(btn)) return;
        event.preventDefault();
        closeProjectPanel(false);
        state.sessionFilter=SESSION_FILTERS.all;
        renderRecentSessions();
      });
    }
    const filterRow=document.querySelector('.taiji-filter-row');
    if(filterRow&&!filterRow.__taijiBound){
      filterRow.__taijiBound=true;
      filterRow.addEventListener('click',async event=>{
        const projectAction=event.target&&event.target.closest?event.target.closest('[data-taiji-project-action]'):null;
        if(projectAction&&filterRow.contains(projectAction)){
          const action=projectAction.dataset.taijiProjectAction;
          const projectId=projectAction.dataset.projectId||projectAction.closest('[data-project-id]')&&projectAction.closest('[data-project-id]').dataset.projectId||'';
          event.preventDefault();
          event.stopPropagation();
          if(action==='toggle'){
            toggleProjectPanel(event);
          }else if(action==='select'){
            selectProjectFromPanel(projectId);
          }else if(action==='rename'){
            await renameProjectFromHome(projectId);
          }else if(action==='delete'){
            await deleteProjectFromHome(projectId);
          }else if(action==='create'){
            await createProject();
          }
          return;
        }
        const btn=event.target&&event.target.closest?event.target.closest('[data-taiji-session-filter]'):null;
        if(!btn||!filterRow.contains(btn)) return;
        closeProjectPanel(false);
        state.sessionFilter=btn.dataset.taijiSessionFilter||SESSION_FILTERS.all;
        renderRecentSessions();
      });
      filterRow.addEventListener('input',event=>{
        const input=event.target&&event.target.closest?event.target.closest('#taijiProjectSearch'):null;
        if(!input||!filterRow.contains(input)) return;
        state.projectSearch=input.value||'';
        renderProjectPanel();
      });
      filterRow.addEventListener('keydown',event=>{
        const row=event.target&&event.target.closest?event.target.closest('.taiji-project-panel-row[data-taiji-project-action="select"]'):null;
        if(!row||!filterRow.contains(row)) return;
        if(event.key!=='Enter'&&event.key!==' ') return;
        event.preventDefault();
        selectProjectFromPanel(row.dataset.projectId||'');
      });
    }
  }

  function bindQuickActions(){
    document.querySelectorAll('.taiji-quick-card').forEach(btn=>{
      if(btn.__taijiBound) return;
      btn.__taijiBound=true;
      btn.addEventListener('click',()=>scheduleSync());
    });
  }

  function setPrompt(text){
    const switchPanelFn=globalFn('switchPanel');
    if(switchPanelFn) switchPanelFn('chat');
    const msg=$('msg');
    if(!msg) return;
    msg.value=text||'';
    msg.dispatchEvent(new Event('input',{bubbles:true}));
    const autoResizeFn=globalFn('autoResize');
    const updateSendBtnFn=globalFn('updateSendBtn');
    if(autoResizeFn) autoResizeFn();
    if(updateSendBtnFn) updateSendBtnFn();
    msg.focus();
    scheduleSync();
  }

  async function createProject(){
    const promptFn=globalFn('showPromptDialog');
    const apiFn=globalFn('api');
    const toastFn=globalFn('showToast');
    if(!promptFn||!apiFn){
      if(toastFn) toastFn('项目创建功能暂不可用',2500,'error');
      return;
    }
    const name=await promptFn({
      message:'请输入新分组名称',
      confirmLabel:'创建',
      placeholder:'分组名称'
    });
    if(!name||!String(name).trim()) return;
    const colors=['#13b6c8','#2f80ed','#38bdf8','#22c55e','#f59e0b','#ef4444'];
    const color=colors[state.projects.length%colors.length];
    try{
      const res=await apiFn('/api/projects/create',{method:'POST',body:JSON.stringify({name:String(name).trim(),color})});
      if(res&&res.project&&res.project.project_id){
        state.projects=state.projects.filter(project=>project&&project.project_id!==res.project.project_id).concat(res.project);
        state.sessionFilter=`project:${res.project.project_id}`;
      }
      closeProjectPanel(false);
      const renderListFn=globalFn('renderSessionList');
      if(renderListFn) await renderListFn();
      await refreshSessions();
      if(toastFn) toastFn('分组已创建');
    }catch(error){
      if(toastFn) toastFn('分组创建失败',3000,'error');
    }
  }

  function init(){
    hydrateTaijiIcons();
    state.secondaryCollapsed=readSecondaryCollapsed();
    wrapLegacyHooks();
    bindRecentControls();
    bindQuickActions();
    scheduleSessionRefresh(0);
    scheduleSync();
  }

  window.taijiHomeSelectNav=function(btn){
    const panel=(btn&&btn.dataset&&btn.dataset.taijiPanel)||PANEL_BY_LABEL[(btn&&btn.textContent||'').trim()]||'chat';
    if(panel!=='chat'&&typeof isUiFeatureVisible==='function'&&!isUiFeatureVisible('nav',panel)){
      scheduleSync();
      return;
    }
    document.querySelectorAll('.taiji-nav-item').forEach(item=>item.classList.toggle('is-active',item===btn));
    const switchPanelFn=globalFn('switchPanel');
    if(switchPanelFn){
      const result=switchPanelFn(panel);
      if(result&&typeof result.finally==='function') result.finally(scheduleSync);
    }
    scheduleSync();
  };
  window.taijiHomeSetPrompt=setPrompt;
  window.taijiHomeNewChat=async function(){
    const newSessionFn=globalFn('newSession');
    const renderListFn=globalFn('renderSessionList');
    if(newSessionFn){
      const projectId=activeProjectId();
      await newSessionFn(true,{project_id:projectId});
      if(renderListFn) await renderListFn();
      await refreshSessions();
      scheduleSync();
    }
    const msg=$('msg');
    if(msg) msg.focus();
  };
  window.taijiHomeLoadSession=async function(sid){
    const openChatSessionFn=globalFn('openChatSession');
    const loadSessionFn=globalFn('loadSession');
    const switchPanelFn=globalFn('switchPanel');
    if(!sid) return;
    closeSessionActionMenu();
    closeProjectMenu();
    closeProjectPanel(false);
    if(openChatSessionFn){
      await openChatSessionFn(sid);
    }else if(loadSessionFn){
      await loadSessionFn(sid);
      if(switchPanelFn) switchPanelFn('chat');
    }
    scheduleSync();
  };
  window.taijiHomeDeleteSession=async function(sid,event){
    if(event){
      event.preventDefault();
      event.stopPropagation();
    }
    if(!sid) return false;
    const deleteSessionFn=globalFn('deleteSession');
    const renderListFn=globalFn('renderSessionList');
    const toastFn=globalFn('showToast');
    if(!deleteSessionFn){
      if(toastFn) toastFn('删除功能暂不可用',2500,'error');
      return false;
    }
    const deleted=await deleteSessionFn(sid);
    if(!deleted) return false;
    if(renderListFn) await renderListFn();
    await refreshSessions();
    scheduleSync();
    return true;
  };
  window.taijiHomeMoveSession=function(sid,event){
    const target=event&&event.target&&event.target.closest?event.target.closest('[data-taiji-session-move]'):null;
    showProjectMenuForSession(sid,target,event);
  };
  window.taijiHomeRefreshSessions=refreshSessions;
  window.taijiHomeToggleAllSessions=function(){
    const container=$('taijiSessionGroups');
    state.showAllSessions=!state.showAllSessions;
    renderRecentSessions();
    if(container) container.scrollTop=0;
  };
  window.taijiHomeToggleProjectPanel=toggleProjectPanel;
  window.taijiHomeCreateProject=createProject;
  window.taijiHomeSend=function(){
    const sendFn=globalFn('send');
    if(sendFn) sendFn();
  };
  window.taijiHomeToggleSecondary=function(){
    state.secondaryCollapsed=!state.secondaryCollapsed;
    writeSecondaryCollapsed(state.secondaryCollapsed);
    scheduleSync();
  };
  window.TaijiHomeController={
    init,
    syncShellState,
    renderSecondaryPanel,
    renderRecentSessions,
    refreshSessions,
    mountRealWorkspace
  };

  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',init,{once:true});
  }else{
    init();
  }
  window.addEventListener('resize',scheduleSync);
})();
