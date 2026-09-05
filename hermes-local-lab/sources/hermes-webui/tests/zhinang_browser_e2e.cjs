'use strict';
// Run one isolated acceptance scope at a time. Required inputs:
// PLAYWRIGHT_NODE_PATH, ZHINANG_E2E_CHROMIUM, and optionally
// ZHINANG_E2E_PYTHON / ZHINANG_E2E_OUT. The harness never installs tools,
// opens a default browser, or connects to a non-loopback HTTP origin.
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const http=require('node:http');
const net=require('node:net');
const crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
if(!process.env.PLAYWRIGHT_NODE_PATH)throw new Error('PLAYWRIGHT_NODE_PATH must name an existing playwright-core module');
if(!process.env.ZHINANG_E2E_CHROMIUM)throw new Error('ZHINANG_E2E_CHROMIUM must name an existing Chromium executable');
const {chromium}=require(process.env.PLAYWRIGHT_NODE_PATH);

const REPO=path.resolve(__dirname,'../../../..');
const WEBUI=path.resolve(__dirname,'..');
const AGENT=path.resolve(__dirname,'../../hermes-agent');
const PYTHON=path.resolve(process.env.ZHINANG_E2E_PYTHON||path.join(AGENT,'venv/bin/python'));
const CHROMIUM=path.resolve(process.env.ZHINANG_E2E_CHROMIUM);
const OUT=path.resolve(process.env.ZHINANG_E2E_OUT||path.join(os.tmpdir(),'taiji-zhinang-browser-evidence'));
const primaryViewports=[[1440,900],[1280,800],[1024,768],[768,1024],[390,844]];
const boundaryViewports=[[900,800],[901,800],[902,800],[1023,768],[1024,768],[1025,768]];
const marker=`ZHINANG_E2E_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
const failureMarker=`ZHINANG_FAIL_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
const assistantDiffMarker=`ZHINANG_ASSISTANT_DIFF_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
const cancelMarker=`ZHINANG_CANCEL_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
const modelFailureMarker=`ZHINANG_MODEL_FAIL_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
const recoveryMarker=`ZHINANG_RECOVER_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
const lifecycleBeforeMarker=`ZHINANG_LIFECYCLE_BEFORE_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
const lifecycleAfterMarker=`ZHINANG_LIFECYCLE_AFTER_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
const artifactText=`# 智囊验收成果\n\n附件标记：${marker}\n\n已由售前方案顾问整理。\n`;
const assistantDiffText=`仅为模型建议 ${assistantDiffMarker}\n\n\`\`\`diff\n--- a/model_only.md\n+++ b/model_only.md\n@@ -0,0 +1 @@\n+未执行工具\n\`\`\``;
let artifactTarget='';
let failureTarget='';
let expectedWebuiRestart=false;
const evidence={marker,failureMarker,assistantDiffMarker,cancelMarker,modelFailureMarker,recoveryMarker,primaryViewports,boundaryViewports,requests:[],screenshots:[],checks:[],consoleErrors:[],pageErrors:[],externalRequests:[],metrics:{}};

function check(value,label){if(!value)throw new Error(`CHECK FAILED: ${label}`);evidence.checks.push(label);}
function sha(file){return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');}
function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms));}
function freePort(){return new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',reject);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});}
function json(res,status,payload){const body=JSON.stringify(payload);res.writeHead(status,{'content-type':'application/json','content-length':Buffer.byteLength(body)});res.end(body);}
function chunks(res,parts){res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-cache','connection':'close'});for(const part of parts)res.write(`data: ${JSON.stringify(part)}\n\n`);res.end('data: [DONE]\n\n');}
function completion(content,model='taiji-zhinang-qa'){return {id:`chatcmpl-${Date.now()}`,object:'chat.completion',created:Math.floor(Date.now()/1000),model,choices:[{index:0,message:{role:'assistant',content},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:8,total_tokens:18}};}
function streamContent(res,content,model='taiji-zhinang-qa'){
  const base={id:`chatcmpl-${Date.now()}`,object:'chat.completion.chunk',created:Math.floor(Date.now()/1000),model};
  chunks(res,[{...base,choices:[{index:0,delta:{role:'assistant'},finish_reason:null}]},{...base,choices:[{index:0,delta:{content},finish_reason:null}]},{...base,choices:[{index:0,delta:{},finish_reason:'stop'}]}]);
}
function streamTool(res,target=artifactTarget,content=artifactText,model='taiji-zhinang-qa'){
  const base={id:`chatcmpl-${Date.now()}`,object:'chat.completion.chunk',created:Math.floor(Date.now()/1000),model};
  const args=JSON.stringify({path:target,content});
  const callId=target===failureTarget?'call_zhinang_write_fail':'call_zhinang_write';
  chunks(res,[
    {...base,choices:[{index:0,delta:{role:'assistant',tool_calls:[{index:0,id:callId,type:'function',function:{name:'write_file',arguments:args}}]},finish_reason:null}]},
    {...base,choices:[{index:0,delta:{},finish_reason:'tool_calls'}]},
  ]);
}
function startProvider(port){
  return new Promise(resolve=>{
    const server=http.createServer((req,res)=>{
      if(req.method==='GET'&&req.url.startsWith('/v1/models'))return json(res,200,{object:'list',data:[{id:'taiji-zhinang-qa',object:'model',owned_by:'taiji-test'}]});
      if(req.method!=='POST'||!req.url.startsWith('/v1/chat/completions'))return json(res,404,{error:'not found'});
      let raw='';req.on('data',chunk=>raw+=chunk);req.on('end',()=>{
        let body={};try{body=JSON.parse(raw)}catch(error){return json(res,400,{error:error.message});}
        const serialized=JSON.stringify(body);
        const tools=Array.isArray(body.tools)?body.tools:[];
        const messages=Array.isArray(body.messages)?body.messages:[];
        const systemRoleText=messages.filter(message=>message&&message.role==='system'&&/Senior pre-sales engineer|Sales Engineer Agent/.test(String(message.content||''))).map(message=>String(message.content||'')).join('\n');
        const lastUserIndex=messages.reduce((found,message,index)=>message&&message.role==='user'?index:found,-1);
        const latestUser=lastUserIndex>=0?JSON.stringify(messages[lastUserIndex]):'';
        const hasToolResult=messages.slice(lastUserIndex+1).some(message=>message&&message.role==='tool');
        const markerPositions={artifact:latestUser.lastIndexOf(marker),fileFailure:latestUser.lastIndexOf(failureMarker),assistantDiff:latestUser.lastIndexOf(assistantDiffMarker),cancel:latestUser.lastIndexOf(cancelMarker),modelFailure:latestUser.lastIndexOf(modelFailureMarker),recovery:latestUser.lastIndexOf(recoveryMarker)};
        const currentIntent=Object.entries(markerPositions).reduce((latest,[name,index])=>index>latest.index?{name,index}:latest,{name:'ordinary',index:-1}).name;
        const hasMarker=currentIntent==='artifact';
        const hasFailureMarker=currentIntent==='fileFailure';
        const hasAssistantDiffMarker=currentIntent==='assistantDiff';
        const hasCancelMarker=currentIntent==='cancel';
        const hasModelFailureMarker=currentIntent==='modelFailure';
        const hasRecoveryMarker=currentIntent==='recovery';
        const hasRole=serialized.includes('Senior pre-sales engineer')||serialized.includes('Sales Engineer Agent');
        const lifecyclePhase=latestUser.includes(lifecycleAfterMarker)?'after-restart':latestUser.includes(lifecycleBeforeMarker)?'before-restart':'';
        const requestEvidence={url:req.url,stream:!!body.stream,tools:tools.map(t=>t?.function?.name),hasToolResult,currentIntent,markerPositions,hasMarker,hasFailureMarker,hasAssistantDiffMarker,hasCancelMarker,hasModelFailureMarker,hasRecoveryMarker,hasRole,lifecyclePhase,systemRoleSha256:systemRoleText?crypto.createHash('sha256').update(systemRoleText).digest('hex'):'',messageRoles:messages.map(m=>m.role)};evidence.requests.push(requestEvidence);
        if(hasModelFailureMarker)return json(res,500,{error:{message:`受控模型失败 ${modelFailureMarker}`,type:'server_error'}});
        if(hasCancelMarker&&body.stream){
          const base={id:`chatcmpl-${Date.now()}`,object:'chat.completion.chunk',created:Math.floor(Date.now()/1000),model:'taiji-zhinang-qa'};
          res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-cache','connection':'keep-alive'});
          res.write(`data: ${JSON.stringify({...base,choices:[{index:0,delta:{role:'assistant',content:'正在执行可取消任务…'},finish_reason:null}]})}\n\n`);
          requestEvidence.firstTokenSent=true;
          const heartbeat=setInterval(()=>{if(!res.destroyed)res.write(': keepalive\n\n');},250);
          const ceiling=setTimeout(()=>{if(!res.destroyed)res.end('data: [DONE]\n\n');},20000);
          const cleanup=()=>{clearInterval(heartbeat);clearTimeout(ceiling);requestEvidence.responseClosed=true;};
          res.once('close',cleanup);return;
        }
        if(hasRecoveryMarker)return body.stream?streamContent(res,`恢复成功 ${recoveryMarker}`):json(res,200,completion(`恢复成功 ${recoveryMarker}`));
        if(hasAssistantDiffMarker)return body.stream?streamContent(res,assistantDiffText):json(res,200,completion(assistantDiffText));
        if(hasToolResult){
          if(hasFailureMarker){
            check(/error|denied|拒绝|失败/i.test(serialized),'provider received real failed write_file result');
            return body.stream?streamContent(res,`写入失败，未生成文件。标记 ${failureMarker}`):json(res,200,completion(`写入失败，未生成文件。标记 ${failureMarker}`));
          }
          check(serialized.includes('bytes_written'),'provider received real write_file bytes_written result');
          return body.stream?streamContent(res,`已生成成果.md，标记 ${marker}`):json(res,200,completion(`已生成成果.md，标记 ${marker}`));
        }
        if(tools.some(tool=>tool?.function?.name==='write_file')&&(hasMarker||hasFailureMarker)){
          check(hasRole,'role snapshot reached provider system context');
          const target=hasFailureMarker?failureTarget:artifactTarget;
          const content=hasFailureMarker?'should not land':artifactText;
          const callId=hasFailureMarker?'call_zhinang_write_fail':'call_zhinang_write';
          return body.stream?streamTool(res,target,content):json(res,200,{...completion(null),choices:[{index:0,message:{role:'assistant',content:null,tool_calls:[{id:callId,type:'function',function:{name:'write_file',arguments:JSON.stringify({path:target,content})}}]},finish_reason:'tool_calls'}]});
        }
        const title=serialized.includes('title')||serialized.includes('标题')?'智囊产物验收':'已收到';
        return body.stream?streamContent(res,title):json(res,200,completion(title));
      });
    });
    server.listen(port,'127.0.0.1',()=>resolve(server));
  });
}
function waitHealth(base,proc){return new Promise(async(resolve,reject)=>{for(let i=0;i<150;i++){if(proc.exitCode!==null)return reject(new Error(`WebUI exited ${proc.exitCode}`));try{const response=await fetch(base+'/health');if(response.ok)return resolve();}catch(_){}await delay(200);}reject(new Error('WebUI health timeout'));});}
function clickRealZhinangNav(page,width){
  if(width>=901)return page.locator('.taiji-brand-nav [data-taiji-panel="zhinang"]').click();
  if(width>=768)return page.locator('nav.rail [data-panel="zhinang"]').click();
  return (async()=>{await page.locator('#btnHamburger').click();await page.locator('.sidebar [data-panel="zhinang"]').click();})();
}
async function waitCatalog(page){await page.locator('#mainZhinang').waitFor({state:'visible'});await page.locator('.zhinang-card').first().waitFor({state:'visible',timeout:15000});}
async function waitAttribute(locator,name,value,timeout=10000){const until=Date.now()+timeout;while(Date.now()<until){if(await locator.getAttribute(name)===value)return;await delay(100);}throw new Error(`attribute timeout: ${name}=${value}`);}
function attachErrors(page,label){page.on('console',msg=>{const expectedFault=((label==='fault-keyboard'||label==='selection-focus')&&/Failed to load resource:.*503/i.test(msg.text()))||(label==='draft-idempotency'&&/Failed to load resource:.*50[34]/i.test(msg.text()))||(expectedWebuiRestart&&label.startsWith('favorite-')&&/ERR_CONNECTION_(?:REFUSED|RESET)/i.test(msg.text()));if(msg.type()==='error'&&!expectedFault&&!/favicon|manifest|service.?worker|404/i.test(msg.text()))evidence.consoleErrors.push(`${label}: ${msg.text()}`);});page.on('pageerror',error=>evidence.pageErrors.push(`${label}: ${error.message}`));page.on('request',request=>{const url=new URL(request.url());if(/^https?:$/.test(url.protocol)&&!['127.0.0.1','localhost'].includes(url.hostname))evidence.externalRequests.push({label,url:url.href,method:request.method()});});}
async function viewportPass(browser,base,width,height,boundary=false){
  const context=await browser.newContext({viewport:{width,height},locale:'zh-CN',acceptDownloads:true});
  const page=await context.newPage();attachErrors(page,`${width}x${height}`);
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(['127.0.0.1','localhost'].includes(url.hostname))route.continue();else route.abort('blockedbyclient');});
  await page.goto(base,{waitUntil:'domcontentloaded'});await page.locator('body').waitFor();
  await clickRealZhinangNav(page,width);await waitCatalog(page);
  check(await page.locator('.zhinang-card').count()===6,`${width}x${height} fixed six featured`);
  check(await page.locator('#zhinangHeading').textContent()==='太极智囊',`${width}x${height} product heading`);
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth);
  check(overflow<=1,`${width}x${height} no page horizontal overflow`);
  if(!boundary){
    const shot=path.join(OUT,`catalog-${width}x${height}.png`);await page.screenshot({path:shot,fullPage:true});evidence.screenshots.push({path:shot,sha256:sha(shot)});
    const card=page.locator('.zhinang-card').first(),detailTrigger=card.locator('[data-zhinang-open]');await detailTrigger.click();await page.locator('#zhinangDetailTitle').waitFor();
    check(await page.getByText('以下均为示例，非已生成文件。').isVisible(),`${width}x${height} example disclaimer visible`);
    check(await page.getByText('使用此智囊').isVisible(),`${width}x${height} approved CTA visible`);
    const box=await page.locator('#zhinangDetail').boundingBox();check(box&&box.x>=0&&box.x+box.width<=width+1,`${width}x${height} detail within viewport`);
    const actionGeometry=await page.evaluate(()=>{
      const detail=document.querySelector('#zhinangDetail'),footer=detail?.querySelector('.zhinang-detail-actions'),button=detail?.querySelector('.zhinang-primary'),note=detail?.querySelector('.zhinang-create-note');
      const rect=node=>{const r=node?.getBoundingClientRect();return r&&{top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};};
      return {detail:rect(detail),footer:rect(footer),button:rect(button),note:rect(note)};
    });
    evidence.checks.push({label:`${width}x${height} detail action geometry`,geometry:actionGeometry});
    check(actionGeometry.detail&&actionGeometry.footer&&actionGeometry.button&&actionGeometry.note,`${width}x${height} detail action elements exist`);
    check(actionGeometry.footer.top>=actionGeometry.detail.top-1&&actionGeometry.footer.bottom<=actionGeometry.detail.bottom+1,`${width}x${height} detail footer fully visible`);
    check(actionGeometry.button.top>=actionGeometry.footer.top&&actionGeometry.button.bottom<=actionGeometry.footer.bottom+1,`${width}x${height} CTA fully visible`);
    check(actionGeometry.note.top>=actionGeometry.footer.top&&actionGeometry.note.bottom<=actionGeometry.footer.bottom+1,`${width}x${height} create note fully visible`);
    const detailShot=path.join(OUT,`detail-${width}x${height}.png`);await page.screenshot({path:detailShot,fullPage:true});evidence.screenshots.push({path:detailShot,sha256:sha(detailShot)});
    await page.keyboard.press('Escape');check(await page.locator('#zhinangDetail').isHidden(),`${width}x${height} Escape closes detail`);
    check(await detailTrigger.evaluate(node=>document.activeElement===node),`${width}x${height} detail restores visible trigger focus`);
  }
  await context.close();
}

