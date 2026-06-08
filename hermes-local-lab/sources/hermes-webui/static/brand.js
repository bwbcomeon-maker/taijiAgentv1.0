// Centralized user-facing brand metadata and production asset paths.
(function(){
  'use strict';
  const assetRoot = 'static/assets/taiji';
  window.TAIJI_BRAND = {
    brandName: 'taiji Agent',
    brandSubtitle: '企业级本地智能助理',
    assets: {
      logo: assetRoot + '/logo/logo-mark.png',
      logoMark: assetRoot + '/logo/logo-mark.png',
      logoWithText: '',
      backgroundGrid: assetRoot + '/background/background-grid.png',
      nav: {
        chat: assetRoot + '/nav/nav-chat.png',
        tasks: assetRoot + '/nav/nav-tasks.png',
        kanban: assetRoot + '/nav/nav-kanban.png',
        writing: assetRoot + '/nav/nav-writing.png',
        skills: assetRoot + '/nav/nav-skills.png',
        memory: assetRoot + '/nav/nav-memory.png',
        workspaces: assetRoot + '/nav/nav-workspaces.png',
        profiles: assetRoot + '/nav/nav-profiles.png',
        todos: assetRoot + '/nav/nav-todos.png',
        insights: assetRoot + '/nav/nav-insights.png',
        logs: assetRoot + '/nav/nav-logs.png',
        settings: assetRoot + '/nav/nav-settings.png',
        dashboard: assetRoot + '/nav/nav-dashboard.png',
      },
      action: {
        search: assetRoot + '/action/action-search.png',
        new: assetRoot + '/action/action-new.png',
        expand: assetRoot + '/action/action-expand.png',
        collapse: assetRoot + '/action/action-collapse.png',
        attach: assetRoot + '/action/action-attach.png',
        voice: assetRoot + '/action/action-voice.png',
        user: assetRoot + '/action/action-user.png',
        folder: assetRoot + '/action/action-folder.png',
        model: assetRoot + '/action/action-model.png',
        mode: assetRoot + '/action/action-mode.png',
        scope: assetRoot + '/action/action-scope.png',
        send: assetRoot + '/action/action-send.png',
      },
    },
  };
})();
