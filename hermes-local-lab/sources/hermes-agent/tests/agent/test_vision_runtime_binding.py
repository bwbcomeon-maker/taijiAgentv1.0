from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest


class _FakeSyncOpenAI:
    def __init__(self, *, api_key, base_url, **_kwargs):
        self.api_key = api_key
        self.base_url = str(base_url)

    def close(self):
        return None


class _FakeAsyncOpenAI:
    captured = {}

    def __init__(self, *, api_key, base_url, **_kwargs):
        self.api_key = api_key
        self.base_url = str(base_url)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.captured[threading.current_thread().name] = {
            "url": self.base_url.rstrip("/"),
            "authorization": f"Bearer {self.api_key}",
            "model": kwargs["model"],
        }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="binding-ok")
                )
            ]
        )

    async def aclose(self):
        return None


@pytest.mark.parametrize(
    ("provider", "api_mode"),
    [
        ("custom:router", "chat_completions"),
        ("alibaba", "chat_completions"),
        ("zai", "chat_completions"),
    ],
)
def test_concurrent_vision_binding_controls_final_client_url_and_auth(
    monkeypatch,
    provider,
    api_mode,
):
    import openai
    import hermes_cli.auth as hermes_auth
    from agent import auxiliary_client
    from agent.auxiliary_client import VisionRequestBinding

    monkeypatch.setattr(auxiliary_client, "OpenAI", _FakeSyncOpenAI)
    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(
        hermes_auth,
        "resolve_api_key_provider_credentials",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient provider credentials were re-read")
        ),
    )
    monkeypatch.setattr(
        auxiliary_client,
        "_resolve_task_provider_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient vision config was re-resolved")
        ),
    )
    monkeypatch.setattr(
        auxiliary_client,
        "_get_auxiliary_task_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient auxiliary config was re-read")
        ),
    )
    monkeypatch.setattr(
        auxiliary_client,
        "_get_aux_model_for_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient auxiliary model was re-read")
        ),
    )
    monkeypatch.setattr(
        auxiliary_client,
        "_read_main_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient main model was re-read")
        ),
    )
    if provider.startswith("custom:"):
        from agent import custom_vision_providers

        monkeypatch.setattr(
            custom_vision_providers,
            "find_custom_vision_provider_entry",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("ambient custom vision entry was re-read")
            ),
        )
    auxiliary_client._client_cache.clear()
    _FakeAsyncOpenAI.captured = {}
    start = threading.Barrier(2)
    results = {}
    errors = {}
    profiles = {
        name: {
            "base_url": f"https://profile-{name.lower()}.example.test/v1",
            "api_key": f"profile-{name.lower()}-secret",
        }
        for name in ("A", "B")
    }

    def worker(profile):
        binding = VisionRequestBinding(
            provider=provider,
            model="vision-model",
            base_url=profiles[profile]["base_url"],
            api_key=profiles[profile]["api_key"],
            api_mode=api_mode,
            network_scope="public_direct",
            endpoint_mode="custom",
        )
        try:
            start.wait(timeout=5)
            resolution = {}
            response = asyncio.run(
                auxiliary_client.async_call_llm(
                    task="vision",
                    vision_binding=binding,
                    messages=[{"role": "user", "content": "inspect"}],
                    timeout=1.0,
                    no_fallback=True,
                    resolution_out=resolution,
                )
            )
            results[profile] = (
                response.choices[0].message.content,
                resolution,
            )
        except Exception as exc:
            errors[profile] = exc

    first = threading.Thread(target=worker, args=("A",), name="A")
    second = threading.Thread(target=worker, args=("B",), name="B")
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == {}
    assert results == {
        profile: (
            "binding-ok",
            {"provider": provider, "model": "vision-model"},
        )
        for profile in ("A", "B")
    }
    assert _FakeAsyncOpenAI.captured == {
        profile: {
            "url": profiles[profile]["base_url"],
            "authorization": f"Bearer {profiles[profile]['api_key']}",
            "model": "vision-model",
        }
        for profile in ("A", "B")
    }