async function realFlow(browser,base,workspace,attachment){
  const context=await browser.newContext({viewport:{width:1440,height:900},locale:'zh-CN',acceptDownloads:true});
  const page=await context.newPage();attachErrors(page,'real-flow');
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(['127.0.0.1','localhost'].includes(url.hostname))route.continue();else route.abort('blockedbyclient');});
  await page.goto(base,{waitUntil:'domcontentloaded'});await clickRealZhinangNav(page,1440);await waitCatalog(page);
  const first=page.locator('.zhinang-card').first();const roleId=await first.getAttribute('data-zhinang-role');
  const favorite=first.locator('.zhinang-favorite');if(await favorite.getAttribute('aria-pressed')!=='true')await favorite.click();
  await waitAttribute(favorite,'aria-pressed','true');
  await page.reload({waitUntil:'domcontentloaded'});await clickRealZhinangNav(page,1440);await waitCatalog(page);
  await page.locator('[data-zhinang-scope="favorites"]').click();await waitCatalog(page);
  check(await page.locator(`[data-zhinang-role="${roleId}"]`).count()===1,'favorite persisted through real backend reload');
  const preCreationRequests=evidence.requests.length;
  await page.locator(`[data-zhinang-role="${roleId}"] [data-zhinang-open]`).click();
  await page.getByText('使用此示例').first().click();
  await page.locator('#mainChat').waitFor({state:'visible',timeout:15000});
  await page.locator('#zhinangSessionRole').waitFor({state:'visible',timeout:15000});
  check((await page.locator('#zhinangSessionRole').textContent()).includes('售前方案顾问'),'new chat shows fixed role badge');
  check(await page.evaluate(expected=>S.activeProfile==='default'&&S.session&&S.session.workspace===expected&&S.session.model==='taiji-zhinang-qa'&&S.session.model_provider==='custom:custom',workspace),'role task preserves active profile, workspace, model, and provider');
  const roleBadgeGeometry=await page.evaluate(()=>{
    const box=node=>{const r=node?.getBoundingClientRect();return r&&{left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
    const badge=document.querySelector('#zhinangSessionRole'),brand=document.querySelector('.taiji-workspace-brand-pill'),hero=document.querySelector('.taiji-hero'),main=document.querySelector('main.main.taiji-real-main'),workspace=document.querySelector('.taiji-main-workspace'),shell=document.querySelector('.taiji-home-shell');
    const badgeRect=box(badge),brandRect=box(brand),center=badgeRect?document.elementFromPoint((badgeRect.left+badgeRect.right)/2,(badgeRect.top+badgeRect.bottom)/2):null;
    return {badge:badgeRect,brand:brandRect,hero:box(hero),main:box(main),workspace:box(workspace),shellClass:shell?.className||'',badgeZ:getComputedStyle(badge).zIndex,heroZ:getComputedStyle(hero).zIndex,mainZ:getComputedStyle(main).zIndex,centerTag:center?.tagName||'',centerId:center?.id||'',centerClass:center?.className||'',centerIsBadge:center===badge||!!center?.closest?.('#zhinangSessionRole')};
  });
  evidence.checks.push({label:'role badge geometry',geometry:roleBadgeGeometry});
  console.error(JSON.stringify({roleBadgeGeometry}));
  check(roleBadgeGeometry.badge&&roleBadgeGeometry.brand&&roleBadgeGeometry.badge.bottom<=roleBadgeGeometry.brand.top||roleBadgeGeometry.badge.top>=roleBadgeGeometry.brand.bottom||roleBadgeGeometry.badge.right<=roleBadgeGeometry.brand.left||roleBadgeGeometry.badge.left>=roleBadgeGeometry.brand.right,'role badge does not overlap brand pill');
  check(roleBadgeGeometry.centerIsBadge,'role badge center remains hit-test visible');
  await page.locator('#zhinangSessionRole').click();await page.getByText('历史角色快照').waitFor();check(await page.getByText('历史角色快照').isVisible(),'draft-only role badge opens historical detail by real pointer click');await page.keyboard.press('Escape');
  await page.locator('.taiji-brand-nav [data-taiji-panel="chat"]').click();check(await page.locator('#msg').inputValue()!=='' ,'draft survives role badge detail round trip');
  check(await page.locator('#msg').inputValue()!=='' ,'chosen starter copied to draft without sending');
  check(await page.evaluate(()=>typeof S!=='undefined'&&Array.isArray(S.messages)&&S.messages.length===0),'starter role task has no auto-send');
  check(evidence.requests.length===preCreationRequests,'starter role creation makes zero provider calls');
  await page.locator('#btnSend').click();await page.getByText('已收到',{exact:true}).waitFor({timeout:30000});
  const afterAcceptedRequests=evidence.requests.length;
  const starterSid=await page.evaluate(()=>S.session&&S.session.session_id);
  await clickRealZhinangNav(page,1440);await waitCatalog(page);
  await page.locator(`[data-zhinang-role="${roleId}"] [data-zhinang-open]`).click();
  await page.locator('#zhinangDetailTitle').waitFor();
  check(await page.getByText('继续最近任务').isVisible(),'used role detail keeps continue action');
  check(await page.getByText('使用此智囊').isVisible(),'used role detail also keeps create action');
  await page.getByText('使用此智囊').click();
  await page.waitForFunction(previous=>S.session&&S.session.session_id&&S.session.session_id!==previous,starterSid);
  check(await page.locator('#msg').inputValue()==='' ,'plain role task starts with empty draft');
  check(await page.evaluate(()=>Array.isArray(S.messages)&&S.messages.length===0),'plain role task has no auto-send');
  check(evidence.requests.length===afterAcceptedRequests,'plain role creation makes zero provider calls');
  await page.locator('#fileInput').setInputFiles(attachment);
  await page.locator('#msg').fill(`请读取附件标记 ${marker}，使用 write_file 生成成果.md。`);
  await page.locator('#btnSend').click();
  const artifact=path.join(workspace,'成果.md');
  for(let i=0;i<300&&!fs.existsSync(artifact);i++)await delay(100);
  check(fs.existsSync(artifact),'real write_file created workspace artifact');
  check(fs.readFileSync(artifact,'utf8')===artifactText,'workspace artifact bytes match provider tool call');
  await page.getByText(`已生成成果.md，标记 ${marker}`,{exact:false}).waitFor({timeout:30000});
  await page.locator('#msg').fill('普通追问：请确认文件已保留。');await page.locator('#btnSend').click();
  await page.getByText('已收到',{exact:true}).waitFor({timeout:30000});
  await page.locator('#btnWorkspacePanelToggle').click();await page.locator('#workspaceArtifactsTab').click();
  const artifactItem=page.locator('.workspace-artifact-item').filter({hasText:'成果.md'});await artifactItem.waitFor({timeout:15000});
  const workspaceRowGeometry=await page.evaluate(()=>{
    const box=node=>{const r=node?.getBoundingClientRect();return r&&{left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
    const panel=document.querySelector('aside.rightpanel'),item=[...document.querySelectorAll('.workspace-artifact-item')].find(node=>node.textContent.includes('成果.md')),title=item?.querySelector('.workspace-artifact-title'),artifactPath=item?.querySelector('.workspace-artifact-path');
    return {panel:box(panel),item:box(item),title:box(title),artifactPath:box(artifactPath)};
  });
  check(workspaceRowGeometry.panel&&workspaceRowGeometry.item&&workspaceRowGeometry.item.left>=workspaceRowGeometry.panel.left-1&&workspaceRowGeometry.item.right<=workspaceRowGeometry.panel.right+1,'workspace artifact row stays inside panel');
  check(workspaceRowGeometry.title&&workspaceRowGeometry.artifactPath&&workspaceRowGeometry.title.left>=workspaceRowGeometry.item.left&&workspaceRowGeometry.artifactPath.left>=workspaceRowGeometry.item.left,'workspace artifact text has a visible left edge');
  await artifactItem.click();await page.locator('#previewArea').waitFor({state:'visible',timeout:15000});const previewContent=page.locator('#previewCode:visible,#previewMd:visible').first();await previewContent.waitFor({state:'visible',timeout:15000});
  const workspacePreviewGeometry=await page.evaluate(()=>{
    const box=node=>{const r=node?.getBoundingClientRect();return r&&{left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
    const panel=document.querySelector('aside.rightpanel'),preview=document.querySelector('#previewArea'),content=document.querySelector('#previewCode:not([style*="display: none"])')||document.querySelector('#previewMd:not([style*="display: none"])');return {panel:box(panel),preview:box(preview),content:box(content),contentScrollWidth:content?.scrollWidth||0,contentClientWidth:content?.clientWidth||0};
  });
  evidence.checks.push({label:'workspace artifact geometry',row:workspaceRowGeometry,preview:workspacePreviewGeometry});
  console.error(JSON.stringify({workspaceRowGeometry,workspacePreviewGeometry}));
  check(workspacePreviewGeometry.panel&&workspacePreviewGeometry.preview&&workspacePreviewGeometry.preview.left>=workspacePreviewGeometry.panel.left-1&&workspacePreviewGeometry.preview.right<=workspacePreviewGeometry.panel.right+1,'workspace preview stays inside panel');
  check(workspacePreviewGeometry.content&&workspacePreviewGeometry.content.left>=workspacePreviewGeometry.panel.left-1&&workspacePreviewGeometry.content.right<=workspacePreviewGeometry.panel.right+1,'workspace preview text stays inside panel');
  const downloadPromise=page.waitForEvent('download');await page.locator('#btnDownloadFile').click();const download=await downloadPromise;
  const downloadPath=await download.path();check(downloadPath&&sha(downloadPath)===sha(artifact),'download bytes equal workspace artifact');
  await page.locator('#btnWorkspacePanelToggle').click();
  await page.locator('#msg').fill(`失败校验 ${failureMarker}：请调用 write_file 生成禁止成果.md。`);
  await page.locator('#btnSend').click();
  await page.getByText(`写入失败，未生成文件。标记 ${failureMarker}`,{exact:false}).waitFor({timeout:30000});
  check(!fs.existsSync(failureTarget),'failed write_file did not create an outside file');
  await page.locator('#btnWorkspacePanelToggle').click();await page.locator('#workspaceArtifactsTab').click();
  check(await page.locator('.workspace-artifact-item').filter({hasText:'禁止成果.md'}).count()===0,'failed write_file has no generated/downloadable artifact');
  await page.locator('#btnWorkspacePanelToggle').click();
  await page.locator('#msg').fill(`仅返回建议 diff，不调用工具 ${assistantDiffMarker}`);await page.locator('#btnSend').click();
  await page.waitForFunction(expected=>Array.isArray(S.messages)&&S.messages.some(message=>message&&message.role==='assistant'&&String(message.content||'').includes(expected)),assistantDiffMarker,{timeout:30000});
  await page.locator('#btnWorkspacePanelToggle').click();await page.locator('#workspaceArtifactsTab').click();
  check(await page.locator('.workspace-artifact-item').filter({hasText:'model_only.md'}).count()===0,'assistant-only diff creates no artifact card');
  const shot=path.join(OUT,'real-role-artifact-flow.png');await page.screenshot({path:shot,fullPage:true});evidence.screenshots.push({path:shot,sha256:sha(shot)});
  await page.locator('#btnWorkspacePanelToggle').click();
  const reloadedSessionResponse=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/api/session'&&url.searchParams.get('messages')==='1'&&url.searchParams.get('msg_limit')==='30';});
  await page.reload({waitUntil:'domcontentloaded'});const reloadedSession=await (await reloadedSessionResponse).json();
  const persistedCalls=(reloadedSession.session.messages||[]).flatMap(message=>Array.isArray(message.tool_calls)?message.tool_calls:[]);
  check(persistedCalls.some(call=>call&&call.tid==='call_zhinang_write'&&call.artifact_path==='成果.md'),'msg_limit API response carries validated successful artifact path');
  check(!persistedCalls.some(call=>call&&call.tid==='call_zhinang_write_fail'&&call.artifact_path),'msg_limit API response omits failed artifact path');
  await page.locator('#zhinangSessionRole').waitFor({state:'visible',timeout:15000});
  check((await page.locator('#zhinangSessionRole').textContent()).includes('售前方案顾问'),'role badge survives refresh');
  await page.locator('#btnWorkspacePanelToggle').click();await page.locator('#workspaceArtifactsTab').click();
  check(await page.locator('.workspace-artifact-item').filter({hasText:'model_only.md'}).count()===0,'assistant-only diff stays absent after msg_limit reload');
  const persistedArtifact=page.locator('.workspace-artifact-item').filter({hasText:'成果.md'});await persistedArtifact.waitFor({timeout:15000});
  await persistedArtifact.click();const reloadDownloadPromise=page.waitForEvent('download');await page.locator('#btnDownloadFile').click();const reloadDownload=await reloadDownloadPromise;const reloadDownloadPath=await reloadDownload.path();
  check(reloadDownloadPath&&sha(reloadDownloadPath)===sha(artifact),'artifact survives ordinary turn and reload with equal download bytes');
  await page.locator('#btnWorkspacePanelToggle').click();
  await clickRealZhinangNav(page,1440);await waitCatalog(page);await page.locator('.zhinang-card [data-zhinang-open]').first().click();
  const continueButton=page.getByText('继续最近任务');await continueButton.focus();await page.keyboard.press('Enter');await page.locator('#mainChat').waitFor({state:'visible'});
  check(await page.locator('#zhinangSessionRole').isVisible(),'native keyboard continue returns to role task');
  await page.locator('#zhinangSessionRole').click();await page.getByText('历史角色快照').waitFor();
  check(await page.getByText('历史角色快照').isVisible(),'historical role detail opens from chat badge');
  await page.keyboard.press('Escape');
  check(await page.locator('#zhinangHeading').evaluate(node=>document.activeElement===node),'historical detail close uses visible heading fallback');
  await context.close();
}

async function faultAndKeyboardPass(browser,base){
  const context=await browser.newContext({viewport:{width:1024,height:768},locale:'zh-CN'});const page=await context.newPage();attachErrors(page,'fault-keyboard');
  let catalogFailures=1,detailFailures=1,favoritePutFailures=1,failFavoriteRefresh=false;
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(!['127.0.0.1','localhost'].includes(url.hostname))return route.abort('blockedbyclient');
    if(url.pathname==='/api/zhinang/catalog'&&catalogFailures>0){catalogFailures--;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'目录故障注入'})});}
    if(url.pathname.startsWith('/api/zhinang/roles/')&&detailFailures>0){detailFailures--;await delay(700);return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'详情故障注入'})});}
    if(request.method()==='PUT'&&url.pathname.startsWith('/api/zhinang/favorites/')&&favoritePutFailures>0){favoritePutFailures--;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'收藏保存故障注入'})});}
    if(request.method()==='PUT'&&url.pathname.startsWith('/api/zhinang/favorites/'))failFavoriteRefresh=true;
    if(url.pathname==='/api/zhinang/catalog'&&failFavoriteRefresh){failFavoriteRefresh=false;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'刷新故障注入'})});}
    return route.continue();
  });
  await page.goto(base,{waitUntil:'domcontentloaded'});await clickRealZhinangNav(page,1024);
  await page.locator('.zhinang-error [data-zhinang-action="refresh"]').waitFor();await page.locator('.zhinang-error [data-zhinang-action="refresh"]').click();await waitCatalog(page);
  const trigger=page.locator('.zhinang-card [data-zhinang-open]').first();await trigger.click();
  const loading=page.locator('.zhinang-detail-loading');await loading.waitFor();check(await loading.evaluate(node=>document.activeElement===node),'modal loading state receives focus');
  await page.keyboard.press('Tab');check(await loading.evaluate(node=>document.activeElement===node),'modal loading state traps Tab while no controls exist');
  const retry=page.locator('[data-zhinang-retry-role]');await retry.waitFor();
  check(await page.locator('.zhinang-detail-error').evaluate(node=>document.activeElement===node),'modal error surface receives focus');
  await page.keyboard.press('Shift+Tab');check(await page.locator('[data-zhinang-close]').evaluate(node=>document.activeElement===node),'initial Shift+Tab stays inside modal');
  await retry.click();await page.locator('#zhinangDetailTitle').waitFor();check(await page.locator('.zhinang-detail-dialog').evaluate(node=>document.activeElement===node),'successful detail retry focuses modal surface');
  await page.keyboard.press('Shift+Tab');check(await page.locator('.zhinang-detail-actions button').last().evaluate(node=>document.activeElement===node),'modal focus wraps backward');
  await page.keyboard.press('Escape');check(await trigger.evaluate(node=>document.activeElement===node),'detail retry close restores trigger focus');
  const favorite=page.locator('.zhinang-card .zhinang-favorite').first();const favoriteInitial=await favorite.getAttribute('aria-pressed'),favoriteTarget=favoriteInitial==='true'?'false':'true';
  const failedFavoritePut=page.waitForResponse(response=>response.request().method()==='PUT'&&response.status()===503&&new URL(response.url()).pathname.includes('/api/zhinang/favorites/'));
  await favorite.focus();await page.keyboard.press('Enter');await failedFavoritePut;await page.waitForFunction(({selector,pressed})=>{const button=document.querySelector(selector);return button&&!button.hasAttribute('aria-disabled')&&button.getAttribute('aria-pressed')===pressed;},{selector:'.zhinang-card .zhinang-favorite',pressed:favoriteInitial});
  check(await favorite.isEnabled(),'favorite PUT failure restores enabled control');
  check(await favorite.getAttribute('aria-pressed')===favoriteInitial,'favorite PUT failure preserves original state');
  const failedFavoriteRefresh=page.waitForResponse(response=>response.status()===503&&new URL(response.url()).pathname==='/api/zhinang/catalog');
  await favorite.focus();await page.keyboard.press('Enter');await failedFavoriteRefresh;await waitAttribute(favorite,'aria-pressed',favoriteTarget);
  await page.locator('#zhinangStatus[data-state="error"]').waitFor();check((await page.locator('#zhinangStatus').textContent()).includes('收藏已保存'),'favorite success remains authoritative when refresh fails');
  const search=page.locator('#zhinangSearch');await search.fill('NO_MATCH_ZHINANG');await page.waitForTimeout(350);await page.locator('[data-zhinang-action="reset-filters"]').waitFor();await page.locator('[data-zhinang-action="reset-filters"]').click();await waitCatalog(page);check(await search.inputValue()==='','empty-state reset clears query');
  await page.locator('[data-zhinang-scope="favorites"]').click();await page.locator('.zhinang-card,.zhinang-empty').first().waitFor();await page.locator('[data-zhinang-scope="all"]').click();await waitCatalog(page);
  check(await page.locator('[data-zhinang-view="all"]').getAttribute('aria-pressed')==='true','all roles scope selects unabridged all view');
  await context.close();
}

