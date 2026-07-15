"""Named custom vision provider configuration and routing tests."""

import os

import pytest
import yaml


def test_named_custom_vision_providers_use_isolated_secret_envs():
    from agent.custom_vision_providers import normalize_custom_vision_provider_entry

    alpha = normalize_custom_vision_provider_entry({
        "id": "alpha-gateway",
        "name": "Alpha",
        "base_url": "https://alpha.example.com/v1",
        "models": ["alpha-vl"],
        "transport": "openai_chat_completions",
    })
    beta = normalize_custom_vision_provider_entry({
        "id": "beta_gateway",
        "name": "Beta",
        "base_url": "https://beta.example.com/anthropic",
        "models": ["beta-vl"],
        "transport": "anthropic_messages",
    })

    assert alpha["api_key_env"] == "TAIJI_VISION_CUSTOM_ALPHA_GATEWAY_API_KEY"
    assert beta["api_key_env"] == "TAIJI_VISION_CUSTOM_BETA_GATEWAY_API_KEY"
    assert alpha["api_key_env"] != beta["api_key_env"]


def test_named_custom_vision_provider_rejects_unknown_transport():
    from agent.custom_vision_providers import normalize_custom_vision_provider_entry

    with pytest.raises(ValueError, match="transport"):
        normalize_custom_vision_provider_entry({
            "id": "unsafe",
            "base_url": "https://vision.example.com/v1",
            "models": ["vision-model"],
            "transport": "arbitrary_native_api",
        })


@pytest.mark.parametrize(
    "base_url",
    [
        "http://vision.example.com/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/v1",
        "https://user:pass@vision.example.com/v1",
        "https://vision.example.com/v1?secret=x",
    ],
)
def test_named_custom_vision_provider_rejects_structurally_unsafe_base_url(base_url):
    from agent.custom_vision_providers import normalize_custom_vision_provider_entry

    with pytest.raises(ValueError, match="Base URL"):
        normalize_custom_vision_provider_entry({
            "id": "unsafe",
            "base_url": base_url,
            "models": ["vision-model"],
            "transport": "openai_chat_completions",
        })


def test_named_custom_vision_runtime_resolves_each_provider_secret(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_ALPHA_API_KEY", "alpha-secret")
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_BETA_API_KEY", "beta-secret")
    monkeypatch.setenv("AUXILIARY_VISION_API_KEY", "legacy-secret-must-not-win")
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    entries = [
        {
            "id": "alpha",
            "name": "Alpha",
            "base_url": "https://alpha.example.com/v1",
            "api_key_env": "TAIJI_VISION_CUSTOM_ALPHA_API_KEY",
            "models": ["alpha-vl"],
            "default_model": "alpha-vl",
            "transport": "openai_chat_completions",
        },
        {
            "id": "beta",
            "name": "Beta",
            "base_url": "https://beta.example.com/anthropic",
            "api_key_env": "TAIJI_VISION_CUSTOM_BETA_API_KEY",
            "models": ["beta-vl"],
            "default_model": "beta-vl",
            "transport": "anthropic_messages",
        },
    ]
    from agent.auxiliary_client import _resolve_task_provider_model

    for provider, expected in (
        ("custom:alpha", ("alpha-vl", "alpha-secret", "chat_completions")),
        ("custom:beta", ("beta-vl", "beta-secret", "anthropic_messages")),
    ):
        (home / "config.yaml").write_text(
            yaml.safe_dump({
                "auxiliary": {"vision": {"provider": provider}},
                "custom_vision_providers": entries,
            }),
            encoding="utf-8",
        )
        resolved = _resolve_task_provider_model("vision")
        assert resolved == (
            provider,
            expected[0],
            entries[0 if provider.endswith("alpha") else 1]["base_url"],
            expected[1],
            expected[2],
        )

    for key in (
        "TAIJI_VISION_CUSTOM_ALPHA_API_KEY",
        "TAIJI_VISION_CUSTOM_BETA_API_KEY",
        "AUXILIARY_VISION_API_KEY",
    ):
        os.environ.pop(key, None)


@pytest.mark.parametrize(
    ("transport", "expected_mode"),
    [
        ("openai_chat_completions", "chat_completions"),
        ("anthropic_messages", "anthropic_messages"),
    ],
)
def test_named_custom_vision_client_keeps_identity_and_disables_redirects(
    tmp_path, monkeypatch, transport, expected_mode
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_RELAY_API_KEY", "relay-secret")
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "auxiliary": {"vision": {"provider": "custom:relay"}},
            "custom_vision_providers": [{
                "id": "relay",
                "base_url": "https://relay.example.com/v1",
                "models": ["relay-vl"],
                "transport": transport,
            }],
        }),
        encoding="utf-8",
    )
    calls = []

    def fake_resolve(provider, model=None, **kwargs):
        calls.append((provider, model, kwargs))
        return object(), model

    monkeypatch.setattr("agent.auxiliary_client.resolve_provider_client", fake_resolve)
    from agent.auxiliary_client import resolve_vision_provider_client

    provider, client, model = resolve_vision_provider_client()

    assert provider == "custom:relay"
    assert client is not None
    assert model == "relay-vl"
    assert calls == [("custom", "relay-vl", {
        "async_mode": False,
        "explicit_base_url": "https://relay.example.com/v1",
        "explicit_api_key": "relay-secret",
        "api_mode": expected_mode,
        "follow_redirects": False,
    })]
    os.environ.pop("TAIJI_VISION_CUSTOM_RELAY_API_KEY", None)


