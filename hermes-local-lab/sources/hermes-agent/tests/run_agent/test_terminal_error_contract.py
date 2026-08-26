import json
import ssl
import socket

import pytest

from agent.error_classifier import FailoverReason, classify_api_error
from agent.error_contract import (
    build_gateway_exception_error,
    build_gateway_run_error,
    build_terminal_failure,
    redact_provider_error,
    transport_kind_for_error,
)


class ProviderHTTPError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("read timed out"), "timeout"),
        (socket.gaierror(-2, "Name or service not known"), "dns"),
        (ConnectionRefusedError("connection refused"), "connect"),
        (ssl.SSLError("certificate verify failed"), "tls"),
        (ConnectionResetError("connection reset by peer"), "disconnect"),
    ],
)
def test_transport_kind_is_a_stable_safe_enum(error, expected):
    assert transport_kind_for_error(error) == expected


@pytest.mark.parametrize(
    ("status", "message", "reason"),
    [
        (401, "invalid api key", FailoverReason.auth),
        (403, "forbidden", FailoverReason.auth),
        (402, "payment required", FailoverReason.billing),
        (403, "insufficient balance", FailoverReason.billing),
        (404, "model not found", FailoverReason.model_not_found),
        (429, "rate limit", FailoverReason.rate_limit),
        (500, "internal server error", FailoverReason.server_error),
        (503, "overloaded", FailoverReason.overloaded),
    ],
)
def test_provider_statuses_keep_existing_failover_classification(status, message, reason):
    classified = classify_api_error(
        ProviderHTTPError(status, message),
        provider="deepseek",
        model="deepseek-chat",
    )
    assert classified.reason == reason


def test_terminal_failure_contains_only_safe_structured_fields():
    secret = "sk-terminal-sentinel"
    error = ProviderHTTPError(401, f"invalid key {secret} Bearer {secret}")
    classified = classify_api_error(error, provider="deepseek", model="deepseek-chat")

    result = build_terminal_failure(classified, error, sensitive_values=[secret])

    assert result["failed"] is True
    assert result["error"] == "Provider request failed."
    assert result["error_code"] == "auth"
    assert result["error_status"] == 401
    assert result["transport_kind"] is None
    assert result["retryable"] is False
    assert result["incident_id"].startswith("inc-")
    rendered = json.dumps(result)
    assert secret not in rendered
    assert "invalid key" not in rendered


def test_provider_error_redaction_removes_headers_query_env_paths_and_traceback():
    secret = "sentinel-provider-key"
    raw = (
        f"Authorization: Bearer {secret} https://provider.test/v1?api_key={secret} "
        f"OPENAI_API_KEY={secret} /Users/alice/private Traceback internal-frame"
    )

    cleaned = redact_provider_error(raw, sensitive_values=[secret])

    for forbidden in (secret, "Bearer", "api_key=", "OPENAI_API_KEY", "/Users/", "Traceback"):
        assert forbidden not in cleaned


def test_gateway_run_error_preserves_safe_terminal_fields_and_drops_raw_values():
    terminal = {
        "failed": True,
        "error": "Provider request failed.",
        "error_code": "auth",
        "error_status": 401,
        "transport_kind": None,
        "retryable": False,
        "incident_id": "inc-0123456789ab",
        "final_response": "raw provider body must not cross",
    }

    payload = build_gateway_run_error(terminal)

    assert payload == {
        "schema": "taiji.gateway.run-error.v1",
        "source": "provider",
        "code": "auth",
        "message": "Provider request failed.",
        "status": 401,
        "transport_kind": None,
        "retryable": False,
        "incident_id": "inc-0123456789ab",
    }
    assert "raw provider body" not in json.dumps(payload)


def test_gateway_worker_exception_contract_never_accepts_exception_text():
    payload = build_gateway_exception_error(incident_id="inc-0123456789ab")

    assert payload == {
        "schema": "taiji.gateway.run-error.v1",
        "source": "gateway",
        "code": "worker_exception",
        "message": "Gateway run failed.",
        "status": None,
        "transport_kind": None,
        "retryable": True,
        "incident_id": "inc-0123456789ab",
    }
