(function(){
  function arr(value){ return Array.isArray(value)?value:[]; }
  function str(value,fallback){ const text=String(value==null?'':value).trim(); return text||fallback||''; }
  function normalizeAction(action){
    if(!action||typeof action!=='object')return null;
    return {
      id:str(action.id),
      label:str(action.label),
      kind:str(action.kind,'primary')
    };
  }
  function taskStatusText(task){
    const explicit=str(task&&task.status_label);
    if(explicit)return explicit;
    const status=str(task&&task.status,'pending');
    return {
      pending:'待执行',
      running:'执行中',
      done:'完成',
      awaiting_review:'待复核',
      error:'需处理',
      cancelled:'已取消'
    }[status]||status;
  }
  function buildExpertTeamPresentation(run){
    run=run||{};
    const view=run.view||{};
    const presentation=view.presentation||{};
    const business=view.business_context||{};
    const primary_action=presentation.primary_action||presentation.primaryAction||null;
    return {
      state:str(presentation.state,run.workflow_state||'collecting_required'),
      title:str(presentation.title,'专家团状态'),
      visibleTitle:str(presentation.visible_title||business.visible_title||run.title,'专家团任务'),
      detail:str(presentation.detail),
      primaryAction:normalizeAction(primary_action),
      secondaryActions:arr(presentation.secondary_actions||presentation.secondaryActions).map(normalizeAction).filter(Boolean),
      result:presentation.result||((view.stage_review||{}).output)||{},
      summary:str(presentation.summary)
    };
  }
  function buildExpertTeamCardFromRun(run,data){
    if(!run||!run.run_id)return null;
    data=data||{};
    const presentation=buildExpertTeamPresentation(run);
    const view=run.view||{};
    const teamTitle=str(run.team_title,'专家团');
    const tasks=arr(run.tasks).map(task=>({
      id:str(task&&task.id),
      title:str(task&&task.title,task&&task.id||'阶段任务'),
      phase:str(task&&task.phase),
      status:str(task&&task.status,'pending'),
      statusText:taskStatusText(task),
      worker_name:str(task&&task.worker_name)
    }));
    const members=arr(run.members).map(member=>({
      id:str(member&&member.id),
      name:str(member&&member.name,member&&member.id||'成员'),
      role:str(member&&member.role),
      status:str(member&&member.status,'待命'),
      image:str(member&&member.image)
    }));
    const timelineEvents=arr(view.timeline_events).map(event=>({
      type:str(event&&event.type),
      title:str(event&&event.title,event&&event.type||'专家团动态'),
      detail:str(event&&event.detail),
      memberId:str(event&&event.member_id),
      memberName:str(event&&event.member_name),
      memberImage:str(event&&event.member_image),
      at:str(event&&event.at)
    }));
    const questions=arr(run.questions).map(question=>({
      id:str(question&&question.id),
      title:str(question&&question.title,question&&question.id||'问题'),
      placeholder:str(question&&question.placeholder),
      answer:str(question&&question.answer),
      status:str(question&&question.status,'pending'),
      required:question&&question.required!==false,
      confirmationGroup:str(question&&question.confirmation_group)
    }));
    const phaseProgress=view.phase_progress||{};
    return {
      type:'writeflow',
      kind:'expert_team',
      title:presentation.title,
      subtitle:presentation.visibleTitle,
      sessionId:str(run.run_id),
      runId:str(run.run_id),
      sourceSessionId:str(run.session_id),
      team:{id:str(run.team_id),title:teamTitle,category:str((data.team||{}).category,'专家团'),image:str(run.team_image)},
      status:presentation.state,
      phase:str(phaseProgress.current||run.phase,'需求确认'),
      progress:{done:Number(phaseProgress.done||0),total:Number(phaseProgress.total||tasks.length||0)},
      presentation,
      questions,
      primaryConfirmation:view.primary_confirmation||null,
      pendingConfirmations:arr(view.pending_confirmations),
      intake:view.intake||{},
      stageReview:view.stage_review||{},
      reviewItems:arr(view.review_items),
      timelineEvents,
      tasks,
      members,
      artifacts:arr(run.artifacts),
      stageOutputs:arr(run.stage_outputs),
      actions:view.actions||{},
      phaselist:arr(view.phases),
      rows:[
        {label:'团队',value:teamTitle},
        {label:'主状态',value:presentation.title},
        {label:'阶段',value:str(phaseProgress.current||run.phase,'需求确认')},
        {label:'主操作',value:presentation.primaryAction?presentation.primaryAction.label:'无'}
      ]
    };
  }
  if(typeof window!=='undefined'){
    window.buildExpertTeamPresentation=buildExpertTeamPresentation;
    window.buildExpertTeamCardFromRun=buildExpertTeamCardFromRun;
  }
})();
