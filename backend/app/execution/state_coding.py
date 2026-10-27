from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

from .dynamic_state import apply_dynamic_state_proposal, parse_state_coder_output, state_coder_instruction


class StateCodingError(RuntimeError):
    pass


def pending_state_actions(dynamic: dict[str, Any], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cursor = dynamic.get("last_coded_event_seq", 0)
    if type(cursor) is not int or cursor < 0:
        raise StateCodingError("Invalid State Coder event cursor.")
    previous = 0
    pending = []
    # Use only this branch's accepted action ledger, never all runs' later database events.
    for action in actions:
        seq = action.get("event_seq")
        if type(seq) is not int or seq <= previous:
            raise StateCodingError("State Coder requires ordered, unique accepted event references.")
        previous = seq
        if seq > cursor:
            pending.append(action)
    if cursor and not any(row["event_seq"] == cursor for row in actions):
        raise StateCodingError("State Coder cursor is not part of this branch's accepted events.")
    return pending


def proposal_from_response(response: dict[str, Any], *, director_envelope: bool = False) -> dict[str, Any]:
    raw = response.get("raw", {})
    choices = raw.get("choices", []) if isinstance(raw, dict) else []
    finish = choices[0].get("finish_reason") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    invalid = isinstance(raw, dict) and (raw.get("mock") or raw.get("done_reason") == "length" or finish in ("length", "max_tokens"))
    return ({"parse_error": "unusable_response"} if invalid else
            parse_state_coder_output(str(response.get("content", "")), director_envelope=director_envelope))


def apply_state_batch(current: dict[str, Any], proposal: Any, *, actions: list[dict[str, Any]],
                      seq: int, round_index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline = copy.deepcopy(current)
    patch = parse_state_coder_output(json.dumps(proposal, ensure_ascii=False, default=str))
    if patch.get("parse_error"):
        next_state = baseline
        result = {"changed": False, "version": current.get("version", 1),
                  "schema_version": current.get("schema_version", 0), "accepted_schema_ops": [],
                  "accepted_state_ops": [], "rejected": [{"kind": "protocol", "reason": patch["parse_error"]}]}
    else:
        next_state, result = apply_dynamic_state_proposal(current, patch, seq=seq, source_actions=actions, round_index=round_index)
    committed = not result["rejected"]
    if not committed:
        next_state = baseline
        result = {**result, "changed": False, "version": current.get("version", 1),
                  "schema_version": current.get("schema_version", 0), "accepted_schema_ops": [], "accepted_state_ops": [],
                  "active_concepts": sum(1 for value in current.get("concepts", {}).values()
                                         if isinstance(value, dict) and value.get("status") == "active")}
    else:
        next_state["last_coded_event_seq"] = max(current.get("last_coded_event_seq", 0),
                                                 max((row["event_seq"] for row in actions), default=0))
    result["coding"] = {"status": "coded" if committed else "pending",
                        "last_coded_event_seq": next_state.get("last_coded_event_seq", 0),
                        "through_event_seq": max((row["event_seq"] for row in actions), default=0),
                        "action_count": len(actions)}
    return next_state, result


async def encode_state_batch(*, provider: Any, state: dict[str, Any], proposal: Any,
                             actions: list[dict[str, Any]], seq: int, round_index: int,
                             record: Callable[[dict[str, Any], dict[str, Any]], None]) -> None:
    """Reuse the audit's patch; only a failed batch permits one additional coding request."""
    for attempt in (1, 2):
        next_state, result = apply_state_batch(state.get("dynamic_state", {}), proposal,
                                              actions=actions, seq=seq, round_index=round_index)
        result["coding"]["attempt"] = attempt
        record(next_state, result)
        if result["coding"]["status"] == "coded":
            return
        if attempt == 2:
            raise StateCodingError("State coding failed after one repair; events remain uncoded. Inspect dynamic_state_updated events.")
        response = await provider.chat(
            model="", system_prompt="You are a State Coder repairing an uncommitted batch, not a director or participant.",
            user_prompt=state_coder_instruction(state=state, actions=actions, round_index=round_index)
            + "\nThe previous batch was not committed. Correct the patch against the SAME actions and state. "
              "Return explicit empty schema_ops/state_ops only if no persistent update is warranted. Safe errors:\n"
            + json.dumps([{"kind": item["kind"], "reason": item["reason"]} for item in result["rejected"]]))
        proposal = proposal_from_response(response)
