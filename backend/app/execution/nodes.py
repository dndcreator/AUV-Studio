from __future__ import annotations

import ast
import re
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx

from ..schemas import ExternalAgentContext, ExternalAgentTaskRequest, ExternalAgentTaskResponse
from ..schemas import WorkflowNode
from .entity_contracts import entity_action_contract
from .provider import OpenAICompatibleProvider, build_provider_with_override
from .tools import execute_tool

TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")


class HumanInterventionRequired(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("question", "human intervention required")))
        self.payload = payload


def _get_path(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def resolve_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        m = TOKEN_RE.fullmatch(value.strip())
        if m:
            return _get_path(context, m.group(1))

        def _replace(match: re.Match[str]) -> str:
            path = match.group(1)
            resolved = _get_path(context, path)
            return "" if resolved is None else str(resolved)

        return TOKEN_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: resolve_value(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v, context) for v in value]
    return value


def resolve_inputs(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    return {k: resolve_value(v, context) for k, v in node.inputs.items()}


def _safe_eval_boolean(expr: str, context: dict[str, Any]) -> bool:
    safe_names = {
        "len": len,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
    }
    flat_context = {"ctx": context}
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Call)):
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in safe_names:
                    raise ValueError("unsafe function call in condition expression")
            else:
                raise ValueError("unsafe syntax in condition expression")
        elif isinstance(
            node,
            (
                ast.And,
                ast.Or,
                ast.Not,
                ast.Eq,
                ast.NotEq,
                ast.Gt,
                ast.GtE,
                ast.Lt,
                ast.LtE,
                ast.In,
                ast.NotIn,
                ast.Is,
                ast.IsNot,
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.Div,
                ast.Mod,
                ast.Pow,
                ast.USub,
                ast.UAdd,
            ),
        ):
            continue
        elif isinstance(node, (ast.Attribute, ast.Subscript, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.Name,
                               ast.Load, ast.Constant, ast.Expression, ast.List, ast.Tuple, ast.Dict)):
            continue
        else:
            raise ValueError("unsupported syntax in condition expression")
    return bool(eval(compile(tree, "<condition>", "eval"), {"__builtins__": {}}, {**safe_names, **flat_context}))


