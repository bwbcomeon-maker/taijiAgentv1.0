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
    strict_model_messages: bool = False
    tools_disabled: bool = False

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
        strict_model_messages: bool = False,
        tools_disabled: bool = False,
    ) -> "TurnEnvelope":
        envelope = cls(
            turn_id=str(turn_id),
            session_id=str(session_id),
            submitted_at=float(submitted_at),
            display_user_message=str(display_user_message or ""),
            model_messages=tuple(copy.deepcopy(message) for message in (model_messages or [])),
            attachments=tuple(copy.deepcopy(attachment) for attachment in (attachments or [])),
            strict_model_messages=bool(strict_model_messages),
            tools_disabled=bool(tools_disabled),
        )
        if envelope.strict_model_messages:
            envelope._validate_strict_contract()
        elif envelope.tools_disabled:
            raise ValueError("tools_disabled requires strict model messages")
        return envelope

    def _validate_strict_contract(self) -> None:
        messages = list(self.model_messages)
        valid = (
            self.tools_disabled
            and not self.attachments
            and len(messages) == 2
            and [message.get("role") if isinstance(message, dict) else None for message in messages]
            == ["system", "user"]
            and all(
                isinstance(message, dict)
                and set(message) == {"role", "content"}
                and isinstance(message.get("content"), str)
                and bool(message["content"].strip())
                for message in messages
            )
        )
        if not valid:
            raise ValueError(
                "strict model messages require exactly one non-empty system message, "
                "one non-empty user message, no attachments, and tools_disabled=true"
            )

    def with_model_messages(self, model_messages) -> "TurnEnvelope":
        """Return an effective envelope isolated from caller-owned request data."""
        if self.strict_model_messages:
            candidate = tuple(copy.deepcopy(message) for message in (model_messages or []))
            if candidate != self.model_messages:
                raise ValueError("strict model messages cannot be replaced by session history")
            return self
        return replace(
            self,
            model_messages=tuple(
                copy.deepcopy(message) for message in (model_messages or [])
            ),
        )

    def model_messages_for_runtime(self, ordinary_messages=None) -> list[dict[str, Any]]:
        """Resolve model input without allowing strict turns to absorb chat state."""
        if self.strict_model_messages:
            self._validate_strict_contract()
            source = self.model_messages
        else:
            source = ordinary_messages if ordinary_messages is not None else self.model_messages
        return [copy.deepcopy(message) for message in source]

    @property
    def platform_message_id(self) -> str:
        return f"webui-turn:{self.turn_id}"
