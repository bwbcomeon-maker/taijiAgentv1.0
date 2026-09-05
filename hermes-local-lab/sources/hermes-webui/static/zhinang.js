/* global S, api, createZhinangSession, openChatSession, switchPanel, showToast */
(function(){
  'use strict';

  const state={
    bound:false,
    filters:{scope:'all',category:'all',view:'featured',query:'',page:1},
    catalog:null,
    catalogVersion:'',
    selectedRoleId:'',
    selectedRole:null,
    historicalSessionId:'',
    returnFocus:null,
    catalogController:null,
    detailController:null,
    roleController:null,
    catalogGeneration:0,
    detailGeneration:0,
    profileGeneration:0,
    roleGeneration:0,
    searchTimer:0,
    requestIds:new Map(),
    favoritePending:new Set(),
    createPending:false,
    profile:'default',
  };

  const $=id=>document.getElementById(id);
  const esc=value=>String(value==null?'':value).replace(/[&<>"']/g,ch=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));
  function safeHttpUrl(value){
    const raw=String(value||'').trim();
    if(!raw)return '';
    try{
      const url=new URL(raw);
      return url.protocol==='http:'||url.protocol==='https:'?url.href:'';
    }catch(_){return '';}
  }
  const currentProfile=()=>String((typeof S!=='undefined'&&S&&S.activeProfile)||'default');
  const active=()=>{
    const main=document.querySelector('main.main');
    return !!(main&&main.classList.contains('showing-zhinang'));
  };
  const notify=(message,type='error')=>{
    if(typeof showToast==='function') showToast(message,3000,type);
  };
  const abort=controller=>{try{if(controller)controller.abort();}catch(_){}};

  function requestIdForRole(roleId,draftText=''){
    const key=`${state.catalogVersion}:${roleId}:${draftText}`;
    if(state.requestIds.has(key)) return state.requestIds.get(key);
    const value=(globalThis.crypto&&typeof globalThis.crypto.randomUUID==='function')
      ? globalThis.crypto.randomUUID()
      : `zhinang-ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    state.requestIds.set(key,value);
    return value;
  }

  function catalogUrl(){
    const params=new URLSearchParams({
      scope:state.filters.scope,
      category:state.filters.category,
      view:state.filters.view,
      query:state.filters.query,
      page:String(state.filters.page),
    });
    return `/api/zhinang/catalog?${params}`;
  }

  function setStatus(kind,message=''){
    const el=$('zhinangStatus');
    if(!el)return;
    el.dataset.state=kind;
    el.innerHTML=message?`<span>${esc(message)}</span>`:'';
  }

  function syncFilterControls(){
    document.querySelectorAll('[data-zhinang-scope]').forEach(button=>{
      const selected=button.dataset.zhinangScope===state.filters.scope;
      button.classList.toggle('is-active',selected);
      button.setAttribute('aria-pressed',selected?'true':'false');
    });
    document.querySelectorAll('[data-zhinang-view]').forEach(button=>{
      const selected=button.dataset.zhinangView===state.filters.view;
      button.classList.toggle('is-active',selected);
      button.setAttribute('aria-pressed',selected?'true':'false');
    });
    const search=$('zhinangSearch');
    if(search&&search.value!==state.filters.query)search.value=state.filters.query;
  }

  function renderCategories(categories=[]){
    const root=$('zhinangCategories');
    if(!root)return;
    const rows=[{category:'all',count:(categories||[]).reduce((sum,row)=>sum+Number(row.count||0),0),label:'全部类别'}]
      .concat((categories||[]).map(row=>({...row,label:row.category})));
    root.innerHTML=rows.map(row=>{
      const selected=row.category===state.filters.category;
      return `<button type="button" data-zhinang-category="${esc(row.category)}" class="${selected?'is-active':''}" aria-pressed="${selected?'true':'false'}"><span>${esc(row.label)}</span><strong>${Number(row.count||0)}</strong></button>`;
    }).join('');
  }

  function cardHtml(role){
    const tags=(Array.isArray(role.tags)?role.tags:[]).slice(0,2);
    const disabled=!role.available;
    const recent=role.last_accepted_at?new Date(Number(role.last_accepted_at)*1000).toLocaleString('zh-CN'):'';
    return `<article class="zhinang-card ${disabled?'is-unavailable':''}" data-zhinang-role="${esc(role.role_id)}">
      <div class="zhinang-card-top"><span class="zhinang-role-mark" aria-hidden="true">${esc(String(role.name||'智').slice(0,1))}</span>
        <button type="button" class="zhinang-favorite" data-zhinang-favorite="${esc(role.role_id)}" aria-label="${role.favorite?'取消收藏':'收藏'}${esc(role.name)}" aria-pressed="${role.favorite?'true':'false'}" ${state.favoritePending.has(role.role_id)?'disabled':''}><span aria-hidden="true">${role.favorite?'★':'☆'}</span></button>
      </div>
      <h2>${esc(role.name||role.original_name||'未命名角色')}</h2>
      <p>${esc(role.summary||role.unavailable_reason||'')}</p>
      <div class="zhinang-card-tags">${tags.map(tag=>`<span>${esc(tag)}</span>`).join('')}</div>
      <div class="zhinang-card-foot"><span>${esc(role.category||'')}</span><button type="button" data-zhinang-open="${esc(role.role_id)}">查看详情</button>${recent?`<time>${esc(recent)}</time>`:''}</div>
      ${disabled?'<span class="zhinang-unavailable">当前版本不可用</span>':''}
      ${role.continue_session_id?`<button type="button" class="zhinang-continue" data-zhinang-continue="${esc(role.continue_session_id)}">继续最近任务</button>`:''}
    </article>`;
  }

  function renderPagination(data){
    const root=$('zhinangPagination');
    if(!root)return;
    const page=Number(data.page||1),pages=Number(data.pages||1);
    if(pages<=1){root.replaceChildren();return;}
    root.innerHTML=`<button type="button" data-zhinang-page="${page-1}" ${page<=1?'disabled':''}>上一页</button><span>第 ${page} / ${pages} 页</span><button type="button" data-zhinang-page="${page+1}" ${page>=pages?'disabled':''}>下一页</button>`;
  }

  function renderCatalog(data){
    const grid=$('zhinangGrid');
    if(!grid)return;
    const items=Array.isArray(data.items)?data.items:[];
    syncFilterControls();
    renderCategories(data.categories||[]);
    renderPagination(data);
    if(!items.length){
      const isFavorites=state.filters.scope==='favorites';
      const isRecent=state.filters.view==='recent';
      const filtered=!!state.filters.query||state.filters.category!=='all';
      const copy=isFavorites?'还没有收藏的智囊。':(isRecent?'还没有使用过智囊。':'没有找到符合条件的智囊角色。');
      const action=filtered
        ?'<button type="button" data-zhinang-action="reset-filters">清除筛选</button>'
        :((isFavorites||isRecent)?'<button type="button" data-zhinang-action="browse-all">浏览全部角色</button>':'');
      grid.innerHTML=`<div class="zhinang-empty"><span aria-hidden="true">⌕</span><h2>${esc(copy)}</h2><p>${filtered?'可以清除筛选条件后重试。':'从全部角色中选择一位智囊开始。'}</p>${action}</div>`;
      setStatus('empty',`共 0 位智囊`);
    }else{
      grid.innerHTML=items.map(cardHtml).join('');
      setStatus('ready',`共 ${Number(data.total||items.length)} 位智囊，每页 ${Number(data.page_size||24)} 位`);
    }
    if(state.selectedRoleId&&!items.some(item=>item.role_id===state.selectedRoleId))closeDetail({restoreFocus:false});
  }

  async function loadCatalog({force=false,preserveOnError=false}={}){
    if(!active()&&!force)return false;
    abort(state.catalogController);
    const controller=new AbortController();
    state.catalogController=controller;
    const generation=++state.catalogGeneration;
    const profileGeneration=state.profileGeneration;
    setStatus('loading','正在加载智囊目录…');
    const grid=$('zhinangGrid');
    if(grid&&!state.catalog)grid.innerHTML='<div class="zhinang-skeleton" aria-hidden="true"></div>'.repeat(6);
    try{
      const data=await api(catalogUrl(),{signal:controller.signal,retryNetworkErrors:false});
      if(controller.signal.aborted||generation!==state.catalogGeneration||profileGeneration!==state.profileGeneration)return false;
      state.catalog=data;
      state.catalogVersion=String(data.catalog_version||'');
      renderCatalog(data);
      return true;
    }catch(error){
      if(controller.signal.aborted)return false;
      setStatus('error',error&&error.message||'智囊目录加载失败');
      if(grid&&!preserveOnError)grid.innerHTML='<div class="zhinang-error"><h2>目录暂时无法加载</h2><p>请检查本机服务后重试。</p><button type="button" data-zhinang-action="refresh">重新加载</button></div>';
      return false;
    }
  }

  function detailList(items){
    return `<ul>${(Array.isArray(items)?items:[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`;
  }

  function starterExamplesHtml(items){
    const rows=Array.isArray(items)?items:[];
    if(!rows.length)return '<p>暂无开场示例。</p>';
    return `<ul class="zhinang-starters">${rows.map((item,index)=>`<li><span>${esc(item)}</span><button type="button" data-zhinang-starter="${index}">使用此示例</button></li>`).join('')}</ul>`;
  }

  function detailHtml(role,{historical=false}={}){
    const disabled=!historical&&!role.available;
    const examples=(Array.isArray(role.deliverable_examples)?role.deliverable_examples:[]).map((item,index)=>{
      const title=typeof item==='object'&&item?item.title||`示例 ${index+1}`:`示例 ${index+1}`;
      const content=typeof item==='object'&&item?item.structure||item.content||item.description||JSON.stringify(item):item;
      return `<details><summary>${esc(title)}</summary><p class="zhinang-example-label">示例，非已生成文件</p><p>${esc(content)}</p></details>`;
    }).join('');
    const sourceUrl=safeHttpUrl(role.source_url);
    const source=sourceUrl?`<a href="${esc(sourceUrl)}" target="_blank" rel="noopener noreferrer">${esc(role.source_path||'查看上游')}</a>`:`<span>${esc(role.source_path||'内置角色')}</span>`;
    const actions=[];
    if(!historical&&!disabled){
      actions.push(`<button type="button" class="zhinang-primary" data-zhinang-create="${esc(role.role_id)}" ${state.createPending?'disabled':''}>${state.createPending?'正在创建…':'使用此智囊'}</button>`);
      if(role.continue_session_id)actions.push(`<button type="button" class="zhinang-continue-detail" data-zhinang-continue="${esc(role.continue_session_id)}">继续最近任务</button>`);
    }
    return `<div class="zhinang-detail-dialog" role="dialog" aria-modal="false" aria-labelledby="zhinangDetailTitle" tabindex="-1">
      <div class="zhinang-detail-head"><div><span>${historical?'历史角色快照':`AI 角色 · ${esc(role.category||'智囊')}`}</span><h2 id="zhinangDetailTitle">${esc(role.name||role.original_name||'智囊角色')}</h2><p>${esc(role.original_name||'')}</p></div><button type="button" data-zhinang-close aria-label="关闭智囊详情">×</button></div>
      ${disabled?`<div class="zhinang-detail-warning">${esc(role.unavailable_reason||'当前版本未提供此角色。')}</div>`:''}
      <p class="zhinang-detail-summary">${esc(role.summary||'')}</p>
      <section><h3>能力范围</h3>${detailList(role.capabilities)}</section>
      <section><h3>适用边界</h3><p>${esc(role.limitations||'请结合任务背景核对输出。')}</p></section>
      <section><h3>交付物示例</h3><p class="zhinang-examples-note">以下均为示例，非已生成文件。</p>${examples}</section>
      <section><h3>开场示例</h3>${historical?detailList(role.starter_examples):starterExamplesHtml(role.starter_examples)}</section>
      <details><summary>原始角色说明</summary><pre>${esc(role.raw_source||'')}</pre></details>
      <section><h3>适配说明</h3><p>${esc(role.adaptation_note||'')}</p></section>
      <section class="zhinang-source"><h3>上游来源</h3>${source}<small>固定提交：${esc(role.upstream_commit||'内置')}</small></section>
      <details><summary>完整 MIT 许可证</summary><pre>${esc(role.license||'')}</pre></details>
      <div class="zhinang-detail-actions">${!historical&&!disabled?'<p class="zhinang-create-note">将创建新任务，补充需求后开始。</p>':''}${actions.join('')}${!historical?`<button type="button" class="zhinang-favorite-text" data-zhinang-favorite="${esc(role.role_id)}" aria-pressed="${role.favorite?'true':'false'}">${role.favorite?'取消收藏':'收藏'}</button>`:''}</div>
    </div>`;
  }

  function focusableElements(root){
    return Array.from(root.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),details>summary,[tabindex]:not([tabindex="-1"])')).filter(node=>!node.hidden&&node.getClientRects().length);
  }

  function updateDetailMode(){
    const root=$('mainZhinang'),detail=$('zhinangDetail'),backdrop=$('zhinangDetailBackdrop');
    if(!root||!detail||detail.hidden)return;
    const wide=root.getBoundingClientRect().width>=1180;
    detail.dataset.mode=wide?'aside':'dialog';
    const dialog=detail.querySelector('.zhinang-detail-dialog');
    if(dialog)dialog.setAttribute('aria-modal',wide?'false':'true');
    if(backdrop)backdrop.hidden=wide;
  }

  function focusDetailSurface(){
    const detail=$('zhinangDetail');
    if(!detail||detail.hidden||detail.dataset.mode==='aside')return;
    const target=detail.querySelector('[data-zhinang-close],button,[href],[tabindex="-1"]');
    if(target)target.focus();
  }

  function focusVisibleFallback(){
    const candidates=[state.returnFocus,$('zhinangHeading'),document.querySelector('[data-taiji-panel="zhinang"]'),document.querySelector('[data-panel="zhinang"]')];
    const target=candidates.find(node=>node&&node.isConnected&&node.getClientRects().length&&!node.disabled);
    if(target){if(!target.hasAttribute('tabindex')&&!/^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(target.tagName))target.setAttribute('tabindex','-1');target.focus();}
  }

  function closeDetail({restoreFocus=true}={}){
    abort(state.detailController);
    state.selectedRoleId='';state.selectedRole=null;
    const detail=$('zhinangDetail'),backdrop=$('zhinangDetailBackdrop');
    if(detail){detail.hidden=true;detail.replaceChildren();}
    if(backdrop)backdrop.hidden=true;
    document.body.classList.remove('zhinang-detail-open');
    if(restoreFocus)focusVisibleFallback();
    state.returnFocus=null;
  }

  function showDetail(role,options={}){
    const detail=$('zhinangDetail');
    if(!detail)return;
    state.selectedRole=role;
    state.selectedRoleId=role.role_id||state.selectedRoleId;
    detail.innerHTML=detailHtml(role,options);
    detail.hidden=false;
    document.body.classList.add('zhinang-detail-open');
    updateDetailMode();
    focusDetailSurface();
  }

  async function openRole(roleId,trigger){
    if(!roleId)return;
    abort(state.detailController);
    const controller=new AbortController();state.detailController=controller;
    const generation=++state.detailGeneration,profileGeneration=state.profileGeneration;
    state.selectedRoleId=roleId;state.returnFocus=trigger||document.activeElement;
    const detail=$('zhinangDetail');
    if(detail){detail.hidden=false;detail.innerHTML='<div class="zhinang-detail-loading" role="status" tabindex="-1">正在加载角色详情…</div>';}
    updateDetailMode();
    focusDetailSurface();
    try{
      const data=await api(`/api/zhinang/roles/${encodeURIComponent(roleId)}`,{signal:controller.signal,retryNetworkErrors:false});
      if(controller.signal.aborted||generation!==state.detailGeneration||profileGeneration!==state.profileGeneration)return;
      const role=data.role||{};
      const params=new URLSearchParams({scope:'all',category:'all',view:'recent',query:String(role.name||''),page:'1'});
      let page=1,pages=1,recent=null;
      do{
        params.set('page',String(page));
        const recentData=await api(`/api/zhinang/catalog?${params}`,{signal:controller.signal,retryNetworkErrors:false});
        recent=(Array.isArray(recentData.items)?recentData.items:[]).find(item=>item.role_id===roleId)||null;
        pages=Math.max(1,Number(recentData.pages||1));
        page+=1;
      }while(!recent&&page<=pages);
      if(controller.signal.aborted||generation!==state.detailGeneration||profileGeneration!==state.profileGeneration)return;
      if(recent){role.continue_session_id=recent.continue_session_id;role.last_accepted_at=recent.last_accepted_at;}
      showDetail(role);
    }catch(error){
      if(controller.signal.aborted)return;
      if(detail)detail.innerHTML=`<div class="zhinang-detail-error" role="alert" tabindex="-1"><h2>角色详情无法加载</h2><p>${esc(error&&error.message||'请稍后重试')}</p><button type="button" data-zhinang-retry-role="${esc(roleId)}">重试</button><button type="button" data-zhinang-close>关闭</button></div>`;
      focusDetailSurface();
    }
  }

  async function openHistoricalSessionRole(trigger){
    const sid=typeof S!=='undefined'&&S&&S.session&&S.session.session_id;
    if(!sid)return;
    abort(state.detailController);
    const controller=new AbortController();state.detailController=controller;
    const generation=++state.detailGeneration;state.returnFocus=trigger||document.activeElement;state.historicalSessionId=sid;
    const detail=$('zhinangDetail');
    if(typeof switchPanel==='function')await switchPanel('zhinang');
    if(detail){detail.hidden=false;detail.innerHTML='<div class="zhinang-detail-loading" role="status" tabindex="-1">正在加载历史角色说明…</div>';}
    updateDetailMode();
    focusDetailSurface();
    try{
      const data=await api(`/api/zhinang/session-role?session_id=${encodeURIComponent(sid)}`,{signal:controller.signal,retryNetworkErrors:false});
      if(controller.signal.aborted||generation!==state.detailGeneration)return;
      state.selectedRoleId='';showDetail(data.role||{},{historical:true});
    }catch(error){
      if(controller.signal.aborted)return;
      if(detail)detail.innerHTML=`<div class="zhinang-detail-error" role="alert" tabindex="-1"><h2>历史角色说明无法读取</h2><p>${esc(error&&error.message||'角色快照可能已损坏')}</p><button type="button" data-zhinang-retry-session>重试</button><button type="button" data-zhinang-close>关闭</button></div>`;
      focusDetailSurface();
    }
  }

  async function setFavorite(roleId,favorite){
    if(!roleId||state.favoritePending.has(roleId))return;
    state.favoritePending.add(roleId);renderCatalog(state.catalog||{items:[]});
    try{
      const saved=await api(`/api/zhinang/favorites/${encodeURIComponent(roleId)}`,{
        method:'PUT',body:JSON.stringify({favorite}),retryNetworkErrors:false,
      });
      const authoritative=!!(saved&&saved.favorite);
      if(state.selectedRole&&state.selectedRole.role_id===roleId)state.selectedRole.favorite=authoritative;
      if(state.catalog&&Array.isArray(state.catalog.items))state.catalog.items.forEach(item=>{if(item.role_id===roleId)item.favorite=authoritative;});
      state.favoritePending.delete(roleId);
      if(state.catalog)renderCatalog(state.catalog);
      const refreshed=await loadCatalog({force:true,preserveOnError:true});
      if(!refreshed&&active()){
        setStatus('error','收藏已保存，目录刷新失败。请重试刷新。');
        notify('收藏已保存，目录刷新失败。请重试刷新。','warning');
      }
      if(state.selectedRole&&state.selectedRole.role_id===roleId)showDetail(state.selectedRole);
    }catch(error){
      state.favoritePending.delete(roleId);
      notify(error&&error.message||'收藏状态保存失败');
      renderCatalog(state.catalog||{items:[]});
    }finally{state.favoritePending.delete(roleId);}
  }

  async function createRoleTask(roleId,draftText=''){
    if(state.createPending||!state.selectedRole||!state.catalogVersion)return;
    state.createPending=true;showDetail(state.selectedRole);
    const requestId=requestIdForRole(roleId,draftText);
    try{
      // createZhinangSession enforces awaitCurrentDraftSave before transition.
      await createZhinangSession(roleId,state.catalogVersion,{requestId,draftText,awaitCurrentDraftSave:true});
      state.requestIds.delete(`${state.catalogVersion}:${roleId}:${draftText}`);
      closeDetail({restoreFocus:false});
      if(typeof switchPanel==='function')await switchPanel('chat');
      await syncSessionRole();
    }catch(error){
      notify(error&&error.message||'角色任务创建失败，可直接重试');
    }finally{
      state.createPending=false;
      if(state.selectedRole)showDetail(state.selectedRole);
    }
  }

  async function continueSession(sessionId){
    if(!sessionId)return;
    try{
      if(typeof openChatSession==='function')await openChatSession(sessionId);
      else if(typeof switchPanel==='function')await switchPanel('chat');
    }catch(error){notify(error&&error.message||'最近任务无法继续');}
  }

  function applyFilter(next){
    state.filters={...state.filters,...next,page:1};
    closeDetail({restoreFocus:false});
    syncFilterControls();
    void loadCatalog({force:true});
  }

  function runSearch(){
    state.searchTimer=0;
    const input=$('zhinangSearch');
    applyFilter({query:String(input&&input.value||'').trim()});
  }

  function handleClick(event){
    const target=event.target.closest('[data-zhinang-action],[data-zhinang-scope],[data-zhinang-view],[data-zhinang-category],[data-zhinang-page],[data-zhinang-favorite],[data-zhinang-open],[data-zhinang-role],[data-zhinang-close],[data-zhinang-create],[data-zhinang-continue],[data-zhinang-starter],[data-zhinang-retry-role],[data-zhinang-retry-session]');
    if(!target)return;
    if(target.dataset.zhinangAction==='refresh'){void loadCatalog({force:true});return;}
    if(target.dataset.zhinangAction==='reset-filters'||target.dataset.zhinangAction==='browse-all'){applyFilter({scope:'all',category:'all',view:'all',query:''});return;}
    if(target.dataset.zhinangScope){
      applyFilter(target.dataset.zhinangScope==='favorites'
        ?{scope:'favorites',category:'all',view:'all',query:''}
        :{scope:'all',category:'all',view:'all',query:''});return;
    }
    if(target.dataset.zhinangCategory){applyFilter({category:target.dataset.zhinangCategory,view:'all'});return;}
    if(target.dataset.zhinangView){applyFilter({view:target.dataset.zhinangView});return;}
    if(target.dataset.zhinangPage){state.filters.page=Number(target.dataset.zhinangPage)||1;closeDetail({restoreFocus:false});void loadCatalog({force:true});return;}
    if(Object.prototype.hasOwnProperty.call(target.dataset,'zhinangFavorite')){
      event.stopPropagation();const roleId=target.dataset.zhinangFavorite;
      void setFavorite(roleId,target.getAttribute('aria-pressed')!=='true');return;
    }
    if(target.dataset.zhinangContinue){event.stopPropagation();void continueSession(target.dataset.zhinangContinue);return;}
    if(Object.prototype.hasOwnProperty.call(target.dataset,'zhinangStarter')){
      const index=Number(target.dataset.zhinangStarter);
      const starter=Array.isArray(state.selectedRole&&state.selectedRole.starter_examples)?String(state.selectedRole.starter_examples[index]||''):'';
      void createRoleTask(state.selectedRole&&state.selectedRole.role_id,starter);return;
    }
    if(target.dataset.zhinangCreate){void createRoleTask(target.dataset.zhinangCreate,'');return;}
    if(target.dataset.zhinangRetryRole){void openRole(target.dataset.zhinangRetryRole,state.returnFocus);return;}
    if(Object.prototype.hasOwnProperty.call(target.dataset,'zhinangRetrySession')){void openHistoricalSessionRole(state.returnFocus);return;}
    if(Object.prototype.hasOwnProperty.call(target.dataset,'zhinangClose')){closeDetail();return;}
    if(target.dataset.zhinangOpen){void openRole(target.dataset.zhinangOpen,target);return;}
    if(target.dataset.zhinangRole){const trigger=target.querySelector('[data-zhinang-open]')||target;void openRole(target.dataset.zhinangRole,trigger);}
  }

  function handleKeydown(event){
    if(event.key==='Escape'&&!$('zhinangDetail')?.hidden){event.preventDefault();closeDetail();return;}
    const detail=$('zhinangDetail');
    if(event.key!=='Tab'||!detail||detail.hidden||detail.dataset.mode==='aside')return;
    const focusables=focusableElements(detail);
    if(!focusables.length){
      event.preventDefault();
      const surface=detail.querySelector('[tabindex="-1"]');
      if(surface)surface.focus();
      return;
    }
    const first=focusables[0],last=focusables[focusables.length-1],current=document.activeElement;
    if(!detail.contains(current)){event.preventDefault();(event.shiftKey?last:first).focus();}
    else if(event.shiftKey&&(current===first||!focusables.includes(current))){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }

  function bind(){
    if(state.bound)return;state.bound=true;
    $('panelZhinang')?.addEventListener('click',handleClick);
    $('mainZhinang')?.addEventListener('click',handleClick);
    $('zhinangDetailBackdrop')?.addEventListener('click',()=>closeDetail());
    $('zhinangSearch')?.addEventListener('input',()=>{
      clearTimeout(state.searchTimer);
      state.searchTimer=setTimeout(runSearch, 200);
    });
    $('zhinangSessionRole')?.addEventListener('click',event=>void openHistoricalSessionRole(event.currentTarget));
    document.addEventListener('keydown',handleKeydown);
    window.addEventListener('resize',updateDetailMode);
    window.addEventListener('focus',()=>{if(active())void refreshIfProfileChanged(true);});
    document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&active())void refreshIfProfileChanged(true);});
  }

  async function refreshIfProfileChanged(refresh=false){
    const profile=currentProfile();
    if(profile!==state.profile){
      state.profile=profile;++state.profileGeneration;
      state.filters={scope:'all',category:'all',view:'featured',query:'',page:1};
      state.catalog=null;closeDetail({restoreFocus:false});refresh=true;
    }
    if(refresh)await loadCatalog({force:true});
  }

  async function activate(){bind();await refreshIfProfileChanged(true);syncFilterControls();}

  async function profileChanged(){
    state.profile=currentProfile();++state.profileGeneration;
    abort(state.catalogController);abort(state.detailController);abort(state.roleController);
    state.filters={scope:'all',category:'all',view:'featured',query:'',page:1};
    state.catalog=null;closeDetail({restoreFocus:false});
    await syncSessionRole();
    if(active())await loadCatalog({force:true});
  }

  async function syncSessionRole(){
    bind();abort(state.roleController);
    const button=$('zhinangSessionRole');if(!button)return;
    const session=typeof S!=='undefined'&&S&&S.session;
    const sid=session&&session.session_id;
    if(!sid){button.hidden=true;button.textContent='';return;}
    const inline=session.zhinang_role;
    if(inline&&inline.name&&!inline.code){
      button.textContent=`智囊 · ${inline.name}`;button.hidden=false;button.disabled=false;return;
    }
    if(inline&&inline.code){button.textContent='智囊角色信息不可用';button.hidden=false;button.disabled=false;return;}
    const controller=new AbortController();state.roleController=controller;
    const generation=++state.roleGeneration;
    try{
      const data=await api(`/api/zhinang/session-role?session_id=${encodeURIComponent(sid)}`,{signal:controller.signal,retryNetworkErrors:false});
      if(controller.signal.aborted||generation!==state.roleGeneration||!S.session||S.session.session_id!==sid)return;
      button.textContent=`智囊 · ${data.role&&data.role.name||'角色'}`;button.hidden=false;button.disabled=false;
    }catch(error){
      if(controller.signal.aborted)return;
      if(error&&error.status===404){button.hidden=true;button.textContent='';return;}
      button.textContent='智囊角色信息不可用';button.hidden=false;button.disabled=false;
    }
  }

  window.TaijiZhinang=Object.freeze({activate,profileChanged,syncSessionRole,closeDetail});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bind,{once:true});else bind();
})();
