"""Frontend contract tests for the unified image capability center."""

import re
from pathlib import Path


ROOT = Path(__file__).parent.parent
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")


def _marked_slice(start: str, end: str) -> str:
    start_index = INDEX_HTML.find(start)
    end_index = INDEX_HTML.find(end)
    assert start_index >= 0, f"missing marker: {start}"
    assert end_index > start_index, f"missing marker: {end}"
    return INDEX_HTML[start_index:end_index]


def test_center_exposes_two_visible_accessible_capability_cards():
    center = _marked_slice(
        "<!-- image-capability-center:start -->",
        "<!-- image-capability-center:end -->",
    )

    assert 'id="imageCapabilityCenter"' in center
    assert 'data-image-capability="vision"' in center
    assert 'data-image-capability="image_generation"' in center
    assert center.count('role="switch"') == 2
    assert 'for="imageCapabilityVisionEnabled"' in center
    assert 'for="imageCapabilityGenerationEnabled"' in center
    assert 'id="imageCapabilityCenterStatus"' in center
    assert 'role="status"' in center
    assert 'aria-live="polite"' in center


def test_center_separates_configured_state_from_effective_route():
    center = _marked_slice(
        "<!-- image-capability-center:start -->",
        "<!-- image-capability-center:end -->",
    )

    for element_id in (
        "imageCapabilityVisionConfigStatus",
        "imageCapabilityVisionRoute",
        "imageCapabilityGenerationConfigStatus",
        "imageCapabilityGenerationRoute",
        "imageCapabilityVisionVerification",
        "imageCapabilityGenerationVerification",
    ):
        assert f'id="{element_id}"' in center
    assert "配置状态" in center
    assert "实际路由" in center


def test_vision_toggle_discloses_that_native_main_model_vision_stays_available():
    center = _marked_slice(
        "<!-- image-capability-center:start -->",
        "<!-- image-capability-center:end -->",
    )
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    assert "启用辅助图片理解" in center
    assert "关闭后不再调用辅助识图 Provider" in center
    assert "主模型原生支持图片时仍会直接处理" in center
    assert "主模型原生识图不受影响" in runtime
    assert "辅助识图与图片生成均未启用" in runtime


def test_center_has_one_primary_save_and_verify_action():
    center = _marked_slice(
        "<!-- image-capability-center:start -->",
        "<!-- image-capability-center:end -->",
    )

    assert center.count('id="btnSaveVerifyImageCapabilityCenter"') == 1
    assert "保存并验证" in center
    assert 'aria-busy="false"' in center
    assert "生图验证" in center
    assert "费用" in center


def test_runtime_uses_one_adjustable_api_base_and_current_protocol_only():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    assert (
        runtime.count("const IMAGE_CAPABILITIES_API='/api/image-capabilities';")
        == 1
    )
    assert "IMAGE_CAPABILITIES_API+'/configure'" in runtime
    assert "'/api/image-capabilities/configure'" not in runtime
    for forbidden in (
        "message.image_artifacts",
        "/api/image-artifacts",
        "file://artifact",
    ):
        assert forbidden not in runtime


def test_runtime_renders_server_driven_provider_auth_credentials_and_endpoints():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    for contract_field in (
        "provider_family",
        "provider_ids",
        "auth_type",
        "supports_named_credentials",
        "credential_fields",
        "endpoint_fields",
        "provider_credentials",
        "effective_route",
    ):
        assert contract_field in runtime


def test_visible_credential_fields_follow_non_ali_provider_selection_contract():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )
    provider_change = runtime[
        runtime.index("function imageCapabilityMarkDraftChanged")
        : runtime.index("async function imageCapabilityConfirmReload")
    ]
    credential_fields = runtime[
        runtime.index("function imageCapabilityRenderCredentialFields")
        : runtime.index("function imageCapabilityFieldValue")
    ]

    assert "imageCapabilityProviderRow(imageCapabilityData,capability,target.value)" in provider_change
    assert "imageCapabilityRenderCredentialFields(capability,row,'')" in provider_change
    assert "row.supports_named_credentials" in credential_fields
    assert "imageCapabilityNormalizeFields(row,'credential_fields',capability)" in credential_fields
    for ali_only_guard in (
        "provider==='alibaba'",
        "provider==='dashscope'",
        "row.provider_family==='alibaba_dashscope'",
    ):
        assert ali_only_guard not in credential_fields


def test_runtime_treats_canonical_and_legacy_no_auth_as_credential_free():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    assert "function imageCapabilityIsNoAuth(authType)" in runtime
    assert "authType==='no_auth'||authType==='none'" in runtime.replace(" ", "")
    assert "imageCapabilityIsNoAuth(authType)" in runtime


