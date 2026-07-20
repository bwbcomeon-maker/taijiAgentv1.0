"""Unit tests for tools.computer_use.vision_routing.

Cover the small ``should_route_capture_to_aux_vision`` policy helper that
decides whether a captured screenshot from ``computer_use(action='capture')``
should be returned as a multimodal envelope (main model handles vision
natively) or pre-analysed via the ``auxiliary.vision`` pipeline so the
main model only sees text.

The companion end-to-end regression for #24015 lives in
``tests/tools/test_computer_use_capture_routing.py``; this file pins the
unit contract of the helper in isolation so behaviour does not regress
silently if the surrounding ``computer_use`` plumbing is refactored.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# should_route_capture_to_aux_vision
# ---------------------------------------------------------------------------

class TestRouteDecision:
    """End-to-end policy: main capability truth precedes auxiliary fallback."""

    def test_native_main_wins_even_with_explicit_auxiliary_config(self):
        from tools.computer_use import vision_routing

        cfg = {
            "auxiliary": {
                "vision": {
                    "provider": "openrouter",
                    "model": "google/gemini-2.5-flash",
                }
            }
        }
        with patch.object(vision_routing, "_lookup_supports_vision", return_value=True), \
             patch.object(vision_routing,
                          "_provider_accepts_multimodal_tool_result",
                          return_value=True):
            assert vision_routing.should_route_capture_to_aux_vision(
                "anthropic", "claude-opus-4-5", cfg
            ) is False

    def test_non_vision_main_model_routes_to_aux(self):
        """The reported #24015 scenario: tencent/hy3-preview has no vision."""
        from tools.computer_use import vision_routing

        cfg = {"model": {"default": "tencent/hy3-preview", "provider": "openrouter"}}
        with patch.object(vision_routing, "_lookup_supports_vision", return_value=False), \
             patch.object(vision_routing,
                          "_provider_accepts_multimodal_tool_result",
                          return_value=True):
            assert vision_routing.should_route_capture_to_aux_vision(
                "openrouter", "tencent/hy3-preview", cfg
            ) is True

    def test_vision_main_model_no_override_keeps_multimodal(self):
        """Default path: vision-capable main model + no aux override → native."""
        from tools.computer_use import vision_routing

        with patch.object(vision_routing, "_lookup_supports_vision", return_value=True), \
             patch.object(vision_routing,
                          "_provider_accepts_multimodal_tool_result",
                          return_value=True):
            assert vision_routing.should_route_capture_to_aux_vision(
                "anthropic", "claude-opus-4-5", None
            ) is False

    def test_native_main_with_unsupported_tool_media_fails_closed(self):
        from tools.computer_use import vision_routing

        with patch.object(vision_routing, "_lookup_supports_vision", return_value=True), \
             patch.object(vision_routing,
                          "_provider_accepts_multimodal_tool_result",
                          return_value=False):
            with pytest.raises(RuntimeError, match="native_tool_media_unsupported"):
                vision_routing.should_route_capture_to_aux_vision(
                    "some-aggregator", "some-vision-model", {}
                )

    def test_unknown_provider_capabilities_fail_closed(self):
        from tools.computer_use import vision_routing

        with patch.object(vision_routing, "_lookup_supports_vision", return_value=True), \
             patch.object(vision_routing,
                          "_provider_accepts_multimodal_tool_result",
                          return_value=None):
            with pytest.raises(RuntimeError, match="native_tool_media_unknown"):
                vision_routing.should_route_capture_to_aux_vision(
                    "exotic-provider", "exotic-model", {}
                )

    def test_unknown_vision_capability_fails_closed(self):
        from tools.computer_use import vision_routing

        with patch.object(vision_routing, "_lookup_supports_vision", return_value=None), \
             patch.object(vision_routing,
                          "_provider_accepts_multimodal_tool_result",
                          return_value=True):
            with pytest.raises(RuntimeError, match="main_model_capability_unknown"):
                vision_routing.should_route_capture_to_aux_vision(
                    "openrouter", "novel/never-seen-model", {}
                )

    def test_explicit_auxiliary_does_not_override_unknown_main_capability(self):
        from tools.computer_use import vision_routing

        cfg = {"auxiliary": {"vision": {"provider": "openrouter"}}}
        with patch.object(vision_routing, "_lookup_supports_vision", return_value=None), \
             patch.object(vision_routing,
                          "_provider_accepts_multimodal_tool_result",
                          return_value=None):
            with pytest.raises(RuntimeError, match="main_model_capability_unknown"):
                vision_routing.should_route_capture_to_aux_vision(
                    "openrouter", "tencent/hy3-preview", cfg
                )

    def test_disabled_auxiliary_blocks_known_text_only_main(self):
        from tools.computer_use import vision_routing

        cfg = {
            "auxiliary": {
                "vision": {
                    "enabled": False,
                    "provider": "openrouter",
                    "model": "google/gemini-2.5-flash",
                }
            }
        }
        with patch.object(
            vision_routing,
            "_lookup_supports_vision",
            return_value=False,
        ):
            with pytest.raises(RuntimeError, match="vision_disabled"):
                vision_routing.should_route_capture_to_aux_vision(
                    "deepseek", "deepseek-v4-pro", cfg
                )

    def test_custom_main_honors_configured_native_capability_override(self):
        from tools.computer_use import vision_routing

        cfg = {"model": {"supports_vision": True}}
        with (
            patch(
                "agent.models_dev.get_model_capabilities",
                return_value=None,
            ),
            patch.object(
                vision_routing,
                "_provider_accepts_multimodal_tool_result",
                return_value=True,
            ),
        ):
            assert (
                vision_routing.should_route_capture_to_aux_vision(
                    "custom",
                    "local-vision-model",
                    cfg,
                )
                is False
            )


