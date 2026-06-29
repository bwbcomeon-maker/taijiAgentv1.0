(function(){
  function safeEsc(value){ return (typeof esc==='function')?esc(value):String(value==null?'':value).replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];}); }
  function actionButton(action,extraClass){
    if(!action||!action.id)return '';
    const cls=extraClass||'expert-team-action-button';
    return `<button type="button" class="${cls}" data-expert-team-action="${safeEsc(action.id)}" onclick="handleExpertTeamPresentationAction(this);event.stopPropagation()">${safeEsc(action.label||'操作')}</button>`;
  }
  function presentationTone(state){
    if(state==='generating'||state==='ready_to_generate'||state==='revising')return 'running';
    if(state==='generated_invalid'||state==='failed')return 'issue';
    if(state==='completed')return 'done';
    return 'waiting';
  }
  function statusText(status){
    return {
      pending:'待执行',
      running:'执行中',
      done:'完成',
      awaiting_review:'待复核',
      error:'需处理',
      cancelled:'已取消',
      collecting_required:'待确认',
      collecting_optional:'待补充',
      ready_to_generate:'待启动',
      generating:'生成中',
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
  function renderExpertTeamDockFromPresentation(card){
    const presentation=card&&card.presentation||{};
    const summary=expertTeamDockSummaryFromPresentation(card);
    return `<div class="status-card-expert-dock-summary ${safeEsc(summary.state)}">
      <span class="status-card-expert-dock-copy">
        <strong>${safeEsc(summary.title)}</strong>
        <small>${safeEsc(summary.detail)}</small>
      </span>
      <span class="status-card-expert-dock-action">${actionButton(presentation.primaryAction,'status-card-expert-dock-button')}</span>
    </div>`;
  }
  function renderExpertTeamWorkspaceFromPresentation(card){
    const presentation=card&&card.presentation||{};
    const workspace=card&&card.workspace||{};
    const stageResult=card&&card.stageResult||workspace.stageResult||{};
    const currentStage=workspace.currentStage||{};
    const currentWorker=workspace.currentWorker||{};
    const result=presentation.result||{};
    const tasks=Array.isArray(card&&card.tasks)?card.tasks:[];
    const members=Array.isArray(card&&card.members)?card.members:[];
    const timelineEvents=Array.isArray(workspace.timeline)&&workspace.timeline.length?workspace.timeline:(Array.isArray(card&&card.timelineEvents)?card.timelineEvents:[]);
    const taskRows=tasks.map(task=>{
      const active=(task.id&&task.id===(currentStage.id||currentStage.task_id))?' active':'';
      return `<span class="expert-team-process-row${active}"><b>${safeEsc(task.title||task.id||'阶段')}</b><small>${safeEsc(task.worker_name||'专家')} · ${safeEsc(task.statusText||statusText(task.status))}</small></span>`;
    }).join('');
    const memberHtml=members.length
      ? `<div class="expert-team-member-strip" aria-label="专家团成员状态">${members.map(member=>{
          const tone=presentationTone(member.status==='执行中'?'generating':member.status==='已完成'?'completed':'collecting_required');
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
            <span class="expert-team-result-card-actions">
              ${actionButton({id:'view_result',label:'查看完整成果'},'expert-team-result-card-button')}
            </span>
          </span>
        </div>`
      : `<div class="expert-team-empty-result">结果将在生成完成后显示</div>`;
    const stageSummary=stageResult&&stageResult.summary?stageResult.summary:(result&&result.summary||'当前阶段产物生成后会在这里沉淀。');
    const currentWorkerHtml=currentWorker&&currentWorker.name
      ? `<div class="expert-team-current-worker">${avatarHtml(currentWorker.image,currentWorker.name)}<span><strong>${safeEsc(currentWorker.name)}</strong><small>${safeEsc(currentWorker.role||currentStage.phase||'当前阶段负责专家')}</small></span></div>`
      : '';
    return `<div class="expert-team-panel-inner" data-expert-team-presentation-state="${safeEsc(presentation.state||'')}">
      <section class="expert-team-panel-section expert-team-workbench-hero" aria-label="专家团工作台">
        <div class="expert-team-panel-section-title"><span>专家团工作台</span><small>${safeEsc(card&&card.team&&card.team.title||'专家团')}</small></div>
        <div class="expert-team-workbench-grid">
          <span><b>${safeEsc(currentStage.phase||card&&card.phase||'需求确认')}</b><small>当前阶段</small></span>
          <span><b>${safeEsc(currentStage.title||presentation.visibleTitle||'阶段任务')}</b><small>阶段任务</small></span>
          <span><b>${safeEsc(currentWorker.name||currentStage.worker_name||'专家团')}</b><small>当前专家</small></span>
        </div>
        ${currentWorkerHtml}
      </section>
      <section class="expert-team-panel-section">
        <div class="expert-team-panel-section-title"><span>${safeEsc(presentation.title||'专家团状态')}</span><small>${safeEsc(presentation.visibleTitle||'')}</small></div>
        <p class="expert-team-panel-detail">${safeEsc(presentation.detail||'')}</p>
        ${memberHtml}
        <div class="expert-team-panel-actions">${actionButton(presentation.primaryAction,'expert-team-panel-action expert-team-primary-action')}${(presentation.secondaryActions||[]).map(action=>actionButton(action,'expert-team-panel-action expert-team-secondary-action')).join('')}</div>
      </section>
      <section class="expert-team-panel-section">
        <div class="expert-team-panel-section-title"><span>成果状态</span><small>${safeEsc(presentation.state==='generating'?'专家团正在生成':presentation.state==='generated_invalid'?'草稿未通过校验':presentation.state==='awaiting_review'?'阶段成果待复核':'当前状态')}</small></div>
        <p class="expert-team-panel-detail">${safeEsc(stageSummary)}</p>
        ${resultHtml}
      </section>
      <section class="expert-team-panel-section">
        <div class="expert-team-panel-section-title"><span>执行明细</span><small>${safeEsc(card&&card.team&&card.team.title||'专家团')}</small></div>
        ${timelineHtml}
        <div class="expert-team-process-panel">${taskRows||'<span class="expert-team-process-row">等待阶段初始化</span>'}</div>
      </section>
    </div>`;
  }
  if(typeof window!=='undefined'){
    window.expertTeamDockSummaryFromPresentation=expertTeamDockSummaryFromPresentation;
    window.renderExpertTeamDockFromPresentation=renderExpertTeamDockFromPresentation;
    window.renderExpertTeamWorkspaceFromPresentation=renderExpertTeamWorkspaceFromPresentation;
  }
})();
