"""Coverage for the Settings model configuration panel."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


_CUSTOM_VISION_PROVIDER_DRIVER = r"""
const fs=require('fs');
const source=fs.readFileSync(process.argv[2],'utf8');
function extractFunc(name){
 const re=new RegExp('(?:async\\s+)?function\\s+'+name+'\\s*\\(');
 const start=source.search(re);if(start<0) throw new Error(name+' not found');
 let i=source.indexOf('{',start),depth=1;i++;
 while(depth>0&&i<source.length){if(source[i]==='{')depth++;else if(source[i]==='}')depth--;i++;}
 return source.slice(start,i);
}
function element(id,value=''){
 return {id,value,disabled:false,hidden:false,dataset:{},textContent:'',isConnected:true,focused:false,attrs:{},
  setAttribute(k,v){this.attrs[k]=v;},focus(){this.focused=true;},querySelectorAll(){return [];}};
}
const ids=['customVisionProviderPanel','customVisionProviderId','customVisionProviderName','customVisionProviderTransport',
 'customVisionProviderBaseUrl','customVisionProviderModels','customVisionProviderDefaultModel','customVisionProviderApiKey',
 'customVisionProviderError','btnSaveCustomVisionProvider','btnManageCustomVisionProviders','customVisionProviderList'];
const elements={};for(const id of ids) elements[id]=element(id);
elements.customVisionProviderPanel.querySelectorAll=()=>ids.slice(1,9).map(id=>elements[id]);
elements.customVisionProviderList.querySelectorAll=()=>[];
elements.customVisionProviderName.value='Relay Vision';
elements.customVisionProviderBaseUrl.value='https://relay.example.com/v1';
elements.customVisionProviderModels.value='relay-vl';
elements.customVisionProviderTransport.value='openai_chat_completions';
const $=id=>elements[id]||null;
const document={activeElement:element('returnButton'),createElement:tag=>element(tag)};
const _settingsSection='providers';
const switchSettingsSection=()=>{};
const toggleModelConfigSection=(id,open)=>{elements[id].hidden=!open;};
const _setFieldError=(id,message)=>{elements[id].textContent=message;};
const _splitCustomImageProviderModels=value=>String(value||'').split(',').map(v=>v.trim()).filter(Boolean);
const _renderCustomVisionProviderList=()=>{};
const _renderModelConfigPanel=()=>{};
const _renderVisionConfigSummary=()=>{};
const _invalidateVisionTest=()=>{};
const showToast=()=>{};
let confirmResult=true;
const showConfirmDialog=async()=>confirmResult;
let apiCalls=[];
const api=async(url,options)=>{apiCalls.push({url,options});return {providers:[{id:'custom:relay',name:'Relay Vision',custom:true,active:false}]};};
let _customVisionProviderBusy=false;
let _customVisionProviderGeneration=0;
let _customVisionProviderReturnFocus=null;
let _modelConfigData={vision:{provider:'alibaba',model:'qwen3-vl-plus'},vision_providers:[{id:'alibaba'},
 {id:'custom:relay',name:'Relay Vision',custom:true,base_url:'https://relay.example.com/v1',models:[{id:'relay-vl'}],default_model:'relay-vl',transport:'openai_chat_completions'},
 {id:'custom:other',name:'Other Vision',custom:true,base_url:'https://other.example.com/v1',models:[{id:'other-vl'}],default_model:'other-vl',transport:'openai_chat_completions'}]};
for(const name of ['_modelConfigCustomVisionRows','_customVisionProviderDraftId','_resetCustomVisionProviderForm',
 '_customVisionProviderPayload','_customVisionProviderDraftIdentity','_setCustomVisionProviderBusy',
 'openCustomVisionProviderEditor','closeCustomVisionProviderEditor','saveCustomVisionProviderConfig',
 'deleteCustomVisionProviderConfig']) eval(extractFunc(name));

async function run(scenario){
 if(scenario==='save'){
  await saveCustomVisionProviderConfig();
  return {apiCalls,busy:_customVisionProviderBusy,hidden:elements.customVisionProviderPanel.hidden,
   secret:elements.customVisionProviderApiKey.value,focused:elements.btnManageCustomVisionProviders.focused};
 }
 if(scenario==='active-delete'){
  _modelConfigData.vision_providers=[{id:'custom:relay',name:'Relay',custom:true,active:true}];
  await deleteCustomVisionProviderConfig('custom:relay',document.activeElement);
  return {apiCalls,error:elements.customVisionProviderError.textContent};
 }
 if(scenario==='switch-cancel'||scenario==='switch-confirm'||scenario==='switch-busy'){
  await openCustomVisionProviderEditor('relay');
  elements.customVisionProviderName.value='Changed draft';
  elements.customVisionProviderApiKey.value='draft-secret';
  document.activeElement=elements.customVisionProviderApiKey;
  if(scenario==='switch-busy') _customVisionProviderBusy=true;
  confirmResult=scenario==='switch-confirm';
  const switched=await openCustomVisionProviderEditor('other');
  return {switched,id:elements.customVisionProviderId.value,name:elements.customVisionProviderName.value,
   baseUrl:elements.customVisionProviderBaseUrl.value,models:elements.customVisionProviderModels.value,
   secret:elements.customVisionProviderApiKey.value,activeId:document.activeElement.id,
   error:elements.customVisionProviderError.textContent};
 }
 await openCustomVisionProviderEditor();
 elements.customVisionProviderName.value='Changed draft';
 confirmResult=false;
 const first=await closeCustomVisionProviderEditor();
 confirmResult=true;
 const second=await closeCustomVisionProviderEditor();
 return {first,second,hidden:elements.customVisionProviderPanel.hidden,focused:document.activeElement.focused};
}
run(process.argv[3]).then(value=>process.stdout.write(JSON.stringify(value))).catch(err=>{console.error(err);process.exit(1);});
"""


_CUSTOM_VISION_ACTIVE_ROW_DRIVER = r"""
const fs=require('fs');const source=fs.readFileSync(process.argv[2],'utf8');
function extractFunc(name){const re=new RegExp('function\\s+'+name+'\\s*\\(');const start=source.search(re);
 let i=source.indexOf('{',start),depth=1;i++;while(depth>0&&i<source.length){if(source[i]==='{')depth++;else if(source[i]==='}')depth--;i++;}return source.slice(start,i);}
function el(tag){return {tag,id:'',textContent:'',disabled:false,title:'',attrs:{},children:[],
 setAttribute(k,v){this.attrs[k]=v;},appendChild(child){this.children.push(child);},set innerHTML(_v){this.children=[];}};}
const list=el('list');const $=id=>id==='customVisionProviderList'?list:null;
const document={createElement:tag=>el(tag)};
const _modelConfigData={vision_providers:[{id:'custom:relay',name:'Relay',custom:true,active:true,
 transport_label:'OpenAI Chat Completions',available:true}]};
const deleteCustomVisionProviderConfig=()=>{};const openCustomVisionProviderEditor=()=>{};
eval(extractFunc('_modelConfigCustomVisionRows'));eval(extractFunc('_renderCustomVisionProviderList'));
_renderCustomVisionProviderList(_modelConfigData);
const row=list.children[0],meta=row.children[0].children[1],button=row.children[1].children[1];
process.stdout.write(JSON.stringify({text:meta.textContent,disabled:button.disabled,describedBy:button.attrs['aria-describedby'],metaId:meta.id}));
"""


_VISION_RACE_DRIVER = r"""
const fs=require('fs');
const source=fs.readFileSync(process.argv[2],'utf8');
function extractFunc(name){
 const re=new RegExp('(?:async\\s+)?function\\s+'+name+'\\s*\\(');
 const start=source.search(re);
 if(start<0) throw new Error(name+' not found');
 let i=source.indexOf('{',start),depth=1;i++;
 while(depth>0&&i<source.length){if(source[i]==='{')depth++;else if(source[i]==='}')depth--;i++;}
 return source.slice(start,i);
}
const ids=['visionConfigProvider','visionConfigModel','visionConfigBaseUrl','visionConfigApiKey',
 'btnSaveVisionConfig','btnTestVisionConfig','modelConfigVisionSummaryCard',
 'visionConfigProviderSummary','visionConfigModelSummary','visionConfigKeyState',
 'visionConfigEffective','visionConfigStatusBadge','visionConfigSummary','visionConfigVerificationStatus'];
const elements={};
for(const id of ids) elements[id]={id,value:'',disabled:false,dataset:{},textContent:'',attrs:{},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];}};
elements.visionConfigProvider.value='alibaba';
elements.visionConfigModel.value='qwen3-vl-plus';
const $=id=>elements[id]||null;
const _setModelConfigText=(id,value)=>{if(elements[id])elements[id].textContent=String(value||'');};
const _setModelConfigStatusBadge=(id,value)=>_setModelConfigText(id,value);
const _modelConfigVisionProviderRow=()=>({id:'alibaba',name:'阿里百炼',requires_base_url:false});
const _modelConfigKeyLabel=()=> '凭据已配置';
const _formatModelConfigProvider=(id,label)=>label||id;
const _imageCapabilityCredentialRef=()=>'';
const _syncVisionConfigControls=()=>{};
const toggleModelConfigSection=()=>{};
const showToast=()=>{};
let _modelConfigData={profile:'default',vision:{provider:'alibaba',model:'qwen3-vl-plus',base_url:'',
 key_status:{configured:true},verification:{status:'verified',message:'已验证'}},vision_providers:[]};
