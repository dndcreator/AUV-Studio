from __future__ import annotations

import json
import re
import uuid
from textwrap import dedent
from typing import Any

from fastapi import HTTPException

from .evidence import compact_evidence
from .execution.provider import OpenAICompatibleProvider
from .mode_contracts import MODE_CONTRACTS
from .schemas import (
    NodePosition,
    PlanCompileRequest,
    PlanCompileResponse,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
async def compile_workflow_from_plan(
    provider: OpenAICompatibleProvider,
    request: PlanCompileRequest,
) -> PlanCompileResponse:
    text = request.plan_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="plan_text is required")

    extracted = await _extract_plan_structured(provider=provider, req=request)
    simulation_blueprint = _build_simulation_blueprint(extracted=extracted, req=request)
    workflow = _build_workflow_from_extracted(extracted=extracted, req=request, simulation_blueprint=simulation_blueprint)
    if request.evidence_pack:
        evidence = compact_evidence(request.evidence_pack)
        workflow.environment.simulation["evidence"] = evidence
        workflow.environment.simulation["evidence_status"] = "user_confirmed"
    rationale = _build_compile_rationale(extracted=extracted, req=request)
    return PlanCompileResponse(
        workflow=workflow,
        extracted=extracted,
        rationale=rationale,
        simulation_blueprint=simulation_blueprint,
    )


async def _extract_plan_structured(provider: OpenAICompatibleProvider, req: PlanCompileRequest) -> dict:
    sys_prompt = (
        "You are a process architect for a distributed multi-agent simulation system. "
        "Turn a loose user plan into a complete but lightweight task contract and a set of autonomous participant identities. "
        "Be exhaustive about research logic, actor/entity coverage, uncertainty, decision criteria, "
        "quality controls, and final outputs. Do not assume the user has already provided every detail; "
        "infer sensible defaults and mark uncertain items as assumptions."
    )
    evidence = compact_evidence(req.evidence_pack) if req.evidence_pack else {}
    user_prompt = dedent(
        f"""
        Parse the plan into strict JSON only.
        Mode hint: {req.mode}
        Max agents: {max(2, min(req.max_agents, 12))}
        Language: {req.language}

        Director decomposition rules:
        - Identify the real objective behind the user's words, not just the surface task.
        - First define one shared task contract: what is being done, what the final deliverable is, how it should be expressed, shared background and constraints, and when it is complete.
        - Then define each participant's identity contract: who it is, its own goal, what it knows and does not know, what it can do, and its boundaries.
        - Choose mode/submode based on intent: roleplay for story/character evolution, research.simulation for "what would happen", research.research for simulated study/fieldwork, research.consulting for issue-tree/workstream reasoning, custom only when no standard mode fits.
        - Decide whether the simulation should use individuals, cohorts, organizations, environment, events, or artifacts.
        - For research/consulting, define hypotheses, decision criteria, sample or actor logic, bias risks, validation gaps, and outputs.
        - For roleplay/story simulation, define characters, tensions, scene phases, turning points, style boundaries, and ending conditions.
        - In roleplay or world-evolution simulation, roles must be entities inside the simulated world; use writers, editors, analysts, or other production roles only when the user explicitly asks for collaborative creation.
        - Define participants by their in-world or professional identity, not by literary functions such as emotional core, plot device, comic relief, or twist generator.
        - A requested writing style controls how participant output is represented. It never grants a participant authority to write other participants' actions or invent global facts.
        - Keep the design runnable locally: prefer a small number of high-leverage agents unless the user asks for scale.
        - The plan may be a short sentence; still return a complete structure with assumptions.

        Required JSON schema:
        {{
          "intent_mode": "roleplay|research|custom",
          "intent_submode": "simulation|research|consulting|none",
          "goal": "string",
          "domain": "string",
          "audience": "string",
          "deliverable": "string",
          "task_contract": {{
            "objective": "string",
            "process_type": "string",
            "deliverable": {{
              "type": "string",
              "format": "string",
              "language": "string",
              "style": "string",
              "expression_rules": ["string"],
              "required_parts": ["string"]
            }},
            "shared_background": "string",
            "shared_constraints": ["string"],
            "completion_condition": "string"
          }},
          "constraints": ["string"],
          "key_questions": ["string"],
          "assumptions": ["string"],
          "hypotheses": ["string"],
          "success_metrics": ["string"],
          "decision_criteria": ["string"],
          "method_modules": ["market_scan|user_segmentation|competitor_analysis|scenario_testing|risk_review|strategy_synthesis"],
          "semantics": {{
            "mode": "individual|cohort|mixed|workstream",
            "confidence": 0.0,
            "reason": "string"
          }},
          "roles": [
            {{
              "name":"string",
              "count":1,
              "focus":"string",
              "entity_type":"individual|group|organization|environment|event|artifact",
              "responsibilities":["string"],
              "identity_contract": {{
                "identity":"string",
                "objective":"string",
                "knowledge":["string"],
                "unknowns":["string"],
                "capabilities":["string"],
                "boundaries":["string"]
              }}
            }}
          ],
          "research_design": {{
            "methodology": "string",
            "sample_logic": "string",
            "segments": ["string"],
            "variables": ["string"],
            "bias_risks": ["string"],
            "validation_plan": ["string"]
          }},
          "simulation_design": {{
            "initial_state": "string",
            "entities": ["string"],
            "interaction_rules": ["string"],
            "phase_logic": ["string"],
            "stop_conditions": ["string"],
            "uncertainty_factors": ["string"]
          }},
          "output_plan": {{
            "full_log": true,
            "briefing_sections": ["string"],
            "narrative_options": ["string"]
          }},
          "output_contract": {{
            "format": "dialogue|story|report|simulation|custom",
            "max_items": 0,
            "unit": "utterance|line|section|none",
            "final_only": true
          }},
          "quality_controls": ["string"],
          "environment": {{
            "profile":"string",
            "scenario":"string",
            "facts":["string"],
            "constraints":["string"],
            "glossary": {{}},
            "context_book": {{
              "entries": [
                {{
                  "title": "string",
                  "content": "standalone background, rule, belief, rumor, assumption, or knowledge",
                  "kind": "background|entity|rule|fact|knowledge|method|belief|rumor|assumption|instruction",
                  "visible_to": ["all or exact role names"],
                  "always": true,
                  "keywords": ["short phrase"],
                  "semantic_hint": "when this information matters",
                  "priority": 50
                }}
              ]
            }}
          }}
        }}

        Plan:
        {req.plan_text}

        User-provided evidence package (treat as supplied source material, distinguish it from assumptions):
        {json.dumps(evidence, ensure_ascii=False, default=str) if evidence else "none"}
        """
    ).strip()
    try:
        llm = await provider.chat(model="", system_prompt=sys_prompt, user_prompt=user_prompt)
        raw = str(llm.get("content", "")).strip()
        parsed = _try_extract_json(raw)
        if isinstance(parsed, dict):
            return _normalize_extracted(parsed, req)
    except Exception:  # noqa: BLE001
        pass
    return _heuristic_extract(req.plan_text, req)