async function selectionAndFavoriteFocusPass(browser,base){
  const context=await browser.newContext({viewport:{width:2000,height:1000},locale:'zh-CN'});const page=await context.newPage();attachErrors(page,'selection-focus');
  let favoriteMode='pass',failNextCatalog=false,releaseDeferredPut=null,signalDeferredPut=null;
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(!['127.0.0.1','localhost'].includes(url.hostname))return route.abort('blockedbyclient');
    if(url.pathname==='/api/zhinang/catalog'&&failNextCatalog){failNextCatalog=false;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'收藏刷新故障注入'})});}
    if(request.method()==='PUT'&&url.pathname.startsWith('/api/zhinang/favorites/')){
      const mode=favoriteMode;favoriteMode='pass';
      if(mode==='fail')return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'收藏保存故障注入'})});
      if(mode==='refresh-fail'){const response=await route.fetch();failNextCatalog=true;return route.fulfill({response});}
      if(mode==='defer'){
        if(signalDeferredPut)signalDeferredPut();
        await new Promise(resolve=>{releaseDeferredPut=resolve;});
      }
    }
    return route.continue();
  });
  const failures=[];
  const verify=(value,label)=>{if(value)evidence.checks.push(label);else failures.push(label);};
  const favoriteFocused=roleId=>page.evaluate(expected=>document.activeElement?.dataset?.zhinangFavorite===expected,roleId);
  await page.goto(base,{waitUntil:'domcontentloaded'});await clickRealZhinangNav(page,2000);await waitCatalog(page);
  verify(await page.locator('.zhinang-card[aria-current="true"]').count()===0,'catalog starts without a selected role card');
  const first=page.locator('.zhinang-card').nth(0),second=page.locator('.zhinang-card').nth(1);
  const firstId=await first.getAttribute('data-zhinang-role'),secondId=await second.getAttribute('data-zhinang-role'),secondName=await second.locator('h2').textContent();
  await first.locator('[data-zhinang-open]').click();await page.locator('#zhinangDetailTitle').waitFor();
  verify(await page.locator('#zhinangDetail').getAttribute('data-mode')==='aside','wide catalog opens detail as an aside');
  verify(await first.getAttribute('aria-current')==='true'&&await first.evaluate(node=>node.classList.contains('is-selected')),'opening detail marks only the corresponding card current');
  const selectedShot=path.join(OUT,'selected-card-aside-2000x1000.png');await page.screenshot({path:selectedShot,fullPage:true});evidence.screenshots.push({path:selectedShot,sha256:sha(selectedShot)});
  await second.locator('[data-zhinang-open]').click();await page.waitForFunction(expected=>document.querySelector('#zhinangDetailTitle')?.textContent===expected,secondName);
  verify(await first.getAttribute('aria-current')===null&&!await first.evaluate(node=>node.classList.contains('is-selected'))&&await second.getAttribute('aria-current')==='true','switching detail moves the current-card state');
  await page.locator('[data-zhinang-close]').click();
  verify(await page.locator('.zhinang-card[aria-current="true"],.zhinang-card.is-selected').count()===0,'closing detail clears the current-card state');
  await first.locator('[data-zhinang-open]').click();await page.locator('#zhinangDetailTitle').waitFor();
  const search=page.locator('#zhinangSearch');await search.fill('NO_MATCH_SELECTION_FOCUS');await delay(350);await page.locator('[data-zhinang-action="reset-filters"]').waitFor();
  verify(await page.locator('#zhinangDetail').isHidden()&&await page.locator('.zhinang-card[aria-current="true"],.zhinang-card.is-selected').count()===0,'filtering out the current role closes detail and clears selection');
  await page.locator('[data-zhinang-action="reset-filters"]').click();await waitCatalog(page);
  const card=page.locator(`[data-zhinang-role="${firstId}"]`),favorite=card.locator('.zhinang-favorite');
  const initial=await favorite.getAttribute('aria-pressed'),target=initial==='true'?'false':'true';
  favoriteMode='fail';const failedPut=page.waitForResponse(response=>response.request().method()==='PUT'&&new URL(response.url()).pathname.includes('/api/zhinang/favorites/'));
  await favorite.focus();await page.keyboard.press('Enter');await failedPut;await page.waitForFunction(({id,value})=>document.querySelector(`[data-zhinang-role="${id}"] .zhinang-favorite`)?.getAttribute('aria-pressed')===value,{id:firstId,value:initial});
  verify(await favoriteFocused(firstId),'PUT failure keeps keyboard focus on the rebuilt favorite control');
  favoriteMode='refresh-fail';const refreshFailed=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/zhinang/catalog'&&response.status()===503);
  await favorite.focus();await page.keyboard.press('Enter');await refreshFailed;await page.locator('#zhinangStatus[data-state="error"]').waitFor();
  verify(await favorite.getAttribute('aria-pressed')===target&&await favoriteFocused(firstId),'saved favorite with refresh failure keeps authoritative state and focus');
  const successPut=page.waitForResponse(response=>response.request().method()==='PUT'&&response.ok()&&new URL(response.url()).pathname.includes('/api/zhinang/favorites/'));
  const successCatalog=page.waitForResponse(response=>response.ok()&&new URL(response.url()).pathname==='/api/zhinang/catalog');
  await favorite.focus();await page.keyboard.press('Enter');await Promise.all([successPut,successCatalog]);await waitAttribute(favorite,'aria-pressed',initial);
  verify(await favoriteFocused(firstId),'successful favorite refresh keeps keyboard focus on the rebuilt control');
  favoriteMode='defer';let deferredStartedResolve;const deferredStarted=new Promise(resolve=>{deferredStartedResolve=resolve;});signalDeferredPut=deferredStartedResolve;
  const deferredPut=page.waitForResponse(response=>response.request().method()==='PUT'&&response.ok()&&new URL(response.url()).pathname.includes('/api/zhinang/favorites/'));
  const deferredCatalog=page.waitForResponse(response=>response.ok()&&new URL(response.url()).pathname==='/api/zhinang/catalog');
  await favorite.focus();await page.keyboard.press('Enter');await deferredStarted;
  verify(await favoriteFocused(firstId),'pending favorite request retains keyboard focus');
  await search.focus();releaseDeferredPut();await Promise.all([deferredPut,deferredCatalog]);await waitAttribute(favorite,'aria-pressed',target);
  verify(await search.evaluate(node=>document.activeElement===node),'async favorite completion does not steal focus after the user moves it');
  favoriteMode='defer';let movedStartedResolve;const movedStarted=new Promise(resolve=>{movedStartedResolve=resolve;});signalDeferredPut=movedStartedResolve;
  const movedPut=page.waitForResponse(response=>response.request().method()==='PUT'&&response.ok()&&new URL(response.url()).pathname.includes('/api/zhinang/favorites/'));
  const movedCatalog=page.waitForResponse(response=>response.ok()&&new URL(response.url()).pathname==='/api/zhinang/catalog');
  await favorite.focus();await page.keyboard.press('Enter');await movedStarted;
  const movedTarget=page.locator(`[data-zhinang-role="${secondId}"] [data-zhinang-open]`);await movedTarget.focus();releaseDeferredPut();await Promise.all([movedPut,movedCatalog]);await waitAttribute(favorite,'aria-pressed',initial);
  verify(await movedTarget.evaluate(node=>document.activeElement===node),'async favorite completion preserves the user-selected in-grid focus target');
  const restorePut=page.waitForResponse(response=>response.request().method()==='PUT'&&response.ok()&&new URL(response.url()).pathname.includes('/api/zhinang/favorites/'));
  const restoreCatalog=page.waitForResponse(response=>response.ok()&&new URL(response.url()).pathname==='/api/zhinang/catalog');
  await favorite.focus();await page.keyboard.press('Enter');await Promise.all([restorePut,restoreCatalog]);await waitAttribute(favorite,'aria-pressed',target);
  const favoritesCatalog=page.waitForResponse(response=>response.ok()&&new URL(response.url()).pathname==='/api/zhinang/catalog'&&new URL(response.url()).searchParams.get('scope')==='favorites');
  await page.locator('[data-zhinang-scope="favorites"]').click();await favoritesCatalog;await card.waitFor();
  const unfavorite=card.locator('.zhinang-favorite');const emptyCatalog=page.waitForResponse(response=>response.ok()&&new URL(response.url()).pathname==='/api/zhinang/catalog'&&new URL(response.url()).searchParams.get('scope')==='favorites');
  await unfavorite.focus();await page.keyboard.press('Enter');await emptyCatalog;await page.getByText('还没有收藏的智囊。',{exact:true}).waitFor();
  verify(await page.locator('[data-zhinang-action="browse-all"]').evaluate(node=>document.activeElement===node),'removing the focused last favorite moves focus to the visible browse-all fallback');
  signalDeferredPut=null;
  if(failures.length){const report=path.join(OUT,'e2e-red-selection-focus.json');fs.writeFileSync(report,JSON.stringify({status:'RED',failures,checks:evidence.checks},null,2));throw new Error(`P2 RED: ${failures.join('; ')}; report=${report}`);}
  await context.close();
}