let _visionTestGeneration=0;
let _visionVerificationSnapshot=null;
let resolveProbe;
const probePromise=new Promise(resolve=>{resolveProbe=resolve;});
const api=(url)=>{
 if(url==='/api/vision/test') return probePromise;
 if(url==='/api/vision/config') return Promise.reject(new Error('save failed'));
 throw new Error('unexpected '+url);
};
for(const name of ['_visionConfigIdentity','_setVisionConfigTestBusy','_restoreVisionTestSnapshot',
 '_invalidateVisionTest','_bindVisionConfigEditInvalidation','_renderVisionConfigSummary','_visionConfigHasUnsavedChanges',
 '_setFieldError','_safeEndpointPreview','_renderImageCapabilityEndpointPreview','saveVisionConfig','testVisionConfig']) eval(extractFunc(name));
_bindVisionConfigEditInvalidation();

async function run(scenario){
 const probeRun=testVisionConfig();
 await Promise.resolve();
 const during={
  status:_modelConfigData.vision.verification.status,
  controls:['visionConfigProvider','visionConfigModel','visionConfigBaseUrl','visionConfigApiKey','btnSaveVisionConfig'].map(id=>elements[id].disabled),
  testDisabled:elements.btnTestVisionConfig.disabled
 };
 if(scenario==='save-failure'){
  elements.visionConfigApiKey.value='draft-secret';
  await saveVisionConfig();
 }else if(scenario==='edit'){
  elements.visionConfigModel.value='draft-model';
  elements.visionConfigModel.oninput();
 }else{
  elements.visionConfigModel.value='draft-model';
  _visionTestGeneration++;
 }
 const afterAction={status:_modelConfigData.vision.verification.status,model:elements.visionConfigModel.value,
  key:elements.visionConfigApiKey.value,testDisabled:elements.btnTestVisionConfig.disabled,
  saveDisabled:elements.btnSaveVisionConfig.disabled};
 resolveProbe({ok:true,status:'verified',message:'old response'});
 await probeRun;
 return {during,afterAction,afterLate:{status:_modelConfigData.vision.verification.status,
  model:elements.visionConfigModel.value,key:elements.visionConfigApiKey.value,
  testDisabled:elements.btnTestVisionConfig.disabled,saveDisabled:elements.btnSaveVisionConfig.disabled}};
}
run(process.argv[3]).then(value=>process.stdout.write(JSON.stringify(value))).catch(err=>{console.error(err);process.exit(1);});
"""


_IMAGE_GEN_RACE_DRIVER = r"""
const fs=require('fs');
const source=fs.readFileSync(process.argv[2],'utf8');
function extractFunc(name){
 const re=new RegExp('(?:async\\s+)?function\\s+'+name+'\\s*\\(');
 const start=source.search(re);
 if(start<0) throw new Error(name+' not found');
 let i=source.indexOf('{',start),depth=1;i++;
 while(depth>0&&i<source.length){if(source[i]==='{')depth++;else if(source[i]==='}')depth--;i++;}
 return source.slice(start,i);
}
const ids=['imageGenConfigProvider','imageGenConfigCredential','imageGenConfigEndpointMode',
 'imageGenConfigRegion','imageGenConfigWorkspaceId','imageGenConfigBaseUrl','imageGenConfigModel',
 'imageGenConfigApiKey','imageGenConfigCredentials','btnSaveImageGenConfig','btnTestImageGenConfig',
 'modelConfigImageSummaryCard','imageGenConfigEffective','imageGenConfigStatusBadge',
 'imageGenConfigSummary','imageGenConfigVerificationStatus','imageGenConfigCardTitle',
 'imageGenConfigProviderSummary','imageGenConfigModelSummary','imageGenConfigKeyState'];
const elements={};
for(const id of ids) elements[id]={id,value:'',disabled:false,dataset:{},textContent:'',attrs:{},
 setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},querySelector(){return null;},querySelectorAll(){return [];}};
elements.imageGenConfigProvider.value='dashscope';
elements.imageGenConfigCredential.value='alibaba-default';
elements.imageGenConfigEndpointMode.value='workspace';
elements.imageGenConfigRegion.value='cn-beijing';
elements.imageGenConfigWorkspaceId.value='llm-demo';
elements.imageGenConfigModel.value='qwen-image-2.0-pro';
const $=id=>elements[id]||null;
const document={querySelectorAll(){return [];}};
const _setModelConfigText=(id,value)=>{if(elements[id])elements[id].textContent=String(value||'');};
const _setModelConfigStatusBadge=(id,value)=>_setModelConfigText(id,value);
const _modelConfigImageProviderRow=()=>({id:'dashscope',name:'阿里百炼',key_status:{configured:true},available:false,can_attempt:true});
const _modelConfigKeyLabel=()=> '凭据已配置';
const _formatModelConfigProvider=(id,label)=>label||id;
const _imageCapabilityCredentialRef=()=>elements.imageGenConfigCredential.value;
const _syncImageGenConfigControls=()=>{};
const _clearModelConfigSecrets=()=>{};
const _collectImageGenCredentials=()=>({});
const _closeModelConfigEditor=()=>{};
const showToast=()=>{};
let _modelConfigData={profile:'default',image_gen:{provider:'dashscope',model:'qwen-image-2.0-pro',
 credential_ref:'alibaba-default',options:{endpoint_mode:'workspace',region:'cn-beijing',workspace_id:'llm-demo'},
 verification:{status:'verified',message:'已验证'}},image_gen_providers:[]};
let _imageGenTestGeneration=0;
let _imageGenVerificationSnapshot=null;
let resolveProbe;
const probePromise=new Promise(resolve=>{resolveProbe=resolve;});
const api=(url)=>{
 if(url==='/api/image-gen/test') return probePromise;
 if(url==='/api/image-gen/config') return Promise.reject(new Error('save failed'));
 throw new Error('unexpected '+url);
};
for(const name of ['_imageGenConfigIdentity','_setImageGenConfigTestBusy','_restoreImageGenTestSnapshot',
 '_invalidateImageGenTest','_bindImageGenConfigEditInvalidation','_renderImageGenConfigSummary',
 '_imageGenConfigHasUnsavedChanges','_setFieldError','_safeEndpointPreview','_renderImageCapabilityEndpointPreview',
 'saveImageGenConfig','testImageGenConfig']) eval(extractFunc(name));
_bindImageGenConfigEditInvalidation();

async function run(scenario){
 const probeRun=testImageGenConfig();
 await Promise.resolve();
 const during={status:_modelConfigData.image_gen.verification.status,
  controls:['imageGenConfigProvider','imageGenConfigCredential','imageGenConfigEndpointMode',
   'imageGenConfigRegion','imageGenConfigWorkspaceId','imageGenConfigBaseUrl','imageGenConfigModel',
   'imageGenConfigApiKey','btnSaveImageGenConfig'].map(id=>elements[id].disabled),
  testDisabled:elements.btnTestImageGenConfig.disabled};
 if(scenario==='save-failure'){
  elements.imageGenConfigCredential.value='';
  elements.imageGenConfigApiKey.value='draft-secret';
  await saveImageGenConfig();
 }else if(scenario==='edit'){
  elements.imageGenConfigModel.value='draft-model';
  elements.imageGenConfigModel.oninput();
 }else{
  elements.imageGenConfigModel.value='draft-model';
  _imageGenTestGeneration++;
 }
 const afterAction={status:_modelConfigData.image_gen.verification.status,
  model:elements.imageGenConfigModel.value,key:elements.imageGenConfigApiKey.value,
  testDisabled:elements.btnTestImageGenConfig.disabled,saveDisabled:elements.btnSaveImageGenConfig.disabled};
 resolveProbe({ok:true,status:'verified',message:'old response'});
 await probeRun;
 return {during,afterAction,afterLate:{status:_modelConfigData.image_gen.verification.status,
  model:elements.imageGenConfigModel.value,key:elements.imageGenConfigApiKey.value,
  testDisabled:elements.btnTestImageGenConfig.disabled,saveDisabled:elements.btnSaveImageGenConfig.disabled}};
}
run(process.argv[3]).then(value=>process.stdout.write(JSON.stringify(value))).catch(err=>{console.error(err);process.exit(1);});
"""


_IMAGE_CONFIG_INTERACTION_DRIVER = r"""
const fs=require('fs');
const source=fs.readFileSync(process.argv[2],'utf8');
function extractFunc(name){
 const re=new RegExp('(?:async\\s+)?function\\s+'+name+'\\s*\\(');
 const start=source.search(re);
 if(start<0) throw new Error(name+' not found');
 let i=source.indexOf('{',start),depth=1;i++;
 while(depth>0&&i<source.length){if(source[i]==='{')depth++;else if(source[i]==='}')depth--;i++;}
 return source.slice(start,i);
}
function element(id,value=''){
 return {id,value,hidden:false,dataset:{},textContent:'',children:[],focused:false,
  attrs:{},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},
  appendChild(child){this.children.push(child);},set innerHTML(_value){this.children=[];},
  get options(){return this.children;},focus(){this.focused=true;}};
}
const elements={
 visionConfigCredential:element('visionConfigCredential'),
 visionConfigProvider:element('visionConfigProvider','alibaba'),
 visionConfigEndpointMode:element('visionConfigEndpointMode','public'),
 visionConfigRegion:element('visionConfigRegion','cn-beijing'),
 visionConfigWorkspaceId:element('visionConfigWorkspaceId','draft-workspace'),
 visionConfigBaseUrl:element('visionConfigBaseUrl'),
 visionConfigEndpointPreview:element('visionConfigEndpointPreview'),
 visionConfigEndpointError:element('visionConfigEndpointError'),
 visionConfigModel:element('visionConfigModel','draft-model'),
 visionConfigApiKey:element('visionConfigApiKey'),
 imageGenConfigCredential:element('imageGenConfigCredential'),
 imageGenConfigProvider:element('imageGenConfigProvider','dashscope'),
 imageGenConfigEndpointMode:element('imageGenConfigEndpointMode','workspace'),
 imageGenConfigRegion:element('imageGenConfigRegion','cn-beijing'),
 imageGenConfigWorkspaceId:element('imageGenConfigWorkspaceId','draft-image-workspace'),
 imageGenConfigBaseUrl:element('imageGenConfigBaseUrl'),
 imageGenConfigEndpointPreview:element('imageGenConfigEndpointPreview'),
 imageGenConfigEndpointError:element('imageGenConfigEndpointError'),
 imageGenConfigModel:element('imageGenConfigModel','draft-image-model'),
 btnEditVisionConfig:element('btnEditVisionConfig')
};
const $=id=>elements[id]||null;
const document={createElement(tag){return element(tag);}};
let _modelConfigData={provider_credentials:[
 {id:'alibaba-default',provider_family:'alibaba_dashscope',label:'共享凭据',configured:true},
 {id:'alibaba-image',provider_family:'alibaba_dashscope',label:'生图独立凭据',configured:true}
]};
let opened=null;
const openPlatformCredentialEditor=(id,capability)=>{opened={id,capability};};
const _imageCapabilityCredentialRef=()=>elements.visionConfigCredential.value;
const _invalidateVisionTest=()=>{};
const _invalidateImageGenTest=()=>{};
let closed=null;
const toggleModelConfigSection=(id,open)=>{closed={id,open};};
for(const name of ['_providerCredentialFamily','_defaultCredentialId','_renderCapabilityCredentialOptions',
 '_uniqueCredentialId',
 '_setFieldError','_safeEndpointPreview','_renderImageCapabilityEndpointPreview','_closeModelConfigEditor',
 '_visionConfigHasUnsavedChanges']) eval(extractFunc(name));