async def execute_node(
    node: WorkflowNode,
    node_input: dict[str, Any],
    context: dict[str, Any],
    provider: OpenAICompatibleProvider,
) -> dict[str, Any]:
    if node.type == "director":
        mode = str(node.config.get("model_connection_mode", "platform_default"))
        model = str(node.config.get("model", "")) if mode == "custom_endpoint" else ""
        objective = resolve_value(str(node.config.get("objective", "")), {**context, **node_input})
        guardrails = resolve_value(str(node.config.get("style_guardrails", "")), {**context, **node_input})
        situation = resolve_value(str(node_input.get("situation", "")), {**context, **node_input})
        env_block = _render_environment(node_input.get("_environment", context.get("_environment", {})))
        memory_block = _render_memory(node_input.get("_memory", context.get("_memory", [])), limit=8)
        simulation_block = _render_simulation(node_input.get("_simulation", context.get("_simulation", {})))
        context_book_block = _render_context_pack(node_input.get("_context_pack", {}))
        candidates = int(node.config.get("gpro_candidates", 3) or 3)
        result = await _run_gpro_director(
            provider=provider,
            model=model,
            objective=str(objective),
            guardrails=str(guardrails),
            situation=(
                f"{situation}\n\nEnvironment:\n{env_block}\n\nActive Background:\n{context_book_block}"
                f"\n\nSimulation State:\n{simulation_block}\n\nMemory:\n{memory_block}"
                if env_block or context_book_block or memory_block or simulation_block
                else str(situation)
            ),
            context=context,
            candidates=max(1, min(candidates, 8)),
        )
        return result

    if node.type == "prompt":
        template = str(node.config.get("template", ""))
        rendered = resolve_value(template, {**context, **node_input})
        return {"text": rendered}

    if node.type == "agent":
        execution_mode = str(node.config.get("execution_mode", "builtin_llm")).strip().lower()
        if execution_mode == "external_agent":
            entity_prompt = str(node_input.get("prompt", node.config.get("user_prompt", "")))
            entity_prompt = _append_entity_context(
                prompt=entity_prompt,
                node=node,
                simulation=node_input.get("_simulation", context.get("_simulation", {})),
                heading="",
                include_identity=False,
                profile_label="Entity profile",
            )
            external_config = {
                **node.config,
                "integration_mode": str(node.config.get("external_integration_mode", "http")).strip() or "http",
                "agent_id": str(node.config.get("entity_name", node.config.get("role", "user_agent"))),
                "endpoint_url": str(node.config.get("external_endpoint_url", "")),
                "endpoint_path": str(node.config.get("external_endpoint_path", "/agent/tasks")),
                "api_key": str(node.config.get("external_api_key", "")),
                "timeout_ms": int(node.config.get("external_timeout_ms", 60000) or 60000),
                "prompt_template": "{{prompt}}",
            }
            external_input = {
                **node_input,
                "prompt": entity_prompt,
            }
            external_node = node.model_copy(update={"type": "external_agent", "config": external_config})
            return await _execute_external_agent(node=external_node, node_input=external_input, context=context)
        role = str(node.config.get("role", "custom"))
        mode = str(node.config.get("model_connection_mode", "platform_default"))
        model = str(node.config.get("model", "")) if mode == "custom_endpoint" else ""
        base_url = str(node.config.get("model_base_url", "")).strip() or None
        api_key = str(node.config.get("model_api_key", "")).strip() or None
        system_prompt = resolve_value(str(node.config.get("system_prompt", "")), {**context, **node_input})
        user_prompt = resolve_value(str(node_input.get("prompt", node.config.get("user_prompt", ""))), {**context, **node_input})
        user_prompt = _append_entity_context(
            prompt=user_prompt,
            node=node,
            simulation=node_input.get("_simulation", context.get("_simulation", {})),
            heading="Entity definition",
            skip_behavior_if_in=system_prompt,
        )
        interaction_block = _render_interactions(node_input.get("_interactions", []))
        if interaction_block:
            user_prompt = f"{user_prompt}\n\nIncoming interactions:\n{interaction_block}"
        env_block = _render_environment(node_input.get("_environment", context.get("_environment", {})))
        memory_block = _render_memory(node_input.get("_memory", context.get("_memory", [])), limit=8)
        simulation_block = _render_simulation(node_input.get("_simulation", context.get("_simulation", {})))
        context_book_block = _render_context_pack(node_input.get("_context_pack", {}))
        if env_block:
            user_prompt = f"{user_prompt}\n\nEnvironment:\n{env_block}"
        if simulation_block:
            user_prompt = f"{user_prompt}\n\nSimulation State:\n{simulation_block}"
        if context_book_block:
            user_prompt = f"{user_prompt}\n\nActive Background:\n{context_book_block}"
        if memory_block:
            user_prompt = f"{user_prompt}\n\nMemory:\n{memory_block}"
        global_guidance = str(node_input.get("_global_guidance", context.get("_global_guidance", ""))).strip()
        if global_guidance:
            user_prompt = f"{user_prompt}\n\nDirector guidance:\n{global_guidance}"
        effective_provider = provider
        if mode == "custom_endpoint":
            if not base_url:
                raise ValueError("model_base_url is required when model_connection_mode=custom_endpoint")
            effective_provider = build_provider_with_override(
                base_url=base_url,
                api_key=api_key,
                provider_kind=str(node.config.get("model_provider", "openai_compatible")),
            )
        response = await effective_provider.chat(model=model, system_prompt=f"[{role}] {system_prompt}", user_prompt=user_prompt)
        content = _apply_output_contract(str(response["content"]), node.config.get("output_contract"))
        return {"role": role, "content": content, "raw": response.get("raw", {})}

    if node.type == "tool":
        name = str(node.config.get("tool_name", "echo"))
        args = node_input if node_input else resolve_value(node.config.get("args", {}), context)
        result = execute_tool(name=name, args=args if isinstance(args, dict) else {"value": args})
        return {"tool": name, "result": result}

    if node.type == "external_agent":
        return await _execute_external_agent(node=node, node_input=node_input, context=context)

    if node.type == "condition":
        expr = str(node.config.get("expression", "False"))
        verdict = _safe_eval_boolean(expr, {**context, **node_input})
        return {"result": verdict}

    if node.type == "human_checkpoint":
        question_override = str(resolve_value(node_input.get("question", ""), {**context, **node_input})).strip()
        template = str(resolve_value(node.config.get("question_template", ""), {**context, **node_input})).strip()
        question = question_override or template or "Please provide human intervention for this checkpoint."
        owner = str(node.config.get("owner", "director")).strip().lower()
        required = str(node.config.get("required", "true")).strip().lower() in {"true", "1", "yes", "y"}
        context_hint = str(resolve_value(node_input.get("context_hint", ""), {**context, **node_input})).strip()
        response_obj = node_input.get("_human_response")

        if isinstance(response_obj, dict) and str(response_obj.get("node_id", "")) == node.id:
            response = str(response_obj.get("response", "")).strip()
            if required and not response:
                raise ValueError("human response is required but empty")
            return {
                "owner": owner,
                "question": question,
                "context_hint": context_hint,
                "response": response,
                "responder": str(response_obj.get("responder", "user")),
                "metadata": response_obj.get("metadata", {}) if isinstance(response_obj.get("metadata", {}), dict) else {},
            }

        raise HumanInterventionRequired(
            {
                "node_id": node.id,
                "owner": owner,
                "question": question,
                "context_hint": context_hint,
                "required": required,
            }
        )


    raise ValueError(f"unsupported node type: {node.type}")


