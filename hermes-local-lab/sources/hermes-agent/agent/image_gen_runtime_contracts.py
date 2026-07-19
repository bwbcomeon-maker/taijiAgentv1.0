"""Import-safe runtime identities shared by image verification and Providers."""

from __future__ import annotations

from typing import Any


_BUILTIN_IMAGE_RUNTIME_CONTRACTS: dict[str, dict[str, str]] = {
    "dashscope": {
        "transport": "dashscope_native_image_generation",
        "endpoint": "",
    },
    "doubao": {
        "transport": "volcengine_ark_images",
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3/images/generations",
    },
    "qianfan": {
        "transport": "qianfan_images",
        "endpoint": "https://qianfan.baidubce.com/v2/images/generations",
    },
    "zhipu-image": {
        "transport": "zhipu_images",
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/images/generations",
    },
    "minimax-image": {
        "transport": "minimax_images",
        "endpoint": "https://api.minimax.io/v1/image_generation",
    },
}

VERIFIABLE_BUILTIN_IMAGE_PROVIDERS = frozenset(
    _BUILTIN_IMAGE_RUNTIME_CONTRACTS
)


def builtin_image_runtime_contract(provider: Any) -> dict[str, str]:
    """Return a copy of the canonical transport and fixed endpoint contract."""
    normalized = str(provider or "").strip().lower()
    return dict(_BUILTIN_IMAGE_RUNTIME_CONTRACTS.get(normalized, {}))