def _try_extract_json(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _normalize_extracted(data: dict, req: PlanCompileRequest) -> dict:
    roles_raw = data.get("roles", [])
    roles: list[dict] = []
    if isinstance(roles_raw, list):
        for item in roles_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "analyst")).strip() or "analyst"
            count = int(item.get("count", 1) or 1)
            focus = str(item.get("focus", "")).strip() or f"{name} analysis"
            entity_type = str(item.get("entity_type", "individual")).strip() or "individual"
            if entity_type not in {"individual", "group", "organization", "environment", "event", "artifact"}:
                entity_type = "individual"
            responsibilities = _as_str_list(item.get("responsibilities", []))
            identity_raw = item.get("identity_contract", {})
            if not isinstance(identity_raw, dict):
                identity_raw = {}
            identity_contract = {
                "identity": str(identity_raw.get("identity", "")).strip() or name,
                "objective": str(identity_raw.get("objective", "")).strip() or focus,
                "knowledge": _as_str_list(identity_raw.get("knowledge", [])),
                "unknowns": _as_str_list(identity_raw.get("unknowns", [])),
                "capabilities": _as_str_list(identity_raw.get("capabilities", [])) or responsibilities,
                "boundaries": _as_str_list(identity_raw.get("boundaries", []))
                or ["Control only this entity's own actions and outputs."],
            }
            roles.append(
                {
                    "name": name,
                    "count": max(1, min(count, 4)),
                    "focus": focus,
                    "entity_type": entity_type,
                    "responsibilities": responsibilities,
                    "identity_contract": identity_contract,
                }
            )
    if not roles:
        roles = [{
            "name": "analyst",
            "count": min(3, max(1, req.max_agents - 1)),
            "focus": "core analysis",
            "entity_type": "individual",
            "responsibilities": ["analysis"],
            "identity_contract": {
                "identity": "analyst",
                "objective": "Perform the assigned analysis.",
                "knowledge": [],
                "unknowns": [],
                "capabilities": ["analysis"],
                "boundaries": ["Control only this entity's own actions and outputs."],
            },
        }]

    env = data.get("environment", {})
    if not isinstance(env, dict):
        env = {}
    intent_mode = _normalize_intent_mode(data.get("intent_mode"), req=req, text=req.plan_text)
    simulation_design = _normalize_simulation_design(data.get("simulation_design", {}))
    semantics = _normalize_semantics(data.get("semantics", {}), req=req, roles=roles, text=req.plan_text)
    goal = str(data.get("goal", "")).strip() or req.plan_text[:120]
    deliverable = str(data.get("deliverable", "")).strip() or "structured report"
    task_raw = data.get("task_contract", {})
    if not isinstance(task_raw, dict):
        task_raw = {}
    deliverable_raw = task_raw.get("deliverable", {})
    if not isinstance(deliverable_raw, dict):
        deliverable_raw = {}
    task_contract = {
        "objective": str(task_raw.get("objective", "")).strip() or goal,
        "process_type": str(task_raw.get("process_type", "")).strip() or str(data.get("domain", "general")),
        "deliverable": {
            "type": str(deliverable_raw.get("type", "")).strip() or deliverable,
            "format": str(deliverable_raw.get("format", "")).strip() or deliverable,
            "language": str(deliverable_raw.get("language", "")).strip() or req.language,
            "style": str(deliverable_raw.get("style", "")).strip(),
            "expression_rules": _as_str_list(deliverable_raw.get("expression_rules", [])),
            "required_parts": _as_str_list(deliverable_raw.get("required_parts", [])),
        },
        "shared_background": str(task_raw.get("shared_background", "")).strip() or req.plan_text[:500],
        "shared_constraints": _as_str_list(task_raw.get("shared_constraints", [])) or _as_str_list(data.get("constraints", [])),
        "completion_condition": str(task_raw.get("completion_condition", "")).strip()
        or "The requested deliverable is complete and consistent with the shared constraints.",
    }
    return {
        "intent_mode": intent_mode,
        "intent_submode": _normalize_intent_submode(data.get("intent_submode")),
        "goal": goal,
        "domain": str(data.get("domain", "")).strip() or "general",
        "audience": str(data.get("audience", "")).strip() or "",
        "deliverable": deliverable,
        "task_contract": task_contract,
        "constraints": _as_str_list(data.get("constraints", [])),
        "key_questions": _as_str_list(data.get("key_questions", [])),
        "assumptions": _as_str_list(data.get("assumptions", [])),
        "hypotheses": _as_str_list(data.get("hypotheses", [])),
        "success_metrics": _as_str_list(data.get("success_metrics", [])),
        "decision_criteria": _as_str_list(data.get("decision_criteria", [])),
        "method_modules": _as_str_list(data.get("method_modules", [])),
        "semantics": semantics,
        "roles": roles,
        "research_design": _normalize_research_design(data.get("research_design", {})),
        "simulation_design": simulation_design,
        "output_plan": _normalize_output_plan(data.get("output_plan", {})),
        "output_contract": _normalize_output_contract(data.get("output_contract", {}), req.plan_text),
        "quality_controls": _as_str_list(data.get("quality_controls", [])),
        "environment": {
            "profile": str(env.get("profile", "")).strip(),
            "scenario": str(env.get("scenario", "")).strip() or req.plan_text[:220],
            "facts": _as_str_list(env.get("facts", [])),
            "constraints": _as_str_list(env.get("constraints", [])),
            "glossary": env.get("glossary", {}) if isinstance(env.get("glossary", {}), dict) else {},
            "context_book": _normalize_context_book(env.get("context_book", {})),
        },
    }


