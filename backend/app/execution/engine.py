from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from ..audit import load_last_event_hash
from ..models import NodeRunRecord, RunRecord
from ..schemas import RunRequest, WorkflowDefinition, WorkflowEdge, WorkflowNode
from .nodes import HumanInterventionRequired, execute_node, resolve_inputs
from .provider import OpenAICompatibleProvider
from .context_book import activation_trace, build_scene_context_pack, context_for_node, environment_for_runtime
from .dynamic_state import (
    apply_dynamic_state_proposal,
    compact_dynamic_state,
    dynamic_state_enabled,
    expire_dynamic_state,
    simulation_state_for_node,
    state_coder_audit_clause,
)
from .checkpoints import (
    build_context_snapshot,
    continuous_snapshot,
    restore_continuous_checkpoint,
    runtime_context_pack_snapshot,
)
from .director_policy import (
    assess_output_quality,
    build_preflight_preview,
    build_quality_guidance,
    collect_vote_ballots,
    is_critical_decision,
    parse_director_quality_policy,
    parse_preflight_confirm_policy,
)
from .events import (
    emit_event,
    emit_human_resumed,
    is_director_preflight_response,
    load_previous_outputs,
    load_previous_outputs_until_event,
    redact_secrets,
    serialize,
)
from .graph import (
    build_interaction_message,
    descendants,
    should_follow_condition,
    topological_sort,
    validate_workflow,
)
from .memory import (
    append_memory_record,
    build_memory_meta,
    extract_memory_content,
    extract_role,
    load_recent_memories,
    memories_at_rollback,
    memory_importance,
    node_subject,
    pull_latest_director_override,
    select_memories_for_node,
    should_write_memory,
)
from .simulation_loop import (
    activation_instruction,
    action_instruction,
    build_context_packet,
    build_loop_policy,
    build_role_packet,
    is_entity_node,
    normalize_action_output,
    parse_activation,
    parse_director_control,
)
from .output_utils import output_text
from .scene_control import apply_scene_audit, init_scene_control, normalize_scene_contract, scene_audit_instruction
from .simulation_state import init_simulation_state, update_simulation_state


