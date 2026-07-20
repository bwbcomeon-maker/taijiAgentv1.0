import json
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_node(source: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", source],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_docx_engine_workbench_has_visible_controls_and_actions():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    messages_js = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    style_css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "docx-engine-workbench" in ui_js
    assert "renderDocxEngineWorkbench" in ui_js
    assert "runDocxEngineJob" in ui_js
    assert "installDocxEngineTemplate" in ui_js
    assert "markDocxEngineWpsVisualAccepted" in ui_js
    assert "openDocxDeliveryFolder" in ui_js
    assert "replaceDocxEngineAsset" in ui_js
    assert "模板包目录" in ui_js
    assert "安装模板包" in ui_js
    assert "覆盖已安装模板" in ui_js
    assert "质量报告" in ui_js
    assert "打开 DOCX" in ui_js
    assert "打开交付目录" in ui_js
    assert "Office 最终验收" in ui_js
    assert "先打开 DOCX" in ui_js
    assert "证据文件路径" in ui_js
    assert "aria-label" in ui_js
    assert ".docx-engine-workbench" in style_css

    assert "/api/docx-engine-v2/templates" in ui_js
    assert "/api/docx-engine-v2/templates/install" in ui_js
    assert "/api/docx-engine-v2/jobs" in ui_js
    assert "/api/docx-engine-v2/drafts/package" in ui_js
    assert "/api/docx-engine-v2/quality/wps-visual" in ui_js
    assert "/api/docx-engine-v2/assets/rerender" in ui_js
    assert "/api/docx-engine-v2/assets/replace" in ui_js
    assert "/api/docx-template/figure-adjust" not in ui_js
    assert "/api/file/open" in ui_js
    assert "renderDocxEngineWorkbenchMessage" in messages_js


def test_docx_engine_workbench_prioritizes_one_click_template_application():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    messages_js = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    style_css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "套用模板生成 DOCX" in ui_js
    assert "套用当前结果生成 DOCX" in ui_js
    assert "use_current_result" in ui_js
    assert "交付目录（可不填）" in ui_js
    assert "请选择方案文件，或点击“套用当前结果生成 DOCX”。" in ui_js
    assert "请填写模板、源文件路径和交付目录。" not in ui_js
    assert "docx-engine-advanced" in ui_js
    assert "<summary>高级操作" in ui_js
    assert ".docx-engine-advanced" in style_css
    assert "正在套用模板" in messages_js


def test_docx_template_selection_flow_uses_result_or_source_request_cards():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    messages_js = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    style_css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "docx-template-delivery-card" in ui_js
    assert "docx-source-request-card" in ui_js
    assert "renderDocxTemplateAppliedMessage" in messages_js
    assert "renderDocxSourceRequestMessage" in messages_js
    assert "startData.docx_template_applied" in messages_js
    assert "startData.docx_source_required" in messages_js
    assert "renderDocxEngineWorkbenchMessage(activeSid,startData);" not in messages_js
    assert ".docx-template-delivery-card" in style_css
    assert ".docx-source-request-card" in style_css


def test_docx_engine_workbench_exposes_required_accessible_control_names():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    for label in [
        "选择模板",
        "套用当前结果生成 DOCX",
        "从源文件生成 DOCX",
        "查看质量报告",
        "打开 DOCX",
        "打开交付目录",
        "先打开 DOCX",
        "提交 Office 验收证据",
        "证据文件路径",
        "审核人",
        "验收备注",
        "重渲染图片",
        "替换 DOCX 图片",
        "模板包目录",
        "安装模板包",
        "覆盖已安装模板",
        "从源文件生成 DOCX",
        "刷新模板列表",
    ]:
        assert label in ui_js


def test_docx_engine_workbench_covers_feedback_and_recovery_states():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert "role=\"status\"" in ui_js
    assert "aria-live=\"polite\"" in ui_js
    assert "data-docx-engine-status" in ui_js
    assert "template_package_path" in ui_js
    assert "replace_existing" in ui_js
    assert "title:'覆盖已安装模板？'" in ui_js
    assert "confirmLabel:'覆盖模板'" in ui_js
    assert "focusCancel:true" in ui_js
    assert "确认已在 WPS/Word 打开 DOCX" in ui_js
    assert "正在记录 WPS 验收结果" in ui_js
    assert "审核人来自本次可信复核会话" in ui_js
    assert "选择文件不等于验收通过" in ui_js
    assert "document_opened" in ui_js
    assert "layout_reviewed" in ui_js
    assert "content_order_reviewed" in ui_js
    assert "figures_reviewed" in ui_js
    assert "tables_reviewed" in ui_js
    assert "evidence_files" in ui_js
    assert "visual_checks" in ui_js
    assert "reviewer" in ui_js
    assert "旧 DOCX 需要先重新套模板" in ui_js
    assert "passed_with_warnings" in ui_js
    assert "quality_status" in ui_js
    assert "quality_report" in ui_js
    assert "data-docx-engine-quality-detail" in ui_js
    assert "data-docx-engine-action=\"quality\"" in ui_js
    assert "aria-invalid" in ui_js
    assert "document_path" in ui_js
    assert "delivery_dir" in ui_js


def test_docx_engine_workbench_keeps_failure_artifacts_out_of_product_copy():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    workspace_js = (REPO_ROOT / "static" / "workspace.js").read_text(encoding="utf-8")
    start = ui_js.index("function _docxEngineFailureEvidence(err)")
    function = ui_js[start : ui_js.index("function _clearDocxEngineFieldErrors", start)]

    assert "err.payload" in workspace_js
    assert "_docxEngineFailureEvidence" in ui_js
    assert "_safeProductErrorEnvelope({payload})" in function
    assert "productError.incident_id" in function
    assert "failure_report_path" not in function
    assert "job_manifest_path" not in function
    assert "payload.failures" not in function


def test_docx_engine_workbench_prevents_duplicate_and_premature_actions():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    style_css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "aria-busy" in ui_js
    assert "_syncDocxEngineActionAvailability" in ui_js
    assert "data-docx-engine-action=\"document\"" in ui_js
    assert "data-docx-engine-action=\"delivery\"" in ui_js
    assert 'data-docx-engine-action="wps" aria-disabled="true" disabled' not in ui_js
    assert 'node.disabled=true;node.setAttribute(\'aria-disabled\',\'true\')' not in ui_js
    assert 'node.disabled=!hasDocument||!hasDelivery' in ui_js
    assert "disabled>打开 DOCX" in ui_js
    assert "_docxFigureAdjustmentSetBusy" in ui_js
    assert ".docx-figure-adjustment-actions button:disabled" in style_css


def test_wps_visual_acceptance_is_discoverable_in_docx_and_expert_team_results():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    expert_ui_js = (REPO_ROOT / "static" / "expert-team-ui.js").read_text(encoding="utf-8")
    style_css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "renderDocxWpsVisualAcceptanceForm" in ui_js
    assert "submitDocxWpsVisualAcceptance" in ui_js
    assert "openDocxWpsAcceptanceDocument" in ui_js
    assert "uploadDocxWpsEvidenceFiles" in ui_js
    assert "data-docx-wps-acceptance" in ui_js
    assert 'type="file"' in ui_js
    assert 'multiple accept="image/png,image/jpeg,application/pdf,.png,.jpg,.jpeg,.pdf"' in ui_js
    assert "选择并上传本次新证据" in ui_js
    assert "renderDocxWpsVisualAcceptanceForm" in expert_ui_js
    assert "openExpertTeamWpsAcceptance" in expert_ui_js
    assert "Office 验收" in expert_ui_js
    assert ".docx-wps-acceptance" in style_css
    assert ".docx-wps-acceptance .docx-wps-checks input{flex:0 0 auto;width:16px;height:16px" in style_css


def test_wps_visual_acceptance_has_recoverable_busy_error_success_and_strict_validation_states():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert "_setDocxWpsAcceptanceBusy" in ui_js
    assert "aria-busy" in ui_js
    assert "正在记录 WPS 验收结果" in ui_js
    assert "Office 验收记录失败" in ui_js
    assert "Office 验收证据已记录" in ui_js
    assert "请完成全部视觉检查项" in ui_js
    assert "请在本次文档打开后上传至少一个真实证据文件" in ui_js
    assert "必须填写验收备注" in ui_js
    assert "_updateDocxEngineQualityOnly" in ui_js
    assert "refreshWriteflowStatusDockForActiveSession" in ui_js
    assert "'/api/workspace/upload'" in ui_js
    assert "payload&&payload.evidence_dir" in ui_js
    assert "正在上传 Office 验收证据" in ui_js
    assert "证据上传失败" in ui_js
    assert "尚未提交验收" in ui_js
    assert "所有验收结论都必须填写验收备注" in ui_js
    assert "不能使用 user、系统、审核人" in ui_js
    assert "PNG、JPEG 每张至少 800×500" in ui_js
    assert "PDF 必须包含可渲染页面" in ui_js


def test_wps_visual_acceptance_requires_a_trusted_review_session_before_upload_or_submit():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    open_start = ui_js.index("async function openDocxWpsAcceptanceDocument")
    open_end = ui_js.index("async function submitDocxWpsVisualAcceptance", open_start)
    open_source = ui_js[open_start:open_end]

    assert "'/api/docx-engine-v2/quality/wps-visual/begin'" in open_source
    assert "'/api/file/open'" in open_source
    assert "_docxWpsMode(root)==='expert'" in open_source
    assert "data-docx-wps-review-session" in ui_js
    assert "data-docx-wps-attestation" in ui_js
    assert "我确认以上证据来自刚刚打开的本次 WPS/Word 文档并完成逐页检查" in ui_js
    assert "review_token" in ui_js
    assert "evidence_dir" in ui_js
    assert "attested_actual_office_review" in ui_js
    assert "本次复核新上传证据（只读）" in ui_js
    assert "aria-readonly=\"true\" readonly" in ui_js


def test_wps_acceptance_render_context_separates_expert_and_generic_protocols():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    expert_ui_js = (REPO_ROOT / "static" / "expert-team-ui.js").read_text(encoding="utf-8")

    assert 'data-docx-wps-mode="${mode}"' in ui_js
    assert "const mode=context.expertTeam===true?'expert':'generic'" in ui_js
    assert "renderDocxWpsVisualAcceptanceForm({" in expert_ui_js
    assert "expertTeam:true" in expert_ui_js
    assert "renderDocxWpsVisualAcceptanceForm()" in ui_js
    assert "_newGenericDocxWpsReviewSession" in ui_js
    assert "本地人工验收" in ui_js


def test_consumed_review_rerender_keeps_record_text_readable_and_all_mutations_locked():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    start = ui_js.index("const _docxWpsAcceptanceFeedback=new Map();")
    end = ui_js.index("function renderDocxEngineWorkbench", start)
    source = textwrap.dedent(
        f"""
        const esc=(value)=>String(value||'');
        function _docxWpsReviewSummary(){{return '已成功提交';}}
        {ui_js[start:end]}
        _docxWpsAcceptanceFeedback.set('delivery',{{
          reviewSession:{{mode:'expert',consumed:true,reviewer:'王审核',documentSha256:'abc123'}},
          evidencePaths:['.taiji/wps-evidence/token/page.png'],
          draft:{{status:'passed',note:'已在 WPS 打开文档，逐页检查目录、版式、图表和分页。',visualChecks:['document_opened','layout_reviewed','content_order_reviewed','figures_reviewed','tables_reviewed'],attestedActualOfficeReview:true}},
        }});
        const html=renderDocxWpsVisualAcceptanceForm({{expertTeam:true,documentPath:'delivery/document.docx',deliveryDir:'delivery',qualityStatus:'passed'}});
        const tag=(pattern)=>(html.match(pattern)||[''])[0];
        const reviewer=tag(/<input[^>]*data-docx-wps-field="reviewer"[^>]*>/);
        const evidence=tag(/<textarea[^>]*data-docx-wps-field="evidence_files"[^>]*>/);
        const note=tag(/<textarea[^>]*data-docx-wps-field="note"[^>]*>/);
        console.log(JSON.stringify({{
          statusDisabled:/<select[^>]*data-docx-wps-field="status"[^>]*disabled/.test(html),
          checksDisabled:(html.match(/<input[^>]*data-docx-wps-check[^>]*disabled/g)||[]).length===5,
          fileDisabled:/data-docx-wps-evidence-input[^>]*disabled/.test(html),
          attestationDisabled:/data-docx-wps-attestation[^>]*disabled/.test(html),
          submitDisabled:/data-docx-engine-action="wps"[^>]*disabled/.test(html),
          reviewerReadable:/readonly/.test(reviewer)&&!/disabled/.test(reviewer)&&html.includes('王审核'),
          evidenceReadable:/readonly/.test(evidence)&&!/disabled/.test(evidence)&&html.includes('.taiji/wps-evidence/token/page.png'),
          noteReadable:/readonly/.test(note)&&!/disabled/.test(note)&&html.includes('已在 WPS 打开文档'),
        }}));
        """
    )
    assert _run_node(source) == {
        "statusDisabled": True,
        "checksDisabled": True,
        "fileDisabled": True,
        "attestationDisabled": True,
        "submitDisabled": True,
        "reviewerReadable": True,
        "evidenceReadable": True,
        "noteReadable": True,
    }


def test_wps_review_session_node_harness_gates_begin_upload_submit_and_recovers_expired_token():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    feedback_start = ui_js.index("const _docxWpsAcceptanceFeedback=new Map();")
    feedback_end = ui_js.index("function renderDocxWpsVisualAcceptanceForm", feedback_start)
    helper_start = ui_js.index("function _docxWpsAcceptanceRoot")
    helper_end = ui_js.index("async function openDocxEnginePath", helper_start)
    source = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'session-1'}}}};
        const calls=[];
        let focused='';
        let expireNext=false;
        let beginCount=0;
        function control(name,value=''){{
          return {{name,value,disabled:false,readOnly:false,checked:false,attrs:{{}},setAttribute(key,val){{this.attrs[key]=val;}},removeAttribute(key){{delete this.attrs[key];}},focus(){{focused=name;}},matches:()=>false}};
        }}
        const fields={{status:control('status','passed'),reviewer:control('reviewer','user'),note:control('note','已在 WPS 打开文档，逐页检查目录、版式、图表和分页。'),evidence_files:control('evidence_files','manual/old.png')}};
        const checks=['document_opened','layout_reviewed','content_order_reviewed','figures_reviewed','tables_reviewed'].map(value=>{{const item=control(`check:${{value}}`);item.value=value;item.checked=true;return item;}});
        const attestation=control('attestation');
        const statusNode={{id:'',dataset:{{}},textContent:''}};
        const uploadStatus={{id:'',dataset:{{}},textContent:''}};
        const reviewNode={{dataset:{{}},textContent:'',setAttribute(){{}}}};
        const qualityNode={{textContent:'passed_with_warnings'}};
        const checkGroup={{attrs:{{}},setAttribute(key,val){{this.attrs[key]=val;}},removeAttribute(key){{delete this.attrs[key];}},matches:()=>true,querySelector:()=>checks[0]}};
        const uploadBox={{attrs:{{}},setAttribute(key,val){{this.attrs[key]=val;}}}};
        const openButton=control('open');
        const submitButton=control('submit');
        const fileInput=control('file');
        fileInput.files=[{{name:'page.png',size:12,type:'image/png'}}];
        const controls=[...Object.values(fields),...checks,attestation,openButton,submitButton,fileInput];
        const root={{
          dataset:{{documentPath:'delivery/document.docx',deliveryDir:'delivery'}},isConnected:true,attrs:{{}},
          setAttribute(key,val){{this.attrs[key]=val;}},
          querySelector(selector){{
            const match=selector.match(/data-docx-wps-field=\"([^\"]+)/);
            if(match)return fields[match[1]]||null;
            if(selector==='[data-docx-wps-status]')return statusNode;
            if(selector==='[data-docx-wps-upload-status]')return uploadStatus;
            if(selector==='[data-docx-wps-review-session]')return reviewNode;
            if(selector==='[data-docx-wps-quality]')return qualityNode;
            if(selector==='[data-docx-wps-checks]')return checkGroup;
            if(selector==='[data-docx-wps-attestation]')return attestation;
            if(selector==='[data-docx-wps-evidence-upload]')return uploadBox;
            if(selector==='[data-docx-wps-evidence-input]')return fileInput;
            if(selector==='[data-docx-wps-open-document]')return openButton;
            if(selector==='[data-docx-engine-action=\"wps\"]')return submitButton;
            return null;
          }},
          querySelectorAll(selector){{
            if(selector==='[data-docx-wps-check]:checked')return checks.filter(item=>item.checked);
            if(selector==='[data-docx-wps-check]')return checks;
            if(selector==='[aria-invalid=\"true\"]')return [];
            if(selector==='button,input,select,textarea')return controls;
            return [];
          }},
          closest(selector){{if(selector==='[data-docx-wps-acceptance]')return root;return null;}},
        }};
        [openButton,submitButton,fileInput].forEach(item=>{{item.closest=(selector)=>selector==='[data-docx-wps-acceptance]'?root:null;}});
        class FormData{{constructor(){{this.entries=[];}}append(key,value,name){{this.entries.push({{key,value,name}});}}}}
        const document={{querySelectorAll:()=>[root]}};
        const api=async(path,options)=>{{
          let body;
          if(options.body instanceof FormData)body=Object.fromEntries(options.body.entries.map(item=>[item.key,item.name||item.value]));
          else body=JSON.parse(options.body);
          calls.push({{path,body}});
          if(path==='/api/docx-engine-v2/quality/wps-visual/begin'){{beginCount+=1;return {{review_token:'review-token-'+beginCount,reviewer:'王审核',opened_at:'2026-07-11T09:00:00Z',expires_at_ns:Date.parse('2026-07-11T09:15:00Z')*1e6,document_sha256:'abc123',evidence_dir:'.taiji/wps-evidence/review-token-'+beginCount}};}}
          if(path==='/api/workspace/upload')return {{path:body.path+'/'+body.file}};
          if(path==='/api/docx-engine-v2/quality/wps-visual'){{
            if(expireNext){{const error=new Error('office review token was already used');error.payload={{code:'office_review_token_used'}};throw error;}}
            return {{quality_status:'passed',quality_report:{{status:'passed',checks:[]}}}};
          }}
          throw new Error(`unexpected path ${{path}}`);
        }};
        const _docxEngineRoot=()=>null;
        const _updateDocxEngineQualityOnly=()=>{{}};
        const refreshWriteflowStatusDockForActiveSession=async()=>true;
        const showToast=()=>{{}};
        {ui_js[feedback_start:feedback_end]}
        {ui_js[helper_start:helper_end]}
        (async()=>{{
          _syncDocxWpsReviewSessionUi(root);
          const lockedBeforeBegin={{status:fields.status.disabled,note:fields.note.readOnly,checks:checks.every(item=>item.disabled),file:fileInput.disabled,attestation:attestation.disabled,submit:submitButton.disabled}};
          const blockedUpload=await uploadDocxWpsEvidenceFiles(fileInput);
          const blockedSubmit=await submitDocxWpsVisualAcceptance(submitButton);
          const callsBeforeBegin=calls.length;
          const beginResult=await openDocxWpsAcceptanceDocument(openButton);
          const reviewerAfterBegin=fields.reviewer.value;
          const oldEvidenceAfterBegin=fields.evidence_files.value;
          const readyAfterBegin={{fileDisabled:fileInput.disabled,submitDisabled:submitButton.disabled,noteDisabled:fields.note.disabled,checksDisabled:checks.some(item=>item.disabled),reviewerReadOnly:fields.reviewer.readOnly}};
          fields.status.value='failed';
          attestation.checked=true;
          rememberDocxWpsAcceptanceDraft(attestation);
          const failedWithoutEvidence=await submitDocxWpsVisualAcceptance(submitButton);
          const callsAfterFailedWithoutEvidence=calls.length;
          fields.status.value='passed';
          attestation.checked=false;
          fileInput.files=[{{name:'page.png',size:12,type:'image/png'}}];
          const uploaded=await uploadDocxWpsEvidenceFiles(fileInput);
          const callsAfterUpload=calls.length;
          const evidenceInvalidAfterUpload=fields.evidence_files.attrs['aria-invalid']||'';
          const acceptanceStatusAfterUpload=statusNode.textContent;
          checks.forEach(item=>{{item.checked=true;}});
          attestation.checked=true;
          rememberDocxWpsAcceptanceDraft(attestation);
          expireNext=true;
          const expired=await submitDocxWpsVisualAcceptance(submitButton);
          const afterExpiry={{note:fields.note.value,noteReadOnly:fields.note.readOnly,checks:checks.map(item=>item.checked),factsLocked:[fields.status,...checks,fileInput,attestation,submitButton].every(item=>item.disabled),attested:attestation.checked,evidence:fields.evidence_files.value,evidenceReadOnly:fields.evidence_files.readOnly,reviewerReadOnly:fields.reviewer.readOnly,fileDisabled:fileInput.disabled,submitDisabled:submitButton.disabled,focused,status:statusNode.textContent}};
          expireNext=false;
          const beginAgain=await openDocxWpsAcceptanceDocument(openButton);
          const resetAfterNewBegin={{checks:checks.map(item=>item.checked),evidence:fields.evidence_files.value,attested:attestation.checked,note:fields.note.value,summary:reviewNode.textContent}};
          fileInput.files=[{{name:'page.png',size:12,type:'image/png'}}];
          const uploadedAgain=await uploadDocxWpsEvidenceFiles(fileInput);
          checks.forEach(item=>{{item.checked=true;}});
          attestation.checked=true;
          rememberDocxWpsAcceptanceDraft(attestation);
          const submitted=await submitDocxWpsVisualAcceptance(submitButton);
          console.log(JSON.stringify({{
            lockedBeforeBegin,blockedUpload,blockedSubmit,callsBeforeBegin,beginResult,reviewerAfterBegin,oldEvidenceAfterBegin,readyAfterBegin,
            failedWithoutEvidence,callsAfterFailedWithoutEvidence,uploaded,callsAfterUpload,evidenceInvalidAfterUpload,acceptanceStatusAfterUpload,expired,afterExpiry,beginAgain,uploadedAgain,submitted,calls,status:statusNode.textContent,
            resetAfterNewBegin,
            afterSuccess:{{reviewSummary:reviewNode.textContent,reviewer:fields.reviewer.value,reviewerReadOnly:fields.reviewer.readOnly,attested:attestation.checked,evidence:fields.evidence_files.value,evidenceReadOnly:fields.evidence_files.readOnly,note:fields.note.value,noteReadOnly:fields.note.readOnly,fileDisabled:fileInput.disabled,submitDisabled:submitButton.disabled,statusDisabled:fields.status.disabled,checksDisabled:checks.every(item=>item.disabled)}},
          }}));
        }})();
        """
    )
    result = _run_node(source)
    assert result["lockedBeforeBegin"] == {
        "status": True,
        "note": True,
        "checks": True,
        "file": True,
        "attestation": True,
        "submit": True,
    }
    assert result["blockedUpload"] == []
    assert result["blockedSubmit"] is None
    assert result["callsBeforeBegin"] == 0
    assert result["reviewerAfterBegin"] == "王审核"
    assert result["oldEvidenceAfterBegin"] == ""
    assert result["readyAfterBegin"] == {
        "fileDisabled": False,
        "submitDisabled": False,
        "noteDisabled": False,
        "checksDisabled": False,
        "reviewerReadOnly": True,
    }
    assert result["failedWithoutEvidence"] is None
    assert result["callsAfterFailedWithoutEvidence"] == 1
    assert result["uploaded"] == [".taiji/wps-evidence/review-token-1/page.png"]
    assert result["callsAfterUpload"] == 2
    assert result["evidenceInvalidAfterUpload"] == ""
    assert "尚未提交验收" in result["acceptanceStatusAfterUpload"]
    assert [call["path"] for call in result["calls"]] == [
        "/api/docx-engine-v2/quality/wps-visual/begin",
        "/api/workspace/upload",
        "/api/docx-engine-v2/quality/wps-visual",
        "/api/docx-engine-v2/quality/wps-visual/begin",
        "/api/workspace/upload",
        "/api/docx-engine-v2/quality/wps-visual",
    ]
    assert result["calls"][0]["body"] == {
        "session_id": "session-1",
        "delivery_dir": "delivery",
        "document_path": "delivery/document.docx",
    }
    assert result["calls"][2]["body"]["review_token"] == "review-token-1"
    assert result["calls"][1]["body"]["path"] == ".taiji/wps-evidence/review-token-1"
    assert result["calls"][2]["body"]["reviewer"] == "王审核"
    assert result["calls"][2]["body"]["attested_actual_office_review"] is True
    assert result["calls"][2]["body"]["evidence_files"] == [".taiji/wps-evidence/review-token-1/page.png"]
    assert result["expired"] is None
    assert "重新打开" in result["afterExpiry"]["status"]
    assert result["afterExpiry"]["note"] == "已在 WPS 打开文档，逐页检查目录、版式、图表和分页。"
    assert result["afterExpiry"]["checks"] == [False, False, False, False, False]
    assert result["afterExpiry"]["factsLocked"] is True
    assert result["afterExpiry"]["noteReadOnly"] is True
    assert result["afterExpiry"]["reviewerReadOnly"] is True
    assert result["afterExpiry"]["evidenceReadOnly"] is True
    assert result["afterExpiry"]["attested"] is False
    assert result["afterExpiry"]["evidence"] == ""
    assert result["afterExpiry"]["fileDisabled"] is True
    assert result["afterExpiry"]["submitDisabled"] is True
    assert result["resetAfterNewBegin"]["checks"] == [False, False, False, False, False]
    assert result["resetAfterNewBegin"]["evidence"] == ""
    assert result["resetAfterNewBegin"]["attested"] is False
    assert result["resetAfterNewBegin"]["note"] == "已在 WPS 打开文档，逐页检查目录、版式、图表和分页。"
    assert "有效至" in result["resetAfterNewBegin"]["summary"]
    assert result["uploadedAgain"] == [".taiji/wps-evidence/review-token-2/page.png"]
    assert result["calls"][5]["body"]["review_token"] == "review-token-2"
    assert result["submitted"]["quality_status"] == "passed"
    assert "已成功提交" in result["afterSuccess"]["reviewSummary"]
    assert result["afterSuccess"]["reviewer"] == "王审核"
    assert result["afterSuccess"]["attested"] is True
    assert result["afterSuccess"]["evidence"] == ".taiji/wps-evidence/review-token-2/page.png"
    assert result["afterSuccess"]["fileDisabled"] is True
    assert result["afterSuccess"]["submitDisabled"] is True
    assert result["afterSuccess"]["statusDisabled"] is True
    assert result["afterSuccess"]["noteReadOnly"] is True
    assert result["afterSuccess"]["reviewerReadOnly"] is True
    assert result["afterSuccess"]["evidenceReadOnly"] is True
    assert result["afterSuccess"]["note"] == "已在 WPS 打开文档，逐页检查目录、版式、图表和分页。"
    assert result["afterSuccess"]["checksDisabled"] is True
    assert "Office 验收证据已记录" in result["status"]


def test_generic_wps_review_opens_locally_uploads_to_unique_dir_and_submits_without_token():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    feedback_start = ui_js.index("const _docxWpsAcceptanceFeedback=new Map();")
    feedback_end = ui_js.index("function renderDocxWpsVisualAcceptanceForm", feedback_start)
    helper_start = ui_js.index("function _docxWpsAcceptanceRoot")
    helper_end = ui_js.index("async function openDocxEnginePath", helper_start)
    open_start = ui_js.index("async function openDocxWpsAcceptanceDocument")
    open_end = ui_js.index("async function submitDocxWpsVisualAcceptance", open_start)
    submit_end = ui_js.index("function markDocxEngineWpsVisualAccepted", open_end)
    source = ui_js[open_start:submit_end]

    assert "_newGenericDocxWpsReviewSession" in source
    assert "'/api/file/open'" in source
    assert "path:documentPath" in source
    assert "if(mode==='generic')" in source
    assert "delete acceptance.review_token" in source

    harness = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'generic-session'}}}};
        const crypto={{randomUUID:()=> 'generic-review-uuid'}};
        const calls=[];
        function control(value=''){{return {{value,disabled:false,readOnly:false,checked:false,attrs:{{}},setAttribute(k,v){{this.attrs[k]=v;}},removeAttribute(k){{delete this.attrs[k];}},focus(){{}},matches:()=>false}};}}
        const fields={{
          status:control('passed'),reviewer:control(''),
          note:control('已在 WPS 打开文档，逐页检查目录、版式、图表和分页。'),
          evidence_files:control(''),
        }};
        const checks=['document_opened','layout_reviewed','content_order_reviewed','figures_reviewed','tables_reviewed'].map(value=>{{const item=control();item.value=value;return item;}});
        const attestation=control();
        const openButton=control();
        const submitButton=control();
        const fileInput=control();
        const statusNode={{id:'',dataset:{{}},textContent:''}};
        const uploadStatus={{dataset:{{}},textContent:''}};
        const reviewNode={{dataset:{{}},textContent:''}};
        const qualityNode={{textContent:'passed_with_warnings'}};
        const checkGroup={{disabled:false,setAttribute(){{}},removeAttribute(){{}},matches:()=>true,querySelector:()=>checks[0]}};
        const uploadBox={{setAttribute(){{}}}};
        const controls=[...Object.values(fields),...checks,attestation,openButton,submitButton,fileInput];
        const root={{
          dataset:{{docxWpsMode:'generic',documentPath:'generic/document.docx',deliveryDir:'generic'}},isConnected:true,attrs:{{}},
          setAttribute(k,v){{this.attrs[k]=v;}},getAttribute(k){{return this.attrs[k];}},
          querySelector(selector){{
            const match=selector.match(/data-docx-wps-field=\"([^\"]+)/);if(match)return fields[match[1]]||null;
            if(selector==='[data-docx-wps-status]')return statusNode;
            if(selector==='[data-docx-wps-upload-status]')return uploadStatus;
            if(selector==='[data-docx-wps-review-session]')return reviewNode;
            if(selector==='[data-docx-wps-quality]')return qualityNode;
            if(selector==='[data-docx-wps-checks]')return checkGroup;
            if(selector==='[data-docx-wps-attestation]')return attestation;
            if(selector==='[data-docx-wps-evidence-upload]')return uploadBox;
            if(selector==='[data-docx-wps-evidence-input]')return fileInput;
            if(selector==='[data-docx-wps-open-document]')return openButton;
            if(selector==='[data-docx-engine-action=\"wps\"]')return submitButton;
            if(selector==='[data-docx-wps-check][value=\"document_opened\"]')return checks[0];
            return null;
          }},
          querySelectorAll(selector){{
            if(selector==='[data-docx-wps-check]:checked')return checks.filter(item=>item.checked);
            if(selector==='[data-docx-wps-check]')return checks;
            if(selector==='[aria-invalid=\"true\"]')return [];
            if(selector==='button,input,select,textarea')return controls;
            return [];
          }},
          closest(selector){{return selector==='[data-docx-wps-acceptance]'?root:null;}},
        }};
        [openButton,submitButton,fileInput].forEach(item=>{{item.closest=(selector)=>selector==='[data-docx-wps-acceptance]'?root:null;}});
        const document={{querySelectorAll:()=>[root]}};
        class FormData{{constructor(){{this.entries=[];}}append(k,v,n){{this.entries.push({{key:k,value:v,name:n}});}}}}
        const api=async(path,options)=>{{
          const body=options.body instanceof FormData?Object.fromEntries(options.body.entries.map(item=>[item.key,item.name||item.value])):JSON.parse(options.body);
          calls.push({{path,body}});
          if(path==='/api/file/open')return {{ok:true}};
          if(path==='/api/workspace/upload')return {{path:`${{body.path}}/${{body.file}}`}};
          if(path==='/api/docx-engine-v2/quality/wps-visual')return {{quality_status:'passed',quality_report:{{status:'passed',checks:[]}}}};
          throw new Error(`unexpected ${{path}}`);
        }};
        const _docxEngineRoot=()=>null;
        const _updateDocxEngineQualityOnly=()=>{{}};
        const showToast=()=>{{}};
        {ui_js[feedback_start:feedback_end]}
        {ui_js[helper_start:helper_end]}
        (async()=>{{
          _syncDocxWpsReviewSessionUi(root);
          const locked={{status:fields.status.disabled,note:fields.note.readOnly,checks:checks.every(item=>item.disabled),file:fileInput.disabled,attestation:attestation.disabled,submit:submitButton.disabled}};
          const opened=await openDocxWpsAcceptanceDocument(openButton);
          checks.forEach(item=>{{item.checked=true;}});
          fileInput.files=[{{name:'generic-page.png',size:12,type:'image/png'}}];
          const uploaded=await uploadDocxWpsEvidenceFiles(fileInput);
          attestation.checked=true;
          rememberDocxWpsAcceptanceDraft(attestation);
          const submitted=await submitDocxWpsVisualAcceptance(submitButton);
          console.log(JSON.stringify({{
            locked,opened,uploaded,submitted,calls,
            consumed:{{status:fields.status.disabled,noteReadOnly:fields.note.readOnly,note:fields.note.value,reviewerReadOnly:fields.reviewer.readOnly,reviewer:fields.reviewer.value,evidenceReadOnly:fields.evidence_files.readOnly,evidence:fields.evidence_files.value,checks:checks.every(item=>item.disabled),file:fileInput.disabled,attestation:attestation.disabled,submit:submitButton.disabled,uploadStatus:uploadStatus.textContent}},
          }}));
        }})();
        """
    )
    result = _run_node(harness)
    assert result["locked"] == {"status": True, "note": True, "checks": True, "file": True, "attestation": True, "submit": True}
    assert result["opened"]["mode"] == "generic"
    assert result["opened"]["evidenceDir"] == ".taiji/wps-evidence/generic/generic-review-uuid"
    assert result["uploaded"] == [".taiji/wps-evidence/generic/generic-review-uuid/generic-page.png"]
    assert [call["path"] for call in result["calls"]] == ["/api/file/open", "/api/workspace/upload", "/api/docx-engine-v2/quality/wps-visual"]
    assert result["calls"][0]["body"] == {"session_id": "generic-session", "path": "generic/document.docx"}
    assert "review_token" not in result["calls"][2]["body"]
    assert result["calls"][2]["body"]["attested_actual_office_review"] is True
    assert result["submitted"]["quality_status"] == "passed"
    assert result["consumed"] == {
        "status": True,
        "noteReadOnly": True,
        "note": "已在 WPS 打开文档，逐页检查目录、版式、图表和分页。",
        "reviewerReadOnly": True,
        "reviewer": "本地人工验收",
        "evidenceReadOnly": True,
        "evidence": ".taiji/wps-evidence/generic/generic-review-uuid/generic-page.png",
        "checks": True,
        "file": True,
        "attestation": True,
        "submit": True,
        "uploadStatus": "本次本地人工验收证据已随成功提交锁定。",
    }