def _heuristic_extract(text: str, req: PlanCompileRequest) -> dict:
    audience = ""
    m = re.search(r"(\d{1,2}\s*[-~到至]\s*\d{1,2}\s*岁?[^\n，。；]*)", text)
    if not m:
        m = re.search(r"(\d{1,2}\s*[-~]\s*\d{1,2}\s*(?:years?|yo|year-olds)[^,.;\n]*)", text, flags=re.IGNORECASE)
    if m:
        audience = m.group(1).strip()

    market_keywords = ["市场", "调研", "用户", "竞品", "market", "research", "segment", "consumer"]
    consulting_keywords = ["咨询", "战略", "增长", "商业模式", "降本", "strategy", "consulting", "growth", "pricing"]
    roleplay_keywords = ["剧情", "故事", "角色", "扮演", "小说", "剧本", "roleplay", "story", "novel", "screenplay"]
    domain = "market_research" if any(k.lower() in text.lower() for k in market_keywords) else "simulation"
    if any(k.lower() in text.lower() for k in consulting_keywords):
        domain = "consulting"
    if any(k.lower() in text.lower() for k in roleplay_keywords):
        domain = "story_simulation"

    if domain == "story_simulation":
        roles = [
            {"name": "director", "count": 1, "focus": "控制剧情阶段、冲突强度和一致性", "entity_type": "organization", "responsibilities": ["phase control", "quality correction"]},
            {"name": "lead_character", "count": 1, "focus": "核心角色行动、对话和心理变化", "entity_type": "individual", "responsibilities": ["act", "speak", "remember"]},
            {"name": "supporting_character", "count": min(3, max(1, req.max_agents - 2)), "focus": "关系压力、冲突和转折", "entity_type": "individual", "responsibilities": ["react", "escalate", "reveal"]},
        ]
        method_modules = ["scenario_testing", "risk_review", "strategy_synthesis"]
        deliverable = "story or script"
    elif domain == "consulting":
        roles = [
            {"name": "engagement_manager", "count": 1, "focus": "问题结构和决策标准", "entity_type": "organization", "responsibilities": ["issue tree", "decision criteria"]},
            {"name": "workstream_analyst", "count": min(4, max(1, req.max_agents - 2)), "focus": "分模块推演与选项筛选", "entity_type": "organization", "responsibilities": ["analysis", "option screening"]},
            {"name": "risk_reviewer", "count": 1, "focus": "风险、反例和验证缺口", "entity_type": "individual", "responsibilities": ["challenge", "validate"]},
        ]
        method_modules = ["market_scan", "competitor_analysis", "risk_review", "strategy_synthesis"]
        deliverable = "consulting briefing"
    else:
        roles = [
            {"name": "research_planner", "count": 1, "focus": "拆解目标、假设和研究方案", "entity_type": "organization", "responsibilities": ["research design", "sampling logic"]},
            {"name": "respondent_cohort", "count": min(4, max(1, req.max_agents - 2)), "focus": "模拟目标群体反应和分歧", "entity_type": "group", "responsibilities": ["cohort response", "segment split"]},
            {"name": "synthesis_reviewer", "count": 1, "focus": "汇总结论、置信度和限制", "entity_type": "individual", "responsibilities": ["synthesis", "limitations"]},
        ]
        method_modules = ["market_scan", "user_segmentation", "scenario_testing", "risk_review", "strategy_synthesis"] if domain == "market_research" else ["scenario_testing", "risk_review", "strategy_synthesis"]
        deliverable = "structured report"
    return _normalize_extracted({
        "intent_mode": _infer_intent_mode(text),
        "intent_submode": "none",
        "goal": text[:160],
        "domain": domain,
        "audience": audience,
        "deliverable": deliverable,
        "constraints": [],
        "key_questions": _default_key_questions(domain=domain, text=text),
        "assumptions": ["User plan is incomplete; missing items are treated as explicit assumptions."],
        "hypotheses": _default_hypotheses(domain=domain, text=text),
        "success_metrics": _default_success_metrics(domain=domain),
        "decision_criteria": _default_decision_criteria(domain=domain),
        "method_modules": method_modules,
        "semantics": _infer_semantics(text=text, req=req, roles=roles, domain=domain),
        "roles": roles,
        "research_design": _default_research_design(domain=domain, audience=audience),
        "simulation_design": _default_simulation_design(domain=domain, text=text),
        "output_plan": _default_output_plan(domain=domain),
        "output_contract": _normalize_output_contract({}, text),
        "quality_controls": _default_quality_controls(domain=domain),
        "environment": {
            "profile": "auto-generated from natural-language plan",
            "scenario": text[:220],
            "facts": [],
            "constraints": [],
            "glossary": {},
        },
    }, req)


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        lines = [v.strip() for v in value.split("\n")]
        return [v for v in lines if v]
    return []


