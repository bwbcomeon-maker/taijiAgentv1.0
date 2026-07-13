"""Frontend contract for recoverable image-analysis failures."""

from pathlib import Path
import json
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MESSAGES = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
UI = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")


VISION_TYPES = (
    "vision_analysis_error",
    "vision_configuration_error",
    "image_attachment_error",
)


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source)
    assert match, f"{name}() not found"
    # Project functions close at column zero; nested callbacks close indented.
    # This avoids treating braces in regex literals and template expressions as syntax.
    end = source.find("\n}", match.end())
    assert end >= 0, f"unterminated {name}()"
    return source[match.start() : end + 2]


def test_typed_vision_errors_render_dedicated_chinese_recovery_card():
    handler = MESSAGES[MESSAGES.index("source.addEventListener('apperror'") :]
    for error_type in VISION_TYPES:
        assert error_type in MESSAGES
    assert "vision_recovery" in handler
    vision_branch = handler[handler.index("else if(isVisionFailure)") : handler.index("} else {", handler.index("else if(isVisionFailure)"))]
    assert "d.message" not in vision_branch
    assert "d.details" not in vision_branch

    assert "vision-recovery-card" in UI
    assert "重试识图" in UI
    assert "打开识图配置" in UI
    assert 'role="alert"' in UI
    assert 'type="button"' in UI


def test_retry_path_reuses_descriptors_without_uploading_or_persisting_them():
    send_body = _function_body(MESSAGES, "send")
    assert "retryAttachments" in send_body
    assert re.search(r"retryAttachments[^;]+uploadPendingFiles", send_body, re.S)
    assert "attachLiveStream(activeSid, streamId, uploaded" in send_body
    assert "retryText:text" in send_body

    retry_body = _function_body(MESSAGES, "retryVisionAnalysis")
    assert "retryText" in retry_body
    assert "retryAttachments" in retry_body
    assert "send(" in retry_body
    assert "uploadPendingFiles" not in retry_body

    store_body = _function_body(MESSAGES, "_storeVisionRecovery")
    assert "localStorage" not in store_body
    assert "sessionStorage" not in store_body
    assert "JSON.stringify" not in store_body

    # Persisted INFLIGHT snapshots must continue to receive safe filenames only.
    assert re.search(r"saveInflightState\([^;]+uploaded:uploadedNames", send_body, re.S)
    assert "uploaded:uploaded," not in send_body


def test_recovery_card_never_renders_descriptor_paths_and_settings_is_reachable():
    card_body = _function_body(UI, "_visionRecoveryCardHtml")
    assert ".path" not in card_body
    assert "attachments" not in card_body
    assert "retryVisionAnalysis" in card_body
    assert "openVisionRecognitionSettings" in card_body
    assert "add(m.vision_recovery" in UI

    settings_body = _function_body(MESSAGES, "openVisionRecognitionSettings")
    assert "switchSettingsSection('models')" in settings_body
    assert "toggleModelConfigSection('visionConfigEdit',true)" in settings_body


def test_persisted_vision_error_rehydrates_recovery_actions_after_reload():
    hydrate_body = _function_body(MESSAGES, "_hydratePersistedVisionRecoveries")
    assert "error_type" in hydrate_body
    assert "_VISION_RECOVERY_TYPES" in hydrate_body
    assert "_storeVisionRecovery" in hydrate_body
    assert "attachments" in hydrate_body

    render_body = _function_body(UI, "renderMessages")
    hydrate_pos = render_body.index("_hydratePersistedVisionRecoveries")
    signature_pos = render_body.index("_messageRenderCacheSignature")
    assert hydrate_pos < signature_pos


def test_retry_is_guarded_against_repeat_clicks_and_transient_state_is_pruned():
    retry_body = _function_body(MESSAGES, "retryVisionAnalysis")
    assert "button.disabled=true" in retry_body
    assert "aria-busy" in retry_body
    assert "button.disabled=false" in retry_body
    assert "finally" in retry_body

    assert "_retainVisionRecoveryForSession" in UI
    assert "_deleteVisionRecovery" in _function_body(MESSAGES, "send")
    retain_body = _function_body(MESSAGES, "_retainVisionRecoveryForSession")
    assert "clearMessageRenderCache" in retain_body


def test_busy_vision_retry_preserves_composer_and_pending_files_without_side_effects():
    send_source = _function_body(MESSAGES, "send")
    driver = f"""
{send_source}
let S;
let _sendInProgress=false;
let _sendInProgressSid=null;
let composer;
let calls;
const document={{querySelector:()=>null}};
const window={{_busyInputMode:'queue'}};
const $=()=>composer;
const _dismissHandoffHint=()=>{{}};
const isCompressionUiRunning=()=>false;
const _clearStaleBusyStateBeforeSend=()=>false;
const _chatPayloadModelState=()=>({{model:'test-model',model_provider:'test-provider'}});
const newSession=async()=>{{calls.push('newSession')}};
const renderSessionList=async()=>{{calls.push('renderSessionList')}};
const parseCommand=()=>null;
const COMMANDS=[];
const _trySteer=async()=>{{calls.push('steer')}};
const queueSessionMessage=()=>{{calls.push('queue')}};
const updateQueueBadge=()=>{{calls.push('queueBadge')}};
const autoResize=()=>{{calls.push('resize')}};
const renderTray=()=>{{calls.push('tray')}};
const cancelStream=async()=>{{calls.push('cancel')}};
const showToast=()=>{{calls.push('toast')}};
const t=key=>key;
const api=async()=>{{calls.push('api');return {{stream_id:'unexpected'}}}};
const uploadPendingFiles=async()=>{{calls.push('upload');return []}};

async function run(mode){{
  const pending={{name:'new-draft.png'}};
  composer={{value:'CURRENT DRAFT'}};
  calls=[];
  window._busyInputMode=mode;
  S={{
    busy:true,
    pendingFiles:[pending],
    session:{{session_id:'session-1',profile:'default'}},
    messages:[],
    activeStreamId:'live-stream',
    activeProfile:'default',
  }};
  const accepted=await send({{
    retryText:'original image question',
    retryAttachments:[{{name:'old.png',path:'/private/server/old.png'}}],
    recoveryId:'recovery-1',
  }});
  return {{
    mode,
    accepted,
    draft:composer.value,
    pendingLength:S.pendingFiles.length,
    pendingSame:S.pendingFiles[0]===pending,
    calls,
  }};
}}

(async()=>{{
  const result=[];
  for(const mode of ['queue','steer','interrupt']){{
    _sendInProgress=false;
    _sendInProgressSid=null;
    result.push(await run(mode));
  }}
  console.log(JSON.stringify(result));
}})();
"""
    result = subprocess.run(
        ["node", "-e", driver],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    cases = json.loads(result.stdout)
    for case in cases:
        assert case.get("accepted") is False
        assert case["draft"] == "CURRENT DRAFT"
        assert case["pendingLength"] == 1
        assert case["pendingSame"] is True
        assert "toast" in case["calls"]
        assert not ({"api", "upload", "queue", "queueBadge", "steer", "cancel", "resize", "tray"} & set(case["calls"]))
