import json
from pathlib import Path
from types import SimpleNamespace

from api.docx_engine_v2 import _known_failure_payload
from api.gateway_chat import _gateway_run_error_event, _gateway_sse_error_event
from api.product_contract import ERROR_SCHEMA, attach_product_error
from api.streaming import _provider_error_payload


ROOT = Path(__file__).resolve().parents[1]


def _assert_safe_envelope(payload: dict, code: str) -> None:
    envelope = payload["product_error"]
    assert envelope["schema"] == ERROR_SCHEMA
    assert envelope["code"] == code
    assert envelope["incident_id"].startswith("inc-")
    rendered = json.dumps(envelope, ensure_ascii=False)
    for forbidden in ("/Users/", "C:\\Users\\", "sentinel", "Traceback", "Hermes"):
        assert forbidden not in rendered


def test_attach_product_error_preserves_legacy_contract():
    legacy = {"type": "model_not_found", "message": "legacy", "run": {"version": 7}}

    mapped = attach_product_error(legacy, "model_configuration_required", incident_id="inc-0123456789ab")

    assert {key: mapped[key] for key in legacy} == legacy
    _assert_safe_envelope(mapped, "model_configuration_required")


def test_provider_and_gateway_errors_keep_old_fields_and_add_safe_mapping():
    provider = _provider_error_payload(
        "model missing at /Users/alice/config with API_TOKEN=sentinel",
        "model_not_found",
        "open settings",
    )
    gateway_model = _gateway_sse_error_event({
        "error": {"code": "model_configuration_error", "message": "missing API key sentinel"}
    })
    gateway_backend = _gateway_sse_error_event({
        "error": {"code": "upstream_failed", "message": "Traceback /Users/alice/private"}
    })

    assert provider["type"] == "model_not_found"
    assert provider["hint"] == "open settings"
    _assert_safe_envelope(provider, "model_configuration_required")
    assert gateway_model["type"] == "model_configuration_error"
    _assert_safe_envelope(gateway_model, "model_configuration_required")
    assert gateway_backend["type"] == "gateway_error"
    _assert_safe_envelope(gateway_backend, "backend_unavailable")


def test_docx_failure_keeps_recovery_evidence_and_adds_artifact_mapping():
    legacy = {
        "code": "validation_failed",
        "message": "validation failed",
        "stage": "validation",
        "failures": ["missing field"],
        "jobManifestPath": "/workspace/delivery/job.manifest.json",
        "failureReportPath": "/workspace/delivery/failure-report.json",
    }

    mapped = _known_failure_payload(legacy)

    assert mapped["code"] == "validation_failed"
    assert mapped["stage"] == "validation"
    assert mapped["job_manifest_path"].endswith("job.manifest.json")
    assert mapped["failure_report_path"].endswith("failure-report.json")
    _assert_safe_envelope(mapped, "artifact_generation_failed")


def test_routes_map_permission_license_backend_and_office_without_replacing_legacy_fields():
    source = (ROOT / "api/routes.py").read_text(encoding="utf-8")

    assert 'attach_product_error({"error": str(e)}, "permission_denied")' in source
    assert 'response = attach_product_error(response, "license_blocked")' in source
    assert '}, "license_blocked"), 403' in source
    assert 'code = "backend_unavailable" if int(status) >= 500 else "unknown_error"' in source
    assert '_expert_team_response_with_product_state(payload, run)' in source
    assert 'attach_product_error(payload, "office_review_required")' in source


def test_office_review_mapping_keeps_successful_http_payload_shape():
    from api.routes import _expert_team_response_with_product_state

    legacy = {"ok": True, "run": {"run_id": "run-1"}, "teams": []}
    mapped = _expert_team_response_with_product_state(
        legacy,
        {"validation": {"status": "office_acceptance_required"}},
    )

    assert mapped["ok"] is True
    assert mapped["run"] == {"run_id": "run-1"}
    _assert_safe_envelope(mapped, "office_review_required")


def test_unknown_http_errors_keep_compatibility_alias_and_incident_log_link():
    source = (ROOT / "server.py").read_text(encoding="utf-8")

    assert source.count('build_product_error("unknown_error")') == 2
    assert source.count("'product_error': product_error") == 2
    assert source.count('incident_id={product_error["incident_id"]}') == 2


def test_rest_and_sse_clients_prefer_allowlisted_product_copy():
    workspace = (ROOT / "static/workspace.js").read_text(encoding="utf-8")
    messages = (ROOT / "static/messages.js").read_text(encoding="utf-8")

    assert "_safeProductErrorEnvelope({payload})" in workspace
    assert "productError.title" in workspace
    assert "productError.message" in workspace
    assert "_safeProductErrorEnvelope({payload:d})" in messages
    assert "productError?productError.title" in messages
    assert "productError?productError.message" in messages
    assert "productError?'':" in messages


def test_license_blocked_chat_turn_prefers_allowlisted_product_copy():
    messages = (ROOT / "static/messages.js").read_text(encoding="utf-8")
    start = messages.index("if(startData.license_blocked)")
    block = messages[start : messages.index("if(startData.title) applySessionTitleUpdate", start)]

    assert "_safeProductErrorEnvelope({payload:startData})" in block
    assert "productError.title" in block
    assert "productError.message" in block


def test_managed_gateway_fallback_and_empty_response_are_product_mapped():
    mapped = _gateway_run_error_event({"error": "Traceback /Users/alice/private"})
    source = (ROOT / "api/gateway_chat.py").read_text(encoding="utf-8")

    _assert_safe_envelope(mapped, "backend_unavailable")
    empty_start = source.index("if not internal_assistant_text:")
    empty = source[empty_start : source.index("with _get_session_agent_lock", empty_start)]
    assert 'record_turn_interrupted("gateway_empty_response")' in empty
    assert 'attach_product_error({' in empty
    assert '}, "backend_unavailable")' in empty


def test_expert_runtime_dispatch_failures_use_backend_product_mapping():
    source = (ROOT / "api/routes.py").read_text(encoding="utf-8")
    block = source[source.index("result = adapter.start_run(") : source.index("response[\"run\"] = updated_run")]

    assert block.count("_backend_failure(") >= 4
    assert block.count("_execution_failure(") >= 2
    assert 'code = "backend_unavailable" if int(status) >= 500 else "unknown_error"' in source
    assert '"code": "start_pending"' in block
    assert 'getattr(exc, "code", "start_commit_failed")' in block


def test_docx_process_start_failure_returns_artifact_product_error(monkeypatch, tmp_path):
    from api import routes

    monkeypatch.setattr(
        routes,
        "get_session_for_file_ops",
        lambda _session_id: SimpleNamespace(workspace=str(tmp_path)),
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: {"status": status, "payload": payload},
    )
    monkeypatch.setattr(
        routes.docx_engine_v2,
        "create_job",
        lambda _payload, _workspace: (_ for _ in ()).throw(FileNotFoundError("/Users/alice/private/node")),
    )

    result = routes._handle_docx_engine_v2_create_job(
        object(),
        {"session_id": "sid-1", "template_id": "general-proposal", "source_path": "source.md"},
    )

    assert result["status"] == 400
    assert result["payload"]["error"] == "<path>"
    _assert_safe_envelope(result["payload"], "artifact_generation_failed")