def test_named_custom_vision_runtime_fails_closed_for_unsafe_dns(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_RELAY_API_KEY", "relay-secret")
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: False)
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "auxiliary": {"vision": {"provider": "custom:relay"}},
            "custom_vision_providers": [{
                "id": "relay",
                "base_url": "https://relay.example.com/v1",
                "models": ["relay-vl"],
                "transport": "openai_chat_completions",
            }],
        }),
        encoding="utf-8",
    )
    from agent.auxiliary_client import _resolve_task_provider_model

    with pytest.raises(ValueError, match="unsafe custom vision endpoint"):
        _resolve_task_provider_model("vision")
    os.environ.pop("TAIJI_VISION_CUSTOM_RELAY_API_KEY", None)


def test_named_anthropic_vision_refuses_openai_wire_fallback(monkeypatch):
    from types import SimpleNamespace

    raw_client = SimpleNamespace()
    monkeypatch.setattr("agent.auxiliary_client.OpenAI", lambda **_kwargs: raw_client)
    monkeypatch.setattr(
        "agent.auxiliary_client._maybe_wrap_anthropic",
        lambda client, *_args, **_kwargs: client,
    )
    from agent.auxiliary_client import resolve_provider_client

    client, model = resolve_provider_client(
        "custom",
        "relay-vl",
        explicit_base_url="https://relay.example.com/anthropic",
        explicit_api_key="relay-secret",
        api_mode="anthropic_messages",
        follow_redirects=False,
        is_vision=True,
    )

    assert client is None
    assert model is None


