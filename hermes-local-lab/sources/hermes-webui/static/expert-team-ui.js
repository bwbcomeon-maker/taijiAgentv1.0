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
    const result=presentation.result||{};
    const tasks=Array.isArray(card&&card.tasks)?card.tasks:[];
    const taskRows=tasks.map(task=>`<span class="expert-team-process-row"><b>${safeEsc(task.title||task.id||'阶段')}</b><small>${safeEsc(task.status||'待执行')}</small></span>`).join('');
    const resultHtml=result&&result.content
      ? `<div class="expert-team-result-card" data-expert-team-result-card="1">
          <strong>${safeEsc(result.visible_title||result.title||presentation.visibleTitle||'专家团成果')}</strong>
          <small>${safeEsc(result.summary||'结果已写入当前对话')}</small>
          <div class="expert-team-result-card-actions">
            ${actionButton({id:'view_result',label:'查看完整成果'},'expert-team-result-card-button')}
          </div>
        </div>`
      : `<div class="expert-team-empty-result">结果将在生成完成后显示</div>`;
    const secondary=(presentation.secondaryActions||[]).map(action=>actionButton(action,'expert-team-secondary-action')).join('');
    return `<div class="expert-team-panel-inner" data-expert-team-presentation-state="${safeEsc(presentation.state||'')}">
      <section class="expert-team-panel-section">
        <div class="expert-team-panel-section-title"><span>${safeEsc(presentation.title||'专家团状态')}</span><small>${safeEsc(presentation.visibleTitle||'')}</small></div>
        <p class="expert-team-panel-detail">${safeEsc(presentation.detail||'')}</p>
        <div class="expert-team-panel-actions">${actionButton(presentation.primaryAction,'expert-team-primary-action')}${secondary}</div>
      </section>
      <section class="expert-team-panel-section">
        <div class="expert-team-panel-section-title"><span>成果状态</span><small>${safeEsc(presentation.state==='generating'?'专家团正在生成':presentation.state==='generated_invalid'?'草稿未通过校验':presentation.state==='awaiting_review'?'阶段成果待复核':'当前状态')}</small></div>
        ${resultHtml}
      </section>
      <section class="expert-team-panel-section">
        <div class="expert-team-panel-section-title"><span>执行明细</span><small>${safeEsc(card&&card.team&&card.team.title||'专家团')}</small></div>
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
