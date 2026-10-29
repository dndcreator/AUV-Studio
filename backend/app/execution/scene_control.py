from __future__ import annotations

import copy
import json
from typing import Any


def _choice(value: Any, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def normalize_scene_contract(raw: Any, state: dict[str, Any]) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    task = state.get("task_contract", {})
    task = task if isinstance(task, dict) else {}
    phases = state.get("phases", [])
    index = int(state.get("current_phase_index", 0) or 0)
    phase = phases[index] if isinstance(phases, list) and 0 <= index < len(phases) else {}
    phase = phase if isinstance(phase, dict) else {}
    exits = source.get("exit_conditions")
    if not isinstance(exits, list) or not any(isinstance(item, str) and item.strip() for item in exits):
        exits = [phase.get("exit_criteria") or task.get("completion_condition") or "The current goal is resolved or a meaningful new situation warrants transition."]
    return {
        "goal": str(source.get("goal") or phase.get("objective") or task.get("objective") or state.get("research_question") or "Develop the current situation toward the user's goal.")[:1200],
        "pacing": _choice(source.get("pacing"), {"slow", "steady", "fast"}, "steady"),
        "detail": _choice(source.get("detail"), {"low", "medium", "high"}, "high" if state.get("detail_granularity") == "detailed" else "medium"),
        "progress_scale": _choice(source.get("progress_scale"), {"micro", "meso", "macro"}, "meso"),
        "exit_conditions": [str(item).strip()[:600] for item in exits if isinstance(item, str) and item.strip()][:6],
    }


def init_scene_control() -> dict[str, Any]:
    return {"contract": {}, "stagnation": 0, "goal_stagnation": 0, "last_audited_round": 0}


def scene_progress_feedback(scene_control: dict[str, Any]) -> dict[str, str]:
    streak = int(scene_control.get("goal_stagnation", 0))
    if streak < 2:
        return {}
    if streak < 4:
        level = "refocus"
        instruction = "Prioritize a grounded contribution toward the current exit conditions rather than repeating positions."
    elif streak < 6:
        level = "reassess"
        instruction = "Reassess the established obstacles, available opportunities and time pressure; do not invent a new deadline or event."
    else:
        level = "consider_stop"
        instruction = "Reassess whether useful progress remains possible; the Director may stop honestly with task_complete false."
    return {"level": level, "instruction": instruction + " Preserve participant autonomy, knowledge boundaries, detail and pacing; do not force an outcome."}


def scene_setup_instruction() -> str:
    return (
        "As the Director, also establish the current Scene Contract before participants act. "
        "Infer it from the task, current phase, user preferences, and established situation. Include scene_contract: "
        '{"goal":"current question or development","pacing":"slow|steady|fast",'
        '"detail":"low|medium|high","progress_scale":"micro|meso|macro",'
        '"exit_conditions":["semantic condition"]}. '
        "Pacing is the speed of development, not response latency; detail is selective richness, not a word quota; "
        "progress_scale is the size of meaningful change. High detail and micro progress can coexist. "
        "Set boundaries without prescribing participant actions or predetermined outcomes. "
    )


def scene_audit_instruction(
    *,
    state: dict[str, Any],
    scene_control: dict[str, Any],
    recent_actions: list[dict[str, Any]],
    round_index: int,
    max_actions: int,
    connections: list[dict[str, Any]] | None = None,
    participants: list[dict[str, Any]] | None = None,
    allowance: dict[str, Any] | None = None,
    uncoded_actions: list[dict[str, Any]] | None = None,
) -> str:
    first_round = int(scene_control.get("last_audited_round", 0)) + 1
    earlier = [item for item in recent_actions if int(item.get("round", 0)) < first_round][-4:]
    pending = [item for item in recent_actions if int(item.get("round", 0)) >= first_round]
    audit_actions = []
    seen = set()
    for item in earlier + pending + (uncoded_actions or []):
        key = item.get("event_seq")
        if key is not None and key in seen:
            continue
        seen.add(key)
        audit_actions.append(item)
    if all(type(item.get("event_seq")) is int for item in audit_actions):
        audit_actions.sort(key=lambda item: item["event_seq"])
    return (
        "Audit the boundary of this distributed process episode. Nodes own decisions and actions; do not "
        "invent their actions, conclusions, plot, or research findings. Private intent, observation and self_update "
        "are audit-only knowledge: do not disclose them in shared guidance, contracts or summaries unless the actor "
        "has publicly revealed them through observable action. Respect messages actually delivered on the listed "
        "directed channels. A public action "
        "does not imply every participant received it. Do not turn a one-way channel into a return channel, "
        "invent an extra holder of an existing role, or relay message contents through global guidance. Decide whether the current episode "
        "should continue, transition to a new macro episode, or stop. This applies to simulation, research, "
        "collaboration, and roleplay. Return JSON only: "
        '{"decision":"continue|transition|stop","task_complete":false,"completion_reason":"evidence-based goal assessment",'
        '"guidance":"short boundary constraint only",'
        f'"reason":"brief audit reason","round_progress":[{{"round":{first_round},"stagnant":false,"reason":"observed change or justified waiting",'
        '"goal_progress":"advanced|none|regressed|uncertain","goal_reason":"evidence of movement toward scene exit conditions"}],'
        '"scene_contract":null,"state_patch":{"schema_ops":[],"state_ops":[]}}. '
        f"Assess EACH completed round {first_round} through {round_index}, in order, using actual actions and "
        f"preserving these global round IDs: {list(range(first_round, round_index + 1))}. Do not renumber a later batch from 1. Use "
        "prior context. Turn Delta is a participant claim, not proof: compare it with what actually happened. "
        "Judge the whole interaction, not one inactive participant. A grounded new response, information, decision, "
        "or small relevant shift can count. Waiting, silence, and gradual development can be appropriate. Mark "
        "stagnant only when the round repeats or adds no useful development relative to the scene goal and "
        "progress_scale; never use word count as quality or require drama every turn. Slow pacing and high detail "
        "must not be mistaken for stagnation. High detail means selectively depicting meaningful particulars. "
        "Assess goal_progress SEPARATELY from novelty: advanced means actual accepted contributions reduce the gap "
        "to the current exit conditions at the contracted progress_scale; none means new content without that movement; "
        "regressed means the gap widened; uncertain means the evidence is insufficient. Explain goal_reason from "
        "actual accepted actions, not Turn Delta, word count, intention or self-reported success. An emotional shift "
        "can be goal progress when the goal calls for it; mere novelty is not automatically progress toward resolution. "
        "If two consecutive rounds show none/regressed, refocus the scene's advancement requirement. If this "
        "persists for four rounds, reassess established obstacles, opportunities and existing time pressure. At six "
        "rounds consider an honest incomplete stop if no useful path remains. Include prior goal_stagnation in "
        "these counts. These are feedback thresholds, not forced pacing or mandatory stopping: respect slow/micro "
        "development, do not fabricate pressure, transitions, role decisions or a predetermined result. "
        "If two consecutive completed rounds stagnate (including the previous counter), consider changing pacing "
        "or progress_scale, requesting closure, transition, or stopping; a justified continue is allowed. "
        "Before deciding to continue, separately check the scene exit_conditions and the overall task completion_condition. "
        "Resolve role descriptions against PARTICIPANT ROSTER: the listed owner already performs that role; "
        "do not create an offscreen duplicate to receive the deliverable. Use received_event_seqs as evidence of "
        "which messages were actually supplied before a participant acted, not merely a possible graph path. "
        "If the completion condition asks for a proposal, do not extend it into execution and acknowledgment. "
        "Listed exit conditions are alternatives unless the contract explicitly requires all. Once a sufficient exit "
        "condition and the task goal are met, stop even if more natural conversation is possible. New small changes do "
        "not by themselves justify extending a completed task. If only the scene is complete and the task remains open, "
        "transition instead. Leaving unresolved background or private subplots is acceptable when the task allows it. "
        "Do not replace participant choices with scripted actions. Keep the Scene Contract stable: on continue, "
        "only tune pacing, detail, or progress_scale if needed; on transition, supply the next scene_contract with "
        "a new goal and exit_conditions. Leave it null otherwise. Stop when the task completion condition is "
        "satisfied or further simulation has no useful purpose; action/round limits are ceilings, not targets. "
        "task_complete is true ONLY when accepted actions satisfy the actual requested end condition; never for "
        "an exhausted budget, a stalled interaction or merely stopping. In those cases stop with task_complete false. "
        "Use remaining allowance to tune pacing/scale toward the requested scope without scripting actors or "
        "inventing outcomes. A requested final segment does not need another setup phase. "
        f"EXECUTION ALLOWANCE:\n{json.dumps(allowance or {}, ensure_ascii=False)}\n"
        f"GOAL FEEDBACK:\n{json.dumps(scene_progress_feedback(scene_control), ensure_ascii=False)}\n"
        f"Hard action limit: {max_actions}. Turn Delta and progress verdicts are runtime signals, not durable facts.\n\n"
        f"SCENE CONTROL:\n{json.dumps(scene_control, ensure_ascii=False, default=str)}\n\n"
        f"CURRENT MACRO STATE:\n{json.dumps(state, ensure_ascii=False, default=str)}\n\n"
        f"PARTICIPANT ROSTER:\n{json.dumps(participants or [], ensure_ascii=False, default=str)}\n\n"
        f"ACTUAL NODE OUTPUTS:\n{json.dumps(audit_actions, ensure_ascii=False, default=str)}"
        + f"\nCOMMUNICATION CHANNELS (empty means shared public scene):\n{json.dumps(connections or [], ensure_ascii=False)}"
    )


def apply_scene_audit(
    current: dict[str, Any],
    control: dict[str, Any],
    *,
    state: dict[str, Any],
    round_index: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = copy.deepcopy(current)
    previous_round = int(result.get("last_audited_round", 0))
    judgments: dict[int, dict[str, Any]] = {}
    seen_rounds: set[int] = set()
    ambiguous_rounds: set[int] = set()
    raw_progress = control.get("round_progress", [])
    for item in raw_progress if isinstance(raw_progress, list) else []:
        if not isinstance(item, dict):
            continue
        value = item.get("round")
        if type(value) is int and previous_round < value <= round_index:
            if value in seen_rounds:
                ambiguous_rounds.add(value)
            seen_rounds.add(value)
            if type(item.get("stagnant")) is not bool:
                continue
            judgments[value] = {"round": value, "stagnant": item["stagnant"], "reason": str(item.get("reason", ""))[:800]}
            goal = item.get("goal_progress")
            reason = item.get("goal_reason")
            if (isinstance(goal, str) and goal in {"advanced", "none", "regressed", "uncertain"}
                    and isinstance(reason, str) and reason.strip()):
                judgments[value].update(goal_progress=goal, goal_reason=reason.strip()[:800])
    for value in ambiguous_rounds:
        judgments.pop(value, None)
    streak = int(result.get("stagnation", 0))
    goal_streak = int(result.get("goal_stagnation", 0))
    needs_attention = False
    goal_needs_attention = False
    for completed_round in range(previous_round + 1, round_index + 1):
        judgment = judgments.get(completed_round)
        # Missing semantic judgments cannot establish consecutive stagnation.
        streak = streak + 1 if judgment and judgment["stagnant"] else 0
        needs_attention = needs_attention or streak >= 2
        goal = judgment.get("goal_progress") if judgment else None
        # Missing/uncertain assessments are not progress and cannot prove consecutive lack of progress.
        goal_streak = goal_streak + 1 if goal in {"none", "regressed"} else 0
        goal_needs_attention = goal_needs_attention or goal_streak >= 2
    assessment = {
        "round_progress": [judgments[key] for key in sorted(judgments)],
        "stagnation": streak,
        "needs_attention": needs_attention,
        "goal_stagnation": goal_streak,
        "goal_needs_attention": goal_needs_attention,
        "unassessed_goal_rounds": [value for value in range(previous_round + 1, round_index + 1)
                                   if value not in judgments or judgments[value].get("goal_progress") in {None, "uncertain"}],
        "unassessed_rounds": [value for value in range(previous_round + 1, round_index + 1) if value not in judgments],
    }
    result["stagnation"] = streak
    result["goal_stagnation"] = goal_streak
    result["last_audited_round"] = max(previous_round, round_index)
    patch = control.get("scene_contract")
    if control.get("decision") == "transition":
        result["contract"] = normalize_scene_contract(patch, state)
        result["stagnation"] = 0
        result["goal_stagnation"] = 0
    elif isinstance(patch, dict):
        contract = result.get("contract", {})
        tunable = {key: patch[key] for key in ("pacing", "detail", "progress_scale") if key in patch}
        result["contract"] = normalize_scene_contract({**contract, **tunable}, state)
    return result, assessment
