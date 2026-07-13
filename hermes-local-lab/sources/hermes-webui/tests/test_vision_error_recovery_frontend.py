"""Frontend contract for recoverable image-analysis failures."""

from pathlib import Path
import re


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
