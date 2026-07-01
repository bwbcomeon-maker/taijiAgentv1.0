(function(){
  const workspaceTabByRun=Object.create(null);
  function currentExpertTeamRunId(btn){
    const root=btn&&btn.closest&&btn.closest('[data-expert-team-run-id]');
    return (root&&root.dataset&&root.dataset.expertTeamRunId)||((window._activeExpertTeamStatusCard||{}).runId)||'';
  }
  function workspaceRunId(root){
    const source=root&&root.closest?root.closest('[data-expert-team-run-id]'):root;
    return source&&source.dataset&&source.dataset.expertTeamRunId||'';
  }
  function applyExpertTeamWorkspaceTab(root,tab){
    if(!root||!tab)return false;
    const target=root.querySelector&&root.querySelector(`[data-expert-team-workspace-tab="${tab}"]`);
    if(!target)return false;
    root.querySelectorAll('[data-expert-team-workspace-tab]').forEach(item=>{
      const active=item.dataset&&item.dataset.expertTeamWorkspaceTab===tab;
      item.classList.toggle('is-active',active);
      item.setAttribute('aria-selected',active?'true':'false');
    });
    root.querySelectorAll('[data-expert-team-tab-panel]').forEach(panel=>{
      panel.hidden=!(panel.dataset&&panel.dataset.expertTeamTabPanel===tab);
    });
    return true;
  }
  function rememberExpertTeamWorkspaceTab(root,tab){
    const runId=workspaceRunId(root);
    if(runId&&tab)workspaceTabByRun[runId]=tab;
  }
  function restoreExpertTeamWorkspaceTab(root){
    const panel=root&&root.querySelector?root:document.getElementById('expertTeamWorkspacePanel');
    const inner=panel&&panel.querySelector?panel.querySelector('.expert-team-panel-inner'):(panel&&panel.classList&&panel.classList.contains('expert-team-panel-inner')?panel:null);
    if(!inner)return false;
    const tab=workspaceTabByRun[workspaceRunId(inner)]||'todo';
    return applyExpertTeamWorkspaceTab(inner,tab)||applyExpertTeamWorkspaceTab(inner,'todo');
  }
  async function refreshExpertTeamAfterAction(){
    if(typeof refreshWriteflowStatusDockForActiveSession==='function')await refreshWriteflowStatusDockForActiveSession();
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
  async function handleExpertTeamPresentationAction(btn){
    const action=btn&&btn.dataset?btn.dataset.expertTeamAction:'';
    const runId=currentExpertTeamRunId(btn);
    if(!action||!runId)return false;
    if(action==='answer_required'||action==='answer_optional'){
      if(typeof openExpertTeamQuestionPopover==='function')openExpertTeamQuestionPopover(btn);
      return true;
    }
    if(action==='start_generation'||action==='regenerate'){
      const data=await api('/api/expert-teams/resume',{method:'POST',body:JSON.stringify({run_id:runId,session_id:S&&S.session&&S.session.session_id||''})});
      applyExpertTeamActionResponse(data);
      await refreshExpertTeamAfterAction();
      return true;
    }
    if(action==='cancel'){
      const data=await api('/api/expert-teams/cancel',{method:'POST',body:JSON.stringify({run_id:runId,session_id:S&&S.session&&S.session.session_id||''})});
      applyExpertTeamActionResponse(data);
      await refreshExpertTeamAfterAction();
      return true;
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
      const data=await api('/api/expert-teams/stage/input',{method:'POST',body:JSON.stringify({run_id:runId,session_id:S&&S.session&&S.session.session_id||'',answer,note})});
      applyExpertTeamActionResponse(data);
      await refreshExpertTeamAfterAction();
      return true;
    }
    if(action==='approve_stage'){
      const data=await api('/api/expert-teams/stage/approve',{method:'POST',body:JSON.stringify({run_id:runId,session_id:S&&S.session&&S.session.session_id||''})});
      applyExpertTeamActionResponse(data);
      await refreshExpertTeamAfterAction();
      return true;
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
    window.handleExpertTeamPresentationAction=handleExpertTeamPresentationAction;
    window.switchExpertTeamWorkspaceTab=function(btn){
      const root=btn&&btn.closest&&btn.closest('.expert-team-panel-inner');
      const tab=btn&&btn.dataset?btn.dataset.expertTeamWorkspaceTab:'';
      if(!root||!tab)return false;
      rememberExpertTeamWorkspaceTab(root,tab);
      return applyExpertTeamWorkspaceTab(root,tab);
    };
    window.restoreExpertTeamWorkspaceTab=restoreExpertTeamWorkspaceTab;
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
