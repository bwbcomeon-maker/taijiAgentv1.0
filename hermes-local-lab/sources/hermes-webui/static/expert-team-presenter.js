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
  function normalizedStageActionBinding(value){
    value=value&&typeof value==='object'?value:{};
    const binding={
      session_id:str(value.session_id),
      run_id:str(value.run_id),
      expected_version:Number(value.expected_version),
      stage_id:str(value.stage_id),
      stage_attempt:Number(value.stage_attempt),
      artifact_id:str(value.artifact_id),
      artifact_sha256:str(value.artifact_sha256).toLowerCase()
    };
    if(
      !binding.session_id||!binding.run_id||!Number.isInteger(binding.expected_version)||binding.expected_version<0||
      !binding.stage_id||!Number.isInteger(binding.stage_attempt)||binding.stage_attempt<1||!binding.artifact_id||
      !/^[0-9a-f]{64}$/.test(binding.artifact_sha256)
    )return null;
    return binding;
  }
  function normalizedCancelActionBinding(value){
    value=value&&typeof value==='object'?value:{};
    const binding={
      session_id:str(value.session_id),
      run_id:str(value.run_id),
      expected_version:Number(value.expected_version),
      stage_id:str(value.stage_id),
      idempotency_key:str(value.idempotency_key)
    };
    if(
      !binding.session_id||!binding.run_id||!Number.isInteger(binding.expected_version)||binding.expected_version<0||
      !binding.stage_id||!binding.idempotency_key
    )return null;
    return binding;
  }
  function normalizedDeliveryActionBinding(value){
    value=value&&typeof value==='object'?value:{};
    const binding={
      session_id:str(value.session_id),
      run_id:str(value.run_id),
      expected_version:Number(value.expected_version),
      stage_id:str(value.stage_id),
      stage_attempt:Number(value.stage_attempt),
      artifact_id:str(value.artifact_id),
      artifact_sha256:str(value.artifact_sha256).toLowerCase(),
      delivery_attempt:Number(value.delivery_attempt),
      delivery_binding_sha256:str(value.delivery_binding_sha256).toLowerCase(),
      document_sha256:str(value.document_sha256).toLowerCase()
    };
    if(
      !binding.session_id||!binding.run_id||!Number.isInteger(binding.expected_version)||binding.expected_version<0||
      !binding.stage_id||!Number.isInteger(binding.stage_attempt)||binding.stage_attempt<1||!binding.artifact_id||
      !/^[0-9a-f]{64}$/.test(binding.artifact_sha256)||
      !Number.isInteger(binding.delivery_attempt)||binding.delivery_attempt<1||
      !/^[0-9a-f]{64}$/.test(binding.delivery_binding_sha256)||
      !/^[0-9a-f]{64}$/.test(binding.document_sha256)
    )return null;
    return binding;
  }
  function normalizedStandaloneDelivery(value){
    if(!value||typeof value!=='object')return null;
    const check=value.automatic_check_summary&&typeof value.automatic_check_summary==='object'
      ? value.automatic_check_summary:{};
    const delivery={
      documentName:str(value.document_name,'最终交付文档.docx'),
      deliveryAttempt:Number(value.delivery_attempt),
      documentSha256:str(value.document_sha256).toLowerCase(),
      qualityReportSha256:str(value.quality_report_sha256).toLowerCase(),
      automaticCheckSummary:{
        status:str(check.status,'pending'),
        passedCount:Number(check.passed_count||0),
        failedCount:Number(check.failed_count||0),
        warningCount:Number(check.warning_count||0),
        blockingCount:Number(check.blocking_count||0)
      }
    };
    if(
      !Number.isInteger(delivery.deliveryAttempt)||delivery.deliveryAttempt<1||
      !/^[0-9a-f]{64}$/.test(delivery.documentSha256)||
      (delivery.qualityReportSha256&&!/^[0-9a-f]{64}$/.test(delivery.qualityReportSha256))
    )return null;
    return delivery;
  }
  function buildExpertTeamStageActionPayload(card,idempotencyKey){
    const binding=normalizedStageActionBinding(card&&card.stageActionBinding);
    const key=str(idempotencyKey);
    if(!binding||!key)return null;
    return {...binding,idempotency_key:key};
  }
  function buildExpertTeamDeliveryActionPayload(card,idempotencyKey){
    const binding=normalizedDeliveryActionBinding(card&&card.deliveryActionBinding);
    const key=str(idempotencyKey);
    if(!binding||!key)return null;
    return {...binding,idempotency_key:key};
  }
  function buildExpertTeamDeliveryRecoveryPayload(card,idempotencyKey){
    const binding=normalizedDeliveryActionBinding(card&&card.deliveryRecoveryBinding);
    const key=str(idempotencyKey);
    if(!binding||!key)return null;
    return {...binding,idempotency_key:key};
  }
  const STATE_LABELS={
    intake:'待确认任务规格',
    ready:'任务规格已确认',
    executing:'专家团正在执行',
    awaiting_stage_confirmation:'阶段成果待确认',
    generating_document:'正在生成正式文档',
    awaiting_delivery_confirmation:'最终文档待确认',
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
  function normalizedGates(value,standalone){
    value=value&&typeof value==='object'?value:{};
    const result={content:normalizedGate(value.content),document:normalizedGate(value.document)};
    if(standalone)result.localConfirmation=normalizedGate(value.local_confirmation);
    else result.office=normalizedGate(value.office);
    return result;
  }
  function normalizedProductError(value){
    if(!value||typeof value!=='object'||str(value.schema)!=='taiji.product.error.v1')return null;
    const allowedActions=new Set(['open_model_settings','export_diagnostics','retry']);
    const actions=arr(value.recovery_actions).map(action=>({
      id:str(action&&action.id),label:str(action&&action.label)
    })).filter(action=>allowedActions.has(action.id)&&action.label);
    return {
      schema:'taiji.product.error.v1',code:str(value.code),title:str(value.title),message:str(value.message),
      incidentId:str(value.incident_id),retryable:value.retryable===true,recoveryActions:actions
    };
  }
  function gateSummary(gates,deliveryStatus,state,standalone){
    if(standalone){
      if(state==='completed')return '本机交付已确认';
      if(gates.document.status==='passed')return 'DOCX 自动检查通过，待本机确认';
      if(gates.content.status==='passed'&&gates.document.status==='failed')return '内容已确认，DOCX 自动检查未通过';
      if(gates.content.status==='passed')return '内容已确认，正在生成文档';
      if(state==='executing'||state==='revising')return '正在生成/待确认内容';
      return '内容待确认';
    }
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
    const fieldSchema=arr(brief.field_schema).map(field=>{
      field=field&&typeof field==='object'?field:{};
      const path=str(field.path);
      if(!/^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$/.test(path))return null;
      const control=['text','textarea','date'].includes(str(field.control))?str(field.control):'text';
      return {
        path,label:str(field.label,path),control,required:field.required===true,
        placeholder:str(field.placeholder),help:str(field.help),value:str(field.value)
      };
    }).filter(Boolean);
    const fieldErrors=arr(brief.field_errors||(brief.validation||{}).field_errors).map(error=>({
      field:str(error&&error.field),code:str(error&&error.code),message:str(error&&error.message)
    })).filter(error=>error.field&&error.message);
    const sourceRequirement=brief.source_requirement&&typeof brief.source_requirement==='object'
      ? brief.source_requirement:{};
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
      fieldSchema,
      fieldErrors,
      sourceRequirement:{
        minimumReady:Number(sourceRequirement.minimum_ready||0),
        emptyHelp:str(sourceRequirement.empty_help)
      },
      requiredSections:arr(brief.required_sections).map(section=>str(section)).filter(Boolean),
      sources:arr(brief.sources).map(source=>({
        source_id:str(source&&source.source_id),kind:str(source&&source.kind),label:str(source&&source.label),
        status:str(source&&source.status),size_bytes:Number(source&&source.size_bytes||0),sha256:str(source&&source.sha256)
      })),
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
      evidenceCount:Number(value.evidence_count||value.evidenceCount||arr(value.evidence).length||arr(value.visual_evidence||value.visualEvidence).length||0),
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
    const productMode=str(view.product_mode);
    const standalone=productMode==='standalone';
    const state=standalone
      ? str(view.public_state,'contract_error')
      : str(presentation.state,run.workflow_state||'collecting_required');
    const gates=normalizedGates(view.completion_gates,standalone);
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
      gateSummary:gateSummary(gates,deliveryStatus,state,standalone),
      capabilityKind:str(capability.kind,'legacy'),
      capabilityLabel:str(capability.label,'历史任务，未按企业合同验证'),
      productMode,
      publicState:standalone?state:''
    };
  }
  function buildExpertTeamWorkspace(run){
    const view=run&&run.view||{};
    const workspace=view.workspace||{};
    return {
      visible:workspace.visible!==false,
      title:str(workspace.title,'专家团工作台'),
      state:str(view.product_mode)==='standalone'
        ? str(view.public_state,'contract_error')
        : str(workspace.state,run&&run.workflow_state||'collecting_required'),
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
    const productMode=str(view.product_mode);
    const standalone=productMode==='standalone';
    const publicState=standalone?str(view.public_state,'contract_error'):'';
    const allowedActions=standalone
      ? arr(view.allowed_actions).map(item=>str(item)).filter(Boolean)
      : [];
    const normalizedStageBinding=standalone?normalizedStageActionBinding(view.stage_action_binding):null;
    const stageActionBinding=normalizedStageBinding&&
      normalizedStageBinding.session_id===str(run.session_id)&&
      normalizedStageBinding.run_id===str(run.run_id)&&
      normalizedStageBinding.expected_version===Number(run.version||0)
      ? normalizedStageBinding
      : null;
    const normalizedCancelBinding=standalone?normalizedCancelActionBinding(view.cancel_action_binding):null;
    const cancelActionBinding=normalizedCancelBinding&&
      normalizedCancelBinding.session_id===str(run.session_id)&&normalizedCancelBinding.run_id===str(run.run_id)
      ? normalizedCancelBinding
      : null;
    const normalizedDeliveryBinding=standalone?normalizedDeliveryActionBinding(view.delivery_action_binding):null;
    const deliveryActionBinding=normalizedDeliveryBinding&&
      normalizedDeliveryBinding.session_id===str(run.session_id)&&
      normalizedDeliveryBinding.run_id===str(run.run_id)&&
      normalizedDeliveryBinding.expected_version===Number(run.version||0)
      ? normalizedDeliveryBinding
      : null;
    const normalizedDeliveryRecoveryBinding=standalone?normalizedDeliveryActionBinding(view.delivery_recovery_binding):null;
    const deliveryRecoveryBinding=normalizedDeliveryRecoveryBinding&&
      normalizedDeliveryRecoveryBinding.session_id===str(run.session_id)&&
      normalizedDeliveryRecoveryBinding.run_id===str(run.run_id)&&
      normalizedDeliveryRecoveryBinding.expected_version===Number(run.version||0)
      ? normalizedDeliveryRecoveryBinding
      : null;
    const normalizedDelivery=standalone?normalizedStandaloneDelivery(view.standalone_delivery):null;
    const standaloneDelivery=deliveryActionBinding&&normalizedDelivery&&
      normalizedDelivery.deliveryAttempt===deliveryActionBinding.delivery_attempt&&
      normalizedDelivery.documentSha256===deliveryActionBinding.document_sha256
      ? normalizedDelivery
      : null;
    const teamView=view.team||{};
    const workflow=view.workflow||{};
    const pendingInput=view.pending_input||workspace.pendingInput||{};
    const stageResult=view.stage_result||workspace.stageResult||{};
    const currentStage=standalone
      ? (workflow.current_stage||workspace.currentStage||{})
      : (workflow.current_stage||workspace.currentStage||run.current_stage||{});
    const stageReview=view.stage_review||{};
    const stageReviewOutput=stageReview.output||{};
    const stageAttemptReservation=run.current_stage_attempt_reservation||{};
    const officeReview=standalone?{}:(view.office_review||view.office_acceptance||run.office_review_view||run.office_review_ref||{});
    const brief=standalone?(view.brief||{}):(view.brief||run.document_brief||{});
    const schemaVersion=Number(run.schema_version||0);
    const teamTitle=str(teamView.title||run.team_title,'专家团');
    const workflowStages=arr(workflow.stages);
    const tasks=(standalone?workflowStages:(workflowStages.length?workflowStages:arr(run.tasks))).map(task=>({
      id:str(task&&task.id),
      title:str(task&&task.title,task&&task.id||'阶段任务'),
      phase:str(task&&task.phase),
      status:str(task&&task.status,'pending'),
      statusText:taskStatusText(task),
      worker_id:str(task&&task.worker_id),
      worker_name:str(task&&task.worker_name)
    }));
    const teamMembers=arr(teamView.members);
    const members=(standalone?teamMembers:(teamMembers.length?teamMembers:arr(run.members))).map(member=>({
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
    const questions=arr(standalone?(view.intake||{}).questions:run.questions).map(question=>({
      id:str(question&&question.id),
      title:str(question&&question.title,question&&question.id||'问题'),
      placeholder:str(question&&question.placeholder),
      answer:str(question&&question.answer),
      status:str(question&&question.status,'pending'),
      required:question&&question.required!==false,
      confirmationGroup:str(question&&question.confirmation_group)
    }));
    const phaseProgress=(workflow&&workflow.progress)||view.phase_progress||{};
    const productError=normalizedProductError(view.product_error);
    const draftIdentity={
      stageAttempt:Number(standalone?(stageActionBinding&&stageActionBinding.stage_attempt||0):(stageReview.stage_attempt||stageReview.attempt||stageResult.stage_attempt||stageResult.attempt||currentStage.stage_attempt||currentStage.attempt||stageAttemptReservation.stage_attempt||0)),
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
      productMode,
      publicState,
      allowedActions,
      stageActionBinding,
      cancelActionBinding,
      deliveryActionBinding,
      deliveryRecoveryBinding,
      standaloneDelivery,
      readOnly:run.read_only===true||schemaVersion<2,
      executionStreamId:str(run.execution_stream_id),
      currentStageId:str(currentStage.task_id||currentStage.id),
      pendingInputId:str(pendingInput.id||pendingInput.input_id),
      stageReviewId:str(stageReview.review_id||stageReviewOutput.review_id||stageReviewOutput.id||stageReviewOutput.task_id),
      draftIdentity,
      cancelRequestId:str(run.cancel_request_id),
      team:{id:str(teamView.id||run.team_id),title:teamTitle,category:str((data.team||{}).category,'专家团'),image:str(teamView.image||run.team_image),members},
      status:standalone?publicState:presentation.state,
      phase:str(phaseProgress.current||run.phase,'需求确认'),
      progress:{
        done:Number(phaseProgress.done||0),
        total:Number(phaseProgress.total||tasks.length||0),
        current:str(phaseProgress.current),
        currentIndex:Number.isInteger(phaseProgress.current_index)?Number(phaseProgress.current_index):null,
        isIntake:phaseProgress.is_intake===true
      },
      presentation,
      productError,
      brief:presentation.brief,
      completionGates:presentation.completionGates,
      deliveryStatus:presentation.deliveryStatus,
      officeReview:standalone?null:normalizedOfficeReview(officeReview),
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
      actions:standalone?{}:(view.actions||{}),
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
    window.buildExpertTeamStageActionPayload=buildExpertTeamStageActionPayload;
    window.buildExpertTeamDeliveryActionPayload=buildExpertTeamDeliveryActionPayload;
    window.buildExpertTeamDeliveryRecoveryPayload=buildExpertTeamDeliveryRecoveryPayload;
  }
})();