_renderCapabilityCredentialOptions('vision','alibaba','alibaba-default');
const select=elements.visionConfigCredential;
select.value='alibaba-image';
select.onchange();
select.value='__new__';
select.onchange();
const draft={model:elements.visionConfigModel.value,workspace:elements.visionConfigWorkspaceId.value};
const sharedSwitch={restored:select.value,opened,draft};
sharedSwitch.uniqueImageId=_uniqueCredentialId('alibaba-image');

elements.visionConfigEndpointMode.value='custom';
elements.visionConfigBaseUrl.value='https://user:pass@example.com/private/path?token=secret#fragment';
_renderImageCapabilityEndpointPreview('vision');
const preview={text:elements.visionConfigEndpointPreview.textContent,error:elements.visionConfigEndpointError.textContent};
preview.ariaInvalid=elements.visionConfigBaseUrl.attrs['aria-invalid'];
elements.imageGenConfigEndpointMode.value='custom';
elements.imageGenConfigBaseUrl.value='https://example.com/not-supported';
_renderImageCapabilityEndpointPreview('image');
const invalidImageEndpoint={text:elements.imageGenConfigEndpointPreview.textContent,error:elements.imageGenConfigEndpointError.textContent};

_closeModelConfigEditor('visionConfigEdit','btnEditVisionConfig');
const focus={closed,focused:elements.btnEditVisionConfig.focused};
_modelConfigData.vision={provider:'alibaba',model:'qwen3-vl-plus',credential_ref:'alibaba-default',
 endpoint_mode:'public',region:'cn-beijing',workspace_id:'',
 base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1'};
elements.visionConfigProvider.value='alibaba';
elements.visionConfigModel.value='qwen3-vl-plus';
elements.visionConfigCredential.value='alibaba-default';
elements.visionConfigEndpointMode.value='public';
elements.visionConfigRegion.value='cn-beijing';
elements.visionConfigWorkspaceId.value='';
elements.visionConfigBaseUrl.value='';
_renderImageCapabilityEndpointPreview('vision');
const endpointInvalidCleared=!('aria-invalid' in elements.visionConfigBaseUrl.attrs);
const publicEndpointDirty=_visionConfigHasUnsavedChanges();
process.stdout.write(JSON.stringify({sharedSwitch,preview,invalidImageEndpoint,focus,endpointInvalidCleared,publicEndpointDirty}));
"""


_CREDENTIAL_SESSION_DRIVER = r"""
const fs=require('fs');
const source=fs.readFileSync(process.argv[2],'utf8');
function extractFunc(name){
 const re=new RegExp('(?:async\\s+)?function\\s+'+name+'\\s*\\(');
 const start=source.search(re);
 if(start<0) throw new Error(name+' not found');
 let i=source.indexOf('{',start),depth=1;i++;
 while(depth>0&&i<source.length){if(source[i]==='{')depth++;else if(source[i]==='}')depth--;i++;}
 return source.slice(start,i);
}
function control(id,value=''){
 return {id,value,disabled:false,hidden:false,dataset:{},textContent:'',attrs:{},focused:false,isConnected:true,
  setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},focus(){this.focused=true;},
  querySelectorAll(){return [];}};
}
const elements={
 platformCredentialId:control('platformCredentialId','alibaba-default'),
 platformCredentialLabel:control('platformCredentialLabel','共享凭据'),
 platformCredentialFamily:control('platformCredentialFamily','alibaba_dashscope'),
 platformCredentialSecret:control('platformCredentialSecret','rotated-secret'),
 platformCredentialError:control('platformCredentialError'),
 platformCredentialListStatus:control('platformCredentialListStatus'),
 platformCredentialEditor:control('platformCredentialEditor'),
 btnSavePlatformCredential:control('btnSavePlatformCredential'),
 btnCancelPlatformCredential:control('btnCancelPlatformCredential'),
 btnAddPlatformCredential:control('btnAddPlatformCredential'),
 modelConfigPlatformCredentialList:control('modelConfigPlatformCredentialList'),
 visionConfigCredential:control('visionConfigCredential','alibaba-default'),
 imageGenConfigCredential:control('imageGenConfigCredential','alibaba-default'),
 visionConfigProvider:control('visionConfigProvider','alibaba'),
 imageGenConfigProvider:control('imageGenConfigProvider','dashscope'),
 visionConfigModel:control('visionConfigModel','vision-draft'),
 imageGenConfigModel:control('imageGenConfigModel','image-draft')
};
const editorControls=[elements.platformCredentialLabel,elements.platformCredentialFamily,elements.platformCredentialSecret,
 elements.btnSavePlatformCredential,elements.btnCancelPlatformCredential];
elements.platformCredentialEditor.querySelectorAll=()=>editorControls;
const action=control('update-action');
const deleteAction=control('delete-action');
elements.modelConfigPlatformCredentialList.querySelectorAll=()=>[action,deleteAction];
const $=id=>elements[id]||null;
const document={activeElement:action,querySelector(selector){
 if(selector.includes('data-credential-update')) return action;
 return null;
}};
let _modelConfigData={provider_credentials:[{id:'alibaba-default',provider_family:'alibaba_dashscope',label:'共享凭据',
 configured:true,used_by:['auxiliary.vision','image_gen']}],
 vision:{verification:{status:'verifying',message:'old vision probe'}},
 image_gen:{verification:{status:'verifying',message:'old image probe'}}};
let _platformCredentialReturnCapability='';
let _platformCredentialReturnFocus={credentialId:'alibaba-default'};
let _platformCredentialEditorGeneration=1;
let _platformCredentialSaveGeneration=0;
let _platformCredentialSaveSession=null;
let _platformCredentialDeleteGeneration=0;
let _platformCredentialDeleteSession=null;
let invalidatedVision=0,invalidatedImage=0,closed=0,rendered=0,optionsRendered=0,loadCount=0;
const _invalidateVisionTest=()=>{invalidatedVision++;};
const _invalidateImageGenTest=()=>{invalidatedImage++;};
const _renderPlatformCredentials=()=>{rendered++;};
const _renderCapabilityCredentialOptions=()=>{optionsRendered++;};
const _renderVisionConfigSummary=()=>{};
const _renderImageGenConfigSummary=()=>{};
const _clearModelConfigSecrets=()=>{elements.platformCredentialSecret.value='';};
const closePlatformCredentialEditor=()=>{closed++;};
const loadModelConfigPanel=()=>{loadCount++;};
const showToast=()=>{};
let confirmResult=true;
let confirmCalls=0;
const showConfirmDialog=async()=>{confirmCalls++;return confirmResult;};
let saveResolve;
let savePromise=new Promise(resolve=>{saveResolve=resolve;});
let deleteResolve,deleteReject;
let deletePromise=new Promise((resolve,reject)=>{deleteResolve=resolve;deleteReject=reject;});
let apiMode='save';
const api=(url)=>{
 if(url==='/api/provider-credentials'&&apiMode==='save') return savePromise;
 if(url.startsWith('/api/provider-credentials/')&&apiMode==='delete') return deletePromise;
 throw new Error('unexpected '+url);
};
for(const name of ['_setFieldError','_setPlatformCredentialActionsBusy','_platformCredentialSessionIsCurrent',
 '_applyProviderCredentialResult','savePlatformCredential','deletePlatformCredential']) eval(extractFunc(name));

