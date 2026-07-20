"""Vision-routing decisions for ``computer_use`` capture results.

Background
----------
``computer_use(action='capture', mode='som'|'vision')`` returns a
``_multimodal`` envelope containing the captured screenshot. That envelope
is delivered back to the **active session model** as the tool result. When
the active main model has no vision capability (e.g. text-only or
text+code-only models), or when the active provider rejects multimodal
content inside tool-result messages, the screenshot trips a 404 / 400 at
the provider boundary and the agent loop reports a hard tool failure.

Issue #24015 reports this regression for the ``cua-driver`` backend:
configuring ``auxiliary.vision`` (a dedicated vision-capable model) in
``config.yaml`` was silently ignored — the screenshot was still routed at
the *main* model and failed with HTTP 404 ``No endpoints found that
support image input`` even though a perfectly good vision backend was
sitting in config waiting to be used.

This module centralises the small policy decision: should a captured
screenshot be returned as multimodal content (main model handles vision
natively) or pre-analysed via the auxiliary vision pipeline so the main
model only ever sees text?

Behaviour (mirrors the canonical image-input router)
----------------------------------------------------
* A main model explicitly known to support vision uses the native route,
  provided its tool-result transport can safely carry image media.
* A main model explicitly known to be text-only may use the auxiliary
  pipeline only while that capability is enabled.
* Unknown model capability, unknown/unsupported native transport, and a
  disabled auxiliary fallback all fail closed before any image bytes or
  Provider request are emitted.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _lookup_supports_vision(
    provider: str,
    model: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[bool]:
    """Use the canonical override-aware main-model capability resolver."""
    try:
        from agent.image_routing import _lookup_supports_vision as resolve

        return resolve(provider, model, cfg)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "computer_use vision_routing: caps lookup failed for %s:%s — %s",
            provider, model, exc,
        )
        return None


def _provider_accepts_multimodal_tool_result(provider: str, model: str) -> Optional[bool]:
    """Return whether *provider*+*model* carries images inside tool-result messages.

    Reuses ``tools.vision_tools._supports_media_in_tool_results`` so the
    capture-routing decision stays in lockstep with the
    ``vision_analyze`` native fast path. Returns None on import failure
    so callers fall back to aux routing rather than guessing.
    """
    if not provider:
        return None
    try:
        from tools.vision_tools import _supports_media_in_tool_results
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "computer_use vision_routing: tool-result support lookup failed: %s",
            exc,
        )
        return None
    return bool(_supports_media_in_tool_results(provider, model))


def should_route_capture_to_aux_vision(
    provider: str,
    model: str,
    cfg: Optional[Dict[str, Any]],
) -> bool:
    """Return True iff the captured screenshot should be pre-analysed via aux vision.

    Args:
      provider: active inference provider id (e.g. ``"openrouter"``,
        ``"anthropic"``, ``"openai-codex"``). Lower-case canonical id.
      model:    active main model slug as it would be sent to the provider.
      cfg:      loaded ``config.yaml`` dict (or None).

    Returns:
      ``True`` when the caller should hand the screenshot to the aux vision
      pipeline (and surface a text-only tool result). ``False`` when the
      caller should keep the existing multimodal envelope (main model
      handles vision natively).
    """
    supports_vision = _lookup_supports_vision(provider, model, cfg)
    if supports_vision is True:
        accepts_tool_image = _provider_accepts_multimodal_tool_result(
            provider,
            model,
        )
        if accepts_tool_image is None:
            raise RuntimeError("native_tool_media_unknown")
        if accepts_tool_image is False:
            raise RuntimeError("native_tool_media_unsupported")
        return False
    if supports_vision is None:
        raise RuntimeError("main_model_capability_unknown")

    from agent.image_routing import _vision_capability_enabled

    if not _vision_capability_enabled(cfg):
        raise RuntimeError("vision_disabled")
    return True


__all__ = [
    "should_route_capture_to_aux_vision",
]