async function draftAndIdempotencyPass(browser,base,attachment){
  const context=await browser.newContext({viewport:{width:1280,height:800},locale:'zh-CN'});const page=await context.newPage();attachErrors(page,'draft-idempotency');
  let failDraft=true,draftFailures=0,acceptedSid='';const newBodies=[];
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(!['127.0.0.1','localhost'].includes(url.hostname))return route.abort('blockedbyclient');
    if(request.method()==='POST'&&url.pathname==='/api/session/draft'&&failDraft){draftFailures++;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'草稿保存故障注入'})});}
    if(request.method()==='POST'&&url.pathname==='/api/session/new'){
      const body=request.postDataJSON();
      if(body&&body.zhinang_role_id){
        newBodies.push(body);
        if(newBodies.length===1){
          const response=await route.fetch();const payload=await response.json();acceptedSid=payload.session&&payload.session.session_id||'';
          return route.fulfill({status:504,contentType:'application/json',body:JSON.stringify({error:'受理响应丢失故障注入'})});
        }
      }
    }
    return route.continue();
  });
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>S._bootReady===true,{timeout:30000});
  if(!(await page.evaluate(()=>!!(S.session&&S.session.session_id)))){await page.locator('.taiji-new-chat').click();await page.waitForFunction(()=>!!(S.session&&S.session.session_id));}
  const originalSid=await page.evaluate(()=>S.session.session_id);
  await page.locator('#fileInput').setInputFiles(attachment);await page.locator('#msg').fill('必须保留的原始草稿');
  await page.evaluate(()=>{window.__zhinangOriginalFile=S.pendingFiles[0];});
  await clickRealZhinangNav(page,1280);await waitCatalog(page);await page.locator('.zhinang-card [data-zhinang-open]').first().click();await page.locator('.zhinang-primary').click();
  for(let i=0;i<100&&draftFailures===0;i++)await delay(50);
  check(draftFailures>0,'draft transition exercises real save failure');
  const draftFailureState=await page.evaluate(sid=>({sid:S.session&&S.session.session_id,expectedSid:sid,text:document.querySelector('#msg').value,pendingFiles:S.pendingFiles.map(file=>({name:file.name,size:file.size,lastModified:file.lastModified,isFile:file instanceof File})),sameFile:S.pendingFiles[0]===window.__zhinangOriginalFile,detailOpen:!document.querySelector('#zhinangDetail').hidden}),originalSid);console.error(JSON.stringify({draftFailureState}));
  check(draftFailureState.sid===draftFailureState.expectedSid&&draftFailureState.text==='必须保留的原始草稿'&&draftFailureState.pendingFiles.length===1&&draftFailureState.sameFile,'draft save failure preserves original session text and native File identity');
  check(newBodies.length===0,'draft save failure prevents role session creation');
  failDraft=false;
  const create=page.locator('.zhinang-primary');const box=await create.boundingBox();check(!!box,'role create button remains available after draft failure');
  await page.mouse.dblclick(box.x+box.width/2,box.y+box.height/2,{delay:12});
  for(let i=0;i<200&&newBodies.length<1;i++)await delay(50);
  check(newBodies.length===1,'double click creates one logical backend request while pending');
  for(let i=0;i<200&&!(await page.locator('.zhinang-primary').isEnabled().catch(()=>false));i++)await delay(50);
  check(await page.evaluate(sid=>S.session&&S.session.session_id===sid&&document.querySelector('#msg').value==='必须保留的原始草稿'&&S.pendingFiles[0]===window.__zhinangOriginalFile,originalSid),'lost accept response preserves old draft and File for retry');
  await page.locator('.zhinang-primary').click();
  await page.waitForFunction(sid=>!!sid&&S.session&&S.session.session_id===sid,acceptedSid);
  check(newBodies.length===2,'retry sends one follow-up create request');
  check(newBodies[0].request_id===newBodies[1].request_id,'lost accept retry reuses stable request_id');
  check(await page.locator('#msg').inputValue()===''&&await page.evaluate(()=>S.pendingFiles.length===0),'successful plain role task does not inherit old draft or File');
  await context.close();
}

