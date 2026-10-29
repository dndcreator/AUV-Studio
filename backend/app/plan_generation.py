from __future__ import annotations

import json
import logging
import re
from typing import Any

from .execution.provider import ProviderError
from .config import settings

logger = logging.getLogger(__name__)


class PlanGenerationError(RuntimeError):
    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        self.diagnostics = diagnostics
        self.code = diagnostics[-1]["code"]
        super().__init__(f"Plan generation failed: {self.code}; attempts={len(diagnostics)}; "
                         f"finish_reason={diagnostics[-1]['finish_reason']}. No fallback draft was applied.")


def parse_plan_object(content: str) -> dict[str, Any] | None:
    candidate = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", candidate)
        if match is None:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _has_participants(value: dict[str, Any]) -> bool:
    roles = value.get("roles")
    valid = (isinstance(value.get("goal"), str) and bool(value["goal"].strip())
            and isinstance(roles, list) and bool(roles)
            and all(isinstance(role, dict) and isinstance(role.get("name"), str) and bool(role["name"].strip())
                    and type(role.get("count", 1)) is int and role.get("count", 1) > 0 for role in roles))
    if not valid:
        return False
    if any(key in value and not isinstance(value[key], str) for key in ("initial_state", "completion_condition")):
        return False
    for role in roles:
        if "objective" in role and not isinstance(role["objective"], str):
            return False
        for key in ("knowledge", "unknowns", "capabilities", "boundaries"):
            if key in role and (not isinstance(role[key], list) or any(not isinstance(item, str) for item in role[key])):
                return False
    return True


async def generate_plan_object(provider: Any, system_prompt: str, user_prompt: str, *, retry_prompt: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    output_limit = getattr(provider, "planning_output_tokens", settings.planning_output_tokens)
    for attempt in range(1, 3):
        limit = output_limit if attempt == 1 else min(output_limit, 1600)
        scoped = provider.with_output_limit(limit) if hasattr(provider, "with_output_limit") else provider
        # Transport/budget exceptions deliberately propagate. Provider owns transport retry policy.
        try:
            response = await scoped.chat(model="", system_prompt=system_prompt, user_prompt=user_prompt)
        except Exception as exc:
            logger.warning("Plan generation request failed: type=%s code=%s status=%s",
                           type(exc).__name__, getattr(exc, "code", "request_failed"), getattr(exc, "status_code", None))
            raise
        content = str(response.get("content", ""))
        raw = response.get("raw", {})
        raw = raw if isinstance(raw, dict) else {}
        if raw.get("mock") is True:
            raise ProviderError("Configure a real model before compiling a plan.", retryable=False, code="model_not_configured")
        choices = raw.get("choices", [])
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        finish = str(choice.get("finish_reason") or (raw.get("done_reason") if raw.get("done") else None) or "unknown")
        parsed = parse_plan_object(content)
        code = ("output_truncated" if finish in {"length", "max_tokens"} else "invalid_json" if parsed is None
                else "invalid_plan_schema" if not _has_participants(parsed) else "")
        if not code:
            return parsed, {"attempts": attempt, "recovered_errors": failures, "format": "compact" if attempt == 1 else "core",
                            "output_limit": limit}
        failure = {"attempt": attempt, "code": code, "finish_reason": str(finish)[:80], "response_chars": len(content),
                   "format": "compact" if attempt == 1 else "core", "output_limit": limit}
        failures.append(failure)
        # Metadata only: neither account credentials nor private model text go into logs.
        logger.warning("Plan generation response rejected: %s", failure)
        user_prompt = retry_prompt or (
            'Return only a complete core JSON object {"goal":"...","roles":[{"name":"...","objective":"..."}]}. '
            'No optional planning fields. Original task data:\n' + user_prompt
        )
    raise PlanGenerationError(failures)
