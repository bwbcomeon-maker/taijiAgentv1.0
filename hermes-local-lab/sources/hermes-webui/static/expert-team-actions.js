(function(){
  const workspaceTabByRun=Object.create(null);
  const mutationEndpoints={
    answer:'/api/expert-teams/answer',
    resume:'/api/expert-teams/resume',
    cancel:'/api/expert-teams/cancel',
    submit_stage_input:'/api/expert-teams/stage/input',
    approve_stage:'/api/expert-teams/stage/approve',
    revise_stage:'/api/expert-teams/stage/revise',
  };
  const mutationInFlight=new Map();
  const mutationIdempotencyKeys=new Map();
  let mutationNonce=0;
  let activeIdentityLoginAttempt=null;
  function activeExpertTeamCard(btn){
    const root=btn&&btn.closest&&btn.closest('[data-expert-team-run-id]');
    const active=(typeof window!=='undefined'&&window._activeExpertTeamStatusCard)||{};
    if(!root||!root.dataset)return active;
    return {
      ...active,
      runId:root.dataset.expertTeamRunId||active.runId,
      schemaVersion:Number(root.dataset.expertTeamSchemaVersion||active.schemaVersion||0),
      version:Number(root.dataset.expertTeamVersion||active.version||0),
      currentStageId:root.dataset.expertTeamStageId||active.currentStageId||'',
      executionStreamId:root.dataset.expertTeamStreamId||active.executionStreamId||'',
      pendingInputId:root.dataset.expertTeamInputId||active.pendingInputId||'',
      stageReviewId:root.dataset.expertTeamReviewId||active.stageReviewId||'',
      readOnly:root.dataset.expertTeamReadOnly==='true'||active.readOnly===true,
    };
  }
  function mutationIdempotencyKey(base,card,action){
    if(action==='retry_cancel'&&card.cancelRequestId)return String(card.cancelRequestId);
    if(mutationIdempotencyKeys.has(base))return mutationIdempotencyKeys.get(base);
    const uuid=(typeof crypto!=='undefined'&&crypto&&typeof crypto.randomUUID==='function')
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${(++mutationNonce).toString(36)}`;
    const key=`expert-team:${card.runId}:${card.version}:${card.currentStageId}:${action}:${uuid}`;
    mutationIdempotencyKeys.set(base,key);
    return key;
  }
  function expertTeamMutationContract(btn,action){
    const card=activeExpertTeamCard(btn);
    const sid=(typeof S!=='undefined'&&S.session&&S.session.session_id)||card.sourceSessionId||'';
    const schemaVersion=Number(card.schemaVersion||0);
    if(card.readOnly||schemaVersion<2)throw new Error('历史专家团任务仅支持查看，请新建任务后继续。');
    if(!card.runId||!sid||!card.currentStageId||!Number.isFinite(Number(card.version)))throw new Error('专家团任务状态不完整，请刷新后重试。');
    const base=`${card.runId}:${card.version}:${card.currentStageId}:${action}`;
    const payload={
      run_id:String(card.runId),
      session_id:String(sid),
      expected_version:Number(card.version),
      stage_id:String(card.currentStageId),
      idempotency_key:mutationIdempotencyKey(base,card,action),
      ...(action==='submit_stage_input'?{input_id:card.pendingInputId}:{}),
      ...(action==='approve_stage'||action==='revise_stage'?{review_id:card.stageReviewId}:{}),
    };
    return {
      card,
      base,
      payload,
    };
  }
  function isExpertTeamExecutionStarted(data){
    const run=data&&data.run||{};
    const streamId=String(data&&data.stream_id||'').trim();
    const runStreamId=String(run.execution_stream_id||'').trim();
    const state=String(run.workflow_state||'').trim();
    return !!streamId&&!!runStreamId&&['generating','revising'].includes(state)&&runStreamId===streamId;
  }
  function isExpertTeamIntakeAccepted(run){
    run=run||{};
    const questions=Array.isArray(run.questions)?run.questions:[];
    const state=String(run.workflow_state||'').trim();
    if(!questions.length||['collecting_required','collecting_optional'].includes(state))return false;
    return !questions.some(question=>String(question&&question.status||'pending')==='pending');
  }
  function setMutationButtonBusy(btn,busy,label){
    if(!btn)return;
    if(busy){
      if(!Object.prototype.hasOwnProperty.call(btn,'_expertTeamOriginalText'))btn._expertTeamOriginalText=btn.textContent;
      btn.disabled=true;
      if(btn.setAttribute)btn.setAttribute('aria-busy','true');
      if(label)btn.textContent=label;
      return;
    }
    btn.disabled=false;
    if(btn.removeAttribute)btn.removeAttribute('aria-busy');
    if(Object.prototype.hasOwnProperty.call(btn,'_expertTeamOriginalText')){
      btn.textContent=btn._expertTeamOriginalText;
      delete btn._expertTeamOriginalText;
    }
  }
  function captureMutationFormState(btn){
    const root=btn&&btn.closest&&btn.closest('[data-expert-team-run-id]');
    if(typeof captureExpertTeamWorkspaceFormState==='function')return {root,state:captureExpertTeamWorkspaceFormState(root)};
    return {root,state:null};
  }
  function restoreMutationFormState(snapshot){
    if(!snapshot||!snapshot.state||typeof restoreExpertTeamWorkspaceFormState!=='function')return false;
    const panel=(typeof document!=='undefined'&&document.getElementById&&document.getElementById('expertTeamWorkspacePanel'))||snapshot.root;
    return restoreExpertTeamWorkspaceFormState(panel,snapshot.state);
  }
  function runExpertTeamMutation(btn,options){
    options=options||{};
    let contract;
    try{contract=expertTeamMutationContract(btn,String(options.action||''));}
    catch(error){
      if(typeof showToast==='function')showToast(error.message||String(error));
      return Promise.reject(error);
    }
    if(mutationInFlight.has(contract.base))return mutationInFlight.get(contract.base);
    const snapshot=captureMutationFormState(btn);
    setMutationButtonBusy(btn,true,options.busyLabel||'');
    const request=(async()=>{
      try{
        const data=await api(options.endpoint,{
          method:'POST',
          body:JSON.stringify({...options.payload,...contract.payload}),
        });
        const executionStarted=isExpertTeamExecutionStarted(data);
        const intakeAccepted=isExpertTeamIntakeAccepted(data&&data.run);
        if(intakeAccepted&&options.closeOnAcceptedIntake&&typeof closeExpertTeamQuestionPopover==='function')closeExpertTeamQuestionPopover(btn);
        applyExpertTeamActionResponse(data);
        if(!executionStarted&&options.preserveWithoutExecution)restoreMutationFormState(snapshot);
        return {data,executionStarted};
      }catch(error){
        const authoritativeRun=error&&error.payload&&error.payload.run;
        const intakeAccepted=isExpertTeamIntakeAccepted(authoritativeRun);
        if(intakeAccepted&&options.closeOnAcceptedIntake&&typeof closeExpertTeamQuestionPopover==='function')closeExpertTeamQuestionPopover(btn);
        if(authoritativeRun){
          applyExpertTeamActionResponse({run:error.payload.run});
        }
        restoreMutationFormState(snapshot);
        throw error;
      }finally{
        mutationInFlight.delete(contract.base);
        if(!btn||btn.isConnected!==false)setMutationButtonBusy(btn,false);
      }
    })();
    mutationInFlight.set(contract.base,request);
    return request;
  }
  function expertTeamMutationEndpoint(action){return mutationEndpoints[String(action||'')]||'';}
  function currentExpertTeamRunId(btn){
    const root=btn&&btn.closest&&btn.closest('[data-expert-team-run-id]');
    return (root&&root.dataset&&root.dataset.expertTeamRunId)||((window._activeExpertTeamStatusCard||{}).runId)||'';
  }
  function workspaceRunId(root){
    const source=root&&root.closest?root.closest('[data-expert-team-run-id]'):root;
    return source&&source.dataset&&source.dataset.expertTeamRunId||'';
  }
  function normalizeExpertTeamWorkspaceTab(tab){
    tab=String(tab||'');
    if(tab==='todo')return 'task';
    if(tab==='flow'||tab==='members'||tab==='collaboration')return 'process';
    return tab;
  }
  function applyExpertTeamWorkspaceTab(root,tab){
    tab=normalizeExpertTeamWorkspaceTab(tab);
    if(!root||!tab)return false;
    const target=root.querySelector&&root.querySelector(`[data-expert-team-workspace-tab="${tab}"]`);
    if(!target)return false;
    root.querySelectorAll('[data-expert-team-workspace-tab]').forEach(item=>{
      const active=item.dataset&&item.dataset.expertTeamWorkspaceTab===tab;
      item.classList.toggle('is-active',active);
      item.setAttribute('aria-selected',active?'true':'false');
      item.setAttribute('tabindex',active?'0':'-1');
    });
    root.querySelectorAll('[data-expert-team-tab-panel]').forEach(panel=>{
      panel.hidden=!(panel.dataset&&panel.dataset.expertTeamTabPanel===tab);
    });
    return true;
  }
  function handleExpertTeamWorkspaceTabKeydown(event){
    const current=event&&event.target&&event.target.closest&&event.target.closest('[data-expert-team-workspace-tab]');
    const root=current&&current.closest&&current.closest('.expert-team-panel-inner');
    if(!current||!root)return false;
    const tabs=Array.from(root.querySelectorAll('[data-expert-team-workspace-tab]'));
    const index=tabs.indexOf(current);
    let next=-1;
    if(event.key==='ArrowLeft'||event.key==='ArrowUp')next=(index-1+tabs.length)%tabs.length;
    else if(event.key==='ArrowRight'||event.key==='ArrowDown')next=(index+1)%tabs.length;
    else if(event.key==='Home')next=0;
    else if(event.key==='End')next=tabs.length-1;
    if(next<0||!tabs[next])return false;
    event.preventDefault();
    const target=tabs[next];
    const tab=normalizeExpertTeamWorkspaceTab(target.dataset&&target.dataset.expertTeamWorkspaceTab);
    rememberExpertTeamWorkspaceTab(root,tab);
    applyExpertTeamWorkspaceTab(root,tab);
    if(target.focus)target.focus();
    return true;
  }
  function rememberExpertTeamWorkspaceTab(root,tab){
    tab=normalizeExpertTeamWorkspaceTab(tab);
    const runId=workspaceRunId(root);
    if(runId&&tab)workspaceTabByRun[runId]=tab;
  }
  function restoreExpertTeamWorkspaceTab(root){
    const panel=root&&root.querySelector?root:document.getElementById('expertTeamWorkspacePanel');
    const inner=panel&&panel.querySelector?panel.querySelector('.expert-team-panel-inner'):(panel&&panel.classList&&panel.classList.contains('expert-team-panel-inner')?panel:null);
    if(!inner)return false;
    const tab=normalizeExpertTeamWorkspaceTab(workspaceTabByRun[workspaceRunId(inner)]||'task');
    return applyExpertTeamWorkspaceTab(inner,tab)||applyExpertTeamWorkspaceTab(inner,'task');
  }

  function expertTeamBriefForm(btn){
    return btn&&btn.closest?btn.closest('[data-expert-team-brief-editor]'):null;
  }
  function assignExpertTeamBriefValue(target,path,value){
    const parts=String(path||'').split('.').filter(Boolean);
    if(!parts.length)return target;
    let cursor=target;
    parts.forEach((part,index)=>{
      if(index===parts.length-1)cursor[part]=value;
      else cursor=cursor[part]||(cursor[part]={});
    });
    return target;
  }
  function collectExpertTeamBriefPayload(form){
    const patch={};
    if(!form||!form.querySelectorAll)return patch;
    let snapshot={};
    try{snapshot=JSON.parse(form.dataset&&form.dataset.expertTeamBriefSnapshot||'{}');}catch(_){}
    let currentControl={};
    let baselineControl={};
    const serializedControl=form.dataset&&form.dataset.expertTeamDocumentControl;
    if(serializedControl){
      try{
        const control=JSON.parse(serializedControl);
        if(control&&typeof control==='object'&&!Array.isArray(control)){
          currentControl=control;
          baselineControl=JSON.parse(JSON.stringify(control));
        }
      }catch(_){}
    }
    form.querySelectorAll('[name]').forEach(control=>{
      if(control.disabled)return;
      const name=String(control.name||'');
      const value=String(control.value==null?'':control.value).trim();
      if(name.startsWith('document_control.'))assignExpertTeamBriefValue(currentControl,name.slice('document_control.'.length),value);
      else if(!Object.prototype.hasOwnProperty.call(snapshot,name)||String(snapshot[name]==null?'':snapshot[name]).trim()!==value)assignExpertTeamBriefValue(patch,name,value);
    });
    const snapshotControl=snapshot.document_control&&typeof snapshot.document_control==='object'?snapshot.document_control:baselineControl;
    if(Object.keys(currentControl).length&&JSON.stringify(currentControl)!==JSON.stringify(snapshotControl))patch.document_control=currentControl;
    return patch;
  }
  function restoreExpertTeamBriefDirtyPatch(form,patch){
    if(!form||!form.querySelector||!patch||typeof patch!=='object')return false;
    const restore=(name,value)=>{const control=form.querySelector(`[name="${String(name).replace(/"/g,'\\"')}"]`);if(control)control.value=String(value==null?'':value);};
    Object.entries(patch).forEach(([name,value])=>{
      if(name==='document_control'&&value&&typeof value==='object')Object.entries(value).forEach(([child,childValue])=>restore(`document_control.${child}`,childValue));
      else restore(name,value);
    });
    return true;
  }
  function focusFirstExpertTeamBriefError(form,field,message){
    if(!form)return false;
    const name=String(field||'').replace(/^patch\./,'');
    const control=form.querySelector&&form.querySelector(`[name="${name.replace(/"/g,'\\"')}"]`);
    const error=form.querySelector&&form.querySelector(`[data-expert-team-field-error="${name.replace(/"/g,'\\"')}"]`);
    if(error)error.textContent=String(message||'请检查此字段。');
    if(control){
      if(control.setAttribute)control.setAttribute('aria-invalid','true');
      const described=String(control.getAttribute&&control.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean);
      if(error&&error.id&&!described.includes(error.id))described.push(error.id);
      if(described.length&&control.setAttribute)control.setAttribute('aria-describedby',described.join(' '));
      if(control.focus){try{control.focus({preventScroll:false});}catch(_){control.focus();}}
      return true;
    }
    return false;
  }
  function clearExpertTeamBriefErrors(form){
    if(!form||!form.querySelectorAll)return false;
    const errors=Array.from(form.querySelectorAll('[data-expert-team-field-error]'));
    const errorIds=new Set(errors.map(item=>String(item&&item.id||'')).filter(Boolean));
    errors.forEach(item=>{item.textContent='';});
    form.querySelectorAll('[aria-invalid="true"]').forEach(control=>{
      if(control.removeAttribute)control.removeAttribute('aria-invalid');
      const described=String(control.getAttribute&&control.getAttribute('aria-describedby')||'').split(/\s+/).filter(id=>id&&!errorIds.has(id));
      if(described.length&&control.setAttribute)control.setAttribute('aria-describedby',described.join(' '));
      else if(control.removeAttribute)control.removeAttribute('aria-describedby');
    });
    return true;
  }
  function expertTeamBriefContract(btn,action){
    const card=activeExpertTeamCard(btn);
    const sid=(typeof S!=='undefined'&&S.session&&S.session.session_id)||card.sourceSessionId||'';
    if(card.readOnly||Number(card.schemaVersion||0)<2)throw new Error('历史专家团任务仅支持查看，请新建任务后继续。');
    if(!card.runId||!sid||!Number.isFinite(Number(card.version)))throw new Error('专家团任务状态不完整，请刷新后重试。');
    const form=expertTeamBriefForm(btn);
    const revision=Number(form&&form.dataset&&form.dataset.expertTeamBriefRevision||card.brief&&card.brief.revision||0);
    const base=`${card.runId}:${card.version}:brief:${action}`;
    return {card,form,base,payload:{
      run_id:String(card.runId),session_id:String(sid),expected_version:Number(card.version),
      expected_brief_revision:revision,
      idempotency_key:mutationIdempotencyKey(base,{...card,currentStageId:'brief'},action),
    }};
  }
  async function submitExpertTeamBrief(btn,confirmAfterSave){
    let contract;
    try{contract=expertTeamBriefContract(btn,confirmAfterSave?'brief_confirm':'brief_update');}
    catch(error){if(typeof showToast==='function')showToast(error.message||String(error));return false;}
    if(mutationInFlight.has(contract.base))return mutationInFlight.get(contract.base);
    const dirtyPatch=collectExpertTeamBriefPayload(contract.form);
    clearExpertTeamBriefErrors(contract.form);
    setMutationButtonBusy(btn,true,confirmAfterSave?'正在确认...':'正在保存...');
    const request=(async()=>{
      let updateCommitted=false;
      try{
        let saved=null;
        if(Object.keys(dirtyPatch).length){
          saved=await api('/api/expert-teams/brief/update',{method:'POST',body:JSON.stringify({...contract.payload,patch:dirtyPatch})});
          updateCommitted=true;
        }
        let response=saved;
        if(confirmAfterSave){
          const updated=saved&&saved.run||{};
          response=await api('/api/expert-teams/brief/confirm',{method:'POST',body:JSON.stringify({
            run_id:contract.payload.run_id,session_id:contract.payload.session_id,
            expected_version:Number(updated.version||contract.payload.expected_version),
            expected_brief_revision:saved
              ?Number(updated.document_brief&&updated.document_brief.revision||contract.payload.expected_brief_revision+1)
              :contract.payload.expected_brief_revision,
            idempotency_key:`${contract.payload.idempotency_key}:confirm`,
          })});
        }
        if(response)applyExpertTeamActionResponse(response);
        if(typeof showToast==='function')showToast(confirmAfterSave?'文档规格已确认，请点击“开始生成”继续。':(updateCommitted?'文档规格已保存。':'没有需要保存的更改。'));
        return true;
      }catch(error){
        const payload=error&&error.payload||{};
        if(payload.run)applyExpertTeamActionResponse({run:payload.run});
        const livePanel=(typeof document!=='undefined'&&document.getElementById&&document.getElementById('expertTeamWorkspacePanel'))||contract.form;
        if(payload.run&&!updateCommitted)restoreExpertTeamBriefDirtyPatch(livePanel,dirtyPatch);
        focusFirstExpertTeamBriefError(livePanel,payload.field,error&&error.message||payload.error);
        if(typeof showToast==='function')showToast(updateCommitted&&confirmAfterSave?'规格已保存，但确认未完成：'+(error&&error.message||error):(payload.code==='brief_revision_conflict'?'规格已被更新，仅保留了本地修改字段，请核对后重试。':'文档规格未保存：'+(error&&error.message||error)));
        return false;
      }finally{
        mutationInFlight.delete(contract.base);
        if(!btn||btn.isConnected!==false)setMutationButtonBusy(btn,false);
      }
    })();
    mutationInFlight.set(contract.base,request);
    return request;
  }
  function expertTeamIdentityCapability(status){
    status=status&&typeof status==='object'?status:{};
    const principal=status.principal&&typeof status.principal==='object'?status.principal:{};
    const roles=Array.isArray(principal.roles)?principal.roles:[];
    if(status.enabled===false)return {allowed:false,label:'未配置企业身份提供方'};
    if(status.expired)return {allowed:false,label:'企业身份已过期'};
    if(!status.authenticated)return {allowed:false,label:'使用企业审批身份登录'};
    if(!roles.includes('document-approver'))return {allowed:false,label:'当前身份缺少文档审批权限'};
    return {allowed:true,label:String(principal.display_name||'企业审批身份')};
  }
  function applyExpertTeamIdentityStatus(status,returnFocus){
    const safeStatus=status&&typeof status==='object'?status:{};
    window._expertTeamIdentityStatus=safeStatus;
    if(window._activeExpertTeamStatusCard){
      window._activeExpertTeamStatusCard.identityStatus=safeStatus;
      if(typeof renderExpertTeamStatusSurface==='function')renderExpertTeamStatusSurface(window._activeExpertTeamStatusCard);
    }
    const capability=expertTeamIdentityCapability(safeStatus);
    if(typeof showToast==='function')showToast(capability.label);
    if(returnFocus&&returnFocus.focus){try{returnFocus.focus({preventScroll:true});}catch(_){returnFocus.focus();}}
    return capability;
  }
  function restoreExpertTeamIdentityFocus(btn){
    const action=btn&&btn.dataset&&btn.dataset.expertTeamIdentityAction;
    const panel=(typeof document!=='undefined'&&document.getElementById&&document.getElementById('expertTeamWorkspacePanel'))||null;
    const selector=action?`[data-expert-team-identity-action="${String(action).replace(/"/g,'\\"')}"]`:'[data-expert-team-identity-action]';
    const target=panel&&panel.querySelector&&(panel.querySelector(selector)||panel.querySelector('[data-expert-team-identity-action]'))||btn;
    if(target&&target.focus){try{target.focus({preventScroll:true});}catch(_){target.focus();}return true;}
    return false;
  }
  function setExpertTeamIdentityLoginPending(pending){
    const panel=(typeof document!=='undefined'&&document.getElementById&&document.getElementById('expertTeamWorkspacePanel'))||null;
    const cancel=panel&&panel.querySelector&&panel.querySelector('[data-expert-team-identity-action="cancel"]');
    if(cancel){cancel.hidden=!pending;cancel.disabled=!pending;}
    const login=panel&&panel.querySelector&&panel.querySelector('[data-expert-team-identity-action="login"]');
    if(!pending&&login)setMutationButtonBusy(login,false);
    return !!pending;
  }
  function abortActiveExpertTeamIdentityLogin(){
    if(!activeIdentityLoginAttempt)return false;
    const attempt=activeIdentityLoginAttempt;
    activeIdentityLoginAttempt=null;
    attempt.controller.abort();
    return true;
  }
  function cancelExpertTeamIdentityLogin(btn){
    if(!abortActiveExpertTeamIdentityLogin())return false;
    setExpertTeamIdentityLoginPending(false);
    if(typeof showToast==='function')showToast('企业身份登录已取消，审批仍保持禁用。');
    restoreExpertTeamIdentityFocus(btn);
    return true;
  }
  function expertTeamIdentityDelay(ms,signal){
    return new Promise((resolve,reject)=>{
      if(signal&&signal.aborted){const error=new Error('identity login aborted');error.name='AbortError';reject(error);return;}
      const timer=setTimeout(resolve,ms);
      if(signal&&signal.addEventListener)signal.addEventListener('abort',()=>{clearTimeout(timer);const error=new Error('identity login aborted');error.name='AbortError';reject(error);},{once:true});
    });
  }
  async function refreshExpertTeamIdentityStatus(btn,options){
    options=options||{};
    try{
      const status=await api('/api/expert-teams/identity/status');
      return applyExpertTeamIdentityStatus(status,options.restoreFocus?btn:null);
    }catch(error){
      if(typeof showToast==='function')showToast('企业身份状态检查失败：'+(error&&error.message||error));
      return {allowed:false,label:'企业身份状态检查失败'};
    }
  }
  async function startExpertTeamIdentityLogin(btn){
    abortActiveExpertTeamIdentityLogin();
    const loginAttempt={controller:new AbortController()};
    activeIdentityLoginAttempt=loginAttempt;
    setExpertTeamIdentityLoginPending(true);
    setMutationButtonBusy(btn,true,'正在打开登录...');
    try{
      const redirectUri=(window.location&&window.location.origin?window.location.origin:'')+'/api/expert-teams/identity/callback';
      const flow=await api('/api/expert-teams/identity/start',{method:'POST',signal:loginAttempt.controller.signal,body:JSON.stringify({redirect_uri:redirectUri,purpose:'login'})});
      if(activeIdentityLoginAttempt!==loginAttempt||loginAttempt.controller.signal.aborted)return false;
      window.open(String(flow&&flow.authorization_url||''),'_blank','noopener,noreferrer');
      if(typeof showToast==='function')showToast('已请求在系统浏览器中登录，完成后此处会自动更新。');
      for(let attempt=0;attempt<120;attempt+=1){
        await expertTeamIdentityDelay(1000,loginAttempt.controller.signal);
        const status=await api('/api/expert-teams/identity/status',{signal:loginAttempt.controller.signal});
        if(activeIdentityLoginAttempt!==loginAttempt||loginAttempt.controller.signal.aborted)return false;
        if(status&&['cancelled','failed'].includes(String(status.identity_flow_status||status.login_state||'')))return false;
        if(status&&status.authenticated){applyExpertTeamIdentityStatus(status,null);return true;}
      }
      if(typeof showToast==='function')showToast('企业身份登录已过期，请重新登录。');
      return false;
    }catch(error){
      if(error&&error.name==='AbortError')return false;
      if(typeof showToast==='function')showToast('企业身份登录失败：'+(error&&error.message||error));
      return false;
    }finally{
      if(activeIdentityLoginAttempt===loginAttempt){
        activeIdentityLoginAttempt=null;
        setExpertTeamIdentityLoginPending(false);
        if(!btn||btn.isConnected!==false)setMutationButtonBusy(btn,false);
        restoreExpertTeamIdentityFocus(btn);
      }
    }
  }
  async function logoutExpertTeamIdentity(btn){
    abortActiveExpertTeamIdentityLogin();
    setExpertTeamIdentityLoginPending(false);
    try{
      await api('/api/expert-teams/identity/logout',{method:'POST',body:'{}'});
      applyExpertTeamIdentityStatus({enabled:true,authenticated:false,provider:'oidc_pkce'},null);
      return true;
    }catch(error){if(typeof showToast==='function')showToast('退出企业身份失败：'+(error&&error.message||error));return false;}
    finally{restoreExpertTeamIdentityFocus(btn);}
  }
  async function refreshExpertTeamRun(btn){
    const card=activeExpertTeamCard(btn);
    const sid=(typeof S!=='undefined'&&S.session&&S.session.session_id)||card.sourceSessionId||'';
    if(!card.runId||!sid)return false;
    setMutationButtonBusy(btn,true,'正在刷新...');
    try{
      const data=await api(`/api/expert-teams/run?session_id=${encodeURIComponent(sid)}&run_id=${encodeURIComponent(card.runId)}`);
      applyExpertTeamActionResponse(data);
      const nextState=String(data&&data.run&&data.run.view&&data.run.view.presentation&&data.run.view.presentation.state||data&&data.run&&data.run.workflow_state||'');
      if(typeof showToast==='function'){
        if(nextState==='result_unverified')showToast('仍未找到可安全绑定的结果。已有内容不会自动重做，你可以稍后再次核验。');
        else showToast('专家团状态已核验。');
      }
      return true;
    }catch(error){
      if(typeof showToast==='function')showToast('刷新专家团状态失败：'+(error&&error.message||error));
      return false;
    }finally{
      if(!btn||btn.isConnected!==false)setMutationButtonBusy(btn,false);
    }
  }
  function applyExpertTeamActionResponse(data){
    const run=data&&data.run;
    if(run&&typeof _expertTeamStatusCardFromRun==='function'&&typeof renderExpertTeamStatusSurface==='function'){
      const card=_expertTeamStatusCardFromRun(run,data);
      if(card)renderExpertTeamStatusSurface(card);
    }
    if(typeof _applyExpertTeamStreamResponse==='function')_applyExpertTeamStreamResponse(data);
    if(typeof renderSessionList==='function')renderSessionList();
  }
  async function openExpertTeamFileArtifact(btn){
    const data=btn&&btn.dataset||{};
    const path=String(data.expertTeamArtifactPath||'').trim();
    const exists=String(data.expertTeamArtifactExists||'true')!=='false';
    const sid=(typeof S!=='undefined'&&S.session&&S.session.session_id)||'';
    if(!exists||!path){
      if(typeof showToast==='function')showToast('无法打开产物：文件不存在，请重新生成当前阶段。');
      return false;
    }
    if(!sid){
      if(typeof showToast==='function')showToast('无法打开产物：当前会话不可用。');
      return false;
    }
    setMutationButtonBusy(btn,true,'正在打开...');
    try{
      await api('/api/file/open',{
        method:'POST',
        body:JSON.stringify({session_id:sid,path}),
      });
      return true;
    }catch(error){
      if(typeof showToast==='function')showToast('打开产物失败：'+(error&&error.message||error));
      return false;
    }finally{
      if(!btn||btn.isConnected!==false)setMutationButtonBusy(btn,false);
    }
  }
  async function downloadExpertTeamFileArtifact(btn){
    const data=btn&&btn.dataset||{};
    const path=String(data.expertTeamArtifactPath||'').trim();
    const exists=String(data.expertTeamArtifactExists||'true')!=='false';
    if(!exists||!path){
      if(typeof showToast==='function')showToast('无法下载产物：文件不存在，请重新生成当前阶段。');
      return false;
    }
    if(typeof downloadFile!=='function'){
      if(typeof showToast==='function')showToast('无法下载产物：下载功能尚未就绪。');
      return false;
    }
    const filename=path.replace(/\\/g,'/').split('/').filter(Boolean).pop()||'专家团产物';
    setMutationButtonBusy(btn,true,'正在下载...');
    try{
      await Promise.resolve(downloadFile(path,filename));
      return true;
    }catch(error){
      if(typeof showToast==='function')showToast('下载产物失败：'+(error&&error.message||error));
      return false;
    }finally{
      if(!btn||btn.isConnected!==false)setMutationButtonBusy(btn,false);
    }
  }
  async function handleExpertTeamPresentationAction(btn){
    const action=btn&&btn.dataset?btn.dataset.expertTeamAction:'';
    const runId=currentExpertTeamRunId(btn);
    if(!action||!runId)return false;
    if(action==='answer_required'||action==='answer_optional'){
      if(typeof openExpertTeamQuestionPopover==='function')openExpertTeamQuestionPopover(btn);
      return true;
    }
    if(action==='relaunch'){
      const card=activeExpertTeamCard(btn);
      const teamId=String(card&&card.team&&card.team.id||'').trim();
      if(teamId&&typeof openWriteflowTeamModal==='function'){
        openWriteflowTeamModal(teamId);
        return true;
      }
      if(typeof showToast==='function')showToast('无法重新发起：专家团中心尚未就绪。');
      return false;
    }
    if(action==='start_generation'||action==='regenerate'){
      return typeof resumeExpertTeamRun==='function'?resumeExpertTeamRun(btn):false;
    }
    if(action==='regenerate_unverified'){
      if(typeof window!=='undefined'&&typeof window.confirm==='function'&&!window.confirm('已有结果可能尚未核验。放弃本次结果并重新生成会产生额外模型消耗，确定继续吗？'))return false;
      return typeof resumeExpertTeamRun==='function'?resumeExpertTeamRun(btn):false;
    }
    if(action==='cancel'){
      return typeof cancelExpertTeamRun==='function'?cancelExpertTeamRun(btn):false;
    }
    if(action==='retry_cancel'){
      return typeof cancelExpertTeamRun==='function'?cancelExpertTeamRun(btn,{skipConfirm:true,action:'retry_cancel'}):false;
    }
    if(action==='refresh'||action==='refresh_status'||action==='retry_cleanup'){
      return refreshExpertTeamRun(btn);
    }
    if(action==='submit_stage_input'){
      const root=btn&&btn.closest&&btn.closest('[data-expert-team-run-id]');
      const selected=root&&root.querySelector?root.querySelector('[data-expert-team-stage-input-choice].is-selected'):null;
      const input=root&&root.querySelector?root.querySelector('[data-expert-team-stage-input-text]'):null;
      const answer=(selected&&selected.dataset&&selected.dataset.expertTeamStageInputChoice)||'';
      const note=input?String(input.value||'').trim():'';
      if(!answer&&!note){
        if(typeof showToast==='function')showToast('请先选择确认项，或填写补充说明。');
        if(input&&input.focus){
          try{input.focus({preventScroll:true});}catch(_){input.focus();}
        }
        return false;
      }
      const card=activeExpertTeamCard(btn);
      try{
        const result=await runExpertTeamMutation(btn,{
          action:'submit_stage_input',
          endpoint:expertTeamMutationEndpoint('submit_stage_input'),
          payload:{answer,note,input_id:card.pendingInputId},
          busyLabel:'正在确认...',
          preserveWithoutExecution:true,
        });
        if(typeof showToast==='function')showToast(result.executionStarted?'已确认，专家团已继续生成。':'确认已保存，尚未开始生成。');
        return true;
      }catch(error){
        if(typeof showToast==='function')showToast('提交阶段确认失败：'+(error&&error.message||error));
        return false;
      }
    }
    if(action==='approve_stage'){
      return typeof approveExpertTeamStage==='function'?approveExpertTeamStage(btn):false;
    }
    if(action==='review_stage'){
      if(typeof openExpertTeamReviewPanel==='function')openExpertTeamReviewPanel(btn);
      return true;
    }
    if(action==='revise_stage'){
      if(typeof openExpertTeamReviewPanel==='function')openExpertTeamReviewPanel(btn);
      if(typeof toggleExpertTeamStageRevision==='function')toggleExpertTeamStageRevision(btn);
      return true;
    }
    if(action==='view_result'){
      if(typeof openExpertTeamResultViewer==='function')openExpertTeamResultViewer(btn);
      return true;
    }
    return false;
  }
  if(typeof window!=='undefined'){
    window.runExpertTeamMutation=runExpertTeamMutation;
    window.isExpertTeamExecutionStarted=isExpertTeamExecutionStarted;
    window.isExpertTeamIntakeAccepted=isExpertTeamIntakeAccepted;
    window.expertTeamMutationContract=expertTeamMutationContract;
    window.expertTeamMutationEndpoint=expertTeamMutationEndpoint;
    window.refreshExpertTeamRun=refreshExpertTeamRun;
    window.submitExpertTeamBrief=submitExpertTeamBrief;
    window.collectExpertTeamBriefPayload=collectExpertTeamBriefPayload;
    window.restoreExpertTeamBriefDirtyPatch=restoreExpertTeamBriefDirtyPatch;
    window.focusFirstExpertTeamBriefError=focusFirstExpertTeamBriefError;
    window.clearExpertTeamBriefErrors=clearExpertTeamBriefErrors;
    window.refreshExpertTeamIdentityStatus=refreshExpertTeamIdentityStatus;
    window.startExpertTeamIdentityLogin=startExpertTeamIdentityLogin;
    window.cancelExpertTeamIdentityLogin=cancelExpertTeamIdentityLogin;
    window.logoutExpertTeamIdentity=logoutExpertTeamIdentity;
    window.expertTeamIdentityCapability=expertTeamIdentityCapability;
    window.openExpertTeamFileArtifact=openExpertTeamFileArtifact;
    window.downloadExpertTeamFileArtifact=downloadExpertTeamFileArtifact;
    window.handleExpertTeamWorkspaceTabKeydown=handleExpertTeamWorkspaceTabKeydown;
    window.handleExpertTeamPresentationAction=handleExpertTeamPresentationAction;
    window.switchExpertTeamWorkspaceTab=function(btn){
      const root=btn&&btn.closest&&btn.closest('.expert-team-panel-inner');
      const tab=normalizeExpertTeamWorkspaceTab(btn&&btn.dataset?btn.dataset.expertTeamWorkspaceTab:'');
      if(!root||!tab)return false;
      rememberExpertTeamWorkspaceTab(root,tab);
      return applyExpertTeamWorkspaceTab(root,tab);
    };
    window.restoreExpertTeamWorkspaceTab=restoreExpertTeamWorkspaceTab;
    window.normalizeExpertTeamWorkspaceTab=normalizeExpertTeamWorkspaceTab;
    window.selectExpertTeamStageInputChoice=function(btn){
      const root=btn&&btn.closest&&btn.closest('.expert-team-stage-input-card');
      if(root&&root.querySelectorAll){
        root.querySelectorAll('[data-expert-team-stage-input-choice]').forEach(item=>item.classList.remove('is-selected'));
      }
      if(btn)btn.classList.add('is-selected');
      return true;
    };
    window.deferExpertTeamStageInput=function(btn){
      if(typeof showToast==='function')showToast('已保留当前确认项，可稍后在右侧工作台继续处理。');
      if(typeof toggleExpertTeamWorkspacePanel==='function')toggleExpertTeamWorkspacePanel(btn);
      return true;
    };
  }
})();