def test_wps_visual_acceptance_rejects_placeholder_reviewer_and_nonsemantic_note_for_every_status():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    feedback_start = ui_js.index("const _docxWpsAcceptanceFeedback=new Map();")
    feedback_end = ui_js.index("function renderDocxWpsVisualAcceptanceForm", feedback_start)
    helper_start = ui_js.index("function _docxWpsAcceptanceRoot")
    helper_end = ui_js.index("async function openDocxEnginePath", helper_start)
    source = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'session-1'}}}};
        function validate(reviewer,note,status='passed'){{
          let focused='';
          function field(name,value){{
            return {{value,attrs:{{}},setAttribute(key,val){{this.attrs[key]=val;}},removeAttribute(key){{delete this.attrs[key];}},focus(){{focused=name;}},matches:()=>false}};
          }}
          const fields={{status:field('status',status),reviewer:field('reviewer','不可手填'),note:field('note',note),evidence_files:field('evidence_files','manual/ignored.png')}};
          const checks=['document_opened','layout_reviewed','content_order_reviewed','figures_reviewed','tables_reviewed'].map(value=>({{value,setAttribute(){{}},removeAttribute(){{}}}}));
          const attestation=field('attestation','');
          attestation.checked=true;
          const statusNode={{id:'',dataset:{{}},textContent:''}};
          const checkGroup={{attrs:{{}},setAttribute(key,val){{this.attrs[key]=val;}},removeAttribute(key){{delete this.attrs[key];}},matches:()=>true,querySelector:()=>checks[0]}};
          const openButton=field('open','');
          const root={{
            dataset:{{documentPath:'delivery/document.docx',deliveryDir:'delivery'}},
            querySelector(selector){{
              const match=selector.match(/data-docx-wps-field=\"([^\"]+)/);
              if(match)return fields[match[1]]||null;
              if(selector==='[data-docx-wps-status]')return statusNode;
              if(selector==='[data-docx-wps-checks]')return checkGroup;
              if(selector==='[data-docx-wps-open-document]')return openButton;
              if(selector==='[data-docx-wps-attestation]')return attestation;
              return null;
            }},
            querySelectorAll(selector){{
              if(selector==='[data-docx-wps-check]:checked')return checks;
              if(selector==='[aria-invalid=\"true\"]')return [];
              return [];
            }},
          }};
          _docxWpsAcceptanceFeedback.set('delivery',{{
            reviewSession:{{reviewToken:'review-token',reviewer,openedAt:'2026-07-11T09:00:00Z',documentSha256:'abc123',evidenceDir:'.taiji/wps-evidence/review-token'}},
            evidencePaths:['evidence/wps-page-1.png'],
          }});
          const result=_validateDocxWpsAcceptance(root);
          return {{result,message:statusNode.textContent,focused,reviewerInvalid:fields.reviewer.attrs['aria-invalid']||'',noteInvalid:fields.note.attrs['aria-invalid']||''}};
        }}
        {ui_js[feedback_start:feedback_end]}
        {ui_js[helper_start:helper_end]}
        console.log(JSON.stringify({{
          placeholders:['user','系统','审核人'].map(value=>validate(value,'已在 WPS 打开文档，逐页检查目录、版式和图表。')),
          emptyPassed:validate('王审核',''),
          weakPassed:validate('王审核','目录、图表、图片和版式已检查'),
          emptyFailed:validate('王审核','', 'failed'),
          validPassed:validate('王审核','已在 WPS 打开文档，逐页检查目录、版式、图表和分页。'),
        }}));
        """
    )
    result = _run_node(source)
    for placeholder in result["placeholders"]:
        assert placeholder["result"] is None
        assert placeholder["focused"] == "reviewer"
        assert placeholder["reviewerInvalid"] == "true"
        assert "服务端返回的审核人不可识别" in placeholder["message"]
    for key in ("emptyPassed", "weakPassed", "emptyFailed"):
        assert result[key]["result"] is None
        assert result[key]["focused"] == "note"
        assert result[key]["noteInvalid"] == "true"
    assert "WPS/Word" in result["weakPassed"]["message"]
    assert "打开或页面" in result["weakPassed"]["message"]
    assert result["validPassed"]["result"]["note"] == "已在 WPS 打开文档，逐页检查目录、版式、图表和分页。"


def test_wps_evidence_upload_node_harness_uses_workspace_formdata_and_appends_real_returned_paths_only():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    feedback_start = ui_js.index("const _docxWpsAcceptanceFeedback=new Map();")
    feedback_end = ui_js.index("function renderDocxWpsVisualAcceptanceForm", feedback_start)
    helper_start = ui_js.index("function _docxWpsAcceptanceRoot")
    helper_end = ui_js.index("async function openDocxEnginePath", helper_start)
    source = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'session-upload'}}}};
        const calls=[];
        const evidence={{value:'manual/existing.pdf',disabled:false,setAttribute(){{}},removeAttribute(){{}},focus(){{}}}};
        const uploadStatus={{textContent:'',dataset:{{}},id:''}};
        const uploadBox={{attrs:{{}},setAttribute(key,value){{this.attrs[key]=value;}}}};
        const submit={{disabled:false,setAttribute(){{}},removeAttribute(){{}}}};
        const root={{
          dataset:{{documentPath:'delivery/document.docx',deliveryDir:'delivery'}},
          querySelector(selector){{
            if(selector==='[data-docx-wps-field="evidence_files"]')return evidence;
            if(selector==='[data-docx-wps-upload-status]')return uploadStatus;
            if(selector==='[data-docx-wps-evidence-upload]')return uploadBox;
            if(selector==='[data-docx-engine-action="wps"]')return submit;
            return null;
          }},
        }};
        const input={{
          files:[{{name:'page-1.png',size:12,type:'image/png'}},{{name:'export.pdf',size:24,type:'application/pdf'}}],
          disabled:false,value:'chosen',
          closest:(selector)=>selector==='[data-docx-wps-acceptance]'?root:null,
        }};
        class FormData{{
          constructor(){{this.entries=[];}}
          append(key,value,name){{this.entries.push({{key,value,name}});}}
        }}
        const api=async(path,options)=>{{
          const fields=Object.fromEntries(options.body.entries.map(item=>[item.key,item.name||item.value]));
          calls.push({{path,fields,headers:options.headers,timeoutMs:options.timeoutMs}});
          if(path==='/api/docx-engine-v2/quality/wps-visual')throw new Error('upload must not submit acceptance');
          return {{path:`/workspace/.taiji/wps-evidence/${{fields.file}}`}};
        }};
        const showToast=()=>{{}};
        {ui_js[feedback_start:feedback_end]}
        {ui_js[helper_start:helper_end]}
        _docxWpsAcceptanceFeedback.set('delivery',{{reviewSession:{{reviewToken:'review-token',reviewer:'王审核',openedAt:'2026-07-11T09:00:00Z',documentSha256:'abc123',evidenceDir:'.taiji/wps-evidence/review-token'}},evidencePaths:[]}});
        uploadDocxWpsEvidenceFiles(input).then(paths=>console.log(JSON.stringify({{
          paths,calls,evidence:evidence.value,status:uploadStatus.textContent,
          statusState:uploadStatus.dataset.state,inputDisabled:input.disabled,
          inputValue:input.value,submitDisabled:submit.disabled,uploadBusy:uploadBox.attrs['aria-busy'],
        }})));
        """
    )
    result = _run_node(source)
    assert result["paths"] == [
        "/workspace/.taiji/wps-evidence/page-1.png",
        "/workspace/.taiji/wps-evidence/export.pdf",
    ]
    assert result["calls"] == [
        {
            "path": "/api/workspace/upload",
            "fields": {
                "session_id": "session-upload",
                "path": ".taiji/wps-evidence/review-token",
                "file": "page-1.png",
            },
            "headers": {},
            "timeoutMs": 120000,
        },
        {
            "path": "/api/workspace/upload",
            "fields": {
                "session_id": "session-upload",
                "path": ".taiji/wps-evidence/review-token",
                "file": "export.pdf",
            },
            "headers": {},
            "timeoutMs": 120000,
        },
    ]
    assert result["evidence"].splitlines() == [
        "/workspace/.taiji/wps-evidence/page-1.png",
        "/workspace/.taiji/wps-evidence/export.pdf",
    ]
    assert "尚未提交验收" in result["status"]
    assert result["statusState"] == "success"
    assert result["inputDisabled"] is False
    assert result["inputValue"] == ""
    assert result["submitDisabled"] is False
    assert result["uploadBusy"] == "false"


