/* global S, api, switchPanel, renderSessionList, loadSession, newSession, send, autoResize, updateSendBtn, showPromptDialog, showToast, deleteSession */
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
  const SECONDARY_COLLAPSED_KEY='hermes-webui-taiji-secondary-collapsed';
  const state={
    mounted:false,
    sessions:[],
    projects:[],
    sessionFilter:'all',
    showAllSessions:false,
    search:'',
    secondaryCollapsed:false,
    refreshInFlight:null,
    refreshTimer:0,
    syncTimer:0,
    wrapped:false,
    panelPlaceholders:new Map()
  };
  let projectMenuClickClose=null;
  let projectMenuKeyClose=null;

  const $=id=>document.getElementById(id);
  const shell=()=>document.querySelector('.taiji-home-shell');
  const workspace=()=>document.querySelector('.taiji-main-workspace');
  const secondary=()=>document.querySelector('.taiji-secondary-panel');
  const secondaryHost=()=>document.getElementById('taijiPanelSecondaryHost');
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
    if(!root||!target||!main||state.mounted) return;
    const rightpanel=document.querySelector('.rightpanel');
    main.classList.add('taiji-real-main');
    target.appendChild(main);
    if(rightpanel){
      rightpanel.classList.add('taiji-workspace-drawer');
      target.appendChild(rightpanel);
    }
    state.mounted=true;
    syncShellState();
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
    text=text
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
        return `${label||taijiWriteflowTeamLabel(session,rawTitle)}｜${topic||'写作项目'}`;
      }
      return taijiCompactTopic(displayTitle)||taijiClampSessionTitle(displayTitle,32);
    }
    if(writeflowTitle||rawLooksWriteflow){
      const topic=taijiCompactTopic(writeflowTitle||rawTitle)||'写作项目';
      return `${taijiWriteflowTeamLabel(session,rawTitle)}｜${topic}`;
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
    const displayLooksWriteflow=['内容创作','深度研究','写作团队','专家团'].includes(displayPrefix);
    const text=[displayTitle,writeflowTitle,rawTitle].filter(Boolean).join(' ');
    if(session.writeflow_team_id||writeflowTitle||rawLooksWriteflow||displayLooksWriteflow||/接手这个写作任务|workflow-producer/.test(text)){
      return '专家团';
    }
    return '问答';
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

  function renderProjectFilters(){
    const container=$('taijiProjectFilters');
    if(!container) return;
    const html=state.projects.map(project=>{
      if(!project||!project.project_id) return '';
      const projectId=escapeHtml(project.project_id);
      const name=escapeHtml(project.name||'未命名分组');
      const color=taijiSafeProjectColor(project);
      const dot=color?`<span class="taiji-project-dot" style="background:${color}"></span>`:'';
      const active=state.sessionFilter===`project:${project.project_id}`?' is-active':'';
      return `<button class="taiji-filter taiji-project-filter${active}" type="button" data-taiji-session-filter="project:${projectId}" title="${name}">${dot}<span>${name}</span></button>`;
    }).join('');
    container.innerHTML=html;
  }

  function syncSessionFilterButtons(){
    document.querySelectorAll('[data-taiji-session-filter]').forEach(btn=>{
      btn.classList.toggle('is-active',(btn.dataset.taijiSessionFilter||'all')===state.sessionFilter);
    });
  }

  function taijiViewAllLabel(){
    return state.showAllSessions?'查看最近会话':'查看全部会话';
  }

  function syncViewAllButton(){
    const btn=document.querySelector('.taiji-view-all');
    if(!btn) return;
    const label=taijiViewAllLabel();
    btn.innerHTML=`${label} <span aria-hidden="true">›</span>`;
    btn.setAttribute('aria-label',label);
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
      if(!state.showAllSessions){
        const s=appState();
        return (session.message_count||0)>0||session.active_stream_id||session.pending_user_message||(s&&s.session&&s.session.session_id===session.session_id);
      }
      return true;
    });
  }

  function renderRecentSessions(){
    const container=$('taijiSessionGroups');
    if(!container) return;
    renderProjectFilters();
    syncSessionFilterButtons();
    syncViewAllButton();
    const sessions=filteredSessions();
    const groups=['今天','昨天','本周','更早'];
    const s=appState();
    const activeSid=s&&s.session&&s.session.session_id;
    if(!sessions.length){
      container.innerHTML='<div class="taiji-session-empty">暂无匹配会话</div>';
      syncShellState();
      return;
    }
    const html=groups.map(group=>{
      const items=sessions.filter(session=>groupNameForSession(session)===group);
      if(!items.length) return '';
      const rows=(state.showAllSessions?items:items.slice(0,18)).map(session=>{
        const title=escapeHtml(taijiSessionDisplayTitle(session));
        const fullTitle=escapeHtml(taijiSessionFullTitle(session));
        const sid=escapeHtml(session.session_id);
        const time=escapeHtml(sessionTimeLabel(session));
        const kind=taijiSessionKind(session);
        const kindCode=kind==='专家团'?'expert':'qa';
        const kindLabel=escapeHtml(kind);
        const badge=session.is_streaming||session.active_stream_id?'<span class="taiji-session-live">运行</span>':'';
        const projectName=projectNameById(session.project_id);
        const moveLabel=escapeHtml(projectName?`更改分组：${projectName}`:`加入分组：${taijiSessionFullTitle(session)}`);
        const deleteLabel=escapeHtml(`删除会话：${taijiSessionFullTitle(session)}`);
        const folderIcon=typeof li==='function'?li('folder',15):'□';
        return `<div class="taiji-session-row${activeSid===session.session_id?' is-active':''}" data-session-id="${sid}" title="${fullTitle}"><button class="taiji-session-open" type="button" data-taiji-session-open data-session-id="${sid}" title="${fullTitle}" aria-label="${fullTitle}"><span class="taiji-session-kind" data-kind="${kindCode}">${kindLabel}</span>${badge}<span class="taiji-session-title">${title}</span><span class="taiji-session-meta"><time class="taiji-session-time">${time}</time></span></button><span class="taiji-session-action-separator" aria-hidden="true"></span><button class="taiji-session-move${session.project_id?' has-project':''}" type="button" data-taiji-session-move data-session-id="${sid}" title="${moveLabel}" aria-label="${moveLabel}">${folderIcon}</button><button class="taiji-session-delete" type="button" data-taiji-session-delete data-session-id="${sid}" title="${deleteLabel}" aria-label="${deleteLabel}">${typeof li==='function'?li('trash-2',15):'×'}</button></div>`;
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
        const moveBtn=event.target&&event.target.closest?event.target.closest('[data-taiji-session-move]'):null;
        if(moveBtn&&groups.contains(moveBtn)){
          window.taijiHomeMoveSession(moveBtn.dataset.sessionId,event);
          return;
        }
        const deleteBtn=event.target&&event.target.closest?event.target.closest('[data-taiji-session-delete]'):null;
        if(deleteBtn&&groups.contains(deleteBtn)){
          window.taijiHomeDeleteSession(deleteBtn.dataset.sessionId,event);
          return;
        }
        const openBtn=event.target&&event.target.closest?event.target.closest('[data-taiji-session-open]'):null;
        if(openBtn&&groups.contains(openBtn)){
          window.taijiHomeLoadSession(openBtn.dataset.sessionId);
        }
      });
    }
    const filterRow=document.querySelector('.taiji-filter-row');
    if(filterRow&&!filterRow.__taijiBound){
      filterRow.__taijiBound=true;
      filterRow.addEventListener('click',event=>{
        const btn=event.target&&event.target.closest?event.target.closest('[data-taiji-session-filter]'):null;
        if(!btn||!filterRow.contains(btn)) return;
        state.sessionFilter=btn.dataset.taijiSessionFilter||SESSION_FILTERS.all;
        renderRecentSessions();
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
    mountRealWorkspace();
    wrapLegacyHooks();
    bindRecentControls();
    bindQuickActions();
    renderSecondaryPanel(activePanel());
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
    state.showAllSessions=!state.showAllSessions;
    renderRecentSessions();
  };
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
  window.addEventListener('resize',()=>{ if(desktop()) scheduleSync(); });
})();