def _apply_output_contract(content: str, value: object) -> str:
    contract = value if isinstance(value, dict) else {}
    try:
        max_items = int(contract.get("max_items", 0) or 0)
    except (TypeError, ValueError):
        max_items = 0
    unit = str(contract.get("unit", "none")).strip().lower()
    if max_items <= 0 or unit not in {"utterance", "line"}:
        return content.strip()
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return "\n".join(lines[:max_items]).strip()


def _render_interactions(interactions: Any) -> str:
    if not isinstance(interactions, list):
        return ""
    lines: list[str] = []
    for item in interactions:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("mode", "dialogue"))
        relation = str(item.get("relation", "peer"))
        source = str(item.get("source", "unknown"))
        message = str(item.get("message", ""))
        lines.append(f"- ({mode}/{relation}) from {source}: {message}")
    return "\n".join(lines)


def _render_environment(environment: Any) -> str:
    if not isinstance(environment, dict):
        return ""
    profile = str(environment.get("profile", "")).strip()
    scenario = str(environment.get("scenario", "")).strip()
    time_context = str(environment.get("time_context", "")).strip()
    spatial_context = str(environment.get("spatial_context", "")).strip()
    facts = environment.get("facts", [])
    constraints = environment.get("constraints", [])
    glossary = environment.get("glossary", {})

    lines: list[str] = []
    if profile:
        lines.append(f"Profile: {profile}")
    if scenario:
        lines.append(f"Scenario: {scenario}")
    if time_context:
        lines.append(f"Time Context: {time_context}")
    if spatial_context:
        lines.append(f"Spatial Context: {spatial_context}")
    if isinstance(facts, list) and facts:
        lines.append("Facts:")
        lines.extend([f"- {str(item)}" for item in facts])
    if isinstance(constraints, list) and constraints:
        lines.append("Constraints:")
        lines.extend([f"- {str(item)}" for item in constraints])
    if isinstance(glossary, dict) and glossary:
        lines.append("Glossary:")
        for k, v in glossary.items():
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _render_memory(memories: Any, limit: int = 8) -> str:
    if not isinstance(memories, list):
        return ""
    buckets: dict[str, list[str]] = {"fact": [], "state": [], "character": []}
    other: list[str] = []
    for item in memories[-max(1, min(limit, 30)) :]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        node_id = str(item.get("node_id", "")).strip()
        kind = str(item.get("kind", "fact")).strip().lower() or "fact"
        subject = str(item.get("subject", "")).strip()
        importance = item.get("importance", "")
        source_seq = item.get("source_event_seq", "")
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        label = subject or role or node_id or "memory"
        meta = []
        if source_seq not in {"", None}:
            meta.append(f"event#{source_seq}")
        if importance not in {"", None}:
            meta.append(f"importance={importance}")
        suffix = f" ({', '.join(meta)})" if meta else ""
        line = f"- [{label}]{suffix} {content[:360]}"
        if kind in buckets:
            buckets[kind].append(line)
        else:
            other.append(line)
    lines: list[str] = []
    sections = [
        ("Facts", buckets["fact"]),
        ("Current State", buckets["state"]),
        ("Character State", buckets["character"]),
        ("Other", other),
    ]
    for title, rows in sections:
        if rows:
            lines.append(f"{title}:")
            lines.extend(rows)
    return "\n".join(lines)


