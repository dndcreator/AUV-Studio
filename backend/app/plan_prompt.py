from __future__ import annotations

import copy
import json
from typing import Any

from .evidence import compact_evidence
from .schemas import PlanCompileRequest


PLAN_SYSTEM = (
    "You are a process architect for a distributed multi-agent simulation. Extract only semantic decisions, "
    "not an implementation blueprint. Python supplies IDs, defaults, wiring and structural boilerplate. "
    "Preserve user intent and uncertainty without repeating the source across fields. Return complete JSON only. "
    "Participants control only themselves; never prewrite their outcomes. Private knowledge and motives belong "
    "only to the relevant role, never in shared initial_state or constraints."
)


def plan_prompt(req: PlanCompileRequest, *, core_only: bool = False) -> str:
    shape: dict[str, Any] = {
        "intent_mode": "roleplay|research|custom", "intent_submode": "none|simulation|research|consulting",
        "goal": "actual objective", "initial_state": "public starting situation only",
        "completion_condition": "observable end condition for the requested segment",
        "constraints": ["nonredundant shared constraints"],
        "deliverable": {"format": "story|dialogue|report|simulation|custom", "style": "requested expression"},
        "roles": [{"name": "name", "objective": "own interest or assignment", "entity_type": "individual|group|organization|environment|event|artifact",
                   "knowledge": ["own relevant knowledge"], "boundaries": ["own behavioral limits"]}],
    }
    if not core_only:
        shape.update({"assumptions": ["uncertain defaults only"],
                      "environment": {"time_context": "if known", "spatial_context": "if known"}})
    research = ""
    if req.mode not in {"roleplay", "custom"}:
        research = ("Only IF intent_mode is research, optionally add research: {methodology, sample_logic, "
                    "questions:[], hypotheses:[], decision_criteria:[], bias_risks:[], validation_plan:[]}. "
                    "Use the chosen submode: simulation for evolution, research for simulated fieldwork, consulting "
                    "for professional workstreams. Omit this object otherwise. ")
    evidence = compact_evidence(req.evidence_pack) if req.evidence_pack else None
    if core_only:
        shape["roles"] = [{"name": "name", "objective": "own interest", "entity_type": "entity type"}]
        instructions = (
            "CORE RECOVERY FORMAT: replace the previous format, not a continuation. Return ONLY the small JSON shape below. "
            "No optional sections. Keep semantic values concise; add role knowledge/boundaries only when essential to "
            "preserve the user's private information or explicit limits. Do not prewrite outcomes. "
        )
    else:
        instructions = (
            "COMPACT PLANNING FORMAT: Do not return workflow nodes/edges, research boilerplate for nonresearch tasks, "
            "or copies of the original request. Use short values (one sentence per field, short lists); optional fields may be omitted. "
            "Roles may add count only for requested repetition; otherwise count=1. Their identity is an in-world or "
            "professional participant, not a writer, plot device or omniscient narrator unless explicitly requested. "
            "Do not force a full-story arc: an ending begins at its final confrontation, not world setup. "
            "Choose the mode from meaning when mode=auto; honor an explicit mode and submode. "
            "Keep style/format separate from authority over other roles. Unknown coordinates may be omitted. "
        )
    return (
        instructions
        + research
        + f"\nMode: {req.mode}; submode: {req.submode or 'auto'}; max_agents: {max(1, min(req.max_agents, 12))}; language: {req.language}.\n"
        + "Output shape (semantic decisions only):\n" + json.dumps(shape, ensure_ascii=False)
        + "\nOriginal user request (preserved verbatim by the application; do not repeat it):\n" + req.plan_text
        + "\nUser-provided evidence (not assumptions):\n" + (json.dumps(evidence, ensure_ascii=False) if evidence else "none")
    )


def expand_plan_core(parsed: dict[str, Any]) -> dict[str, Any]:
    """Fill the existing compiler DTO from a small semantic response; do not invent facts or actors."""
    data = copy.deepcopy(parsed)
    task = data.get("task_contract", {})
    task = task if isinstance(task, dict) else {}
    deliverable = data.get("deliverable", {})
    if isinstance(deliverable, dict) and deliverable:
        task.setdefault("deliverable", deliverable)
        data["deliverable"] = deliverable.get("format", "simulation")
        data.setdefault("output_contract", {"format": deliverable.get("format", "simulation")})
    task.setdefault("completion_condition", data.get("completion_condition", ""))
    task.setdefault("shared_background", data.get("initial_state", ""))
    task.setdefault("shared_constraints", data.get("constraints", []))
    data["task_contract"] = task
    design = data.get("simulation_design", {})
    design = design if isinstance(design, dict) else {}
    design.setdefault("initial_state", data.get("initial_state", ""))
    data["simulation_design"] = design
    env = data.get("environment", {})
    env = env if isinstance(env, dict) else {}
    env.setdefault("scenario", data.get("initial_state", ""))
    data["environment"] = env
    for role in data.get("roles", []):
        identity = role.get("identity_contract", {})
        identity = identity if isinstance(identity, dict) else {}
        for field in ("objective", "knowledge", "unknowns", "capabilities", "boundaries"):
            if field in role:
                identity.setdefault(field, role[field])
        role["identity_contract"] = identity
        role.setdefault("focus", role.get("objective", ""))
    research = data.get("research", {})
    if isinstance(research, dict) and research:
        data.setdefault("research_design", {key: research[key] for key in
                        ("methodology", "sample_logic", "bias_risks", "validation_plan") if key in research})
        for field, source in (("key_questions", "questions"), ("hypotheses", "hypotheses"), ("decision_criteria", "decision_criteria")):
            data.setdefault(field, research.get(source, []))
    return data
