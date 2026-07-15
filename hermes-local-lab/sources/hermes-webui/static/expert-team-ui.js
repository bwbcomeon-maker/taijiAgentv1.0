(function(){
  function safeEsc(value){ return (typeof esc==='function')?esc(value):String(value==null?'':value).replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];}); }
  function actionButton(action,extraClass){
    if(!action||!action.id)return '';
    const cls=extraClass||'expert-team-action-button';
    const kind=action.kind?` data-expert-team-action-kind="${safeEsc(action.kind)}"`:'';
    const disabled=action.disabled?' disabled aria-disabled="true"':'';
    const reason=action.disabledReason?` title="${safeEsc(action.disabledReason)}"`:'';
    return `<button type="button" class="${cls}" data-expert-team-action="${safeEsc(action.id)}"${kind}${disabled}${reason} onclick="handleExpertTeamPresentationAction(this);event.stopPropagation()" aria-label="${safeEsc(action.label||'操作')}">${safeEsc(action.label||'操作')}</button>`;
  }
  function setExpertTeamCapsuleExpanded(trigger,expanded){
    const root=trigger&&trigger.closest?trigger.closest('.expert-team-panel-inner'):null;
    const capsule=root&&root.querySelector?root.querySelector('.expert-team-capsule-action'):null;
    if(capsule)capsule.setAttribute('aria-expanded',expanded?'true':'false');
    return !!expanded;
  }
  function showExpertTeamWorkspaceFromCapsule(trigger){
    const expanded=typeof showExpertTeamWorkspacePanel==='function'
      ? !!showExpertTeamWorkspacePanel(trigger)
      : true;
    return setExpertTeamCapsuleExpanded(trigger,expanded);
  }
  function toggleExpertTeamWorkspaceFromControl(trigger){
    const expanded=typeof toggleExpertTeamWorkspacePanel==='function'
      ? !!toggleExpertTeamWorkspacePanel(trigger)
      : false;
    return setExpertTeamCapsuleExpanded(trigger,expanded);
  }
  function presentationTone(state){
    if(state==='generating'||state==='ready_to_generate'||state==='starting'||state==='revising'||state==='cancelling')return 'running';
    if(state==='awaiting_stage_input'||state==='collecting_required'||state==='collecting_optional'||state==='awaiting_review')return 'waiting';
    if(state==='generated_invalid'||state==='start_failed'||state==='generation_failed'||state==='result_unverified'||state==='legacy_result_unverified'||state==='failed')return 'issue';
    if(state==='completed')return 'done';
    return 'waiting';
  }
  function statusText(status){
    return {
      pending:'待执行',
      running:'执行中',
      done:'已完成',
      awaiting_review:'待复核',
      awaiting_input:'暂停等待确认',
      error:'需处理',
      cancelled:'已取消',
      collecting_required:'待确认',
      collecting_optional:'待补充',
      ready_to_generate:'待启动',
      starting:'正在启动',
      start_failed:'启动失败',
      generation_failed:'生成失败',
      result_unverified:'结果待核验',
      legacy_result_unverified:'历史结果未绑定',
      generating:'生成中',
      revising:'重做中',
      cancelling:'正在停止',
      awaiting_stage_input:'待确认',
      generated_invalid:'草稿未通过校验',
      completed:'已完成',
      failed:'失败'
    }[String(status||'')]||String(status||'待执行');
  }
  function tabStatusText(status){
    status=String(status||'');
    return status==='generated_invalid'||status==='failed'?'需处理':statusText(status);
  }
  function avatarHtml(src,label){
    const name=safeEsc(label||'专家');
    const fallback=safeEsc(String(label||'专').trim().slice(0,1)||'专');
    if(src){
      return `<span class="expert-team-member-avatar"><img src="${safeEsc(src)}" alt="${name}" loading="lazy"><span>${fallback}</span></span>`;
    }
    return `<span class="expert-team-member-avatar"><span>${fallback}</span></span>`;
  }
  function collaborationTaskForMember(member,tasks){
    const memberId=String(member&&member.id||'');
    const memberName=String(member&&member.name||'');
    return (Array.isArray(tasks)?tasks:[]).find(task=>{
      const workerId=String(task&&task.worker_id||task&&task.workerId||'');
      const workerName=String(task&&task.worker_name||task&&task.workerName||'');
      return (memberId&&workerId===memberId)||(memberName&&workerName===memberName);
    })||null;
  }
  function collaborationMemberState(member,task,isCurrent){
    const raw=String(member&&member.status||'').trim();
    const taskStatus=String(task&&task.status||'').trim();
    if(isCurrent)return {label:'当前',tone:'running'};
    if(taskStatus==='done'||raw==='已完成')return {label:'已完成',tone:'done'};
    if(taskStatus==='running'||raw==='执行中')return {label:'执行中',tone:'running'};
    if(taskStatus==='awaiting_input'||raw==='等待确认')return {label:'待确认',tone:'waiting'};
    if(taskStatus==='error'||raw==='需处理')return {label:'需处理',tone:'issue'};
    return {label:raw||'待命',tone:'waiting'};
  }
  function expertTeamDockSummaryFromPresentation(card){
    const presentation=card&&card.presentation||{};
    const action=presentation.primaryAction||null;
    return {
      state:presentationTone(presentation.state),
      title:presentation.title||'专家团状态',
      detail:presentation.detail||presentation.visibleTitle||'',
      action:action&&action.label||''
    };
  }
  function progressInfo(card){
    const workflow=card&&card.workflow||{};
    const progress=workflow.progress||card&&card.progress||{};
    const stages=Array.isArray(workflow.stages)?workflow.stages:(Array.isArray(card&&card.tasks)?card.tasks:[]);
    const total=Number(progress.total||stages.length||0);
    const done=Number(progress.done||0);
    const progressCurrentIndex=Number(progress.current_index==null?progress.currentIndex:progress.current_index);
    const isIntake=!!(progress.is_intake||progress.isIntake);
    let current=0;
    if(total){
      if(isIntake)current=0;
      else if((card&&card.presentation&&card.presentation.state)==='completed')current=total;
      else current=Math.min(total,Math.max(done,Number.isFinite(progressCurrentIndex)?progressCurrentIndex+1:done));
    }
    const text=(card&&card.presentation&&card.presentation.progressText)||progress.text||(total?`${current}/${total}`:'0/0');
    const pct=total?Math.max(0,Math.min(100,Math.round(current*100/total))):0;
    return {total,done,current,currentIndex:progressCurrentIndex,isIntake,text,pct,stages};
  }
  function renderBriefCard(card){
    const presentation=card&&card.presentation||{};
    const brief=card&&card.brief||presentation.brief;
    const capability=card&&card.capability||{};
    if(!brief){
      return `<section class="expert-team-brief-card is-legacy" aria-label="文档规格状态"><strong>${safeEsc(capability.label||presentation.capabilityLabel||'历史任务，未按企业合同验证')}</strong><p>该任务未按企业文档规格合同创建，不补造 Brief 或企业验收结论。</p></section>`;
    }
    const runId=String(card&&card.runId||'expert-team').replace(/[^a-zA-Z0-9_-]/g,'-');
    const originalId=`expert-team-brief-original-request-${runId}`;
    const actionLabel=brief.viewAction&&brief.viewAction.label||'查看/编辑文档规格';
    const validation=brief.validation&&typeof brief.validation==='object'?brief.validation:{};
    const errors=Array.isArray(validation.field_errors)?validation.field_errors:[];
    const errorFor=(name)=>errors.find(item=>String(item&&item.field||'')===name)||null;
    const field=(name,label,value,options)=>{
      options=options||{};
      const id=`expert-team-brief-${name.replace(/[^a-zA-Z0-9_-]/g,'-')}-${runId}`;
      const helpId=`${id}-help`;
      const error=errorFor(name);
      const errorId=`${id}-error`;
      const described=[options.help?helpId:'',error?errorId:''].filter(Boolean).join(' ');
      const common=`id="${safeEsc(id)}" name="${safeEsc(name)}"${described?` aria-describedby="${safeEsc(described)}"`:''}${error?' aria-invalid="true"':''}`;
      const control=options.rows
        ? `<textarea ${common} rows="${safeEsc(options.rows)}">${safeEsc(value||'')}</textarea>`
        : `<input ${common} type="text" value="${safeEsc(value||'')}">`;
      return `<label class="expert-team-brief-field"><span>${safeEsc(label)}</span>${control}${options.help?`<small id="${safeEsc(helpId)}">${safeEsc(options.help)}</small>`:''}<small id="${safeEsc(errorId)}" data-expert-team-field-error="${safeEsc(name)}" role="alert">${safeEsc(error&&error.message||'')}</small></label>`;
    };
    const control=brief.documentControl||{};
    const editor=brief.editable
      ? `<form class="expert-team-brief-editor" data-expert-team-brief-editor data-expert-team-brief-revision="${safeEsc(brief.revision||0)}" data-expert-team-document-control="${safeEsc(JSON.stringify(control))}" onsubmit="return false">
          <fieldset><legend>文档目标</legend>
            ${field('original_request','原始诉求',brief.originalRequest,{rows:4,help:'这是创建任务时的原始要求，可在首阶段启动前修正。'})}
            ${field('exact_title','精确标题',brief.exactTitle)}
            <div class="expert-team-brief-field"><span>文种</span><strong>${safeEsc(brief.documentTypeLabel||'待选择')}</strong><small>文种决定企业模板和必填字段；如需更换文种，请新建任务。</small></div>
            ${field('purpose','用途',brief.purpose)}
          </fieldset>
          <fieldset><legend>使用与资料边界</legend>
            ${field('audience','读者',brief.audience)}
            ${field('usage_scenario','使用场景',brief.usageScenario)}
            ${field('additional_context','补充背景',brief.additionalContext,{rows:4,help:'补充可核对的背景和资料使用边界，不会覆盖原始诉求。'})}
            <p class="expert-team-brief-boundary">已绑定资料：${safeEsc(brief.sourcePolicySummary&&brief.sourcePolicySummary.source_count||0)} 项；资料策略由企业配置管理。</p>
          </fieldset>
          <fieldset><legend>交付控制</legend>
            ${field('document_control.classification','密级',control.classification)}
            ${field('document_control.document_version','文档版本',control.document_version||control.version)}
            <label class="expert-team-brief-field"><span>模板</span><input name="document_control.render_template_id" type="text" value="${safeEsc(control.render_template_id||'')}" readonly><small>模板由所选文种确定。</small></label>
          </fieldset>
          <div class="expert-team-brief-actions">
            <button type="button" class="expert-team-panel-action expert-team-secondary-action" onclick="submitExpertTeamBrief(this,false);event.stopPropagation()">保存规格</button>
            <button type="button" class="expert-team-panel-action expert-team-primary-action" onclick="submitExpertTeamBrief(this,true);event.stopPropagation()">确认文档规格</button>
          </div>
        </form>`
      : `<div class="expert-team-brief-frozen" role="status"><strong>首阶段已经启动，整份文档规格已冻结</strong><p>当前任务不会覆盖已生成阶段；如需修改，请基于当前规格创建新任务。</p><button type="button" class="expert-team-panel-action expert-team-secondary-action" data-expert-team-action="relaunch" onclick="handleExpertTeamPresentationAction(this);event.stopPropagation()">基于当前规格创建新任务</button></div>`;
    return `<section class="expert-team-brief-card" aria-label="文档规格摘要">
      <div class="expert-team-panel-section-title"><span>文档规格</span><small>${safeEsc(capability.label||presentation.capabilityLabel||'AI 草稿能力')}</small></div>
      <strong>${safeEsc(brief.exactTitle||'待补充精确标题')}</strong>
      <dl>
        <div><dt>原始诉求</dt><dd>${safeEsc(brief.originalRequestSummary||'未提供')}</dd></div>
        <div><dt>精确标题</dt><dd>${safeEsc(brief.exactTitle||'待补充')}</dd></div>
        <div><dt>文种</dt><dd>${safeEsc(brief.documentTypeLabel||'待选择')}</dd></div>
        <div><dt>Brief revision</dt><dd>${safeEsc(brief.revision||0)}</dd></div>
      </dl>
      <details class="expert-team-brief-details">
        <summary>${safeEsc(actionLabel)}</summary>
        <label for="${safeEsc(originalId)}">原始诉求</label>
        <p id="${safeEsc(originalId)}">${safeEsc(brief.originalRequest||'')}</p>
        ${editor}
      </details>
    </section>`;
  }

  function expertTeamApprovalState(card){
    const identity=card&&card.identityStatus||(typeof window!=='undefined'&&window._expertTeamIdentityStatus)||{};
    const principal=identity.principal&&typeof identity.principal==='object'?identity.principal:{};
    const roles=Array.isArray(principal.roles)?principal.roles:[];
    const validation=card&&card.artifactValidation||{};
    const warningCount=Number(validation.unresolved_warning_count||validation.blocking_count||0);
    if(identity.enabled===false)return {allowed:false,reason:'未配置企业身份提供方，无法批准阶段成果。',action:'login'};
    if(identity.expired)return {allowed:false,reason:'企业身份已过期，请重新登录。',action:'login'};
    if(!identity.authenticated)return {allowed:false,reason:'需使用企业审批身份登录后才能确认。',action:'login'};
    if(!roles.includes('document-approver'))return {allowed:false,reason:'当前身份缺少文档审批权限。',action:'login'};
    if(warningCount>0)return {allowed:false,reason:`仍有 ${warningCount} 个阻断问题或警告，修复后才能确认。`,action:'repair'};
    return {allowed:true,reason:`当前审批身份：${String(principal.display_name||'已认证用户')}`,action:'logout'};
  }

  function renderExpertTeamIdentityStatus(card,approval){
    const identity=card&&card.identityStatus||(typeof window!=='undefined'&&window._expertTeamIdentityStatus)||{};
    const principal=identity.principal&&typeof identity.principal==='object'?identity.principal:{};
    const label=approval.reason||'正在核验企业审批身份。';
    if(identity.authenticated){
      return `<div class="expert-team-identity-status" role="status"><span>${safeEsc(label)}</span><button type="button" onclick="logoutExpertTeamIdentity(this);event.stopPropagation()">退出企业身份</button></div>`;
    }
    return `<div class="expert-team-identity-status" role="status"><span>${safeEsc(label)}</span><button type="button" onclick="startExpertTeamIdentityLogin(this);event.stopPropagation()">使用企业审批身份登录</button><button type="button" onclick="refreshExpertTeamIdentityStatus(this);event.stopPropagation()">检查登录状态</button></div>`;
  }
  function renderCompletionGates(card){
    const presentation=card&&card.presentation||{};
    const gates=card&&card.completionGates||presentation.completionGates||{};
    const labels={content:'内容确认',document:'DOCX 生成',office:'Office 验收'};
    return `<section class="expert-team-completion-gates" aria-label="企业交付三道门">
      ${['content','document','office'].map(name=>{
        const gate=gates[name]||{};
        return `<span class="expert-team-completion-gate is-${safeEsc(gate.status||'pending')}"><b>${safeEsc(labels[name])}</b><small>${safeEsc(gate.label||'待完成')}</small></span>`;
      }).join('')}
    </section>`;
  }
  function renderStageInput(card,pendingInput){
    pendingInput=pendingInput||{};
    if(!(pendingInput.question||pendingInput.description))return '';
    const options=Array.isArray(pendingInput.options)?pendingInput.options:[];
    const optionHtml=options.length?`<div class="expert-team-stage-input-options">${options.map(option=>`<button type="button" data-expert-team-stage-input-choice="${safeEsc(option)}" onclick="selectExpertTeamStageInputChoice(this);event.stopPropagation()">${safeEsc(option)}</button>`).join('')}</div>`:'';
    return `<section class="expert-team-panel-section expert-team-stage-input-card" aria-label="当前阶段需要确认" data-expert-team-run-id="${safeEsc(card&&card.runId||'')}">
      <div class="expert-team-panel-section-title"><span>需要确认 1 项</span><small>生成暂停在当前阶段</small></div>
      <strong class="expert-team-stage-input-question">${safeEsc(pendingInput.question||'当前阶段需要你确认后继续生成。')}</strong>
      <p class="expert-team-panel-detail">${safeEsc(pendingInput.description||'确认后专家团会继续当前阶段，不会跳到下一阶段。')}</p>
      ${optionHtml}
      <label class="expert-team-stage-input-note">
        <span>补充说明</span>
        <textarea data-expert-team-stage-input-text rows="4" placeholder="也可以补充具体说明..."></textarea>
      </label>
      <div class="expert-team-stage-actions">
        <button type="button" class="expert-team-panel-action expert-team-secondary-action" onclick="deferExpertTeamStageInput(this);event.stopPropagation()">稍后处理</button>
        ${actionButton({id:'submit_stage_input',label:'确认并继续生成',kind:'primary'},'expert-team-panel-action expert-team-primary-action')}
      </div>
    </section>`;
  }
  function artifactStageLabel(stage,currentStage){
    const labels={plan:'流程安排',materials:'素材整理',draft:'富内容初稿',polish:'审稿打磨',delivery:'交付确认',direction:'研究方向',research:'资料调研',evidence:'事实核验',outline:'结构提纲',review:'复核交付'};
    const currentId=String(currentStage&&((currentStage.id||currentStage.task_id))||'');
    if(String(stage||'')===currentId&&currentStage&&currentStage.phase)return String(currentStage.phase);
    return labels[String(stage||'')]||String(stage||'阶段产物');
  }
  function renderVersionedArtifacts(card,currentStage){
    const rows=(Array.isArray(card&&card.artifacts)?card.artifacts:[]).slice().sort((a,b)=>{
      const priority={final_document:0,delivery_package:1,quality_report:2,final_rich_draft:3,rich_draft:4,chat:9};
      const attemptDiff=Number(b&&b.attempt||0)-Number(a&&a.attempt||0);
      return attemptDiff||Number(priority[String(a&&a.kind||'')]??8)-Number(priority[String(b&&b.kind||'')]??8);
    });
    if(!rows.length)return '<div class="expert-team-empty-result">尚无可打开的产物，阶段生成后会按版本保留在这里。</div>';
    return `<div class="expert-team-panel-artifacts" aria-label="版本化产物">${rows.map(item=>{
      item=item||{};
      const kind=String(item.kind||'artifact');
      const path=String(item.path||'');
      const exists=item.exists!==false;
      const attempt=Math.max(1,Number(item.attempt||1));
      const versionText=`${artifactStageLabel(item.stage,currentStage)} · 第 ${attempt} 版`;
      const title=String(item.label||item.title||'阶段产物');
      const icon=kind==='final_document'?'DOCX':kind==='delivery_package'?'交付':kind==='quality_report'?'质检':kind.includes('rich_draft')?'初稿':'对话';
      let action='';
      if(kind==='chat'&&!path){
        action=`<button type="button" data-expert-team-action="view_result" onclick="handleExpertTeamPresentationAction(this);event.stopPropagation()" aria-label="查看${safeEsc(title)}">查看对话成果</button>`;
      }else if(exists&&path){
        const label=kind==='final_document'?'打开 DOCX':(kind==='delivery_package'?'打开交付包':'打开');
        const openButton=`<button type="button" class="expert-team-panel-artifact-open" data-expert-team-artifact-kind="${safeEsc(kind)}" data-expert-team-artifact-path="${safeEsc(path)}" data-expert-team-artifact-exists="true" onclick="openExpertTeamFileArtifact(this);event.stopPropagation()" aria-label="${safeEsc(label+' '+title)}">${safeEsc(label)}</button>`;
        const downloadButton=kind==='delivery_package'
          ?''
          :`<button type="button" class="expert-team-panel-artifact-download" data-expert-team-artifact-kind="${safeEsc(kind)}" data-expert-team-artifact-path="${safeEsc(path)}" data-expert-team-artifact-exists="true" onclick="downloadExpertTeamFileArtifact(this);event.stopPropagation()" aria-label="${safeEsc('下载 '+title)}">下载</button>`;
        action=`<span class="expert-team-panel-artifact-actions">${openButton}${downloadButton}</span>`;
      }else{
        action=`<button type="button" data-expert-team-artifact-kind="${safeEsc(kind)}" data-expert-team-artifact-path="${safeEsc(path)}" data-expert-team-artifact-exists="false" disabled title="文件不存在，请重新生成当前阶段">文件不存在</button>`;
      }
      return `<span class="expert-team-panel-artifact ${exists&&path?'ready':'missing'}" data-artifact-id="${safeEsc(item.id||'')}" data-expert-team-artifact-kind="${safeEsc(kind)}"><i>${safeEsc(icon)}</i><span><strong>${safeEsc(title)}</strong><small>${safeEsc(versionText)} · ${safeEsc(item.status||'')}</small>${action}</span></span>`;
    }).join('')}</div>`;
  }
  function openExpertTeamWpsAcceptance(trigger){
    const inner=trigger&&trigger.closest?trigger.closest('.expert-team-panel-inner'):null;
    if(!inner)return false;
    const resultTab=inner.querySelector('[data-expert-team-workspace-tab="result"]');
    if(resultTab&&typeof switchExpertTeamWorkspaceTab==='function')switchExpertTeamWorkspaceTab(resultTab);
    const form=inner.querySelector('[data-docx-wps-acceptance]');
    if(!form){
      if(typeof showToast==='function')showToast('尚未找到可验收的 DOCX 与交付目录。');
      return false;
    }
    try{form.scrollIntoView({block:'nearest',inline:'nearest'});}catch(_){}
    const openButton=form.querySelector('[data-docx-wps-open-document]');
    if(openButton&&openButton.focus){
      try{openButton.focus({preventScroll:true});}catch(_){openButton.focus();}
    }
    return true;
  }
  function renderExpertTeamWorkspaceFromPresentation(card){
    const presentation=card&&card.presentation||{};
    const workspace=card&&card.workspace||{};
    const workflow=card&&card.workflow||{};
    const stageResult=card&&card.stageResult||workspace.stageResult||{};
    const currentStage=workflow.currentStage||workspace.currentStage||{};
    const currentWorker=workspace.currentWorker||{};
    const result=presentation.result||{};
    const progress=progressInfo(card);
    const tasks=progress.stages;
    const members=Array.isArray(card&&card.members)?card.members:[];
    const pendingInput=card&&card.pendingInput||workspace.pendingInput||{};
    const secondaryActions=Array.isArray(presentation.secondaryActions)?presentation.secondaryActions:[];
    const runId=card&&card.runId||card&&card.sessionId||'';
    const isReadOnly=!!(card&&card.readOnly);
    const statusTone=presentationTone(presentation.state);
    const briefCardHtml=renderBriefCard(card);
    const completionGatesHtml=renderCompletionGates(card);
    const approvalState=expertTeamApprovalState(card);
    const identityStatusHtml=renderExpertTeamIdentityStatus(card,approvalState);
    const currentStageId=String(currentStage.id||currentStage.task_id||'');
    const currentWorkerId=String(currentWorker.id||currentWorker.member_id||currentStage.worker_id||'');
    const currentWorkerName=String(currentWorker.name||currentStage.worker_name||'');
    const currentMember=members.find(member=>
      (member.id&&String(member.id)===currentWorkerId)||(member.name&&String(member.name)===currentWorkerName)
    )||{name:currentWorkerName||currentStage.worker_name||'专家团',role:currentWorker.role||currentStage.phase||'当前阶段负责专家',image:currentWorker.image||''};
    const currentCollaborationState='当前处理';
    const collaborationMembersHtml=members.length
      ? `<div class="expert-team-member-list expert-team-collaboration-grid" aria-label="专家团成员协作状态">${members.map(member=>{
          const task=collaborationTaskForMember(member,tasks);
          const isCurrent=!!((member.id&&String(member.id)===currentWorkerId)||(member.name&&String(member.name)===currentWorkerName));
          const state=collaborationMemberState(member,task,isCurrent);
          const roleText=member.role||task&&task.phase||'协作';
          const taskText=task&&task.title?task.title:(task&&task.phase?task.phase:state.label);
          const label=`${member.name||member.id||'专家'} · ${roleText} · ${state.label}`;
          return `<span class="expert-team-member-row ${safeEsc(state.tone)}" title="${safeEsc(label)}" aria-label="${safeEsc(label)}">${avatarHtml(member.image,member.name)}<span class="expert-team-member-copy"><strong title="${safeEsc(member.name||member.id||'专家')}">${safeEsc(member.name||member.id||'专家')}</strong><small title="${safeEsc(`${roleText} · ${taskText}`)}">${safeEsc(roleText)} · ${safeEsc(taskText)}</small></span><em class="expert-team-member-state ${safeEsc(state.tone)}">${safeEsc(state.label)}</em></span>`;
        }).join('')}</div>`
      : '<div class="expert-team-panel-empty">专家团成员将在任务初始化后显示</div>';
    const resultHtml=result&&result.content
      ? `<div class="expert-team-result-card" data-expert-team-result-card="1">
          <span class="expert-team-result-card-icon">文</span>
          <span class="expert-team-result-card-main">
            <strong>${safeEsc(result.visible_title||result.title||presentation.visibleTitle||'专家团成果')}</strong>
            <small>${safeEsc(result.phase||'阶段成果')}</small>
            <p>${safeEsc(result.summary||'结果已写入当前对话')}</p>
            <span class="expert-team-result-card-actions">${actionButton({id:'view_result',label:'查看完整成果'},'expert-team-result-card-button')}</span>
          </span>
        </div>`
      : `<div class="expert-team-empty-result">当前阶段产物将在生成完成后显示</div>`;
    const versionedArtifactsHtml=renderVersionedArtifacts(card,currentStage);
    const artifacts=Array.isArray(card&&card.artifacts)?card.artifacts:[];
    const readyArtifactCount=artifacts.filter(item=>item&&item.exists!==false&&String(item.path||'')).length;
    const finalDocument=artifacts.find(item=>item&&String(item.kind||'')==='final_document'&&item.exists!==false&&String(item.path||''));
    const deliveryPackage=artifacts.find(item=>item&&String(item.kind||'')==='delivery_package'&&item.exists!==false&&String(item.path||''));
    const qualityReportArtifact=artifacts.find(item=>item&&String(item.kind||'')==='quality_report');
    const officeAcceptanceHtml=!isReadOnly&&finalDocument&&deliveryPackage&&typeof renderDocxWpsVisualAcceptanceForm==='function'
      ? renderDocxWpsVisualAcceptanceForm({
          expertTeam:true,
          documentPath:String(finalDocument.path||''),
          deliveryDir:String(deliveryPackage.path||''),
          qualityStatus:String(qualityReportArtifact&&qualityReportArtifact.status||'待验收'),
        })
      : '';
    const officeReviewAction=officeAcceptanceHtml
      ? '<button type="button" class="expert-team-panel-action expert-team-secondary-action" onclick="openExpertTeamWpsAcceptance(this);event.stopPropagation()" aria-label="打开 Office 验收表单">Office 验收</button>'
      : '';
    const stageSummary=stageResult&&stageResult.summary?stageResult.summary:(result&&result.summary||'当前阶段产物生成后会在这里沉淀。');
    const reviewActions={
      view:secondaryActions.find(action=>action&&action.id==='view_result')||{id:'view_result',label:'查看成果',kind:'ghost'},
      approve:{...(secondaryActions.find(action=>action&&action.id==='approve_stage')||{id:'approve_stage',label:'无修改，进入下一阶段',kind:'primary'}),disabled:!approvalState.allowed,disabledReason:approvalState.reason},
      revise:secondaryActions.find(action=>action&&action.id==='revise_stage')||{id:'revise_stage',label:'需要修改',kind:'ghost'}
    };
    const reviewItems=Array.isArray(card&&card.reviewItems)?card.reviewItems:[];
    const reviewItemHtml=reviewItems.length
      ? `<div class="expert-team-review-items-list">${reviewItems.map((item,idx)=>`<span class="expert-team-review-item"><i>${idx+1}</i><b>${safeEsc(item.title||'待确认事项')}</b><small>${safeEsc(item.phase||'待人工补充')}</small>${isReadOnly?'':`<span class="expert-team-review-item-actions"><button type="button" data-expert-team-review-item-title="${safeEsc(item.title||'')}" onclick="appendExpertTeamReviewItemToRevision(this);event.stopPropagation()">加入修改意见</button></span>`}</span>`).join('')}</div>`
      : '';
    const stageReviewHtml=presentation.state==='awaiting_review'
      ? `<section class="expert-team-panel-section expert-team-stage-review" aria-label="阶段成果复核" data-expert-team-run-id="${safeEsc(runId)}">
          <div class="expert-team-panel-section-title"><span>阶段成果待复核</span><small>${safeEsc(currentStage.phase||'当前阶段')}</small></div>
          <p class="expert-team-panel-detail">${safeEsc(stageSummary||'阶段结果已生成，请查看后确认是否进入下一阶段。')}</p>
          ${resultHtml}
          ${reviewItemHtml}
          ${identityStatusHtml}
          ${isReadOnly?'<p class="expert-team-panel-detail">历史任务仅支持查看，请新建专家团任务后继续。</p>':`<div class="expert-team-stage-actions">
            ${actionButton(reviewActions.view,'expert-team-panel-action expert-team-secondary-action expert-team-stage-locate')}
            ${officeReviewAction}
            ${actionButton(reviewActions.approve,'expert-team-panel-action expert-team-primary-action expert-team-stage-approve')}
            ${actionButton(reviewActions.revise,'expert-team-panel-action expert-team-secondary-action expert-team-stage-revision-toggle')}
          </div>
          <div class="expert-team-stage-feedback" hidden aria-hidden="true">
            <label><span>修改意见</span><textarea data-expert-team-stage-feedback rows="4" placeholder="请写明需要调整的内容、口径、事实或结构。"></textarea></label>
            <button type="button" class="expert-team-panel-action expert-team-primary-action" data-expert-team-stage-revise-run-id="${safeEsc(runId)}" onclick="submitExpertTeamStageRevision(this);event.stopPropagation()">提交修改意见</button>
          </div>`}
        </section>`
      : '';
    const stageInputHtml=presentation.state==='awaiting_stage_input'&&!isReadOnly?renderStageInput(card,pendingInput):'';
    const genericPrimaryAction=!isReadOnly&&presentation.primaryAction&&presentation.state!=='awaiting_stage_input'&&presentation.state!=='awaiting_review';
    const genericActionHtml=genericPrimaryAction
      ? `<section class="expert-team-panel-section expert-team-primary-task-card" aria-label="当前主操作">
          <div class="expert-team-panel-section-title"><span>当前待办</span><small>${safeEsc(statusText(presentation.state))}</small></div>
          <p class="expert-team-panel-detail">${safeEsc(presentation.detail||'请处理当前专家团待办。')}</p>
          <div class="expert-team-stage-actions">
            ${actionButton(presentation.primaryAction,'expert-team-panel-action expert-team-primary-action')}
            ${secondaryActions.map(action=>actionButton(action,'expert-team-panel-action expert-team-secondary-action')).join('')}
          </div>
        </section>`
      : '';
    const questionPopoverHtml=!isReadOnly&&typeof _expertTeamQuestionPopoverHtml==='function'?_expertTeamQuestionPopoverHtml(card):'';
    const recoverableDraftHintHtml=typeof _expertTeamRecoverableDraftHintHtml==='function'?_expertTeamRecoverableDraftHintHtml(card):'';
    const readOnlyHtml=isReadOnly?`<section class="expert-team-panel-section" role="status"><strong>历史任务仅支持查看</strong><p class="expert-team-panel-detail">原任务内容不会被修改；如需继续，请以当前专家团新建任务。</p><div class="expert-team-stage-actions">${actionButton({id:'relaunch',label:'以新任务重新发起',kind:'primary'},'expert-team-panel-action expert-team-primary-action')}</div></section>`:'';
    const currentTodoLabel=presentation.state==='awaiting_review'
      ? '阶段成果待复核'
      : (presentation.state==='awaiting_stage_input'
        ? '当前阶段需要确认'
        : (presentation.primaryAction&&presentation.primaryAction.label||statusText(presentation.state)));
    const currentTodoDetail=presentation.detail||stageSummary||'请处理当前专家团待办。';
    const hasDetailedTask=!!(genericActionHtml||stageInputHtml||stageReviewHtml);
    const actionSummaryHtml=presentation.primaryAction&&!isReadOnly&&!hasDetailedTask
      ? `<button type="button" class="expert-team-panel-inline-action" data-expert-team-action="${safeEsc(presentation.primaryAction.id)}" data-expert-team-action-kind="${safeEsc(presentation.primaryAction.kind||'')}" onclick="handleExpertTeamPresentationAction(this);event.stopPropagation()">${safeEsc(presentation.primaryAction.label||'处理')}</button>`
      : `<span class="expert-team-panel-inline-note">${safeEsc(statusText(presentation.state))}</span>`;
    const todoSummaryCardHtml=hasDetailedTask?'':`<section class="expert-team-panel-section expert-team-primary-task-card" aria-label="当前待办摘要">
          <div class="expert-team-panel-section-title"><span>当前待办</span><small>${safeEsc(statusText(presentation.state))}</small></div>
          <div class="expert-team-todo-summary">
            <span><strong>${safeEsc(currentTodoLabel)}</strong><small>${safeEsc(currentTodoDetail)}</small></span>
            ${actionSummaryHtml}
          </div>
        </section>`;
    const todoPanelHtml=`${readOnlyHtml}${genericActionHtml}${stageInputHtml}${stageReviewHtml}${todoSummaryCardHtml}`;
    const collaborationPanelHtml=`<section class="expert-team-panel-section expert-team-collaboration-card" aria-label="专家团协作状态">
          <div class="expert-team-panel-section-title"><span>专家团协作状态</span><small>${safeEsc(card&&card.team&&card.team.title||'专家团')}</small></div>
          <div class="expert-team-collaboration-current">
            ${avatarHtml(currentMember.image,currentMember.name)}
            <span class="expert-team-collaboration-current-copy">
              <strong>${safeEsc(progress.text)} · ${safeEsc(currentStage.phase||card&&card.phase||'当前阶段')}</strong>
              <small>${safeEsc(currentMember.name||'专家团')}正在处理：${safeEsc(currentStage.title||presentation.visibleTitle||'阶段任务')}</small>
            </span>
            <em>${safeEsc(currentCollaborationState)}</em>
          </div>
          <div class="expert-team-panel-progress expert-team-collaboration-progress" style="--expert-team-panel-progress:${progress.pct}%"><i></i><span><b>${safeEsc(progress.text)}</b> · ${safeEsc(currentStage.title||'等待阶段推进')}</span></div>
          ${collaborationMembersHtml}
        </section>`;
    const resultPanelHtml=`<section class="expert-team-panel-section">
          <div class="expert-team-panel-section-title"><span>成果状态</span><small>${safeEsc(presentation.state==='generating'?'专家团正在生成':presentation.state==='awaiting_stage_input'?'等待确认后继续':presentation.state==='generated_invalid'?'草稿未通过校验':presentation.state==='awaiting_review'?'阶段成果待复核':'当前状态')}</small></div>
          <p class="expert-team-panel-detail">${safeEsc(stageSummary)}</p>
          ${presentation.state==='awaiting_review'?'':resultHtml}
        </section><section class="expert-team-panel-section expert-team-panel-artifacts-section ${readyArtifactCount?'is-priority':''}"><div class="expert-team-panel-section-title"><span>版本化产物</span><small>${safeEsc(readyArtifactCount?`${readyArtifactCount} 个可打开`:'待生成')}</small></div>${versionedArtifactsHtml}</section>${officeAcceptanceHtml}`;
    function tabPanel(id,html,active){
      const tabId=`expert-team-tab-${id}`;
      const panelId=`expert-team-tabpanel-${id}`;
      return `<div id="${safeEsc(panelId)}" class="expert-team-tab-panel" role="tabpanel" aria-labelledby="${safeEsc(tabId)}" data-expert-team-tab-panel="${safeEsc(id)}" ${active?'':'hidden'}>${html}</div>`;
    }
    return `<div class="expert-team-panel-inner" data-expert-team-run-id="${safeEsc(runId)}" data-expert-team-schema-version="${safeEsc(card&&card.schemaVersion||0)}" data-expert-team-version="${safeEsc(card&&card.version||0)}" data-expert-team-stage-id="${safeEsc(card&&card.currentStageId||currentStageId)}" data-expert-team-stream-id="${safeEsc(card&&card.executionStreamId||'')}" data-expert-team-input-id="${safeEsc(card&&card.pendingInputId||pendingInput.id||'')}" data-expert-team-review-id="${safeEsc(card&&card.stageReviewId||'')}" data-expert-team-read-only="${isReadOnly?'true':'false'}" data-expert-team-presentation-state="${safeEsc(presentation.state||'')}" data-expert-team-workspace-mode="summary">
      <div class="expert-team-capsule" aria-label="专家团收起状态">
        <span class="expert-team-capsule-icon">专</span>
        <strong>${safeEsc(progress.text)}</strong>
        <small>${safeEsc(currentStage.phase||card&&card.phase||'需求确认')}</small>
        <span class="expert-team-capsule-state ${safeEsc(statusTone)}">${safeEsc(presentation.title||'专家团状态')}</span>
        <span class="expert-team-capsule-todo-count">${safeEsc(['awaiting_review','awaiting_stage_input','collecting_required','collecting_optional','ready_to_generate'].includes(String(presentation.state||''))?'1 个待办':'0 个待办')}</span>
        <button type="button" class="expert-team-capsule-action" onclick="showExpertTeamWorkspaceFromCapsule(this);event.stopPropagation()" aria-label="展开专家团工作台" aria-expanded="false" aria-controls="expert-team-workspace-expanded">处理</button>
      </div>
      <div class="expert-team-panel-head">
        <div class="expert-team-panel-topbar">
          <span class="expert-team-panel-copy">
            <small class="expert-team-panel-eyebrow">专家团工作台</small>
            <strong class="expert-team-panel-title">${safeEsc(card&&card.brief&&card.brief.exactTitle||presentation.visibleTitle||'专家团任务')}</strong>
            <span class="expert-team-panel-summary">${safeEsc(card&&card.brief?`${card.brief.documentTypeLabel||'待选择文种'} · Brief revision ${card.brief.revision||0}`:(card&&card.team&&card.team.title||'专家团'))}</span>
          </span>
          <button type="button" class="expert-team-panel-hide expert-team-panel-collapse-toggle" onclick="toggleExpertTeamWorkspaceFromControl(this);event.stopPropagation()" aria-label="展开或合上专家团工作台">
            <span class="expert-team-panel-collapse-icon is-collapse">合上</span>
            <span class="expert-team-panel-collapse-icon is-expand">展开</span>
          </button>
        </div>
        <div class="expert-team-panel-overview">
          <span class="expert-team-panel-copy" role="status" aria-live="polite"><span class="expert-team-panel-status ${safeEsc(statusTone)}">${safeEsc(presentation.statusLabel||presentation.title||'专家团状态')}</span><span class="expert-team-panel-summary">${safeEsc(presentation.gateSummary||presentation.detail||'')}</span></span>
          <span class="expert-team-panel-progress-summary"><b>${safeEsc(progress.text)}</b><i><em style="width:${progress.pct}%"></em></i><small>${safeEsc(currentStage.phase||card&&card.phase||'需求确认')}</small></span>
        </div>
      </div>
      <div id="expert-team-workspace-expanded" class="expert-team-panel-expanded-body">
        ${recoverableDraftHintHtml}
        ${completionGatesHtml}
        ${briefCardHtml}
        <div class="expert-team-confirmation-wizard" data-expert-team-workspace-mode="confirm" data-confirmation-title="需求确认 1/" data-ready-label="确认并下一题" data-draft-label="保存草稿" data-defer-label="稍后处理">${questionPopoverHtml}</div>
        <nav class="expert-team-panel-tabs" role="tablist" aria-label="专家团工作台视图" onkeydown="handleExpertTeamWorkspaceTabKeydown(event)">
          <button id="expert-team-tab-task" type="button" role="tab" class="is-active" data-expert-team-workspace-tab="task" aria-selected="true" aria-controls="expert-team-tabpanel-task" tabindex="0" onclick="switchExpertTeamWorkspaceTab(this);event.stopPropagation()"><span>任务</span><small>${safeEsc(tabStatusText(presentation.state))}</small></button>
          <button id="expert-team-tab-result" type="button" role="tab" data-expert-team-workspace-tab="result" aria-selected="false" aria-controls="expert-team-tabpanel-result" tabindex="-1" onclick="switchExpertTeamWorkspaceTab(this);event.stopPropagation()"><span>成果</span><small>${safeEsc(readyArtifactCount?`${readyArtifactCount} 个可打开`:(presentation.state==='completed'?'可查看':'沉淀中'))}</small></button>
          <button id="expert-team-tab-process" type="button" role="tab" data-expert-team-workspace-tab="process" aria-selected="false" aria-controls="expert-team-tabpanel-process" tabindex="-1" onclick="switchExpertTeamWorkspaceTab(this);event.stopPropagation()"><span>过程</span><small>${safeEsc(members.length?`${progress.text} · ${members.length} 人`:progress.text)}</small></button>
        </nav>
        ${tabPanel('task',todoPanelHtml,true)}
        ${tabPanel('result',resultPanelHtml,false)}
        ${tabPanel('process',collaborationPanelHtml,false)}
      </div>
    </div>`;
  }
  if(typeof window!=='undefined'){
    window.setExpertTeamCapsuleExpanded=setExpertTeamCapsuleExpanded;
    window.showExpertTeamWorkspaceFromCapsule=showExpertTeamWorkspaceFromCapsule;
    window.toggleExpertTeamWorkspaceFromControl=toggleExpertTeamWorkspaceFromControl;
    window.expertTeamDockSummaryFromPresentation=expertTeamDockSummaryFromPresentation;
    window.renderExpertTeamWorkspaceFromPresentation=renderExpertTeamWorkspaceFromPresentation;
    window.openExpertTeamWpsAcceptance=openExpertTeamWpsAcceptance;
  }
})();