def test_wps_evidence_upload_failure_preserves_only_successful_post_begin_paths_for_retry():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    feedback_start = ui_js.index("const _docxWpsAcceptanceFeedback=new Map();")
    feedback_end = ui_js.index("function renderDocxWpsVisualAcceptanceForm", feedback_start)
    helper_start = ui_js.index("function _docxWpsAcceptanceRoot")
    helper_end = ui_js.index("async function openDocxEnginePath", helper_start)
    source = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'session-upload'}}}};
        const evidence={{value:'manual/kept.pdf',disabled:false,setAttribute(){{}},removeAttribute(){{}}}};
        const uploadStatus={{textContent:'',dataset:{{}},id:''}};
        const uploadBox={{setAttribute(){{}}}};
        const submit={{disabled:false,setAttribute(){{}},removeAttribute(){{}}}};
        const root={{
          dataset:{{documentPath:'delivery/document.docx',deliveryDir:'delivery'}},
          querySelector(selector){{
            if(selector==='[data-docx-wps-field="evidence_files"]')return evidence;
            if(selector==='[data-docx-wps-upload-status]')return uploadStatus;
            if(selector==='[data-docx-wps-evidence-upload]')return uploadBox;
            if(selector==='[data-docx-engine-action="wps"]')return submit;
            return null;
          }},
        }};
        const input={{files:[{{name:'good.png',size:1,type:'image/png'}},{{name:'bad.pdf',size:1,type:'application/pdf'}}],disabled:false,value:'chosen',closest:()=>root}};
        class FormData{{constructor(){{this.entries=[];}}append(key,value,name){{this.entries.push({{key,value,name}});}}}}
        let count=0;
        const api=async()=>{{count+=1;if(count===2)throw new Error('network down');return {{path:'/workspace/.taiji/wps-evidence/good.png'}};}};
        const showToast=()=>{{}};
        {ui_js[feedback_start:feedback_end]}
        {ui_js[helper_start:helper_end]}
        _docxWpsAcceptanceFeedback.set('delivery',{{reviewSession:{{reviewToken:'review-token',reviewer:'王审核',openedAt:'2026-07-11T09:00:00Z',documentSha256:'abc123',evidenceDir:'.taiji/wps-evidence/review-token'}},evidencePaths:[]}});
        uploadDocxWpsEvidenceFiles(input).then(paths=>console.log(JSON.stringify({{paths,evidence:evidence.value,status:uploadStatus.textContent,state:uploadStatus.dataset.state,inputDisabled:input.disabled,submitDisabled:submit.disabled}})));
        """
    )
    result = _run_node(source)
    assert result["paths"] == ["/workspace/.taiji/wps-evidence/good.png"]
    assert result["evidence"].splitlines() == ["/workspace/.taiji/wps-evidence/good.png"]
    assert result["state"] == "error"
    assert "bad.pdf" in result["status"]
    assert "network down" in result["status"]
    assert result["inputDisabled"] is False
    assert result["submitDisabled"] is False


def test_wps_evidence_upload_recovers_into_authoritative_rerendered_form():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    feedback_start = ui_js.index("const _docxWpsAcceptanceFeedback=new Map();")
    feedback_end = ui_js.index("function renderDocxWpsVisualAcceptanceForm", feedback_start)
    helper_start = ui_js.index("function _docxWpsAcceptanceRoot")
    helper_end = ui_js.index("async function openDocxEnginePath", helper_start)
    source = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'session-upload'}}}};
        function makeRoot(existing){{
          const evidence={{value:existing,disabled:false,setAttribute(){{}},removeAttribute(){{}}}};
          const status={{textContent:'',dataset:{{}},id:''}};
          const box={{attrs:{{}},setAttribute(key,value){{this.attrs[key]=value;}}}};
          const submit={{disabled:false,setAttribute(){{}},removeAttribute(){{}}}};
          const fileInput={{files:[],disabled:false,value:'',closest:()=>root,setAttribute(){{}},removeAttribute(){{}}}};
          const root={{
            isConnected:true,dataset:{{documentPath:'delivery/document.docx',deliveryDir:'delivery'}},
            querySelector(selector){{
              if(selector==='[data-docx-wps-field="evidence_files"]')return evidence;
              if(selector==='[data-docx-wps-upload-status]')return status;
              if(selector==='[data-docx-wps-evidence-upload]')return box;
              if(selector==='[data-docx-engine-action="wps"]')return submit;
              if(selector==='[data-docx-wps-evidence-input]')return fileInput;
              return null;
            }},
          }};
          return {{root,evidence,status,box,submit,fileInput}};
        }}
        const oldForm=makeRoot('manual/kept.pdf');
        const newForm=makeRoot('');
        oldForm.fileInput.files=[{{name:'slow.png',size:12,type:'image/png'}}];
        oldForm.fileInput.value='chosen';
        const document={{querySelectorAll:()=>[newForm.root]}};
        class FormData{{constructor(){{this.entries=[];}}append(key,value,name){{this.entries.push({{key,value,name}});}}}}
        const api=async()=>{{oldForm.root.isConnected=false;return {{path:'/workspace/.taiji/wps-evidence/slow.png'}};}};
        const showToast=()=>{{}};
        {ui_js[feedback_start:feedback_end]}
        {ui_js[helper_start:helper_end]}
        _docxWpsAcceptanceFeedback.set('delivery',{{reviewSession:{{reviewToken:'review-token',reviewer:'王审核',openedAt:'2026-07-11T09:00:00Z',documentSha256:'abc123',evidenceDir:'.taiji/wps-evidence/review-token'}},evidencePaths:[]}});
        uploadDocxWpsEvidenceFiles(oldForm.fileInput).then(paths=>console.log(JSON.stringify({{
          paths,evidence:newForm.evidence.value,status:newForm.status.textContent,state:newForm.status.dataset.state,
          inputDisabled:newForm.fileInput.disabled,submitDisabled:newForm.submit.disabled,uploadBusy:newForm.box.attrs['aria-busy'],
        }})));
        """
    )
    result = _run_node(source)
    assert result == {
        "paths": ["/workspace/.taiji/wps-evidence/slow.png"],
        "evidence": "/workspace/.taiji/wps-evidence/slow.png",
        "status": "已上传 1 个证据文件并回填服务端返回的真实路径；尚未提交验收。",
        "state": "success",
        "inputDisabled": False,
        "submitDisabled": False,
        "uploadBusy": "false",
    }


