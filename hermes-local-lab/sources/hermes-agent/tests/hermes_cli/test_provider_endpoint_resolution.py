from types import SimpleNamespace

import pytest

from hermes_cli.providers import (
    HermesOverlay,
    HERMES_OVERLAYS,
    endpoint_policy_for,
    get_provider,
    resolve_custom_provider,
    resolve_provider_endpoint,
    resolve_user_provider,
)


def _normalizer_contract(name):
    """Resolve a planned normalizer without turning a missing API into collection error."""
    import hermes_cli.providers as providers

    normalizer = getattr(providers, name, None)
    assert callable(normalizer), f"missing planned provider normalizer: {name}"
    return normalizer


def test_endpoint_policy_for_zai_aliases_is_fixed():
    assert endpoint_policy_for("zai-cn") == "fixed"
    assert endpoint_policy_for("glm-cn") == "fixed"
    assert endpoint_policy_for("zhipu-cn") == "fixed"


@pytest.mark.parametrize(
    "endpoint_policy,auth_type",
    [("runtime_managed", "api_key"), ("configurable", "oauth_external")],
)
def test_endpoint_policy_comes_only_from_overlay_metadata(
    monkeypatch, endpoint_policy, auth_type
):
    baseline = HERMES_OVERLAYS["zai-cn"]
    monkeypatch.setitem(
        HERMES_OVERLAYS,
        "zai-cn",
        HermesOverlay(
            transport=baseline.transport,
            auth_type=auth_type,
            extra_env_vars=baseline.extra_env_vars,
            base_url_override=baseline.base_url_override,
            endpoint_policy=endpoint_policy,
        ),
    )
    monkeypatch.setattr(
        "agent.models_dev.get_provider_info",
        lambda _provider: SimpleNamespace(
            name="Z.AI China",
            env=(),
            api="https://models.example.test/v1",
            doc="",
        ),
    )

    assert endpoint_policy_for("zai-cn") == endpoint_policy
    assert get_provider("zai-cn").endpoint_policy == endpoint_policy


def test_resolve_provider_endpoint_signature_defaults_and_positional_guard():
    resolved = resolve_provider_endpoint(provider_id="zai-cn")

    assert resolved.provider == "zai-cn"
    assert resolved.policy == "fixed"
    assert resolved.effective_url == "https://open.bigmodel.cn/api/paas/v4"
    assert resolved.source == "system"
    assert resolved.editable is False
    assert resolved.requires_endpoint is False

    with pytest.raises(TypeError):
        # configured_url must be keyword-only.
        resolve_provider_endpoint("zai", "https://legacy.example.test/v1")


def test_provider_id_normalization_is_canonical_and_editable_rules():
    resolved = resolve_provider_endpoint(
        provider_id="  ZAI-CN ",
        configured_url="https://api.deepseek.com/v1",
        candidate_source="runtime",
        candidate_override_present=False,
    )
    assert resolved.provider == "zai-cn"

    whitespace_custom = resolve_provider_endpoint(
        provider_id=" custom ",
        configured_url="https://proxy.example.test/v1/",
        candidate_source="custom",
    )
    assert whitespace_custom.provider == "custom"
    assert whitespace_custom.editable is True
    assert whitespace_custom.requires_endpoint is True
    assert whitespace_custom.source == "custom"

    aliased_custom = resolve_provider_endpoint(
        provider_id="ollama",
        configured_url="https://proxy.example.test/v1",
        candidate_source="custom",
    )
    assert aliased_custom.provider == "custom"
    assert aliased_custom.editable is False

    unknown = resolve_provider_endpoint(
        provider_id="unknown-provider",
        configured_url="https://proxy.example.test/v1",
    )
    assert unknown.provider == "unknown-provider"
    assert unknown.requires_endpoint is True

    empty = resolve_provider_endpoint(provider_id="\n  ")
    assert empty.provider == ""
    assert empty.effective_url == ""
    assert empty.editable is False