def _render_context_pack(pack: Any) -> str:
    if not isinstance(pack, dict):
        return ""
    entries = pack.get("entries", [])
    if not isinstance(entries, list):
        return ""
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content", "")).strip()
        if not content:
            continue
        title = str(entry.get("title", "")).strip()
        kind = str(entry.get("kind", "background")).strip()
        label = f"{kind}: {title}" if title else kind
        lines.append(f"- [{label}] {content}")
    return "\n".join(lines)


def _entity_context_lines(
    *,
    node: WorkflowNode,
    simulation: Any,
    skip_behavior_if_in: str = "",
    include_identity: bool = True,
    profile_label: str = "Profile",
) -> list[str]:
    entity_type = str(node.config.get("entity_type", "")).strip()
    entity_name = str(node.config.get("entity_name", "")).strip()
    entity_profile = str(node.config.get("entity_profile", "")).strip()
    behavior_prompt = str(node.config.get("behavior_prompt", "")).strip()
    detail_instruction = _detail_instruction(entity_type, simulation)
    lines: list[str] = []
    if include_identity and entity_type:
        lines.append(f"Type: {entity_type}")
    if include_identity and entity_name:
        lines.append(f"Name: {entity_name}")
    if entity_profile:
        lines.append(f"{profile_label}: {entity_profile}")
    if entity_type:
        lines.append(f"Entity behavior contract: {entity_action_contract(entity_type)}")
    if behavior_prompt and behavior_prompt not in str(skip_behavior_if_in):
        lines.append(f"Basic behavior rule: {behavior_prompt}")
    if detail_instruction:
        lines.append(f"Detail granularity: {detail_instruction}")
    return lines


def _append_entity_context(
    *,
    prompt: str,
    node: WorkflowNode,
    simulation: Any,
    heading: str,
    skip_behavior_if_in: str = "",
    include_identity: bool = True,
    profile_label: str = "Profile",
) -> str:
    lines = _entity_context_lines(
        node=node,
        simulation=simulation,
        skip_behavior_if_in=skip_behavior_if_in,
        include_identity=include_identity,
        profile_label=profile_label,
    )
    if not lines:
        return prompt
    if heading:
        return f"{prompt}\n\n{heading}:\n" + "\n".join(lines)
    return f"{prompt}\n\n" + "\n".join(lines)