async function favoriteLifecyclePass(browser,base,restartWebui,state){
  const context=await browser.newContext({viewport:{width:1280,height:800},locale:'zh-CN'});const one=await context.newPage(),two=await context.newPage();attachErrors(one,'favorite-window-one');attachErrors(two,'favorite-window-two');
  for(const page of [one,two]){await page.route('**/*',route=>{const url=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(url.hostname)?route.continue():route.abort('blockedbyclient');});await page.goto(base,{waitUntil:'domcontentloaded'});await clickRealZhinangNav(page,1280);await waitCatalog(page);}
  const roleId=await one.locator('.zhinang-card').first().getAttribute('data-zhinang-role'),favOne=one.locator(`[data-zhinang-role="${roleId}"] .zhinang-favorite`);
  if(await favOne.getAttribute('aria-pressed')!=='true'){await favOne.click();await waitAttribute(favOne,'aria-pressed','true');}
  await two.reload({waitUntil:'domcontentloaded'});await clickRealZhinangNav(two,1280);await waitCatalog(two);check(await two.locator(`[data-zhinang-role="${roleId}"] .zhinang-favorite`).getAttribute('aria-pressed')==='true','second window observes authoritative favorite after refresh');
  const createdProfile=await one.evaluate(async()=>{const response=await fetch('/api/profile/create',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:'research',clone_config:true})});return {ok:response.ok,status:response.status,body:await response.json()};});check(createdProfile.ok||createdProfile.status===409,'multi-profile lifecycle fixture creates named profile through real API');
  const research=await browser.newContext({viewport:{width:1280,height:800},locale:'zh-CN'});await research.addCookies([{name:'hermes_profile',value:'research',domain:'127.0.0.1',path:'/'}]);const other=await research.newPage();attachErrors(other,'favorite-research-profile');
  await other.route('**/*',route=>{const url=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(url.hostname)?route.continue():route.abort('blockedbyclient');});await other.goto(base,{waitUntil:'domcontentloaded'});await clickRealZhinangNav(other,1280);await waitCatalog(other);
  const researchDiagnostic=await other.evaluate(async()=>({cookie:document.cookie,activeProfile:S.activeProfile,active:await (await fetch('/api/profile/active')).json(),favorite:document.querySelector(`[data-zhinang-role="${document.querySelector('.zhinang-card')?.dataset.zhinangRole}"] .zhinang-favorite`)?.getAttribute('aria-pressed')}));console.error(JSON.stringify({researchDiagnostic}));
  check(await other.locator(`[data-zhinang-role="${roleId}"] .zhinang-favorite`).getAttribute('aria-pressed')==='false','named profile does not inherit default favorite');
  await one.locator(`[data-zhinang-role="${roleId}"] [data-zhinang-open]`).click();await one.locator('[data-zhinang-create]').click();await one.locator('#mainChat').waitFor({state:'visible'});const roleSessionId=await one.evaluate(()=>S.session&&S.session.session_id);check(!!roleSessionId,'lifecycle scope creates a persistent role task before restart');
  const roleBefore=await one.evaluate(async sid=>{const response=await fetch(`/api/zhinang/session-role?session_id=${encodeURIComponent(sid)}`);return {status:response.status,body:await response.json()};},roleSessionId);check(roleBefore.status===200&&roleBefore.body?.role?.role_id===roleId,'role task exposes its persisted role snapshot before restart');
  await one.locator('#msg').fill(`重启前角色请求 ${lifecycleBeforeMarker}`);await one.locator('#btnSend').click();await one.waitForFunction(()=>!S.busy&&!S.activeStreamId,{timeout:30000});const beforeRequest=evidence.requests.find(request=>request.lifecyclePhase==='before-restart');check(!!beforeRequest?.hasRole&&!!beforeRequest.systemRoleSha256,'pre-restart turn reaches Provider with the selected role system context');
  const snapshotFile=path.join(state,'sessions',`${roleSessionId}.json`);const snapshotBefore=JSON.parse(fs.readFileSync(snapshotFile,'utf8')).zhinang_role_snapshot;check(!!snapshotBefore?.identity?.effective_prompt_sha256&&!!snapshotBefore?.private?.effective_prompt,'persisted role task stores the complete role snapshot and effective prompt digest');
  await restartWebui();
  await one.reload({waitUntil:'domcontentloaded'});await one.waitForFunction(expected=>S._bootReady===true&&S.session&&S.session.session_id===expected,roleSessionId,{timeout:30000});check(await one.locator('#zhinangSessionRole').isVisible(),'persistent role task and visible role badge survive real WebUI restart');const roleAfter=await one.evaluate(async sid=>{const response=await fetch(`/api/zhinang/session-role?session_id=${encodeURIComponent(sid)}`);return {status:response.status,body:await response.json()};},roleSessionId);check(roleAfter.status===200&&JSON.stringify(roleAfter.body.role)===JSON.stringify(roleBefore.body.role),'historical role identity and public summary remain byte-equivalent after restart');
  await one.locator('#msg').fill(`重启后角色请求 ${lifecycleAfterMarker}`);await one.locator('#btnSend').click();await one.waitForFunction(()=>!S.busy&&!S.activeStreamId,{timeout:30000});const afterRequest=evidence.requests.find(request=>request.lifecyclePhase==='after-restart');check(!!afterRequest?.hasRole&&afterRequest.systemRoleSha256===beforeRequest.systemRoleSha256,'post-restart turn reaches Provider with the same role system context');
  const snapshotAfter=JSON.parse(fs.readFileSync(snapshotFile,'utf8')).zhinang_role_snapshot;const snapshotSha256=value=>crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');evidence.lifecycle={sessionId:roleSessionId,roleId,completeSnapshotSha256Before:snapshotSha256(snapshotBefore),completeSnapshotSha256After:snapshotSha256(snapshotAfter),effectivePromptSha256Before:snapshotBefore.identity.effective_prompt_sha256,effectivePromptSha256After:snapshotAfter.identity.effective_prompt_sha256,providerRoleSystemSha256Before:beforeRequest.systemRoleSha256,providerRoleSystemSha256After:afterRequest.systemRoleSha256};check(evidence.lifecycle.completeSnapshotSha256After===evidence.lifecycle.completeSnapshotSha256Before&&evidence.lifecycle.effectivePromptSha256After===evidence.lifecycle.effectivePromptSha256Before,'complete persisted role snapshot and effective prompt digest remain unchanged after restart');
  await clickRealZhinangNav(one,1280);await waitCatalog(one);for(const page of [two,other]){await page.reload({waitUntil:'domcontentloaded'});await clickRealZhinangNav(page,1280);await waitCatalog(page);}await delay(100);expectedWebuiRestart=false;
  check(await one.locator(`[data-zhinang-role="${roleId}"] .zhinang-favorite`).getAttribute('aria-pressed')==='true','default favorite survives real WebUI restart');
  check(await two.locator(`[data-zhinang-role="${roleId}"] .zhinang-favorite`).getAttribute('aria-pressed')==='true','two default windows stay consistent after restart');
  check(await other.locator(`[data-zhinang-role="${roleId}"] .zhinang-favorite`).getAttribute('aria-pressed')==='false','profile-separated favorite remains separate after restart');
  await context.close();await research.close();
}