def test_concurrent_anthropic_vision_binding_controls_final_url_auth_and_model(
    monkeypatch,
):
    from agent import anthropic_adapter, auxiliary_client, custom_vision_providers
    from agent.auxiliary_client import VisionRequestBinding

    captured = {}

    class _FakeAnthropicClient:
        def __init__(self, api_key, base_url):
            self._api_key = api_key
            self._base_url = base_url
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            captured[self._api_key] = {
                "url": self._base_url.rstrip("/"),
                "authorization": f"Bearer {self._api_key}",
                "model": kwargs["model"],
            }
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="binding-ok")],
                stop_reason="end_turn",
                usage=None,
            )

        def close(self):
            return None

    def build_fake_anthropic_client(api_key, base_url, **_kwargs):
        return _FakeAnthropicClient(api_key, base_url)

    monkeypatch.setattr(
        anthropic_adapter,
        "build_anthropic_client",
        build_fake_anthropic_client,
    )
    monkeypatch.setattr(
        auxiliary_client,
        "_resolve_task_provider_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient vision config was re-resolved")
        ),
    )
    monkeypatch.setattr(
        auxiliary_client,
        "_get_auxiliary_task_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient auxiliary config was re-read")
        ),
    )
    monkeypatch.setattr(
        auxiliary_client,
        "_get_aux_model_for_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient auxiliary model was re-read")
        ),
    )
    monkeypatch.setattr(
        auxiliary_client,
        "_read_main_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient main model was re-read")
        ),
    )
    monkeypatch.setattr(
        custom_vision_providers,
        "find_custom_vision_provider_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambient custom vision entry was re-read")
        ),
    )
    start = threading.Barrier(2)
    results = {}
    errors = {}
    profiles = {
        name: {
            "base_url": f"https://anthropic-{name.lower()}.example.test/v1",
            "api_key": f"anthropic-{name.lower()}-secret",
            "model": f"vision-model-{name.lower()}",
        }
        for name in ("A", "B")
    }

    def worker(profile):
        binding = VisionRequestBinding(
            provider="custom:router",
            model=profiles[profile]["model"],
            base_url=profiles[profile]["base_url"],
            api_key=profiles[profile]["api_key"],
            api_mode="anthropic_messages",
            network_scope="public_direct",
        )
        try:
            start.wait(timeout=5)
            resolution = {}
            response = asyncio.run(
                auxiliary_client.async_call_llm(
                    task="vision",
                    vision_binding=binding,
                    messages=[{"role": "user", "content": "inspect"}],
                    timeout=1.0,
                    no_fallback=True,
                    resolution_out=resolution,
                )
            )
            results[profile] = (
                response.choices[0].message.content,
                resolution,
            )
        except Exception as exc:
            errors[profile] = exc

    first = threading.Thread(target=worker, args=("A",), name="A")
    second = threading.Thread(target=worker, args=("B",), name="B")
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == {}
    assert results == {
        profile: (
            "binding-ok",
            {
                "provider": "custom:router",
                "model": profiles[profile]["model"],
            },
        )
        for profile in ("A", "B")
    }
    assert captured == {
        profile_data["api_key"]: {
            "url": profile_data["base_url"],
            "authorization": f"Bearer {profile_data['api_key']}",
            "model": profile_data["model"],
        }
        for profile_data in profiles.values()
    }


def test_vision_binding_and_client_cache_key_do_not_expose_secret():
    from agent.auxiliary_client import (
        VisionRequestBinding,
        _client_cache_key,
    )

    secret = "vision-secret-must-not-leak"
    binding = VisionRequestBinding(
        provider="zai",
        model="glm-5v-turbo",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key=secret,
        network_scope="trusted_proxy",
        trusted_proxy_profile="private-proxy-profile-canary",
    )
    cache_key = _client_cache_key(
        "zai",
        async_mode=True,
        base_url=binding.base_url,
        api_key=secret,
        api_mode=binding.api_mode,
        is_vision=True,
    )
    other_cache_key = _client_cache_key(
        "zai",
        async_mode=True,
        base_url=binding.base_url,
        api_key="different-vision-secret",
        api_mode=binding.api_mode,
        is_vision=True,
    )
    other_endpoint_cache_key = _client_cache_key(
        "zai",
        async_mode=True,
        base_url="https://other-private-endpoint.example.test/v1",
        api_key=secret,
        api_mode=binding.api_mode,
        is_vision=True,
    )

    assert secret not in repr(binding)
    assert binding.base_url not in repr(binding)
    assert binding.trusted_proxy_profile not in repr(binding)
    assert secret not in repr(cache_key)
    assert binding.base_url not in repr(cache_key)
    assert cache_key != other_cache_key
    assert cache_key != other_endpoint_cache_key