@pytest.mark.parametrize("async_mode", [False, True])
def test_named_custom_vision_without_dedicated_key_never_uses_global_fallback(
    tmp_path, monkeypatch, async_mode
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-global-openai-secret")
    monkeypatch.setenv("AUXILIARY_VISION_API_KEY", "wrong-legacy-vision-secret")
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    (home / "config.yaml").write_text(yaml.safe_dump({
        "auxiliary": {"vision": {"provider": "custom:relay"}},
        "custom_vision_providers": [{
            "id": "relay",
            "base_url": "https://relay.example.com/v1",
            "models": ["relay-vl"],
            "transport": "openai_chat_completions",
        }],
    }), encoding="utf-8")
    from agent.auxiliary_client import resolve_vision_provider_client

    with pytest.raises(ValueError, match="credential unavailable"):
        resolve_vision_provider_client(async_mode=async_mode)

    for key in ("OPENAI_API_KEY", "AUXILIARY_VISION_API_KEY"):
        os.environ.pop(key, None)


@pytest.mark.asyncio
async def test_strict_async_named_vision_without_key_fails_before_any_global_fallback(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-global-openai-secret")
    monkeypatch.setenv("AUXILIARY_VISION_API_KEY", "wrong-legacy-vision-secret")
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    (home / "config.yaml").write_text(yaml.safe_dump({
        "auxiliary": {"vision": {"provider": "custom:relay"}},
        "custom_vision_providers": [{
            "id": "relay", "base_url": "https://relay.example.com/v1",
            "models": ["relay-vl"], "transport": "openai_chat_completions",
        }],
    }), encoding="utf-8")
    from agent.auxiliary_client import async_call_llm

    with pytest.raises(ValueError, match="credential unavailable"):
        await async_call_llm(
            task="vision",
            messages=[{"role": "user", "content": "inspect"}],
            no_fallback=True,
        )
    for key in ("OPENAI_API_KEY", "AUXILIARY_VISION_API_KEY"):
        os.environ.pop(key, None)


@pytest.mark.parametrize(
    "broken_entry",
    [
        None,
        {"id": "relay", "base_url": "https://relay.example.com/v1", "models": ["relay-vl"], "transport": "native_magic"},
        {"id": "relay", "base_url": "http://127.0.0.1/v1", "models": ["relay-vl"], "transport": "openai_chat_completions"},
        {"id": "relay", "base_url": "https://relay.example.com/v1", "models": ["relay-vl"], "transport": "openai_chat_completions", "api_key_env": "OPENAI_API_KEY"},
    ],
)
def test_missing_or_corrupt_named_vision_never_collides_with_legacy_custom(
    tmp_path, monkeypatch, broken_entry
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-global-secret")
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    config = {
        "auxiliary": {"vision": {"provider": "custom:relay"}},
        "custom_providers": [{
            "name": "relay",
            "base_url": "https://legacy.example.com/v1",
            "api_key": "legacy-same-name-secret",
        }],
    }
    if broken_entry is not None:
        config["custom_vision_providers"] = [broken_entry]
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    from agent.auxiliary_client import _resolve_task_provider_model

    with pytest.raises(ValueError, match="named custom vision provider unavailable"):
        _resolve_task_provider_model("vision")
    os.environ.pop("OPENAI_API_KEY", None)


def test_named_custom_vision_rejects_model_outside_provider_allowlist(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_RELAY_API_KEY", "relay-secret")
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    (home / "config.yaml").write_text(yaml.safe_dump({
        "auxiliary": {"vision": {"provider": "custom:relay", "model": "other-model"}},
        "custom_vision_providers": [{
            "id": "relay", "base_url": "https://relay.example.com/v1",
            "models": ["relay-vl"], "transport": "openai_chat_completions",
        }],
    }), encoding="utf-8")
    from agent.auxiliary_client import _resolve_task_provider_model

    with pytest.raises(ValueError, match="model unavailable"):
        _resolve_task_provider_model("vision")
    os.environ.pop("TAIJI_VISION_CUSTOM_RELAY_API_KEY", None)


@pytest.mark.parametrize("async_mode", [False, True])
def test_named_openai_vision_clients_disable_redirects_on_real_transport(
    tmp_path, monkeypatch, async_mode
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_RELAY_API_KEY", "relay-secret")
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    (home / "config.yaml").write_text(yaml.safe_dump({
        "auxiliary": {"vision": {"provider": "custom:relay"}},
        "custom_vision_providers": [{
            "id": "relay", "base_url": "https://relay.example.com/v1",
            "models": ["relay-vl"], "transport": "openai_chat_completions",
        }],
    }), encoding="utf-8")
    from agent.auxiliary_client import resolve_vision_provider_client

    provider, client, _model = resolve_vision_provider_client(async_mode=async_mode)
    assert provider == "custom:relay"
    assert client._client.follow_redirects is False
    if async_mode:
        import asyncio

        asyncio.run(client.close())
    else:
        client.close()
    os.environ.pop("TAIJI_VISION_CUSTOM_RELAY_API_KEY", None)


@pytest.mark.parametrize("async_mode", [False, True])
def test_named_anthropic_vision_clients_disable_redirects_on_real_transport(
    tmp_path, monkeypatch, async_mode
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_RELAY_API_KEY", "relay-secret")
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    (home / "config.yaml").write_text(yaml.safe_dump({
        "auxiliary": {"vision": {"provider": "custom:relay"}},
        "custom_vision_providers": [{
            "id": "relay", "base_url": "https://relay.example.com/anthropic",
            "models": ["relay-vl"], "transport": "anthropic_messages",
        }],
    }), encoding="utf-8")
    from agent.auxiliary_client import resolve_vision_provider_client

    provider, client, _model = resolve_vision_provider_client(async_mode=async_mode)
    assert provider == "custom:relay"
    assert client._real_client._client.follow_redirects is False
    if async_mode:
        import asyncio

        asyncio.run(client.close())
    else:
        client.close()
    os.environ.pop("TAIJI_VISION_CUSTOM_RELAY_API_KEY", None)
