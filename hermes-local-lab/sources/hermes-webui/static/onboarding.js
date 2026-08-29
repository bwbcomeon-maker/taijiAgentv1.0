const ONBOARDING={status:null,step:0,steps:['system','setup','workspace','password','finish'],form:{provider:'openrouter',workspace:'',model:'',password:'',apiKey:'',baseUrl:''},active:false,statusLoadFailed:false,probe:{status:'idle',error:null,detail:'',models:null,probedKey:''},preflight:null,preflightState:'idle',preflightError:'',retryingCheck:'',busy:false,savedOnce:false,confirmOverwrite:false,overwriteConflict:null};
const ONBOARDING_SETUP_ITEM_IDS=['license','model','workspace','security'];
let _onboardingDialog=null;

function _getOnboardingDialog(){
  if(!_onboardingDialog){
    _onboardingDialog=ManagedDialog.create($('onboardingOverlay'),{
      initialFocus:'#onboardingTitle',
      returnFocus:'#msg',
      closeOnBackdrop:false,
      display:'flex',
      onRequestClose:()=>dismissOnboardingWizard(),
    });
  }
  return _onboardingDialog;
}

function _syncOnboardingResumeEntry(){
  const resume=$('onboardingResumeBtn');
  if(!resume)return;
  const overlay=$('onboardingOverlay');
  const dialogOpen=!!overlay&&overlay.style.display!=='none';
  const incomplete=!!ONBOARDING.status&&ONBOARDING.status.completed!==true;
  resume.hidden=!incomplete||dialogOpen;
  resume.setAttribute('aria-expanded',dialogOpen?'true':'false');
}

// ── Onboarding base-URL probe (#1499) ───────────────────────────────────────
// Probes <base_url>/models so the wizard can validate the configured endpoint
// before persisting AND populate the model dropdown from the live catalog.
// Probe state lives on ONBOARDING.probe; the dropdown render and the
// nextOnboardingStep gate both consult it.

let _onboardingProbeTimer=null;

function _onboardingProbeKey(provider,baseUrl,apiKey){
  return `${provider||''}|${(baseUrl||'').trim().replace(/\/+$/,'')}|${apiKey||''}`;
}

function _setOnboardingProbeState(patch){
  ONBOARDING.probe={...ONBOARDING.probe,...patch};
  _syncOnboardingProbeUi();
}

function _syncOnboardingProbeUi(){
  const probe=ONBOARDING.probe||{status:'idle'};
  const status=$('onboardingProbeStatus');
  const button=$('onboardingProbeBtn');
  if(status){
    const msg=_onboardingProbeMessage(probe);
    status.textContent=msg;
    status.style.display=msg?'block':'none';
    status.className='onboarding-copy onboarding-probe-banner '+({ok:'onboarding-probe-ok',probing:'onboarding-probe-probing',error:'onboarding-probe-error'}[probe.status]||'');
  }
  if(button){
    const probing=probe.status==='probing';
    button.disabled=probing;
    button.setAttribute('aria-busy',probing?'true':'false');
  }
  _syncOnboardingActionState();
}

async function _runOnboardingProbe({force=false}={}){
  const provider=ONBOARDING.form.provider;
  const cat=_getOnboardingSetupProvider(provider);
  if(!cat||!cat.requires_base_url){
    _setOnboardingProbeState({status:'idle',error:null,detail:'',models:null,probedKey:''});
    return ONBOARDING.probe;
  }
  const baseUrl=(ONBOARDING.form.baseUrl||'').trim();
  if(!baseUrl){
    _setOnboardingProbeState({status:'idle',error:null,detail:'',models:null,probedKey:''});
    return ONBOARDING.probe;
  }
  const apiKey=(ONBOARDING.form.apiKey||'').trim();
  const key=_onboardingProbeKey(provider,baseUrl,apiKey);
  if(!force&&ONBOARDING.probe.probedKey===key&&ONBOARDING.probe.status!=='probing'){
    return ONBOARDING.probe;
  }
  _setOnboardingProbeState({status:'probing',error:null,detail:'',probedKey:key});
  try{
    const res=await api('/api/onboarding/probe',{method:'POST',body:JSON.stringify({provider,base_url:baseUrl,api_key:apiKey||undefined})});
    if(res&&res.ok){
      _setOnboardingProbeState({status:'ok',error:null,detail:'',models:Array.isArray(res.models)?res.models:[],probedKey:key});
      // If the user hasn't picked a model yet (or their pick is no longer in
      // the list), default to the first probed model so Continue isn't blocked
      // on an empty selection.
      const stillPresent=ONBOARDING.form.model&&(res.models||[]).some(m=>m.id===ONBOARDING.form.model);
      if(!stillPresent&&(res.models||[]).length>0){
        ONBOARDING.form.model=res.models[0].id;
        _markOnboardingDirty();
      }
    }else{
      const err=(res&&res.error)||'unreachable';
      const detail=(res&&res.detail)||'';
      _setOnboardingProbeState({status:'error',error:err,detail,models:null,probedKey:key});
    }
  }catch(e){
    _setOnboardingProbeState({status:'error',error:'unreachable',detail:(e&&e.message)||String(e),models:null,probedKey:key});
  }
  return ONBOARDING.probe;
}

function _scheduleOnboardingProbe(){
  if(_onboardingProbeTimer)clearTimeout(_onboardingProbeTimer);
  _onboardingProbeTimer=setTimeout(()=>{_runOnboardingProbe();},400);
}

function _onboardingProbeMessage(probe){
  if(!probe||probe.status==='idle')return '';
  if(probe.status==='probing')return t('onboarding_probe_probing')||'Testing connection…';
  if(probe.status==='ok'){
    const n=(probe.models||[]).length;
    const tmpl=t('onboarding_probe_ok')||'Connected. {n} model(s) available.';
    return tmpl.replace('{n}',String(n));
  }
  // status === 'error'
  const errKey='onboarding_probe_error_'+probe.error;
  const localized=t(errKey);
  // i18n.js's `t()` returns the key itself when missing — fall back to a generic message.
  const heading=(localized&&localized!==errKey)?localized:(t('onboarding_probe_error_generic')||'Could not reach the configured base URL.');
  const detail=probe.detail?` (${probe.detail})`:'';
  return heading+detail;
}

function _getOnboardingSetupProviders(){
  return (((ONBOARDING.status||{}).setup||{}).providers)||[];
}

function _getOnboardingSetupProvider(id){
  return _getOnboardingSetupProviders().find(p=>p.id===id)||null;
}

function _getOnboardingSetupCategories(){
  return (((ONBOARDING.status||{}).setup||{}).categories)||[];
}

/** Render the provider <select> with <optgroup> per category. */
function _renderProviderSelectOptions(selectedId){
  const providers=_getOnboardingSetupProviders();
  const categories=_getOnboardingSetupCategories();
  const provMap={};
  providers.forEach(p=>{provMap[p.id]=p;});
  if(!categories.length){
    // Fallback: flat list when no categories are available.
    return providers.map(p=>`<option value="${esc(p.id)}">${esc(p.label)}${p.quick?' — '+esc(t('onboarding_quick_setup_badge')):''}</option>`).join('');
  }
  return categories.map(cat=>{
    const opts=cat.providers.map(pid=>{
      const p=provMap[pid];
      if(!p)return '';
      return `<option value="${esc(p.id)}"${p.id===selectedId?' selected':''}>${esc(p.label)}${p.quick?' — '+esc(t('onboarding_quick_setup_badge')):''}</option>`;
    }).join('');
    return `<optgroup label="${esc(t('provider_category_'+cat.id)||cat.label)}">${opts}</optgroup>`;
  }).join('');
}

function _getOnboardingCurrentSetup(){
  return (((ONBOARDING.status||{}).setup||{}).current)||{};
}

function _onboardingStepMeta(key){
  return ({
    system:{title:t('onboarding_step_system_title'),desc:t('onboarding_step_system_desc')},
    setup:{title:t('onboarding_step_setup_title'),desc:t('onboarding_step_setup_desc')},
    workspace:{title:t('onboarding_step_workspace_title'),desc:t('onboarding_step_workspace_desc')},
    password:{title:t('onboarding_step_password_title'),desc:t('onboarding_step_password_desc')},
    finish:{title:t('onboarding_step_finish_title'),desc:t('onboarding_step_finish_desc')}
  })[key];
}