def _render_simulation(simulation: Any) -> str:
    if not isinstance(simulation, dict):
        return ""
    mode = str(simulation.get("mode", "")).strip()
    question = str(simulation.get("research_question", "")).strip()
    semantics = simulation.get("semantics", {})
    variables = simulation.get("variables", {})
    world_state = simulation.get("world_state", {})
    state_memory = simulation.get("state_memory", {})
    evidence = simulation.get("evidence", {})
    phase_index = int(simulation.get("current_phase_index", 0) or 0)
    phases = simulation.get("phases", [])
    lines: list[str] = []
    if mode:
        lines.append(f"Mode: {mode}")
    granularity = str(simulation.get("detail_granularity", "")).strip()
    if granularity:
        lines.append(f"Detail Granularity: {granularity}")
    if question:
        lines.append(f"Question: {question}")
    if isinstance(semantics, dict) and semantics:
        sem_mode = str(semantics.get("mode", "")).strip()
        sem_confidence = str(semantics.get("confidence", "")).strip()
        sem_reason = str(semantics.get("reason", "")).strip()
        if sem_mode:
            lines.append(f"Simulation Semantics: {sem_mode} (confidence={sem_confidence})")
        if sem_reason:
            lines.append(f"Semantics Reason: {sem_reason}")
    if isinstance(phases, list) and phases:
        if 0 <= phase_index < len(phases) and isinstance(phases[phase_index], dict):
            phase_name = str(phases[phase_index].get("name", "")).strip()
            if phase_name:
                lines.append(f"Current Phase: {phase_name}")
    if isinstance(variables, dict) and variables:
        vars_part = ", ".join([f"{k}={v}" for k, v in variables.items()])
        lines.append(f"Variables: {vars_part}")
    if isinstance(world_state, dict) and world_state:
        world_parts = []
        for key in ["current_time", "current_location", "temporal_scope", "spatial_scope"]:
            value = str(world_state.get(key, "")).strip()
            if value:
                world_parts.append(f"{key}={value}")
        if world_parts:
            lines.append(f"World State: {', '.join(world_parts)}")
        conditions = world_state.get("conditions", [])
        if isinstance(conditions, list) and conditions:
            lines.append("World Conditions:")
            lines.extend([f"- {str(item)}" for item in conditions[-8:]])
        active_events = world_state.get("active_events", [])
        if isinstance(active_events, list) and active_events:
            lines.append("Active Events:")
            lines.extend([f"- {str(item)}" for item in active_events[-8:]])
    if isinstance(state_memory, dict):
        current_states = state_memory.get("current_states", {})
        if isinstance(current_states, dict) and current_states:
            memory_part = ", ".join([f"{k}={v}" for k, v in current_states.items()])
            lines.append(f"State Memory: {memory_part}")
    if isinstance(evidence, dict):
        items = evidence.get("items", [])
        if isinstance(items, list) and items:
            lines.append("User-confirmed Evidence:")
            for item in items[:8]:
                if isinstance(item, dict):
                    name = str(item.get("name", "source"))
                    content = " ".join(str(item.get("content", "")).split())[:1200]
                    lines.append(f"- [{name}] {content}")
    return "\n".join(lines)


def _detail_instruction(entity_type: str, simulation: Any) -> str:
    if not isinstance(simulation, dict):
        return ""
    granularity = str(simulation.get("detail_granularity", "concise")).strip().lower()
    if granularity != "detailed":
        return ""
    instructions = {
        "individual": (
            "Do not summarize the scene. Expand observable personal behavior: key spoken lines, pauses, gestures, "
            "position changes, object interactions, emotional triggers, and private thoughts when useful."
        ),
        "group": (
            "Do not write the group as one person. Expand collective detail through atmosphere, opinion distribution, "
            "typical short quotes, subgroup reactions, rumors, silence, avoidance, pressure, and visible social signals."
        ),
        "organization": (
            "Expand institutional detail through decisions, procedures, assignments, resource changes, internal factions, "
            "approval delays, enforcement actions, and strategic tradeoffs. Do not make it casual personal chatter."
        ),
        "environment": (
            "Expand environmental detail through time, place, sensory conditions, rules, risk, resource constraints, "
            "visibility, accessibility, and how these change the cost or possibility of actions."
        ),
        "event": (
            "Expand the event as a concrete interruption or state change: what happens, immediate sequence, affected entities, "
            "intensity, duration, direct consequences, and likely aftershocks. Do not turn it into a long-term character."
        ),
        "artifact": (
            "Expand artifact detail through content, physical/digital state, owner, visibility, credibility, circulation, "
            "how it is read or used, and what it changes in belief, trust, leverage, or available action."
        ),
    }
    return instructions.get(entity_type, "Expand concrete observable process instead of only summarizing the result.")