async function cancellationAndRecoveryPass(browser,base){
  const context=await browser.newContext({viewport:{width:1280,height:800},locale:'zh-CN'});const page=await context.newPage();attachErrors(page,'cancel-recovery');
  await page.route('**/*',route=>{const url=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(url.hostname)?route.continue():route.abort('blockedbyclient');});await page.goto(base,{waitUntil:'domcontentloaded'});
  await clickRealZhinangNav(page,1280);await waitCatalog(page);await page.locator('.zhinang-card [data-zhinang-open]').first().click();await page.locator('.zhinang-primary').click();await page.locator('#mainChat').waitFor({state:'visible'});
  await page.locator('#msg').fill(`启动可取消任务 ${cancelMarker}`);await page.locator('#btnSend').click();await page.waitForFunction(()=>S.busy&&S.activeStreamId&&getComposerPrimaryAction()==='stop',{timeout:30000});
  for(let i=0;i<300&&!evidence.requests.some(request=>request.hasCancelMarker&&request.hasRole&&request.firstTokenSent);i++)await delay(100);
  check(evidence.requests.some(request=>request.hasCancelMarker&&request.hasRole&&request.firstTokenSent),'real role task reached cancellable loopback provider and provider wrote first token');
  const cancelledStreamId=await page.evaluate(()=>S.activeStreamId);const cancelRequest=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/chat/cancel');await page.locator('#btnSend').click();await cancelRequest;await page.waitForFunction(()=>!S.busy&&!S.activeStreamId,{timeout:15000});
  await page.waitForFunction(async streamId=>{const response=await fetch(`/api/chat/stream/status?stream_id=${encodeURIComponent(streamId)}`);const body=await response.json();return body.active===false&&body.journal&&body.journal.terminal===true;},cancelledStreamId,{timeout:15000});
  for(let i=0;i<150&&!evidence.requests.some(request=>request.hasCancelMarker&&request.responseClosed);i++)await delay(100);
  check(evidence.requests.some(request=>request.hasCancelMarker&&request.responseClosed),'cancelled Provider response socket closed before recovery');
  check(await page.evaluate(value=>S.messages.filter(message=>message&&message.role==='user'&&String(message.content||'').includes(value)).length===1,cancelMarker),'cancelled turn keeps one user message');
  check(await page.locator('.workspace-artifact-item').count()===0,'cancelled turn does not fabricate an artifact card');
  await page.locator('#msg').fill(`取消后恢复 ${recoveryMarker}`);await page.locator('#btnSend').click();
  for(let i=0;i<300&&!evidence.requests.some(request=>request.hasRecoveryMarker);i++)await delay(100);
  check(evidence.requests.some(request=>request.hasRecoveryMarker),'post-cancel recovery reached loopback Provider');
  await page.getByText(`恢复成功 ${recoveryMarker}`,{exact:false}).waitFor({timeout:30000});
  check(await page.evaluate(value=>S.messages.filter(message=>message&&message.role==='user'&&String(message.content||'').includes(value)).length===1,recoveryMarker),'post-cancel recovery keeps one user message');
  await page.locator('#msg').fill(`受控模型失败 ${modelFailureMarker}`);await page.locator('#btnSend').click();await page.waitForFunction(()=>!S.busy,{timeout:45000});
  check(evidence.requests.some(request=>request.hasModelFailureMarker),'real Agent observed controlled Provider model failure');
  check(await page.evaluate(value=>S.messages.filter(message=>message&&message.role==='user'&&String(message.content||'').includes(value)).length===1,modelFailureMarker),'model failure keeps one user message');
  check(await page.locator('.workspace-artifact-item').count()===0,'model failure does not fabricate an artifact card');
  const priorRecoveryCount=await page.getByText(`恢复成功 ${recoveryMarker}`,{exact:false}).count();await page.locator('#msg').fill(`模型失败后恢复 ${recoveryMarker}`);await page.locator('#btnSend').click();await page.waitForFunction(({text,count})=>[...document.querySelectorAll('*')].filter(node=>node.children.length===0&&node.textContent.includes(text)).length>count,{text:`恢复成功 ${recoveryMarker}`,count:priorRecoveryCount},{timeout:30000});
  check(await page.evaluate(()=>!S.busy&&!S.activeStreamId),'role chat recovers after cancel and model failure');
  await context.close();
}