function _renderOnboardingSteps(){
  const wrap=$('onboardingSteps');
  if(!wrap)return;
  wrap.innerHTML='';
  ONBOARDING.steps.forEach((key,idx)=>{
    const meta=_onboardingStepMeta(key);
    const item=document.createElement('div');
    item.className='onboarding-step'+(idx===ONBOARDING.step?' active':idx<ONBOARDING.step?' done':'');
    item.setAttribute('role','listitem');
    if(idx===ONBOARDING.step)item.setAttribute('aria-current','step');
    item.innerHTML=`<div class="onboarding-step-index">${idx+1}</div><div><div class="onboarding-step-title">${meta.title}</div><div class="onboarding-step-desc">${meta.desc}</div></div>`;
    wrap.appendChild(item);
  });
}

function _setOnboardingNotice(msg,kind='info'){
  const el=$('onboardingNotice');
  if(!el)return;
  if(!msg){el.style.display='none';el.textContent='';el.className='onboarding-status';return;}
  el.style.display='block';
  el.className='onboarding-status '+kind;
  el.textContent=msg;
}

function _setOnboardingBusy(busy){
  ONBOARDING.busy=!!busy;
  const body=$('onboardingBody');
  const actions=$('onboardingActions');
  if(body)body.setAttribute('aria-busy',ONBOARDING.busy?'true':'false');
  if(actions)actions.setAttribute('aria-busy',ONBOARDING.busy?'true':'false');
  _syncOnboardingActionState();
}

function _syncOnboardingActionState(){
  const key=ONBOARDING.steps[ONBOARDING.step];
  const nextBtn=$('onboardingNextBtn');
  const backBtn=$('onboardingBackBtn');
  const skipBtn=$('onboardingSkipBtn');
  const preflightReady=!!(ONBOARDING.preflight&&ONBOARDING.preflight.overall_ready);
  const finishBlocked=key==='finish'&&ONBOARDING.savedOnce&&(!preflightReady||ONBOARDING.preflightState!=='ready');
  const initialStatusBlocked=key==='system'&&ONBOARDING.statusLoadFailed;
  const setupProvider=_getOnboardingSetupProvider(ONBOARDING.form.provider);
  const setupProbeBlocked=key==='setup'&&!!(setupProvider&&setupProvider.requires_base_url)&&ONBOARDING.probe.status!=='ok';
  if(nextBtn){
    nextBtn.disabled=ONBOARDING.busy||initialStatusBlocked||setupProbeBlocked||finishBlocked||!!ONBOARDING.overwriteConflict;
    nextBtn.setAttribute('aria-disabled',nextBtn.disabled?'true':'false');
    nextBtn.setAttribute('aria-busy',ONBOARDING.busy?'true':'false');
    nextBtn.textContent=key==='finish'
      ? (preflightReady?t('onboarding_open'):'保存并重新检查')
      : t('onboarding_continue');
  }
  if(backBtn){
    backBtn.disabled=ONBOARDING.busy;
    backBtn.setAttribute('aria-disabled',backBtn.disabled?'true':'false');
  }
  if(skipBtn){
    skipBtn.disabled=ONBOARDING.busy;
    skipBtn.setAttribute('aria-disabled',skipBtn.disabled?'true':'false');
  }
}

function _markOnboardingDirty(){
  ONBOARDING.savedOnce=false;
  ONBOARDING.confirmOverwrite=false;
  ONBOARDING.overwriteConflict=null;
  _syncOnboardingActionState();
}

function _setupStatusItem(id){
  const items=Array.isArray((ONBOARDING.preflight||{}).items)?ONBOARDING.preflight.items:[];
  return items.find(item=>item&&item.id===id)||null;
}

function _renderSetupWorkbench(){
  if(ONBOARDING.statusLoadFailed&&ONBOARDING.preflightState!=='loading'){
    return `<div class="onboarding-workbench-state error" role="status" aria-live="polite" aria-atomic="true"><strong>检查失败</strong><span>${esc(ONBOARDING.preflightError||'无法读取首次启动状态。')}</span><button type="button" class="sm-btn" id="onboardingStatusRetryBtn" onclick="retryOnboardingStatus()">重新检查</button></div>`;
  }
  if(ONBOARDING.preflightState==='loading'){
    return `<div class="onboarding-workbench-state" role="status" aria-live="polite" aria-atomic="true"><strong>正在检查…</strong><span>正在读取授权、模型、工作区和安全策略。</span></div>`;
  }
  if(ONBOARDING.preflightState==='error'){
    const retryAction=ONBOARDING.statusLoadFailed?'retryOnboardingStatus()':"retryOnboardingCheck('all')";
    const retryId=ONBOARDING.statusLoadFailed?' id="onboardingStatusRetryBtn"':'';
    return `<div class="onboarding-workbench-state error" role="status" aria-live="polite" aria-atomic="true"><strong>检查失败</strong><span>${esc(ONBOARDING.preflightError||'无法读取当前状态。')}</span><button type="button" class="sm-btn"${retryId} onclick="${retryAction}">重新检查</button></div>`;
  }
  const rawItems=Array.isArray((ONBOARDING.preflight||{}).items)?ONBOARDING.preflight.items:[];
  const items=ONBOARDING_SETUP_ITEM_IDS.map(id=>rawItems.find(item=>item&&item.id===id)).filter(Boolean);
  if(!items.length){
    return `<div class="onboarding-workbench-state empty" role="status" aria-live="polite" aria-atomic="true"><strong>暂无检查项</strong><span>请重新读取当前状态。</span><button type="button" class="sm-btn" onclick="retryOnboardingCheck('all')">重新检查</button></div>`;
  }
  const rows=items.map(item=>{
    const id=item.id;
    const ready=!!item.ready;
    const retrying=ONBOARDING.retryingCheck===id||ONBOARDING.retryingCheck==='all';
    const recovery=item.recovery||{};
    const statusLabel=ready?'已就绪':(item.status==='unavailable'?'暂不可用':'需要处理');
    const recoveryButton=!ready&&recovery.id
      ? `<button type="button" class="sm-btn onboarding-check-action" onclick="openOnboardingRecovery('${id}')">${esc(recovery.label||'去处理')}</button>`
      : '';
    return `<div class="onboarding-check-row ${ready?'ready':'blocked'}" id="onboardingCheck-${id}" data-setup-check="${id}" role="listitem" tabindex="-1">
      <div class="onboarding-check-copy"><div class="onboarding-check-heading"><strong>${esc(item.label||id)}</strong><span class="onboarding-check-badge">${statusLabel}</span></div><p>${esc(item.reason||'状态暂时不可用。')}</p></div>
      <div class="onboarding-check-actions">${recoveryButton}<button type="button" class="sm-btn onboarding-check-retry" onclick="retryOnboardingCheck('${id}')" aria-label="重新检查${esc(item.label||id)}" ${retrying?'disabled aria-busy="true"':''}>${retrying?'检查中…':'重新检查'}</button></div>
    </div>`;
  }).join('');
  const overall=ONBOARDING.preflight&&ONBOARDING.preflight.overall_ready;
  return `<div class="onboarding-check-list" role="list" aria-live="polite" aria-atomic="false">${rows}</div><p class="onboarding-workbench-summary ${overall?'success':'pending'}" role="status">${overall?'全部检查已通过，可以完成设置。':'尚有检查项未通过，请处理后重新检查。'}</p>`;
}

async function _loadSetupPreflight({focusId=''}={}){
  ONBOARDING.preflightState='loading';
  ONBOARDING.preflightError='';
  ONBOARDING.retryingCheck=focusId||'all';
  _renderOnboardingBody();
  try{
    ONBOARDING.preflight=await api('/api/setup/status');
    ONBOARDING.preflightState='ready';
    return ONBOARDING.preflight;
  }catch(e){
    ONBOARDING.preflight=null;
    ONBOARDING.preflightState='error';
    ONBOARDING.preflightError=(e&&e.message)||String(e);
    throw e;
  }finally{
    ONBOARDING.retryingCheck='';
    _renderOnboardingBody();
    const focusTarget=focusId&&focusId!=='all'?$(`onboardingCheck-${focusId}`):document.querySelector('.onboarding-workbench-state button');
    if(focusTarget)focusTarget.focus();
  }
}

async function retryOnboardingCheck(id){
  if(ONBOARDING.busy||ONBOARDING.retryingCheck)return;
  try{
    await _loadSetupPreflight({focusId:ONBOARDING_SETUP_ITEM_IDS.includes(id)?id:'all'});
  }catch(e){
    _setOnboardingNotice('检查失败：'+((e&&e.message)||String(e)),'warn');
  }
}