async def _execute_external_agent(node: WorkflowNode, node_input: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    mode = str(node.config.get("integration_mode", "mock")).strip().lower()
    prompt = str(resolve_value(node_input.get("prompt", ""), {**context, **node_input}))
    run_ctx = context.get("_run", {}) if isinstance(context.get("_run"), dict) else {}
    run_id = str(run_ctx.get("run_id", "run_unknown"))
    node_id = node.id
    timeout_ms = int(node.config.get("timeout_ms", 60000) or 60000)
    max_tokens = int(node.config.get("max_tokens", 1500) or 1500)
    include_background = str(node.config.get("include_background_in_prompt", "true")).strip().lower() in {"true", "1", "yes", "y"}

    runtime_environment = node_input.get("_environment", {})
    env_block = _render_environment(runtime_environment)
    interaction_block = _render_interactions(node_input.get("_interactions", []))
    memory_block = _render_memory(node_input.get("_memory", []), limit=8)
    simulation_block = _render_simulation(node_input.get("_simulation", context.get("_simulation", {})))
    global_guidance = str(context.get("_global_guidance", ""))
    prompt_template = str(node.config.get("prompt_template", "{{prompt}}"))
    if include_background:
        enriched_prompt = (
            prompt_template.replace("{{prompt}}", prompt)
            .replace("{{environment}}", env_block)
            .replace("{{global_guidance}}", global_guidance)
            .replace("{{interactions}}", interaction_block)
            .replace("{{memory}}", memory_block)
            .replace("{{simulation}}", simulation_block)
        )
    else:
        enriched_prompt = prompt

    req = ExternalAgentTaskRequest(
        task_id=f"task_{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        node_id=node_id,
        context=ExternalAgentContext(
            environment=runtime_environment if isinstance(runtime_environment, dict) else {},
            global_guidance=str(context.get("_global_guidance", "")),
            interactions=node_input.get("_interactions", []) if isinstance(node_input.get("_interactions"), list) else [],
            task_goal=str(context.get("input", {}).get("task", "")) if isinstance(context.get("input"), dict) else "",
            constraints={"max_tokens": max_tokens, "timeout_ms": timeout_ms},
        ),
        input={
            "text": enriched_prompt,
            "raw_prompt": prompt,
            "background": {
                "environment": runtime_environment,
                "global_guidance": global_guidance,
                "interactions": node_input.get("_interactions", []) if isinstance(node_input.get("_interactions"), list) else [],
                "memory": node_input.get("_memory", []) if isinstance(node_input.get("_memory"), list) else [],
                "simulation": node_input.get("_simulation", {}) if isinstance(node_input.get("_simulation"), dict) else {},
            },
        },
        constraints={"max_tokens": max_tokens, "timeout_ms": timeout_ms},
        reply_mode="sync",
    )

    if mode == "mock":
        return {
            "agent_id": str(node.config.get("agent_id", "user_agent")),
            "status": "succeeded",
            "text": f"[mock external agent] {enriched_prompt}",
            "protocol_version": "1.0",
            "metrics": {"latency_ms": 1, "token_in": max(1, len(enriched_prompt) // 4), "token_out": max(1, len(enriched_prompt) // 5)},
        }

    if mode not in {"http", "bridge_local"}:
        raise ValueError("external_agent.integration_mode must be mock, http, or bridge_local")

    endpoint_url = str(node.config.get("endpoint_url", "")).strip()
    if mode == "bridge_local" and not endpoint_url:
        endpoint_url = "http://127.0.0.1:8787"
    if not endpoint_url:
        raise ValueError("external_agent.endpoint_url is required when integration_mode=http")
    parsed_endpoint = urlparse(endpoint_url)
    if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.hostname:
        raise ValueError("external_agent.endpoint_url must use http or https")
    endpoint_path = str(node.config.get("endpoint_path", "/agent/tasks")).strip() or "/agent/tasks"
    api_key = str(node.config.get("api_key", "")).strip()
    url = f"{endpoint_url.rstrip('/')}{endpoint_path}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=max(1.0, timeout_ms / 1000)) as client:
            resp = await client.post(url, headers=headers, json=req.model_dump())
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000]
        raise ValueError(f"external agent returned HTTP {exc.response.status_code}: {detail}") from exc
    except httpx.RequestError as exc:
        raise ValueError(f"external agent connection failed: {exc}") from exc
    data = resp.json()
    parsed = ExternalAgentTaskResponse.model_validate(data)
    if parsed.task_id != req.task_id:
        raise ValueError("external agent response task_id does not match the request")
    if parsed.status != "succeeded":
        raise ValueError(f"external agent failed: {parsed.errors}")
    text = str(parsed.output.get("text", ""))
    return {
        "agent_id": str(node.config.get("agent_id", "user_agent")),
        "status": parsed.status,
        "text": text,
        "protocol_version": "1.0",
        "metrics": parsed.metrics,
        "raw": parsed.model_dump(),
    }


async def _run_gpro_director(
    provider: OpenAICompatibleProvider,
    model: str,
    objective: str,
    guardrails: str,
    situation: str,
    context: dict[str, Any],
    candidates: int,
) -> dict[str, Any]:
    sys_prompt = (
        "You are a top-level director for a multi-agent simulation. "
        "Produce concise corrective guidance for all agents."
    )
    base_user = (
        f"Objective:\n{objective}\n\n"
        f"Guardrails:\n{guardrails}\n\n"
        f"Situation:\n{situation}\n\n"
        "Output one short global guidance paragraph."
    )
    scored: list[dict[str, Any]] = []
    for i in range(candidates):
        response = await provider.chat(model=model, system_prompt=sys_prompt, user_prompt=f"{base_user}\n\nCandidate index: {i+1}")
        content = str(response.get("content", "")).strip()
        score = _score_guidance(content=content, objective=objective, guardrails=guardrails, context=context)
        scored.append({"candidate_index": i + 1, "guidance": content, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0] if scored else {"candidate_index": 1, "guidance": "", "score": 0.0}
    return {
        "global_guidance": best["guidance"],
        "best_score": best["score"],
        "gpro": {
            "enabled": True,
            "candidate_count": candidates,
            "selected_index": best["candidate_index"],
            "candidates": scored,
        },
    }


def _score_guidance(content: str, objective: str, guardrails: str, context: dict[str, Any]) -> float:
    if not content.strip():
        return -100.0
    score = 0.0
    objective_tokens = {t.lower() for t in re.findall(r"[a-zA-Z0-9_]+", objective) if len(t) > 3}
    guardrail_tokens = {t.lower() for t in re.findall(r"[a-zA-Z0-9_]+", guardrails) if len(t) > 3}
    text = content.lower()
    score += sum(1.5 for t in objective_tokens if t in text)
    score += sum(1.0 for t in guardrail_tokens if t in text)
    # Prefer concise global guidance.
    word_count = len(re.findall(r"\S+", content))
    if word_count <= 90:
        score += 6.0
    else:
        score -= min((word_count - 90) * 0.08, 12.0)
    # Encourage referencing active context keys for stronger alignment.
    score += sum(0.5 for k in context.keys() if isinstance(k, str) and k in text)
    return score


