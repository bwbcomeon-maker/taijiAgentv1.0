"""Task 9 integration contract for the provider endpoint authority boundary.

The test deliberately exercises the WebUI's real material and public API
projections while keeping credentials and all provider traffic in a temporary
test home.  Network and model-catalog calls remain behind the existing test
network guard; the endpoint resolver/material/projection code is not mocked.
"""

import json
import os
from pathlib import Path
from urllib.request import urlopen

FAKE_GLM_KEY = "task9-fake-glm-cn-key-123456"
FAKE_DEEPSEEK_KEY = "task9-fake-deepseek-key-654321"
BIGMODEL_URL = "https://open.bigmodel.cn/api/paas/v4"
DEEPSEEK_URL = "https://api.deepseek.com/v1"


def _get_json(base_url: str, path: str) -> dict:
    with urlopen(base_url + path, timeout=10) as response:
        return json.loads(response.read())


def _seed_zai_cn_with_stale_residue() -> Path:
    state_dir = Path(os.environ["HERMES_WEBUI_TEST_STATE_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "config.yaml").write_text(
        "model:\n"
        "  provider: zai-cn\n"
        "  default: glm-5\n"
        f"  base_url: {DEEPSEEK_URL}\n"
        "providers:\n"
        "  deepseek:\n"
        f"    base_url: {DEEPSEEK_URL}\n",
        encoding="utf-8",
    )
    (state_dir / ".env").write_text(
        f"GLM_CN_API_KEY={FAKE_GLM_KEY}\n"
        f"DEEPSEEK_API_KEY={FAKE_DEEPSEEK_KEY}\n",
        encoding="utf-8",
    )
    return state_dir / "config.yaml"


def test_zai_cn_chat_material_and_public_endpoint_projections_are_authoritative(
    base_url,
):
    config_path = _seed_zai_cn_with_stale_residue()

    from api import model_config

    material = model_config._main_model_material(
        {
            "model": {
                "provider": "zai-cn",
                "default": "glm-5",
                "base_url": DEEPSEEK_URL,
            },
            "providers": {"deepseek": {"base_url": DEEPSEEK_URL}},
        }
    )
    assert material["provider"] == "zai-cn"
    assert material["model"] == "glm-5"
    assert material["base_url"] == BIGMODEL_URL
    assert material["api_key"] == FAKE_GLM_KEY

    model_config_payload = _get_json(base_url, "/api/model-config")
    providers_payload = _get_json(base_url, "/api/providers")
    public_text = json.dumps(
        {"model_config": model_config_payload, "providers": providers_payload},
        ensure_ascii=False,
    )

    main = model_config_payload["main"]
    endpoint = main["endpoint"]
    assert endpoint["display_url"] == BIGMODEL_URL
    assert endpoint["policy"] == "fixed"
    assert endpoint["override_ignored"] is True
    assert main["provider"] == "zai-cn"
    assert main["model"] == "glm-5"

    rows = {row["id"]: row for row in providers_payload["providers"]}
    assert rows["zai-cn"]["endpoint"] == endpoint
    assert rows["deepseek"]["endpoint"]["display_url"] == DEEPSEEK_URL
    assert DEEPSEEK_URL not in json.dumps(
        {"main": main, "zai_cn": rows["zai-cn"]},
        ensure_ascii=False,
    )

    forbidden = (
        FAKE_GLM_KEY,
        FAKE_DEEPSEEK_KEY,
        "Authorization",
        str(config_path),
        "?token=",
        "@open.bigmodel.cn",
    )
    assert all(value not in public_text for value in forbidden)