def _normalize_context_book(value: object) -> dict:
    raw_entries = value.get("entries", []) if isinstance(value, dict) else value
    if not isinstance(raw_entries, list):
        raw_entries = []
    allowed_kinds = {"background", "entity", "rule", "fact", "knowledge", "method", "belief", "rumor", "assumption", "instruction"}
    entries: list[dict] = []
    for index, raw in enumerate(raw_entries[:120], start=1):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content", "")).strip()
        if not content:
            continue
        kind = str(raw.get("kind", "background")).strip().lower()
        if kind not in allowed_kinds:
            kind = "background"
        visible_to = _as_str_list(raw.get("visible_to", []))
        visibility_raw = raw.get("visibility", {})
        if isinstance(visibility_raw, dict):
            visible_to = visible_to or _as_str_list(visibility_raw.get("node_ids", []))
        is_global = not visible_to or any(item.casefold() in {"all", "global", "everyone", "所有角色", "全部"} for item in visible_to)
        activation_raw = raw.get("activation", {})
        if not isinstance(activation_raw, dict):
            activation_raw = {}
        entries.append(
            {
                "id": f"entry_{index}",
                "title": str(raw.get("title", "")).strip() or content[:32],
                "content": content,
                "kind": kind,
                "visibility": {"scope": "global" if is_global else "private", "node_ids": [] if is_global else visible_to},
                "activation": {
                    "always": bool(raw.get("always", activation_raw.get("always", True))),
                    "keywords": _as_str_list(raw.get("keywords", activation_raw.get("keywords", []))),
                    "semantic_hint": str(raw.get("semantic_hint", activation_raw.get("semantic_hint", ""))).strip(),
                    "related_entry_ids": _as_str_list(activation_raw.get("related_entry_ids", [])),
                },
                "priority": max(0, min(100, int(raw.get("priority", 50) or 50))),
                "source": "director",
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    budget = int(value.get("token_budget", 1600) or 1600) if isinstance(value, dict) else 1600
    return {"entries": entries, "token_budget": max(128, min(32000, budget))}


def _infer_intent_mode(text: str) -> str:
    lower = text.lower()
    research_tokens = ["调研", "问卷", "访谈", "市场", "选举", "咨询", "research", "survey", "interview", "consulting"]
    roleplay_tokens = [
        "角色", "剧情", "故事", "小说", "剧本", "对白", "对话", "台词", "吵架", "家庭", "情侣",
        "roleplay", "character", "story", "novel", "screenplay", "dialogue",
    ]
    if any(token in lower for token in research_tokens):
        return "research"
    if any(token in lower for token in roleplay_tokens):
        return "roleplay"
    return "custom"


def _normalize_intent_mode(value: object, *, req: PlanCompileRequest, text: str) -> str:
    if req.mode in {"research", "roleplay", "custom"}:
        return req.mode
    normalized = str(value or "").strip().lower()
    inferred = _infer_intent_mode(text)
    if normalized == "custom" and inferred != "custom":
        return inferred
    if normalized in {"research", "roleplay", "custom"}:
        return normalized
    return inferred


def _normalize_intent_submode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"simulation", "research", "consulting"} else "none"


def _chinese_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return digits.get(value)


def _normalize_output_contract(value: object, text: str) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    lower = text.lower()
    requested_format = str(raw.get("format", "")).strip().lower()
    if requested_format not in {"dialogue", "story", "report", "simulation", "custom"}:
        if any(token in lower for token in ["对话", "对白", "台词", "dialogue"]):
            requested_format = "dialogue"
        elif any(token in lower for token in ["小说", "故事", "剧本", "novel", "story", "screenplay"]):
            requested_format = "story"
        elif any(token in lower for token in ["报告", "汇报", "调研", "咨询", "report", "briefing"]):
            requested_format = "report"
        else:
            requested_format = "simulation"

    max_items = 0
    try:
        max_items = max(0, min(int(raw.get("max_items", 0) or 0), 500))
    except (TypeError, ValueError):
        max_items = 0
    match = re.search(r"(?:不超过|最多|限制在|控制在|within|at most|maximum of)?\s*([零一二两三四五六七八九十百\d]{1,4})\s*(句话|句|条台词|行|轮|utterances?|lines?)", text, flags=re.IGNORECASE)
    unit = str(raw.get("unit", "none")).strip().lower()
    if match:
        parsed_limit = _chinese_number(match.group(1))
        if parsed_limit is not None:
            max_items = max(1, min(parsed_limit, 500))
        token = match.group(2).lower()
        unit = "utterance" if token in {"句话", "句", "条台词"} or token.startswith("utterance") else "line"
    if unit not in {"utterance", "line", "section", "none"}:
        unit = "none"
    if max_items and unit == "none":
        unit = "utterance" if requested_format == "dialogue" else "line"
    return {
        "format": requested_format,
        "max_items": max_items,
        "unit": unit,
        "final_only": bool(raw.get("final_only", True)),
    }


def _normalize_research_design(value: object) -> dict:
    raw = value if isinstance(value, dict) else {}
    return {
        "methodology": str(raw.get("methodology", "")).strip(),
        "sample_logic": str(raw.get("sample_logic", "")).strip(),
        "segments": _as_str_list(raw.get("segments", [])),
        "variables": _as_str_list(raw.get("variables", [])),
        "bias_risks": _as_str_list(raw.get("bias_risks", [])),
        "validation_plan": _as_str_list(raw.get("validation_plan", [])),
    }


def _normalize_simulation_design(value: object) -> dict:
    raw = value if isinstance(value, dict) else {}
    return {
        "initial_state": str(raw.get("initial_state", "")).strip(),
        "entities": _as_str_list(raw.get("entities", [])),
        "interaction_rules": _as_str_list(raw.get("interaction_rules", [])),
        "phase_logic": _as_str_list(raw.get("phase_logic", [])),
        "stop_conditions": _as_str_list(raw.get("stop_conditions", [])),
        "uncertainty_factors": _as_str_list(raw.get("uncertainty_factors", [])),
    }


def _normalize_output_plan(value: object) -> dict:
    raw = value if isinstance(value, dict) else {}
    return {
        "full_log": bool(raw.get("full_log", True)),
        "briefing_sections": _as_str_list(raw.get("briefing_sections", [])),
        "narrative_options": _as_str_list(raw.get("narrative_options", [])),
    }


def _default_key_questions(*, domain: str, text: str) -> list[str]:
    if domain == "story_simulation":
        return ["What changes first?", "Which actor escalates the tension?", "What irreversible turning point occurs?"]
    if domain == "consulting":
        return ["What options should be kept or dropped?", "Which assumption drives the decision?", "What evidence would change the recommendation?"]
    if domain == "market_research":
        return ["Who is likely to accept or reject the idea?", "What drives adoption and resistance?", "Which next validation is worth paying for?"]
    return ["What is likely to happen?", "Which assumptions are fragile?", "What should be observed next?"]


def _default_hypotheses(*, domain: str, text: str) -> list[str]:
    if domain == "story_simulation":
        return ["Character goals will conflict under pressure.", "Prior context and memory will shape later choices."]
    if domain == "consulting":
        return ["A small number of decision criteria will dominate the recommendation.", "At least one attractive option will fail under risk review."]
    if domain == "market_research":
        return ["Interest will vary sharply by segment.", "Novelty may create curiosity while taste/price constraints limit adoption."]
    return ["The described setup contains enough constraints to simulate plausible evolution."]


def _default_success_metrics(*, domain: str) -> list[str]:
    if domain == "story_simulation":
        return ["scene specificity", "character consistency", "turning point traceability"]
    if domain == "consulting":
        return ["option screening clarity", "risk coverage", "actionability"]
    if domain == "market_research":
        return ["segment signal clarity", "adoption/resistance drivers", "validation usefulness"]
    return ["coherence", "traceability", "decision usefulness"]


def _default_decision_criteria(*, domain: str) -> list[str]:
    if domain == "story_simulation":
        return ["dramatic tension", "internal logic", "style fit"]
    if domain == "consulting":
        return ["impact", "feasibility", "risk", "speed"]
    if domain == "market_research":
        return ["acceptance likelihood", "price sensitivity", "channel fit", "validation cost"]
    return ["plausibility", "risk", "confidence"]


def _default_research_design(*, domain: str, audience: str) -> dict:
    if domain == "market_research":
        return {
            "methodology": "simulated mixed-method early screening",
            "sample_logic": audience or "target segment inferred from plan",
            "segments": [audience] if audience else ["primary target", "skeptical subgroup", "early adopter subgroup"],
            "variables": ["need intensity", "novelty appeal", "price sensitivity", "purchase barrier"],
            "bias_risks": ["LLM prior bias", "thin factual grounding", "over-generalized segment behavior"],
            "validation_plan": ["compare with real interviews", "test pricing message", "run small survey"],
        }
    if domain == "consulting":
        return {
            "methodology": "issue-tree workstream simulation",
            "sample_logic": "role-based expert workstreams",
            "segments": ["market", "customer", "competitor", "risk"],
            "variables": ["impact", "feasibility", "risk", "confidence"],
            "bias_risks": ["missing facts", "overconfident logic", "weak counterfactuals"],
            "validation_plan": ["fact check key assumptions", "stress-test recommendation", "define next diligence"],
        }
    return {
        "methodology": "scenario simulation",
        "sample_logic": "actors/entities inferred from plan",
        "segments": [],
        "variables": ["pressure", "alignment", "risk", "confidence"],
        "bias_risks": ["underspecified background", "model prior drift"],
        "validation_plan": ["review assumptions", "rerun with alternate seed"],
    }


def _default_simulation_design(*, domain: str, text: str) -> dict:
    if domain == "story_simulation":
        return {
            "initial_state": text[:220],
            "entities": ["lead characters", "supporting characters", "environment", "trigger events"],
            "interaction_rules": ["individuals act through concrete speech/action/memory", "environment constrains choices", "events escalate or redirect tension"],
            "phase_logic": ["setup", "conflict escalation", "turning point", "resolution"],
            "stop_conditions": ["major conflict resolves", "user checkpoint", "style or logic drift detected"],
            "uncertainty_factors": ["hidden motives", "relationship memory", "external pressure"],
        }
    return {
        "initial_state": text[:220],
        "entities": ["decision subject", "target population or actors", "environment", "evidence artifacts"],
        "interaction_rules": ["actors respond according to role/entity type", "groups produce distributional reactions", "reviewers challenge weak conclusions"],
        "phase_logic": ["scope", "explore", "synthesize"],
        "stop_conditions": ["required outputs complete", "confidence too low", "human checkpoint"],
        "uncertainty_factors": ["missing facts", "segment heterogeneity", "model uncertainty"],
    }


def _default_output_plan(*, domain: str) -> dict:
    if domain == "story_simulation":
        return {
            "full_log": True,
            "briefing_sections": ["timeline", "character state", "turning points"],
            "narrative_options": ["novel", "screenplay", "episode recap"],
        }
    return {
        "full_log": True,
        "briefing_sections": ["question", "method", "findings", "keep/drop/to-validate", "limits"],
        "narrative_options": ["plain briefing", "consulting memo", "scenario narrative"],
    }


def _default_quality_controls(*, domain: str) -> list[str]:
    base = ["separate assumptions from findings", "track confidence and limitations", "preserve full-log traceability"]
    if domain == "story_simulation":
        return base + ["avoid summary-only scenes when detailed granularity is requested", "maintain character memory and motivation"]
    if domain == "market_research":
        return base + ["avoid treating simulated respondents as real survey evidence", "surface segment disagreement"]
    if domain == "consulting":
        return base + ["force counterarguments", "map recommendations to decision criteria"]
    return base


def _normalize_semantics(value: object, *, req: PlanCompileRequest, roles: list[dict], text: str) -> dict:
    fallback = _infer_semantics(text=text, req=req, roles=roles, domain="")
    if not isinstance(value, dict):
        return fallback
    mode = str(value.get("mode", fallback["mode"])).strip().lower()
    if mode not in {"individual", "cohort", "mixed", "workstream"}:
        mode = fallback["mode"]
    try:
        confidence = float(value.get("confidence", fallback["confidence"]))
    except Exception:  # noqa: BLE001
        confidence = float(fallback["confidence"])
    reason = str(value.get("reason", fallback["reason"])).strip() or fallback["reason"]
    return {"mode": mode, "confidence": max(0.0, min(1.0, confidence)), "reason": reason}


def _infer_semantics(*, text: str, req: PlanCompileRequest, roles: list[dict], domain: str) -> dict:
    role_count = 0
    for r in roles:
        if isinstance(r, dict):
            role_count += int(r.get("count", 1) or 1)
    role_count = max(role_count, min(max(req.max_agents, 1), 12))
    lower = text.lower()
    cohort_tokens = ["市场", "调研", "消费者", "大学生", "样本", "问卷", "投票", "选举", "受访者", "market", "survey", "consumer", "sample", "voter", "population"]
    individual_tokens = ["角色", "剧情", "人物", "领导", "下属", "关系", "对话", "故事", "roleplay", "character", "story", "dialogue"]
    workstream_tokens = ["咨询", "战略", "workstream", "consulting", "issue tree", "strategy"]
    cohort_score = sum(1 for k in cohort_tokens if k in lower)
    individual_score = sum(1 for k in individual_tokens if k in lower)
    workstream_score = sum(1 for k in workstream_tokens if k in lower)
    if req.mode == "roleplay" or individual_score >= max(1, cohort_score + workstream_score):
        return {"mode": "individual", "confidence": 0.82, "reason": "roleplay or character/story signals detected"}
    if req.mode == "research" and (req.submode == "consulting" or workstream_score > 0):
        return {"mode": "workstream", "confidence": 0.78, "reason": "consulting/workstream signals detected"}
    if role_count > 12 or domain == "market_research" or cohort_score > 0:
        return {"mode": "cohort", "confidence": 0.8, "reason": "population, survey, market, or scale signals detected"}
    if role_count > 8:
        return {"mode": "mixed", "confidence": 0.62, "reason": "medium actor count suggests mixed individual/cohort semantics"}
    return {"mode": "individual", "confidence": 0.58, "reason": "small actor count defaults to individual ensemble"}


def _build_workflow_from_extracted(extracted: dict, req: PlanCompileRequest, simulation_blueprint: dict) -> WorkflowDefinition:
    workflow_id = f"wf_auto_{uuid.uuid4().hex[:8]}"
    workflow_name = f"AutoPlan - {str(extracted.get('domain', 'workflow'))}"
    nodes: list[WorkflowNode] = []
    edges: list[WorkflowEdge] = []
    mode = str(simulation_blueprint.get("mode", "custom"))
    director_guardrails = (
        "Preserve character autonomy and the user's requested tone. Control only pacing, length, detail, and drift; "
        "do not choose actions for the roles."
        if mode == "roleplay"
        else "Keep the simulation aligned, traceable, concise, and bounded by the requested horizon."
    )

    nodes.append(
        WorkflowNode(
            id="director_1",
            type="director",
            position=NodePosition(x=80, y=60),
            config={
                "model": "",
                "objective": f"Deliver plan goal: {extracted.get('goal', '')}",
                "style_guardrails": director_guardrails,
                "gpro_candidates": 1,
            },
            inputs={"situation": str(extracted.get("goal", ""))},
        )
    )

    role_nodes: list[str] = []
    roles = extracted.get("roles", [])
    y = 200
    output_contract = simulation_blueprint.get("output_contract", {})
    task_contract = simulation_blueprint.get("task_contract", {})
    agent_budget = max(1, min(req.max_agents, 12))
    if isinstance(output_contract, dict) and output_contract.get("format") == "dialogue":
        requested_items = int(output_contract.get("max_items", 0) or 0)
        if 0 < requested_items <= 20:
            agent_budget = min(agent_budget, 3)
    used_agents = 0
    idx = 1
    key_questions = _as_str_list(extracted.get("key_questions", []))
    assumptions = _as_str_list(extracted.get("assumptions", []))
    decision_criteria = _as_str_list(extracted.get("decision_criteria", []))
    quality_controls = _as_str_list(extracted.get("quality_controls", []))
    participant_instruction = _participant_output_instruction(mode=mode, output_contract=output_contract)
    if isinstance(roles, list):
        for role in roles:
            if not isinstance(role, dict):
                continue
            name = str(role.get("name", "analyst")).strip() or "analyst"
            focus = str(role.get("focus", "")).strip() or "analysis"
            entity_type = str(role.get("entity_type", "individual")).strip() or "individual"
            if entity_type not in {"individual", "group", "organization", "environment", "event", "artifact"}:
                entity_type = "individual"
            responsibilities = _as_str_list(role.get("responsibilities", []))
            identity_contract = role.get("identity_contract", {})
            if not isinstance(identity_contract, dict):
                identity_contract = {}
            behavior_prompt = _entity_behavior_prompt(entity_type)
            entity_profile = _build_entity_profile(
                name=name,
                focus=focus,
                responsibilities=responsibilities,
                extracted=extracted,
                simulation_blueprint=simulation_blueprint,
            )
            count = int(role.get("count", 1) or 1)
            for _ in range(max(1, min(count, 4))):
                if used_agents >= agent_budget:
                    break
                node_id = f"agent_{idx}"
                idx += 1
                used_agents += 1
                role_nodes.append(node_id)
                nodes.append(
                    WorkflowNode(
                        id=node_id,
                        type="agent",
                        position=NodePosition(x=260 + (idx % 3) * 220, y=y),
                        config={
                            "role": entity_type,
                            "entity_type": entity_type,
                            "entity_name": name,
                            "entity_profile": entity_profile,
                            "behavior_prompt": behavior_prompt,
                            "profile": f"{name}: {focus}",
                            "responsibilities": responsibilities,
                            "identity_contract": identity_contract,
                            "execution_mode": "builtin_llm",
                            "model_connection_mode": "platform_default",
                            "model": "",
                            "model_base_url": "",
                            "model_api_key": "",
                            "external_endpoint_url": "",
                            "external_integration_mode": "mock",
                            "external_endpoint_path": "/agent/tasks",
                            "external_api_key": "",
                            "external_timeout_ms": 60000,
                            "system_prompt": (
                                f"You are {name}, an autonomous participant rather than the author of the whole process. "
                                f"Identity contract: {json.dumps(identity_contract, ensure_ascii=False, default=str)}. "
                                f"Basic behavior rule: {behavior_prompt} "
                                "Decide whether to participate from your own knowledge and goals. Control only yourself, "
                                "never decide another participant's response, and treat unobserved global facts as unknown."
                            ),
                        },
                        inputs={
                            "prompt": (
                                f"Shared task contract: {json.dumps(task_contract, ensure_ascii=False, default=str)}\n"
                                f"Your identity contract: {json.dumps(identity_contract, ensure_ascii=False, default=str)}\n"
                                f"Key questions: {'; '.join(key_questions[:6])}\n"
                                f"Assumptions: {'; '.join(assumptions[:6])}\n"
                                f"Decision criteria: {'; '.join(decision_criteria[:6])}\n"
                                f"Quality controls: {'; '.join(quality_controls[:6])}\n"
                                f"{participant_instruction}"
                            )
                        },
                    )
                )
                edges.append(
                    WorkflowEdge(
                        id=f"e_director_{node_id}",
                        source="director_1",
                        target=node_id,
                    )
                )
                y += 70
            if used_agents >= agent_budget:
                break

    if not role_nodes:
        role_nodes = ["agent_1"]
        nodes.append(
            WorkflowNode(
                id="agent_1",
                type="agent",
                position=NodePosition(x=300, y=200),
                config={
                    "role": "individual",
                    "entity_type": "individual",
                    "entity_name": "analyst",
                    "entity_profile": "Fallback analyst for the requested simulation.",
                    "behavior_prompt": _entity_behavior_prompt("individual"),
                    "profile": "analyst: core analysis",
                    "responsibilities": ["analysis"],
                    "identity_contract": {
                        "identity": "analyst",
                        "objective": "Perform the assigned analysis.",
                        "knowledge": [],
                        "unknowns": [],
                        "capabilities": ["analysis"],
                        "boundaries": ["Control only this entity's own actions and outputs."],
                    },
                    "execution_mode": "builtin_llm",
                    "model_connection_mode": "platform_default",
                    "model": "",
                    "model_base_url": "",
                    "model_api_key": "",
                    "external_endpoint_url": "",
                    "external_integration_mode": "mock",
                    "external_endpoint_path": "/agent/tasks",
                    "external_api_key": "",
                    "external_timeout_ms": 60000,
                    "system_prompt": (
                        "You are an autonomous analyst participant. Decide whether to act from your own identity and memory; "
                        "control only your own output and do not invent shared facts."
                    ),
                },
                inputs={"prompt": f"Analyze and execute: {extracted.get('goal', '')}"},
            )
        )
        edges.append(WorkflowEdge(id="e_director_agent_1", source="director_1", target="agent_1"))

    summary_node_id = "summary_1"
    nodes.append(
        WorkflowNode(
            id=summary_node_id,
            type="agent",
            position=NodePosition(x=720, y=360),
            config={
                "role": "reviewer",
                "model": "",
                "system_prompt": (
                    "You are the synthesis lead. Use the simulation blueprint, decision criteria, "
                    "quality controls, and upstream outputs to produce the requested final deliverable. "
                    "Treat the output contract as mandatory."
                ),
                "output_contract": output_contract,
            },
            inputs={
                "prompt": (
                    f"Synthesize findings for goal: {extracted.get('goal', '')}\n"
                    f"Target audience: {extracted.get('audience', '')}\n"
                    f"Required outputs: {'; '.join(_as_str_list(simulation_blueprint.get('required_outputs', [])))}\n"
                    f"Decision criteria: {'; '.join(_as_str_list(simulation_blueprint.get('decision_criteria', [])))}\n"
                    f"Output plan: {json.dumps(simulation_blueprint.get('output_plan', {}), ensure_ascii=False)}\n"
                    f"Output contract: {json.dumps(output_contract, ensure_ascii=False)}\n"
                    f"{_synthesis_output_instruction(mode=mode, output_contract=output_contract)}"
                )
            },
        )
    )
    for rn in role_nodes:
        edges.append(WorkflowEdge(id=f"e_{rn}_{summary_node_id}", source=rn, target=summary_node_id))

    report_node_id = "report_1"
    nodes.append(
        WorkflowNode(
            id=report_node_id,
            type="prompt",
            position=NodePosition(x=980, y=360),
            config={"template": "Final summary output:\n{{summary_1.content}}"},
            inputs={},
        )
    )
    edges.append(WorkflowEdge(id=f"e_{summary_node_id}_{report_node_id}", source=summary_node_id, target=report_node_id))

    env = extracted.get("environment", {})
    if not isinstance(env, dict):
        env = {}
    context_book = _resolve_context_book_visibility(env.get("context_book", {}), nodes)
    return WorkflowDefinition(
        id=workflow_id,
        name=workflow_name,
        version=1,
        nodes=nodes,
        edges=edges,
        entry_nodes=["director_1"],
        environment={
            "profile": str(env.get("profile", "")),
            "scenario": str(env.get("scenario", extracted.get("goal", ""))),
            "time_context": _environment_time_context(env),
            "spatial_context": _environment_spatial_context(env),
            "facts": _as_str_list(env.get("facts", [])),
            "constraints": _as_str_list(env.get("constraints", [])),
            "glossary": env.get("glossary", {}) if isinstance(env.get("glossary", {}), dict) else {},
            "context_book": context_book,
            "simulation": simulation_blueprint,
        },
    )


def _resolve_context_book_visibility(value: object, nodes: list[WorkflowNode]) -> dict:
    book = _normalize_context_book(value)
    name_to_ids: dict[str, list[str]] = {}
    node_ids = {node.id for node in nodes}
    for node in nodes:
        name = str(node.config.get("entity_name", "")).strip().casefold()
        if name:
            name_to_ids.setdefault(name, []).append(node.id)
    for entry in book["entries"]:
        visibility = entry.get("visibility", {})
        if not isinstance(visibility, dict) or visibility.get("scope") != "private":
            continue
        resolved: list[str] = []
        for target in visibility.get("node_ids", []):
            raw = str(target).strip()
            if raw in node_ids:
                resolved.append(raw)
            resolved.extend(name_to_ids.get(raw.casefold(), []))
        visibility["node_ids"] = list(dict.fromkeys(resolved))
    return book


def _entity_behavior_prompt(entity_type: str) -> str:
    prompts = {
        "individual": (
            "Simulate one concrete individual. Speak, act, remember, hesitate, misjudge, hide information, "
            "change stance, and make subjective choices based on identity, goals, state, and context."
        ),
        "group": (
            "Simulate a collective, not one person. Produce majority/minority views, rumors, social pressure, "
            "typical quotes, subgroup splits, and aggregate behavior tendencies."
        ),
        "organization": (
            "Simulate an institution with goals, resources, hierarchy, rules, internal disagreement, "
            "task assignment, resource allocation, and strategic decisions."
        ),
        "environment": (
            "Simulate the surrounding world. Maintain time, place, constraints, resources, social rules, risks, "
            "and changing conditions that shape what other entities can do."
        ),
        "event": (
            "Simulate a bounded world event. Explain what happens, who is affected, intensity, duration, causes, "
            "and what new constraints or opportunities it creates."
        ),
        "artifact": (
            "Simulate an information or object carrier. Maintain content, owner, credibility, visibility, state, "
            "and effects when read, spread, modified, contested, or used as evidence."
        ),
    }
    return prompts.get(entity_type, prompts["individual"])


def _participant_output_instruction(*, mode: str, output_contract: object) -> str:
    contract = output_contract if isinstance(output_contract, dict) else {}
    if mode == "roleplay" or contract.get("format") in {"dialogue", "story"}:
        return (
            "Contribute only this entity's next concrete action, reaction, or one to two in-character utterances. "
            "Do not write the whole scene, do not summarize other characters, and do not add analysis or production notes."
        )
    return "Produce concrete observations, uncertainty, and next implications from this entity's perspective."


def _synthesis_output_instruction(*, mode: str, output_contract: object) -> str:
    contract = output_contract if isinstance(output_contract, dict) else {}
    max_items = int(contract.get("max_items", 0) or 0)
    unit = str(contract.get("unit", "none"))
    if mode == "roleplay" or contract.get("format") in {"dialogue", "story"}:
        limit = f" Use no more than {max_items} {unit}s." if max_items else ""
        return (
            "Return the final scene/deliverable only, with no headings, rationale, character analysis, notes, or alternatives."
            f"{limit} Preserve concrete dialogue/action and the requested tone."
        )
    limit = f" Keep the final output within {max_items} {unit}s." if max_items else ""
    return f"Return the requested final deliverable, then concise limitations and next actions.{limit}"


def _build_entity_profile(
    *,
    name: str,
    focus: str,
    responsibilities: list[str],
    extracted: dict,
    simulation_blueprint: dict,
) -> str:
    parts = [
        f"Name/role: {name}",
        f"Focus: {focus}",
        f"Goal context: {extracted.get('goal', '')}",
    ]
    audience = str(extracted.get("audience", "")).strip()
    if audience:
        parts.append(f"Audience/subject: {audience}")
    if responsibilities:
        parts.append(f"Responsibilities: {', '.join(responsibilities)}")
    decision_criteria = _as_str_list(simulation_blueprint.get("decision_criteria", []))
    if decision_criteria:
        parts.append(f"Decision criteria: {'; '.join(decision_criteria[:4])}")
    return "\n".join(parts)


def _build_simulation_blueprint(extracted: dict, req: PlanCompileRequest) -> dict:
    roles = extracted.get("roles", [])
    role_names: list[str] = []
    if isinstance(roles, list):
        for r in roles:
            if not isinstance(r, dict):
                continue
            role_name = str(r.get("name", "")).strip()
            count = int(r.get("count", 1) or 1)
            if role_name:
                role_names.extend([role_name] * max(1, min(count, 4)))
    if not role_names:
        role_names = ["planner", "analyst", "reviewer"]

    question = str(extracted.get("goal", "")).strip() or "What is likely to happen under current setup?"
    domain = str(extracted.get("domain", "simulation")).strip() or "simulation"
    mode = _resolve_mode(req.mode, domain, str(extracted.get("intent_mode", "")), req.plan_text)
    submode = _resolve_submode(mode=mode, requested=req.submode, text=req.plan_text, domain=domain)
    method_modules = extracted.get("method_modules", [])
    if not isinstance(method_modules, list) or not method_modules:
        method_modules = ["strategy_synthesis"]
    semantics = extracted.get("semantics", {})
    if not isinstance(semantics, dict):
        semantics = _infer_semantics(text=req.plan_text, req=req, roles=roles if isinstance(roles, list) else [], domain=domain)
    research_design = _normalize_research_design(extracted.get("research_design", {}))
    simulation_design = _normalize_simulation_design(extracted.get("simulation_design", {}))
    output_plan = _normalize_output_plan(extracted.get("output_plan", {}))
    output_contract = _normalize_output_contract(extracted.get("output_contract", {}), req.plan_text)
    env = extracted.get("environment", {})
    if not isinstance(env, dict):
        env = {}
    time_context = _environment_time_context(env)
    spatial_context = _environment_spatial_context(env)
    assumptions = _as_str_list(extracted.get("assumptions", []))
    hypotheses = _as_str_list(extracted.get("hypotheses", []))
    success_metrics = _as_str_list(extracted.get("success_metrics", []))
    decision_criteria = _as_str_list(extracted.get("decision_criteria", []))
    quality_controls = _as_str_list(extracted.get("quality_controls", []))

    phases: list[dict[str, str]] = [
        {
            "id": "phase_scope",
            "name": "Scope Definition",
            "objective": "Align decision question, constraints, and assumptions.",
            "exit_criteria": "Key assumptions and scope confirmed.",
        },
        {
            "id": "phase_explore",
            "name": "Evidence Exploration",
            "objective": "Collect and compare evidence from role-specific perspectives.",
            "exit_criteria": "Major hypotheses have supporting or conflicting signals.",
        },
        {
            "id": "phase_synthesize",
            "name": "Synthesis",
            "objective": "Converge findings into scenarios and recommended actions.",
            "exit_criteria": "Clear options, risks, and next actions are produced.",
        },
    ]
    if mode == "roleplay":
        phases = [
            {
                "id": "phase_setup",
                "name": "Story Setup",
                "objective": "Establish world state, motivations, and tensions.",
                "exit_criteria": "All major actors have clear intent.",
            },
            {
                "id": "phase_conflict",
                "name": "Conflict Evolution",
                "objective": "Simulate interactions, pressure, and turning points.",
                "exit_criteria": "At least one irreversible turning point occurs.",
            },
            {
                "id": "phase_resolution",
                "name": "Resolution",
                "objective": "Conclude outcomes and unresolved tensions.",
                "exit_criteria": "Outcome is coherent and traceable to prior events.",
            },
        ]
    if mode == "research" and submode == "research":
        phases = [
            {
                "id": "phase_design",
                "name": "Research Design",
                "objective": "Define methods, sampling logic, and measurement scope.",
                "exit_criteria": "Method and quality controls are explicit.",
            },
            {
                "id": "phase_fieldwork",
                "name": "Fieldwork Simulation",
                "objective": "Simulate data collection process and respondent/subject behavior.",
                "exit_criteria": "Sufficient evidence and anomalies are captured.",
            },
            {
                "id": "phase_analysis",
                "name": "Analysis and Validation",
                "objective": "Interpret evidence with bias and limitation controls.",
                "exit_criteria": "Findings, confidence, and validation plan are explicit.",
            },
        ]
    if mode == "research" and submode == "consulting":
        phases = [
            {
                "id": "phase_problem",
                "name": "Problem Structuring",
                "objective": "Build issue tree and define decision criteria.",
                "exit_criteria": "Decision criteria and hypotheses are aligned.",
            },
            {
                "id": "phase_workstream",
                "name": "Workstream Analysis",
                "objective": "Run role-based workstreams across market/user/finance/risk.",
                "exit_criteria": "Each workstream returns defensible conclusions.",
            },
            {
                "id": "phase_recommend",
                "name": "Recommendation",
                "objective": "Screen options and produce action-ready recommendations.",
                "exit_criteria": "Keep/drop/to-validate list is complete.",
            },
        ]

    contract = MODE_CONTRACTS.get(mode, MODE_CONTRACTS["custom"])
    submode_contract: dict[str, Any] = {}
    if mode == "research":
        submode_contract = contract.submodes.get(submode, {})

    return {
        "version": 1,
        "seed": f"seed_{uuid.uuid4().hex[:10]}",
        "mode": mode,
        "ui_mode": mode,
        "submode": submode,
        "contract": contract.model_dump(),
        "submode_contract": submode_contract,
        "semantics": semantics,
        "task_contract": extracted.get("task_contract", {}) if isinstance(extracted.get("task_contract", {}), dict) else {},
        "research_question": question,
        "domain": domain,
        "actors": role_names[: max(2, min(req.max_agents + 1, 16))],
        "assumptions": assumptions,
        "hypotheses": hypotheses,
        "success_metrics": success_metrics,
        "decision_criteria": decision_criteria,
        "research_design": research_design,
        "simulation_design": simulation_design,
        "output_plan": output_plan,
        "output_contract": output_contract,
        "quality_controls": quality_controls,
        "method_modules": [str(m) for m in method_modules[:8]],
        "horizon_rounds": max(3, min(req.max_agents + 2, 12)),
        "execution_model": "continuous" if mode == "roleplay" or (mode == "research" and submode == "simulation") else "dag",
        "director_interval_rounds": 1 if mode == "roleplay" else 2,
        "detail_granularity": "detailed" if mode == "roleplay" else "concise",
        "phases": phases,
        "required_outputs": submode_contract.get("required_outputs", contract.required_outputs),
        "variables": {
            "progress": 0.0,
            "confidence": 0.35 if mode == "research" else 0.5,
            "risk": 0.25 if mode == "research" else 0.4,
            "alignment": 0.5,
        },
        "world_state": {
            "time_context": time_context,
            "spatial_context": spatial_context,
            "current_time": time_context,
            "current_location": spatial_context,
            "temporal_scope": time_context,
            "spatial_scope": spatial_context,
            "conditions": _as_str_list(env.get("conditions", [])),
            "active_events": [],
        },
        "intervention_policy": "director_on_deviation",
    }


def _environment_time_context(env: dict[str, Any]) -> str:
    return str(env.get("time_context") or env.get("time") or env.get("period") or env.get("timeline") or "").strip()


def _environment_spatial_context(env: dict[str, Any]) -> str:
    return str(env.get("spatial_context") or env.get("space") or env.get("location") or env.get("place") or "").strip()


def _build_compile_rationale(extracted: dict, req: PlanCompileRequest) -> str:
    roles = extracted.get("roles", [])
    role_summary = ", ".join([f"{r.get('name', 'role')}x{r.get('count', 1)}" for r in roles if isinstance(r, dict)])
    return (
        f"Auto-compiled by director parser. mode={req.mode}, submode={req.submode or 'auto'}, "
        f"goal='{extracted.get('goal', '')[:80]}', roles={role_summary or 'default analyst'}, "
        f"max_agents={req.max_agents}."
    )


def _resolve_mode(requested_mode: str, domain: str, intent_mode: str = "", text: str = "") -> str:
    if requested_mode and requested_mode != "auto":
        if requested_mode == "collab":
            return "research"
        return requested_mode
    if intent_mode in {"research", "roleplay", "custom"}:
        return intent_mode
    if domain in {"market_research", "consulting"}:
        return "research"
    if domain == "story_simulation":
        return "roleplay"
    return _infer_intent_mode(text)


def _resolve_submode(mode: str, requested: str | None, text: str, domain: str) -> str:
    if mode != "research":
        return "none"
    normalized = (requested or "").strip().lower()
    if normalized in {"simulation", "research", "consulting"}:
        return normalized
    lower = text.lower()
    if any(k in lower for k in ["咨询", "consult", "issue tree", "workstream", "strategy"]):
        return "consulting"
    if any(k in lower for k in ["问卷", "访谈", "抽样", "调研方法", "survey", "interview", "sample"]):
        return "research"
    if any(k in lower for k in ["演化", "simulate", "evolution", "推演"]):
        return "simulation"
    if domain == "consulting":
        return "consulting"
    if domain == "market_research":
        return "research"
    return "simulation"