async function run(){
 const saveRun=savePlatformCredential();
 await Promise.resolve();
 const saveDuring={editor:editorControls.map(item=>item.disabled),add:elements.btnAddPlatformCredential.disabled,
  actions:[action.disabled,deleteAction.disabled],busy:elements.btnSavePlatformCredential.attrs['aria-busy']};
 saveResolve({credential:{id:'alibaba-default',provider_family:'alibaba_dashscope',label:'共享凭据',configured:true,
  used_by:['auxiliary.vision','image_gen']}});
 await saveRun;
 const saveAfter={secret:elements.platformCredentialSecret.value,invalidatedVision,invalidatedImage,
  visionStatus:_modelConfigData.vision.verification.status,imageStatus:_modelConfigData.image_gen.verification.status,closed};

 elements.platformCredentialSecret.value='old-request-secret';
 elements.platformCredentialLabel.value='旧请求';
 savePromise=new Promise(resolve=>{saveResolve=resolve;});
 const lateRun=savePlatformCredential();
 await Promise.resolve();
 _platformCredentialEditorGeneration++;
 elements.platformCredentialSecret.value='new-session-draft';
 elements.platformCredentialLabel.value='新会话草稿';
 saveResolve({credential:{id:'alibaba-default',provider_family:'alibaba_dashscope',label:'过期响应',configured:true,used_by:[]}});
 await lateRun;
 const lateAfter={secret:elements.platformCredentialSecret.value,label:elements.platformCredentialLabel.value,
  storedLabel:_modelConfigData.provider_credentials[0].label,closed};

 elements.platformCredentialSecret.value='retry-secret';
 savePromise=Promise.reject(new Error('temporary failure'));
 await savePlatformCredential();
 const failedSave={secret:elements.platformCredentialSecret.value,error:elements.platformCredentialError.textContent};

 confirmResult=false;
 apiMode='delete';
 await deletePlatformCredential('alibaba-default',deleteAction);
 const cancelled={loadCount,rendered};
 confirmResult=true;
 elements.visionConfigModel.value='vision-dirty';
 elements.imageGenConfigModel.value='image-dirty';
 deletePromise=new Promise((resolve,reject)=>{deleteResolve=resolve;deleteReject=reject;});
 const deleteRun=deletePlatformCredential('unused',deleteAction);
 await Promise.resolve();
 await deletePlatformCredential('competing-delete',deleteAction);
 const deleteDuring={disabled:deleteAction.disabled,add:elements.btnAddPlatformCredential.disabled};
 deleteResolve({credentials:[]});
 await deleteRun;
 const deleteAfter={visionDraft:elements.visionConfigModel.value,imageDraft:elements.imageGenConfigModel.value,
  rows:_modelConfigData.provider_credentials.length,loadCount,focused:elements.btnAddPlatformCredential.focused};

 deletePromise=new Promise((resolve,reject)=>{deleteResolve=resolve;deleteReject=reject;});
 const failedRun=deletePlatformCredential('unused-2',deleteAction);
 await Promise.resolve();
 deleteReject(new Error('凭据正在使用'));
 await failedRun;
 const deleteFailed={message:elements.platformCredentialListStatus.textContent,focused:deleteAction.focused,confirmCalls};
 return {saveDuring,saveAfter,lateAfter,failedSave,cancelled,deleteDuring,deleteAfter,deleteFailed};
}
run().then(result=>process.stdout.write(JSON.stringify(result))).catch(err=>{console.error(err);process.exit(1);});
"""


_MODEL_CONFIG_DRAFT_GUARD_DRIVER = r"""
const fs=require('fs');
const source=fs.readFileSync(process.argv[2],'utf8');
function extractFunc(name){
 const re=new RegExp('(?:async\\s+)?function\\s+'+name+'\\s*\\(');
 const start=source.search(re);
 if(start<0) throw new Error(name+' not found');
 let i=source.indexOf('{',start),depth=1;i++;
 while(depth>0&&i<source.length){if(source[i]==='{')depth++;else if(source[i]==='}')depth--;i++;}
 return source.slice(start,i);
}
function control(id,value=''){
 return {id,value,hidden:false,dataset:{},textContent:'',disabled:false,attrs:{},focused:false,
  classList:{add(){},remove(){}},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},
  focus(){this.focused=true;},querySelector(){return null;},querySelectorAll(){return [];}};
}
const ids=['modelConfigStatus','modelConfigDraftStatus','modelConfigProvider','modelConfigModel','modelConfigBaseUrl',
 'modelConfigApiKey','visionConfigProvider','visionConfigModel','visionConfigBaseUrl','visionConfigApiKey',
 'visionConfigCredential','visionConfigEndpointMode','visionConfigRegion','visionConfigWorkspaceId',
 'imageGenConfigProvider','imageGenConfigModel','imageGenConfigApiKey','imageGenConfigCredential',
 'imageGenConfigEndpointMode','imageGenConfigRegion','imageGenConfigWorkspaceId','imageGenConfigBaseUrl',
 'imageGenConfigCredentials','platformCredentialEditor','platformCredentialId','platformCredentialLabel',
 'platformCredentialFamily','platformCredentialSecret','visionConfigCredentialRow','imageGenConfigCredentialRow',
 'modelConfigPlatformCredentials','btnAddPlatformCredential','modelConfigActive'];
const elements={}; for(const id of ids) elements[id]=control(id);
Object.assign(elements.modelConfigProvider,{value:'openai'});
Object.assign(elements.modelConfigModel,{value:'gpt-4o'});
Object.assign(elements.visionConfigProvider,{value:'zai'});
Object.assign(elements.visionConfigModel,{value:'glm-4v'});
Object.assign(elements.visionConfigEndpointMode,{value:'public'});
Object.assign(elements.visionConfigRegion,{value:'cn-beijing'});
Object.assign(elements.imageGenConfigProvider,{value:'doubao'});
Object.assign(elements.imageGenConfigModel,{value:'seedream'});
Object.assign(elements.imageGenConfigEndpointMode,{value:'workspace'});
Object.assign(elements.imageGenConfigRegion,{value:'cn-beijing'});
elements.platformCredentialEditor.hidden=true;
const dynamicSecret=control('dynamicSecret'); dynamicSecret.dataset.imageGenCredential='api_key';
const secretFields=[elements.modelConfigApiKey,elements.visionConfigApiKey,elements.imageGenConfigApiKey,elements.platformCredentialSecret,dynamicSecret];
secretFields.forEach(item=>{item.dataset.secretField='true';});
const $=id=>elements[id]||null;
const document={querySelectorAll(selector){
 if(selector.includes('data-image-gen-credential')) return [dynamicSecret];
 return selector.includes('data-secret-field')?secretFields:[];
}};
let _modelConfigData={profile:'default',main:{provider:'openai',model:'gpt-4o',base_url:''},
 vision:{provider:'zai',model:'glm-4v',base_url:'',credential_ref:'',endpoint_mode:'',region:'',workspace_id:''},
 image_gen:{provider:'doubao',model:'seedream',credential_ref:'',options:{}},provider_credentials:[]};
let _modelConfigLoadGeneration=0;
const _imageCapabilityProviderDrafts={vision:{},image:{}};
let _platformCredentialSaveSession=null,_platformCredentialDeleteSession=null;
let _platformCredentialReturnCapability='',_platformCredentialReturnFocus=null;
let _settingsDirty=false;
let renderCount=0,providerRenderCount=0,apiCount=0,confirmCount=0,confirmResult=false,resolveLoad,hideCount=0;
let pendingLoad=new Promise(resolve=>{resolveLoad=resolve;});
const _renderModelConfigPanel=data=>{renderCount++;_modelConfigData=data;};
const _renderProviderImageGenSettings=()=>{providerRenderCount++;};
const _loadModelConfigAuxiliaryModels=async()=>{};
const _bindTaijiLicenseControls=()=>{};
const loadTaijiLicenseStatus=async()=>{};
const showToast=()=>{};
const showConfirmDialog=async()=>{confirmCount++;return confirmResult;};
const toggleModelConfigSection=(id,open)=>{if(elements[id]) elements[id].hidden=!open;};
const _restorePlatformCredentialFocus=()=>{};
const _revertSettingsPreview=()=>{};
const _hideSettingsPanel=()=>{hideCount++;};
const _showSettingsUnsavedBar=()=>{};
const api=async()=>{apiCount++;return await pendingLoad;};
for(const name of ['_providerSupportsNamedCredential','_imageCapabilityCredentialRef','_syncPlatformCredentialSurface',
 '_collectImageGenCredentials','_captureImageCapabilityProviderDraft','_restoreImageCapabilityProviderDraft',
 '_visionConfigHasUnsavedChanges','_imageGenConfigHasUnsavedChanges','_modelConfigMainHasUnsavedChanges',
 '_platformCredentialEditorHasUnsavedChanges','_imageGenCredentialDraftHasValues','_modelConfigAnySecretDraft','_modelConfigHasUnsavedChanges',
 '_modelConfigDraftIdentity','_clearCapabilityProviderDraftSecrets','_clearModelConfigSecrets','_discardImageCapabilityProviderDrafts',
 '_setModelConfigDraftStatus','closePlatformCredentialEditor','loadModelConfigPanel','refreshProviderImageGenStatus',
 '_closeSettingsPanel']) eval(extractFunc(name));