async function retryOnboardingStatus(){
  if(ONBOARDING.busy)return;
  ONBOARDING.preflightState='loading';
  ONBOARDING.preflightError='';
  _renderOnboardingBody();
  await loadOnboardingWizard();
  const retry=$('onboardingStatusRetryBtn')||document.querySelector('.onboarding-check-row.blocked');
  if(retry)retry.focus();
}

function openOnboardingRecovery(id){
  const item=_setupStatusItem(id);
  const recovery=(item&&item.recovery)||{};
  if(recovery.target_step&&ONBOARDING.steps.includes(recovery.target_step)){
    _markOnboardingDirty();
    ONBOARDING.step=ONBOARDING.steps.indexOf(recovery.target_step);
    _renderOnboardingSteps();
    _renderOnboardingBody();
    const target=$('onboardingProviderSelect')||$('onboardingWorkspaceInput')||$('onboardingNextBtn');
    if(target)target.focus();
    return;
  }
  if(recovery.target_section&&typeof switchSettingsSection==='function'){
    dismissOnboardingWizard({focusResume:false});
    switchSettingsSection(recovery.target_section);
    requestAnimationFrame(()=>focusSettingsRecoveryTarget(recovery.target_section));
    showToast('处理完成后点击“继续配置·开始使用检查”返回检查。',5000,'info');
    return;
  }
  retryOnboardingCheck(id);
}

function focusSettingsRecoveryTarget(section){
  const activePane=document.querySelector('[id^="settingsPane"].active');
  const heading=activePane&&activePane.querySelector('.settings-section-title');
  const fallback=document.querySelector(`#settingsMenu [data-settings-section="${CSS.escape(section||'')}"]`);
  const target=heading||fallback;
  if(!target)return;
  if(!target.matches('button,a,input,select,textarea,[tabindex]'))target.setAttribute('tabindex','-1');
  target.focus();
}

function _renderOnboardingOverwriteConflict(){
  const conflict=ONBOARDING.overwriteConflict;
  if(!conflict)return '';
  return `<div class="onboarding-conflict" role="alert" aria-labelledby="onboardingConflictTitle" aria-describedby="onboardingConflictMessage">
    <strong id="onboardingConflictTitle">已存在配置，需要你确认</strong>
    <p id="onboardingConflictMessage">${esc(conflict.message||'当前终端已有模型配置。覆盖后将使用本次选择，其他设置保持不变。')}</p>
    <div class="onboarding-conflict-actions"><button type="button" class="sm-btn" onclick="cancelOnboardingOverwrite()">返回检查</button><button type="button" class="sm-btn primary" id="onboardingConfirmOverwriteBtn" onclick="confirmOnboardingOverwrite()">确认覆盖并重试</button></div>
  </div>`;
}

function _getOnboardingWorkspaceChoices(){
  const items=((ONBOARDING.status||{}).workspaces||{}).items||[];
  return items.length?items:[{name:t('onboarding_workspace_default'),path:ONBOARDING.form.workspace||''}];
}

function _getOnboardingWorkspaceDisplayName(ws, idx){
  const rawName=String((ws&&ws.name)||'').trim();
  const rawPath=String((ws&&ws.path)||'').trim();
  if(rawName && rawName!==rawPath && !/[\\/]/.test(rawName)) return rawName;
  return idx===0?t('onboarding_workspace_default'):`${t('onboarding_workspace_local')} ${idx+1}`;
}

function _getOnboardingProviderModelChoices(){
  const provider=_getOnboardingSetupProvider(ONBOARDING.form.provider);
  // Probe-discovered models (#1499) take precedence over the static catalog
  // for providers with requires_base_url=True.  The catalog ships an empty
  // list for self-hosted providers (lmstudio, ollama, custom) — without the
  // probe the user had nothing to pick from.
  if(provider&&provider.requires_base_url&&ONBOARDING.probe&&ONBOARDING.probe.status==='ok'&&Array.isArray(ONBOARDING.probe.models)&&ONBOARDING.probe.models.length){
    return ONBOARDING.probe.models;
  }
  return provider?(provider.models||[]):[];
}

function _renderOnboardingBaseUrlField(showBaseUrl){
  // Renders the base_url input PLUS the probe status banner / Test button
  // when the active provider has requires_base_url=True (#1499).  Returns
  // the empty string when the active provider does not require a base URL,
  // so the existing call sites can continue to template-interpolate this in
  // place of the previous inline `<label …>` snippet.
  if(!showBaseUrl)return '';
  const probe=ONBOARDING.probe||{status:'idle'};
  const msg=_onboardingProbeMessage(probe);
  const cls={ok:'onboarding-probe-ok',probing:'onboarding-probe-probing',error:'onboarding-probe-error'}[probe.status]||'';
  const banner=`<p id="onboardingProbeStatus" class="onboarding-copy onboarding-probe-banner ${cls}" role="status" aria-live="polite" aria-atomic="true" style="display:${msg?'block':'none'}">${esc(msg)}</p>`;
  const testBtnLabel=t('onboarding_probe_test_button')||'Test connection';
  const testBtnDisabled=(probe.status==='probing')?'disabled':'';
  return `<label class="onboarding-field"><span>${t('onboarding_base_url_label')}</span><input id="onboardingBaseUrlInput" value="${esc(ONBOARDING.form.baseUrl||'')}" placeholder="${t('onboarding_base_url_placeholder')}" oninput="ONBOARDING.form.baseUrl=this.value;_markOnboardingDirty();_scheduleOnboardingProbe()" onblur="_runOnboardingProbe()"></label><div class="onboarding-probe-row"><button type="button" class="onboarding-probe-btn" id="onboardingProbeBtn" aria-busy="${probe.status==='probing'?'true':'false'}" ${testBtnDisabled} onclick="_runOnboardingProbe({force:true})">${esc(testBtnLabel)}</button></div>${banner}`;
}

function _renderOnboardingApiKeyField(){
  // Renders the API-key input.  For providers flagged `key_optional` in the
  // setup catalog (lmstudio, ollama, custom — typically self-hosted servers
  // that run keyless by default), the field shows an "(optional)" hint and
  // empty input is accepted on Continue.  Pre-#1499-third-sub-bug-fix the
  // wizard required a non-empty string here even for keyless installs, which
  // forced users to type random gibberish to clear onboarding.
  const provider=_getOnboardingSetupProvider(ONBOARDING.form.provider);
  const keyOptional=!!(provider&&provider.key_optional);
  const labelKey=keyOptional?'onboarding_api_key_label_optional':'onboarding_api_key_label';
  const placeholderKey=keyOptional?'onboarding_api_key_placeholder_optional':'onboarding_api_key_placeholder';
  const helpHtml=keyOptional?`<p class="onboarding-copy onboarding-api-key-help">${esc(t('onboarding_api_key_help_keyless')||'')}</p>`:'';
  return `<label class="onboarding-field" id="onboardingApiKeyField"><span>${t(labelKey)}</span><input id="onboardingApiKeyInput" type="password" value="${esc(ONBOARDING.form.apiKey||'')}" placeholder="${t(placeholderKey)}" oninput="ONBOARDING.form.apiKey=this.value;_markOnboardingDirty()" onblur="_runOnboardingProbe()"></label>${helpHtml}`;
}

function _getOnboardingSelectedModel(){
  return ONBOARDING.form.model||'';
}

function _renderOnboardingModelField(){
  const choices=_getOnboardingProviderModelChoices();
  if(ONBOARDING.form.provider==='custom'){
    return `<label class="onboarding-field"><span>${t('onboarding_model_label')}</span><input id="onboardingModelInput" value="${esc(_getOnboardingSelectedModel())}" placeholder="${t('onboarding_custom_model_placeholder')}" oninput="ONBOARDING.form.model=this.value;_markOnboardingDirty()"></label><p class="onboarding-copy">${t('onboarding_custom_model_help')}</p>`;
  }
  const options=choices.map(m=>`<option value="${esc(m.id)}">${esc(m.label)}</option>`).join('');
  return `<label class="onboarding-field"><span>${t('onboarding_model_label')}</span><select id="onboardingModelSelect" onchange="ONBOARDING.form.model=this.value;_markOnboardingDirty()">${options}</select></label><p class="onboarding-copy">${t('onboarding_workspace_help')}</p>`;
}

