"""Regression coverage for provider preload and local-only refresh controls."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

PANEL_DRIVER = r"""
const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[1],'utf8'),scenario=process.argv[2];
function extract(name){
 const start=source.search(new RegExp('^(?:async )?function '+name+'\\(','m'));
 const rest=source.slice(start+1),next=rest.search(/^(?:async )?function /m);
 return source.slice(start,next<0?source.length:start+1+next);
}
const field=value=>({value,dataset:{},style:{},classList:{add(){},remove(){}},appendChild(){}});
const els={providersList:field(''),providersEmpty:field(''),modelConfigProvider:field(''),
 modelConfigModel:field(''),modelConfigBaseUrl:field(''),modelConfigApiKey:field(''),modelConfigActive:field('')};
let server={main:{provider:'zai-cn',model:'glm-5',key_status:{configured:true}}};
let renders=0,reads=0,status='',fail=false;
const ctx={_modelConfigData:null,_modelConfigLoadGeneration:0,$:id=>els[id]||null,
 _pendingMainModelConfigReconciliation:null,
 api:async url=>{if(url==='/api/model-config'){reads++;if(fail)throw Error('fixture unavailable');return server;}return {providers:[]};},
 _fetchProviderQuotaStatus:async()=>{
  if(scenario==='provider-race')ctx._renderModelConfigPanel({main:{provider:'zai-cn',model:'glm-6',key_status:{configured:true}}});
  if(scenario==='provider-load-race')ctx._modelConfigLoadGeneration++;
  return {};
 },_providerCardEls:new Map(),_buildProviderQuotaCard:()=>null,
 _renderProviderImageGenSettings:()=>{},['_modelConfigAnySecretDraft']:()=>!!els.modelConfigApiKey.value,
 _visionConfigHasUnsavedChanges:()=>false,_imageGenConfigHasUnsavedChanges:()=>false,
 _imageGenCredentialDraftHasValues:()=>false,_imageCapabilityProviderDrafts:{vision:{},image:{}},
 _platformCredentialEditorHasUnsavedChanges:()=>false,_bindTaijiLicenseControls:()=>{},
 loadTaijiLicenseStatus:async()=>{},_setModelConfigDraftStatus:m=>status=m,
 _renderModelConfigPanel:data=>{renders++;ctx._modelConfigData=data;els.modelConfigProvider.value=data.main.provider;els.modelConfigModel.value=data.main.model;},
 _loadModelConfigAuxiliaryModels:async()=>{},_clearModelConfigSecrets:()=>{},showConfirmDialog:async()=>true,
 _discardImageCapabilityProviderDrafts:()=>{},showToast:()=>{},esc:String,t:String};
