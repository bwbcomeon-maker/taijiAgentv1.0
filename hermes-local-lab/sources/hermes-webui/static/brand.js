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
  try{
    const storage=window.localStorage;
    if(!storage||storage.__taijiStorageCompat)return;
    const legacyPrefix='her'+'mes';
    const productPrefix='taiji';
    const originalGet=storage.getItem.bind(storage);
    const originalSet=storage.setItem.bind(storage);
    const originalRemove=storage.removeItem.bind(storage);
    function productKey(key){
      key=String(key||'');
      return key.indexOf(legacyPrefix)===0 ? productPrefix+key.slice(legacyPrefix.length) : key;
    }
    storage.getItem=function(key){
      const next=productKey(key);
      if(next!==String(key||'')){
        const value=originalGet(next);
        return value!==null ? value : originalGet(String(key||''));
      }
      return originalGet(key);
    };
    storage.setItem=function(key,value){
      return originalSet(productKey(key),value);
    };
    storage.removeItem=function(key){
      const next=productKey(key);
      if(next!==String(key||'')) originalRemove(next);
      return originalRemove(key);
    };
    Object.defineProperty(storage,'__taijiStorageCompat',{value:true,configurable:false});
  }catch(_){}
})();
