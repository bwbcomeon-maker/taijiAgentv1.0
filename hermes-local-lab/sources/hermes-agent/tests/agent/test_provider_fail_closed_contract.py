"""Task B1 contracts for provider aliases and fail-closed model selection."""

from __future__ import annotations

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


class _RecordingEnvironment:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def get(self, key: str, default: Any = None) -> Any:
        self._calls.append(key)
        return default


def _provider_entry(**overrides: Any) -> dict[str, Any]:
    entry = {
        "id": "router",
        "name": "Router Images",
        "base_url": "https://images.example.com/v1",
        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
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

    monkeypatch.setattr(dashscope.requests, "post", record_http)
    monkeypatch.setattr(doubao.requests, "post", record_http)
    monkeypatch.setattr(qianfan.requests, "post", record_http)
    monkeypatch.setattr(zhipu_image.requests, "post", record_http)
    monkeypatch.setattr(minimax_image.requests, "post", record_http)
    monkeypatch.setattr(
        dashscope,
        "_resolve_api_key",
        lambda *args, **kwargs: credential_calls.append("dashscope") or "",
    )
    monkeypatch.setattr(doubao, "_load_image_gen_config", lambda: {})
    doubao_env_calls: list[str] = []
    monkeypatch.setattr(
        doubao,
        "os",
        SimpleNamespace(environ=_RecordingEnvironment(doubao_env_calls)),
    )

    def forbidden_required(*args: Any, **kwargs: Any) -> list[str]:
        del args, kwargs
        credential_calls.append("missing_required")
        return ["forbidden"]

    for module in (qianfan, zhipu_image, minimax_image):
        monkeypatch.setattr(module, "missing_required", forbidden_required)
        monkeypatch.setattr(
            module,
            "env_value",
            lambda *args, **kwargs: credential_calls.append("env_value") or "",
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
    assert "ARK_API_KEY" not in doubao_env_calls
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

        monkeypatch.setattr(module.requests, "post", record_http)
        if label == "dashscope":
            monkeypatch.setattr(
                module,
                "_resolve_api_key",
                lambda *args, **kwargs: credential_calls.append("secret") or "",
            )
        elif label == "doubao":
            monkeypatch.setattr(module, "_load_image_gen_config", lambda: {})
            monkeypatch.setattr(
                module,
                "os",
                SimpleNamespace(environ=_RecordingEnvironment(credential_calls)),
            )
        else:
            monkeypatch.setattr(
                module,
                "missing_required",
                lambda *args, **kwargs: credential_calls.append("required")
                or ["forbidden"],
            )
            monkeypatch.setattr(
                module,
                "env_value",
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
    env_calls: list[str] = []
    http_calls: list[str] = []
    save_calls: list[str] = []
    monkeypatch.setattr(
        custom_image_providers,
        "os",
        SimpleNamespace(
            getenv=lambda key, default="": env_calls.append(key) or default,
        ),
    )
    monkeypatch.setattr(
        custom_image_providers.requests,
        "post",
        lambda *args, **kwargs: http_calls.append("post"),
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

    assert env_calls == []
    assert http_calls == []
    assert save_calls == []
    assert failures == []


def test_unknown_vision_model_and_capability_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from agent import image_routing
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
    original_save = model_config._save_yaml_config_file

    def record_save(path: Any, data: dict[str, Any]) -> None:
        vision = (data.get("auxiliary") or {}).get("vision") or {}
        saved_providers.append(str(vision.get("provider") or ""))
        original_save(path, data)

    monkeypatch.setattr(model_config, "_save_yaml_config_file", record_save)

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
