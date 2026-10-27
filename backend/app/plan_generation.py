from __future__ import annotations

import json
import logging
import re
from typing import Any

from .execution.provider import ProviderError

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
    return (isinstance(value.get("goal"), str) and bool(value["goal"].strip())
            and isinstance(roles, list) and bool(roles)
            and all(isinstance(role, dict) and isinstance(role.get("name"), str) and bool(role["name"].strip())
                    and type(role.get("count", 1)) is int and role.get("count", 1) > 0 for role in roles))


async def generate_plan_object(provider: Any, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for attempt in range(1, 3):
        # Transport/budget exceptions deliberately propagate. Provider owns transport retry policy.
        try:
            response = await provider.chat(model="", system_prompt=system_prompt, user_prompt=user_prompt)
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
            return parsed, {"attempts": attempt, "recovered_errors": failures}
        failure = {"attempt": attempt, "code": code, "finish_reason": str(finish)[:80], "response_chars": len(content)}
        failures.append(failure)
        # Metadata only: neither account credentials nor private model text go into logs.
        logger.warning("Plan generation response rejected: %s", failure)
        user_prompt += (
            f"\n\nPrevious response failed validation ({code}). Regenerate one COMPLETE compact JSON object, "
            "including goal and named roles. Prioritize task contract and participant identities; do not repeat facts "
            "across sections. Use empty arrays/objects for inapplicable research fields. No prose or code fences. "
            "Keep within the existing output budget; never omit closing delimiters."
        )
    raise PlanGenerationError(failures)
