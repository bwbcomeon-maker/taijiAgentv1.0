import json
import logging


from api.product_contract import (
    ERROR_SCHEMA,
    build_product_error,
    safe_public_text,
)


def test_product_error_envelope_is_stable_and_actionable():
    payload = build_product_error("agent_unavailable", incident_id="inc-0123456789ab")

    assert payload == {
        "schema": ERROR_SCHEMA,
        "code": "agent_unavailable",
        "title": "本地服务暂不可用",
        "message": "太极智能体尚未准备完成，请稍后重试。",
        "recovery_actions": [
            {"id": "retry", "label": "重试"},
            {"id": "restart_app", "label": "重启应用"},
            {"id": "export_diagnostics", "label": "导出诊断"},
        ],
        "incident_id": "inc-0123456789ab",
        "retryable": True,
    }


def test_unknown_error_uses_safe_generic_copy():
    payload = build_product_error("attacker_code", incident_id="invalid /Users/alice/.env")

    assert payload["code"] == "unknown_error"
    assert payload["incident_id"].startswith("inc-")
    assert "/Users/" not in json.dumps(payload, ensure_ascii=False)
    assert payload["recovery_actions"][-1]["id"] == "export_diagnostics"


def test_public_text_redacts_paths_secrets_env_and_internal_brand():
    raw = (
        "Hermes couldn't start at /Users/alice/private/config.yaml\n"
        "C:\\Users\\alice\\secret.txt API_TOKEN=sentinel-token "
        "Authorization: Bearer sentinel-bearer password=sentinel-password sk-sentinel-key"
    )

    cleaned = safe_public_text(raw)

    for sentinel in (
        "Hermes",
        "/Users/alice",
        "C:\\Users\\alice",
        "sentinel-token",
        "sentinel-bearer",
        "sentinel-password",
        "sk-sentinel-key",
        "API_TOKEN",
    ):
        assert sentinel not in cleaned
    assert "\n" not in cleaned
    assert len(cleaned) <= 240


def test_recovery_actions_are_allowlisted_objects_only():
    payload = build_product_error("license_blocked")

    assert {item["id"] for item in payload["recovery_actions"]} == {
        "open_license",
        "export_diagnostics",
    }
    assert all(set(item) == {"id", "label"} for item in payload["recovery_actions"])


def test_public_text_redacts_json_quoted_values_and_brand_substrings():
    raw = (
        'password="sentinel secret" '
        '{"password":"sentinel-json","api_token":"sentinel-json-token"} '
        "HermesAgent-1.0 "
        "C:\\Users\\Alice Smith\\private-secret.txt "
        "OPENAI_API_KEY = sentinel key "
        "Traceback (most recent call last): internal frame"
    )

    cleaned = safe_public_text(raw)

    for forbidden in (
        "sentinel", "password", "api_token", "Hermes", "Alice Smith",
        "private-secret", "OPENAI_API_KEY", "Traceback", "internal frame",
    ):
        assert forbidden.lower() not in cleaned.lower()


def test_public_text_redacts_each_spaced_path_env_and_traceback_shape():
    samples = {
        r"C:\Users\Alice Smith\private-secret.txt": ("Alice Smith", "private-secret"),
        "/Users/Alice Smith/private-secret.txt": ("Alice Smith", "private-secret"),
        "OPENAI_API_KEY = sentinel key": ("OPENAI_API_KEY", "sentinel", " key"),
        "Traceback (most recent call last): internal frame": ("Traceback", "internal frame"),
    }

    for raw, forbidden_values in samples.items():
        cleaned = safe_public_text(raw)
        for forbidden in forbidden_values:
            assert forbidden.lower() not in cleaned.lower()


def test_error_catalog_covers_product_failure_categories():
    expected_actions = {
        "model_configuration_required": "open_model_settings",
        "backend_unavailable": "restart_app",
        "permission_denied": "open_security_settings",
        "license_blocked": "open_license",
        "artifact_generation_failed": "retry",
        "office_review_required": "open_office_review",
        "unknown_error": "export_diagnostics",
    }

    for code, action in expected_actions.items():
        payload = build_product_error(code)
        assert payload["code"] == code
        assert action in {item["id"] for item in payload["recovery_actions"]}


def test_attached_product_error_logs_the_same_safe_incident(caplog):
    from api.product_contract import attach_product_error

    with caplog.at_level(logging.WARNING, logger="taiji.product_error"):
        payload = attach_product_error(
            {"error": "legacy details"},
            "permission_denied",
            incident_id="inc-0123456789ab",
        )

    assert payload["product_error"]["incident_id"] == "inc-0123456789ab"
    assert "code=permission_denied incident_id=inc-0123456789ab" in caplog.text
    assert "legacy details" not in caplog.text