def test_configurable_prefers_runtime_candidate_over_configured_when_runtime_exists():
    resolved_with_runtime = resolve_provider_endpoint(
        provider_id="zai",
        configured_url="https://configured.example.test/v1/",
        runtime_url="https://runtime.example.test/v1/",
    )
    assert resolved_with_runtime.policy == "configurable"
    assert resolved_with_runtime.effective_url == "https://runtime.example.test/v1"
    assert resolved_with_runtime.source == "runtime"
    assert resolved_with_runtime.requires_endpoint is False

    resolved_without_runtime = resolve_provider_endpoint(
        provider_id="zai",
        configured_url="https://configured.example.test/v1/",
        runtime_url="",
        candidate_source="managed",
    )
    assert resolved_without_runtime.policy == "configurable"
    assert resolved_without_runtime.effective_url == "https://configured.example.test/v1"
    assert resolved_without_runtime.source == "managed"


def test_runtime_managed_keeps_runtime_ownership_even_when_url_missing():
    resolved = resolve_provider_endpoint(
        provider_id="xai-oauth",
        configured_url="https://configured.example.test/v1/",
        runtime_url="",
        candidate_source="managed",
    )

    assert resolved.policy == "runtime_managed"
    assert resolved.source == "runtime"
    assert resolved.editable is False
    assert resolved.requires_endpoint is False
    assert resolved.effective_url == ""


def test_requires_endpoint_matrix_from_contract_and_fail_closed():
    zai = resolve_provider_endpoint(
        provider_id="zai-cn",
        configured_url="https://api.deepseek.com/v1",
    )
    assert zai.requires_endpoint is False

    azure = resolve_provider_endpoint(
        provider_id="azure-foundry",
        configured_url="https://regional.azure-example.test/v1",
    )
    assert azure.requires_endpoint is True

    exact_custom = resolve_provider_endpoint(
        provider_id="custom",
        configured_url="https://proxy.example.test/v1/",
    )
    assert exact_custom.requires_endpoint is True

    named_custom = resolve_provider_endpoint(
        provider_id="custom:glmcode",
        configured_url="https://proxy.example.test/v1/",
    )
    assert named_custom.requires_endpoint is True

    unknown = resolve_provider_endpoint(
        provider_id="unknown-provider",
        configured_url="https://proxy.example.test/v1/",
    )
    assert unknown.requires_endpoint is True

    alias_custom = resolve_provider_endpoint(
        provider_id="ollama",
        configured_url="https://proxy.example.test/v1/",
    )
    assert alias_custom.requires_endpoint is True

    runtime = resolve_provider_endpoint(
        provider_id="xai-oauth",
        runtime_url="https://runtime.example.test/v1/",
    )
    assert runtime.requires_endpoint is False


@pytest.mark.parametrize("provider", ["local", "vllm", "llamacpp", "llama.cpp", "llama-cpp"])
def test_local_provider_aliases_require_an_explicit_endpoint(provider):
    result = resolve_provider_endpoint(provider)

    assert result.provider == "local"
    assert result.effective_url == ""
    assert result.requires_endpoint is True


def test_user_and_saved_custom_provider_defs_require_an_endpoint():
    user_provider = resolve_user_provider(
        "team-proxy",
        {"team-proxy": {"name": "Team proxy", "api": "https://proxy.example.test/v1"}},
    )
    saved_custom = resolve_custom_provider(
        "custom:team-proxy",
        [{"name": "Team proxy", "base_url": "https://proxy.example.test/v1"}],
    )

    assert user_provider.requires_endpoint is True
    assert saved_custom.requires_endpoint is True


def test_zai_cn_dirty_candidates_are_ignored_and_clean_boundary_is_not():
    dirty = resolve_provider_endpoint(
        "zai-cn",
        configured_url="https://api.deepseek.com/v1",
        runtime_url="https://pool.example.test/v1",
        candidate_source="runtime",
        candidate_override_present=True,
    )
    clean = resolve_provider_endpoint(
        "zai-cn",
        configured_url="https://api.deepseek.com/v1",
        runtime_url="https://pool.example.test/v1",
        candidate_source="runtime",
        candidate_override_present=False,
    )

    assert dirty.effective_url == "https://open.bigmodel.cn/api/paas/v4"
    assert dirty.policy == "fixed"
    assert dirty.source == "system"
    assert dirty.editable is False
    assert dirty.requires_endpoint is False
    assert dirty.candidate_ignored is True
    assert clean.candidate_ignored is False