async function catalogPerformancePass(browser,base){
  const apiDurations=[];
  for(let index=0;index<30;index++){
    const term=`性能角色 ${String(index).padStart(3,'0')}`,started=performance.now();
    const response=await fetch(`${base}/api/zhinang/catalog?scope=all&category=all&view=all&query=${encodeURIComponent(term)}&page=1`);
    check(response.ok,'500-row fixture real catalog HTTP query returns 200');
    const payload=await response.json();
    check(payload.total===1&&payload.items.length===1,'500-row fixture real catalog HTTP query returns matching page');
    apiDurations.push(performance.now()-started);
  }
  const context=await browser.newContext({viewport:{width:1280,height:800},locale:'zh-CN'});const page=await context.newPage();attachErrors(page,'catalog-performance');
  await page.route('**/*',route=>{const url=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(url.hostname)?route.continue():route.abort('blockedbyclient');});
  await page.goto(base,{waitUntil:'domcontentloaded'});await clickRealZhinangNav(page,1280);await waitCatalog(page);const allResponse=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/api/zhinang/catalog'&&url.searchParams.get('view')==='all';});await page.locator('[data-zhinang-view="all"]').click();await allResponse;await page.waitForFunction(()=>document.querySelectorAll('.zhinang-card').length===24);
  check(await page.locator('.zhinang-card').count()===24,'500-row real HTTP catalog renders one 24-role page');
  const afterDebounce=[];for(let index=0;index<30;index++){const term=`性能角色 ${String(index).padStart(3,'0')}`,started=performance.now(),responsePromise=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/api/zhinang/catalog'&&url.searchParams.get('query')===term;});await page.locator('#zhinangSearch').fill(term);await responsePromise;await page.locator('.zhinang-card').first().waitFor();afterDebounce.push(Math.max(0,performance.now()-started-200));}
  const longText=await page.locator('.zhinang-card').first().innerText();check(longText.includes('安全长内容'),'500-row long-content fixture stays rendered and reachable');
  afterDebounce.sort((a,b)=>a-b);apiDurations.sort((a,b)=>a-b);const p95=values=>values[Math.ceil(values.length*.95)-1];evidence.metrics.fixture500Rows=500;evidence.metrics.fixture500PageSize=24;evidence.metrics.p95Algorithm='sort ascending; sample[Math.ceil(sample.length * 0.95) - 1]';evidence.metrics.fixture500BrowserPostDebounceSamplesMs=afterDebounce;evidence.metrics.fixture500HttpSamplesMs=apiDurations;evidence.metrics.fixture500BrowserPostDebounceP95Ms=p95(afterDebounce);evidence.metrics.fixture500HttpP95Ms=p95(apiDurations);check(p95(afterDebounce)<=500,'500-row fixture 30-query browser p95 after debounce <=500ms');check(p95(apiDurations)<=500,'500-row fixture 30-query real catalog HTTP p95 <=500ms');
  await context.close();
}

async function removedRolePass(browser,base){
  const context=await browser.newContext({viewport:{width:1280,height:800},locale:'zh-CN'});const page=await context.newPage();attachErrors(page,'removed-role');
  const roleId='agency:retired/browser-fixture';let favorite=true;
  const item={role_id:roleId,name:'已下架验收智囊',original_name:'Retired Browser Fixture',summary:'保留的收藏摘要',category:'文档与研究',tags:['历史','下架'],available:false,favorite:true,historical:true,last_accepted_at:1788600000,continue_session_id:'retired-visible-tip'};
  const catalog=(query)=>({catalog_version:'removed-ui-fixture-v1',items:favorite?[{...item,favorite}]:[],total:favorite?1:0,page:1,pages:1,page_size:24,filters:{scope:query.get('scope')||'all',category:query.get('category')||'all',view:query.get('view')||'featured',query:query.get('query')||''},categories:favorite?[{category:'文档与研究',count:1}]:[]});
  await page.route('**/*',async route=>{const request=route.request(),url=new URL(request.url());if(!['127.0.0.1','localhost'].includes(url.hostname))return route.abort('blockedbyclient');if(url.pathname==='/api/zhinang/catalog')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog(url.searchParams))});if(url.pathname===`/api/zhinang/roles/${encodeURIComponent(roleId)}`)return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({role:{...item,capabilities:[],limitations:'该角色已从当前目录移除，仅保留安全历史摘要。',starter_examples:[],deliverable_examples:[],unavailable_reason:'当前版本未提供此角色。'}})});if(url.pathname===`/api/zhinang/favorites/${encodeURIComponent(roleId)}`&&request.method()==='PUT'){favorite=false;return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({ok:true,role_id:roleId,favorite:false})});}return route.continue();});
  await page.goto(base,{waitUntil:'domcontentloaded'});await clickRealZhinangNav(page,1280);await waitCatalog(page);const favoritesResponse=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/api/zhinang/catalog'&&url.searchParams.get('scope')==='favorites';});await page.locator('[data-zhinang-scope="favorites"]').click();await favoritesResponse;const card=page.locator(`[data-zhinang-role="${roleId}"]`);await card.waitFor({state:'visible'});check(await card.locator('.zhinang-unavailable').innerText()==='当前版本不可用','removed favorite stays visible with unavailable status');check(await card.locator('.zhinang-favorite').getAttribute('aria-pressed')==='true','removed favorite keeps authoritative cancel control');check(await card.locator('.zhinang-continue').isVisible(),'removed recent favorite exposes its verified historical task entry');await card.locator('[data-zhinang-open]').click();const detail=page.locator('#zhinangDetail');await detail.getByText('当前版本未提供此角色。',{exact:true}).waitFor();check(await detail.locator('[data-zhinang-create]').count()===0,'removed role detail cannot create a new task');check(await detail.getByText('继续最近任务',{exact:true}).count()===0,'removed catalog detail does not trust synthetic continue action');const put=page.waitForResponse(response=>response.request().method()==='PUT'&&new URL(response.url()).pathname.includes('/api/zhinang/favorites/'));await detail.locator('[data-zhinang-favorite]').click();await put;await page.getByText('还没有收藏的智囊。',{exact:true}).waitFor();check(await card.count()===0,'canceling a removed favorite removes the stale card');
  await context.close();
}

async function productRegressionPass(browser,base){
  const context=await browser.newContext({viewport:{width:1280,height:800},locale:'zh-CN'});const page=await context.newPage();attachErrors(page,'product-regression');
  await page.route('**/*',route=>{const url=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(url.hostname)?route.continue():route.abort('blockedbyclient');});await page.goto(base,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>S._bootReady===true,{timeout:30000});
  if(!(await page.evaluate(()=>!!(S.session&&S.session.session_id)))){await page.locator('.taiji-new-chat').click();await page.waitForFunction(()=>!!(S.session&&S.session.session_id));}
  const ordinaryMarker=`普通聊天回归 ${Date.now()}`;await page.locator('#msg').fill(ordinaryMarker);await page.locator('#btnSend').click();await page.getByText('已收到',{exact:true}).waitFor({timeout:30000});await page.waitForFunction(()=>!S.busy&&!S.activeStreamId,{timeout:30000});check(evidence.requests.some(request=>!request.hasRole&&request.messageRoles.includes('user')),'ordinary chat still reaches loopback Provider without a fixed role prompt');
  await page.locator('.taiji-brand-nav [data-taiji-panel="settings"]').click();await page.locator('[data-settings-section="models"]').click();await page.getByText('模型配置',{exact:true}).last().waitFor({state:'visible'});check(await page.locator('#modelConfigHero').isVisible(),'model configuration entry remains available');
  await page.locator('.taiji-brand-nav [data-taiji-panel="chat"]').click();await page.locator('#msg').fill('/personality');await page.locator('#cmdDropdown').waitFor({state:'visible'});const personality=page.locator('#cmdDropdown [data-command="personality"],#cmdDropdown .cmd-item').filter({hasText:'personality'}).first();check(await personality.count()===1&&await personality.getAttribute('aria-disabled')!=='true','ordinary chat keeps personality command available');await page.keyboard.press('Escape');
  await page.locator('.taiji-brand-nav [data-taiji-panel="writing"]').click();await page.locator('#mainWriting').waitFor({state:'visible'});const teamCard=page.locator('#expertTeamV3PortalRoot [data-et3-action="open-team"][data-team-id="content-creator-team"]:not([disabled])');await teamCard.waitFor({state:'visible'});check(true,'expert team entry remains discoverable from visible navigation');await teamCard.click();const teamPrompt=page.locator('#expertTeamV3Prompt');await teamPrompt.waitFor({state:'visible'});const expertMarker=`真实专家团启动 ${Date.now()}`;await teamPrompt.fill(expertMarker);const summon=page.locator('#expertTeamV3PortalRoot [data-et3-dialog-backdrop]:not([hidden]) [data-et3-action="summon"]');await summon.waitFor({state:'visible'});check(await summon.isEnabled(),'expert team launch control is enabled for a verified content profile');const launchResponsePromise=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/expert-teams/launch'&&response.request().method()==='POST',{timeout:30000});await summon.click();const launchResponse=await launchResponsePromise;check(launchResponse.status()===200,'expert team launches through real /api/expert-teams/launch handler');const launchPayload=await launchResponse.json();const runId=launchPayload.run&&launchPayload.run.run_id,sid=launchPayload.session&&launchPayload.session.session_id;check(!!(runId&&sid),'expert team launch returns bound persisted Run and Session');check(Array.isArray(launchPayload.session_messages)&&launchPayload.session_messages.length===2&&launchPayload.session_messages.every(message=>message.expert_team_run_id===runId),'expert team launch response carries one bound user/lifecycle message pair');await page.waitForFunction(expected=>S.session&&S.session.session_id===expected,sid,{timeout:30000});const workbench=page.locator(`#expertTeamV3Workbench[data-expert-team-run-id="${runId}"]`);await workbench.waitFor({state:'visible',timeout:30000});check((await workbench.innerText()).includes('需求'),'expert team launch synchronizes the persisted Run into the visible workbench');await page.reload({waitUntil:'domcontentloaded'});await page.waitForFunction(expected=>S._bootReady===true&&S.session&&S.session.session_id===expected,sid,{timeout:30000});await page.locator(`#expertTeamV3Workbench[data-expert-team-run-id="${runId}"]`).waitFor({state:'visible',timeout:30000});check(true,'expert team Session and Run binding survive a real page reload');
  await context.close();
}

async function zoomPass(browser,base){
  const context=await browser.newContext({viewport:{width:1280,height:800},locale:'zh-CN'});const page=await context.newPage();attachErrors(page,'zoom-200');
  await page.goto(base,{waitUntil:'domcontentloaded'});await clickRealZhinangNav(page,1280);await waitCatalog(page);await page.evaluate(()=>{document.documentElement.style.zoom='2';});await delay(300);
  check(await page.locator('[data-zhinang-view="featured"]').isVisible(),'200% zoom keeps view controls visible');
  check(await page.locator('.zhinang-card').first().isVisible(),'200% zoom keeps catalog reachable');
  await context.close();
}

