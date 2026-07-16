"""Internal identity and model-input envelope for one accepted WebUI turn."""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class TurnEnvelope:
    turn_id: str
    session_id: str
    submitted_at: float
    display_user_message: str
    model_messages: tuple[dict[str, Any], ...]
    attachments: tuple[dict[str, Any], ...]

    @classmethod
    def create(
        cls,
        *,
        turn_id: str,
        session_id: str,
        submitted_at: float,
        display_user_message: str,
        model_messages,
        attachments,
    ) -> "TurnEnvelope":
        return cls(
            turn_id=str(turn_id),
            session_id=str(session_id),
            submitted_at=float(submitted_at),
            display_user_message=str(display_user_message or ""),
            model_messages=tuple(copy.deepcopy(message) for message in (model_messages or [])),
            attachments=tuple(copy.deepcopy(attachment) for attachment in (attachments or [])),
        )

    def with_model_messages(self, model_messages) -> "TurnEnvelope":
        """Return an effective envelope isolated from caller-owned request data."""
        return replace(
            self,
            model_messages=tuple(
                copy.deepcopy(message) for message in (model_messages or [])
            ),
        )

    @property
    def platform_message_id(self) -> str:
        return f"webui-turn:{self.turn_id}"