# ---------------------------------------------------------------------------
# Internal lookups — defensive paths
# ---------------------------------------------------------------------------

class TestLookupHelpers:
    def test_lookup_supports_vision_returns_none_for_blank_provider(self):
        from tools.computer_use.vision_routing import _lookup_supports_vision
        assert _lookup_supports_vision("", "claude") is None

    def test_lookup_supports_vision_returns_none_for_blank_model(self):
        from tools.computer_use.vision_routing import _lookup_supports_vision
        assert _lookup_supports_vision("anthropic", "") is None

    def test_lookup_supports_vision_handles_lookup_exception(self):
        """Underlying caps lookup may raise; helper must swallow + return None."""
        from tools.computer_use import vision_routing

        def _boom(_provider, _model):
            raise RuntimeError("models.dev unreachable")

        with patch("agent.models_dev.get_model_capabilities", side_effect=_boom):
            assert vision_routing._lookup_supports_vision("anthropic", "claude") is None

    def test_lookup_supports_vision_returns_none_when_caps_missing(self):
        from tools.computer_use import vision_routing

        with patch("agent.models_dev.get_model_capabilities", return_value=None):
            assert vision_routing._lookup_supports_vision("anthropic", "claude") is None

    def test_provider_accepts_multimodal_tool_result_returns_none_for_blank_provider(self):
        from tools.computer_use.vision_routing import (
            _provider_accepts_multimodal_tool_result,
        )
        assert _provider_accepts_multimodal_tool_result("", "claude") is None


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

class TestModuleSurface:
    """Pin the public surface so dependents stay in lockstep."""

    def test_should_route_capture_to_aux_vision_is_exported(self):
        from tools.computer_use import vision_routing

        assert "should_route_capture_to_aux_vision" in vision_routing.__all__
        assert callable(vision_routing.should_route_capture_to_aux_vision)

    @pytest.mark.parametrize("name", [
        "_lookup_supports_vision",
        "_provider_accepts_multimodal_tool_result",
    ])
    def test_internal_helpers_are_addressable(self, name):
        """Internal helpers stay importable so tests can monkeypatch them."""
        from tools.computer_use import vision_routing

        assert hasattr(vision_routing, name)
        assert callable(getattr(vision_routing, name))
