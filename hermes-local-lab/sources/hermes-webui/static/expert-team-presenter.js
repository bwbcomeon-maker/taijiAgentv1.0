(function(){
  function arr(value){ return Array.isArray(value)?value:[]; }
  function str(value,fallback){ const text=String(value==null?'':value).trim(); return text||fallback||''; }
  function normalizeAction(action){
    if(!action||typeof action!=='object')return null;
    return {
      id:str(action.id||action.type),
      label:str(action.label),
      kind:str(action.kind,'primary')
    };
  }
  const STATE_LABELS={
    collecting_required:'待确认文档规格',
    collecting_optional:'待补全文档规格',
    ready_to_generate:'文档规格已确认，待开始生成',
    starting:'正在启动 AI 阶段协作',
    start_failed:'启动失败',
    generation_failed:'生成失败',
    result_unverified:'结果待核验',
    legacy_result_unverified:'历史结果未绑定',
    generating:'AI 阶段协作正在生成',
    revising:'AI 阶段协作正在修改',
    cancelling:'正在停止生成',
    awaiting_stage_input:'当前阶段需要确认',
    generated_invalid:'草稿未通过校验',
    awaiting_review:'阶段成果待复核',
    delivery_validation_required:'内容已确认，正在生成文档',
    completion_reconciling:'正在恢复交付完成状态',
    completed_invalid:'交付状态异常',
    completed:'专家团阶段已完成',
    failed:'生成失败',
    cancelled:'已取消'
  };
  function normalizedGate(gate){
    gate=gate&&typeof gate==='object'?gate:{};
    return {
      status:str(gate.status,'pending'),
      label:str(gate.label),
      reasonCode:str(gate.reason_code||gate.reasonCode),
      blockingIssueCount:Number(gate.blocking_issue_count||gate.blockingIssueCount||0)
    };
  }
  function normalizedGates(value){
    value=value&&typeof value==='object'?value:{};
    return {content:normalizedGate(value.content),document:normalizedGate(value.document),office:normalizedGate(value.office)};
  }
  function gateSummary(gates,deliveryStatus,state){
    if(deliveryStatus==='passed'&&gates.content.status==='passed'&&gates.document.status==='passed'&&gates.office.status==='passed')return '交付已通过';
    if(gates.office.status==='failed')return 'Office 验收不通过，待修改';
    if(gates.document.status==='passed'&&gates.office.status!=='passed')return 'DOCX 自动检查通过，待 Office 验收';
    if(gates.content.status==='passed'&&gates.document.status==='failed')return '内容已确认，DOCX 自动检查未通过';
    if(gates.content.status==='passed'&&gates.document.status!=='passed')return '内容已确认，正在生成文档';
    if(state==='generating'||state==='revising'||state==='starting')return '正在生成/待复核内容';
    return '内容待确认';
  }
  function normalizedBrief(brief){
    if(!brief||typeof brief!=='object')return null;
    return {
      status:str(brief.status,'draft'),
      revision:Number(brief.revision||0),
      originalRequest:str(brief.original_request),
      originalRequestSummary:str(brief.original_request_summary||brief.original_request),
      originalRequestLabel:'原始诉求',
      exactTitle:str(brief.exact_title),
      documentType:str(brief.document_type),
      documentTypeLabel:str(brief.document_type_label||brief.document_type),
      purpose:str(brief.purpose),
      audience:str(brief.audience),
      usageScenario:str(brief.usage_scenario),
      additionalContext:str(brief.additional_context),
      documentControl:brief.document_control&&typeof brief.document_control==='object'?brief.document_control:{},
      sourcePolicySummary:brief.source_policy_summary&&typeof brief.source_policy_summary==='object'?brief.source_policy_summary:{},
      editable:brief.editable===true,
      editPolicy:str(brief.edit_policy),
      validation:brief.validation||{},
      viewAction:normalizeAction(brief.view_action)||{id:str((brief.view_action||{}).type),label:str((brief.view_action||{}).label),kind:'ghost'}
    };
  }
  function normalizedOfficeReview(value){
    if(!value||typeof value!=='object')return null;
    const issues=arr(value.issues).map(item=>({
      issueId:str(item&&item.issue_id||item&&item.issueId),severity:str(item&&item.severity),
      targetDomain:str(item&&item.target_domain||item&&item.targetDomain),category:str(item&&item.category),
      sectionId:str(item&&item.section_id),blockId:str(item&&item.block_id),logicalAssetId:str(item&&item.logical_asset_id),
      page:Number(item&&item.page||0),description:str(item&&item.description),expectedFix:str(item&&item.expected_fix||item&&item.expectedFix)
    }));
    return {
      reviewId:str(value.review_id||value.reviewId),documentRevision:Number(value.document_revision||value.documentRevision||1),
      documentSha256:str(value.document_sha256||value.documentSha256),canonicalSha256:str(value.canonical_sha256||value.canonicalSha256),
      status:str(value.status,'pending'),decision:str(value.decision,'pending'),validity:str(value.validity,'active'),
      reviewSessionStatus:str(value.review_session_status||value.reviewSessionStatus,'begin_required'),
      checklist:value.checklist&&typeof value.checklist==='object'?value.checklist:{},issues,
      issueCount:Number(value.issue_count==null?issues.length:value.issue_count),reviewerLabel:str(value.reviewer_label||value.reviewerLabel),
      waivedIssueIds:arr(value.waived_issue_ids||value.waivedIssueIds).map(String)
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
    const state=str(presentation.state,run.workflow_state||'collecting_required');
    const gates=normalizedGates(view.completion_gates);
    const deliveryStatus=str(view.delivery_status,'pending');
    const nextAction=normalizeAction(view.next_action)||(
      view.next_action&&typeof view.next_action==='object'
        ? {id:str(view.next_action.type),label:str(view.next_action.label),kind:'primary'}
        : null
    );
    const capability=view.capability&&typeof view.capability==='object'?view.capability:{};
    return {
      state,
      title:str(presentation.title,'专家团状态'),
      statusLabel:STATE_LABELS[state]||str(presentation.title,'专家团状态'),
      visibleTitle:str(presentation.visible_title||business.visible_title||run.title,'专家团任务'),
      detail:str(presentation.detail),
      primaryAction:normalizeAction(primary_action),
      secondaryActions:arr(presentation.secondary_actions||presentation.secondaryActions).map(normalizeAction).filter(Boolean),
      result:presentation.result||((view.stage_review||{}).output)||{},
      summary:str(presentation.summary),
      progressText:str(presentation.progress_text),
      brief:normalizedBrief(view.brief),
      completionGates:gates,
      deliveryStatus,
      nextAction,
      gateSummary:gateSummary(gates,deliveryStatus,state),
      capabilityKind:str(capability.kind,'legacy'),
      capabilityLabel:str(capability.label,'历史任务，未按企业合同验证')
    };
  }
  function buildExpertTeamWorkspace(run){
    const view=run&&run.view||{};
    const workspace=view.workspace||{};
    return {
      visible:workspace.visible!==false,
      title:str(workspace.title,'专家团工作台'),
      state:str(workspace.state,run&&run.workflow_state||'collecting_required'),
      currentStage:workspace.current_stage||{},
      currentWorker:workspace.current_worker||{},
      phases:arr(workspace.phases),
      members:arr(workspace.members),
      timeline:arr(workspace.timeline||view.timeline_events),
      stageResult:workspace.stage_result||view.stage_result||{},
      pendingInput:workspace.pending_input||view.pending_input||{}
    };
  }
  function buildExpertTeamCardFromRun(run,data){
    if(!run||!run.run_id)return null;
    data=data||{};
    const presentation=buildExpertTeamPresentation(run);
    const view=run.view||{};
    const workspace=buildExpertTeamWorkspace(run);
    const teamView=view.team||{};
    const workflow=view.workflow||{};
    const pendingInput=view.pending_input||workspace.pendingInput||{};
    const stageResult=view.stage_result||workspace.stageResult||{};
    const currentStage=workflow.current_stage||workspace.currentStage||run.current_stage||{};
    const stageReview=view.stage_review||{};
    const stageReviewOutput=stageReview.output||{};
    const stageAttemptReservation=run.current_stage_attempt_reservation||{};
    const officeReview=view.office_review||view.office_acceptance||run.office_review_view||run.office_review_ref||{};
    const brief=view.brief||run.document_brief||{};
    const schemaVersion=Number(run.schema_version||0);
    const teamTitle=str(teamView.title||run.team_title,'专家团');
    const workflowStages=arr(workflow.stages);
    const tasks=(workflowStages.length?workflowStages:arr(run.tasks)).map(task=>({
      id:str(task&&task.id),
      title:str(task&&task.title,task&&task.id||'阶段任务'),
      phase:str(task&&task.phase),
      status:str(task&&task.status,'pending'),
      statusText:taskStatusText(task),
      worker_id:str(task&&task.worker_id),
      worker_name:str(task&&task.worker_name)
    }));
    const teamMembers=arr(teamView.members);
    const members=(teamMembers.length?teamMembers:arr(run.members)).map(member=>({
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
    const phaseProgress=(workflow&&workflow.progress)||view.phase_progress||{};
    const draftIdentity={
      stageAttempt:Number(stageReview.stage_attempt||stageReview.attempt||stageResult.stage_attempt||stageResult.attempt||currentStage.stage_attempt||currentStage.attempt||stageAttemptReservation.stage_attempt||0),
      artifactAttempt:Number(stageReviewOutput.stage_attempt||stageReviewOutput.attempt||stageResult.artifact_attempt||0),
      executionAttempt:Number(run.execution_attempt||run.current_execution_attempt||(run.execution_context&&run.execution_context.attempt)||0),
      briefRevision:Number(brief.revision||brief.brief_revision||0),
      reviewId:str(stageReview.review_id||stageReviewOutput.review_id||stageReviewOutput.id||stageReviewOutput.task_id),
      officeReviewId:str(officeReview.review_id||officeReview.office_review_id||officeReview.acceptance_id),
    };
    return {
      type:'writeflow',
      kind:'expert_team',
      title:presentation.title,
      subtitle:presentation.visibleTitle,
      sessionId:str(run.run_id),
      runId:str(run.run_id),
      sourceSessionId:str(run.session_id),
      schemaVersion,
      version:Number(run.version||0),
      readOnly:run.read_only===true||schemaVersion<2,
      executionStreamId:str(run.execution_stream_id),
      currentStageId:str(currentStage.task_id||currentStage.id),
      pendingInputId:str(pendingInput.id||pendingInput.input_id),
      stageReviewId:str(stageReview.review_id||stageReviewOutput.review_id||stageReviewOutput.id||stageReviewOutput.task_id),
      draftIdentity,
      cancelRequestId:str(run.cancel_request_id),
      team:{id:str(teamView.id||run.team_id),title:teamTitle,category:str((data.team||{}).category,'专家团'),image:str(teamView.image||run.team_image),members},
      status:presentation.state,
      phase:str(phaseProgress.current||run.phase,'需求确认'),
      progress:{done:Number(phaseProgress.done||0),total:Number(phaseProgress.total||tasks.length||0)},
      presentation,
      brief:presentation.brief,
      completionGates:presentation.completionGates,
      deliveryStatus:presentation.deliveryStatus,
      officeReview:normalizedOfficeReview(officeReview),
      nextAction:presentation.nextAction,
      capability:{kind:presentation.capabilityKind,label:presentation.capabilityLabel},
      artifactValidation:view.artifact_validation||{},
      workspace,
      workflow:{stages:tasks,currentStage,progress:phaseProgress},
      pendingInput,
      stageResult,
      questions,
      primaryConfirmation:view.primary_confirmation||null,
      pendingConfirmations:arr(view.pending_confirmations),
      intake:view.intake||{},
      stageReview,
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