def test_wps_visual_acceptance_node_harness_posts_real_evidence_contract_and_refreshes_quality():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    feedback_start = ui_js.index("const _docxWpsAcceptanceFeedback=new Map();")
    feedback_end = ui_js.index("function renderDocxWpsVisualAcceptanceForm", feedback_start)
    helper_start = ui_js.index("function _docxWpsAcceptanceRoot")
    helper_end = ui_js.index("async function openDocxEnginePath", helper_start)
    source = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'session-1'}}}};
        const calls=[];
        const feedback=[];
        const quality=[];
        const attrs={{}};
        function field(value){{return {{value,disabled:false,setAttribute(){{}},removeAttribute(){{}},focus(){{}}}};}}
        const fields={{
          status:field('passed'),reviewer:field('user'),note:field('已在 WPS 打开文档，逐页检查目录、版式、图表和分页'),
          evidence_files:field('evidence/wps-page-1.png\\nevidence/wps-export.pdf'),
        }};
        const checks=['document_opened','layout_reviewed','content_order_reviewed','figures_reviewed','tables_reviewed'].map(value=>({{value,disabled:false,setAttribute(){{}},removeAttribute(){{}}}}));
        const attestation=field('');
        attestation.checked=true;
        const statusNode={{id:'',dataset:{{}},textContent:''}};
        const qualityNode={{textContent:''}};
        const checkGroup={{setAttribute(){{}},removeAttribute(){{}},matches:()=>true,querySelector:()=>checks[0]}};
        const openButton={{disabled:false,setAttribute(){{}},removeAttribute(){{}},focus(){{}}}};
        const submitButton={{disabled:false,setAttribute(){{}},removeAttribute(){{}}}};
        const controls=[...Object.values(fields),...checks,attestation,openButton,submitButton];
        const root={{
          dataset:{{documentPath:'delivery/document.docx',deliveryDir:'delivery'}},isConnected:true,
          setAttribute:(key,value)=>{{attrs[key]=value;}},
          querySelector:(selector)=>{{
            const match=selector.match(/data-docx-wps-field=\"([^\"]+)/);
            if(match)return fields[match[1]]||null;
            if(selector==='[data-docx-wps-status]')return statusNode;
            if(selector==='[data-docx-wps-quality]')return qualityNode;
            if(selector==='[data-docx-wps-checks]')return checkGroup;
            if(selector==='[data-docx-wps-open-document]')return openButton;
            if(selector==='[data-docx-wps-attestation]')return attestation;
            if(selector==='[data-docx-engine-action=\"wps\"]')return submitButton;
            return null;
          }},
          querySelectorAll:(selector)=>{{
            if(selector==='[data-docx-wps-check]:checked')return checks;
            if(selector==='[aria-invalid=\"true\"]')return [];
            if(selector==='button,input,select,textarea')return controls;
            return [];
          }},
          closest:(selector)=>selector==='[data-docx-wps-acceptance]'?root:null,
        }};
        const button={{closest:(selector)=>selector==='[data-docx-wps-acceptance]'?root:null}};
        const document={{querySelectorAll:()=>[root]}};
        const api=async(path,options)=>{{calls.push({{path,payload:JSON.parse(options.body)}});return {{quality_status:'passed',quality_report:{{status:'passed',checks:[]}}}};}};
        const _docxEngineRoot=()=>null;
        const _updateDocxEngineQualityOnly=()=>quality.push('workbench');
        const refreshWriteflowStatusDockForActiveSession=async()=>true;
        const showToast=(message)=>feedback.push(message);
        {ui_js[feedback_start:feedback_end]}
        {ui_js[helper_start:helper_end]}
        _docxWpsAcceptanceFeedback.set('delivery',{{
          reviewSession:{{reviewToken:'review-token',reviewer:'王审核',openedAt:'2026-07-11T09:00:00Z',documentSha256:'abc123',evidenceDir:'.taiji/wps-evidence/review-token'}},
          evidencePaths:['evidence/wps-page-1.png','evidence/wps-export.pdf'],
        }});
        submitDocxWpsVisualAcceptance(button).then(result=>console.log(JSON.stringify({{
          calls,
          result,
          status:statusNode.textContent,
          statusState:statusNode.dataset.state,
          quality:qualityNode.textContent,
          ariaBusy:attrs['aria-busy'],
          feedback,
        }})));
        """
    )
    result = _run_node(source)
    assert result["calls"] == [
        {
            "path": "/api/docx-engine-v2/quality/wps-visual",
            "payload": {
                "session_id": "session-1",
                "delivery_dir": "delivery",
                "status": "passed",
                "reviewer": "王审核",
                "note": "已在 WPS 打开文档，逐页检查目录、版式、图表和分页",
                "visual_checks": [
                    "document_opened",
                    "layout_reviewed",
                    "content_order_reviewed",
                    "figures_reviewed",
                    "tables_reviewed",
                ],
                "evidence_files": ["evidence/wps-page-1.png", "evidence/wps-export.pdf"],
                "review_token": "review-token",
                "attested_actual_office_review": True,
            },
        }
    ]
    assert result["result"]["quality_status"] == "passed"
    assert result["statusState"] == "success"
    assert result["quality"] == "passed"
    assert result["ariaBusy"] == "false"
    assert "Office 验收证据已记录" in result["status"]


def test_expert_team_final_review_node_harness_renders_one_discoverable_office_acceptance_form():
    source = textwrap.dedent(
        """
        const fs=require('fs');
        const vm=require('vm');
        const context={
          window:{},console,
          renderDocxWpsVisualAcceptanceForm:(payload)=>`<section data-docx-wps-acceptance="1" data-document="${payload.documentPath}" data-delivery="${payload.deliveryDir}">Office 最终验收</section>`,
        };
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/expert-team-ui.js','utf8'),context);
        function render(readOnly){
          return context.window.renderExpertTeamWorkspaceFromPresentation({
            runId:'run-1',sourceSessionId:'session-1',schemaVersion:2,version:4,readOnly,
            currentStageId:'delivery',presentation:{state:'awaiting_review',title:'交付确认',secondaryActions:[]},
            workflow:{currentStage:{id:'delivery',phase:'交付确认'},stages:[],progress:{}},workspace:{},members:[],
            artifacts:[
              {kind:'final_document',path:'delivery/document.docx',exists:true,status:'ready'},
              {kind:'delivery_package',path:'delivery',exists:true,status:'ready'},
              {kind:'quality_report',path:'delivery/quality-report.json',exists:true,status:'passed_with_warnings'},
            ],
          });
        }
        const editable=render(false);
        const readOnly=render(true);
        console.log(JSON.stringify({
          formCount:(editable.match(/data-docx-wps-acceptance/g)||[]).length,
          hasReviewEntry:editable.includes('openExpertTeamWpsAcceptance'),
          hasDocument:editable.includes('delivery/document.docx'),
          hasDelivery:editable.includes('data-delivery="delivery"'),
          readOnlyHasForm:readOnly.includes('data-docx-wps-acceptance'),
        }));
        """
    )
    assert _run_node(source) == {
        "formCount": 1,
        "hasReviewEntry": True,
        "hasDocument": True,
        "hasDelivery": True,
        "readOnlyHasForm": False,
    }


def test_structured_office_drawer_has_progressive_disclosure_and_responsive_single_scroll_surface():
    ui = (REPO_ROOT / "static" / "expert-team-ui.js").read_text(encoding="utf-8")
    actions = (REPO_ROOT / "static" / "expert-team-actions.js").read_text(encoding="utf-8")
    css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")
    for token in (
        "expert-team-office-summary", "expert-team-office-drawer", "expert-team-office-scroll",
        'role="dialog"', 'aria-modal="true"', "data-office-live", "data-office-checklist",
    ):
        assert token in ui
    for token in ("officeDrawerIsDirty", "handleExpertTeamOfficeDrawerKeydown", "officeRevisionMutationPayload"):
        assert token in actions
    for selector in (".expert-team-office-summary", ".expert-team-office-drawer", ".expert-team-office-scroll"):
        assert selector in css
    assert "@media (max-width: 720px)" in css
