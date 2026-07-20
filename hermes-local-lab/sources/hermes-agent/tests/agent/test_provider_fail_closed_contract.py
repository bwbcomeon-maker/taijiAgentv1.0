"""Task B1 contracts for provider aliases and fail-closed model selection."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest


def _value_error_failure(label: str, call: Callable[[], Any]) -> str | None:
    try:
        call()
    except ValueError:
        return None
    except Exception as exc:  # noqa: BLE001 - report the wrong public failure type
        return f"{label}: expected ValueError, got {type(exc).__name__}: {exc}"
    return f"{label}: expected ValueError, but no exception was raised"


def _provider_entry(**overrides: Any) -> dict[str, Any]:
    entry = {
        "id": "router",
        "name": "Router Images",
        "base_url": "https://images.example.com/v1",
        "credential_ref": "custom-router",
        "models": ["configured-image-model"],
        "default_model": "configured-image-model",
    }
    entry.update(overrides)
    return entry


def test_provider_family_aliases_are_canonical() -> None:
    from agent.provider_credentials import provider_family

    expected = {
        "alibaba": "alibaba_dashscope",
        "alibaba_dashscope": "alibaba_dashscope",
        "dashscope": "alibaba_dashscope",
        "zai": "zhipu",
        "zhipu": "zhipu",
        "zhipu-image": "zhipu",
        "ark": "doubao",
        "doubao": "doubao",
        "volcengine": "doubao",
        "baidu-qianfan": "qianfan",
        "qianfan": "qianfan",
        "minimax": "minimax",
        "minimax-image": "minimax",
    }

    assert {alias: provider_family(alias) for alias in expected} == expected
    assert provider_family("dashscope-preview") == "dashscope-preview"


def test_custom_provider_aliases_are_canonical_without_secret_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import provider_credentials

    secret_calls: list[str] = []
    environment_calls: list[str] = []

    def record_secret(secret_env: str) -> str:
        secret_calls.append(secret_env)
        return ""

    monkeypatch.setattr(
        provider_credentials,
        "_credential_secret_value",
        record_secret,
    )
    monkeypatch.setattr(
        provider_credentials,
        "os",
        SimpleNamespace(
            getenv=lambda key, default="": (
                environment_calls.append(key)
                or ("legacy-dashscope-secret" if key == "DASHSCOPE_API_KEY" else default)
            ),
        ),
    )

    assert provider_credentials.provider_family("custom") == "custom"
    assert provider_credentials.provider_family("custom-image") == "custom"
    assert provider_credentials.provider_family("custom:router") == "custom"
    assert provider_credentials.provider_family("custom:dashscope") == "custom"
    assert (
        provider_credentials.resolve_api_key(
            "custom:dashscope",
            config_data={},
        )
        == ""
    )
    assert secret_calls == []
    assert environment_calls == []


def test_builtin_known_image_models_resolve_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugins.image_gen import dashscope, doubao, minimax_image, qianfan
    from plugins.image_gen import zhipu_image

    http_calls: list[str] = []
    credential_calls: list[str] = []

    def record_http(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        http_calls.append("post")
        raise AssertionError("known-model characterization reached HTTP")

    for module in (dashscope, doubao, qianfan, zhipu_image, minimax_image):
        monkeypatch.setattr(
            module,
            "post_json",
            lambda *args, **kwargs: record_http(*args, **kwargs),
        )
        monkeypatch.setattr(
            module,
            "provider_api_key",
            lambda *args, **kwargs: credential_calls.append("secret") or "",
        )

    cases = (
        (dashscope.DashScopeQwenImageProvider(), "qwen-image"),
        (doubao.DoubaoImageGenProvider(), "doubao-seedream-5-0-lite-260128"),
        (qianfan.QianfanImageGenProvider(), "qwen-image"),
        (zhipu_image.ZhipuImageGenProvider(), "cogview-4"),
        (minimax_image.MinimaxImageGenProvider(), "image-01"),
    )
    for provider, model in cases:
        result = provider.generate("", model=model)
        assert result["model"] == model

    assert credential_calls == []
    assert http_calls == []


def test_builtin_unknown_image_models_fail_before_credential_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugins.image_gen import dashscope, doubao, minimax_image, qianfan
    from plugins.image_gen import zhipu_image

    failures: list[str] = []
    unknown_model = "vendor-unknown-image-model"

    cases: tuple[
        tuple[
            str,
            Any,
            Any,
            Callable[[Any, str], Any],
        ],
        ...,
    ] = (
        (
            "dashscope",
            dashscope,
            dashscope.DashScopeQwenImageProvider(),
            lambda provider, model: provider._model(model),
        ),
        (
            "doubao",
            doubao,
            doubao.DoubaoImageGenProvider(),
            lambda _provider, model: doubao._resolve_model(model),
        ),
        (
            "qianfan",
            qianfan,
            qianfan.QianfanImageGenProvider(),
            lambda provider, model: provider._model(model),
        ),
        (
            "zhipu-image",
            zhipu_image,
            zhipu_image.ZhipuImageGenProvider(),
            lambda provider, model: provider._model(model),
        ),
        (
            "minimax-image",
            minimax_image,
            minimax_image.MinimaxImageGenProvider(),
            lambda provider, model: provider._model(model),
        ),
    )

    for label, module, provider, resolver in cases:
        credential_calls: list[str] = []
        http_calls: list[str] = []

        def record_http(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            http_calls.append("post")
            raise AssertionError("unknown model reached HTTP")

        monkeypatch.setattr(module, "post_json", record_http)
        monkeypatch.setattr(
            module,
            "provider_api_key",
            lambda *args, **kwargs: credential_calls.append("secret") or "",
        )

        failure = _value_error_failure(
            f"{label} resolver",
            lambda resolver=resolver, provider=provider: resolver(
                provider,
                unknown_model,
            ),
        )
        if failure:
            failures.append(failure)

        try:
            result = provider.generate("draw a test image", model=unknown_model)
        except Exception as exc:  # noqa: BLE001 - public seam must be structured
            failures.append(
                f"{label} generate: expected structured error, got "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            if result.get("success") is not False:
                failures.append(f"{label} generate: did not fail")
            if result.get("error_type") != "invalid_argument":
                failures.append(
                    f"{label} generate: expected invalid_argument, got "
                    f"{result.get('error_type')!r}"
                )
            if result.get("model") != unknown_model:
                failures.append(
                    f"{label} generate: changed model to {result.get('model')!r}"
                )
        if credential_calls:
            failures.append(
                f"{label}: credential/environment seam called {credential_calls!r}"
            )
        if http_calls:
            failures.append(f"{label}: HTTP seam called {http_calls!r}")

    assert failures == []


def test_custom_image_model_requires_explicit_allow_custom_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import custom_image_providers

    failures: list[str] = []
    credential_calls: list[str] = []
    http_calls: list[str] = []
    save_calls: list[str] = []
    monkeypatch.setattr(
        custom_image_providers,
        "_entry_api_key",
        lambda *args, **kwargs: credential_calls.append("secret") or "",
    )
    monkeypatch.setattr(
        custom_image_providers,
        "request_pinned_https",
        lambda *args, **kwargs: http_calls.append("public"),
    )
    monkeypatch.setattr(
        custom_image_providers,
        "request_via_trusted_proxy",
        lambda *args, **kwargs: http_calls.append("proxy"),
    )
    monkeypatch.setattr(
        custom_image_providers,
        "save_b64_image",
        lambda *args, **kwargs: save_calls.append("b64"),
    )
    monkeypatch.setattr(
        custom_image_providers,
        "save_url_image",
        lambda *args, **kwargs: save_calls.append("url"),
    )

    strict_values = (
        False,
        "false",
        "0",
        "true",
        0,
        1,
        {},
        {"flag": True},
        [],
        ["true"],
        ("true",),
    )
    for raw_value in strict_values:
        normalized = custom_image_providers.normalize_custom_image_provider_entry(
            _provider_entry(allow_custom_model_id=raw_value)
        )
        if normalized["allow_custom_model_id"] is not False:
            failures.append(
                f"malformed opt-in {raw_value!r} normalized to "
                f"{normalized['allow_custom_model_id']!r}"
            )
        strict_provider = custom_image_providers.ConfigurableOpenAIImageProvider(
            normalized
        )
        result = strict_provider.generate(
            "draw a contract test",
            model="unlisted-image-model",
        )
        if result.get("success") is not False:
            failures.append(
                f"malformed opt-in {raw_value!r} did not fail publicly"
            )
        if result.get("error_type") != "invalid_argument":
            failures.append(
                f"malformed opt-in {raw_value!r} returned "
                f"{result.get('error_type')!r}"
            )
        if result.get("model") != "unlisted-image-model":
            failures.append(
                f"malformed opt-in {raw_value!r} changed model to "
                f"{result.get('model')!r}"
            )

    opt_in_provider = custom_image_providers.ConfigurableOpenAIImageProvider(
        _provider_entry(allow_custom_model_id=True)
    )
    selected = opt_in_provider._model("unlisted-image-model")
    if selected != "unlisted-image-model":
        failures.append(f"opt-in custom provider changed model to {selected!r}")

    assert credential_calls == []
    assert http_calls == []
    assert save_calls == []
    assert failures == []


def test_unknown_vision_model_and_capability_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from agent import image_routing
    webui_root = Path(__file__).resolve().parents[3] / "hermes-webui"
    monkeypatch.syspath_prepend(str(webui_root))
    sys.modules.pop("api.model_config", None)
    from api import model_config

    failures: list[str] = []
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda provider, model, cfg=None: None,
    )
    failure = _value_error_failure(
        "unknown main-model image capability",
        lambda: image_routing.decide_image_input_mode(
            "unknown-provider",
            "unknown-main-model",
            {},
        ),
    )
    if failure:
        failures.append(failure)

    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "reload_config", lambda: None)
    monkeypatch.setattr(model_config, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(model_config, "_invalidate_vision_verification", lambda: None)
    monkeypatch.setattr(model_config, "get_vision_config", lambda: {"ok": True})

    saved_providers: list[str] = []
    original_commit = model_config._commit_expected_config_env

    def record_commit(
        path: Any,
        *,
        expected_config: dict[str, Any],
        desired_config: dict[str, Any],
        env_updates: dict[str, str | None],
    ) -> None:
        vision = (desired_config.get("auxiliary") or {}).get("vision") or {}
        saved_providers.append(str(vision.get("provider") or ""))
        original_commit(
            path,
            expected_config=expected_config,
            desired_config=desired_config,
            env_updates=env_updates,
        )

    monkeypatch.setattr(model_config, "_commit_expected_config_env", record_commit)

    failure = _value_error_failure(
        "unknown ZAI vision model",
        lambda: model_config.set_vision_config(
            {"provider": "zai", "model": "unknown-zai-vision-model"}
        ),
    )
    if failure:
        failures.append(failure)

    try:
        model_config.set_vision_config(
            {
                "provider": "custom",
                "model": "arbitrary-private-vision-model",
                "base_url": "https://vision.example.com/v1",
            }
        )
    except ValueError as exc:
        failures.append(f"generic custom model was incorrectly rejected: {exc}")

    if saved_providers != ["custom"]:
        failures.append(
            f"unexpected save-before-validation providers: {saved_providers!r}"
        )

    assert failures == []