function _renderOnboardingProviderOAuthField(provider){
  if(!provider||provider.oauth_provider!=='anthropic')return '';
  return `<div class="onboarding-oauth-card onboarding-oauth-pending" role="group" aria-labelledby="anthropicOAuthTitle" style="margin-top:12px">
    <div class="onboarding-oauth-icon">🔑</div>
    <div style="flex:1">
      <strong id="anthropicOAuthTitle">改用 Claude Code OAuth</strong>
      <p id="anthropicOAuthDescription" style="margin-top:6px;color:var(--muted);font-size:13px"><strong>Claude Code 订阅凭据不同于 Anthropic API 密钥。</strong>仅当你希望 taiji Agent 使用服务器上已有的 Claude Code 凭据，或在主机上完成 <code>claude setup-token</code> 时启动短轮询流程，才使用此路径。</p>
      <div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="sm-btn" id="anthropicOAuthBtn" onclick="startAnthropicOAuth()" type="button" aria-controls="anthropicOAuthFlow" aria-describedby="anthropicOAuthDescription">使用 Claude Code 登录</button></div>
      <div id="anthropicOAuthFlow" role="status" aria-live="polite" aria-atomic="true" style="display:none;margin-top:12px"></div>
    </div>
  </div>`;
}

function _providerStatusLabel(system){
  if(system.chat_ready) return t('onboarding_check_provider_ready');
  if(system.provider_configured) return t('onboarding_check_provider_partial');
  return t('onboarding_check_provider_pending');
}