async function main(){
  const scope=process.env.ZHINANG_E2E_SCOPE||'viewports';
  const allowedScopes=new Set(['viewports','flow','faults','selection-focus','draft-idempotency','lifecycle','recovery','performance','removed','regression','serve']);
  if(!allowedScopes.has(scope))throw new Error(`unsupported ZHINANG_E2E_SCOPE: ${scope}`);
  fs.mkdirSync(OUT,{recursive:true});
  const root=fs.mkdtempSync('/private/tmp/taiji-zhinang-stage4-e2e-');
  const runtime=path.join(root,'runtime'),state=path.join(root,'state'),workspace=path.join(root,'workspace');for(const dir of [runtime,state,workspace])fs.mkdirSync(dir,{recursive:true});
  artifactTarget=path.join(workspace,'成果.md');
  failureTarget=path.join(root,'outside','禁止成果.md');
  const attachment=path.join(root,'材料.txt');fs.writeFileSync(attachment,`真实附件随机标记：${marker}\n`);
  const providerPort=await freePort(),webuiPort=await freePort();const provider=await startProvider(providerPort);
  const config=`model:\n  provider: custom\n  default: taiji-zhinang-qa\n  base_url: http://127.0.0.1:${providerPort}/v1\n  api_key: TEST_ONLY_ZHINANG_LOOPBACK_KEY\ncustom_providers:\n  - name: custom\n    model: taiji-zhinang-qa\n    models:\n      - taiji-zhinang-qa\n    base_url: http://127.0.0.1:${providerPort}/v1\n    api_key: TEST_ONLY_ZHINANG_LOOPBACK_KEY\nfallback_providers: []\nfallback_model: []\nplatform_toolsets:\n  cli:\n    - file\nmcp_servers: {}\nterminal:\n  backend: local\n  cwd: ${JSON.stringify(workspace)}\n`;
  fs.writeFileSync(path.join(runtime,'config.yaml'),config);
  evidence.configBeforeSha256=sha(path.join(runtime,'config.yaml'));
  const env={...process.env};for(const key of Object.keys(env)){if(key.endsWith('_API_KEY')||['ANTHROPIC_AUTH_TOKEN','GH_TOKEN','GITHUB_TOKEN','GOOGLE_APPLICATION_CREDENTIALS','OPENAI_BASE_URL'].includes(key))delete env[key];}
  Object.assign(env,{HERMES_WEBUI_PORT:String(webuiPort),HERMES_WEBUI_HOST:'127.0.0.1',HERMES_WEBUI_STATE_DIR:state,HERMES_HOME:runtime,HERMES_BASE_HOME:runtime,TAIJI_RUNTIME_HOME:runtime,HERMES_CONFIG_PATH:path.join(runtime,'config.yaml'),HERMES_WEBUI_DEFAULT_WORKSPACE:workspace,HERMES_WEBUI_SKIP_ONBOARDING:'1',HERMES_WEBUI_AGENT_DIR:AGENT,HERMES_WEBUI_PYTHON:PYTHON,HERMES_WRITE_SAFE_ROOT:workspace,TERMINAL_ENV:'local',TERMINAL_CWD:workspace,HERMES_WEBUI_TEST_NETWORK_BLOCK:'1',TAIJI_WEBUI_TEST_NETWORK_BLOCK:'1',AWS_EC2_METADATA_DISABLED:'true'});
  let serverEntry=path.join(WEBUI,'server.py');
  if(scope==='lifecycle'){
    serverEntry=path.join(root,'multi-profile-server.py');
    fs.writeFileSync(serverEntry,`import os, sys\nsys.path.insert(0, ${JSON.stringify(WEBUI)})\nfrom api import profiles, helpers\nprofiles.taiji_single_runtime_mode = lambda: False\n_original_get_profile_cookie = helpers.get_profile_cookie\ndef _fixture_get_profile_cookie(handler):\n    value = os.environ.pop('TAIJI_RUNTIME_HOME', None)\n    try:\n        return _original_get_profile_cookie(handler)\n    finally:\n        if value is not None:\n            os.environ['TAIJI_RUNTIME_HOME'] = value\nhelpers.get_profile_cookie = _fixture_get_profile_cookie\nimport runpy\nrunpy.run_path(${JSON.stringify(path.join(WEBUI,'server.py'))}, run_name='__main__')\n`);
  }
  if(scope==='performance'){
    serverEntry=path.join(root,'catalog-500-server.py');
    fs.writeFileSync(serverEntry,`import copy, runpy, sys\nsys.path.insert(0, ${JSON.stringify(WEBUI)})\nfrom api import zhinang\n_base = zhinang.load_current_catalog_rows()[0]\n_rows = []\nfor index in range(500):\n    row = copy.deepcopy(_base)\n    row['role_id'] = f'fixture:role-{index:03d}'\n    row['name'] = f'性能角色 {index:03d}'\n    row['original_name'] = f'Fixture Role {index:03d}'\n    row['summary'] = '真实 HTTP 五百条长内容目录性能测试 ' + ('安全长内容 ' * 40)\n    row['tags'] = ['fixture', f'编号-{index:03d}']\n    row['catalog_order'] = index\n    row['featured_order'] = index + 1 if index < 6 else None\n    _rows.append(row)\nzhinang.load_current_catalog_rows = lambda: copy.deepcopy(_rows)\nrunpy.run_path(${JSON.stringify(path.join(WEBUI,'server.py'))}, run_name='__main__')\n`);
  }
  const serverArgs=[serverEntry];const webLog=path.join(root,'webui.log');const fd=fs.openSync(webLog,'w');let proc=spawn(PYTHON,serverArgs,{cwd:WEBUI,env,stdio:['ignore',fd,fd]});const base=`http://127.0.0.1:${webuiPort}`;
  const restartWebui=async()=>{
    expectedWebuiRestart=true;
    const previous=proc;const exited=new Promise(resolve=>previous.once('exit',resolve));previous.kill('SIGTERM');await Promise.race([exited,delay(5000)]);if(previous.exitCode===null){previous.kill('SIGKILL');await Promise.race([exited,delay(2000)]);}
    proc=spawn(PYTHON,serverArgs,{cwd:WEBUI,env,stdio:['ignore',fd,fd]});await waitHealth(base,proc);evidence.checks.push('WebUI process restarted with same isolated state');
  };
  try{
    await waitHealth(base,proc);console.error(JSON.stringify({status:'READY',base,root,webLog}));
    if(scope==='serve'){
      const reviewInfo={status:'REVIEW_READY',base,root,webLog,workspace,state,runtime};
      fs.writeFileSync('/private/tmp/taiji-zhinang-stage4-review.json',JSON.stringify(reviewInfo,null,2));
      console.log(JSON.stringify(reviewInfo));
      await new Promise(resolve=>{process.once('SIGINT',resolve);process.once('SIGTERM',resolve);});
      return;
    }
    const browser=await chromium.launch({headless:true,executablePath:CHROMIUM,args:['--no-sandbox','--disable-dev-shm-usage']});
    try{
      evidence.runtime={node:process.version,python:execFileSync(PYTHON,['--version'],{encoding:'utf8'}).trim(),chromium:browser.version(),chromiumExecutable:CHROMIUM,pythonExecutable:PYTHON,playwrightModule:process.env.PLAYWRIGHT_NODE_PATH,fixtureInjection:scope==='performance'?'generated catalog-500-server.py replaces api.zhinang.load_current_catalog_rows before executing production server.py':''};
      if(scope==='viewports'){
        for(const [w,h]of primaryViewports)await viewportPass(browser,base,w,h,false);
        for(const [w,h]of boundaryViewports)await viewportPass(browser,base,w,h,true);
        await zoomPass(browser,base);
      }
      if(scope==='flow')await realFlow(browser,base,workspace,attachment);
      if(scope==='faults')await faultAndKeyboardPass(browser,base);
      if(scope==='selection-focus')await selectionAndFavoriteFocusPass(browser,base);
      if(scope==='draft-idempotency')await draftAndIdempotencyPass(browser,base,attachment);
      if(scope==='lifecycle')await favoriteLifecyclePass(browser,base,restartWebui,state);
      if(scope==='recovery')await cancellationAndRecoveryPass(browser,base);
      if(scope==='performance')await catalogPerformancePass(browser,base);
      if(scope==='removed')await removedRolePass(browser,base);
      if(scope==='regression')await productRegressionPass(browser,base);
    }finally{await browser.close();}
    check(evidence.consoleErrors.length===0,`no browser console errors (${evidence.consoleErrors.join('; ')})`);check(evidence.pageErrors.length===0,`no page errors (${evidence.pageErrors.join('; ')})`);check(evidence.externalRequests.length===0,'browser attempted zero external HTTP requests while route-level network block was active');
    if(scope==='flow'){
      check(evidence.requests.some(r=>r.hasMarker&&r.hasRole&&r.tools.includes('write_file')),'provider observed attachment marker, role prompt, and write_file tool');
      check(evidence.requests.some(r=>r.hasToolResult),'provider observed real tool result turn');
    }
    evidence.root=root;evidence.base=base;evidence.webLog=webLog;evidence.configSha256=sha(path.join(runtime,'config.yaml'));
    if(scope==='flow')evidence.artifactSha256=sha(path.join(workspace,'成果.md'));
    check(sha(path.join(runtime,'config.yaml'))===evidence.configBeforeSha256,'isolated runtime config bytes remained unchanged');evidence.scope=scope;const report=path.join(OUT,`e2e-evidence-${scope}.json`);fs.writeFileSync(report,JSON.stringify(evidence,null,2));console.log(JSON.stringify({status:'PASS',scope,root,base,report,checks:evidence.checks.length,requests:evidence.requests.length}));
  }catch(error){console.error(error.stack||error);console.error(`root=${root}\nwebLog=${webLog}`);process.exitCode=1;}finally{if(proc&&proc.exitCode===null)proc.kill('SIGTERM');provider.close();fs.closeSync(fd);}
}
main();
