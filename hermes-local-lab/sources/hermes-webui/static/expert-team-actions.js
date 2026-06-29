(function(){
  function currentExpertTeamRunId(btn){
    const root=btn&&btn.closest&&btn.closest('[data-expert-team-run-id]');
    return (root&&root.dataset&&root.dataset.expertTeamRunId)||((window._activeExpertTeamStatusCard||{}).runId)||'';
  }
  async function refreshExpertTeamAfterAction(){
    if(typeof refreshWriteflowStatusDockForActiveSession==='function')await refreshWriteflowStatusDockForActiveSession();
  }
  function applyExpertTeamActionResponse(data){
    const run=data&&data.run;
    if(run&&typeof _expertTeamStatusCardFromRun==='function'&&typeof renderWriteflowStatusDock==='function'){
      const card=_expertTeamStatusCardFromRun(run,data);
      if(card)renderWriteflowStatusDock(card);
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
  }
})();