class WorkflowEngine:
    def __init__(self, provider: OpenAICompatibleProvider) -> None:
        self.provider = provider

    def _restore_retry_context(
        self,
        *,
        db: Session,
        request: RunRequest,
        run_id: str,
        workflow: WorkflowDefinition,
        context: dict[str, Any],
        node_output: dict[str, dict[str, Any]],
        node_status: dict[str, str],
    ) -> set[str]:
        execute_scope: set[str] = {n.id for n in workflow.nodes}
        if not request.retry_from_node:
            return execute_scope

        execute_scope = descendants(request.retry_from_node, workflow.edges)
        execute_scope.add(request.retry_from_node)
        rollback = request.input.get("_rollback") if isinstance(request.input, dict) else None
        rollback_seq = rollback.get("event_seq") if isinstance(rollback, dict) else None
        if isinstance(rollback_seq, int):
            prev = load_previous_outputs_until_event(db, request.retry_from_run_id or run_id, rollback_seq)
        else:
            prev = load_previous_outputs(db, request.retry_from_run_id or run_id)
        for nid, output in prev.items():
            if nid not in execute_scope:
                context[nid] = output
                node_output[nid] = output
                node_status[nid] = "succeeded"
        return execute_scope

    @staticmethod
    def _parent_status_for_routing(
        *,
        source: str,
        node_status: dict[str, str],
        node_output: dict[str, dict[str, Any]],
        execute_scope: set[str],
    ) -> str | None:
        src_status = node_status.get(source)
        if src_status != "succeeded" and source not in execute_scope and source in node_output:
            node_status[source] = "succeeded"
            return "succeeded"
        return src_status

    @staticmethod
    def _stop_requested(db: Session, run_id: str) -> bool:
        db.expire_all()
        status = db.query(RunRecord.status).filter(RunRecord.id == run_id).scalar()
        return status in {"stopping", "stopped"}

    def _finish_stopped_run(
        self,
        db: Session,
        run: RunRecord,
        *,
        output: dict[str, Any],
        last_event_hash: str,
    ) -> str:
        db.refresh(run)
        run.status = "stopped"
        run.output_json = serialize(output)
        run.error_json = "null"
        run.ended_at = datetime.now(timezone.utc)
        db.add(run)
        db.commit()
        emit_event(
            db,
            run.id,
            "run_control",
            "stopped",
            {"status": "stopped", "output_preserved": True},
            caused_by="user",
            context_snapshot={"status": "stopped"},
            prev_hash=last_event_hash,
        )
        return run.id

    def _maybe_director_quality_correction(
        self,
        *,
        db: Session,
        run_id: str,
        workflow_id: str,
        node: WorkflowNode,
        runtime_input: dict[str, Any],
        output: dict[str, Any],
        success_seq: int,
        last_event_hash: str,
        context: dict[str, Any],
        executed_non_director_count: int,
    ) -> tuple[str, str]:
        policy = context.get("_director_quality_policy", {})
        if not isinstance(policy, dict) or not bool(policy.get("enabled", True)):
            return last_event_hash, ""
        if node.type in {"director", "condition"}:
            return last_event_hash, ""
        interval = int(policy.get("interval_nodes", 2) or 2)
        if executed_non_director_count % max(1, interval) != 0:
            return last_event_hash, ""
        simulation = context.get("_simulation", {}) if isinstance(context.get("_simulation"), dict) else {}
        assessment = assess_output_quality(node=node, runtime_input=runtime_input, output=output, simulation=simulation)
        threshold = float(policy.get("threshold", 0.4) or 0.4)
        if float(assessment.get("score", 0.0) or 0.0) >= threshold:
            return last_event_hash, ""
        guidance = build_quality_guidance(node=node, assessment=assessment, simulation=simulation)
        _, last_event_hash = emit_event(
            db,
            run_id,
            "director_console",
            "director_corrected",
            {
                "trigger_node_id": node.id,
                "score": assessment.get("score"),
                "threshold": threshold,
                "reasons": assessment.get("reasons", []),
                "guidance": guidance,
            },
            caused_by="director_quality",
            parent_event_ids=[success_seq],
            context_snapshot={"workflow_id": workflow_id, "trigger_node_id": node.id, "assessment": assessment},
            prev_hash=last_event_hash,
        )
        return last_event_hash, guidance

    def _maybe_director_vote(
        self,
        *,
        db: Session,
        run_id: str,
        workflow_id: str,
        node: WorkflowNode,
        runtime_input: dict[str, Any],
        output: dict[str, Any],
        parent_event_ids: list[int],
        success_seq: int,
        last_event_hash: str,
        context: dict[str, Any],
    ) -> tuple[str, str]:
        interactions = runtime_input.get("_interactions", [])
        if not isinstance(interactions, list) or len(interactions) < 2:
            return last_event_hash, ""
        if not is_critical_decision(runtime_input, output):
            return last_event_hash, ""
        ballots = collect_vote_ballots(interactions)
        yes_votes = sum(1 for b in ballots if b["vote"] == "yes")
        no_votes = sum(1 for b in ballots if b["vote"] == "no")
        if yes_votes == 0 or no_votes == 0:
            return last_event_hash, ""

        topic = ""
        for key in ["question", "prompt"]:
            raw = runtime_input.get(key)
            if isinstance(raw, str) and raw.strip():
                topic = raw.strip()[:220]
                break
        if not topic:
            topic = f"Decision at node {node.id}"

        common_parents = [*parent_event_ids, success_seq]
        started_seq, last_event_hash = emit_event(
            db,
            run_id,
            "director_console",
            "director_vote_started",
            {
                "topic": topic,
                "trigger_node_id": node.id,
                "reason": "conflict_detected",
                "ballot_candidates": len(ballots),
            },
            caused_by="director_vote",
            parent_event_ids=common_parents,
            context_snapshot={"workflow_id": workflow_id, "trigger_node_id": node.id},
            prev_hash=last_event_hash,
        )
        cast_event_ids = [started_seq]
        for ballot in ballots:
            cast_seq, last_event_hash = emit_event(
                db,
                run_id,
                "director_console",
                "director_vote_cast",
                {"topic": topic, "source": ballot["source"], "vote": ballot["vote"], "reason": ballot["reason"]},
                caused_by="director_vote",
                parent_event_ids=[started_seq],
                context_snapshot={"trigger_node_id": node.id},
                prev_hash=last_event_hash,
            )
            cast_event_ids.append(cast_seq)

        total = yes_votes + no_votes
        support_ratio = yes_votes / total if total > 0 else 0.0
        passed = support_ratio >= 0.6 and yes_votes > no_votes
        winner = "yes" if yes_votes >= no_votes else "no"
        guidance = (
            f"[Director Vote] topic='{topic}' result={'PASS' if passed else 'NOT_PASS'} "
            f"(yes={yes_votes}, no={no_votes}, ratio={support_ratio:.2f}). "
            f"{'Proceed with stricter risk checks.' if passed else 'Do not proceed; revise assumptions and resubmit.'}"
        )
        _, last_event_hash = emit_event(
            db,
            run_id,
            "director_console",
            "director_vote_finished",
            {
                "topic": topic,
                "trigger_node_id": node.id,
                "counts": {"yes": yes_votes, "no": no_votes, "abstain": sum(1 for b in ballots if b["vote"] == "abstain")},
                "winner": winner,
                "support_ratio": round(support_ratio, 4),
                "passed": passed,
                "guidance": guidance,
            },
            caused_by="director_vote",
            parent_event_ids=cast_event_ids,
            context_snapshot={"trigger_node_id": node.id, "topic": topic},
            prev_hash=last_event_hash,
        )
        return last_event_hash, guidance

    async def _run_continuous_simulation(
        self,
        *,
        db: Session,
        workflow: WorkflowDefinition,
        run: RunRecord,
        context: dict[str, Any],
        last_event_hash: str,
    ) -> str:
        policy = build_loop_policy(workflow)
        entities = [node for node in workflow.nodes if is_entity_node(node)]
        director = next((node for node in workflow.nodes if node.type == "director"), None)
        summary = next((node for node in workflow.nodes if node.id.startswith("summary_")), None)
        resume = context.pop("_continuous_resume", {})
        recent_actions = list(resume.get("actions", [])) if isinstance(resume, dict) and isinstance(resume.get("actions"), list) else []
        simulation_seq = int(resume.get("simulation_seq", 0) or 0) if isinstance(resume, dict) else 0
        guidance = ""
        director_override_cursor = 0
        activation_cursor = 0
        state_coder_cursor = len(recent_actions)
        context.setdefault("_scene_control", init_scene_control())

        stop_requested = False
        action_index = int(resume.get("action_index", len(recent_actions)) or 0) if isinstance(resume, dict) else 0
        start_round = int(resume.get("round", 0) or 0) + 1 if isinstance(resume, dict) else 1
        for round_index in range(start_round, policy.max_rounds + 1):
            simulation_state = context.get("_simulation", {})
            if isinstance(simulation_state, dict) and dynamic_state_enabled(simulation_state):
                expired_state, expiry_result = expire_dynamic_state(
                    simulation_state.get("dynamic_state", {}), round_index=round_index, seq=simulation_seq
                )
                context["_simulation"]["dynamic_state"] = expired_state
                if expiry_result["changed"]:
                    _, last_event_hash = emit_event(
                        db,
                        run.id,
                        "state_coder",
                        "dynamic_state_updated",
                        {
                            "round": round_index,
                            **expiry_result,
                            "dynamic_state": compact_dynamic_state(expired_state),
                            "_rollback_snapshot": continuous_snapshot(
                                context, recent_actions, round_index, action_index, simulation_seq
                            ),
                        },
                        caused_by="state_retention",
                        prev_hash=last_event_hash,
                    )
            activation_response = await self.provider.chat(
                model="",
                system_prompt=(
                    "You route attention in a distributed multi-agent process. Select relevant autonomous nodes by "
                    "semantic relevance only. You do not decide their actions and you are not a story writer."
                ),
                user_prompt=activation_instruction(
                    state=context.get("_simulation", {}),
                    recent_actions=recent_actions,
                    nodes=entities,
                    scene_control=context["_scene_control"],
                ),
            )
            activation = parse_activation(
                str(activation_response.get("content", "")),
                entities,
                fallback_index=activation_cursor,
            )
            activation_cursor += 1
            if not context["_scene_control"].get("contract"):
                context["_scene_control"]["contract"] = normalize_scene_contract(
                    activation.get("scene_contract"), context.get("_simulation", {})
                )
            activation["scene_contract"] = context["_scene_control"]["contract"]
            active_ids = set(activation["active_node_ids"])
            active_nodes = [node for node in entities if node.id in active_ids]
            _, last_event_hash = emit_event(
                db,
                run.id,
                "distributed_router",
                "nodes_activated",
                {"episode": round_index, **activation},
                caused_by="semantic_router",
                context_snapshot={"episode": round_index, "active_node_ids": activation["active_node_ids"]},
                prev_hash=last_event_hash,
            )
            boundary_votes = 0
            for node in active_nodes:
                if action_index >= policy.max_actions or stop_requested:
                    break
                if self._stop_requested(db, run.id):
                    return self._finish_stopped_run(
                        db,
                        run,
                        output={"actions": recent_actions, "simulation": context.get("_simulation", {}), "scene_control": context.get("_scene_control", {})},
                        last_event_hash=load_last_event_hash(db, run.id)[1],
                    )
                director_override_cursor, override_guidance = pull_latest_director_override(
                    db,
                    workflow_id=workflow.id,
                    run_id=run.id,
                    after_id=director_override_cursor,
                )
                if override_guidance:
                    guidance = override_guidance
                    context["_global_guidance"] = guidance
                memories = select_memories_for_node(
                    context.get("_memory", []),
                    node,
                    include_peer_facts=False,
                )
                node_uses_external_runtime = node.type == "external_agent" or (
                    node.type == "agent" and str(node.config.get("execution_mode", "")).strip().lower() == "external_agent"
                )
                node_context_pack = (
                    context_for_node(context.get("_context_pack", {}), node)
                    if not node_uses_external_runtime
                    else {"episode": context.get("_context_pack", {}).get("episode", 0), "estimated_tokens": 0, "entries": []}
                )
                node_simulation_state = simulation_state_for_node(context.get("_simulation", {}), node.id)
                context_packet = build_context_packet(
                    state=node_simulation_state,
                    memories=memories,
                    recent_actions=recent_actions,
                    director_guidance=guidance,
                    background_context=node_context_pack,
                    round_index=round_index,
                    action_index=action_index + 1,
                    scene_contract=context["_scene_control"]["contract"],
                )
                role_packet = build_role_packet(node, memories)
                runtime_input = {
                    **resolve_inputs(node, context),
                    "prompt": action_instruction(context_packet, role_packet),
                    "context": context_packet,
                    "role": role_packet,
                    "_environment": context.get("_runtime_environment", {}),
                    "_context_pack": node_context_pack,
                    "_memory": memories,
                    "_simulation": node_simulation_state,
                    "_global_guidance": guidance,
                }
                output, _, success_seq, last_event_hash = await self._execute_continuous_node(
                    db=db,
                    run_id=run.id,
                    workflow_id=workflow.id,
                    node=node,
                    runtime_input=runtime_input,
                    context=context,
                    simulation_seq=simulation_seq + 1,
                    last_event_hash=last_event_hash,
                    caused_by="simulation_turn",
                )
                action_index += 1
                simulation_seq += 1
                context[node.id] = output
                recent_actions.append(
                    {
                        "round": round_index,
                        "action_index": action_index,
                        "event_seq": success_seq,
                        "node_id": node.id,
                        "actor": node_subject(node),
                        "participation": output.get("participation", "act"),
                        "action": output.get("action", ""),
                        "turn_delta": output.get("turn_delta", ""),
                        "intent": output.get("intent", ""),
                        "observation": output.get("observation", ""),
                        "shared_effect_claim": output.get("shared_effect_claim", ""),
                    }
                )
                if str(output.get("episode_signal") or output.get("boundary_signal", "none")) == "ready":
                    boundary_votes += 1
                memory_content = str(output.get("action", "")).strip()
                if memory_content:
                    role_name = extract_role(node.type, node, output)
                    append_memory_record(
                        db=db,
                        context=context,
                        workflow_id=workflow.id,
                        run_id=run.id,
                        node=node,
                        role=role_name,
                        content=memory_content,
                        meta=build_memory_meta(
                            kind="fact",
                            node=node,
                            role=role_name,
                            importance=0.7,
                            confidence=0.75,
                            source_event_seq=success_seq,
                            trigger=f"simulation_action#{action_index}",
                        ),
                    )
                    character_state = {
                        "subject": node_subject(node),
                        "role": role_name,
                        "last_action": memory_content[:1000],
                        "round": round_index,
                        "action_index": action_index,
                        "self_state": str(output.get("self_update") or output.get("private_state_update", ""))[:1600],
                        "active_intent": str(output.get("intent") or output.get("proposal_summary", ""))[:1200],
                    }
                    append_memory_record(
                        db=db,
                        context=context,
                        workflow_id=workflow.id,
                        run_id=run.id,
                        node=node,
                        role=role_name,
                        content=json.dumps(character_state, ensure_ascii=False, default=str),
                        meta=build_memory_meta(
                            kind="character",
                            node=node,
                            role=role_name,
                            importance=0.65,
                            confidence=0.7,
                            source_event_seq=success_seq,
                            trigger=f"simulation_action#{action_index}",
                        ),
                    )

            if self._stop_requested(db, run.id):
                return self._finish_stopped_run(
                    db,
                    run,
                    output={"actions": recent_actions, "simulation": context.get("_simulation", {}), "scene_control": context.get("_scene_control", {})},
                    last_event_hash=load_last_event_hash(db, run.id)[1],
                )
            should_audit = bool(active_nodes) and (
                boundary_votes > 0
                or round_index % policy.director_interval_rounds == 0
                or action_index >= policy.max_actions
                or round_index >= policy.max_rounds
                or int(context["_scene_control"].get("stagnation", 0)) >= 2
            )
            if director is not None and should_audit:
                simulation_state = context.get("_simulation", {})
                state_coder_active = isinstance(simulation_state, dict) and dynamic_state_enabled(simulation_state)
                uncoded_actions = recent_actions[state_coder_cursor:] if state_coder_active else []
                state_coder_prompt = state_coder_audit_clause(uncoded_actions) if uncoded_actions else ""
                control_prompt = scene_audit_instruction(
                    state=context.get("_simulation", {}),
                    scene_control=context["_scene_control"],
                    recent_actions=recent_actions,
                    round_index=round_index,
                    max_actions=policy.max_actions,
                ) + f"\n\n{state_coder_prompt}"
                response = await self.provider.chat(
                    model="",
                    system_prompt="You are a continuity auditor and macro-boundary controller, not the central decision maker.",
                    user_prompt=control_prompt,
                )
                control = parse_director_control(str(response.get("content", "")), default_guidance=guidance)
                if control["decision"] == "transition":
                    context["_simulation"]["episode_index"] = int(context["_simulation"].get("episode_index", 0) or 0) + 1
                    phases = context["_simulation"].get("phases", [])
                    current_phase = int(context["_simulation"].get("current_phase_index", 0) or 0)
                    if isinstance(phases, list) and phases:
                        context["_simulation"]["current_phase_index"] = min(len(phases) - 1, current_phase + 1)
                context["_scene_control"], progress_assessment = apply_scene_audit(
                    context["_scene_control"], control, state=context.get("_simulation", {}), round_index=round_index
                )
                if control["guidance"]:
                    guidance = control["guidance"]
                    context["_global_guidance"] = guidance
                if state_coder_active and uncoded_actions:
                    source_ids = list(
                        dict.fromkeys(str(item.get("node_id", "")) for item in uncoded_actions if item.get("node_id"))
                    )
                    next_dynamic_state, coder_result = apply_dynamic_state_proposal(
                        context["_simulation"].get("dynamic_state", {}),
                        control["state_patch"],
                        seq=simulation_seq,
                        source_actions=uncoded_actions,
                        round_index=round_index,
                    )
                    context["_simulation"]["dynamic_state"] = next_dynamic_state
                    state_coder_cursor = len(recent_actions)
                    _, last_event_hash = emit_event(
                        db,
                        run.id,
                        "state_coder",
                        "dynamic_state_updated",
                        {
                            "round": round_index,
                            **coder_result,
                            "dynamic_state": compact_dynamic_state(next_dynamic_state),
                            "_rollback_snapshot": continuous_snapshot(
                                context, recent_actions, round_index, action_index, simulation_seq
                            ),
                        },
                        caused_by="director_state_coder",
                        context_snapshot={
                            "round": round_index,
                            "version": coder_result["version"],
                            "schema_version": coder_result["schema_version"],
                            "source_node_ids": source_ids,
                        },
                        prev_hash=last_event_hash,
                    )
                audit_payload = {key: value for key, value in control.items() if key != "state_patch"}
                _, last_event_hash = emit_event(
                    db,
                    run.id,
                    "director_console",
                    "episode_audited",
                    {
                        "round": round_index,
                        **audit_payload,
                        "progress_assessment": progress_assessment,
                        "scene_control": context["_scene_control"],
                        "_rollback_snapshot": continuous_snapshot(context, recent_actions, round_index, action_index, simulation_seq),
                    },
                    caused_by="simulation_director_control",
                    prev_hash=last_event_hash,
                )
                if control["decision"] == "transition":
                    context["_context_pack"] = build_scene_context_pack(
                        environment=workflow.environment,
                        state=context.get("_simulation", {}),
                        recent_actions=recent_actions,
                    )
                    _, last_event_hash = emit_event(
                        db,
                        run.id,
                        "context_engine",
                        "context_pack_activated",
                        activation_trace(context["_context_pack"]),
                        caused_by="episode_transition",
                        context_snapshot=activation_trace(context["_context_pack"]),
                        prev_hash=last_event_hash,
                    )
                stop_requested = control["decision"] == "stop"
            if action_index >= policy.max_actions or stop_requested:
                break

        context["simulation_actions"] = recent_actions
        if self._stop_requested(db, run.id):
            return self._finish_stopped_run(
                db,
                run,
                output={"actions": recent_actions, "simulation": context.get("_simulation", {}), "scene_control": context.get("_scene_control", {})},
                last_event_hash=load_last_event_hash(db, run.id)[1],
            )
        final_output: dict[str, Any] = {"content": "\n".join(str(item["action"]) for item in recent_actions)}
        if summary is not None:
            summary_input = {
                **resolve_inputs(summary, context),
                "prompt": (
                    f"{str(summary.inputs.get('prompt', 'Produce the final result.'))}\n\n"
                    "The following are the actual simulation actions. Synthesize them without inventing additional actions "
                    "that did not occur. Preserve the requested output format and language. Lead with the final established "
                    "outcome; distinguish earlier intermediate states from the situation at the end. Do not retell every "
                    "beat unless the requested form needs it, or pad the summary with new unresolved hooks.\n"
                    f"{json.dumps(recent_actions, ensure_ascii=False, default=str)}"
                ),
                "_environment": context.get("_runtime_environment", {}),
                "_context_pack": context.get("_context_pack", {}),
                "_memory": context.get("_memory", []),
                "_simulation": context.get("_simulation", {}),
            }
            final_output, _, _, last_event_hash = await self._execute_continuous_node(
                db=db,
                run_id=run.id,
                workflow_id=workflow.id,
                node=summary,
                runtime_input=summary_input,
                context=context,
                simulation_seq=simulation_seq + 1,
                last_event_hash=last_event_hash,
                caused_by="simulation_summary",
            )
            context[summary.id] = final_output

        run.status = "succeeded"
        run.output_json = serialize(
            {"actions": recent_actions, "final": final_output, "simulation": context.get("_simulation", {}), "scene_control": context.get("_scene_control", {})}
        )
        run.error_json = "null"
        run.ended_at = datetime.now(timezone.utc)
        db.add(run)
        db.commit()
        return run.id

    async def _execute_continuous_node(
        self,
        *,
        db: Session,
        run_id: str,
        workflow_id: str,
        node: WorkflowNode,
        runtime_input: dict[str, Any],
        context: dict[str, Any],
        simulation_seq: int,
        last_event_hash: str,
        caused_by: str,
    ) -> tuple[dict[str, Any], int, int, str]:
        node_run = NodeRunRecord(
            run_id=run_id,
            node_id=node.id,
            status="running",
            started_at=datetime.now(timezone.utc),
            input_json=serialize(redact_secrets(runtime_input)),
            output_json="{}",
            error_json="null",
        )
        db.add(node_run)
        db.commit()
        _, last_event_hash = emit_event(
            db,
            run_id,
            node.id,
            "queued",
            {"input": runtime_input},
            caused_by=caused_by,
            context_snapshot=runtime_context_pack_snapshot(runtime_input),
            prev_hash=last_event_hash,
        )
        _, last_event_hash = emit_event(
            db,
            run_id,
            node.id,
            "running",
            {"input": runtime_input},
            caused_by=caused_by,
            context_snapshot=runtime_context_pack_snapshot(runtime_input),
            prev_hash=last_event_hash,
        )
        start = perf_counter()
        try:
            output = await execute_node(node=node, node_input=runtime_input, context=context, provider=self.provider)
        except Exception as exc:
            duration = int((perf_counter() - start) * 1000)
            error = {"message": str(exc), "node_id": node.id}
            node_run.status = "failed"
            node_run.ended_at = datetime.now(timezone.utc)
            node_run.duration_ms = duration
            node_run.error_json = serialize(redact_secrets(error))
            db.add(node_run)
            db.commit()
            emit_event(
                db,
                run_id,
                node.id,
                "failed",
                {"input": runtime_input, "output": {}, "error": error},
                duration_ms=duration,
                caused_by=caused_by,
                prev_hash=last_event_hash,
            )
            raise
        duration = int((perf_counter() - start) * 1000)
        if caused_by == "simulation_turn":
            context_packet = runtime_input.get("context", {})
            round_index = int(context_packet.get("round", 1) or 1) if isinstance(context_packet, dict) else 1
            action_index = int(context_packet.get("action_index", 1) or 1) if isinstance(context_packet, dict) else 1
            output = normalize_action_output(output, round_index=round_index, action_index=action_index)
        next_state = (
            context.get("_simulation", {})
            if caused_by == "simulation_turn"
            else update_simulation_state(
                context.get("_simulation", {}),
                node=node,
                runtime_input=runtime_input,
                output=output,
                status="succeeded",
                seq=simulation_seq,
            )
        )
        node_run.status = "succeeded"
        node_run.ended_at = datetime.now(timezone.utc)
        node_run.duration_ms = duration
        node_run.output_json = serialize(redact_secrets(output))
        db.add(node_run)
        db.commit()
        success_seq, last_event_hash = emit_event(
            db,
            run_id,
            node.id,
            "succeeded",
            {"input": runtime_input, "output": output, "error": None, "simulation_state": next_state},
            duration_ms=duration,
            caused_by=caused_by,
            prev_hash=last_event_hash,
        )
        return output, duration, success_seq, last_event_hash

    async def run(
        self,
        db: Session,
        workflow: WorkflowDefinition,
        request: RunRequest,
        run_id: str | None = None,
    ) -> str:
        validate_workflow(workflow)

        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        run = db.query(RunRecord).filter(RunRecord.id == run_id).first()
        if run is not None and run.status in {"stopping", "stopped"}:
            if run.status == "stopping":
                return self._finish_stopped_run(db, run, output={}, last_event_hash=load_last_event_hash(db, run_id)[1])
            return run_id
        if run is None:
            run = RunRecord(
                id=run_id,
                workflow_id=workflow.id,
                status="running",
                input_json=serialize(request.input),
                output_json="{}",
                error_json="null",
                retry_from_run_id=request.retry_from_run_id,
                retry_from_node=request.retry_from_node,
            )
        else:
            run.status = "running"
            run.workflow_id = workflow.id
            run.input_json = serialize(request.input)
            run.output_json = "{}"
            run.error_json = "null"
            run.retry_from_run_id = request.retry_from_run_id
            run.retry_from_node = request.retry_from_node
        db.add(run)
        db.commit()

        node_map = {n.id: n for n in workflow.nodes}
        topo = topological_sort(workflow.nodes, workflow.edges)
        incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
        outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
        for edge in workflow.edges:
            incoming[edge.target].append(edge)
            outgoing[edge.source].append(edge)

        context: dict[str, Any] = {"input": request.input}
        context["_run"] = {"run_id": run_id, "workflow_id": workflow.id}
        context["_environment"] = workflow.environment.model_dump()
        context["_runtime_environment"] = environment_for_runtime(workflow.environment)
        context["_memory"] = memories_at_rollback(
            load_recent_memories(db, workflow_id=workflow.id, limit=20), request
        )
        context["_simulation"] = init_simulation_state(workflow)
        context["_context_pack"] = build_scene_context_pack(
            environment=workflow.environment,
            state=context["_simulation"],
            recent_actions=[],
        )
        context["_preflight_confirm_policy"] = parse_preflight_confirm_policy(workflow)
        context["_director_quality_policy"] = parse_director_quality_policy(workflow)
        context["_preflight_confirmed"] = False
        if isinstance(request.input.get("_human_response"), dict):
            context["_human_response"] = request.input["_human_response"]
        node_status: dict[str, str] = {}
        node_output: dict[str, dict[str, Any]] = {}
        node_success_event_seq: dict[str, int] = {}
        simulation_seq = 0
        executed_non_director_count = 0
        director_override_cursor = 0
        _, last_event_hash = load_last_event_hash(db, run_id)
        if context["_context_pack"].get("entries"):
            _, last_event_hash = emit_event(
                db,
                run_id,
                "context_engine",
                "context_pack_activated",
                activation_trace(context["_context_pack"]),
                caused_by="run_start",
                context_snapshot=activation_trace(context["_context_pack"]),
                prev_hash=last_event_hash,
            )
        loop_policy = build_loop_policy(workflow)
        if loop_policy.enabled:
            restore_continuous_checkpoint(context, request)
            resume_actions = context.get("_continuous_resume", {}).get("actions", [])
            context["_context_pack"] = build_scene_context_pack(
                environment=workflow.environment,
                state=context["_simulation"],
                recent_actions=resume_actions if isinstance(resume_actions, list) else [],
            )
            try:
                return await self._run_continuous_simulation(
                    db=db,
                    workflow=workflow,
                    run=run,
                    context=context,
                    last_event_hash=last_event_hash,
                )
            except Exception as exc:  # noqa: BLE001
                run.status = "failed"
                run.error_json = serialize({"message": str(exc)})
                run.ended_at = datetime.now(timezone.utc)
                db.add(run)
                db.commit()
                return run_id
        human_resume_emitted = False
        pending_human_at_start = context.get("_human_response")
        if isinstance(pending_human_at_start, dict):
            resumed_node_id = str(pending_human_at_start.get("node_id", request.retry_from_node or "human")).strip() or "human"
            if is_director_preflight_response(pending_human_at_start):
                context["_preflight_confirmed"] = True
            last_event_hash = emit_human_resumed(
                db=db,
                run_id=run_id,
                node_id=resumed_node_id,
                response=pending_human_at_start,
                last_event_hash=last_event_hash,
            )
            human_resume_emitted = True
        execute_scope = self._restore_retry_context(
            db=db,
            request=request,
            run_id=run_id,
            workflow=workflow,
            context=context,
            node_output=node_output,
            node_status=node_status,
        )

        try:
            for node_id in topo:
                if self._stop_requested(db, run_id):
                    return self._finish_stopped_run(
                        db,
                        run,
                        output={
                            "nodes": {nid: node_output.get(nid) for nid in node_output},
                            "simulation": context.get("_simulation", {}),
                        },
                        last_event_hash=load_last_event_hash(db, run_id)[1],
                    )
                node = node_map[node_id]
                if node_id not in execute_scope:
                    node_status[node_id] = "skipped"
                    continue
                director_override_cursor, override_guidance = pull_latest_director_override(
                    db,
                    workflow_id=workflow.id,
                    run_id=run_id,
                    after_id=director_override_cursor,
                )
                if override_guidance:
                    context["_global_guidance"] = override_guidance

                parents = incoming.get(node_id, [])
                active_incoming_edges: list[WorkflowEdge] = []
                if parents:
                    allowed_parent = False
                    for edge in parents:
                        src_status = self._parent_status_for_routing(
                            source=edge.source,
                            node_status=node_status,
                            node_output=node_output,
                            execute_scope=execute_scope,
                        )
                        if src_status != "succeeded":
                            continue
                        source_node = node_map[edge.source]
                        if source_node.type == "condition":
                            if should_follow_condition(edge, node_output.get(edge.source, {})):
                                allowed_parent = True
                                active_incoming_edges.append(edge)
                        else:
                            allowed_parent = True
                            active_incoming_edges.append(edge)
                    if not allowed_parent:
                        node_status[node_id] = "skipped"
                        _, last_event_hash = emit_event(
                            db,
                            run_id,
                            node_id,
                            "skipped",
                            {"reason": "no active upstream"},
                            caused_by="routing",
                            parent_event_ids=[node_success_event_seq[e.source] for e in parents if e.source in node_success_event_seq],
                            context_snapshot={"node_id": node_id, "reason": "no active upstream"},
                            prev_hash=last_event_hash,
                        )
                        continue
                elif workflow.entry_nodes and node_id not in workflow.entry_nodes:
                    if node.type == "director":
                        pass
                    else:
                        node_status[node_id] = "skipped"
                        _, last_event_hash = emit_event(
                            db,
                            run_id,
                            node_id,
                            "skipped",
                            {"reason": "not in entry_nodes"},
                            caused_by="routing",
                            context_snapshot={"node_id": node_id, "reason": "not in entry_nodes"},
                            prev_hash=last_event_hash,
                        )
                        continue

                node_input = resolve_inputs(node, context)
                incoming_interactions: list[dict[str, Any]] = []
                for edge in active_incoming_edges if parents else []:
                    src_out = node_output.get(edge.source, {})
                    if edge.interaction and edge.interaction.required and not src_out:
                        raise ValueError(f"required interaction from '{edge.source}' to '{node_id}' is empty")
                    incoming_interactions.append(build_interaction_message(edge=edge, source_output=src_out))
                runtime_input = {**node_input, "_interactions": incoming_interactions} if incoming_interactions else node_input
                runtime_input = {**runtime_input, "_environment": context.get("_runtime_environment", {})}
                node_uses_external_runtime = node.type == "external_agent" or (
                    node.type == "agent" and str(node.config.get("execution_mode", "")).strip().lower() == "external_agent"
                )
                node_context_pack = (
                    context_for_node(context.get("_context_pack", {}), node)
                    if not node_uses_external_runtime
                    else {"episode": context.get("_context_pack", {}).get("episode", 0), "estimated_tokens": 0, "entries": []}
                )
                runtime_input = {**runtime_input, "_context_pack": node_context_pack}
                if isinstance(context.get("_memory"), list):
                    runtime_input = {**runtime_input, "_memory": select_memories_for_node(context.get("_memory", []), node)}
                if isinstance(context.get("_simulation"), dict):
                    runtime_input = {**runtime_input, "_simulation": context.get("_simulation", {})}
                if context.get("_global_guidance"):
                    runtime_input = {**runtime_input, "_global_guidance": context.get("_global_guidance", "")}
                pending_human = context.get("_human_response")
                if isinstance(pending_human, dict) and str(pending_human.get("node_id", "")) == node_id:
                    runtime_input = {**runtime_input, "_human_response": pending_human}
                    if is_director_preflight_response(pending_human):
                        context["_preflight_confirmed"] = True
                    if not human_resume_emitted:
                        last_event_hash = emit_human_resumed(
                            db=db,
                            run_id=run_id,
                            node_id=node_id,
                            response=pending_human,
                            last_event_hash=last_event_hash,
                            parent_event_ids=[node_success_event_seq[e.source] for e in active_incoming_edges if e.source in node_success_event_seq],
                        )
                        human_resume_emitted = True
                preflight = context.get("_preflight_confirm_policy", {})
                if isinstance(preflight, dict) and bool(preflight.get("enabled")) and not bool(context.get("_preflight_confirmed")):
                    warmup_nodes = int(preflight.get("warmup_nodes", 1) or 1)
                    expensive_types = set(str(v) for v in preflight.get("expensive_node_types", []))
                    min_remaining = int(preflight.get("min_remaining_expensive_nodes", 1) or 1)
                    succeeded_count = sum(1 for s in node_status.values() if s == "succeeded")
                    node_index = topo.index(node_id)
                    remaining_candidates = [nid for nid in topo[node_index:] if nid in execute_scope]
                    expensive_remaining = sum(1 for nid in remaining_candidates if node_map[nid].type in expensive_types)
                    should_pause = succeeded_count >= warmup_nodes and node.type in expensive_types and expensive_remaining >= min_remaining
                    if should_pause:
                        preview = build_preflight_preview(
                            workflow=workflow,
                            node_id=node_id,
                            node_output=node_output,
                            context=context,
                            expensive_remaining=expensive_remaining,
                        )
                        wait_payload = {
                            "node_id": node_id,
                            "owner": "director",
                            "question": str(preflight.get("question", "请确认方向后继续执行。")),
                            "context_hint": "director_preflight_confirm",
                            "required": True,
                            "preview": preview,
                        }
                        parent_event_ids = [node_success_event_seq[e.source] for e in active_incoming_edges if e.source in node_success_event_seq]
                        context_snapshot = {
                            "workflow_id": workflow.id,
                            "node_id": node_id,
                            "kind": "director_preflight_confirm",
                            "expensive_remaining": expensive_remaining,
                        }
                        _, last_event_hash = emit_event(
                            db,
                            run_id,
                            node_id,
                            "waiting_human",
                            {
                                "input": runtime_input,
                                "output": wait_payload,
                                "error": None,
                                "simulation_state": update_simulation_state(
                                    context.get("_simulation", {}),
                                    node=node,
                                    runtime_input=runtime_input,
                                    output=wait_payload,
                                    status="waiting_human",
                                    seq=simulation_seq + 1,
                                ),
                            },
                            caused_by="director_preflight",
                            parent_event_ids=parent_event_ids,
                            context_snapshot=context_snapshot,
                            prev_hash=last_event_hash,
                        )
                        simulation_seq += 1
                        run.status = "waiting_human"
                        run.retry_from_node = node_id
                        run.error_json = serialize(
                            {
                                "code": "DIRECTOR_PREFLIGHT_CONFIRM_REQUIRED",
                                "message": "Run paused for director preflight confirmation",
                                "node_id": node_id,
                                "checkpoint": wait_payload,
                            }
                        )
                        run.ended_at = None
                        db.add(run)
                        db.commit()
                        return run_id
                if incoming_interactions:
                    context.setdefault("interactions", {})
                    context["interactions"][node_id] = incoming_interactions
                node_run = NodeRunRecord(
                    run_id=run_id,
                    node_id=node_id,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    input_json=serialize(redact_secrets(runtime_input)),
                    output_json="{}",
                    error_json="null",
                )
                db.add(node_run)
                db.commit()

                parent_event_ids = [node_success_event_seq[e.source] for e in active_incoming_edges if e.source in node_success_event_seq]
                context_snapshot = build_context_snapshot(
                    workflow_id=workflow.id,
                    node=node,
                    runtime_input=runtime_input,
                    active_incoming_edges=active_incoming_edges,
                    global_guidance=str(context.get("_global_guidance", "")),
                )
                _, last_event_hash = emit_event(
                    db,
                    run_id,
                    node_id,
                    "queued",
                    {"input": runtime_input},
                    caused_by="scheduler",
                    parent_event_ids=parent_event_ids,
                    context_snapshot=context_snapshot,
                    prev_hash=last_event_hash,
                )
                _, last_event_hash = emit_event(
                    db,
                    run_id,
                    node_id,
                    "running",
                    {"input": runtime_input},
                    caused_by="scheduler",
                    parent_event_ids=parent_event_ids,
                    context_snapshot=context_snapshot,
                    prev_hash=last_event_hash,
                )
                start = perf_counter()
                try:
                    output = await execute_node(node=node, node_input=runtime_input, context=context, provider=self.provider)
                    duration = int((perf_counter() - start) * 1000)
                    node_run.status = "succeeded"
                    node_run.ended_at = datetime.now(timezone.utc)
                    node_run.duration_ms = duration
                    node_run.output_json = serialize(redact_secrets(output))
                    db.add(node_run)
                    db.commit()
                    node_status[node_id] = "succeeded"
                    node_output[node_id] = output
                    context[node_id] = output
                    if node.type == "director":
                        global_guidance = str(output.get("global_guidance", "")).strip()
                        if global_guidance:
                            context["_global_guidance"] = global_guidance
                    next_simulation_state = update_simulation_state(
                        context.get("_simulation", {}),
                        node=node,
                        runtime_input=runtime_input,
                        output=output,
                        status="succeeded",
                        seq=simulation_seq + 1,
                    )
                    success_seq, last_event_hash = emit_event(
                        db,
                        run_id,
                        node_id,
                        "succeeded",
                        {
                            "input": runtime_input,
                            "output": output,
                            "error": None,
                            "simulation_state": next_simulation_state,
                        },
                        duration_ms=duration,
                        caused_by="node",
                        parent_event_ids=parent_event_ids,
                        context_snapshot=context_snapshot,
                        prev_hash=last_event_hash,
                    )
                    context["_simulation"] = next_simulation_state
                    node_success_event_seq[node_id] = success_seq
                    role = extract_role(node.type, node, output)
                    importance = memory_importance(node.type, "succeeded", output)
                    memory_content = extract_memory_content(node.type, output)
                    if should_write_memory(
                        kind="fact",
                        node=node,
                        content=memory_content,
                        importance=importance,
                        interactions=incoming_interactions,
                    ):
                        append_memory_record(
                            db=db,
                            context=context,
                            workflow_id=workflow.id,
                            run_id=run_id,
                            node=node,
                            role=role,
                            content=memory_content,
                            meta=build_memory_meta(
                                kind="fact",
                                node=node,
                                role=role,
                                importance=importance,
                                confidence=0.75,
                                source_event_seq=success_seq,
                                trigger=f"event#{success_seq}",
                            ),
                        )
                    if node.type in {"agent", "external_agent"}:
                        character_state = {
                            "subject": node_subject(node),
                            "role": role,
                            "last_action": memory_content[:500],
                            "last_observation": json.dumps(runtime_input.get("_interactions", []), ensure_ascii=False, default=str)[:500],
                        }
                        character_content = json.dumps(character_state, ensure_ascii=False, default=str)
                        if should_write_memory(
                            kind="character",
                            node=node,
                            content=character_content,
                            importance=max(0.6, importance),
                            interactions=incoming_interactions,
                        ):
                            append_memory_record(
                                db=db,
                                context=context,
                                workflow_id=workflow.id,
                                run_id=run_id,
                                node=node,
                                role=role,
                                content=character_content,
                                meta=build_memory_meta(
                                    kind="character",
                                    node=node,
                                    role=role,
                                    importance=max(0.6, importance),
                                    confidence=0.65,
                                    source_event_seq=success_seq,
                                    trigger=f"event#{success_seq}",
                                ),
                            )
                    if node.type != "director":
                        executed_non_director_count += 1
                        last_event_hash, quality_guidance = self._maybe_director_quality_correction(
                            db=db,
                            run_id=run_id,
                            workflow_id=workflow.id,
                            node=node,
                            runtime_input=runtime_input,
                            output=output,
                            success_seq=success_seq,
                            last_event_hash=last_event_hash,
                            context=context,
                            executed_non_director_count=executed_non_director_count,
                        )
                        if quality_guidance:
                            context["_global_guidance"] = quality_guidance
                        last_event_hash, vote_guidance = self._maybe_director_vote(
                            db=db,
                            run_id=run_id,
                            workflow_id=workflow.id,
                            node=node,
                            runtime_input=runtime_input,
                            output=output,
                            parent_event_ids=parent_event_ids,
                            success_seq=success_seq,
                            last_event_hash=last_event_hash,
                            context=context,
                        )
                        if vote_guidance:
                            context["_global_guidance"] = vote_guidance
                    simulation_seq += 1
                except HumanInterventionRequired as checkpoint:
                    duration = int((perf_counter() - start) * 1000)
                    wait_payload = checkpoint.payload
                    node_run.status = "waiting_human"
                    node_run.ended_at = datetime.now(timezone.utc)
                    node_run.duration_ms = duration
                    node_run.output_json = serialize(redact_secrets(wait_payload))
                    db.add(node_run)
                    db.commit()
                    node_status[node_id] = "waiting_human"
                    _, last_event_hash = emit_event(
                        db,
                        run_id,
                        node_id,
                        "waiting_human",
                        {
                            "input": runtime_input,
                            "output": wait_payload,
                            "error": None,
                            "simulation_state": update_simulation_state(
                                context.get("_simulation", {}),
                                node=node,
                                runtime_input=runtime_input,
                                output=wait_payload,
                                status="waiting_human",
                                seq=simulation_seq + 1,
                            ),
                        },
                        duration_ms=duration,
                        caused_by="human_checkpoint",
                        parent_event_ids=parent_event_ids,
                        context_snapshot=context_snapshot,
                        prev_hash=last_event_hash,
                    )
                    simulation_seq += 1
                    run.status = "waiting_human"
                    run.error_json = serialize(
                        {
                            "code": "HUMAN_INTERVENTION_REQUIRED",
                            "message": "Run paused for human intervention",
                            "node_id": node_id,
                            "checkpoint": wait_payload,
                        }
                    )
                    run.ended_at = None
                    db.add(run)
                    db.commit()
                    return run_id
                except Exception as exc:  # noqa: BLE001
                    duration = int((perf_counter() - start) * 1000)
                    error_payload = {"message": str(exc), "node_id": node_id}
                    node_run.status = "failed"
                    node_run.ended_at = datetime.now(timezone.utc)
                    node_run.duration_ms = duration
                    node_run.error_json = serialize(redact_secrets(error_payload))
                    db.add(node_run)
                    db.commit()
                    node_status[node_id] = "failed"
                    _, last_event_hash = emit_event(
                        db,
                        run_id,
                        node_id,
                        "failed",
                        {
                            "input": runtime_input,
                            "output": {},
                            "error": error_payload,
                            "simulation_state": update_simulation_state(
                                context.get("_simulation", {}),
                                node=node,
                                runtime_input=runtime_input,
                                output={},
                                status="failed",
                                seq=simulation_seq + 1,
                            ),
                        },
                        duration_ms=duration,
                        caused_by="node_exception",
                        parent_event_ids=parent_event_ids,
                        context_snapshot=context_snapshot,
                        prev_hash=last_event_hash,
                    )
                    simulation_seq += 1
                    run.status = "failed"
                    run.error_json = serialize(error_payload)
                    run.ended_at = datetime.now(timezone.utc)
                    db.add(run)
                    db.commit()
                    return run_id

            if self._stop_requested(db, run_id):
                return self._finish_stopped_run(
                    db,
                    run,
                    output={
                        "nodes": {nid: node_output.get(nid) for nid in node_output},
                        "simulation": context.get("_simulation", {}),
                    },
                    last_event_hash=load_last_event_hash(db, run_id)[1],
                )
            run.status = "succeeded"
            run.output_json = serialize(
                {
                    "nodes": {nid: node_output.get(nid) for nid in node_output},
                    "simulation": context.get("_simulation", {}),
                }
            )
            run.ended_at = datetime.now(timezone.utc)
            db.add(run)
            db.commit()
            return run_id
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.error_json = serialize({"message": str(exc)})
            run.ended_at = datetime.now(timezone.utc)
            db.add(run)
            db.commit()
            return run_id
