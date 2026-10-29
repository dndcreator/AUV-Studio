from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..db import Base
from ..execution.action_consistency import assess_candidate
from ..execution.action_output import normalize_action_output
from ..execution.dynamic_state import compact_dynamic_state, init_dynamic_state, state_coder_instruction
from ..execution.context_book import build_scene_context_pack, context_for_node
from ..execution.memory import select_memories_for_node
from ..execution.engine import WorkflowEngine
from ..execution.nodes import execute_node
from ..execution.scene_control import apply_scene_audit, init_scene_control, scene_audit_instruction
from ..execution.simulation_loop import action_instruction, build_role_packet, parse_director_control
from ..execution.state_coding import apply_state_batch, proposal_from_response
from ..plan_compiler import compile_workflow_from_plan
from ..schemas import EnvironmentSpec, PlanCompileRequest, RunReportRequest, RunRequest, WorkflowNode
from ..service import create_workflow, generate_run_report, get_events, get_run
from .long_horizon import build_long_horizon_checkpoints, probe_prompt
from .scenario import load_scenario
from .judging import strict_object


def actor_input(payload: dict[str, Any]) -> tuple[WorkflowNode, dict[str, Any]]:
    node = WorkflowNode(id="actor", type="agent", position={"x": 0, "y": 0}, config=payload["role"])
    role = build_role_packet(node, payload.get("memory", []))
    context = {"round": 1, "action_index": 1, "state": {}, "recent_actions": [], "memory": [],
               "scene_contract": {}, **payload.get("context", {})}
    return node, {"context": context, "role": role, "_participant_turn": True, "prompt": action_instruction(context, role)}


async def run_target(case: dict[str, Any], provider: Any) -> Any:
    kind, payload = case["capability"], case["input"]
    if kind == "planning":
        compiled = await compile_workflow_from_plan(provider, PlanCompileRequest.model_validate(payload))
        workflow = compiled.workflow.model_dump(mode="json")
        # Generated UUIDs are bookkeeping, not semantic evidence or benchmark identity.
        return {"extracted": compiled.extracted, "nodes": workflow["nodes"], "environment": workflow["environment"]}
    if kind == "actor":
        node, runtime_input = actor_input(payload)
        output = await execute_node(node, runtime_input, {}, provider)
        return normalize_action_output(output, round_index=1, action_index=1)
    if kind == "action_check":
        evidence, candidate = payload["evidence"], payload["candidate"]
        return await assess_candidate(provider=provider, candidate=candidate, evidence=evidence, record=lambda _: None)
    if kind == "director":
        state = payload.get("state", {})
        scene = {**init_scene_control(), **payload["scene_control"]}
        round_index = payload.get("round", 1)
        prompt = scene_audit_instruction(state=state, scene_control=scene, recent_actions=payload["actions"],
                                         round_index=round_index, max_actions=12)
        response = await provider.chat("", "You are a continuity auditor and macro-boundary controller, not the central decision maker.", prompt)
        strict_object(response["content"])
        control = parse_director_control(response["content"])
        next_scene, assessment = apply_scene_audit(scene, control, state=state, round_index=round_index)
        return {"control": control, "assessment": assessment, "scene": next_scene}
    if kind == "state_coder":
        state = {"dynamic_state": init_dynamic_state({"enabled": True}), **payload.get("state", {})}
        response = await provider.chat("", "You are a State Coder repairing an uncommitted batch, not a director or participant.",
                                       state_coder_instruction(state=state, actions=payload["actions"], round_index=1))
        proposal = proposal_from_response(response)
        if proposal.get("parse_error"):
            raise ValueError("invalid state coder output")
        updated, result = apply_state_batch(state["dynamic_state"], proposal, actions=payload["actions"], seq=1, round_index=1)
        return {"state": updated, "result": result}
    if kind == "memory":
        if "checkpoint" in payload:
            from ..long_horizon_eval import _load_scenario
            checkpoint = next(row for row in build_long_horizon_checkpoints(_load_scenario()) if row["id"] == payload["checkpoint"])
        else:
            node, runtime_input = actor_input(payload)
            environment = EnvironmentSpec.model_validate(payload.get("environment", {}))
            state = payload.get("context", {}).get("state", {})
            pack = build_scene_context_pack(environment=environment, state=state, recent_actions=[])
            memories = select_memories_for_node(payload.get("memories", []), node, include_peer_facts=False)
            checkpoint = {"question": payload["question"], "packet": {
                "role": runtime_input["role"], "memory": memories, "background": context_for_node(pack, node),
                "state": state, "recent_actions": [],
            }}
        response = await provider.chat("", "Act as the specified participant using only supplied recollections.", probe_prompt(checkpoint))
        return {"response": strict_object(response["content"]), "checkpoint": checkpoint["packet"], "question": checkpoint["question"]}
    if kind == "narrative":
        from ..narrative_fidelity import generate_grounded_narrative
        text, fidelity = await generate_grounded_narrative(provider, source=payload["source"], style=payload["style"],
                                                           language="Chinese", audience="general", length="short")
        return {"text": text, "fidelity": fidelity}
    if kind == "system":
        return await _run_system(payload, provider)
    raise ValueError("unknown evaluation capability")