function _renderOnboardingBody(){
  const body=$('onboardingBody');
  if(!body||!ONBOARDING.status)return;
  const key=ONBOARDING.steps[ONBOARDING.step];
  const system=ONBOARDING.status.system||{};
  const settings=ONBOARDING.status.settings||{};
  const setup=ONBOARDING.status.setup||{};
  const nextBtn=$('onboardingNextBtn');
  const backBtn=$('onboardingBackBtn');
  if(backBtn) backBtn.style.display=ONBOARDING.step>0?'':'none';
  _syncOnboardingActionState();

  if(key==='system'){
    const preflightReady=!!(ONBOARDING.preflight&&ONBOARDING.preflight.overall_ready);
    const retryAllAction=ONBOARDING.statusLoadFailed?'retryOnboardingStatus()':"retryOnboardingCheck('all')";
    if(ONBOARDING.statusLoadFailed){
      _setOnboardingNotice('开始使用前状态读取失败，请重新检查；状态恢复前不能继续。','warn');
    }else if(ONBOARDING.preflightState==='error'){
      _setOnboardingNotice('开始使用前检查暂时失败，可重试或继续填写配置。','warn');
    }else if(preflightReady){
      _setOnboardingNotice('授权、模型、工作区和安全策略均已就绪。','success');
    }else{
      _setOnboardingNotice('请按下方检查项逐项处理；失败项可单独重新检查。','info');
    }
    body.innerHTML=`
      <section class="onboarding-workbench" aria-labelledby="onboardingWorkbenchTitle">
        <div class="onboarding-workbench-heading"><div><h3 id="onboardingWorkbenchTitle">开始使用前检查</h3><p>这是你的配置工作台。四项全部通过后才会标记完成。</p></div><button type="button" class="sm-btn" onclick="${retryAllAction}" ${ONBOARDING.preflightState==='loading'?'disabled aria-busy="true"':''}>全部重新检查</button></div>
        ${_renderSetupWorkbench()}
      </section>`;
    return;
  }

  if(key==='setup'){
    const selectedId=ONBOARDING.form.provider;
    const groupedOptions=_renderProviderSelectOptions(selectedId);
    const selectedProvider=_getOnboardingSetupProvider(selectedId);
    const provider=selectedProvider||_getOnboardingSetupProviders()[0]||null;
    const showBaseUrl=provider&&provider.requires_base_url;
    const keyHelp=provider
      ? (provider.id==='anthropic'
        ? 'Anthropic API 密钥路径：请在此粘贴 Anthropic Console API 密钥。Claude Code 订阅凭据不同于 Anthropic API 密钥；如需使用订阅凭据，请改用 Claude Code OAuth 卡片。'
        : `${t('onboarding_api_key_help_prefix')}.`)
      : '';

    // OAuth provider path: configured via CLI, no API key input needed.
    const currentIsOauth=!!(ONBOARDING.status.setup||{}).current_is_oauth;
    const currentProviderName=((ONBOARDING.status.setup||{}).current||{}).provider||'';
    if(currentIsOauth&&!selectedProvider){
      const isReady=!!(ONBOARDING.status.system||{}).chat_ready;
      const providerLabel=esc(currentProviderName);
      const codexOauthPendingBody=currentProviderName==='openai-codex'
        ? '此实例已配置为使用 <strong>openai-codex</strong>，它使用 OAuth 而不是 API 密钥。请使用下方按钮通过 ChatGPT 认证，等待提供商状态刷新后继续。'
        : t('onboarding_oauth_provider_not_ready_body').replace('{provider}',providerLabel);
      if(isReady){
        _setOnboardingNotice(t('onboarding_notice_setup_already_ready'),'success');
        body.innerHTML=`
          <div class="onboarding-oauth-card onboarding-oauth-ready">
            <div class="onboarding-oauth-icon">✓</div>
            <div>
              <strong>${t('onboarding_oauth_provider_ready_title')}</strong>
              <p>${t('onboarding_oauth_provider_ready_body').replace('{provider}',providerLabel)}</p>
            </div>
          </div>
          <p class="onboarding-copy" style="margin-top:20px">${t('onboarding_oauth_switch_hint')}</p>
          <label class="onboarding-field">
            <span>${t('onboarding_provider_label')}</span>
            <select id="onboardingProviderSelect" onchange="syncOnboardingProvider(this.value)">${groupedOptions}</select>
          </label>
          ${_renderOnboardingApiKeyField()}
          ${_renderOnboardingBaseUrlField(showBaseUrl)}
          <p class="onboarding-copy">${keyHelp}</p>`;
      } else {
        _setOnboardingNotice(t('onboarding_notice_setup_required'),'warn');
        body.innerHTML=`
          <div class="onboarding-oauth-card onboarding-oauth-pending">
            <div class="onboarding-oauth-icon">⚠</div>
            <div style="flex:1">
              <strong>${t('onboarding_oauth_provider_not_ready_title')}</strong>
              <p>${codexOauthPendingBody}</p>
              ${currentProviderName==='openai-codex'?`<div style="margin-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="sm-btn" id="codexOAuthBtn" onclick="startCodexOAuth()" type="button">${t('oauth_login_codex')}</button></div><div id="codexOAuthFlow" style="display:none;margin-top:12px"></div>`:''}
            </div>
          </div>
          <p class="onboarding-copy" style="margin-top:20px">${t('onboarding_oauth_switch_hint')}</p>
          <label class="onboarding-field">
            <span>${t('onboarding_provider_label')}</span>
            <select id="onboardingProviderSelect" onchange="syncOnboardingProvider(this.value)">${groupedOptions}</select>
          </label>
          ${_renderOnboardingApiKeyField()}
          ${_renderOnboardingBaseUrlField(showBaseUrl)}
          <p class="onboarding-copy">${keyHelp}</p>`;
      }
      return;
    }

    _setOnboardingNotice(system.chat_ready?t('onboarding_notice_setup_already_ready'):t('onboarding_notice_setup_required'),system.chat_ready?'success':'info');
    body.innerHTML=`
      <label class="onboarding-field">
        <span>${t('onboarding_provider_label')}</span>
        <select id="onboardingProviderSelect" onchange="syncOnboardingProvider(this.value)">${groupedOptions}</select>
      </label>
      ${_renderOnboardingApiKeyField()}
      ${_renderOnboardingProviderOAuthField(provider)}
      ${_renderOnboardingBaseUrlField(showBaseUrl)}
      <p class="onboarding-copy">${keyHelp}</p>
      ${showBaseUrl?`<p class="onboarding-copy">${t('onboarding_base_url_help')}</p>`:''}
      <p class="onboarding-copy">${esc(setup.unsupported_note||'')||''}</p>`;
    return;
  }

  if(key==='workspace'){
    const workspaceOptions=_getOnboardingWorkspaceChoices().map((ws,idx)=>`<option value="${esc(ws.path)}">${esc(_getOnboardingWorkspaceDisplayName(ws,idx))}</option>`).join('');
    _setOnboardingNotice(t('onboarding_notice_workspace'), 'info');
    body.innerHTML=`
      <label class="onboarding-field">
        <span>${t('onboarding_workspace_label')}</span>
        <select id="onboardingWorkspaceSelect" onchange="syncOnboardingWorkspaceSelect(this.value)">${workspaceOptions}</select>
      </label>
      <label class="onboarding-field">
        <span>${t('onboarding_workspace_or_path')}</span>
        <input id="onboardingWorkspaceInput" value="${esc(ONBOARDING.form.workspace||'')}" placeholder="${t('onboarding_workspace_placeholder')}" oninput="ONBOARDING.form.workspace=this.value;_markOnboardingDirty()">
      </label>
      ${_renderOnboardingModelField()}`;
    const wsSel=$('onboardingWorkspaceSelect');
    if(wsSel && ONBOARDING.form.workspace) wsSel.value=ONBOARDING.form.workspace;
    const modelSel=$('onboardingModelSelect');
    if(modelSel && ONBOARDING.form.model) modelSel.value=ONBOARDING.form.model;
    return;
  }

  if(key==='password'){
    _setOnboardingNotice(settings.password_enabled?t('onboarding_notice_password_enabled'):t('onboarding_notice_password_recommended'), settings.password_enabled?'success':'info');
    body.innerHTML=`
      <label class="onboarding-field">
        <span>${t('onboarding_password_label')}</span>
        <input id="onboardingPasswordInput" type="password" value="${esc(ONBOARDING.form.password||'')}" placeholder="${t('onboarding_password_placeholder')}" oninput="ONBOARDING.form.password=this.value;_markOnboardingDirty()">
      </label>
      <p class="onboarding-copy">${t('onboarding_password_help')}</p>`;
    return;
  }

  const provider=_getOnboardingSetupProvider(ONBOARDING.form.provider);
  const finishReady=!!(ONBOARDING.preflight&&ONBOARDING.preflight.overall_ready);
  _setOnboardingNotice(
    finishReady?'检查已通过，可以完成并打开太极 Agent。':'先保存当前选择，系统会在完成前重新检查四项最低门槛。',
    finishReady?'success':'info'
  );
  body.innerHTML=`
    <div class="onboarding-summary">
      <div><strong>${t('onboarding_provider_label')}</strong><span>${esc((provider&&provider.label)||ONBOARDING.form.provider||t('onboarding_not_set'))}</span></div>
      <div><strong>${t('onboarding_model_label')}</strong><span>${esc(_getOnboardingSelectedModel()||t('onboarding_not_set'))}</span></div>
      <div><strong>${t('onboarding_workspace_label')}</strong><span>${esc(ONBOARDING.form.workspace||t('onboarding_not_set'))}</span></div>
      <div><strong>${t('onboarding_check_password')}</strong><span>${t(_getOnboardingPasswordSummaryKey(settings))}</span></div>
    </div>
    ${ONBOARDING.form.baseUrl?`<p class="onboarding-copy"><strong>${t('onboarding_base_url_label')}</strong> ${esc(ONBOARDING.form.baseUrl)}</p>`:''}
    ${_renderOnboardingOverwriteConflict()}
    <section class="onboarding-workbench compact" aria-labelledby="onboardingFinishChecksTitle"><div class="onboarding-workbench-heading"><div><h3 id="onboardingFinishChecksTitle">完成前复检</h3><p>网络或 API 读取失败时不会继续完成。</p></div><button type="button" class="sm-btn" onclick="retryOnboardingCheck('all')" ${ONBOARDING.preflightState==='loading'?'disabled aria-busy="true"':''}>全部重新检查</button></div>${_renderSetupWorkbench()}</section>
    <p class="onboarding-copy">${t('onboarding_finish_help')}</p>`;
}

function _getOnboardingPasswordSummaryKey(settings){
  const hasExistingPassword=!!(settings&&settings.password_enabled);
  const hasNewPassword=!!((ONBOARDING.form.password||'').trim());
  if(hasNewPassword) return hasExistingPassword?'onboarding_password_will_replace':'onboarding_password_will_enable';
  return hasExistingPassword?'onboarding_password_keep_existing':'onboarding_password_remains_disabled';
}

function syncOnboardingWorkspaceSelect(value){
  ONBOARDING.form.workspace=value;
  _markOnboardingDirty();
  const input=$('onboardingWorkspaceInput');
  if(input) input.value=value;
}

function syncOnboardingProvider(value){
  const provider=_getOnboardingSetupProvider(value);
  ONBOARDING.form.provider=value;
  _markOnboardingDirty();
  if(provider){
    if(!ONBOARDING.form.model || !_getOnboardingProviderModelChoices().some(m=>m.id===ONBOARDING.form.model) || value==='custom'){
      ONBOARDING.form.model=provider.default_model||'';
    }
    if(provider.requires_base_url){
      ONBOARDING.form.baseUrl=ONBOARDING.form.baseUrl||provider.default_base_url||'';
    }else{
      ONBOARDING.form.baseUrl=provider.default_base_url||'';
    }
  }
  _renderOnboardingBody();
  const providerSelect=$('onboardingProviderSelect');
  if(providerSelect) providerSelect.focus();
}

async function loadOnboardingWizard(){
  try{
    const status=await api('/api/onboarding/status');
    ONBOARDING.status=status;
    ONBOARDING.statusLoadFailed=false;
    const current=((status.setup||{}).current)||{};
    ONBOARDING.form.provider=current.provider||'openrouter';
    ONBOARDING.form.workspace=(status.workspaces&&status.workspaces.last)||status.settings.default_workspace||'';
    ONBOARDING.form.model=status.settings.default_model||current.model||'';
    ONBOARDING.form.password='';
    ONBOARDING.form.apiKey='';
    ONBOARDING.form.baseUrl=current.base_url||'';
    ONBOARDING.step=0;
    ONBOARDING.savedOnce=false;
    ONBOARDING.confirmOverwrite=false;
    ONBOARDING.overwriteConflict=null;
    ONBOARDING.preflight=null;
    ONBOARDING.preflightState='loading';
    ONBOARDING.preflightError='';
    ONBOARDING.active=!status.completed;
    if(!ONBOARDING.active){
      _getOnboardingDialog().close();
      _syncOnboardingResumeEntry();
      return false;
    }
    _renderOnboardingSteps();
    _renderOnboardingBody();
    _getOnboardingDialog().open();
    _syncOnboardingResumeEntry();
    try{
      await _loadSetupPreflight();
    }catch(e){
      _setOnboardingNotice('检查状态读取失败：'+((e&&e.message)||String(e)),'warn');
    }
    return true;
  }catch(e){
    console.warn('onboarding status failed',e);
    ONBOARDING.status={
      completed:false,
      settings:{default_model:'',default_workspace:'',password_enabled:false,bot_name:'taiji Agent'},
      system:{hermes_found:false,imports_ok:false,config_exists:false,chat_ready:false,provider_configured:false,provider_ready:false,setup_state:'unavailable',provider_note:'状态 API 暂不可用。',current_provider:'',current_model:''},
      setup:{providers:[],categories:[],unsupported_note:'',current_is_oauth:false,current:{provider:'',model:'',base_url:''}},
      workspaces:{items:[],last:null},
      models:[],
      preflight:null,
    };
    ONBOARDING.form={provider:'',workspace:'',model:'',password:'',apiKey:'',baseUrl:''};
    ONBOARDING.step=0;
    ONBOARDING.savedOnce=false;
    ONBOARDING.confirmOverwrite=false;
    ONBOARDING.overwriteConflict=null;
    ONBOARDING.preflight=null;
    ONBOARDING.preflightState='error';
    ONBOARDING.preflightError='无法读取首次启动状态：'+((e&&e.message)||String(e));
    ONBOARDING.statusLoadFailed=true;
    ONBOARDING.active=true;
    _renderOnboardingSteps();
    _renderOnboardingBody();
    _getOnboardingDialog().open();
    _syncOnboardingResumeEntry();
    requestAnimationFrame(()=>{const retry=$('onboardingStatusRetryBtn');if(retry)retry.focus();});
    return true;
  }
}

async function resumeOnboardingWizard(){
  const resume=$('onboardingResumeBtn');
  if(resume)resume.disabled=true;
  try{
    return await loadOnboardingWizard();
  }finally{
    if(resume)resume.disabled=false;
  }
}

function prevOnboardingStep(){
  if(ONBOARDING.step===0||ONBOARDING.busy)return;
  ONBOARDING.step--;
  _renderOnboardingSteps();
  _renderOnboardingBody();
}

async function _saveOnboardingProviderSetup(){
  const provider=(ONBOARDING.form.provider||'').trim();
  const model=(ONBOARDING.form.model||'').trim();
  const apiKey=(ONBOARDING.form.apiKey||'').trim();
  const baseUrl=(ONBOARDING.form.baseUrl||'').trim();
  const current=_getOnboardingCurrentSetup();
  const isUnchanged=current.provider===provider&&((current.model||'')===model)&&((current.base_url||'')===baseUrl);
  // Skip the POST when nothing changed.  We also skip when the provider is
  // unsupported/OAuth-based and already working — chat_ready may be false for
  // providers not in the quick-setup list (e.g. minimax-cn) even though they are
  // fully configured.  Posting in that case would either be a no-op (the server
  // just marks complete for unsupported providers) or could silently overwrite
  // the existing provider configuration if the user accidentally changed the
  // provider dropdown.
  const currentIsOauth=!!(ONBOARDING.status&&ONBOARDING.status.setup&&ONBOARDING.status.setup.current_is_oauth);
  if(isUnchanged && !apiKey && ((ONBOARDING.status.system||{}).chat_ready || currentIsOauth)) return;
  const body={provider,model};
  if(apiKey) body.api_key=apiKey;
  if(baseUrl) body.base_url=baseUrl;
  if(ONBOARDING.confirmOverwrite)body.confirm_overwrite=true;
  try{
    const status=await api('/api/onboarding/setup',{method:'POST',body:JSON.stringify(body)});
    ONBOARDING.status=status;
    if(status&&status.preflight){
      ONBOARDING.preflight=status.preflight;
      ONBOARDING.preflightState='ready';
    }
    ONBOARDING.form.apiKey='';
    ONBOARDING.confirmOverwrite=false;
    ONBOARDING.overwriteConflict=null;
  }catch(e){
    const payload=e&&e.payload;
    if(e&&e.status===409&&payload&&payload.error==='config_exists'){
      ONBOARDING.confirmOverwrite=false;
      ONBOARDING.overwriteConflict={message:payload.message||e.message};
      e.onboardingConflict=true;
      _renderOnboardingBody();
      requestAnimationFrame(()=>{const btn=$('onboardingConfirmOverwriteBtn');if(btn)btn.focus();});
    }
    throw e;
  }
}

async function _saveOnboardingDefaults(){
  const workspace=(ONBOARDING.form.workspace||'').trim();
  const model=(ONBOARDING.form.model||'').trim();
  const password=(ONBOARDING.form.password||'').trim();
  if(!workspace) throw new Error(t('onboarding_error_choose_workspace'));
  if(!model) throw new Error(t('onboarding_error_choose_model'));
  const known=_getOnboardingWorkspaceChoices().some(ws=>ws.path===workspace);
  if(!known){
    await api('/api/workspaces/add',{method:'POST',body:JSON.stringify({path:workspace})});
  }
  // Model persisted by /api/onboarding/setup — no /api/default-model call needed here
  const body={default_workspace:workspace};
  if(password) body._set_password=password;
  const saved=await api('/api/settings',{method:'POST',body:JSON.stringify(body)});
  if(ONBOARDING.status){
    ONBOARDING.status.settings={...(ONBOARDING.status.settings||{}),password_enabled:!!saved.auth_enabled};
  }
  try{localStorage.setItem('hermes-webui-model',model)}catch{}
  if($('modelSelect')) _applyModelToDropdown(model,$('modelSelect'));
}

async function _finishOnboarding(){
  if(ONBOARDING.busy)return false;
  _setOnboardingBusy(true);
  try{
    await _saveOnboardingProviderSetup();
    await _saveOnboardingDefaults();
    ONBOARDING.savedOnce=true;

    try{
      ONBOARDING.preflight=await api('/api/setup/status');
      ONBOARDING.preflightState='ready';
      ONBOARDING.preflightError='';
    }catch(e){
      ONBOARDING.preflight=null;
      ONBOARDING.preflightState='error';
      ONBOARDING.preflightError=(e&&e.message)||String(e);
      _renderOnboardingBody();
      _setOnboardingNotice('配置已保存，但复检 API 失败；本次不会标记完成。','warn');
      return false;
    }

    if(!ONBOARDING.preflight.overall_ready){
      _renderOnboardingBody();
      _setOnboardingNotice('配置已保存，但仍有检查项未通过。请逐项处理并重新检查。','warn');
      const blocked=document.querySelector('.onboarding-check-row.blocked');
      if(blocked)blocked.focus();
      return false;
    }

    let done;
    try{
      done=await api('/api/onboarding/complete',{method:'POST',body:'{}'});
    }catch(e){
      if(e&&e.status===409&&e.payload&&e.payload.preflight){
        ONBOARDING.preflight=e.payload.preflight;
        ONBOARDING.preflightState='ready';
        ONBOARDING.savedOnce=true;
        _renderOnboardingBody();
      }
      throw e;
    }
    if(!done||done.completed!==true||!done.preflight||done.preflight.overall_ready!==true){
      ONBOARDING.status=done||ONBOARDING.status;
      ONBOARDING.preflight=(done&&done.preflight)||null;
      ONBOARDING.preflightState=ONBOARDING.preflight?'ready':'error';
      ONBOARDING.preflightError=ONBOARDING.preflight?'':'服务端没有返回一致的完成状态。';
      ONBOARDING.savedOnce=true;
      _renderOnboardingBody();
      _setOnboardingNotice('服务端未确认配置已完成；窗口将保持打开，请重新检查。','warn');
      return false;
    }
    ONBOARDING.status=done;
    ONBOARDING.active=false;
    _getOnboardingDialog().close();
    _syncOnboardingResumeEntry();
    showToast(t('onboarding_complete'));
    await loadWorkspaceList();
    if(typeof renderSessionList==='function') await renderSessionList();
    if(!S.session && typeof newSession==='function'){
      await newSession(true);
      await renderSessionList();
    }
    return true;
  }catch(e){
    if(e&&e.onboardingConflict){
      _setOnboardingNotice('当前已有配置。只有在你明确确认后才会覆盖并重试。','warn');
      return false;
    }
    _setOnboardingNotice((e&&e.message)||String(e),'warn');
    return false;
  }finally{
    _setOnboardingBusy(false);
  }
}

async function confirmOnboardingOverwrite(){
  if(ONBOARDING.busy)return;
  ONBOARDING.confirmOverwrite=true;
  ONBOARDING.overwriteConflict=null;
  _renderOnboardingBody();
  await _finishOnboarding();
}

function cancelOnboardingOverwrite(){
  ONBOARDING.confirmOverwrite=false;
  ONBOARDING.overwriteConflict=null;
  _renderOnboardingBody();
  const back=$('onboardingBackBtn');
  if(back)back.focus();
}

function dismissOnboardingWizard({focusResume=true}={}){
  // Dismissal is deliberately local-only. Keep a persistent, keyboard-usable
  // re-entry control visible until the server confirms setup is complete.
  _getOnboardingDialog().close({restoreFocus:false});
  _syncOnboardingResumeEntry();
  if(focusResume)requestAnimationFrame(()=>{
    const resume=$('onboardingResumeBtn');
    const resumeVisible=resume&&!resume.hidden&&resume.getClientRects().length>0;
    if(resumeVisible){resume.focus();return;}
    const workbenchClose=document.querySelector('#expertTeamV3Workbench:not(.is-collapsed) [data-et3-action="close-workbench"]');
    if(workbenchClose&&workbenchClose.getClientRects().length>0){workbenchClose.focus();return;}
    $('msg')?.focus();
  });
}

async function skipOnboarding(){
  dismissOnboardingWizard();
  showToast('检查窗口已暂时关闭；未通过检查前不会标记完成。',4000,'info');
}

async function nextOnboardingStep(){
  if(ONBOARDING.busy)return;
  try{
    if(ONBOARDING.steps[ONBOARDING.step]==='finish'){
      await _finishOnboarding();
      return;
    }
    _setOnboardingBusy(true);
    if(ONBOARDING.steps[ONBOARDING.step]==='setup'){
      ONBOARDING.form.provider=(($('onboardingProviderSelect')||{}).value||ONBOARDING.form.provider||'').trim();
      ONBOARDING.form.apiKey=(($('onboardingApiKeyInput')||{}).value||'').trim();
      ONBOARDING.form.baseUrl=(($('onboardingBaseUrlInput')||{}).value||ONBOARDING.form.baseUrl||'').trim();
      if(!ONBOARDING.form.provider) throw new Error(t('onboarding_error_provider_required'));
      if(ONBOARDING.form.provider==='custom' && !ONBOARDING.form.baseUrl) throw new Error(t('onboarding_error_base_url_required'));
      // For self-hosted providers (requires_base_url=True), gate Continue on a
      // successful probe of <base_url>/models — otherwise the wizard would
      // happily persist an unreachable URL and finish in 200ms with no
      // outbound HTTP, exactly the bug in #1499.  Run the probe synchronously
      // here, then check status; the probe is idempotent & cached on
      // (provider, baseUrl, apiKey) so this rarely triggers a second network
      // call when the user already saw a green banner.
      const cat=_getOnboardingSetupProvider(ONBOARDING.form.provider);
      if(cat&&cat.requires_base_url){
        if(!ONBOARDING.form.baseUrl) throw new Error(t('onboarding_error_base_url_required'));
        await _runOnboardingProbe();
        if(ONBOARDING.probe.status!=='ok'){
          // Surface the same localized error string the inline banner shows.
          const msg=_onboardingProbeMessage(ONBOARDING.probe)||t('onboarding_error_probe_failed')||'Could not reach the configured base URL.';
          throw new Error(msg);
        }
      }
    }
    if(ONBOARDING.steps[ONBOARDING.step]==='workspace'){
      ONBOARDING.form.workspace=(($('onboardingWorkspaceInput')||{}).value||ONBOARDING.form.workspace||'').trim();
      ONBOARDING.form.model=(($('onboardingModelInput')||{}).value||($('onboardingModelSelect')||{}).value||ONBOARDING.form.model||'').trim();
      if(!ONBOARDING.form.workspace) throw new Error(t('onboarding_error_workspace_required'));
      if(!ONBOARDING.form.model) throw new Error(t('onboarding_error_model_required'));
    }
    if(ONBOARDING.steps[ONBOARDING.step]==='password'){
      ONBOARDING.form.password=(($('onboardingPasswordInput')||{}).value||'').trim();
    }
    ONBOARDING.step++;
    _renderOnboardingSteps();
    _renderOnboardingBody();
  }catch(e){
    _setOnboardingNotice(e.message||String(e),'warn');
  }finally{
    _setOnboardingBusy(false);
  }
}

/* ── Codex OAuth device-code flow ── */
let _codexOAuthPollTimer=null;
let _codexOAuthFlowId=null;

function _clearCodexOAuthPoll(){
  if(_codexOAuthPollTimer){clearTimeout(_codexOAuthPollTimer);_codexOAuthPollTimer=null;}
}

function _setCodexOAuthButton(enabled){
  const btn=$('codexOAuthBtn');
  if(btn){btn.disabled=!enabled;btn.textContent=enabled?t('oauth_login_codex'):'...';}
}

async function copyCodexOAuthCode(code){
  try{
    await navigator.clipboard.writeText(code||'');
    showToast('代码已复制');
  }catch(e){
    showToast(code||'');
  }
}

async function cancelCodexOAuth(){
  const flowDiv=$('codexOAuthFlow');
  const flowId=_codexOAuthFlowId;
  _clearCodexOAuthPoll();
  _codexOAuthFlowId=null;
  if(flowId){
    try{await api('/api/onboarding/oauth/cancel',{method:'POST',body:JSON.stringify({flow_id:flowId})});}catch(e){}
  }
  _setCodexOAuthButton(true);
  if(flowDiv){
    flowDiv.innerHTML=`<div class="onboarding-oauth-card"><div class="onboarding-oauth-icon">⏹</div><div><strong>OAuth 登录已取消</strong><p style="margin-top:6px;color:var(--muted);font-size:13px">准备好后可以重新开始。</p></div></div>`;
  }
}

function _renderCodexOAuthTerminal(status,message){
  const flowDiv=$('codexOAuthFlow');
  if(!flowDiv)return;
  const ok=status==='success';
  const icon=ok?'✅':status==='expired'?'⌛':status==='cancelled'?'⏹':'❌';
  const title=ok?t('oauth_codex_success'):(status==='expired'?t('oauth_codex_expired'):(status==='cancelled'?'OAuth 登录已取消':t('oauth_codex_error')));
  flowDiv.innerHTML=`
    <div class="onboarding-oauth-card ${ok?'onboarding-oauth-ready':''}" ${ok?'':'style="border-color:var(--error,#e55)"'}>
      <div class="onboarding-oauth-icon">${icon}</div>
      <div><strong>${title}</strong><p style="margin-top:6px;color:var(--muted);font-size:13px">${esc(message||'')}</p></div>
    </div>`;
}

async function _pollCodexOAuth(){
  const flowId=_codexOAuthFlowId;
  if(!flowId)return;
  try{
    const resp=await api('/api/onboarding/oauth/poll?flow_id='+encodeURIComponent(flowId));
    const status=(resp&&resp.status)||'error';
    if(status==='pending'){
      _codexOAuthPollTimer=setTimeout(_pollCodexOAuth,3000);
      return;
    }
    _clearCodexOAuthPoll();
    _codexOAuthFlowId=null;
    _setCodexOAuthButton(true);
    if(status==='success'){
      _renderCodexOAuthTerminal('success','Credentials saved to the taiji Agent credential pool. Refreshing provider status…');
      showToast(t('oauth_codex_success'));
      try{await loadOnboardingWizard();}catch(e){}
    }else if(status==='expired'){
      _renderCodexOAuthTerminal('expired','The code expired. Start a new login flow to try again.');
    }else if(status==='cancelled'){
      _renderCodexOAuthTerminal('cancelled','The login flow was cancelled.');
    }else{
      _renderCodexOAuthTerminal('error',(resp&&resp.error)||'OAuth login failed. Please try again.');
    }
  }catch(e){
    _clearCodexOAuthPoll();
    _codexOAuthFlowId=null;
    _setCodexOAuthButton(true);
    _renderCodexOAuthTerminal('error',(e&&e.message)||String(e));
  }
}

async function startCodexOAuth(){
  const flowDiv=$('codexOAuthFlow');
  if(!flowDiv)return;
  _clearCodexOAuthPoll();
  _codexOAuthFlowId=null;
  _setCodexOAuthButton(false);
  flowDiv.style.display='block';
  flowDiv.innerHTML=`<div class="onboarding-oauth-card onboarding-oauth-pending"><div class="onboarding-oauth-icon">⏳</div><div><strong>${t('oauth_codex_polling')}</strong><p>Starting device-code flow…</p></div></div>`;
  try{
    const resp=await api('/api/onboarding/oauth/start',{method:'POST',body:JSON.stringify({provider:'openai-codex'})});
    if(resp.error) throw new Error(resp.error);
    const{flow_id,user_code,verification_uri}=resp;
    if(!flow_id||!user_code||!verification_uri) throw new Error('Invalid OAuth response');
    _codexOAuthFlowId=flow_id;
    flowDiv.innerHTML=`
      <div class="onboarding-oauth-card onboarding-oauth-pending">
        <div class="onboarding-oauth-icon">📋</div>
        <div style="flex:1">
          <strong>${t('oauth_codex_step1')}</strong>
          <p><a href="${esc(verification_uri)}" target="_blank" rel="noopener" style="color:var(--accent);word-break:break-all">${esc(verification_uri)}</a></p>
          <p style="margin-top:8px"><strong>${t('oauth_codex_step2')}</strong></p>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:4px">
            <code style="display:inline-block;font-size:18px;letter-spacing:0.1em;background:rgba(255,255,255,.08);padding:6px 14px;border-radius:8px;user-select:all">${esc(user_code)}</code>
            <button class="sm-btn" type="button" onclick="copyCodexOAuthCode('${esc(user_code)}')">Copy code</button>
            <button class="sm-btn" type="button" onclick="cancelCodexOAuth()">Cancel</button>
          </div>
          <p style="margin-top:8px;color:var(--muted);font-size:13px">${t('oauth_codex_polling')}</p>
        </div>
      </div>`;
    _codexOAuthPollTimer=setTimeout(_pollCodexOAuth,Math.max(1000,Number(resp.poll_interval_seconds||3)*1000));
  }catch(e){
    _clearCodexOAuthPoll();
    _codexOAuthFlowId=null;
    _renderCodexOAuthTerminal('error',(e&&e.message)||String(e));
    _setCodexOAuthButton(true);
  }
}

/* ── Anthropic / Claude Code credential-link flow ── */
let _anthropicOAuthPollTimer=null;
let _anthropicOAuthFlowId=null;

function _clearAnthropicOAuthPoll(){
  if(_anthropicOAuthPollTimer){clearTimeout(_anthropicOAuthPollTimer);_anthropicOAuthPollTimer=null;}
}

function _setAnthropicOAuthButton(enabled){
  const btn=$('anthropicOAuthBtn');
  if(btn){btn.disabled=!enabled;btn.textContent=enabled?'使用 Claude Code 登录':'处理中…';}
}

function _isAnthropicOAuthActive(status){
  return status==='pending'||status==='linking'||status==='committing';
}

function _scheduleAnthropicOAuthPoll(delayMs){
  _clearAnthropicOAuthPoll();
  if(_anthropicOAuthFlowId){
    _anthropicOAuthPollTimer=setTimeout(_pollAnthropicOAuth,Math.max(1000,Number(delayMs||3000)));
  }
}

function _renderAnthropicOAuthProgress(status,message){
  const flowDiv=$('anthropicOAuthFlow');
  if(!flowDiv)return;
  const finishing=status==='linking'||status==='committing';
  const title=finishing?'正在完成 Claude Code OAuth 关联…':'正在等待 Claude Code 凭据…';
  const detail=message||(finishing
    ?'taiji Agent 正在安全完成凭据关联，此步骤已无法取消。'
    :"请在服务器上运行“claude setup-token”，然后返回此处。taiji Agent 会自动检测凭据。");
  flowDiv.style.display='block';
  flowDiv.innerHTML=`
    <div class="onboarding-oauth-card onboarding-oauth-pending">
      <div class="onboarding-oauth-icon">${finishing?'🔐':'🖥️'}</div>
      <div style="flex:1">
        <strong>${title}</strong>
        <p style="margin-top:6px">${esc(detail)}</p>
        ${finishing?'':`<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px">
          <code style="display:inline-block;background:rgba(255,255,255,.08);padding:6px 10px;border-radius:8px;user-select:all">claude setup-token</code>
          <button class="sm-btn" type="button" onclick="cancelAnthropicOAuth()">取消</button>
        </div>`}
      </div>
    </div>`;
}

async function cancelAnthropicOAuth(){
  const flowId=_anthropicOAuthFlowId;
  if(!flowId)return;
  _clearAnthropicOAuthPoll();
  _setAnthropicOAuthButton(false);
  try{
    const resp=await api('/api/onboarding/oauth/cancel',{method:'POST',body:JSON.stringify({flow_id:flowId,provider:'anthropic'})});
    const status=(resp&&resp.status)||'error';
    if(_isAnthropicOAuthActive(status)){
      _renderAnthropicOAuthProgress(status,status==='pending'
        ?'正在处理取消请求。taiji Agent 将继续检查服务器的权威状态。'
        :'服务器已开始安全提交，taiji Agent 正在完成关联。');
      _scheduleAnthropicOAuthPoll(3000);
      return;
    }
    _anthropicOAuthFlowId=null;
    _setAnthropicOAuthButton(true);
    if(status==='success'){
      _renderAnthropicOAuthTerminal('success','taiji Agent 现已关联 Claude Code 凭据。');
      try{await loadOnboardingWizard();}catch(e){}
    }else if(status==='cancelled'){
      _renderAnthropicOAuthTerminal('cancelled','登录流程已在凭据关联开始前取消。');
    }else if(status==='expired'){
      _renderAnthropicOAuthTerminal('expired','凭据关联流程在取消完成前已过期。');
    }else{
      _renderAnthropicOAuthTerminal('error',(resp&&resp.error)||'无法确认取消结果。');
    }
  }catch(e){
    _renderAnthropicOAuthProgress(
      'pending',
      `取消请求失败（${(e&&e.message)||String(e)}）。正在继续检查服务器状态；尚未声明取消成功。`
    );
    _scheduleAnthropicOAuthPoll(3000);
  }
}

function _renderAnthropicOAuthTerminal(status,message){
  const flowDiv=$('anthropicOAuthFlow');
  if(!flowDiv)return;
  const ok=status==='success';
  const icon=ok?'✅':status==='expired'?'⌛':status==='cancelled'?'⏹':'❌';
  const title=ok?'Claude Code OAuth 已关联':(status==='expired'?'Claude Code 轮询已过期':(status==='cancelled'?'Claude Code OAuth 已取消':'Claude Code OAuth 失败'));
  flowDiv.style.display='block';
  flowDiv.innerHTML=`
    <div class="onboarding-oauth-card ${ok?'onboarding-oauth-ready':''}" ${ok?'':'style="border-color:var(--error,#e55)"'}>
      <div class="onboarding-oauth-icon">${icon}</div>
      <div><strong>${title}</strong><p style="margin-top:6px;color:var(--muted);font-size:13px">${esc(message||'')}</p></div>
    </div>`;
}

async function _pollAnthropicOAuth(){
  const flowId=_anthropicOAuthFlowId;
  if(!flowId)return;
  try{
    const resp=await api('/api/onboarding/oauth/poll?flow_id='+encodeURIComponent(flowId));
    const status=(resp&&resp.status)||'error';
    if(_isAnthropicOAuthActive(status)){
      if(status!=='pending')_renderAnthropicOAuthProgress(status);
      _scheduleAnthropicOAuthPoll(3000);
      return;
    }
    _clearAnthropicOAuthPoll();
    _anthropicOAuthFlowId=null;
    _setAnthropicOAuthButton(true);
    if(status==='success'){
      _renderAnthropicOAuthTerminal('success','taiji Agent 已关联 Claude Code 凭据，正在刷新提供商状态…');
      showToast('Claude Code OAuth 已连接');
      try{await loadOnboardingWizard();}catch(e){}
    }else if(status==='expired'){
      _renderAnthropicOAuthTerminal('expired','在此流程过期前未检测到 Claude Code 凭据。请启动新流程后重试。');
    }else if(status==='cancelled'){
      _renderAnthropicOAuthTerminal('cancelled','登录流程已取消。');
    }else{
      _renderAnthropicOAuthTerminal('error',(resp&&resp.error)||'Claude Code OAuth 关联失败，请重试。');
    }
  }catch(e){
    _renderAnthropicOAuthProgress(
      'pending',
      `状态检查失败（${(e&&e.message)||String(e)}）。将在不更改服务器端流程的情况下重试。`
    );
    _scheduleAnthropicOAuthPoll(3000);
  }
}

async function startAnthropicOAuth(){
  const flowDiv=$('anthropicOAuthFlow');
  if(!flowDiv)return;
  _clearAnthropicOAuthPoll();
  _anthropicOAuthFlowId=null;
  _setAnthropicOAuthButton(false);
  flowDiv.style.display='block';
  flowDiv.innerHTML=`<div class="onboarding-oauth-card onboarding-oauth-pending"><div class="onboarding-oauth-icon">⏳</div><div><strong>正在检查 Claude Code 凭据…</strong><p>taiji Agent 正在检查此服务器上已有的 Claude Code OAuth 凭据。</p></div></div>`;
  try{
    const resp=await api('/api/onboarding/oauth/start',{method:'POST',body:JSON.stringify({provider:'anthropic'})});
    if(resp.error) throw new Error(resp.error);
    const{flow_id,status,action_required}=resp;
    if(!flow_id) throw new Error('OAuth 响应无效');
    _anthropicOAuthFlowId=flow_id;
    if(status==='success'){
      _clearAnthropicOAuthPoll();
      _anthropicOAuthFlowId=null;
      _setAnthropicOAuthButton(true);
      _renderAnthropicOAuthTerminal('success','taiji Agent 已关联 Claude Code 凭据，正在刷新提供商状态…');
      showToast('Claude Code OAuth 已连接');
      try{await loadOnboardingWizard();}catch(e){}
      return;
    }
    if(!_isAnthropicOAuthActive(status)){
      throw new Error((resp&&resp.error)||'Claude Code OAuth 状态异常');
    }
    _renderAnthropicOAuthProgress(status,action_required);
    _scheduleAnthropicOAuthPoll(Number(resp.poll_interval_seconds||3)*1000);
  }catch(e){
    _clearAnthropicOAuthPoll();
    _anthropicOAuthFlowId=null;
    _renderAnthropicOAuthTerminal('error',(e&&e.message)||String(e));
    _setAnthropicOAuthButton(true);
  }
}