async function run(){
 elements.platformCredentialEditor.hidden=false; elements.platformCredentialSecret.value='cancel-secret';
 closePlatformCredentialEditor();
 const cancelSecretCleared=elements.platformCredentialSecret.value==='';
 _syncPlatformCredentialSurface();
 const unsupported={visionRowHidden:elements.visionConfigCredentialRow.hidden,
  imageRowHidden:elements.imageGenConfigCredentialRow.hidden,addHidden:elements.btnAddPlatformCredential.hidden,
  visionRef:_imageCapabilityCredentialRef('vision','zai'),imageRef:_imageCapabilityCredentialRef('image','doubao')};
 elements.visionConfigProvider.value='alibaba'; _syncPlatformCredentialSurface();
 const alibabaVisible=!elements.visionConfigCredentialRow.hidden&&!elements.btnAddPlatformCredential.hidden;
 elements.visionConfigProvider.value='custom'; elements.imageGenConfigProvider.value='dashscope'; _syncPlatformCredentialSurface();
 const dashscopeVisible=!elements.imageGenConfigCredentialRow.hidden&&!elements.btnAddPlatformCredential.hidden;

 elements.visionConfigModel.value='zai-draft'; elements.visionConfigApiKey.value='zai-secret';
 _captureImageCapabilityProviderDraft('vision','zai');
 elements.visionConfigModel.value=''; elements.visionConfigApiKey.value='';
 const visionDraftRestored=_restoreImageCapabilityProviderDraft('vision','zai');
 elements.imageGenConfigModel.value='doubao-draft'; dynamicSecret.value='doubao-secret';
 _captureImageCapabilityProviderDraft('image','doubao');
 elements.imageGenConfigModel.value=''; dynamicSecret.value='';
 const imageDraftRestored=_restoreImageCapabilityProviderDraft('image','doubao');
 const providerDrafts={visionDraftRestored,imageDraftRestored,visionModel:elements.visionConfigModel.value,
  visionSecret:elements.visionConfigApiKey.value,imageModel:elements.imageGenConfigModel.value,imageSecret:dynamicSecret.value};
 elements.visionConfigModel.value='glm-4v'; elements.visionConfigApiKey.value='';
 elements.imageGenConfigModel.value='seedream'; dynamicSecret.value='';

 elements.visionConfigProvider.value='zai'; elements.imageGenConfigProvider.value='doubao';
 const initialDirty=_modelConfigHasUnsavedChanges();
 const late=loadModelConfigPanel(true,{skipDirtyConfirm:true});
 await Promise.resolve();
 elements.visionConfigModel.value='new-draft';
 const lateDirty=_modelConfigHasUnsavedChanges();
 resolveLoad({profile:'default',main:{provider:'openai',model:'server'},vision:{provider:'zai',model:'server'},image_gen:{provider:'doubao',model:'server'}});
 await late;
 const lateGuard={renderCount,draft:elements.visionConfigModel.value,status:elements.modelConfigDraftStatus.textContent};

 await loadModelConfigPanel(false);
 const returnGuard={renderCount,draft:elements.visionConfigModel.value};

 pendingLoad=Promise.resolve({profile:'default',main:{provider:'openai',model:'fresh'},vision:{provider:'zai',model:'fresh'},image_gen:{provider:'doubao',model:'fresh'}});
 secretFields.forEach((item,index)=>{item.value='secret-'+index;});
 await loadModelConfigPanel(true);
 const cancelled={apiCount,confirmCount,renderCount,draft:elements.visionConfigModel.value};
 confirmResult=true;
 await loadModelConfigPanel(true);
 const accepted={apiCount,confirmCount,renderCount,secrets:secretFields.map(item=>item.value)};
 function resetScopeSecrets(){
  secretFields.forEach((item,index)=>{item.value='scope-'+index;});
  _imageCapabilityProviderDrafts.vision={zai:{ApiKey:'vision-draft-secret'}};
  _imageCapabilityProviderDrafts.image={doubao:{ApiKey:'image-draft-legacy',Credentials:{api_key:'image-draft-secret'},CredentialSecretKeys:['api_key']}};
 }
 const scopeMatrix={};
 for(const scope of ['platform','main','vision','image']){
  resetScopeSecrets();
  _clearModelConfigSecrets(scope,scope==='vision'?'zai':(scope==='image'?'doubao':''));
  scopeMatrix[scope]={dom:secretFields.map(item=>item.value),visionDraft:_imageCapabilityProviderDrafts.vision.zai.ApiKey,
   imageLegacy:_imageCapabilityProviderDrafts.image.doubao.ApiKey,imageDraft:_imageCapabilityProviderDrafts.image.doubao.Credentials.api_key};
 }
 resetScopeSecrets();
 _clearModelConfigSecrets('all');
 const cleared=secretFields.every(item=>item.value==='');

 elements.modelConfigModel.value='close-draft'; elements.modelConfigApiKey.value='close-secret';
 confirmResult=false;
 const closeCancelled=await _closeSettingsPanel();
 const closeCancel={result:closeCancelled,hideCount,model:elements.modelConfigModel.value,secret:elements.modelConfigApiKey.value};
 confirmResult=true;
 const closeConfirmed=await _closeSettingsPanel();
 const closeConfirm={result:closeConfirmed,hideCount,secret:elements.modelConfigApiKey.value};

 elements.modelConfigModel.value='provider-refresh-draft';
 pendingLoad=Promise.resolve({profile:'default',main:{provider:'openai',model:'server-replacement'},image_gen:{provider:'doubao'},image_gen_providers:[]});
 const baselineBefore=_modelConfigData.main.model;
 const renderBefore=renderCount;
 await refreshProviderImageGenStatus();
 const providerRefresh={draft:elements.modelConfigModel.value,baseline:_modelConfigData.main.model,baselineBefore,
  modelRenderDelta:renderCount-renderBefore,providerRenderCount};
 return {cancelSecretCleared,unsupported,alibabaVisible,dashscopeVisible,providerDrafts,initialDirty,lateDirty,
  lateGuard,returnGuard,cancelled,accepted,scopeMatrix,cleared,closeCancel,closeConfirm,providerRefresh};
}
run().then(result=>process.stdout.write(JSON.stringify(result))).catch(err=>{console.error(err);process.exit(1);});
"""


def _run_vision_race(tmp_path: Path, scenario: str) -> dict:
    driver = tmp_path / "vision-race-driver.js"
    driver.write_text(_VISION_RACE_DRIVER, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(driver), str(ROOT / "static" / "panels.js"), scenario],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def _run_image_gen_race(tmp_path: Path, scenario: str) -> dict:
    driver = tmp_path / "image-gen-race-driver.js"
    driver.write_text(_IMAGE_GEN_RACE_DRIVER, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(driver), str(ROOT / "static" / "panels.js"), scenario],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def _run_image_config_interactions(tmp_path: Path) -> dict:
    driver = tmp_path / "image-config-interaction-driver.js"
    driver.write_text(_IMAGE_CONFIG_INTERACTION_DRIVER, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(driver), str(ROOT / "static" / "panels.js")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def _run_credential_sessions(tmp_path: Path) -> dict:
    driver = tmp_path / "credential-session-driver.js"
    driver.write_text(_CREDENTIAL_SESSION_DRIVER, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(driver), str(ROOT / "static" / "panels.js")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def _run_model_config_draft_guard(tmp_path: Path) -> dict:
    driver = tmp_path / "model-config-draft-guard-driver.js"
    driver.write_text(_MODEL_CONFIG_DRAFT_GUARD_DRIVER, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(driver), str(ROOT / "static" / "panels.js")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def test_model_config_settings_section_exists():
    assert 'data-settings-section="models"' in INDEX_HTML
    assert 'id="settingsPaneModels"' in INDEX_HTML
    assert "模型配置" in INDEX_HTML


def test_model_config_has_three_required_surfaces():
    for marker in (
        'id="modelConfigProvider"',
        'id="modelConfigAuxContainer"',
        'id="imageGenConfigProvider"',
    ):
        assert marker in INDEX_HTML


def test_model_config_js_calls_expected_endpoints():
    assert "async function loadModelConfigPanel" in PANELS_JS
    assert "/api/model-config" in PANELS_JS
    assert "/api/model-config/main" in PANELS_JS
    assert "/api/image-gen/config" in PANELS_JS
    assert "/api/model/auxiliary" in PANELS_JS
    assert "/api/model/set" in PANELS_JS


def test_model_config_secret_inputs_start_empty():
    assert 'id="modelConfigApiKey"' in INDEX_HTML
    assert 'id="imageGenConfigApiKey"' in INDEX_HTML
    assert 'id="imageGenConfigCredentials"' in INDEX_HTML
    assert "modelConfigApiKey')||{}).value||''" in PANELS_JS
    assert "imageGenConfigApiKey')||{}).value||''" in PANELS_JS
    assert "key_status" in PANELS_JS
    assert "payload.api_key=apiKey" in PANELS_JS
    assert "payload.credentials=credentials" in PANELS_JS


def test_secret_paste_uses_desktop_clipboard_bridge_when_available():
    assert "readSecretClipboardText" in PANELS_JS
    assert "window.taijiDesktop" in PANELS_JS
    assert "readClipboardText" in PANELS_JS
    assert "navigator.clipboard.readText" in PANELS_JS


def test_model_config_focus_layout_has_summary_cards():
    for marker in (
        'class="model-config-focus-layout"',
        "model-config-license-strip",
        'id="modelConfigMainSummaryCard"',
        'id="modelConfigImageSummaryCard"',
        'id="modelConfigAuxSummary"',
    ):
        assert marker in INDEX_HTML


def test_model_config_license_actions_remain_visible():
    for marker in (
        'id="btnImportTaijiLicense"',
        'id="btnExportTaijiMachineRequest"',
        'id="btnRefreshTaijiLicense"',
    ):
        assert marker in INDEX_HTML


def test_model_config_edit_forms_are_collapsible_in_cards():
    for marker in (
        'id="modelConfigMainEdit"',
        'id="imageGenConfigEdit"',
        'id="modelConfigAuxEdit"',
        'toggleModelConfigSection',
    ):
        assert marker in INDEX_HTML or marker in PANELS_JS


def test_model_config_js_updates_focus_summaries():
    for marker in (
        "_setModelConfigStatusBadge",
        "_renderModelConfigFocusSummary",
        "modelConfigMainEffective",
        "imageGenConfigKeyState",
        "taijiLicenseRemainingBadge",
    ):
        assert marker in PANELS_JS


def test_image_generation_summary_uses_real_verification_state():
    assert "const verification=imageGen.verification||{}" in PANELS_JS
    assert "status==='verified'" in PANELS_JS
    assert "imageRow.available===true" not in PANELS_JS
    assert "imageRow.oauth_managed));" not in PANELS_JS


def test_image_generation_auth_hint_is_taiji_branded():
    assert "此图片生成服务由太极智能体授权管理" in PANELS_JS
    assert "Codex/ChatGPT OAuth" not in PANELS_JS


def test_image_generation_custom_provider_management_has_visible_entry():
    for marker in (
        'id="btnAddCustomImageProvider"',
        'id="btnManageCustomImageProviders"',
        'id="btnGoImageProviders"',
        'id="customImageProviderPanel"',
        'id="customImageProviderBaseUrl"',
        "添加外部图片模型",
        "管理外部模型",
        "去提供商配置",
    ):
        assert marker in INDEX_HTML
    assert "saveCustomImageProviderConfig" in PANELS_JS
    assert "deleteCustomImageProviderConfig" in PANELS_JS
    assert "/api/image-gen/custom-providers" in PANELS_JS


def test_named_custom_vision_provider_management_has_visible_accessible_entry():
    for marker in (
        'id="btnManageCustomVisionProviders"',
        'id="customVisionProviderPanel"',
        'id="customVisionProviderTransport"',
        '兼容协议',
        'OpenAI Chat Completions',
        'Anthropic Messages',
    ):
        assert marker in INDEX_HTML
    assert "任意平台" not in INDEX_HTML
    for marker in (
        "saveCustomVisionProviderConfig",
        "deleteCustomVisionProviderConfig",
        "/api/vision/custom-providers",
        "_setCustomVisionProviderBusy",
        "customVisionProviderError",
        "_customVisionProviderDraftIdentity",
        "originalDraft",
        "closeCustomVisionProviderEditor",
        "_customVisionProviderReturnFocus",
        "row.active",
        "正在使用，需先切换后删除",
        "aria-describedby",
    ):
        assert marker in PANELS_JS


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
@pytest.mark.parametrize("scenario", ["save", "active-delete", "dirty-close", "switch-cancel", "switch-confirm", "switch-busy"])
def test_named_custom_vision_provider_interactions(tmp_path, scenario):
    driver = tmp_path / "custom-vision-provider-driver.js"
    driver.write_text(_CUSTOM_VISION_PROVIDER_DRIVER, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(driver), str(ROOT / "static" / "panels.js"), scenario],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    if scenario == "save":
        assert payload["apiCalls"][0]["url"] == "/api/vision/custom-providers"
        assert payload["busy"] is False
        assert payload["hidden"] is True
        assert payload["secret"] == ""
        assert payload["focused"] is True
    elif scenario == "active-delete":
        assert payload["apiCalls"] == []
        assert "正在使用" in payload["error"]
    elif scenario == "switch-cancel":
        assert payload == {
            "switched": False,
            "id": "relay",
            "name": "Changed draft",
            "baseUrl": "https://relay.example.com/v1",
            "models": "relay-vl",
            "secret": "draft-secret",
            "activeId": "customVisionProviderApiKey",
            "error": "",
        }
    elif scenario == "switch-confirm":
        assert payload["switched"] is True
        assert payload["id"] == "other"
        assert payload["name"] == "Other Vision"
        assert payload["secret"] == ""
    elif scenario == "switch-busy":
        assert payload["switched"] is False
        assert payload["id"] == "relay"
        assert payload["name"] == "Changed draft"
        assert payload["secret"] == "draft-secret"
        assert "正在处理" in payload["error"]
    else:
        assert payload["first"] is False
        assert payload["second"] is True
        assert payload["hidden"] is True
        assert payload["focused"] is True


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_active_custom_vision_row_explains_delete_block_with_aria_relation(tmp_path):
    driver = tmp_path / "custom-vision-active-row-driver.js"
    driver.write_text(_CUSTOM_VISION_ACTIVE_ROW_DRIVER, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(driver), str(ROOT / "static" / "panels.js")],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert "正在使用，需先切换后删除" in payload["text"]
    assert payload["disabled"] is True
    assert payload["describedBy"] == payload["metaId"]


def test_settings_menu_does_not_add_auth_keys_section():
    assert 'data-settings-section="authorization"' not in INDEX_HTML
    assert 'data-settings-section="auth"' not in INDEX_HTML
    assert 'data-settings-section="keys"' not in INDEX_HTML
    assert "授权与密钥" not in INDEX_HTML


def test_model_config_routes_image_generation_management_to_existing_providers_tab():
    assert "switchSettingsSection('providers')" in PANELS_JS
    assert "openImageProvidersPanel" in PANELS_JS
    assert 'id="settingsPaneProviders"' in INDEX_HTML
    assert 'data-settings-section="providers"' in INDEX_HTML


def test_providers_panel_contains_image_generation_provider_management_surface():
    for marker in (
        'id="providerImageGenServices"',
        'id="providerImageGenTemplates"',
        "外部模型服务",
        "添加图片生成提供商",
        "通义万相",
        "豆包 Seedream",
        "百度千帆",
        "腾讯混元",
        "智谱 GLM-Image",
        "讯飞 HiDream",
        "自定义 HTTP",
    ):
        assert marker in INDEX_HTML
    for marker in (
        "_renderProviderImageGenSettings",
        "_DOMESTIC_IMAGE_PROVIDER_TEMPLATES",
        "providerImageGenServices",
        "providerImageGenTemplates",
    ):
        assert marker in PANELS_JS
    assert ".provider-image-services" in STYLE_CSS
    assert ".provider-template-grid" in STYLE_CSS


def test_model_config_has_clear_image_capability_cards():
    for marker in (
        'id="modelConfigImageCapabilities"',
        'id="modelConfigVisionSummaryCard"',
        'id="modelConfigImageSummaryCard"',
        'id="visionConfigProviderSummary"',
        'id="visionConfigModelSummary"',
        'id="visionConfigKeyState"',
        'id="visionConfigEdit"',
        'id="visionConfigProvider"',
        'id="visionConfigModel"',
        'id="visionConfigApiKey"',
        'id="btnSaveVisionConfig"',
        "图片能力",
        "看图识别",
        "生成图片",
    ):
        assert marker in INDEX_HTML
    assert "_renderVisionConfigSummary" in PANELS_JS
    assert "saveVisionConfig" in PANELS_JS
    assert "/api/vision/config" in PANELS_JS
    assert ".model-config-image-capability-grid" in STYLE_CSS
    assert ".model-config-capability-card" in STYLE_CSS


def test_image_capability_has_visible_safe_platform_credential_surface():
    for marker in (
        'id="modelConfigPlatformCredentials"',
        'id="modelConfigPlatformCredentialList"',
        'id="btnAddPlatformCredential"',
        'id="platformCredentialEditor"',
        'id="platformCredentialLabel"',
        'id="platformCredentialSecret"',
        '平台凭据',
        '新增独立凭据',
        '使用范围',
    ):
        assert marker in INDEX_HTML
    for marker in (
        "_renderPlatformCredentials",
        "openPlatformCredentialEditor",
        "savePlatformCredential",
        "/api/provider-credentials",
    ):
        assert marker in PANELS_JS
    assert "secret_env" not in PANELS_JS
    assert "Key 片段" not in INDEX_HTML


def test_credential_controls_expose_expansion_errors_and_touch_targets():
    for marker in (
        'id="btnAddPlatformCredential"',
        'aria-expanded="false"',
        'aria-controls="platformCredentialEditor"',
        'id="platformCredentialListStatus" aria-live="polite"',
        'id="btnCancelPlatformCredential"',
        'id="btnEditVisionConfig"',
        'aria-controls="visionConfigEdit"',
        'id="btnEditImageGenConfig"',
        'aria-controls="imageGenConfigEdit"',
    ):
        assert marker in INDEX_HTML
    for marker in (
        "_setFieldError",
        "aria-invalid",
        "showConfirmDialog",
        "_setPlatformCredentialActionsBusy",
        "_platformCredentialSessionIsCurrent",
    ):
        assert marker in PANELS_JS
    assert "min-height:44px" in STYLE_CSS


def test_named_credential_controls_are_scoped_to_supported_providers():
    assert 'id="visionConfigCredentialRow"' in INDEX_HTML
    assert 'id="imageGenConfigCredentialRow"' in INDEX_HTML
    for marker in (
        "_providerSupportsNamedCredential",
        "_imageCapabilityCredentialRef",
        "_syncPlatformCredentialSurface",
        "id==='alibaba'",
        "id==='dashscope'",
    ):
        assert marker in PANELS_JS
    assert "const payload={provider,model,base_url:baseUrl,credential_ref:credentialRef" not in PANELS_JS
    assert "const payload={provider,model,credential_ref:credentialRef}" not in PANELS_JS


def test_model_config_load_and_secret_lifecycle_have_explicit_guards():
    assert 'id="modelConfigDraftStatus" aria-live="polite"' in INDEX_HTML
    for marker in (
        "_modelConfigLoadGeneration",
        "_modelConfigMainHasUnsavedChanges",
        "_platformCredentialEditorHasUnsavedChanges",
        "_modelConfigHasUnsavedChanges",
        "_clearModelConfigSecrets",
        "skipDirtyConfirm",
        "检测到未保存草稿",
    ):
        assert marker in PANELS_JS
    assert "_clearModelConfigSecrets();" in PANELS_JS
    close_settings = PANELS_JS.split("function _closeSettingsPanel", 1)[1].split(
        "function _revertSettingsPreview", 1
    )[0]
    hide_settings = PANELS_JS.split("function _hideSettingsPanel", 1)[1].split(
        "function _closeSettingsPanel", 1
    )[0]
    assert "_clearModelConfigSecrets" in close_settings
    assert "_clearModelConfigSecrets" in hide_settings
    assert "showConfirmDialog" in close_settings
    assert "_modelConfigHasUnsavedChanges" in close_settings


def test_secret_cleanup_is_scoped_and_provider_refresh_does_not_replace_model_baseline():
    for marker in (
        "_clearModelConfigSecrets('platform')",
        "_clearModelConfigSecrets('main')",
        "_clearModelConfigSecrets('vision'",
        "_clearModelConfigSecrets('image'",
    ):
        assert marker in PANELS_JS
    refresh_body = PANELS_JS.split("async function refreshProviderImageGenStatus", 1)[1].split(
        "function _splitCustomImageProviderModels", 1
    )[0]
    assert "_renderModelConfigPanel" not in refresh_body
    provider_render_body = PANELS_JS.split("function _renderProviderImageGenSettings", 1)[1].split(
        "async function _selectImageProviderFromProviders", 1
    )[0]
    assert "_modelConfigData=data" not in provider_render_body


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_provider_scope_load_generation_and_secret_cleanup_are_state_safe(tmp_path):
    result = _run_model_config_draft_guard(tmp_path)
    assert result["cancelSecretCleared"] is True
    assert result["unsupported"] == {
        "visionRowHidden": True,
        "imageRowHidden": True,
        "addHidden": True,
        "visionRef": "",
        "imageRef": "",
    }
    assert result["alibabaVisible"] is True
    assert result["dashscopeVisible"] is True
    assert result["providerDrafts"] == {
        "visionDraftRestored": True,
        "imageDraftRestored": True,
        "visionModel": "zai-draft",
        "visionSecret": "zai-secret",
        "imageModel": "doubao-draft",
        "imageSecret": "doubao-secret",
    }
    assert result["lateGuard"] == {
        "renderCount": 0,
        "draft": "new-draft",
        "status": "检测到未保存草稿，已保留当前编辑内容；服务器状态未覆盖页面。",
    }
    assert result["returnGuard"] == {"renderCount": 0, "draft": "new-draft"}
    assert result["cancelled"] == {
        "apiCount": 1,
        "confirmCount": 1,
        "renderCount": 0,
        "draft": "new-draft",
    }
    assert result["accepted"] == {
        "apiCount": 2,
        "confirmCount": 2,
        "renderCount": 1,
        "secrets": ["", "", "", "", ""],
    }
    assert result["scopeMatrix"]["platform"] == {
        "dom": ["scope-0", "scope-1", "scope-2", "", "scope-4"],
        "visionDraft": "vision-draft-secret",
        "imageLegacy": "image-draft-legacy",
        "imageDraft": "image-draft-secret",
    }
    assert result["scopeMatrix"]["main"] == {
        "dom": ["", "scope-1", "scope-2", "scope-3", "scope-4"],
        "visionDraft": "vision-draft-secret",
        "imageLegacy": "image-draft-legacy",
        "imageDraft": "image-draft-secret",
    }
    assert result["scopeMatrix"]["vision"] == {
        "dom": ["scope-0", "", "scope-2", "scope-3", "scope-4"],
        "visionDraft": "",
        "imageLegacy": "image-draft-legacy",
        "imageDraft": "image-draft-secret",
    }
    assert result["scopeMatrix"]["image"] == {
        "dom": ["scope-0", "scope-1", "", "scope-3", ""],
        "visionDraft": "vision-draft-secret",
        "imageLegacy": "",
        "imageDraft": "",
    }
    assert result["cleared"] is True
    assert result["closeCancel"] == {
        "result": False,
        "hideCount": 0,
        "model": "close-draft",
        "secret": "close-secret",
    }
    assert result["closeConfirm"] == {"result": True, "hideCount": 1, "secret": ""}
    assert result["providerRefresh"] == {
        "draft": "provider-refresh-draft",
        "baseline": result["providerRefresh"]["baselineBefore"],
        "baselineBefore": result["providerRefresh"]["baselineBefore"],
        "modelRenderDelta": 0,
        "providerRenderCount": 1,
    }


def test_both_image_capability_cards_use_consistent_endpoint_and_test_controls():
    expected_ids = (
        "visionConfigCredential", "visionConfigEndpointMode", "visionConfigRegion",
        "visionConfigWorkspaceId", "visionConfigBaseUrl", "visionConfigEndpointPreview",
        "btnTestVisionConfig", "imageGenConfigCredential", "imageGenConfigEndpointMode",
        "imageGenConfigRegion", "imageGenConfigWorkspaceId", "imageGenConfigBaseUrl",
        "imageGenConfigEndpointPreview", "btnTestImageGenConfig",
        "imageGenConfigVerificationStatus",
    )
    for element_id in expected_ids:
        assert f'id="{element_id}"' in INDEX_HTML
    assert 'aria-label="测试生图配置"' in INDEX_HTML
    assert 'id="imageGenConfigVerificationStatus" aria-live="polite"' in INDEX_HTML
    assert '真实生图测试可能产生少量费用' in INDEX_HTML


def test_image_endpoint_fields_are_accessible_and_progressively_disclosed():
    for marker in (
        'id="visionConfigEndpointError"',
        'id="imageGenConfigEndpointError"',
        'aria-describedby="visionConfigEndpointError"',
        'aria-describedby="imageGenConfigEndpointError"',
        'id="visionConfigEndpointPreview" aria-live="polite"',
        'id="imageGenConfigEndpointPreview" aria-live="polite"',
        '<option value="cn-beijing">',
        '<option value="ap-southeast-1">',
    ):
        assert marker in INDEX_HTML
    for marker in (
        "_syncImageCapabilityEndpointFields",
        "_renderImageCapabilityEndpointPreview",
        "endpointMode==='workspace'",
        "endpointMode==='custom'",
    ):
        assert marker in PANELS_JS
    assert "/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/" in PANELS_JS


def test_model_config_closing_editor_restores_focus_to_visible_toggle():
    assert "function _closeModelConfigEditor" in PANELS_JS
    assert "toggle.focus()" in PANELS_JS
    assert 'id="btnEditVisionConfig"' in INDEX_HTML
    assert 'id="btnEditImageGenConfig"' in INDEX_HTML


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_image_config_switching_preview_and_focus_are_state_safe(tmp_path):
    result = _run_image_config_interactions(tmp_path)

    assert result["sharedSwitch"] == {
        "restored": "alibaba-image",
        "opened": {"id": "", "capability": "vision"},
        "draft": {"model": "draft-model", "workspace": "draft-workspace"},
        "uniqueImageId": "alibaba-image-2",
    }
    assert result["preview"] == {
        "text": "端点尚不完整",
        "error": "自定义 Base URL 不得包含账号信息、查询参数或片段。",
        "ariaInvalid": "true",
    }
    assert result["invalidImageEndpoint"] == {
        "text": "端点尚不完整",
        "error": "生图自定义端点必须是根地址或完整生成接口。",
    }
    assert result["focus"] == {
        "closed": {"id": "visionConfigEdit", "open": False},
        "focused": True,
    }
    assert result["endpointInvalidCleared"] is True
    assert result["publicEndpointDirty"] is False


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_credential_save_delete_sessions_do_not_clobber_newer_drafts(tmp_path):
    result = _run_credential_sessions(tmp_path)

    assert result["saveDuring"] == {
        "editor": [True] * 5,
        "add": True,
        "actions": [True, True],
        "busy": "true",
    }
    assert result["saveAfter"] == {
        "secret": "",
        "invalidatedVision": 1,
        "invalidatedImage": 1,
        "visionStatus": "configured_unverified",
        "imageStatus": "configured_unverified",
        "closed": 1,
    }
    assert result["lateAfter"] == {
        "secret": "new-session-draft",
        "label": "新会话草稿",
        "storedLabel": "共享凭据",
        "closed": 1,
    }
    assert result["failedSave"] == {
        "secret": "retry-secret",
        "error": "凭据保存失败：temporary failure",
    }
    assert result["cancelled"] == {"loadCount": 0, "rendered": 1}
    assert result["deleteDuring"] == {"disabled": True, "add": True}
    assert result["deleteAfter"] == {
        "visionDraft": "vision-dirty",
        "imageDraft": "image-dirty",
        "rows": 0,
        "loadCount": 0,
        "focused": True,
    }
    assert result["deleteFailed"] == {
        "message": "凭据删除失败：凭据正在使用",
        "focused": True,
        "confirmCalls": 3,
    }


def test_vision_verification_has_visible_accessible_status_and_action():
    for marker in (
        'id="btnTestVisionConfig"',
        'onclick="testVisionConfig()"',
        'aria-label="测试识图配置"',
        'id="visionConfigVerificationStatus"',
        'aria-live="polite"',
        "图片会发送给你配置的外部视觉服务",
        "请勿上传密钥或隐私截图",
    ):
        assert marker in INDEX_HTML


def test_vision_verification_ui_uses_explicit_state_machine_and_test_endpoint():
    for marker in (
        "configured_unverified",
        "verifying",
        "verified",
        "failed",
        "async function testVisionConfig",
        "/api/vision/test",
        "已配置，尚未验证",
        "验证失败",
        "正在验证",
    ):
        assert marker in PANELS_JS
    assert "ready?'\u5df2可用':'\u5f85配置'" not in PANELS_JS


def test_vision_verification_uses_long_timeout_and_stale_response_guard():
    assert "timeoutMs:150000" in PANELS_JS
    assert "_visionTestGeneration" in PANELS_JS
    assert "_visionConfigIdentity" in PANELS_JS
    assert "runGeneration!==_visionTestGeneration" in PANELS_JS
    assert "_restoreVisionTestSnapshot(runGeneration)" in PANELS_JS


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_vision_verifying_save_failure_restores_state_without_losing_draft(tmp_path):
    result = _run_vision_race(tmp_path, "save-failure")

    assert result["during"] == {
        "status": "verifying",
        "controls": [True, True, True, True, True],
        "testDisabled": True,
    }
    assert result["afterAction"] == {
        "status": "verified",
        "model": "qwen3-vl-plus",
        "key": "draft-secret",
        "testDisabled": False,
        "saveDisabled": False,
    }
    assert result["afterLate"] == result["afterAction"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_vision_verifying_edit_invalidates_probe_and_ignores_late_response(tmp_path):
    result = _run_vision_race(tmp_path, "edit")

    assert result["during"]["status"] == "verifying"
    assert result["during"]["controls"] == [True, True, True, True, True]
    assert result["afterAction"] == {
        "status": "verified",
        "model": "draft-model",
        "key": "",
        "testDisabled": False,
        "saveDisabled": False,
    }
    assert result["afterLate"] == result["afterAction"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_vision_stale_finally_restores_snapshot_without_overwriting_input(tmp_path):
    result = _run_vision_race(tmp_path, "stale")

    assert result["afterAction"]["status"] == "verifying"
    assert result["afterLate"] == {
        "status": "verified",
        "model": "draft-model",
        "key": "",
        "testDisabled": False,
        "saveDisabled": False,
    }


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
@pytest.mark.parametrize("scenario", ["save-failure", "edit", "stale"])
def test_image_gen_verification_guards_late_responses_and_preserves_draft(
    tmp_path, scenario
):
    result = _run_image_gen_race(tmp_path, scenario)

    assert result["during"] == {
        "status": "verifying",
        "controls": [True] * 9,
        "testDisabled": True,
    }
    if scenario == "save-failure":
        assert result["afterAction"] == {
            "status": "verified",
            "model": "qwen-image-2.0-pro",
            "key": "draft-secret",
            "testDisabled": False,
            "saveDisabled": False,
        }
    elif scenario == "edit":
        assert result["afterAction"] == {
            "status": "verified",
            "model": "draft-model",
            "key": "",
            "testDisabled": False,
            "saveDisabled": False,
        }
    else:
        assert result["afterAction"]["status"] == "verifying"
    assert result["afterLate"]["status"] == "verified"
    expected_model = "draft-model" if scenario == "stale" else result["afterAction"]["model"]
    assert result["afterLate"]["model"] == expected_model
    assert result["afterLate"]["key"] == result["afterAction"]["key"]
    assert result["afterLate"]["testDisabled"] is False
    assert result["afterLate"]["saveDisabled"] is False


def test_vision_test_route_is_registered():
    routes_source = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    assert 'parsed.path == "/api/vision/test"' in routes_source
    assert "test_vision_config" in routes_source
    assert 'parsed.path == "/api/image-gen/test"' in routes_source
    assert "test_image_gen_config" in routes_source
    assert 'parsed.path == "/api/provider-credentials"' in routes_source
    assert 'parsed.path.startswith("/api/provider-credentials/")' in routes_source


def test_image_generation_advanced_actions_are_outside_primary_card():
    card_start = INDEX_HTML.find('id="modelConfigImageSummaryCard"')
    card_end = INDEX_HTML.find('</section>', card_start)
    assert card_start >= 0 and card_end > card_start
    card_html = INDEX_HTML[card_start:card_end]
    for advanced_marker in (
        'id="btnAddCustomImageProvider"',
        'id="btnManageCustomImageProviders"',
        'id="btnGoImageProviders"',
        "通义万相",
        "豆包 Seedream",
        "自定义 HTTP",
    ):
        assert advanced_marker not in card_html
    assert 'id="modelConfigImageAdvanced"' in INDEX_HTML


def test_image_generation_uses_dynamic_domestic_credential_form():
    assert 'id="imageGenConfigCredentials"' in INDEX_HTML
    assert "生成图片只显示中国可用的稳定出图服务" in INDEX_HTML
    for marker in (
        "_renderImageGenCredentialFields",
        "credential_fields",
        "imageGenConfigCredentials",
        "data-image-gen-credential",
        "payload.credentials=credentials",
        "当前配置不符合国产策略，请切换到上方中国可用 Provider。",
    ):
        assert marker in PANELS_JS


def test_image_generation_custom_provider_new_form_generates_id():
    assert "_customImageProviderDraftId" in PANELS_JS
    assert "customImageProviderId" in PANELS_JS
    assert "||_customImageProviderDraftId(name,baseUrl)" in PANELS_JS


def test_image_generation_save_updates_only_its_capability_without_clobbering_other_drafts():
    assert "async function saveImageGenConfig" in PANELS_JS
    save_body = PANELS_JS.split("async function saveImageGenConfig", 1)[1].split(
        "function _imageGenConfigHasUnsavedChanges", 1
    )[0]
    assert "await loadModelConfigPanel(true)" not in save_body
    assert "_modelConfigData.image_gen=data.image_gen" in save_body


def test_image_generation_edit_uses_selected_provider_default_model():
    assert "const selectedImage=_modelConfigImageProviderRow" in PANELS_JS
    assert (
        "imageModelInput.value=imageGen.model||String((selectedImage&&selectedImage.default_model)||'')"
        in PANELS_JS
    )


def test_image_generation_oauth_managed_provider_hides_key_paste_action():
    assert "modelConfigImagePasteAction" in INDEX_HTML
    assert "modelConfigImagePasteAction" in PANELS_JS
    assert (
        "pasteAction.style.display=(managedAuth||policyBlocked||namedCredential)?'none':''"
        in PANELS_JS
    )


def test_image_generation_key_row_uses_dynamic_credentials_and_policy_explanation():
    assert 'id="imageGenProviderScopeHint"' in INDEX_HTML
    assert 'id="visionProviderScopeHint"' in INDEX_HTML
    assert "provider&&provider.oauth_managed" in PANELS_JS
    assert "imageGenConfigApiKey.disabled=oauth||blocked" in PANELS_JS
    assert "此服务由太极授权托管，无需填写 API 密钥。" in PANELS_JS
    assert "keyRow.hidden=named||fields.length>0" in PANELS_JS
    assert "当前配置不符合国产策略，请切换到上方中国可用 Provider。" in PANELS_JS


def test_model_config_license_layout_prioritizes_customer_and_compacts_actions():
    assert 'class="model-config-license-customer"' in INDEX_HTML
    assert 'id="taijiLicenseCustomer"' in INDEX_HTML
    assert 'class="model-config-license-toolbar"' in INDEX_HTML
    assert "#settingsPaneModels .model-config-license-customer" in STYLE_CSS
    assert "#settingsPaneModels .model-config-license-toolbar" in STYLE_CSS
    assert "grid-template-columns:minmax(0,1fr) auto" in STYLE_CSS
    assert (
        "#settingsPaneModels .model-config-license-actions{display:grid;gap:6px;"
        "justify-items:end;align-self:start;min-width:0;max-width:220px;}"
    ) in STYLE_CSS


def test_model_config_styles_are_present():
    assert ".model-config-status" in STYLE_CSS
    assert ".model-config-panel" in STYLE_CSS
    assert ".model-config-aux-row" in STYLE_CSS
    assert ".model-config-focus-layout" in STYLE_CSS
    assert ".model-config-summary-card" in STYLE_CSS
    assert ".model-config-collapsible" in STYLE_CSS
