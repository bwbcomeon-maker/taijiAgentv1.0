"""Import-stable multiprocessing targets for WebUI credential tests.

Pytest can import the same ``tests`` package from both hermes-agent and
hermes-webui.  Spawned interpreters therefore must not unpickle targets from a
``tests.test_*`` module whose package identity depends on collection order.
"""

from __future__ import annotations

import os
from pathlib import Path


def exact_pair_process_writer(
    config_path: str,
    entered_event,
    release_event,
) -> None:
    from agent.provider_credentials import mutate_config_env_strict

    def mutate(config: dict) -> None:
        entered_event.set()
        if not release_event.wait(timeout=10):
            raise RuntimeError("timed out waiting to release exact pair writer")
        config["pair_writer"] = "committed"

    mutate_config_env_strict(
        mutate,
        {"PAIR_WRITER_KEY": "pair-value"},
        config_path=Path(config_path),
    )


def oauth_anthropic_clear_process(
    config_path: str,
    started_event,
    completed_event,
) -> None:
    from api.oauth import _clear_anthropic_env_values

    started_event.set()
    _clear_anthropic_env_values(Path(config_path))
    completed_event.set()


def crash_main_model_pair_after_first_replace(config_path: str) -> None:
    os.environ["HERMES_CONFIG_PATH"] = config_path
    os.environ["HERMES_HOME"] = str(Path(config_path).parent)

    import agent.provider_credentials as credential_store
    from api.model_config import set_main_model_config

    original_replace = credential_store._replace_credential_stage
    replaced = 0

    def crash_after_first_replace(stage_path, **kwargs):
        nonlocal replaced
        original_replace(stage_path, **kwargs)
        replaced += 1
        if replaced == 1:
            os._exit(91)

    credential_store._replace_credential_stage = crash_after_first_replace
    set_main_model_config(
        {
            "provider": "custom",
            "model": "crash-recovery-model",
            "base_url": "https://models.example.com/v1",
            "api_key": "crash-recovery-secret",
        }
    )