def test_runtime_overlay_metadata_is_static(monkeypatch):
    runtime_overlays = [
        "nous",
        "openai-codex",
        "xai-oauth",
        "qwen-oauth",
        "google-gemini-cli",
        "copilot-acp",
        "minimax-oauth",
        "bedrock",
    ]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("unexpected models.dev lookup")

    # Overlay-only path must be deterministic without models.dev/network.
    monkeypatch.setattr("agent.models_dev.get_provider_info", _boom)
    for candidate in runtime_overlays:
        pdef = get_provider(candidate)
        assert pdef is not None
        assert pdef.endpoint_policy == "runtime_managed"
        assert pdef.requires_endpoint is False
        assert endpoint_policy_for(candidate) == "runtime_managed"


def test_fail_closed_when_fixed_builtin_url_missing(monkeypatch):
    baseline = HERMES_OVERLAYS["zai-cn"]
    monkeypatch.setattr("agent.models_dev.get_provider_info", lambda _provider: None)
    monkeypatch.setitem(
        HERMES_OVERLAYS,
        "zai-cn",
        HermesOverlay(
            transport=baseline.transport,
            is_aggregator=baseline.is_aggregator,
            auth_type=baseline.auth_type,
            extra_env_vars=baseline.extra_env_vars,
            base_url_override="",
            base_url_env_var=baseline.base_url_env_var,
            endpoint_policy="fixed",
            requires_endpoint=baseline.requires_endpoint,
        ),
    )

    provider_def = get_provider("zai-cn")
    result = resolve_provider_endpoint(
        provider_id="zai-cn",
        runtime_url="https://runtime.example.test/v1",
        candidate_override_present=True,
    )
    assert provider_def is not None
    assert provider_def.requires_endpoint is True
    assert result.policy == "fixed"
    assert result.effective_url == ""
    assert result.source == "system"
    assert result.requires_endpoint is True
    assert result.candidate_ignored is True


def test_models_dev_provider_requires_endpoint_tracks_known_default_url(monkeypatch):
    catalog_entry = SimpleNamespace(
        name="Catalog provider",
        env=(),
        api="https://catalog.example.test/v1",
        doc="",
    )
    monkeypatch.setattr(
        "agent.models_dev.get_provider_info",
        lambda _provider: catalog_entry,
    )

    with_default = get_provider("catalog-only-provider")
    assert with_default is not None
    assert with_default.requires_endpoint is False

    catalog_entry.api = ""
    without_default = get_provider("catalog-only-provider")
    assert without_default is not None
    assert without_default.requires_endpoint is True


@pytest.mark.parametrize(
    "base_url_override,expected_requires_endpoint",
    [("https://overlay.example.test/v1", False), ("", True)],
)
def test_overlay_only_configurable_requires_endpoint_tracks_default_url(
    monkeypatch, base_url_override, expected_requires_endpoint
):
    monkeypatch.setattr("agent.models_dev.get_provider_info", lambda _provider: None)
    monkeypatch.setitem(
        HERMES_OVERLAYS,
        "overlay-configurable-test",
        HermesOverlay(
            base_url_override=base_url_override,
            endpoint_policy="configurable",
        ),
    )

    provider_def = get_provider("overlay-configurable-test")
    assert provider_def is not None
    assert provider_def.requires_endpoint is expected_requires_endpoint


def test_overlay_explicit_requires_endpoint_and_unknown_resolver_contract_remain(monkeypatch):
    monkeypatch.setattr("agent.models_dev.get_provider_info", lambda _provider: None)
    assert get_provider("azure-foundry").requires_endpoint is True
    assert resolve_provider_endpoint("unknown-provider").requires_endpoint is True


