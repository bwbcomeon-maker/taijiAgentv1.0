(function(){
  function safeEsc(value){ return (typeof esc==='function')?esc(value):String(value==null?'':value).replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];}); }
  function actionButton(action,extraClass){
    if(!action||!action.id)return '';
    const cls=extraClass||'expert-team-action-button';
    const kind=action.kind?` data-expert-team-action-kind="${safeEsc(action.kind)}"`:'';
    return `<button type="button" class="${cls}" data-expert-team-action="${safeEsc(action.id)}"${kind} onclick="handleExpertTeamPresentationAction(this);event.stopPropagation()" aria-label="${safeEsc(action.label||'操作')}">${safeEsc(action.label||'操作')}</button>`;
  }
  function presentationTone(state){
    if(state==='generating'||state==='ready_to_generate'||state==='revising')return 'running';
    if(state==='awaiting_stage_input'||state==='collecting_required'||state==='collecting_optional'||state==='awaiting_review')return 'waiting';
    if(state==='generated_invalid'||state==='failed')return 'issue';
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
      generating:'生成中',
      awaiting_stage_input:'待确认',
      completed:'已完成',
      failed:'失败'
    }[String(status||'')]||String(status||'待执行');
  }
  function avatarHtml(src,label){
    const name=safeEsc(label||'专家');
    const fallback=safeEsc(String(label||'专').trim().slice(0,1)||'专');
    if(src){
      return `<span class="expert-team-member-avatar"><img src="${safeEsc(src)}" alt="${name}" loading="lazy"><span>${fallback}</span></span>`;
    }
    return `<span class="expert-team-member-avatar"><span>${fallback}</span></span>`;
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
    const statusTone=presentationTone(presentation.state);
    const timelineEvents=Array.isArray(workspace.timeline)&&workspace.timeline.length?workspace.timeline:(Array.isArray(card&&card.timelineEvents)?card.timelineEvents:[]);
    const currentStageId=String(currentStage.id||currentStage.task_id||'');
    const taskRows=tasks.map(task=>{
      const active=(task.id&&String(task.id)===currentStageId)?' active':'';
      return `<span class="expert-team-process-row${active}"><b>${safeEsc(task.title||task.id||'阶段')}</b><small>${safeEsc(task.worker_name||'专家')} · ${safeEsc(task.statusText||statusText(task.status))}</small></span>`;
    }).join('');
    const phaseRows=tasks.map((task,idx)=>{
      const active=(task.id&&String(task.id)===currentStageId)?' active':'';
      const doneCls=idx<progress.currentIndex||task.status==='done'?' done':'';
      const waitCls=task.status==='awaiting_input'?' waiting':'';
      return `<span class="expert-team-panel-phase${active}${doneCls}${waitCls}"><i>${idx+1}</i><b>${safeEsc(task.phase||task.title||`阶段${idx+1}`)}</b><small>${safeEsc(task.statusText||statusText(task.status))}</small></span>`;
    }).join('');
    const memberHtml=members.length
      ? `<div class="expert-team-member-strip" aria-label="专家团成员状态">${members.map(member=>{
          const tone=presentationTone(member.status==='执行中'?'generating':member.status==='已完成'?'completed':member.status==='等待确认'?'awaiting_stage_input':'collecting_required');
          return `<span class="expert-team-member ${safeEsc(tone)}">${avatarHtml(member.image,member.name)}<span><strong>${safeEsc(member.name||member.id||'专家')}</strong><small>${safeEsc(member.role||member.status||'协作')}</small></span></span>`;
        }).join('')}</div>`
      : '';
    const timelineHtml=timelineEvents.length
      ? `<div class="expert-team-timeline" aria-label="专家团动态">${timelineEvents.slice(0,6).map(event=>`<span class="expert-team-timeline-item">${avatarHtml(event.memberImage,event.memberName||event.title)}<span><strong>${safeEsc(event.title||'专家团动态')}</strong><small>${safeEsc(event.detail||event.memberName||'')}</small></span></span>`).join('')}</div>`
      : `<div class="expert-team-timeline" aria-label="专家团动态"><span class="expert-team-timeline-item"><span class="expert-team-member-avatar"><span>专</span></span><span><strong>专家团已就绪</strong><small>等待当前阶段推进</small></span></span></div>`;
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
    const stageSummary=stageResult&&stageResult.summary?stageResult.summary:(result&&result.summary||'当前阶段产物生成后会在这里沉淀。');
    const currentWorkerHtml=currentWorker&&currentWorker.name
      ? `<div class="expert-team-current-worker">${avatarHtml(currentWorker.image,currentWorker.name)}<span><strong>${safeEsc(currentWorker.name)}</strong><small>${safeEsc(currentWorker.role||currentStage.phase||'当前阶段负责专家')}</small></span></div>`
      : '';
    const reviewActions={
      view:secondaryActions.find(action=>action&&action.id==='view_result')||{id:'view_result',label:'查看成果',kind:'ghost'},
      approve:secondaryActions.find(action=>action&&action.id==='approve_stage')||{id:'approve_stage',label:'无修改，进入下一阶段',kind:'primary'},
      revise:secondaryActions.find(action=>action&&action.id==='revise_stage')||{id:'revise_stage',label:'需要修改',kind:'ghost'}
    };
    const reviewItems=Array.isArray(card&&card.reviewItems)?card.reviewItems:[];
    const reviewItemHtml=reviewItems.length
      ? `<div class="expert-team-review-items-list">${reviewItems.map((item,idx)=>`<span class="expert-team-review-item"><i>${idx+1}</i><b>${safeEsc(item.title||'待确认事项')}</b><small>${safeEsc(item.phase||'待人工补充')}</small><span class="expert-team-review-item-actions"><button type="button" data-expert-team-review-item-title="${safeEsc(item.title||'')}" onclick="appendExpertTeamReviewItemToRevision(this);event.stopPropagation()">加入修改意见</button><button type="button" onclick="markExpertTeamReviewItemRead(this);event.stopPropagation()">标记已阅</button></span></span>`).join('')}</div>`
      : '';
    const stageReviewHtml=presentation.state==='awaiting_review'
      ? `<section class="expert-team-panel-section expert-team-stage-review" aria-label="阶段成果复核" data-expert-team-run-id="${safeEsc(runId)}">
          <div class="expert-team-panel-section-title"><span>阶段成果待复核</span><small>${safeEsc(currentStage.phase||'当前阶段')}</small></div>
          <p class="expert-team-panel-detail">${safeEsc(stageSummary||'阶段结果已生成，请查看后确认是否进入下一阶段。')}</p>
          ${resultHtml}
          ${reviewItemHtml}
          <div class="expert-team-stage-actions">
            ${actionButton(reviewActions.view,'expert-team-panel-action expert-team-secondary-action expert-team-stage-locate')}
            ${actionButton(reviewActions.approve,'expert-team-panel-action expert-team-primary-action expert-team-stage-approve')}
            ${actionButton(reviewActions.revise,'expert-team-panel-action expert-team-secondary-action expert-team-stage-revision-toggle')}
          </div>
          <div class="expert-team-stage-feedback" hidden aria-hidden="true">
            <label><span>修改意见</span><textarea data-expert-team-stage-feedback rows="4" placeholder="请写明需要调整的内容、口径、事实或结构。"></textarea></label>
            <button type="button" class="expert-team-panel-action expert-team-primary-action" data-expert-team-stage-revise-run-id="${safeEsc(runId)}" onclick="submitExpertTeamStageRevision(this);event.stopPropagation()">提交修改意见</button>
          </div>
        </section>`
      : '';
    const stageInputHtml=presentation.state==='awaiting_stage_input'?renderStageInput(card,pendingInput):'';
    const genericPrimaryAction=presentation.primaryAction&&presentation.state!=='awaiting_stage_input'&&presentation.state!=='awaiting_review';
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
    const questionPopoverHtml=(typeof _expertTeamQuestionPopoverHtml==='function')?_expertTeamQuestionPopoverHtml(card):'';
    const currentTodoLabel=presentation.state==='awaiting_review'
      ? '阶段成果待复核'
      : (presentation.state==='awaiting_stage_input'
        ? '当前阶段需要确认'
        : (presentation.primaryAction&&presentation.primaryAction.label||statusText(presentation.state)));
    const currentTodoDetail=presentation.detail||stageSummary||'请处理当前专家团待办。';
    const hasDetailedTask=!!(genericActionHtml||stageInputHtml||stageReviewHtml);
    const actionSummaryHtml=presentation.primaryAction&&!hasDetailedTask
      ? `<button type="button" class="expert-team-panel-inline-action" data-expert-team-action="${safeEsc(presentation.primaryAction.id)}" data-expert-team-action-kind="${safeEsc(presentation.primaryAction.kind||'')}" onclick="handleExpertTeamPresentationAction(this);event.stopPropagation()">${safeEsc(presentation.primaryAction.label||'处理')}</button>`
      : `<span class="expert-team-panel-inline-note">${safeEsc(statusText(presentation.state))}</span>`;
    const todoSummaryCardHtml=hasDetailedTask?'':`<section class="expert-team-panel-section expert-team-primary-task-card" aria-label="当前待办摘要">
          <div class="expert-team-panel-section-title"><span>当前待办</span><small>${safeEsc(statusText(presentation.state))}</small></div>
          <div class="expert-team-todo-summary">
            <span><strong>${safeEsc(currentTodoLabel)}</strong><small>${safeEsc(currentTodoDetail)}</small></span>
            ${actionSummaryHtml}
          </div>
        </section>`;
    const todoPanelHtml=`${genericActionHtml}${stageInputHtml}${stageReviewHtml}${todoSummaryCardHtml}`;
    const flowPanelHtml=`<section class="expert-team-panel-section expert-team-workbench-hero" aria-label="专家团当前阶段">
          <div class="expert-team-panel-section-title"><span>当前协作</span><small>${safeEsc(currentStage.phase||card&&card.phase||'需求确认')}</small></div>
          <div class="expert-team-workbench-grid">
            <span><b>${safeEsc(currentStage.phase||card&&card.phase||'需求确认')}</b><small>当前阶段</small></span>
            <span><b>${safeEsc(currentStage.title||presentation.visibleTitle||'阶段任务')}</b><small>阶段任务</small></span>
            <span><b>${safeEsc(currentWorker.name||currentStage.worker_name||'专家团')}</b><small>当前专家</small></span>
          </div>
          ${currentWorkerHtml}
        </section>
        <section class="expert-team-panel-section">
          <div class="expert-team-panel-section-title"><span>阶段进度</span><small>${safeEsc(progress.text)}</small></div>
          <div class="expert-team-panel-progress" style="--expert-team-panel-progress:${progress.pct}%"><i></i><span><b>${safeEsc(progress.text)}</b> · ${safeEsc(currentStage.title||'等待阶段推进')}</span></div>
          <div class="expert-team-panel-phases">${phaseRows||'<span class="expert-team-panel-phase active"><i>0</i><b>需求确认</b><small>前置准备</small></span>'}</div>
        </section>
        <section class="expert-team-panel-section"><div class="expert-team-panel-section-title"><span>执行明细</span><small>${safeEsc(card&&card.team&&card.team.title||'专家团')}</small></div>${timelineHtml}<div class="expert-team-process-panel">${taskRows||'<span class="expert-team-process-row">等待阶段初始化</span>'}</div></section>`;
    const membersPanelHtml=`<section class="expert-team-panel-section"><div class="expert-team-panel-section-title"><span>专家团成员</span><small>各司其职</small></div>${memberHtml||'<div class="expert-team-panel-empty">专家团成员将在任务初始化后显示</div>'}</section>`;
    const resultPanelHtml=`<section class="expert-team-panel-section">
          <div class="expert-team-panel-section-title"><span>成果状态</span><small>${safeEsc(presentation.state==='generating'?'专家团正在生成':presentation.state==='awaiting_stage_input'?'等待确认后继续':presentation.state==='generated_invalid'?'草稿未通过校验':presentation.state==='awaiting_review'?'阶段成果待复核':'当前状态')}</small></div>
          <p class="expert-team-panel-detail">${safeEsc(stageSummary)}</p>
          ${presentation.state==='awaiting_review'?'':resultHtml}
        </section>`;
    function tabPanel(id,html,active){
      return `<div class="expert-team-tab-panel" data-expert-team-tab-panel="${safeEsc(id)}" ${active?'':'hidden'}>${html}</div>`;
    }
    return `<div class="expert-team-panel-inner" data-expert-team-run-id="${safeEsc(runId)}" data-expert-team-presentation-state="${safeEsc(presentation.state||'')}" data-expert-team-workspace-mode="summary">
      <div class="expert-team-capsule" aria-label="专家团收起状态">
        <span class="expert-team-capsule-icon">专</span>
        <strong>${safeEsc(progress.text)}</strong>
        <small>${safeEsc(currentStage.phase||card&&card.phase||'需求确认')}</small>
        <span class="expert-team-capsule-state ${safeEsc(statusTone)}">${safeEsc(presentation.title||'专家团状态')}</span>
        <button type="button" class="expert-team-capsule-action" onclick="showExpertTeamWorkspacePanel(this);event.stopPropagation()" aria-label="展开专家团工作台">处理</button>
      </div>
      <div class="expert-team-panel-head">
        <div class="expert-team-panel-topbar">
          <span class="expert-team-panel-copy">
            <small class="expert-team-panel-eyebrow">专家团工作台</small>
            <strong class="expert-team-panel-title">${safeEsc(card&&card.team&&card.team.title||'专家团')}</strong>
            <span class="expert-team-panel-summary">${safeEsc(presentation.visibleTitle||'专家团任务')}</span>
          </span>
          <button type="button" class="expert-team-panel-hide expert-team-panel-collapse-toggle" onclick="toggleExpertTeamWorkspacePanel(this);event.stopPropagation()" aria-label="展开或合上专家团工作台">
            <span class="expert-team-panel-collapse-icon is-collapse">合上</span>
            <span class="expert-team-panel-collapse-icon is-expand">展开</span>
          </button>
        </div>
        <div class="expert-team-panel-overview">
          <span class="expert-team-panel-copy"><span class="expert-team-panel-status ${safeEsc(statusTone)}">${safeEsc(presentation.title||'专家团状态')}</span><span class="expert-team-panel-summary">${safeEsc(presentation.detail||'')}</span></span>
          <span class="expert-team-panel-progress-summary"><b>${safeEsc(progress.text)}</b><i><em style="width:${progress.pct}%"></em></i><small>${safeEsc(currentStage.phase||card&&card.phase||'需求确认')}</small></span>
        </div>
      </div>
      <div class="expert-team-panel-expanded-body">
        <div class="expert-team-confirmation-wizard" data-expert-team-workspace-mode="confirm" data-confirmation-title="需求确认 1/" data-ready-label="确认并下一题" data-draft-label="保存草稿" data-defer-label="稍后处理">${questionPopoverHtml}</div>
        <nav class="expert-team-panel-tabs" aria-label="专家团工作台视图">
          <button type="button" class="is-active" data-expert-team-workspace-tab="todo" aria-selected="true" onclick="switchExpertTeamWorkspaceTab(this);event.stopPropagation()"><span>待办</span><small>${safeEsc(statusText(presentation.state))}</small></button>
          <button type="button" data-expert-team-workspace-tab="flow" aria-selected="false" onclick="switchExpertTeamWorkspaceTab(this);event.stopPropagation()"><span>流程</span><small>${safeEsc(progress.text)}</small></button>
          <button type="button" data-expert-team-workspace-tab="members" aria-selected="false" onclick="switchExpertTeamWorkspaceTab(this);event.stopPropagation()"><span>成员</span><small>${safeEsc(members.length?`${members.length} 人`:'待定')}</small></button>
          <button type="button" data-expert-team-workspace-tab="result" aria-selected="false" onclick="switchExpertTeamWorkspaceTab(this);event.stopPropagation()"><span>成果</span><small>${safeEsc(presentation.state==='completed'?'可查看':'沉淀中')}</small></button>
        </nav>
        ${tabPanel('todo',todoPanelHtml,true)}
        ${tabPanel('flow',flowPanelHtml,false)}
        ${tabPanel('members',membersPanelHtml,false)}
        ${tabPanel('result',resultPanelHtml,false)}
      </div>
    </div>`;
  }
  if(typeof window!=='undefined'){
    window.expertTeamDockSummaryFromPresentation=expertTeamDockSummaryFromPresentation;
    window.renderExpertTeamWorkspaceFromPresentation=renderExpertTeamWorkspaceFromPresentation;
  }
})();