vm.createContext(ctx);
for(const name of ['_modelConfigMainHasUnsavedChanges','_modelConfigHasUnsavedChanges',
 '_modelConfigDraftIdentity','loadModelConfigPanel','loadProvidersPanel']){
 if(name==='_modelConfigDraftIdentity')ctx[name]=()=>JSON.stringify([els.modelConfigProvider.value,els.modelConfigModel.value,els.modelConfigApiKey.value]);
 else vm.runInContext(extract(name),ctx);
}
if(scenario==='defaults'){
 for(const [id,value] of Object.entries({visionConfigProvider:'alibaba',visionConfigModel:'qwen-vl-plus',
  imageGenConfigProvider:'dashscope',imageGenConfigModel:'wanx-v1'}))els[id]=field(value);
 ctx._imageCapabilityCredentialRef=()=>'';ctx._collectImageCapabilityEndpointValues=()=>({});
 vm.runInContext(extract('_visionConfigHasUnsavedChanges'),ctx);
 vm.runInContext(extract('_imageGenConfigHasUnsavedChanges'),ctx);
}
(async()=>{
 if(scenario!=='preload')ctx._renderModelConfigPanel({main:{provider:'zai-cn',model:'glm-4',key_status:{configured:true}}});
 if(scenario==='defaults'){
  Object.assign(ctx._modelConfigData,{vision_providers:[{id:'alibaba',default_model:'qwen-vl-plus'}],
   image_gen_providers:[{id:'dashscope',default_model:'wanx-v1'}]});
  const dirty=ctx._modelConfigHasUnsavedChanges();
  els.visionConfigModel.value='real-draft';
  console.log(JSON.stringify({dirty,editedDirty:ctx._modelConfigHasUnsavedChanges()}));return;
 }
 if(scenario==='draft'){els.modelConfigModel.value='draft-model';els.modelConfigApiKey.value='FAKE-unsaved';}
 if(scenario!=='return'&&scenario!=='failure')await ctx.loadProvidersPanel();
 const baselineBefore=ctx._modelConfigData&&ctx._modelConfigData.main.model;
 if(scenario==='provider-race'||scenario==='provider-load-race'){
  console.log(JSON.stringify({baselineBefore,model:els.modelConfigModel.value}));return;
 }
 if(scenario==='failure')fail=true;
 const result=await ctx.loadModelConfigPanel(scenario==='failure');
 console.log(JSON.stringify({renders,reads,status,baselineBefore,resultIsNull:result===null,
  provider:els.modelConfigProvider.value,model:els.modelConfigModel.value,hasDraftKey:els.modelConfigApiKey.value==='FAKE-unsaved'}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""

REFRESH_DRIVER = r"""
const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync(process.argv[1],'utf8'),scenario=process.argv[2];
const source=html.split('/* image-capability-center-runtime:start */')[1].split('/* image-capability-center-runtime:end */')[0];
const elements={};
for(const id of ['imageCapabilityCenter','btnReloadAllModelConfig','modelConfigDraftStatus','imageCapabilityCenterStatusDetail']){
 elements[id]={id,dataset:{},textContent:'',disabled:false,attrs:{},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},querySelectorAll(){return [];}};
}
let calls=0,release;
const ctx={window:{api:()=>new Promise(()=>{}),loadModelConfigPanel:async()=>{
 calls++;if(scenario==='failure')throw Error('fixture unavailable');
 if(scenario==='duplicate')await new Promise(resolve=>release=resolve);
 return {main:{}};
}},document:{readyState:'loading',addEventListener(){},getElementById:id=>elements[id]||null}};
vm.createContext(ctx);vm.runInContext(source,ctx);
(async()=>{
 ctx.window.loadImageCapabilityCenter();
 await Promise.resolve();await Promise.resolve();
 const disabledByImage=elements.btnReloadAllModelConfig.disabled;
 const first=ctx.window.refreshModelAndImageCapabilities();
 await Promise.resolve();await Promise.resolve();
 if(scenario==='duplicate'){await ctx.window.refreshModelAndImageCapabilities();if(release)release();}
 await first;
 console.log(JSON.stringify({calls,disabledByImage,disabled:elements.btnReloadAllModelConfig.disabled,
  status:elements.modelConfigDraftStatus.textContent,busy:elements.btnReloadAllModelConfig.attrs['aria-busy']}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""


def run_driver(driver, filename, scenario):
    if NODE is None:
        pytest.skip("node not on PATH")
    result = subprocess.run(
        [NODE, "-e", driver, str(ROOT / "static" / filename), scenario],
        capture_output=True, text=True, timeout=15, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("scenario", ["preload", "provider-refresh", "return"])
def test_pristine_model_panel_reads_current_state_without_false_drafts(scenario):
    result = run_driver(PANEL_DRIVER, "panels.js", scenario)
    assert result["model"] == "glm-5"
    assert "未保存草稿" not in result["status"]
    if scenario == "provider-refresh":
        assert result["baselineBefore"] == "glm-5"


def test_provider_refresh_preserves_real_model_and_secret_drafts():
    result = run_driver(PANEL_DRIVER, "panels.js", "draft")
    assert result["baselineBefore"] == "glm-4"
    assert result["model"] == "draft-model"
    assert result["hasDraftKey"]
    assert "未保存草稿" in result["status"]


@pytest.mark.parametrize("scenario,expected", [("provider-race", "glm-6"), ("provider-load-race", "glm-4")])
def test_delayed_provider_preload_cannot_overwrite_new_model_state(scenario, expected):
    result = run_driver(PANEL_DRIVER, "panels.js", scenario)
    assert result["baselineBefore"] == expected
    assert result["model"] == expected


def test_failed_model_reload_has_visible_status_and_no_success_result():
    result = run_driver(PANEL_DRIVER, "panels.js", "failure")
    assert result["resultIsNull"]
    assert "失败" in result["status"]


def test_unconfigured_capability_defaults_are_pristine_but_edits_are_dirty():
    result = run_driver(PANEL_DRIVER, "panels.js", "defaults")
    assert not result["dirty"]
    assert result["editedDirty"]


@pytest.mark.parametrize("scenario", ["image-busy", "failure", "duplicate"])
def test_refresh_main_is_independent_of_image_busy_and_reports_outcome(scenario):
    result = run_driver(REFRESH_DRIVER, "index.html", scenario)
    assert not result["disabledByImage"]
    assert result["calls"] == 1
    assert not result["disabled"]
    assert "失败" in result["status"] if scenario == "failure" else "主模型" in result["status"]
