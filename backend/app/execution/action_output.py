from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, ValidationError, model_validator


class ActionOutputError(ValueError):
    code = "invalid_action_output"

    def __init__(self, issues: list[dict[str, str]]) -> None:
        super().__init__("Action output does not match the participant protocol; no action was accepted.")
        self.issues = issues


class ParticipantAction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    participation: Literal["act", "observe", "wait"]
    action: StrictStr
    intent: StrictStr = ""
    turn_delta: StrictStr = ""
    observation: StrictStr = ""
    self_update: StrictStr = ""
    shared_effect_claim: StrictStr = ""
    episode_signal: Literal["continue", "ready"] = "continue"
    result: StrictBool | None = None

    @model_validator(mode="after")
    def require_action(self) -> ParticipantAction:
        self.action = self.action.strip()
        if self.participation == "act" and not self.action:
            raise ValueError("act requires nonempty action")
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")


def normalize_action_output(output: dict[str, Any], *, round_index: int, action_index: int) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ActionOutputError([{"field": "output", "type": "object_required"}])
    raw = output.get("raw", {})
    if isinstance(raw, dict):
        choices = raw.get("choices", [])
        finish = (choices[0].get("finish_reason")
                  if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None)
        if raw.get("mock") or finish in ("length", "max_tokens") or raw.get("done_reason") == "length":
            raise ActionOutputError([{"field": "content", "type": "mock_or_truncated_output"}])
    parsed = output
    if "action" not in output and "participation" not in output:
        content = output.get("content")
        if not isinstance(content, str):
            raise ActionOutputError([{"field": "content", "type": "json_string_required"}])
        content = content.strip()
        if content.startswith("```") and content.endswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            parsed = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (ValueError, TypeError) as exc:
            raise ActionOutputError([{"field": "content", "type": "invalid_json"}]) from exc
    try:
        action = ParticipantAction.model_validate(parsed)
    except ValidationError as exc:
        raise ActionOutputError([{"field": ".".join(map(str, row["loc"])) or "output", "type": row["type"]}
                                 for row in exc.errors(include_input=False, include_context=False, include_url=False)]) from exc
    data = action.model_dump(exclude_none=True)
    metadata = {key: output[key] for key in ("role", "raw", "agent_id", "protocol_version", "metrics", "status") if key in output}
    return {**metadata, **data, "content": data["action"], "round": round_index, "action_index": action_index}


def external_action_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ActionOutputError([{"field": "output", "type": "object_required"}])
    if "action" in output or "participation" in output:
        return output
    text = output.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ActionOutputError([{"field": "text", "type": "nonempty_text_required"}])
    if text.lstrip().startswith(("{", "[", "```")):
        return {"content": text}
    return {"participation": "act", "action": text}