def test_new_credential_ids_are_random_draft_scoped_and_retry_stable():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    assert "imageCapabilityCredentialDraftIds" in runtime
    assert "function imageCapabilityCredentialDraftId(capability,row)" in runtime
    assert "imageCapabilityRequestId()" in runtime
    assert "managed_by:'image-capability-center'" in runtime
    assert "source_capability:capability" in runtime
    assert "source_provider_id:imageCapabilityProviderId(row,capability)" in runtime
    assert "operation:'create'" in runtime
    assert "id:'ui-'+family+'-'+suffix" not in runtime


def test_runtime_renders_provider_auth_guidance_only_once_per_card():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )
    start = runtime.index("function imageCapabilityRenderCredentialFields")
    end = runtime.index("function imageCapabilityFieldValue", start)
    credential_fields_source = runtime[start:end]

    assert "note.textContent=imageCapabilityAuthLabel(row)" not in credential_fields_source
    assert "imageCapabilityText(meta.prefix+'Auth',imageCapabilityAuthLabel(selectedRow))" in runtime


def test_generation_route_is_rendered_from_server_effective_route_only():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )
    generation_route = runtime[
        runtime.index("function imageCapabilityGenerationRouteLabel")
        : runtime.index("function imageCapabilityRenderCapability")
    ]

    assert "function imageCapabilityGenerationRouteLabel(data)" in generation_route
    assert "effective_route" in generation_route
    assert "image_generation" in generation_route
    assert "config.verification" not in generation_route
    assert "imageCapabilityGenerationRouteLabel(imageCapabilityData)" in runtime


def test_runtime_guards_busy_and_stale_operations():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    assert "imageCapabilityOperationGeneration" in runtime
    assert re.search(r"operation\s*!==\s*imageCapabilityOperationGeneration", runtime)
    assert "draftIdentity" in runtime
    assert "aria-busy" in runtime
    assert ".disabled=busy" in runtime.replace(" ", "")
    assert "本次结果已忽略" in runtime


def test_runtime_sends_revision_and_reuses_one_idempotency_key_per_draft():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    assert "expected_revision" in runtime
    assert "request_id" in runtime
    assert "imageCapabilityPendingRequest" in runtime
    assert "imageCapabilityRequestInFlight" in runtime
    assert "crypto.randomUUID" in runtime
    assert "configuration_conflict" in runtime
    assert "配置已被其他窗口更新" in runtime
    assert "btnReloadImageCapabilityCenter" in INDEX_HTML


def test_refresh_guards_inflight_requests_and_unsaved_image_drafts():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    assert "imageCapabilityLoadInFlight" in runtime
    assert "imageCapabilityDirty" in runtime
    assert "imageCapabilityBaselineIdentity" in runtime
    assert "imageCapabilityConfirmReload" in runtime
    assert "refreshModelAndImageCapabilities" in runtime
    assert "图片能力有未保存更改" in runtime
    assert "imageCapabilityRequestInFlight||imageCapabilityLoadInFlight" in runtime
    assert 'id="btnReloadAllModelConfig"' in INDEX_HTML
    assert 'onclick="refreshModelAndImageCapabilities()"' in INDEX_HTML


def test_model_config_secret_helpers_do_not_touch_image_capability_center():
    for function_name in (
        "function _modelConfigAnySecretDraft()",
        "function _modelConfigDraftIdentity()",
        "function _clearModelConfigSecrets(scope,providerId)",
    ):
        start = PANELS_JS.index(function_name)
        end = PANELS_JS.index("\n}\n", start) + 2
        function_source = PANELS_JS[start:end]
        assert "closest('#imageCapabilityCenter')" in function_source


def test_runtime_recognizes_real_api_conflicts_and_allows_long_verification():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    assert "error.payload&&error.payload.error_code" in runtime
    assert "timeoutMs:195000" in runtime
    assert "请求可能仍在服务器处理中" in runtime
    assert "请勿刷新或修改配置" in runtime


def test_runtime_never_reports_a_superseded_verification_as_completed():
    runtime = _marked_slice(
        "/* image-capability-center-runtime:start */",
        "/* image-capability-center-runtime:end */",
    )

    assert "request_status" in runtime
    assert "status==='superseded'" in runtime
    assert "本请求已被较新配置取代" in runtime
    assert "未执行旧配置验证" in runtime
    assert "当前显示为最新服务器状态" in runtime


def test_center_has_responsive_and_reduced_motion_guards():
    styles = _marked_slice(
        "/* image-capability-center-styles:start */",
        "/* image-capability-center-styles:end */",
    )

    assert "@media (max-width: 720px)" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert ".image-capability-center-grid" in styles
    assert "min-width:0" in styles.replace(" ", "")
