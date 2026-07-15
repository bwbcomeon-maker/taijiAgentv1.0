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
for(const id of ids) elements[id]={id,value:'',disabled:false,dataset:{},textContent:'',attrs:{},setAttribute(k,v){this.attrs[k]=v;}};
elements.visionConfigProvider.value='alibaba';
elements.visionConfigModel.value='qwen3-vl-plus';
const $=id=>elements[id]||null;
const _setModelConfigText=(id,value)=>{if(elements[id])elements[id].textContent=String(value||'');};
const _setModelConfigStatusBadge=(id,value)=>_setModelConfigText(id,value);
const _modelConfigVisionProviderRow=()=>({id:'alibaba',name:'阿里百炼',requires_base_url:false});
const _modelConfigKeyLabel=()=> '凭据已配置';
const _formatModelConfigProvider=(id,label)=>label||id;
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
 '_safeEndpointPreview','_renderImageCapabilityEndpointPreview','saveVisionConfig','testVisionConfig']) eval(extractFunc(name));
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
 setAttribute(k,v){this.attrs[k]=v;},querySelector(){return null;},querySelectorAll(){return [];}};
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
const _syncImageGenConfigControls=()=>{};
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
 '_imageGenConfigHasUnsavedChanges','_safeEndpointPreview','_renderImageCapabilityEndpointPreview',
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
const _invalidateVisionTest=()=>{};
const _invalidateImageGenTest=()=>{};
let closed=null;
const toggleModelConfigSection=(id,open)=>{closed={id,open};};
for(const name of ['_providerCredentialFamily','_defaultCredentialId','_renderCapabilityCredentialOptions',
 '_uniqueCredentialId',
 '_safeEndpointPreview','_renderImageCapabilityEndpointPreview','_closeModelConfigEditor',
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
const publicEndpointDirty=_visionConfigHasUnsavedChanges();
process.stdout.write(JSON.stringify({sharedSwitch,preview,invalidImageEndpoint,focus,publicEndpointDirty}));
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
    }
    assert result["invalidImageEndpoint"] == {
        "text": "端点尚不完整",
        "error": "生图自定义端点必须是根地址或完整生成接口。",
    }
    assert result["focus"] == {
        "closed": {"id": "visionConfigEdit", "open": False},
        "focused": True,
    }
    assert result["publicEndpointDirty"] is False


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


def test_image_generation_save_forces_full_model_config_refresh():
    assert "async function saveImageGenConfig" in PANELS_JS
    assert "await loadModelConfigPanel(true)" in PANELS_JS
    assert "image_gen:data.image_gen" not in PANELS_JS


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