def test_endpoint_policy_resolution_does_not_touch_models_or_runtime_state(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("unexpected lookup")

    monkeypatch.setattr("agent.models_dev.get_provider_info", _boom)
    monkeypatch.setattr("hermes_cli.providers.get_provider", _boom)
    monkeypatch.setattr("hermes_cli.providers.resolve_provider_full", _boom)

    result = resolve_provider_endpoint(
        provider_id="zai-cn",
        configured_url="https://api.deepseek.com/v1",
        runtime_url="https://pool.example.test/v1",
        candidate_source="runtime",
        candidate_override_present=True,
    )
    assert result.policy == "fixed"
    assert result.effective_url == "https://open.bigmodel.cn/api/paas/v4"


def test_fixed_provider_model_normalization_removes_owned_fields_and_preserves_receipt():
    normalize = _normalizer_contract("normalize_model_endpoint_fields")
    model = {
        "provider": "zai-cn",
        "default": "glm-5",
        "base_url": "https://legacy.example.invalid/v1",
        "api_mode": "codex_responses",
        "request_receipt": {"id": "keep"},
    }

    assert normalize(model) is True
    assert set(model) == {"provider", "default", "request_receipt"}
    assert model["request_receipt"] == {"id": "keep"}
    assert normalize(model) is False


def test_config_normalizer_covers_known_paths_without_touching_unknown_or_media_sections():
    normalize = _normalizer_contract("normalize_config_endpoint_fields")
    config = {
        "model": {
            "provider": "zai-cn",
            "default": "glm-5",
            "base_url": "https://legacy.example.invalid/v1",
            "api_mode": "codex_responses",
        },
        "fallback_providers": [
            {
                "provider": "zai-cn",
                "model": "glm-5",
                "base_url": "https://legacy.example.invalid/fallback",
                "api_mode": "codex_responses",
            },
            {
                "provider": "custom",
                "model": "team-model",
                "base_url": "https://custom.example.test/v1",
                "api_mode": "chat_completions",
            },
        ],
        "fallback_model": {
            "provider": "zai-cn",
            "model": "glm-5",
            "base_url": "https://legacy.example.invalid/legacy",
            "api_mode": "codex_responses",
        },
        "auxiliary": {
            "compression": {
                "provider": "zai-cn",
                "model": "glm-5",
                "base_url": "https://legacy.example.invalid/compression",
                "api_mode": "codex_responses",
                "fallback_chain": [
                    {
                        "provider": "zai-cn",
                        "model": "glm-5",
                        "base_url": "https://legacy.example.invalid/chain",
                        "api_mode": "codex_responses",
                    }
                ],
            },
            "vision": {
                "provider": "zai-cn",
                "base_url": "https://media.example.test/v1",
                "api_mode": "chat_completions",
            },
        },
        "custom_providers": [
            {
                "name": "MiniMax custom",
                "base_url": "https://minimax.example.test/anthropic",
                "api_mode": "anthropic_messages",
            }
        ],
        "tts": {
            "provider": "zai-cn",
            "model": "voice-model",
            "base_url": "https://voice.example.test/v1",
            "api_mode": "chat_completions",
        },
        "voice": {
            "auto_tts": True,
            "provider": "zai-cn",
            "base_url": "https://voice.example.test/voice",
        },
        "unknown_section": {
            "provider": "zai-cn",
            "base_url": "https://unknown.example.test/v1",
            "api_mode": "codex_responses",
        },
    }

    assert normalize(config) is True
    for path in (
        config["model"],
        config["fallback_providers"][0],
        config["fallback_model"],
        config["auxiliary"]["compression"],
        config["auxiliary"]["compression"]["fallback_chain"][0],
    ):
        fields = set(path)
        assert "base_url" not in fields, "fixed endpoint base_url survived normalization"
        assert "api_mode" not in fields, "fixed endpoint api_mode survived normalization"
    assert config["fallback_providers"][1] == {
        "provider": "custom",
        "model": "team-model",
        "base_url": "https://custom.example.test/v1",
        "api_mode": "chat_completions",
    }
    assert config["custom_providers"][0] == {
        "name": "MiniMax custom",
        "base_url": "https://minimax.example.test/anthropic",
        "api_mode": "anthropic_messages",
    }
    assert config["auxiliary"]["vision"] == {
        "provider": "zai-cn",
        "base_url": "https://media.example.test/v1",
        "api_mode": "chat_completions",
    }
    assert config["tts"] == {
        "provider": "zai-cn",
        "model": "voice-model",
        "base_url": "https://voice.example.test/v1",
        "api_mode": "chat_completions",
    }
    assert config["voice"] == {
        "auto_tts": True,
        "provider": "zai-cn",
        "base_url": "https://voice.example.test/voice",
    }
    assert config["unknown_section"] == {
        "provider": "zai-cn",
        "base_url": "https://unknown.example.test/v1",
        "api_mode": "codex_responses",
    }
    assert normalize(config) is False


def test_config_normalizer_covers_legacy_fallback_dict_and_list_shapes():
    normalize = _normalizer_contract("normalize_config_endpoint_fields")
    configurable = {
        "provider": "deepseek",
        "model": "custom-fallback",
        "base_url": "https://configurable.example.test/v1",
        "api_mode": "chat_completions",
    }
    config = {
        "fallback_model": [
            {
                "provider": "zai-cn",
                "model": "legacy-list-fixed",
                "base_url": "https://legacy.example.invalid/dict",
                "api_mode": "codex_responses",
            },
            dict(configurable),
        ],
        "auxiliary": {
            "compression": {
                "fallback_chain": [
                    {
                        "provider": "zai-cn",
                        "model": "chain-fixed",
                        "base_url": "https://legacy.example.invalid/list",
                        "api_mode": "codex_responses",
                    },
                    dict(configurable),
                ]
            }
        },
    }
    assert normalize(config) is True
    assert config["fallback_model"][0] == {
        "provider": "zai-cn",
        "model": "legacy-list-fixed",
    }
    assert config["fallback_model"][1] == configurable
    assert config["auxiliary"]["compression"]["fallback_chain"][0] == {
        "provider": "zai-cn",
        "model": "chain-fixed",
    }
    assert config["auxiliary"]["compression"]["fallback_chain"][1] == configurable
    assert normalize(config) is False


def test_config_normalizer_transition_cleans_old_configurable_ownership_but_keeps_explicit_new_value():
    transition = _normalizer_contract("normalize_config_endpoint_transitions")
    previous = {
        "model": {
            "provider": "deepseek",
            "default": "old-model",
            "base_url": "https://old.example.invalid/v1",
            "api_mode": "chat_completions",
        },
        "fallback_providers": [
            {
                "provider": "deepseek",
                "model": "old-fallback",
                "base_url": "https://old.example.invalid/fallback",
                "api_mode": "chat_completions",
            }
        ],
        "fallback_model": {
            "provider": "deepseek",
            "model": "old-legacy",
            "base_url": "https://old.example.invalid/legacy",
            "api_mode": "chat_completions",
        },
        "auxiliary": {
            "compression": {
                "provider": "deepseek",
                "model": "old-compression",
                "base_url": "https://old.example.invalid/compression",
                "api_mode": "chat_completions",
                "fallback_chain": [
                    {
                        "provider": "deepseek",
                        "model": "old-chain",
                        "base_url": "https://old.example.invalid/chain",
                        "api_mode": "chat_completions",
                    }
                ],
            }
        },
    }
    next_config = {
        "model": {
            "provider": "zai",
            "default": "new-model",
            "base_url": "https://new.example.test/v1",
            "api_mode": "codex_responses",
        },
        "fallback_providers": [
            {
                "provider": "zai",
                "model": "new-fallback",
                "base_url": "https://old.example.invalid/fallback",
                "api_mode": "chat_completions",
            }
        ],
        "fallback_model": {
            "provider": "zai",
            "model": "new-legacy",
            "base_url": "https://old.example.invalid/legacy",
            "api_mode": "chat_completions",
        },
        "auxiliary": {
            "compression": {
                "provider": "zai",
                "model": "new-compression",
                "base_url": "https://old.example.invalid/compression",
                "api_mode": "chat_completions",
                "fallback_chain": [
                    {
                        "provider": "zai",
                        "model": "new-chain",
                        "base_url": "https://old.example.invalid/chain",
                        "api_mode": "chat_completions",
                    }
                ],
            }
        },
    }

    assert transition(previous, next_config) is True
    assert next_config["model"]["base_url"] == "https://new.example.test/v1"
    assert next_config["model"]["api_mode"] == "codex_responses"
    fallback_fields = set(next_config["fallback_providers"][0])
    compression_fields = set(next_config["auxiliary"]["compression"])
    assert "base_url" not in fallback_fields, "old fallback base_url survived transition"
    assert "api_mode" not in fallback_fields, "old fallback api_mode survived transition"
    assert "base_url" not in compression_fields, "old auxiliary base_url survived transition"
    assert "api_mode" not in compression_fields, "old auxiliary api_mode survived transition"
    chain_fields = set(next_config["auxiliary"]["compression"]["fallback_chain"][0])
    assert "base_url" not in chain_fields, "old auxiliary chain base_url survived transition"
    assert "api_mode" not in chain_fields, "old auxiliary chain api_mode survived transition"
    legacy_fields = set(next_config["fallback_model"])
    assert "base_url" not in legacy_fields, "old legacy fallback base_url survived transition"
    assert "api_mode" not in legacy_fields, "old legacy fallback api_mode survived transition"


def test_config_normalizer_transition_covers_fixed_to_configurable_and_configurable_to_fixed():
    transition = _normalizer_contract("normalize_config_endpoint_transitions")
    configurable_previous = {
        "model": {
            "provider": "deepseek",
            "default": "old-model",
            "base_url": "https://previous.example.invalid/v1",
            "api_mode": "chat_completions",
        }
    }
    fixed_previous = {
        "model": {
            "provider": "zai-cn",
            "default": "old-model",
            "base_url": "https://previous.example.invalid/v1",
            "api_mode": "codex_responses",
        }
    }
    fixed_to_configurable = {
        "model": {
            "provider": "deepseek",
            "default": "new-model",
            "base_url": "https://previous.example.invalid/v1",
            "api_mode": "codex_responses",
        }
    }
    assert transition(fixed_previous, fixed_to_configurable) is True
    fixed_to_configurable_fields = set(fixed_to_configurable["model"])
    assert "base_url" not in fixed_to_configurable_fields, "fixed residue survived provider transition"
    assert "api_mode" not in fixed_to_configurable_fields, "fixed transport residue survived provider transition"

    configurable_to_fixed = {
        "model": {
            "provider": "zai-cn",
            "default": "new-model",
            "base_url": "https://new.example.test/v1",
            "api_mode": "chat_completions",
        }
    }
    assert transition(configurable_previous, configurable_to_fixed) is True
    configurable_to_fixed_fields = set(configurable_to_fixed["model"])
    assert "base_url" not in configurable_to_fixed_fields, "configurable residue survived fixed transition"
    assert "api_mode" not in configurable_to_fixed_fields, "configurable transport residue survived fixed transition"


@pytest.mark.parametrize(
    "provider",
    ["custom", "deepseek", "zai", "minimax", "azure-foundry", "anthropic", "lmstudio"],
)
def test_config_normalizer_keeps_configurable_provider_model_fields(provider):
    normalize = _normalizer_contract("normalize_model_endpoint_fields")
    model = {
        "provider": provider,
        "default": "model",
        "base_url": f"https://{provider}.example.test/v1",
        "api_mode": "chat_completions",
    }

    before = dict(model)
    assert normalize(model) is False
    assert model == before
