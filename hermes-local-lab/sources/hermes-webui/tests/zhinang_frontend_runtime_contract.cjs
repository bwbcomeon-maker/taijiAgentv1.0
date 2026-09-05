'use strict';
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const sourcePath=process.argv[2]||'/private/tmp/taiji-zhinang-draft-probe/sessions.baseline.js';
const src=fs.readFileSync(sourcePath,'utf8');
const prefix=src.slice(0,src.indexOf('const SESSION_VIEWED_COUNTS_KEY'));
const flows=src.slice(src.indexOf('let _newSessionInFlight='),src.indexOf('function _forceChatSessionPanel'));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject};}
async function tick(){for(let i=0;i<20;i++)await Promise.resolve();}
function harness({holdDrafts=false,failNew=false}={}){
  const calls=[],disk=new Map(),timers=new Map();let timerId=0;
  const msg={value:'old input',focus(){}};
  const status={textContent:''};
  const S={session:{session_id:'old'},messages:[],toolCalls:[],pendingFiles:[],activeProfile:'default'};
  const el={msg,composerStatus:status,btnNewChat:{setAttribute(){}},msgInner:{innerHTML:''}};
  const noop=()=>{};
  const ctx={console,S,File,Blob,Promise,Map,Set,JSON,Math,Date,encodeURIComponent,
    $:id=>el[id]||null,t:x=>x,
    setTimeout(fn){const id=++timerId;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),
    _activeProject:null,NO_PROJECT_FILTER:'none',INFLIGHT:{},_messagesTruncated:false,_oldestIdx:0,
    localStorage:{setItem:noop,getItem:()=>null},window:{},
    setComposerStatus:text=>{status.textContent=text;},loadDir:()=>Promise.resolve(),
    _ensureMessagesLoaded:()=>Promise.resolve(),_hydrateWriteflowStatusCardForSession:()=>Promise.resolve(),
    api(url,opts){
      if(url==='/api/session/draft'){
        const body=JSON.parse(opts.body),d=deferred();
        calls.push({url,body,...d});
        const result=d.promise.then(()=>{disk.set(body.session_id,body);return {ok:true};});
        if(!holdDrafts)d.resolve();
        return result;
      }
      if(url==='/api/session/new'){
        const body=JSON.parse(opts.body);calls.push({url,body});
        if(failNew)return Promise.reject(new Error('new failed'));
        return Promise.resolve({session:{session_id:'new',messages:[],composer_draft:body.composer_draft||{text:'',files:[]}}});
      }
      if(url.startsWith('/api/session?')){
        const sid=new URL('http://fixture'+url).searchParams.get('session_id');
        return Promise.resolve({session:{session_id:sid,messages:[],composer_draft:disk.get(sid)||{text:'',files:[]}}});
      }
      throw new Error('Unexpected API: '+url);
    },
  };
  for(const name of ['_resetWriteflowDockForSessionChange','updateQueueBadge','clearLiveToolCards','_setActiveSessionUrl','_setSessionViewedCount','updateSendBtn','setStatus','syncTopbar','renderMessages','autoResize','renderTray','showToast','stopApprovalPolling','hideApprovalCard','_updateYoloPill','_resolveSessionModelForDisplaySoon','_clearSessionCompletionUnread','_hideHandoffHint'])ctx[name]=noop;
  ctx._isMessagingSession=()=>false;
  vm.createContext(ctx);vm.runInContext(prefix+'\n'+flows,ctx);
  return {ctx,S,msg,calls,disk,run:code=>vm.runInContext(code,ctx),fireTimers(){const fns=[...timers.values()];timers.clear();for(const fn of fns)fn();},drafts:()=>calls.filter(c=>c.url==='/api/session/draft')};
}
const tests=[];
function test(name,fn){tests.push([name,fn]);}
test('transition C waits behind in-flight debounce A and wins on disk',async()=>{
  const h=harness({holdDrafts:true});
  h.ctx._saveComposerDraft('old','A',[]);h.fireTimers();await tick();
  assert.equal(h.drafts().length,1);
  const transition=h.ctx._saveComposerDraftBeforeTransition('old','C',[]);await tick();
  assert.equal(h.drafts().length,1,'C must not start while A is unresolved');
  h.drafts()[0].resolve();await tick();assert.equal(h.drafts().length,2);
  assert.equal(h.drafts()[1].body.text,'C');h.drafts()[1].resolve();await transition;
  assert.equal(h.disk.get('old').text,'C');
});
test('clear waits behind in-flight A and cannot resurrect sent text',async()=>{
  const h=harness({holdDrafts:true});
  h.ctx._saveComposerDraftNow('old','A',[]);await tick();
  h.ctx._clearComposerDraft('old');await tick();
  assert.equal(h.drafts().length,1,'clear must not race A');
  h.drafts()[0].resolve();await tick();assert.equal(h.drafts().length,2);
  assert.equal(h.drafts()[1].body.text,'');h.drafts()[1].resolve();await tick();
  assert.equal(h.disk.get('old').text,'');
});
test('a rejected predecessor does not poison a later transition save',async()=>{
  const h=harness({holdDrafts:true});
  h.ctx._saveComposerDraftNow('old','A',[]);await tick();
  const transition=h.ctx._saveComposerDraftBeforeTransition('old','C',[]);await tick();
  h.drafts()[0].reject(new Error('old write failed'));await tick();
  assert.equal(h.drafts().length,2);h.drafts()[1].resolve();await transition;
  assert.equal(h.disk.get('old').text,'C');
});
test('different sid writes progress independently',async()=>{
  const h=harness({holdDrafts:true});
  h.ctx._saveComposerDraftNow('old','A',[]);await tick();
  const other=h.ctx._saveComposerDraftBeforeTransition('other','B',[]);await tick();
  assert.equal(h.drafts().length,2);h.drafts()[1].resolve();await other;
  assert.equal(h.disk.get('other').text,'B');h.drafts()[0].resolve();await tick();
});
test('successful new role is empty; new B stays in new; old A restores as File',async()=>{
  const h=harness();const A=new File(['alpha'],'A.txt'),B=new File(['beta'],'B.txt');
  assert.equal(JSON.stringify(A),'{}');h.S.pendingFiles=[A];
  await h.ctx.createZhinangSession('writer','v1');
  assert.equal(h.S.session.session_id,'new');assert.equal(h.msg.value,'');
  assert.equal(h.S.pendingFiles.length,0,'old File A must not carry into new role');
  h.S.pendingFiles=[B];h.msg.value='new input';
  await h.ctx.loadSession('old');await tick();
  assert.equal(h.S.session.session_id,'old');assert.equal(h.msg.value,'old input');
  assert.equal(h.S.pendingFiles.length,1);assert.equal(h.S.pendingFiles[0],A);
  assert.equal(await h.S.pendingFiles[0].text(),'alpha');
  await h.ctx.loadSession('new');await tick();
  assert.equal(h.S.pendingFiles.length,1);assert.equal(h.S.pendingFiles[0],B);
  assert.equal(h.msg.value,'new input');
});
test('failed new role preserves old sid/input/A exactly',async()=>{
  const h=harness({failNew:true});const A=new File(['alpha'],'A.txt');h.S.pendingFiles=[A];
  await assert.rejects(h.ctx.createZhinangSession('writer','v1'),/new failed/);
  assert.equal(h.S.session.session_id,'old');assert.equal(h.msg.value,'old input');
  assert.equal(h.S.pendingFiles.length,1);assert.equal(h.S.pendingFiles[0],A);
});
test('failed transition save does not issue new session request or change state',async()=>{
  const h=harness({holdDrafts:true});const A=new File(['alpha'],'A.txt');h.S.pendingFiles=[A];
  const pending=h.ctx.createZhinangSession('writer','v1');
  const rejected=assert.rejects(pending,/draft failed/);await tick();
  assert.equal(h.drafts().length,1);h.drafts()[0].reject(new Error('draft failed'));await rejected;
  assert.equal(h.calls.filter(c=>c.url==='/api/session/new').length,0);
  assert.equal(h.S.session.session_id,'old');assert.equal(h.msg.value,'old input');
  assert.equal(h.S.pendingFiles[0],A);
});
test('same-session force refresh preserves attachment-only live input',async()=>{
  const h=harness();const A=new File(['alpha'],'A.txt');h.S.pendingFiles=[A];h.msg.value='';
  await h.ctx.loadSession('old',{force:true});
  assert.equal(h.S.pendingFiles.length,1);assert.equal(h.S.pendingFiles[0],A);
});
test('clear invalidates cached File A so returning after send cannot resurrect it',async()=>{
  const h=harness();const A=new File(['alpha'],'A.txt'),B=new File(['beta'],'B.txt');
  h.ctx._saveComposerDraftNow('old','old input',[A]);await tick();
  h.S.pendingFiles=[];h.msg.value='';h.ctx._clearComposerDraft('old');await tick();
  h.S.session={session_id:'new'};h.S.pendingFiles=[B];h.msg.value='new input';
  await h.ctx.loadSession('old');
  assert.equal(h.msg.value,'');assert.equal(h.S.pendingFiles.length,0);
});
test('cross-session load with absent server draft clears unrelated attachment',async()=>{
  const h=harness();const A=new File(['alpha'],'A.txt');h.S.pendingFiles=[A];
  const api=h.ctx.api;
  h.ctx.api=(url,opts)=>url.startsWith('/api/session?')
    ?Promise.resolve({session:{session_id:'no-draft',messages:[]}}):api(url,opts);
  await h.ctx.loadSession('no-draft');
  assert.equal(h.S.session.session_id,'no-draft');assert.equal(h.msg.value,'');
  assert.equal(h.S.pendingFiles.length,0);
});
(async()=>{
  console.log(JSON.stringify({sourcePath,sha256:crypto.createHash('sha256').update(src).digest('hex')}));
  let failed=0;
  for(const [name,fn]of tests){try{await fn();console.log('PASS '+name);}catch(e){failed++;console.log('FAIL '+name+'\n  '+e.message);}}
  console.log(JSON.stringify({passed:tests.length-failed,failed,total:tests.length}));process.exitCode=failed?1:0;
})();