async def _run_system(payload: dict[str, Any], provider: Any) -> dict[str, Any]:
    from ..eval_runner import _validate_scenario_safety
    scenario, workflow = load_scenario(payload["scenario"])
    if scenario.get("plan_text"):
        compiled = await compile_workflow_from_plan(provider, PlanCompileRequest(plan_text=scenario["plan_text"], max_agents=int(scenario.get("max_agents", 2))))
        workflow = compiled.workflow
        limits = scenario.get("run_limits", {})
        workflow.environment.simulation["horizon_rounds"] = min(24, int(limits.get("rounds", 2)))
        workflow.environment.simulation["output_contract"] = {"unit": "action", "max_items": int(limits.get("actions", 3))}
    if "rounds" in payload:
        workflow.environment.simulation["horizon_rounds"] = max(1, min(24, int(payload["rounds"])))
        workflow.environment.simulation["output_contract"] = {"unit": "action", "max_items": max(1, min(120, int(payload.get("actions", 72))))}
    _validate_scenario_safety(workflow)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with sessionmaker(bind=engine)() as db:
            create_workflow(db, workflow)
            run_id = await WorkflowEngine(provider).run(db, workflow, RunRequest(input=scenario.get("input", {})))
            detail = get_run(db, run_id)
            events = get_events(db, run_id, after_seq=0, limit=10000).events
            output = {**detail.output}
            simulation = output.get("simulation", {})
            output["simulation"] = {**simulation, "dynamic_state": compact_dynamic_state(simulation.get("dynamic_state", {}), include_private=True)}
            result = {"status": detail.status, "task": scenario.get("plan_text") or scenario.get("input", {}),
                    "roles": {node.id: {key: node.config[key] for key in ("entity_name", "role", "entity_profile", "identity_contract") if key in node.config}
                              for node in workflow.nodes if node.type == "agent"},
                    "observed_rounds": max((row.get("round", 0) for row in detail.output.get("actions", [])), default=0),
                    "output": output, "error": detail.error,
                    "coding_progress": [{"seq": event.seq, **{key: event.payload.get(key) for key in
                        ("round", "coding", "rejected", "version", "schema_version")}}
                        for event in events if event.event == "dynamic_state_updated"],
                    "failed_checks": [{"seq": event.seq, "node_id": event.node_id, "assessment": event.payload.get("assessment"),
                                       "context_digest": event.payload.get("context_digest")}
                                      for event in events if event.event == "action_checked"
                                      and event.payload.get("assessment", {}).get("decision") != "accept"],
                    "audits": [{"seq": event.seq, "event": event.event, "payload": {
                        key: value for key, value in event.payload.items() if key in
                        {"round", "decision", "task_complete", "completion_reason", "progress_assessment"}}}
                        for event in events if event.event == "episode_audited"]}
            result["deliverable_status"] = "not_requested"
            if payload.get("deliverable"):
                result["deliverable_status"] = "skipped_incomplete"
                if detail.status == "succeeded":
                    try:
                        report = await generate_run_report(db, provider, run_id, RunReportRequest.model_validate(payload["deliverable"]))
                        result.update(deliverable_status="generated", report={"text": report.report_markdown, "fidelity": report.fidelity})
                    except Exception as exc:
                        result.update(deliverable_status="failed", report_error={"type": type(exc).__name__, "code": getattr(exc, "code", "report_error")})
            return result
    finally:
        engine.dispose()
